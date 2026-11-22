"""Independent adversarial cases for the final vector encoding guard."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.section_hatch_precision import validate_section_hatch_precision


def contour_rows(points):
    return [{"kind": "silhouette", "start": list(a), "end": list(b)}
            for a, b in zip(points, points[1:] + points[:1])]


def hatch(a, b):
    return {"kind": "hatch", "start": list(a), "end": list(b)}


def micro(points):
    return [[x * 1e-6, y * 1e-6] for x, y in points]


class SectionPrecisionAuditTests(unittest.TestCase):
    def test_subgrid_triangle_winding_flip_is_rejected_with_distinct_vertices(self):
        # Source area is positive; encoded A=(0,0),B=(2,1e-6),C=(1,0)
        # has negative area. Every encoded vertex and edge remains distinct.
        triangle = [[0, 0], [2, .00000051], [1, .00000049]]
        rows = contour_rows(triangle) + [hatch([.8, .00000023], [1.2, .00000035])]
        with self.assertRaisesRegex(ValueError, "section_hatch_output_contour"):
            validate_section_hatch_precision(rows)

    def test_nested_contour_cannot_jump_outside_without_touch_or_crossing(self):
        # The inner triangle is strictly inside the original outer triangle,
        # but all its encoded vertices lie beyond outer AB. Neither loop
        # flips winding and no encoded pair touches, so segment tests alone
        # cannot establish unchanged nonzero fill.
        outer = micro([[.49, .49], [20.49, 4.49], [20, -20]])
        inner = micro([[6.2741945999348765, 1.6087844015592028],
                       [4.093628354593523, 1.0983605960610827],
                       [16.546871785429254, 3.624248374643205]])
        rows = contour_rows(outer) + contour_rows(inner) + [hatch(*micro([[10, -5], [15, -5]]))]
        with self.assertRaisesRegex(ValueError, "section_hatch_output_contour"):
            validate_section_hatch_precision(rows)

    def test_actual_affine_mapping_can_flip_winding_although_model_rounding_is_safe(self):
        # The small Y extent is representable in model coordinates; it is
        # the output's anisotropic transform that exposes the grid failure.
        triangle = [[0, 0], [2, .51], [1, .49]]
        rows = contour_rows(triangle) + [hatch([.8, .23], [1.2, .35])]
        validate_section_hatch_precision(rows)
        with self.assertRaisesRegex(ValueError, "section_hatch_output_contour"):
            validate_section_hatch_precision(rows, transform=lambda p: (p[0], p[1] * 1e-6))

    def test_internal_void_smaller_than_one_grid_is_still_rejected(self):
        # A sloped one-grid-wide triangular void can intersect a hatch for
        # less than one grid unit. The endpoint tail exception must never
        # authorize such an internal empty interval.
        outer = micro([[0, 0], [20, 0], [20, 20], [0, 20]])
        hole = micro([[10, 5], [10, 15], [11, 15]])
        rows = contour_rows(outer) + contour_rows(hole) + [hatch(*micro([[2, 6], [18, 6]]))]
        with self.assertRaisesRegex(ValueError, "section_hatch_output_fill_outside_contour"):
            validate_section_hatch_precision(rows)

    def test_large_safe_nested_loops_and_separated_hatches_are_accepted(self):
        outer = [[0, 0], [20, 0], [20, 20], [0, 20]]
        hole = [[5, 5], [5, 15], [15, 15], [15, 5]]
        rows = contour_rows(outer) + contour_rows(hole) + [hatch([0, 10], [5, 10]), hatch([15, 10], [20, 10])]
        validate_section_hatch_precision(rows)
        validate_section_hatch_precision(rows, transform=lambda p: (43 + p[0] * 2, 37 + p[1] * 2))
        validate_section_hatch_precision(rows, transform=lambda p: (43 + p[0] * 2, 37 - p[1] * 2),
                                         transform_orientation=-1)

    def test_shallow_angle_tail_can_extend_several_grids_along_hatch(self):
        # This is valid source fill. Quantization moves the first endpoint
        # beyond the encoded sloped edge; a shallow intersection makes its
        # tail exceed four grids along X, while its exact L-infinity distance
        # to that finite boundary segment is only half one grid.
        outer = micro([[.49, .49], [20.49, 4.49], [20, -20]])
        rows = contour_rows(outer) + [hatch(*micro([[2.4, .7], [18, 2.4]]))]
        validate_section_hatch_precision(rows)

    def test_endpoint_farther_than_one_grid_from_crossed_boundary_is_rejected(self):
        outer = micro([[0, 0], [20, 4], [20, -20]])
        rows = contour_rows(outer) + [hatch(*micro([[2, 3], [18, 2]]))]
        with self.assertRaisesRegex(ValueError, "section_hatch_output_fill_outside_contour"):
            validate_section_hatch_precision(rows)

    def test_sharp_corner_uses_finite_boundary_not_its_infinite_line(self):
        # The endpoint is one grid from the infinite bottom edge line, but
        # two grids from the finite bottom and left edges at the sharp corner.
        outer = micro([[0, 0], [10, 0], [0, 10]])
        rows = contour_rows(outer) + [hatch(*micro([[-2, -1], [1, 1]]))]
        with self.assertRaisesRegex(ValueError, "section_hatch_output_fill_outside_contour"):
            validate_section_hatch_precision(rows)


if __name__ == "__main__":
    unittest.main()
