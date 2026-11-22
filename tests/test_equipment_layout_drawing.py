import unittest

from aveva_e3d_mcp.equipment_layout_drawing import build_equipment_layout_drawing


def row(refno, position=(10.0, 20.0, 30.0), axes=None):
    axes = axes or {
        "x_axis": [1.0, 0.0, 0.0],
        "y_axis": [0.0, 1.0, 0.0],
        "z_axis": [0.0, 0.0, 1.0],
    }
    return {"refno": refno, "measurements": {"spatial": {"result": {
        "position": {"available": True, "x": position[0], "y": position[1], "z": position[2]},
        "orientation": {"available": True, **axes},
    }}}}


class EquipmentLayoutDrawingTests(unittest.TestCase):
    def test_builds_three_views_from_world_pose(self):
        drawing = build_equipment_layout_drawing([row("/EQUI-1")])
        self.assertTrue(drawing["success"])
        self.assertTrue(drawing["complete"])
        self.assertEqual([view["name"] for view in drawing["views"]], ["FRONT", "TOP", "SIDE"])
        self.assertEqual(drawing["coordinate_frame"], "WORLD")
        self.assertFalse(drawing["extents_included"])
        self.assertEqual(len(drawing["equipment"]), 1)
        self.assertGreater(drawing["segment_count"], 0)
        front_axes = [line for line in drawing["views"][0]["lines"] if "axis_name" in line]
        self.assertEqual({line["axis_name"] for line in front_axes}, {"X", "Z"})
        self.assertTrue(all(line["kind"] == "feature" for line in front_axes))

    def test_missing_orientation_keeps_position_and_marks_incomplete(self):
        value = row("/EQUI-2")
        del value["measurements"]["spatial"]["result"]["orientation"]
        drawing = build_equipment_layout_drawing([value])
        self.assertTrue(drawing["success"])
        self.assertFalse(drawing["complete"])
        self.assertEqual(drawing["equipment"][0]["error_code"], "world_orientation_unavailable")
        self.assertGreater(drawing["segment_count"], 0)

    def test_no_position_fails_without_fabricating_geometry(self):
        value = row("/EQUI-3")
        del value["measurements"]["spatial"]["result"]["position"]
        drawing = build_equipment_layout_drawing([value])
        self.assertFalse(drawing["success"])
        self.assertEqual(drawing["error_code"], "no_world_positions")
        self.assertEqual(drawing["views"], [])

    def test_empty_native_batch_is_a_structured_no_position_result(self):
        drawing = build_equipment_layout_drawing([])
        self.assertFalse(drawing["success"])
        self.assertFalse(drawing["complete"])
        self.assertEqual(drawing["error_code"], "no_world_positions")

    def test_non_orthonormal_axes_are_marked_unavailable(self):
        value = row("/EQUI-BAD-ORI", axes={
            "x_axis": [0.0, 0.0, 0.0],
            "y_axis": [0.0, 1.0, 0.0],
            "z_axis": [0.0, 0.0, 1.0],
        })
        drawing = build_equipment_layout_drawing([value])
        self.assertTrue(drawing["success"])
        self.assertFalse(drawing["complete"])
        self.assertEqual(drawing["equipment"][0]["error_code"], "world_orientation_unavailable")
        self.assertFalse(any("axis_name" in line for view in drawing["views"] for line in view["lines"]))

    def test_axis_length_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "invalid_axis_length_mm"):
            build_equipment_layout_drawing([row("/EQUI")], axis_length_mm=0)


if __name__ == "__main__":
    unittest.main()
