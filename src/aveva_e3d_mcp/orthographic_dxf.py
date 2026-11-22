"""Bounded, dependency-free ASCII DXF export for orthographic line drawings."""

from __future__ import annotations

import math
from collections.abc import Mapping
from itertools import count
from typing import Any

from .section_hatch_precision import validate_section_hatch_precision


MAX_DXF_LINES = 20_000
MAX_DXF_BYTES = 8_000_000
MAX_ABS_COORDINATE_MM = 1_000_000_000.0
VIEW_NAMES = ("FRONT", "TOP", "SIDE", "SECTION")
VIEW_GAP_MM = 50.0
_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}


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
    return _number(value[0], field), _number(value[1], field)


def _fmt(value: float) -> str:
    # Fixed decimal output is deterministic and accepted by ASCII DXF readers.
    result = format(value, ".6f")
    return "0.000000" if result == "-0.000000" else result


def _validate_coordinate_decimal_places(value: Any) -> None:
    if type(value) is not int or not 6 <= value <= 15:
        raise ValueError("invalid_coordinate_decimal_places")


def _pair(lines: list[str], code: int, value: str | int) -> None:
    lines.extend((str(code), str(value)))


class _DxfDocument:
    def __init__(self, *, coordinate_decimal_places: int = 6) -> None:
        _validate_coordinate_decimal_places(coordinate_decimal_places)
        self.lines: list[str] = []
        self._handles = count(1)
        self._coordinate_format = f".{coordinate_decimal_places}f"
        self._coordinate_zero = "0." + "0" * coordinate_decimal_places
        self.model_space = self.handle()

    def format_coordinate(self, value: float) -> str:
        result = format(value, self._coordinate_format)
        return self._coordinate_zero if result == "-" + self._coordinate_zero else result

    def handle(self) -> str:
        return format(next(self._handles), "X")

    def write(self, *tags: tuple[int, str | int]) -> None:
        for code, value in tags:
            _pair(self.lines, code, value)

    def record(self, kind: str, handle: str, owner: str, *subclasses: str) -> None:
        self.write((0, kind), (5, handle), (330, owner),
                   *((100, subclass) for subclass in subclasses))

    def table(self, name: str, size: int) -> str:
        handle = self.handle()
        self.write((0, "TABLE"), (2, name), (5, handle), (330, "0"),
                   (100, "AcDbSymbolTable"), (70, size))
        return handle

    def symbol(self, kind: str, owner: str, subclass: str) -> None:
        self.record(kind, self.handle(), owner, "AcDbSymbolTableRecord", subclass)

    def entity(self, kind: str, owner: str, layer: str, *subclasses: str) -> None:
        self.record(kind, self.handle(), owner, "AcDbEntity")
        self.write((8, layer), *((100, subclass) for subclass in subclasses))


def _layout_point(point: tuple[float, float]) -> tuple[float, float]:
    if any(not math.isfinite(value) or abs(value) > MAX_ABS_COORDINATE_MM for value in point):
        raise ValueError("layout_out_of_bounds")
    return point


def _line_entity(doc: _DxfDocument, layer: str, start: tuple[float, float], end: tuple[float, float]) -> bool:
    start = _layout_point(start)
    end = _layout_point(end)
    start_text = (doc.format_coordinate(start[0]), doc.format_coordinate(start[1]))
    end_text = (doc.format_coordinate(end[0]), doc.format_coordinate(end[1]))
    # Test the actual serialized layout coordinates, including any float
    # rounding caused by adding the layout origin. Never emit a zero-length
    # entity when the requested precision cannot preserve a source interval.
    if start_text == end_text:
        return False
    doc.entity("LINE", doc.model_space, layer, "AcDbLine")
    zero = doc.format_coordinate(0.0)
    doc.write((10, start_text[0]), (20, start_text[1]), (30, zero),
              (11, end_text[0]), (21, end_text[1]), (31, zero))
    return True


def _circle_entity(doc: _DxfDocument, layer: str, center: tuple[float, float], radius: float) -> bool:
    center = _layout_point(center)
    if (isinstance(radius, bool) or not math.isfinite(radius) or radius <= 0
            or radius > MAX_ABS_COORDINATE_MM
            or any(abs(value) + radius > MAX_ABS_COORDINATE_MM for value in center)):
        raise ValueError("layout_out_of_bounds")
    x, y = map(doc.format_coordinate, center)
    serialized_radius = doc.format_coordinate(radius)
    if serialized_radius == doc.format_coordinate(0.0):
        return False
    zero = doc.format_coordinate(0.0)
    doc.entity("CIRCLE", doc.model_space, layer, "AcDbCircle")
    doc.write((10, x), (20, y), (30, zero), (40, serialized_radius),
              (210, zero), (220, zero), (230, doc.format_coordinate(1.0)))
    return True


def _arc_entity(doc: _DxfDocument, layer: str, center: tuple[float, float], radius: float,
                start_angle_degrees: float, end_angle_degrees: float) -> bool:
    center = _layout_point(center)
    if (isinstance(radius, bool) or not math.isfinite(radius) or radius <= 0
            or radius > MAX_ABS_COORDINATE_MM
            or not math.isfinite(start_angle_degrees) or not math.isfinite(end_angle_degrees)
            or not 0.0 < end_angle_degrees - start_angle_degrees < 360.0
            or any(abs(value) + radius > MAX_ABS_COORDINATE_MM for value in center)):
        raise ValueError("invalid_arc")
    start_text = doc.format_coordinate(start_angle_degrees % 360.0)
    end_text = doc.format_coordinate(end_angle_degrees % 360.0)
    if start_text == end_text:
        return False
    x, y = map(doc.format_coordinate, center)
    serialized_radius = doc.format_coordinate(radius)
    if serialized_radius == doc.format_coordinate(0.0):
        return False
    zero = doc.format_coordinate(0.0)
    doc.entity("ARC", doc.model_space, layer, "AcDbCircle", "AcDbArc")
    doc.write((10, x), (20, y), (30, zero), (40, serialized_radius),
              (50, start_text), (51, end_text),
              (210, zero), (220, zero), (230, doc.format_coordinate(1.0)))
    return True


def _ellipse_entity(doc: _DxfDocument, layer: str, center: tuple[float, float],
                    major_axis: tuple[float, float], major_radius: float, minor_radius: float) -> bool:
    center = _layout_point(center)
    if (not all(math.isfinite(value) for value in (*major_axis, major_radius, minor_radius))
            or major_radius <= 0 or minor_radius <= 0 or minor_radius > major_radius
            or major_radius > MAX_ABS_COORDINATE_MM
            or abs(math.hypot(*major_axis) - major_radius) > max(1e-9, major_radius * 1e-9)):
        raise ValueError("invalid_ellipse")
    direction = (major_axis[0] / major_radius, major_axis[1] / major_radius)
    perpendicular = (-direction[1], direction[0])
    extent_x = math.hypot(major_axis[0], minor_radius * perpendicular[0])
    extent_y = math.hypot(major_axis[1], minor_radius * perpendicular[1])
    if (abs(center[0]) + extent_x > MAX_ABS_COORDINATE_MM
            or abs(center[1]) + extent_y > MAX_ABS_COORDINATE_MM):
        raise ValueError("layout_out_of_bounds")
    center_text = tuple(map(doc.format_coordinate, center))
    axis_text = tuple(map(doc.format_coordinate, major_axis))
    ratio_text = doc.format_coordinate(minor_radius / major_radius)
    zero, one = doc.format_coordinate(0.0), doc.format_coordinate(1.0)
    if (axis_text == (zero, zero) or ratio_text == zero or
            math.hypot(*map(float, axis_text)) == 0.0):
        return False
    doc.entity("ELLIPSE", doc.model_space, layer, "AcDbEllipse")
    doc.write((10, center_text[0]), (20, center_text[1]), (30, zero),
              (11, axis_text[0]), (21, axis_text[1]), (31, zero),
              (40, ratio_text), (41, zero), (42, doc.format_coordinate(2.0 * math.pi)),
              (210, zero), (220, zero), (230, one))
    return True


def _text_entity(doc: _DxfDocument, layer: str, position: tuple[float, float], text: str,
                 height: float, *, rotation: float = 0.0) -> None:
    position = _layout_point(position)
    if not math.isfinite(height) or height <= 0 or height > MAX_ABS_COORDINATE_MM or not math.isfinite(rotation):
        raise ValueError("layout_out_of_bounds")
    x, y = map(doc.format_coordinate, position)
    zero = doc.format_coordinate(0.0)
    doc.entity("TEXT", doc.model_space, layer, "AcDbText")
    doc.write((10, x), (20, y), (30, zero), (40, doc.format_coordinate(height)),
              (1, text), (7, "STANDARD"), (50, doc.format_coordinate(rotation)), (72, 1),
              (11, x), (21, y), (31, zero), (100, "AcDbText"), (73, 2))


def _validate_view(view: Any, root_unit: Any, show_hidden_lines: bool,
                   *, allow_verified_curves: bool = False) -> dict[str, Any]:
    if not isinstance(view, Mapping):
        raise ValueError("invalid_view")
    name = view.get("name")
    if not isinstance(name, str) or name not in VIEW_NAMES:
        raise ValueError("invalid_view_name")
    bounds = view.get("bounds")
    if not isinstance(bounds, Mapping):
        raise ValueError("invalid_bounds")
    minimum = _point(bounds.get("min"), "bounds")
    maximum = _point(bounds.get("max"), "bounds")
    if maximum[0] < minimum[0] or maximum[1] < minimum[1]:
        raise ValueError("invalid_bounds")
    width, height = maximum[0] - minimum[0], maximum[1] - minimum[1]
    if not math.isfinite(width) or not math.isfinite(height):
        raise ValueError("invalid_bounds")

    dimensions = view.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise ValueError("invalid_dimensions")
    dimension_width = _number(dimensions.get("width"), "dimensions")
    dimension_height = _number(dimensions.get("height"), "dimensions")
    unit = dimensions.get("unit", view.get("coordinate_unit", root_unit))
    if not isinstance(unit, str) or unit not in _UNIT_TO_MM:
        raise ValueError("unsupported_coordinate_unit")
    factor = _UNIT_TO_MM[unit]
    # Dimensions and the declared bounds must describe the same projection.
    tolerance = max(1e-9, abs(width) * 1e-9, abs(height) * 1e-9)
    if abs(width - dimension_width) > tolerance or abs(height - dimension_height) > tolerance:
        raise ValueError("dimensions_bounds_mismatch")
    min_mm = tuple(value * factor for value in minimum)
    max_mm = tuple(value * factor for value in maximum)
    width_mm, height_mm = width * factor, height * factor
    if any(not math.isfinite(value) or abs(value) > MAX_ABS_COORDINATE_MM
           for value in (*min_mm, *max_mm, width_mm, height_mm)):
        raise ValueError("geometry_out_of_bounds")

    segments = view.get("lines")
    if not isinstance(segments, (list, tuple)):
        raise ValueError("invalid_lines")
    if len(segments) > MAX_DXF_LINES:
        raise ValueError("drawing_line_limit_exceeded")
    export_segments: list[dict[str, Any]] = []
    source_to_export_index: dict[int, int] = {}
    for source_index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raise ValueError("invalid_line")
        visibility = segment.get("visibility", "visible")
        if visibility not in ("visible", "hidden"):
            raise ValueError("invalid_line_visibility")
        kind = segment.get("kind")
        if kind not in ("silhouette", "feature", "hatch") or (kind == "hatch" and name != "SECTION"):
            raise ValueError("invalid_line_kind")
        axis_name = segment.get("axis_name")
        if axis_name is not None and (kind != "feature" or axis_name not in {"X", "Y", "Z"}):
            raise ValueError("invalid_axis_label")
        if kind == "hatch" and visibility == "hidden":
            raise ValueError("hidden_hatch_unsupported")
        start = _point(segment.get("start"), "line_endpoint")
        end = _point(segment.get("end"), "line_endpoint")
        if start == end:
            raise ValueError("degenerate_line")
        for point in (start, end):
            if (point[0] < minimum[0] - tolerance or point[0] > maximum[0] + tolerance or
                    point[1] < minimum[1] - tolerance or point[1] > maximum[1] + tolerance):
                raise ValueError("line_outside_bounds")
        if visibility == "hidden" and not show_hidden_lines:
            continue
        export_segment = {
            "kind": kind,
            "visibility": visibility,
            "start": (start[0] * factor, start[1] * factor),
            "end": (end[0] * factor, end[1] * factor),
            "source_index": source_index,
        }
        if axis_name is not None:
            export_segment["axis_name"] = axis_name
        source_to_export_index[source_index] = len(export_segments)
        export_segments.append(export_segment)
    curves: list[dict[str, Any]] = []
    if allow_verified_curves:
        from .orthographic_drawing import _validated_curve_primitives

        for curve in _validated_curve_primitives(view):
            source_indices = curve["source_line_indices"]
            if any(index not in source_to_export_index for index in source_indices):
                raise ValueError("invalid_curve_source_cycle")
            center = curve["center"]
            normalized_curve = {
                "type": curve["type"],
                "center": (center[0] * factor, center[1] * factor),
                "kind": curve["kind"],
                "segment_indices": tuple(source_to_export_index[index] for index in source_indices),
            }
            if curve["type"] in {"circle", "arc"}:
                normalized_curve["radius"] = curve["radius"] * factor
                if curve["type"] == "arc":
                    normalized_curve["start_angle_degrees"] = curve["start_angle_degrees"]
                    normalized_curve["end_angle_degrees"] = curve["end_angle_degrees"]
            else:
                normalized_curve.update({
                    "major_axis": (curve["major_axis"][0] * factor, curve["major_axis"][1] * factor),
                    "major_radius": curve["major_radius"] * factor,
                    "minor_radius": curve["minor_radius"] * factor,
                })
            curves.append(normalized_curve)
    return {
        "name": name,
        "min_x": min_mm[0],
        "min_y": min_mm[1],
        "width": width_mm,
        "height": height_mm,
        "segments": export_segments,
        "curves": curves,
    }


def _write_tables_and_blocks(doc: _DxfDocument, layers: list[str], show_hidden_lines: bool,
                             *, hidden_dash_mm: float = 3.0, hidden_gap_mm: float = 1.5,
                             layout_handles: tuple[str, str] | None = None) -> str:
    paper_space = doc.handle()
    doc.write((0, "SECTION"), (2, "TABLES"))
    ltype_table = doc.table("LTYPE", 4 if show_hidden_lines else 3)
    for name in ("BYBLOCK", "BYLAYER", "CONTINUOUS"):
        doc.symbol("LTYPE", ltype_table, "AcDbLinetypeTableRecord")
        doc.write((2, name), (70, 0), (3, ""), (72, 65), (73, 0), (40, "0.0"))
    if show_hidden_lines:
        doc.symbol("LTYPE", ltype_table, "AcDbLinetypeTableRecord")
        doc.write((2, "E3D_HIDDEN"), (70, 0), (3, "Hidden edges"), (72, 65),
                  (73, 2), (40, str(hidden_dash_mm + hidden_gap_mm)), (49, str(hidden_dash_mm)),
                  (74, 0), (49, str(-hidden_gap_mm)), (74, 0))
    doc.write((0, "ENDTAB"))

    layer_table = doc.table("LAYER", len(layers))
    for name in layers:
        hidden = name.endswith("_HIDDEN")
        doc.symbol("LAYER", layer_table, "AcDbLayerTableRecord")
        doc.write((2, name), (70, 0), (62, 8 if hidden or name.endswith("_HATCH") else 7),
                  (6, "E3D_HIDDEN" if hidden else "CONTINUOUS"))
    doc.write((0, "ENDTAB"))

    style_table = doc.table("STYLE", 1)
    doc.symbol("STYLE", style_table, "AcDbTextStyleTableRecord")
    doc.write((2, "STANDARD"), (70, 0), (40, "0.0"), (41, "1.0"),
              (50, "0.0"), (71, 0), (42, "2.5"), (3, "txt"), (4, ""), (0, "ENDTAB"))

    block_table = doc.table("BLOCK_RECORD", 2)
    spaces = (("*Model_Space", doc.model_space), ("*Paper_Space", paper_space))
    for index, (name, handle) in enumerate(spaces):
        doc.record("BLOCK_RECORD", handle, block_table,
                   "AcDbSymbolTableRecord", "AcDbBlockTableRecord")
        doc.write((2, name))
        if layout_handles is not None:
            doc.write((340, layout_handles[index]))
    doc.write((0, "ENDTAB"), (0, "ENDSEC"), (0, "SECTION"), (2, "BLOCKS"))
    for name, handle in spaces:
        doc.entity("BLOCK", handle, "0", "AcDbBlockBegin")
        doc.write((2, name), (70, 0), (10, "0.0"), (20, "0.0"), (30, "0.0"),
                  (3, name), (1, ""))
        doc.entity("ENDBLK", handle, "0", "AcDbBlockEnd")
    doc.write((0, "ENDSEC"))
    return paper_space


def _write_sheet_layouts(doc: _DxfDocument, *, root: str, layouts: str,
                        layout_handles: tuple[str, str], paper_space: str,
                        paper_size: str, width: float, height: float) -> None:
    """Persist page limits in LAYOUT objects as well as the DXF header.

    CAD writers can refresh header limits from these linked layout records.
    Plotting stays 1:1 because the scene is already expressed in paper mm.
    """
    doc.write((0, "SECTION"), (2, "OBJECTS"))
    doc.record("DICTIONARY", root, "0", "AcDbDictionary")
    doc.write((281, 1), (3, "ACAD_LAYOUT"), (350, layouts))
    doc.record("DICTIONARY", layouts, root, "AcDbDictionary")
    doc.write((281, 1), (3, "Model"), (350, layout_handles[0]),
              (3, "Layout1"), (350, layout_handles[1]))
    for index, (name, block) in enumerate((("Model", doc.model_space), ("Layout1", paper_space))):
        doc.record("LAYOUT", layout_handles[index], layouts, "AcDbPlotSettings")
        doc.write((1, ""), (4, paper_size),
                  (40, "0.0"), (41, "0.0"), (42, "0.0"), (43, "0.0"),
                  (44, doc.format_coordinate(width)), (45, doc.format_coordinate(height)),
                  (70, 1024 if index == 0 else 0), (72, 1), (73, 0), (74, 2),
                  (142, "1.0"), (143, "1.0"), (100, "AcDbLayout"),
                  (1, name), (70, 1), (71, index), (10, "0.0"), (20, "0.0"),
                  (11, doc.format_coordinate(width)), (21, doc.format_coordinate(height)), (330, block))
    doc.write((0, "ENDSEC"))


def export_orthographic_dxf(drawing: Any, *, include_dimensions: bool = True,
                            footer: str | None = None, coordinate_decimal_places: int = 6) -> str:
    """Export a successful orthographic drawing as deterministic R2000 DXF.

    Coordinates are converted to millimetres, laid out in the order supplied,
    and separated by a 50 mm horizontal gap. Explicitly requested hidden
    segments use a dashed linetype on a separate layer for each view.
    ``coordinate_decimal_places`` is an integer from 6 to 15, local to this
    document. It controls fixed decimal coordinates and dimension text; the
    default preserves the existing six-place output. A source interval that
    collapses after layout arithmetic or serialization rejects the export.
    """
    if not isinstance(include_dimensions, bool):
        raise ValueError("include_dimensions must be a boolean")
    _validate_coordinate_decimal_places(coordinate_decimal_places)
    if footer is not None and (not isinstance(footer, str) or len(footer) > 200 or
                               not footer.isascii() or any(ord(char) < 32 for char in footer)):
        raise ValueError("invalid_footer")
    if not isinstance(drawing, Mapping) or drawing.get("success") is not True or drawing.get("complete") is not True:
        raise ValueError("successful_complete_drawing_required")
    views_value = drawing.get("views")
    if not isinstance(views_value, (list, tuple)) or not 1 <= len(views_value) <= 3:
        raise ValueError("invalid_views")
    root_unit = drawing.get("coordinate_unit", "mm")
    show_hidden_lines = drawing.get("show_hidden_lines") is True
    curve_refinement = drawing.get("curve_refinement")
    allow_verified_curves = (
        drawing.get("drawing_kind") == "parameter-derived CSG mesh linework"
        and drawing.get("curve_representation") in {
            "fitted_projected_circles_with_source_edges_retained",
            "fitted_projected_conics_with_source_edges_retained",
            "fitted_projected_curves_with_source_edges_retained",
        }
        and isinstance(curve_refinement, Mapping)
        and curve_refinement.get("mode") in {
            "closed_projected_circle_fit", "closed_projected_conic_fit",
            "source_verified_projected_curve_fit",
        }
        and curve_refinement.get("analytic_source_required") is True
        and curve_refinement.get("analytic_source_verified") is True
    )
    prepared = [_validate_view(view, root_unit, show_hidden_lines,
                               allow_verified_curves=allow_verified_curves) for view in views_value]
    names = [view["name"] for view in prepared]
    if len(set(names)) != len(names):
        raise ValueError("duplicate_view")
    line_count = sum(len(view["segments"]) for view in prepared)
    if line_count > MAX_DXF_LINES:
        raise ValueError("drawing_line_limit_exceeded")

    layers = ["0", "DRAWING_LABEL"]
    pose_only = drawing.get("drawing_kind") == "WORLD equipment pose layout"
    pane_boundary = drawing.get("drawing_kind") == "native PANE control-point polygon"
    for view in prepared:
        name = view["name"]
        if pane_boundary:
            layers.append(f"{name}_BOUNDARY")
        elif pose_only:
            kinds = {segment["kind"] for segment in view["segments"]}
            if "silhouette" in kinds:
                layers.append(f"{name}_OUTLINE")
            if "feature" in kinds:
                layers.append(f"{name}_FEATURE")
        else:
            layers.extend((f"{name}_OUTLINE", f"{name}_FEATURE"))
        if include_dimensions:
            layers.append(f"{name}_DIMENSION")
        if any(segment["kind"] == "hatch" for segment in view["segments"]):
            layers.append(f"{name}_HATCH")
        if show_hidden_lines:
            layers.append(f"{name}_HIDDEN")
    doc = _DxfDocument(coordinate_decimal_places=coordinate_decimal_places)
    fmt = doc.format_coordinate
    doc.write((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1015"),
              (9, "$INSUNITS"), (70, 4), (0, "ENDSEC"))
    _write_tables_and_blocks(doc, layers, show_hidden_lines)
    doc.write((0, "SECTION"), (2, "ENTITIES"))
    x_offset = 0.0
    for view in prepared:
        name = view["name"]
        width, height = view["width"], view["height"]
        text_height = max(2.5, min(max(width, height) * 0.025, 25.0))
        padding = max(max(width, height) * 0.1, text_height * 12.0)
        geometry_origin_x = x_offset + 2.0 * padding
        geometry_origin_y = 2.0 * padding
        validate_section_hatch_precision(view["segments"], transform=lambda point: (
            geometry_origin_x + (point[0] - view["min_x"]),
            geometry_origin_y + (point[1] - view["min_y"])))
        label = f"{name} {fmt(width)} x {fmt(height)} mm" if include_dimensions else name
        _text_entity(doc, "DRAWING_LABEL", (geometry_origin_x, geometry_origin_y + height + 1.5 * text_height),
                     label, text_height)
        fitted_segment_indices = {index for curve in view["curves"] for index in curve["segment_indices"]}
        for curve in view["curves"]:
            x = geometry_origin_x + (curve["center"][0] - view["min_x"])
            y = geometry_origin_y + (curve["center"][1] - view["min_y"])
            layer = f"{name}_{'OUTLINE' if curve['kind'] == 'silhouette' else 'FEATURE'}"
            if curve["type"] == "circle":
                emitted = _circle_entity(doc, layer, (x, y), curve["radius"])
            elif curve["type"] == "arc":
                emitted = _arc_entity(doc, layer, (x, y), curve["radius"],
                                      curve["start_angle_degrees"], curve["end_angle_degrees"])
            else:
                emitted = _ellipse_entity(doc, layer, (x, y), curve["major_axis"],
                                          curve["major_radius"], curve["minor_radius"])
            if not emitted:
                raise ValueError("geometry_below_dxf_precision")
        for segment_index, segment in enumerate(view["segments"]):
            if segment_index in fitted_segment_indices:
                continue
            if segment["kind"] == "hatch":
                layer = f"{name}_HATCH"
            elif pane_boundary:
                layer = f"{name}_BOUNDARY"
            elif segment["visibility"] == "hidden":
                layer = f"{name}_HIDDEN"
            else:
                layer = f"{name}_{'OUTLINE' if segment['kind'] == 'silhouette' else 'FEATURE'}"
            start, end = segment["start"], segment["end"]
            x1 = geometry_origin_x + (start[0] - view["min_x"])
            y1 = geometry_origin_y + (start[1] - view["min_y"])
            x2 = geometry_origin_x + (end[0] - view["min_x"])
            y2 = geometry_origin_y + (end[1] - view["min_y"])
            if not _line_entity(doc, layer, (x1, y1), (x2, y2)):
                raise ValueError("geometry_below_dxf_precision")
            if isinstance(segment.get("axis_name"), str):
                _text_entity(doc, layer, (x2 + text_height * 0.35, y2 + text_height * 0.35),
                             segment["axis_name"], max(1.0, text_height * 0.5))

        if include_dimensions:
            # Overall projected dimensions: extension lines, dimension line,
            # inward arrowheads and explicit millimetre labels on their own layer.
            dimension_layer = f"{name}_DIMENSION"
            x_min, x_max = geometry_origin_x, geometry_origin_x + width
            y_min, y_max = geometry_origin_y, geometry_origin_y + height
            horizontal_y = geometry_origin_y - padding
            vertical_x = geometry_origin_x - padding
            arrow = max(1.0, min(text_height * 1.2, max(width, height) * 0.04))
            arrow_half = arrow * 0.32

            # A zero extent has no meaningful dimension line. The view label still
            # reports the zero value, while omitting overlapping arrows and text.
            # Use this document's serialized value at the final DXF precision.
            if float(fmt(width)) > 0.0:
                _line_entity(doc, dimension_layer, (x_min, y_min), (x_min, horizontal_y))
                _line_entity(doc, dimension_layer, (x_max, y_min), (x_max, horizontal_y))
                _line_entity(doc, dimension_layer, (x_min, horizontal_y), (x_max, horizontal_y))
                for tip, direction in ((x_min, 1.0), (x_max, -1.0)):
                    for sign in (-1.0, 1.0):
                        _line_entity(doc, dimension_layer, (tip, horizontal_y),
                                     (tip + direction * arrow, horizontal_y + sign * arrow_half))
                _text_entity(doc, dimension_layer, ((x_min + x_max) / 2.0, horizontal_y - 0.75 * text_height),
                             f"{fmt(width)} mm", text_height)

            if float(fmt(height)) > 0.0:
                _line_entity(doc, dimension_layer, (x_min, y_min), (vertical_x, y_min))
                _line_entity(doc, dimension_layer, (x_min, y_max), (vertical_x, y_max))
                _line_entity(doc, dimension_layer, (vertical_x, y_min), (vertical_x, y_max))
                for tip, direction in ((y_min, 1.0), (y_max, -1.0)):
                    for sign in (-1.0, 1.0):
                        _line_entity(doc, dimension_layer, (vertical_x, tip),
                                     (vertical_x + sign * arrow_half, tip + direction * arrow))
                _text_entity(doc, dimension_layer, (vertical_x - 0.75 * text_height, (y_min + y_max) / 2.0),
                             f"{fmt(height)} mm", text_height, rotation=90.0)

        x_offset += width + 3.0 * padding + VIEW_GAP_MM
        if not math.isfinite(x_offset) or x_offset > MAX_ABS_COORDINATE_MM:
            raise ValueError("layout_out_of_bounds")
    if footer:
        text_height = max(2.5, min(max((max(view["width"], view["height"]) for view in prepared), default=0.0) * 0.025, 25.0))
        _text_entity(doc, "DRAWING_LABEL", (0.0, 0.0), footer, text_height)
    doc.write((0, "ENDSEC"), (0, "SECTION"), (2, "OBJECTS"))
    doc.record("DICTIONARY", doc.handle(), "0", "AcDbDictionary")
    doc.write((281, 1), (0, "ENDSEC"), (0, "EOF"))
    result = "\n".join(doc.lines) + "\n"
    if len(result.encode("ascii", errors="strict")) > MAX_DXF_BYTES:
        raise ValueError("drawing_output_limit_exceeded")
    return result


def export_drawing_sheet_dxf(scene: Any, *, coordinate_decimal_places: int = 6) -> str:
    """Export the shared sheet scene in paper mm at its declared scale.

    Entities are deliberately in modelspace at paper coordinates, so importing
    the page never changes source model coordinates or creates a native Draft
    sheet. Dimension labels were resolved by the scene in true model units.
    ``coordinate_decimal_places`` is a document-local integer from 6 to 15;
    its default preserves the existing six-place output. Scene metadata does
    not select it, and any serialized zero-length stroke rejects the export.
    """
    from .drawing_sheet_render import validate_drawing_sheet_scene

    _validate_coordinate_decimal_places(coordinate_decimal_places)
    normalized = validate_drawing_sheet_scene(scene)
    validate_section_hatch_precision(
        ({**stroke, "kind": stroke["role"]} for stroke in normalized["strokes"] if stroke["view_name"] == "SECTION"))
    denominator = _number(scene.get("scale_denominator"), "sheet_scale_denominator")
    if denominator <= 0 or scene.get("coordinate_unit") != "mm" or scene.get("model_coordinate_unit") != "mm":
        raise ValueError("invalid_sheet_scale_or_units")
    paper = normalized["paper"]
    width, height = paper["width_mm"], paper["height_mm"]

    def layer_for(item: dict[str, Any]) -> str:
        view = item["view_name"]
        role = item["role"]
        if role == "border":
            return "SHEET_BORDER"
        if role == "title_block":
            return "SHEET_TITLEBLOCK"
        if view is not None:
            if item.get("visibility") == "hidden":
                return f"{view}_HIDDEN"
            if role in {"silhouette", "feature", "dimension", "hatch"}:
                suffix = {"silhouette": "OUTLINE", "feature": "FEATURE", "dimension": "DIMENSION", "hatch": "HATCH"}[role]
                return f"{view}_{suffix}"
        return "DRAWING_LABEL"

    layers = ["0"]
    for item in (*normalized["strokes"], *normalized["texts"]):
        name = layer_for(item)
        if name not in layers:
            layers.append(name)
    hidden = any(name.endswith("_HIDDEN") for name in layers)
    doc = _DxfDocument(coordinate_decimal_places=coordinate_decimal_places)
    root, layouts = doc.handle(), doc.handle()
    layout_handles = (doc.handle(), doc.handle())
    doc.write((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1015"),
              (9, "$INSUNITS"), (70, 4), (9, "$MEASUREMENT"), (70, 1),
              (9, "$INSBASE"), (10, "0.0"), (20, "0.0"), (30, "0.0"),
              (9, "$LIMMIN"), (10, "0.0"), (20, "0.0"),
              (9, "$LIMMAX"), (10, doc.format_coordinate(width)), (20, doc.format_coordinate(height)),
              (999, f"E3D PAPER-MM SCENE SCALE 1:{denominator:.12g}; MODEL DIMENSION LABELS IN MM"),
              (0, "ENDSEC"))
    paper_space = _write_tables_and_blocks(doc, layers, hidden, hidden_dash_mm=2.0, hidden_gap_mm=1.0,
                                          layout_handles=layout_handles)
    doc.write((0, "SECTION"), (2, "ENTITIES"))
    for stroke in normalized["strokes"]:
        if not _line_entity(doc, layer_for(stroke), stroke["start"], stroke["end"]):
            raise ValueError("sheet_geometry_below_dxf_precision")
    for text in normalized["texts"]:
        _text_entity(doc, layer_for(text), text["position"], text["text"], text["height"],
                     rotation=text["rotation"])
    doc.write((0, "ENDSEC"))
    _write_sheet_layouts(doc, root=root, layouts=layouts, layout_handles=layout_handles,
                        paper_space=paper_space, paper_size=paper["size"], width=width, height=height)
    doc.write((0, "EOF"))
    result = "\n".join(doc.lines) + "\n"
    if len(result.encode("ascii", errors="strict")) > MAX_DXF_BYTES:
        raise ValueError("drawing_output_limit_exceeded")
    return result
