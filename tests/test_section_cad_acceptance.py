"""Exercise the independent CAD containment checks used by live acceptance."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("section_cad_acceptance", SCRIPTS / "test-e3d21-section-readonly.py")
ACCEPTANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCEPTANCE)


def closed(points):
    return [*points, points[0]]


OUTER = closed([(0, 0), (10, 0), (10, 10), (0, 10)])


class SectionCadAcceptanceTests(unittest.TestCase):
    def test_recovers_oriented_loops_from_shuffled_cad_endpoints(self):
        hole = closed([(3, 2), (3, 8), (4, 8), (4, 2)])
        lines = [SimpleNamespace(dxf=SimpleNamespace(start=(*a, 0), end=(*b, 0)))
                 for loop in (OUTER, hole) for a, b in zip(loop, loop[1:])]
        loops = ACCEPTANCE.cad_contours(lines[::2] + lines[1::2])
        self.assertEqual(len(loops), 2)
        self.assertTrue(ACCEPTANCE.winding_at((2, 5), loops))
        self.assertFalse(ACCEPTANCE.winding_at((3.5, 5), loops))
        lines[0].dxf.start, lines[0].dxf.end = lines[0].dxf.end, lines[0].dxf.start
        with self.assertRaises(AssertionError):
            ACCEPTANCE.cad_contours(lines)

    def test_rejects_crossing_narrow_hole_missed_by_quarter_samples(self):
        hole = closed([(3, 2), (3, 8), (3.01, 8), (3.01, 2)])
        loops = [OUTER, hole]
        self.assertTrue(all(ACCEPTANCE.winding_at((10 * t, 5), loops) for t in (0, .25, .5, .75, 1)))
        with self.assertRaisesRegex(AssertionError, "unfilled interval"):
            ACCEPTANCE.verify_cad_hatch_segment((0, 5), (10, 5), loops)
        ACCEPTANCE.verify_cad_hatch_segment((0, 5), (3, 5), loops)
        ACCEPTANCE.verify_cad_hatch_segment((3.01, 5), (10, 5), loops)

    def test_rejects_crossing_concave_notch(self):
        notch = closed([(0, 0), (10, 0), (10, 10), (3.01, 10), (3.01, 3), (3, 3), (3, 10), (0, 10)])
        with self.assertRaisesRegex(AssertionError, "unfilled interval"):
            ACCEPTANCE.verify_cad_hatch_segment((0, 5), (10, 5), [notch])

    def test_same_direction_nested_contour_remains_filled(self):
        nested = closed([(3, 2), (4, 2), (4, 8), (3, 8)])
        ACCEPTANCE.verify_cad_hatch_segment((0, 5), (10, 5), [OUTER, nested])

    def test_accepts_collinear_subdivided_boundary(self):
        boundary = closed([(0, 0), (3, 0), (10, 0), (10, 10), (0, 10)])
        ACCEPTANCE.verify_cad_hatch_segment((0, 0), (10, 0), [boundary])

    def test_rejects_outside_endpoints_and_collapsed_lines(self):
        for a, b in (((-1, 5), (10, 5)), ((0, 5), (11, 5)), ((0, 0), (0, 0))):
            with self.subTest(a=a, b=b), self.assertRaises(AssertionError):
                ACCEPTANCE.verify_cad_hatch_segment(a, b, [OUTER])


if __name__ == "__main__":
    unittest.main()
