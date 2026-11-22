from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class GeometryOverlapToolTests(unittest.TestCase):
    tool_name = "e3d_geometry_overlap_candidates"

    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=False))

    def _rpc(self, method: str, params: dict, *, server=None) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, arguments: dict, *, server=None) -> dict:
        return self._rpc("tools/call", {"name": self.tool_name, "arguments": arguments}, server=server)

    @staticmethod
    def _metadata(response: dict) -> dict:
        metadata = response["structuredContent"]
        return metadata.get("result", metadata)

    @staticmethod
    def _bounds(refno: str, minimum: list[float], maximum: list[float]) -> dict:
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "available": True, "partial": False,
            "refno": refno, "type": "BOX", "min": minimum, "max": maximum,
            "unit": "mm", "coordinate_frame": "SDK native (unverified)", "errors": {},
        }}

    @staticmethod
    def _candidate_map(metadata: dict) -> dict[tuple[str, str], dict]:
        return {
            (pair["first"]["refno"], pair["second"]["refno"]): pair
            for pair in metadata["candidate_pairs"]
        }

    @staticmethod
    def _axis_gaps(pair: dict) -> tuple[float, float, float]:
        gaps = pair["axis_gaps_mm"]
        if isinstance(gaps, dict):
            return gaps["x"], gaps["y"], gaps["z"]
        return tuple(gaps)

    def test_tool_is_read_only_and_declares_bounded_explicit_inputs(self) -> None:
        definitions = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        tool = definitions[self.tool_name]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"first_refnos", "second_refnos", "tolerance_mm"})
        self.assertEqual(schema["required"], ["first_refnos", "second_refnos"])
        self.assertEqual(schema["properties"]["first_refnos"]["minItems"], 1)
        self.assertEqual(schema["properties"]["first_refnos"]["maxItems"], 32)
        self.assertEqual(schema["properties"]["second_refnos"]["minItems"], 1)
        self.assertEqual(schema["properties"]["second_refnos"]["maxItems"], 32)
        self.assertEqual(schema["properties"]["tolerance_mm"]["default"], 0)
        self.assertFalse(schema["additionalProperties"])

    def test_inclusive_aabb_gaps_cover_overlap_touch_and_tolerance_boundary(self) -> None:
        boxes = {
            "/A": self._bounds("/A", [0, 0, 0], [1, 1, 1]),
            "/OVERLAP": self._bounds("/OVERLAP", [0.75, -1, 0], [2, 2, 1]),
            "/TOUCH": self._bounds("/TOUCH", [1, 0, 0], [2, 1, 1]),
            "/AT_LIMIT": self._bounds("/AT_LIMIT", [1.125, 0, 0], [2, 1, 1]),
            "/PAST_LIMIT": self._bounds("/PAST_LIMIT", [1.126, 0, 0], [2, 1, 1]),
        }
        args = {"first_refnos": ["/A"],
                "second_refnos": ["/OVERLAP", "/TOUCH", "/AT_LIMIT", "/PAST_LIMIT"]}

        with patch.object(BridgeClient, "geometry_bounds", side_effect=lambda refno: boxes[refno]):
            zero = self._call(args)
        self.assertFalse(zero.get("isError", False), zero)
        zero_metadata = self._metadata(zero)
        self.assertEqual(zero_metadata["coordinate_frame"], "SDK native (unverified)")
        self.assertEqual(zero_metadata["coordinate_comparability"], "unverified")
        zero_pairs = self._candidate_map(zero_metadata)
        self.assertEqual(set(zero_pairs), {("/A", "/OVERLAP"), ("/A", "/TOUCH")})
        self.assertEqual(self._axis_gaps(zero_pairs[("/A", "/OVERLAP")]), (0, 0, 0))
        self.assertEqual(self._axis_gaps(zero_pairs[("/A", "/TOUCH")]), (0, 0, 0))
        for pair in zero_pairs.values():
            self.assertIsInstance(pair["relation"], str)
            self.assertTrue(pair["relation"])

        with patch.object(BridgeClient, "geometry_bounds", side_effect=lambda refno: boxes[refno]):
            tolerant = self._call({**args, "tolerance_mm": 0.125})
        self.assertFalse(tolerant.get("isError", False), tolerant)
        tolerant_metadata = self._metadata(tolerant)
        tolerant_pairs = self._candidate_map(tolerant_metadata)
        self.assertEqual(set(tolerant_pairs), {
            ("/A", "/OVERLAP"), ("/A", "/TOUCH"), ("/A", "/AT_LIMIT"),
        })
        self.assertEqual(self._axis_gaps(tolerant_pairs[("/A", "/AT_LIMIT")]), (0.125, 0, 0))
        self.assertAlmostEqual(self._axis_gaps(tolerant_pairs[("/A", "/AT_LIMIT")])[0], 0.125)

    def test_each_unique_reference_is_read_once_across_both_groups(self) -> None:
        boxes = {
            "/A": self._bounds("/A", [0, 0, 0], [1, 1, 1]),
            "/B": self._bounds("/B", [0.5, 0, 0], [2, 1, 1]),
            "/C": self._bounds("/C", [3, 0, 0], [4, 1, 1]),
        }
        with patch.object(BridgeClient, "geometry_bounds", side_effect=lambda refno: boxes[refno]) as read, \
                patch.object(BridgeClient, "command") as command, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self._call({"first_refnos": ["/A", "/B"],
                                   "second_refnos": ["/B", "/C"]})
        self.assertFalse(response.get("isError", False), response)
        self.assertCountEqual([call.args[0] for call in read.call_args_list], ["/A", "/B", "/C"])
        self.assertEqual(read.call_count, 3)
        command.assert_not_called()
        capture.assert_not_called()

    def test_maximum_groups_allow_exactly_1024_cross_group_pairs(self) -> None:
        first = [f"/FIRST-{index}" for index in range(32)]
        second = [f"/SECOND-{index}" for index in range(32)]

        def read_bounds(refno: str) -> dict:
            return self._bounds(refno, [0, 0, 0], [1, 1, 1])

        with patch.object(BridgeClient, "geometry_bounds", side_effect=read_bounds) as read:
            response = self._call({"first_refnos": first, "second_refnos": second})
        self.assertFalse(response.get("isError", False), response)
        metadata = self._metadata(response)
        self.assertEqual(len(metadata["candidate_pairs"]), 1024)
        self.assertEqual(read.call_count, 64)

    def test_partial_bounds_failure_keeps_known_candidates_and_counts_unknown_pairs(self) -> None:
        boxes = {
            "/A": self._bounds("/A", [0, 0, 0], [1, 1, 1]),
            "/B": self._bounds("/B", [0.5, 0, 0], [2, 1, 1]),
            "/Y": self._bounds("/Y", [4, 0, 0], [5, 1, 1]),
            "/MISSING": {"ok": True, "result": {
                "success": False, "available": False,
                "errors": {"bounds": "SDK bounds unavailable"},
            }},
            "/MALFORMED": {"ok": True, "result": {
                "success": True, "available": True,
                "min": [0, 0], "max": [1, 1, 1], "unit": "mm",
            }},
            "/OVERSIZED": {"ok": True, "result": {
                "success": True, "available": True,
                "min": [0, 10 ** 400, 0], "max": [1, 10 ** 400, 1], "unit": "mm",
                "coordinate_frame": "SDK native (unverified)",
            }},
        }
        with patch.object(BridgeClient, "geometry_bounds", side_effect=lambda refno: boxes[refno]):
            response = self._call({"first_refnos": ["/A", "/MISSING", "/MALFORMED", "/OVERSIZED"],
                                   "second_refnos": ["/B", "/Y"]})

        self.assertTrue(response["isError"])
        metadata = self._metadata(response)
        self.assertFalse(metadata["success"])
        self.assertFalse(metadata["complete"])
        self.assertEqual(metadata["unknown_pair_count"], 6)
        candidates = self._candidate_map(metadata)
        self.assertIn(("/A", "/B"), candidates)
        self.assertEqual(set(metadata["bounds_errors"]), {"/MISSING", "/MALFORMED", "/OVERSIZED"})
        self.assertEqual(metadata["coordinate_frame"], "SDK native (unverified)")
        self.assertEqual(metadata["coordinate_comparability"], "unverified")

    def test_bridge_error_stops_later_reads_and_keeps_unattempted_pairs_unknown(self) -> None:
        with patch.object(BridgeClient, "geometry_bounds",
                          side_effect=BridgeError("native database fault", diagnostics={"state": {}})) as read:
            response = self._call({"first_refnos": ["/A"], "second_refnos": ["/B"]})

        self.assertTrue(response["isError"])
        metadata = self._metadata(response)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(metadata["bounds_read_count"], 1)
        self.assertEqual(metadata["unknown_pair_count"], 1)
        self.assertEqual(set(metadata["bounds_errors"]), {"/A", "/B"})

    def test_invalid_groups_references_tolerance_and_unknown_fields_fail_before_bridge(self) -> None:
        valid = {"first_refnos": ["/A"], "second_refnos": ["/B"]}
        too_many = [f"/A{index}" for index in range(33)]
        invalid_arguments = [
            {"first_refnos": [], "second_refnos": ["/B"]},
            {"first_refnos": ["/A"], "second_refnos": []},
            {"first_refnos": too_many, "second_refnos": ["/B"]},
            {"first_refnos": ["/A"], "second_refnos": too_many},
            {"first_refnos": ["/A", "/A"], "second_refnos": ["/B"]},
            {"first_refnos": ["/A"], "second_refnos": ["/B", "/B"]},
            *[
                {"first_refnos": [bad], "second_refnos": ["/B"]}
                for bad in ("CE", "=x/2", "/A\nDELETE CE", True, 7, [])
            ],
            *[{**valid, "tolerance_mm": value} for value in (
                -0.001, True, False, None, "0.1", float("nan"), float("inf"), -float("inf"),
            )],
            {**valid, "unexpected": True},
        ]

        with patch.object(BridgeClient, "geometry_bounds") as read:
            for arguments in invalid_arguments:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments)["isError"])
            read.assert_not_called()

    def test_mock_reports_unavailable_instead_of_claiming_no_candidates(self) -> None:
        mock = create_server(Settings(mode="mock", version="E3D21", allow_commands=False))
        response = self._call({"first_refnos": ["/A"], "second_refnos": ["/B"]}, server=mock)
        self.assertTrue(response["isError"])
        metadata = self._metadata(response)
        self.assertFalse(metadata["success"])
        self.assertFalse(metadata["complete"])
        self.assertGreater(metadata["unknown_pair_count"], 0)


if __name__ == "__main__":
    unittest.main()
