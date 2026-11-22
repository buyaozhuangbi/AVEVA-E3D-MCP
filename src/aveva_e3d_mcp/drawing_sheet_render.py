"""Bounded SVG and dependency-free PNG renderers for paper-millimetre scenes.

The scene owns placement and annotation values. These renderers only convert
its bottom-left Cartesian paper coordinates to SVG or a letterboxed bitmap.
The sheet font deliberately supports an explicit uppercase ASCII subset;
unsupported text is rejected rather than replaced with question marks.
"""

from __future__ import annotations

import html
import math
import struct
import zlib
from collections.abc import Mapping
from typing import Any

from .orthographic_png import _FONT, _line, _png_chunk
from .section_hatch_precision import validate_section_hatch_precision


MAX_SHEET_STROKES = 22_000
MAX_SHEET_TEXTS = 128
MAX_SHEET_TEXT_CHARACTERS = 8192
MAX_SHEET_BYTES = 8_000_000
SHEET_TEXT_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-+/:_")
_GLYPHS = {**_FONT, "_": ("00000",) * 6 + ("11111",)}
_STROKE_ROLES = {"border", "title_block", "silhouette", "feature", "dimension", "hatch"}
_TEXT_ROLES = {"title", "drawing_number", "sheet_metadata", "view_label", "dimension", "disclaimer"}
_INK = (23, 33, 43)
_HIDDEN = (113, 128, 150)
_HATCH = (176, 176, 176)
_PAPER = (255, 255, 255)
_BACKGROUND = (235, 238, 241)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_sheet_{field}")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid_sheet_{field}") from None
    if not math.isfinite(result):
        raise ValueError(f"invalid_sheet_{field}")
    return result


def _point(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError(f"invalid_sheet_{field}")
    return _number(value[0], field), _number(value[1], field)


def _view(value: Any) -> str | None:
    if value is not None and value not in ("FRONT", "TOP", "SIDE", "SECTION"):
        raise ValueError("invalid_sheet_view_name")
    return value


def validate_drawing_sheet_scene(scene: Any) -> dict[str, Any]:
    """Validate and copy the shared scene without changing physical placement."""
    if not isinstance(scene, Mapping) or scene.get("success") is not True or scene.get("complete") is not True:
        raise ValueError("successful_complete_sheet_scene_required")
    if (scene.get("coordinate_space") != "paper" or scene.get("coordinate_unit") != "mm" or
            scene.get("model_coordinate_unit") != "mm"):
        raise ValueError("sheet_paper_and_model_millimetres_required")
    paper = scene.get("paper")
    if not isinstance(paper, Mapping):
        raise ValueError("invalid_sheet_paper")
    size, orientation = paper.get("size"), paper.get("orientation")
    if size not in ("A3", "A4") or orientation not in ("portrait", "landscape"):
        raise ValueError("invalid_sheet_paper")
    w, h = (297.0, 420.0) if size == "A3" else (210.0, 297.0)
    if orientation == "landscape":
        w, h = h, w
    if _number(paper.get("width_mm"), "paper") != w or _number(paper.get("height_mm"), "paper") != h:
        raise ValueError("sheet_paper_dimensions_mismatch")

    def in_paper(point: tuple[float, float]) -> bool:
        return -1e-7 <= point[0] <= w + 1e-7 and -1e-7 <= point[1] <= h + 1e-7

    raw_strokes, raw_texts = scene.get("strokes"), scene.get("texts")
    if not isinstance(raw_strokes, (tuple, list)) or len(raw_strokes) > MAX_SHEET_STROKES:
        raise ValueError("sheet_stroke_limit_exceeded_or_invalid")
    if not isinstance(raw_texts, (tuple, list)) or len(raw_texts) > MAX_SHEET_TEXTS:
        raise ValueError("sheet_text_limit_exceeded_or_invalid")
    strokes: list[dict[str, Any]] = []
    for stroke in raw_strokes:
        if (not isinstance(stroke, Mapping) or not isinstance(stroke.get("role"), str) or
                stroke["role"] not in _STROKE_ROLES):
            raise ValueError("invalid_sheet_stroke")
        start, end = _point(stroke.get("start"), "stroke"), _point(stroke.get("end"), "stroke")
        if not in_paper(start) or not in_paper(end):
            raise ValueError("sheet_stroke_outside_paper")
        visibility = stroke.get("visibility", "visible")
        if visibility not in ("visible", "hidden"):
            raise ValueError("invalid_sheet_visibility")
        view_name = _view(stroke.get("view_name"))
        if stroke["role"] == "hatch" and (view_name != "SECTION" or visibility != "visible"):
            raise ValueError("invalid_sheet_hatch")
        strokes.append({"start": start, "end": end, "role": stroke["role"], "visibility": visibility,
                        "view_name": view_name})
    texts: list[dict[str, Any]] = []
    characters = 0
    for item in raw_texts:
        if (not isinstance(item, Mapping) or not isinstance(item.get("role"), str) or
                item["role"] not in _TEXT_ROLES):
            raise ValueError("invalid_sheet_text")
        content = item.get("text")
        if not isinstance(content, str) or len(content) > 160 or any(c not in SHEET_TEXT_CHARACTERS for c in content):
            raise ValueError("unsupported_sheet_text")
        characters += len(content)
        if characters > MAX_SHEET_TEXT_CHARACTERS:
            raise ValueError("sheet_text_character_limit_exceeded")
        position = _point(item.get("position"), "text_position")
        height = _number(item.get("height"), "text_height")
        rotation = _number(item.get("rotation", 0), "text_rotation")
        if height <= 0 or height > min(w, h) or rotation not in (0, 90):
            raise ValueError("invalid_sheet_text_geometry")
        text_width = max(0, len(content) * 6 - 1) * height / 7.0
        extent_x, extent_y = (text_width, height) if rotation == 0 else (height, text_width)
        if not all(in_paper(point) for point in (
                (position[0] - extent_x / 2, position[1] - extent_y / 2),
                (position[0] + extent_x / 2, position[1] + extent_y / 2))):
            raise ValueError("sheet_text_outside_paper")
        texts.append({"position": position, "text": content, "height": height, "rotation": rotation,
                      "text_width": text_width, "role": item["role"], "view_name": _view(item.get("view_name"))})
    return {"coordinate_space": "paper", "coordinate_unit": "mm", "model_coordinate_unit": "mm",
            "paper": {"size": size, "orientation": orientation, "width_mm": w, "height_mm": h},
            "strokes": strokes, "texts": texts}


class _SvgCoordinateFormatter:
    def __init__(self, coordinate_decimal_places: int) -> None:
        if type(coordinate_decimal_places) is not int or not 6 <= coordinate_decimal_places <= 15:
            raise ValueError("invalid_coordinate_decimal_places")
        self._format = f".{coordinate_decimal_places}f"

    def format_coordinate(self, value: float) -> str:
        result = format(value, self._format).rstrip("0").rstrip(".")
        return "0" if result in ("", "-0") else result


def render_drawing_sheet_svg(scene: Any, *, coordinate_decimal_places: int = 6) -> str:
    """Render paper millimetres using one document's 6–15 decimal precision.

    The default retains legacy serialization. Higher precision also requires
    nonzero formatted stroke endpoints in paper XY and in the actual SVG
    coordinates after the Y flip. Scene metadata never selects this option.
    """
    fmt = _SvgCoordinateFormatter(coordinate_decimal_places).format_coordinate
    normalized = validate_drawing_sheet_scene(scene)
    paper = normalized["paper"]
    w, h = paper["width_mm"], paper["height_mm"]
    validate_section_hatch_precision(
        ({**stroke, "kind": stroke["role"]} for stroke in normalized["strokes"] if stroke["view_name"] == "SECTION"),
        transform=lambda point: (point[0], h - point[1]), transform_orientation=-1)
    rows = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(w)}mm" height="{fmt(h)}mm" viewBox="0 0 {fmt(w)} {fmt(h)}">',
            '<rect width="100%" height="100%" fill="white"/>']
    for stroke in sorted(normalized["strokes"], key=lambda item: item["role"] != "hatch"):
        a, b = stroke["start"], stroke["end"]
        hidden = stroke["visibility"] == "hidden"
        weight = .15 if stroke["role"] == "hatch" else .35 if stroke["role"] in ("border", "silhouette") else .2
        style = ('stroke="#b0b0b0"' if stroke["role"] == "hatch" else
                 'stroke="#718096" stroke-dasharray="2 1"' if hidden else 'stroke="#17212b"')
        view = stroke["view_name"] or "SHEET"
        svg_a, svg_b = (fmt(a[0]), fmt(h - a[1])), (fmt(b[0]), fmt(h - b[1]))
        if coordinate_decimal_places > 6:
            if (fmt(a[0]), fmt(a[1])) == (fmt(b[0]), fmt(b[1])):
                raise ValueError("sheet_geometry_below_svg_precision")
            if svg_a == svg_b:
                raise ValueError("sheet_geometry_below_svg_precision_after_y_flip")
        rows.append(f'<path data-role="{stroke["role"]}" data-view="{view}" fill="none" {style} stroke-width="{fmt(weight)}" d="M {svg_a[0]} {svg_a[1]} L {svg_b[0]} {svg_b[1]}"/>')
    for item in normalized["texts"]:
        x, y = item["position"][0], h - item["position"][1]
        rotation = f' transform="rotate(-90 {fmt(x)} {fmt(y)})"' if item["rotation"] else ""
        # Fix the advance to the same physical width as the bitmap preview.
        length = f' textLength="{fmt(item["text_width"])}" lengthAdjust="spacingAndGlyphs"' if item["text"] else ""
        rows.append(f'<text data-role="{item["role"]}" data-view="{item["view_name"] or "SHEET"}" x="{fmt(x)}" y="{fmt(y)}" font-family="monospace" font-size="{fmt(item["height"])}" text-anchor="middle" dominant-baseline="central" fill="#17212b"{length}{rotation}>{html.escape(item["text"])}</text>')
    rows.append('</svg>')
    result = "\n".join(rows)
    if len(result.encode("utf-8")) > MAX_SHEET_BYTES:
        raise ValueError("sheet_svg_output_limit_exceeded")
    return result


def _fill_rect(pixels: bytearray, width: int, height: int, rect: tuple[float, float, float, float],
               color: tuple[int, int, int]) -> None:
    # Pixel centers determine glyph coverage, so subpixel glyph cells do not
    # grow when a large paper sheet is previewed at a small image resolution.
    left, top, right, bottom = rect
    x0, x1 = max(0, math.ceil(left - .5)), min(width, math.ceil(right - .5))
    y0, y1 = max(0, math.ceil(top - .5)), min(height, math.ceil(bottom - .5))
    if x0 >= x1 or y0 >= y1:
        return
    row = bytes(color) * (x1 - x0)
    for y in range(y0, y1):
        start = (y * width + x0) * 3
        pixels[start:start + len(row)] = row


def _draw_text(pixels: bytearray, width: int, height: int, item: dict[str, Any],
               scale: float, offset_x: float, offset_y: float, paper_h: float) -> None:
    cell = item["height"] / 7
    cx, cy = item["position"]
    half_w = item["text_width"] / 2
    half_h = item["height"] / 2
    for index, char in enumerate(item["text"]):
        for row, bits in enumerate(_GLYPHS[char]):
            for col, bit in enumerate(bits):
                if bit == "0":
                    continue
                x0 = -half_w + (index * 6 + col) * cell
                x1 = x0 + cell
                y1 = half_h - row * cell
                y0 = y1 - cell
                if item["rotation"]:
                    x0, x1, y0, y1 = -y1, -y0, x0, x1
                rect = (offset_x + (cx + x0) * scale,
                        offset_y + (paper_h - cy - y1) * scale,
                        offset_x + (cx + x1) * scale,
                        offset_y + (paper_h - cy - y0) * scale)
                _fill_rect(pixels, width, height, rect, _INK)


def rasterize_drawing_sheet_png(scene: Any, *, width: int = 1200, height: int = 440) -> bytes:
    """Render the entire physical page at one letterboxed pixel/mm scale."""
    if type(width) is not int or type(height) is not int or not (64 <= width <= 2048 and 64 <= height <= 2048):
        raise ValueError("invalid_image_dimensions")
    if width * height > 2_000_000:
        raise ValueError("image_pixel_limit_exceeded")
    normalized = validate_drawing_sheet_scene(scene)
    paper = normalized["paper"]
    w, h = paper["width_mm"], paper["height_mm"]
    scale = min((width - 2) / w, (height - 2) / h)
    offset_x, offset_y = (width - w * scale) / 2, (height - h * scale) / 2
    pixels = bytearray(_BACKGROUND * (width * height))
    _fill_rect(pixels, width, height, (offset_x, offset_y, offset_x + w * scale, offset_y + h * scale), _PAPER)
    clip = (0, 0, width, height)
    for stroke in sorted(normalized["strokes"], key=lambda item: item["role"] != "hatch"):
        ax, ay = stroke["start"]
        bx, by = stroke["end"]
        start = (offset_x + ax * scale, offset_y + (h - ay) * scale)
        end = (offset_x + bx * scale, offset_y + (h - by) * scale)
        hidden = stroke["visibility"] == "hidden"
        if hidden:
            # Match the SVG's 2 mm dash / 1 mm gap in physical paper space.
            length = math.hypot(bx - ax, by - ay)
            if length == 0:
                continue
            for dash_start in range(math.ceil(length / 3)):
                low, high = dash_start * 3 / length, min((dash_start * 3 + 2) / length, 1)
                if low >= 1:
                    break
                a = tuple(start[i] + (end[i] - start[i]) * low for i in (0, 1))
                b = tuple(start[i] + (end[i] - start[i]) * high for i in (0, 1))
                _line(pixels, width, height, *(round(v) for v in (*a, *b)), _HIDDEN, clip)
        else:
            _line(pixels, width, height, *(round(v) for v in (*start, *end)),
                  _HATCH if stroke["role"] == "hatch" else _INK, clip)
    for item in normalized["texts"]:
        _draw_text(pixels, width, height, item, scale, offset_x, offset_y, h)

    scanlines = bytearray()
    row_size = width * 3
    for row in range(height):
        scanlines.append(0)
        scanlines.extend(pixels[row * row_size:(row + 1) * row_size])
    result = (b"\x89PNG\r\n\x1a\n" +
              _png_chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)) +
              _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6)) + _png_chunk(b"IEND", b""))
    if len(result) > MAX_SHEET_BYTES:
        raise ValueError("sheet_png_output_limit_exceeded")
    return result
