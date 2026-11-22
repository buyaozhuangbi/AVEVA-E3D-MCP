"""Optional third-party interchange checks; ezdxf is not a runtime dependency."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import ezdxf
except ImportError:
    ezdxf = None

from aveva_e3d_mcp.orthographic_dxf import export_orthographic_dxf


def interchange_drawing(*, hidden: bool = False) -> dict:
    # The translated centimetre coordinates exercise both unit conversion and
    # drawing placement; the reader must see millimetres rather than raw input.
    return {
        "success": True,
        "complete": True,
        "coordinate_unit": "cm",
        "show_hidden_lines": hidden,
        "views": [{
            "name": "FRONT",
            "bounds": {"min": [1, 2], "max": [5, 8]},
            "dimensions": {"width": 4, "height": 6, "unit": "cm"},
            "lines": [
                {"kind": "silhouette", "start": [1, 2], "end": [5, 2]},
                {"kind": "feature", "start": [5, 2], "end": [5, 8]},
                {"kind": "feature", "start": [1, 5], "end": [5, 5], "visibility": "hidden"},
            ],
        }],
    }


@unittest.skipIf(ezdxf is None, "optional ezdxf parser is not installed")
class OrthographicDxfInteropTests(unittest.TestCase):
    def read_export(self, *, hidden: bool = False):
        # Use the normal reader, without recovery: a recovery parse could hide
        # broken ownership, symbol-table handles or missing subclass markers.
        document = ezdxf.read(io.StringIO(export_orthographic_dxf(interchange_drawing(hidden=hidden))))
        self.assert_clean(document)
        return document

    def assert_clean(self, document) -> None:
        audit = document.audit()
        self.assertEqual(audit.errors, [], "DXF reader reported unrecoverable errors")
        self.assertEqual(audit.fixes, [], "DXF reader needed structural repairs")

    @staticmethod
    def geometry(document) -> list[tuple]:
        rows = []
        for entity in document.modelspace():
            if entity.dxftype() == "LINE":
                rows.append(("LINE", entity.dxf.layer, tuple(entity.dxf.start), tuple(entity.dxf.end)))
            elif entity.dxftype() == "TEXT":
                rows.append(("TEXT", entity.dxf.layer, entity.dxf.text,
                             tuple(entity.dxf.insert), tuple(entity.dxf.align_point),
                             entity.dxf.height, entity.dxf.rotation,
                             entity.dxf.halign, entity.dxf.valign))
        return rows

    def test_reader_preserves_units_layer_geometry_text_and_ownership(self):
        document = self.read_export()
        self.assertEqual(document.dxfversion, "AC1015")
        self.assertEqual(document.header["$INSUNITS"], 4)
        self.assertEqual(document.units, 4)
        space = document.modelspace()
        outline = list(space.query('LINE[layer=="FRONT_OUTLINE"]'))
        feature = list(space.query('LINE[layer=="FRONT_FEATURE"]'))
        self.assertEqual(len(outline), 1)
        self.assertEqual(len(feature), 1)
        self.assertEqual(tuple(outline[0].dxf.start), (60.0, 60.0, 0.0))
        self.assertEqual(tuple(outline[0].dxf.end), (100.0, 60.0, 0.0))
        self.assertEqual(tuple(feature[0].dxf.end), (100.0, 120.0, 0.0))
        self.assertNotIn("FRONT_HIDDEN", document.layers)
        self.assertEqual(len(space.query("LINE")), 16)
        texts = list(space.query("TEXT"))
        self.assertCountEqual([text.dxf.text for text in texts], [
            "FRONT 40.000000 x 60.000000 mm", "40.000000 mm", "60.000000 mm",
        ])
        vertical = next(text for text in texts if text.dxf.text == "60.000000 mm")
        self.assertEqual(vertical.dxf.rotation, 90.0)
        self.assertEqual((vertical.dxf.halign, vertical.dxf.valign), (1, 2))
        self.assertEqual(tuple(vertical.dxf.align_point), (28.125, 90.0, 0.0))
        handles = [entity.dxf.handle for entity in space]
        self.assertEqual(len(set(handles)), len(handles))
        self.assertTrue(all(handle for handle in handles))
        self.assertTrue(all(entity.dxf.owner == space.block_record_handle for entity in space))

    def test_hidden_geometry_uses_declared_dashed_layer(self):
        document = self.read_export(hidden=True)
        hidden = list(document.modelspace().query('LINE[layer=="FRONT_HIDDEN"]'))
        self.assertEqual(len(hidden), 1)
        self.assertEqual(tuple(hidden[0].dxf.start), (60.0, 90.0, 0.0))
        self.assertEqual(tuple(hidden[0].dxf.end), (100.0, 90.0, 0.0))
        self.assertEqual(hidden[0].dxf.linetype, "BYLAYER")
        layer = document.layers.get("FRONT_HIDDEN")
        self.assertEqual(layer.dxf.linetype, "E3D_HIDDEN")
        pattern = document.linetypes.get(layer.dxf.linetype).pattern_tags.tags
        self.assertEqual([tag.value for tag in pattern if tag.code == 40], [4.5])
        self.assertEqual([tag.value for tag in pattern if tag.code == 49], [3.0, -1.5])
        self.assertEqual(document.layers.get("FRONT_OUTLINE").dxf.linetype, "CONTINUOUS")

    def test_write_read_round_trip_preserves_modelspace_semantics(self):
        for hidden in (False, True):
            with self.subTest(hidden=hidden), tempfile.TemporaryDirectory(prefix="dxf-interop-") as directory:
                original = self.read_export(hidden=hidden)
                path = Path(directory) / "drawing.dxf"
                original.saveas(path)
                reread = ezdxf.readfile(path)
                self.assert_clean(reread)
                self.assertEqual(reread.dxfversion, "AC1015")
                self.assertEqual(reread.units, 4)
                self.assertEqual(self.geometry(reread), self.geometry(original))
                if hidden:
                    self.assertEqual(reread.layers.get("FRONT_HIDDEN").dxf.linetype, "E3D_HIDDEN")
                    self.assertEqual(reread.linetypes.get("E3D_HIDDEN").simplified_line_pattern(), (3.0, 1.5))


if __name__ == "__main__":
    unittest.main()
