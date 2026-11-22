"""Small dependency-free raster previews for orthographic mesh drawings."""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Mapping, Sequence
from typing import Any

from .orthographic_drawing import _arc_extrema_points, _validated_curve_primitives


_MIN_DIMENSION = 64
_MAX_DIMENSION = 2048
_MAX_PIXELS = 2_000_000
_MAX_SEGMENTS = 20_000
_MAX_PNG_BYTES = 8_000_000
_INK = (23, 33, 43)
_HIDDEN = (145, 155, 165)
_HATCH = (176, 176, 176)
_RULE = (220, 225, 230)
_WHITE = (255, 255, 255)

# Five-by-seven uppercase bitmap font. Unsupported characters render as '?'.
_FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "×": ("00000", "10001", "01010", "00100", "01010", "10001", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    ":": ("00000", "00110", "00110", "00000", "00110", "00110", "00000"),
    " ": ("00000",) * 7,
}


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{field}")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid_{field}") from None
    if not math.isfinite(result):
        raise ValueError(f"invalid_{field}")
    return result


def _point(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"invalid_{field}")
    return (_number(value[0], field), _number(value[1], field))


def _validate_views(views: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(views, (list, tuple)) or not 1 <= len(views) <= 3:
        raise ValueError("invalid_views")
    normalized: list[dict[str, Any]] = []
    segment_count = 0
    seen_names: set[str] = set()
    for view in views:
        if not isinstance(view, Mapping):
            raise ValueError("invalid_view")
        name = view.get("name")
        if name not in {"FRONT", "TOP", "SIDE", "SECTION"} or name in seen_names:
            raise ValueError("invalid_view_name")
        seen_names.add(name)
        bounds = view.get("bounds")
        if not isinstance(bounds, Mapping):
            raise ValueError("invalid_bounds")
        xmin, ymin = _point(bounds.get("min"), "bounds")
        xmax, ymax = _point(bounds.get("max"), "bounds")
        dx, dy = xmax - xmin, ymax - ymin
        if not math.isfinite(dx) or not math.isfinite(dy) or dx < 0 or dy < 0:
            raise ValueError("invalid_bounds")
        dimensions = view.get("dimensions")
        if not isinstance(dimensions, Mapping):
            raise ValueError("invalid_dimensions")
        width = _number(dimensions.get("width"), "dimensions")
        height = _number(dimensions.get("height"), "dimensions")
        unit = dimensions.get("unit")
        if width < 0 or height < 0 or not isinstance(unit, str) or not unit.strip() or len(unit) > 32:
            raise ValueError("invalid_dimensions")
        # Keep the displayed dimensions truthful: the bounds drive the
        # projection scale, so a mismatched label would otherwise describe a
        # different shape than the linework shown in the panel.  Use a small
        # relative tolerance for values that have crossed a JSON/float
        # boundary, matching the tolerance used by the DXF exporter.
        tolerance = max(1e-9, abs(dx) * 1e-9, abs(dy) * 1e-9)
        if abs(width - dx) > tolerance or abs(height - dy) > tolerance:
            raise ValueError("dimensions_bounds_mismatch")
        lines = view.get("lines")
        if not isinstance(lines, (list, tuple)):
            raise ValueError("invalid_lines")
        segment_count += len(lines)
        if segment_count > _MAX_SEGMENTS:
            raise ValueError("drawing_segment_limit_exceeded")
        normalized_lines: list[dict[str, Any]] = []
        for line_index, line in enumerate(lines):
            if not isinstance(line, Mapping):
                raise ValueError("invalid_line")
            start = _point(line.get("start"), "line")
            end = _point(line.get("end"), "line")
            kind = line.get("kind")
            visibility = line.get("visibility")
            if kind not in {"silhouette", "feature", "hatch"} or (kind == "hatch" and name != "SECTION"):
                raise ValueError("invalid_line_kind")
            if visibility not in (None, "visible", "hidden"):
                raise ValueError("invalid_line_visibility")
            if kind == "hatch" and visibility == "hidden":
                raise ValueError("hidden_hatch_unsupported")
            axis_name = line.get("axis_name")
            if axis_name is not None and (kind != "feature" or axis_name not in {"X", "Y", "Z"}):
                raise ValueError("invalid_axis_label")
            if not all(xmin <= p[0] <= xmax and ymin <= p[1] <= ymax for p in (start, end)):
                raise ValueError("line_outside_bounds")
            normalized_line = {"start": start, "end": end, "kind": kind,
                               "visibility": visibility, "source_index": line_index}
            if axis_name is not None:
                normalized_line["axis_name"] = axis_name
            normalized_lines.append(normalized_line)
        normalized_curves: list[dict[str, Any]] = []
        curves = _validated_curve_primitives(view)
        for curve in curves:
            center = curve.get("center")
            cx, cy = _number(center[0], "curve_center"), _number(center[1], "curve_center")
            kind = curve.get("kind", "feature")
            curve_type = curve["type"]
            if curve_type == "arc":
                radius_value = _number(curve.get("radius"), "curve_radius")
                major_axis = None
                major_radius = minor_radius = radius_value
            elif curve_type == "circle":
                radius_value = _number(curve.get("radius"), "curve_radius")
                major_axis = None
                major_radius = minor_radius = radius_value
            else:
                major_axis = _point(curve.get("major_axis"), "curve_major_axis")
                major_radius = _number(curve.get("major_radius"), "curve_major_radius")
                minor_radius = _number(curve.get("minor_radius"), "curve_minor_radius")
                radius_value = major_radius
            if curve_type == "arc":
                start_angle = _number(curve.get("start_angle_degrees"), "curve_angle")
                end_angle = _number(curve.get("end_angle_degrees"), "curve_angle")
                if any(not (xmin - tolerance <= x <= xmax + tolerance and
                            ymin - tolerance <= y <= ymax + tolerance)
                           for x, y in _arc_extrema_points(curve)):
                    raise ValueError("curve_outside_bounds")
            elif not (xmin - tolerance <= cx <= xmax + tolerance and
                      ymin - tolerance <= cy <= ymax + tolerance and
                      radius_value <= max(width, height) * 2.0 + tolerance):
                raise ValueError("curve_outside_bounds")
            source_indices = curve.get("source_line_indices", ())
            normalized_curve = {"type": curve_type, "center": (cx, cy),
                                "kind": kind, "source_line_indices": tuple(source_indices)}
            if curve_type == "arc":
                normalized_curve.update(radius=radius_value,
                                        start_angle_degrees=start_angle,
                                        end_angle_degrees=end_angle)
            elif curve_type == "circle":
                normalized_curve["radius"] = radius_value
            else:
                normalized_curve.update(major_axis=major_axis, major_radius=major_radius,
                                        minor_radius=minor_radius)
            normalized_curves.append(normalized_curve)
        normalized.append({"name": name, "bounds": (xmin, ymin, xmax, ymax),
                           "dimensions": (width, height), "unit": unit.strip(),
                           "dimension_label": (view.get("dimension_label")
                                               if isinstance(view.get("dimension_label"), str) and
                                               view.get("dimension_label").isascii() and
                                               len(view.get("dimension_label")) <= 32 else ""),
                           "lines": normalized_lines, "curves": normalized_curves})
    return normalized, segment_count


def _set_pixel(pixels: bytearray, width: int, height: int, x: int, y: int,
               color: tuple[int, int, int], clip: tuple[int, int, int, int] | None = None) -> None:
    if x < 0 or x >= width or y < 0 or y >= height:
        return
    if clip is not None and not (clip[0] <= x < clip[2] and clip[1] <= y < clip[3]):
        return
    offset = (y * width + x) * 3
    pixels[offset:offset + 3] = bytes(color)


def _line(pixels: bytearray, width: int, height: int, x0: int, y0: int,
          x1: int, y1: int, color: tuple[int, int, int],
          clip: tuple[int, int, int, int] | None = None, dashed: bool = False) -> None:
    """Draw clipped Bresenham strokes; clipping keeps work bounded by canvas size."""
    if clip is None:
        clip = (0, 0, width, height)
    left, top, right, bottom = clip
    # Clamp endpoints before Bresenham, preventing off-canvas huge coordinates
    # from creating unbounded loops. Line geometry is within the mesh bounds.
    x0, x1 = max(left, min(right - 1, x0)), max(left, min(right - 1, x1))
    y0, y1 = max(top, min(bottom - 1, y0)), max(top, min(bottom - 1, y1))
    dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
    dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
    error = dx + dy
    step = 0
    while True:
        if not dashed or (step // 4) % 2 == 0:
            _set_pixel(pixels, width, height, x0, y0, color, clip)
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy
        step += 1


def _text(pixels: bytearray, width: int, height: int, x: int, y: int, text: str,
          color: tuple[int, int, int], clip: tuple[int, int, int, int]) -> None:
    cursor = x
    for character in text.upper():
        glyph = _FONT.get(character, _FONT["?"])
        for row, pattern in enumerate(glyph):
            for column, bit in enumerate(pattern):
                if bit == "1":
                    _set_pixel(pixels, width, height, cursor + column, y + row, color, clip)
        cursor += 6
        if cursor >= clip[2]:
            break


def _clip_segment(x0: float, y0: float, x1: float, y1: float,
                  clip: tuple[int, int, int, int]) -> tuple[float, float, float, float] | None:
    """Cohen-Sutherland clipping against the pixel-center clip rectangle."""
    left, top, right, bottom = clip[0], clip[1], clip[2] - 1, clip[3] - 1

    def code(x: float, y: float) -> int:
        # Projection arithmetic can put a nominal bound a few ulps outside
        # the integer pixel rectangle; treat that as on-boundary.
        epsilon = 1e-9
        return ((1 if x < left - epsilon else 0) | (2 if x > right + epsilon else 0) |
                (4 if y < top - epsilon else 0) | (8 if y > bottom + epsilon else 0))

    c0, c1 = code(x0, y0), code(x1, y1)
    for _ in range(8):
        if not (c0 | c1):
            return x0, y0, x1, y1
        if c0 & c1:
            return None
        out = c0 or c1
        if out & 8:
            if y1 == y0:
                return None
            x, y = x0 + (x1 - x0) * (bottom - y0) / (y1 - y0), float(bottom)
        elif out & 4:
            if y1 == y0:
                return None
            x, y = x0 + (x1 - x0) * (top - y0) / (y1 - y0), float(top)
        elif out & 2:
            if x1 == x0:
                return None
            x, y = float(right), y0 + (y1 - y0) * (right - x0) / (x1 - x0)
        else:
            if x1 == x0:
                return None
            x, y = float(left), y0 + (y1 - y0) * (left - x0) / (x1 - x0)
        if out == c0:
            x0, y0, c0 = x, y, code(x, y)
        else:
            x1, y1, c1 = x, y, code(x, y)
    return None


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def rasterize_orthographic_png(views: Any, *, width: int = 1200, height: int = 440,
                              footer: str | None = None) -> bytes:
    """Render supplied orthographic line views as a bounded deterministic RGB PNG."""
    if type(width) is not int or type(height) is not int or not (
            _MIN_DIMENSION <= width <= _MAX_DIMENSION and _MIN_DIMENSION <= height <= _MAX_DIMENSION):
        raise ValueError("invalid_image_dimensions")
    if width * height > _MAX_PIXELS:
        raise ValueError("image_pixel_limit_exceeded")
    if footer is not None and (not isinstance(footer, str) or len(footer) > 128 or not footer.isascii()):
        raise ValueError("invalid_image_footer")
    normalized, _segment_count = _validate_views(views)

    pixels = bytearray(_WHITE * (width * height))
    count = len(normalized)
    margin = max(6, min(24, width // 40))
    gap = max(4, min(20, width // 60))
    header = 23
    panel_width = (width - 2 * margin - (count - 1) * gap) / count
    panel_top = margin
    panel_bottom = height - margin - 12
    # Leave one pixel for the lower clipping boundary: PNG pixel coordinates
    # are integer-centered while the panel's right/bottom bounds are exclusive.
    available_height = max(1, panel_bottom - panel_top - header - 1)
    available_width = max(1.0, panel_width - 12)
    scale_limits: list[float] = []
    for item in normalized:
        xmin, ymin, xmax, ymax = item["bounds"]
        dx, dy = xmax - xmin, ymax - ymin
        limits = [available_width / dx if dx > 0 else math.inf,
                  available_height / dy if dy > 0 else math.inf]
        scale_limits.append(min(limits))
    scale = min(scale_limits, default=1.0)
    if not math.isfinite(scale) or scale <= 0:
        scale = 1.0

    for index, item in enumerate(normalized):
        panel_left = margin + int(index * (panel_width + gap))
        panel_right = margin + int((index + 1) * panel_width + index * gap)
        clip = (panel_left + 2, panel_top + header, max(panel_left + 3, panel_right - 2), panel_bottom)
        if index:
            _line(pixels, width, height, panel_left - gap // 2, panel_top,
                  panel_left - gap // 2, panel_bottom, _RULE)
        _text(pixels, width, height, panel_left + 4, panel_top + 2,
              item["name"], _INK, (panel_left, panel_top, panel_right, panel_bottom))
        dimension_width, dimension_height = item["dimensions"]
        prefix = item["dimension_label"]
        label = (f"{prefix} " if prefix else "") + f"{dimension_width:.2f} × {dimension_height:.2f} {item['unit']}"
        _text(pixels, width, height, panel_left + 4, panel_top + 12,
              label, _INK, (panel_left, panel_top + 10, panel_right, panel_bottom))

        xmin, ymin, xmax, ymax = item["bounds"]
        dx, dy = xmax - xmin, ymax - ymin
        content_w, content_h = dx * scale, dy * scale
        offset_x = clip[0] + (clip[2] - clip[0] - content_w) / 2.0
        offset_y = clip[1] + max(0.0, (clip[3] - clip[1] - 1 - content_h) / 2.0)
        fitted_source_indices = {
            source_index for curve in item["curves"]
            for source_index in curve["source_line_indices"]
        }
        for segment in sorted(item["lines"], key=lambda line: line["kind"] != "hatch"):
            if segment["source_index"] in fitted_source_indices:
                continue
            ax, ay = segment["start"]
            bx, by = segment["end"]
            x0, y0 = offset_x + (ax - xmin) * scale, offset_y + (ymax - ay) * scale
            x1, y1 = offset_x + (bx - xmin) * scale, offset_y + (ymax - by) * scale
            clipped = _clip_segment(x0, y0, x1, y1, clip)
            if clipped is None:
                continue
            color = _HATCH if segment["kind"] == "hatch" else _HIDDEN if segment["visibility"] == "hidden" else _INK
            dashed = segment["visibility"] == "hidden"
            _line(pixels, width, height, *(int(round(value)) for value in clipped),
                  color, clip=clip, dashed=dashed)
            axis_name = segment.get("axis_name")
            if isinstance(axis_name, str) and axis_name:
                _text(pixels, width, height, int(round(clipped[2])) + 2, int(round(clipped[3])) - 7,
                      axis_name, _INK, clip)
        for curve in item["curves"]:
            cx, cy = curve["center"]
            center_x = offset_x + (cx - xmin) * scale
            center_y = offset_y + (ymax - cy) * scale
            if curve["type"] in {"circle", "arc"}:
                major_axis = (curve["radius"], 0.0)
                major_radius = minor_radius = curve["radius"]
            else:
                major_axis = curve["major_axis"]
                major_radius = curve["major_radius"]
                minor_radius = curve["minor_radius"]
            major_axis_length = math.hypot(*major_axis)
            direction = (major_axis[0] / major_axis_length, major_axis[1] / major_axis_length)
            perpendicular = (-direction[1], direction[0])
            major_radius_px = major_radius * scale
            minor_radius_px = minor_radius * scale
            # A bounded adaptive polyline is used only for rasterization; the
            # SVG renderer carries the smooth vector conic while source lines
            # remain available to structured/DXF consumers.
            full_sample_count = max(32, min(512, int(math.ceil(math.pi * math.sqrt(
                2.0 * max(major_radius_px, minor_radius_px, 0.25))))))
            if curve["type"] == "arc":
                start_angle = math.radians(curve["start_angle_degrees"])
                end_angle = math.radians(curve["end_angle_degrees"])
                angular_span = end_angle - start_angle
                sample_count = max(8, min(512, int(math.ceil(
                    full_sample_count * angular_span / (2.0 * math.pi)))))
                angles = [start_angle + angular_span * step / sample_count
                          for step in range(sample_count + 1)]
            else:
                sample_count = full_sample_count
                angles = [2.0 * math.pi * step / sample_count for step in range(sample_count)]
            points = [(center_x + (direction[0] * major_radius_px * math.cos(angle)
                                   + perpendicular[0] * minor_radius_px * math.sin(angle)),
                       center_y - (direction[1] * major_radius_px * math.cos(angle)
                                   + perpendicular[1] * minor_radius_px * math.sin(angle)))
                      for angle in angles]
            segment_count = sample_count if curve["type"] != "arc" else sample_count
            for step in range(segment_count):
                start = points[step]
                end = points[step + 1] if curve["type"] == "arc" else points[(step + 1) % sample_count]
                clipped = _clip_segment(start[0], start[1], end[0], end[1], clip)
                if clipped is not None:
                    _line(pixels, width, height, *(int(round(value)) for value in clipped),
                          _INK, clip=clip)

    if footer:
        _text(pixels, width, height, margin, height - margin, footer, _INK,
              (margin, height - margin, width - margin, height - 1))

    scanlines = bytearray()
    row_size = width * 3
    for y in range(height):
        scanlines.append(0)  # PNG filter: None
        start = y * row_size
        scanlines.extend(pixels[start:start + row_size])
    compressed = zlib.compress(bytes(scanlines), level=6)
    metadata = _png_chunk(b"tEXt", b"Comment\x00" + footer.encode("latin-1")) if footer else b""
    png = (b"\x89PNG\r\n\x1a\n" +
           _png_chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)) + metadata +
           _png_chunk(b"IDAT", compressed) + _png_chunk(b"IEND", b""))
    if len(png) > _MAX_PNG_BYTES:
        raise ValueError("image_output_limit_exceeded")
    return png
