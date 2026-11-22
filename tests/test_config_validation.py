from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings


class ConfigValidationTests(unittest.TestCase):
    def settings(self, **environment: str) -> Settings:
        with patch.dict(os.environ, environment, clear=True):
            return Settings.from_env()

    def test_defaults_are_preserved(self) -> None:
        settings = self.settings()
        self.assertEqual(settings.mode, "mock")
        self.assertEqual(settings.bridge_url, "http://127.0.0.1:8765")
        self.assertEqual(settings.bridge_timeout, 10.0)
        self.assertEqual(settings.versions(), ["default"])
        self.assertEqual(settings.url_for(), ("http://127.0.0.1:8765", "default"))

    def test_timeout_accepts_finite_positive_values_through_upper_bound(self) -> None:
        for value in ("0.001", "10", "120", " 1.5 "):
            with self.subTest(value=value):
                settings = self.settings(E3D_BRIDGE_TIMEOUT=value)
                self.assertEqual(settings.bridge_timeout, float(value))

    def test_timeout_rejects_nonfinite_and_out_of_range_values(self) -> None:
        for value in ("nan", "NaN", "+nan", "inf", "+Infinity", "-inf", "1e999", "0", "-1", "120.001"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "E3D_BRIDGE_TIMEOUT.*between 0 and 120"):
                    self.settings(E3D_BRIDGE_TIMEOUT=value)

    def test_timeout_rejects_nonnumeric_values(self) -> None:
        for value in ("", "ten"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "E3D_BRIDGE_TIMEOUT must be a number"):
                    self.settings(E3D_BRIDGE_TIMEOUT=value)

    def test_both_url_sources_preserve_valid_hosts_paths_and_ipv6(self) -> None:
        for url, expected in (
            ("http://127.0.0.1:8766/", "http://127.0.0.1:8766"),
            ("https://bridge.example.test/e3d/proxy///", "https://bridge.example.test/e3d/proxy"),
            ("http://localhost:8766/base%20path/", "http://localhost:8766/base%20path"),
            ("http://[::1]:8766/", "http://[::1]:8766"),
            ("https://[2001:db8::1]/proxy/", "https://[2001:db8::1]/proxy"),
        ):
            for source in ("direct", "mapping"):
                with self.subTest(url=url, source=source):
                    environment = {"E3D_BRIDGE_URL": url} if source == "direct" else {
                        "E3D_BRIDGES": json.dumps({" E3D21 ": url}),
                        "E3D_MCP_VERSION": " E3D21 ",
                    }
                    settings = self.settings(**environment)
                    self.assertEqual(settings.bridge_url, expected)
                    self.assertEqual(settings.url_for()[0], expected)
                    if source == "mapping":
                        self.assertEqual(settings.bridge_urls, {"E3D21": expected})

    def test_both_url_sources_reject_invalid_urls(self) -> None:
        for url in (
            "", "ftp://localhost", "http://", "https:///proxy", "http://:8766", "http://user@:8766",
            "http://[::1", "http://[invalid]:8766", "http://bad\\host:8766",
            "http://localhost:bad", "http://localhost:-1", "http://localhost:65536", "http://localhost:80:90",
            "http://localhost?x=y", "http://localhost?", "http://localhost/base#part", "http://localhost#",
            " http://localhost", "http://localhost ", "http://local host", "http://localhost/base path",
            "http://local\thost", "http://local\nhost", "http://localhost/\x7f",
        ):
            for source in ("direct", "mapping"):
                with self.subTest(url=url, source=source):
                    environment = {"E3D_BRIDGE_URL": url} if source == "direct" else {
                        "E3D_BRIDGES": json.dumps({"E3D21": url}),
                    }
                    with self.assertRaisesRegex(ValueError, "E3D_BRIDGE"):
                        self.settings(**environment)

    def test_bridge_mapping_rejects_json_escaped_nul(self) -> None:
        # JSON can represent NUL even though a direct environment value cannot.
        with self.assertRaisesRegex(ValueError, "E3D_BRIDGES.*control characters"):
            self.settings(E3D_BRIDGES=json.dumps({"E3D21": "http://localhost/\x00"}))

    def test_bridge_mapping_rejects_nonstring_urls(self) -> None:
        for url in (None, False, 8766, [], {}):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "E3D_BRIDGES URL for 'E3D21'"):
                    self.settings(E3D_BRIDGES=json.dumps({"E3D21": url}))

    def test_bridge_mapping_rejects_exact_and_trimmed_duplicate_labels(self) -> None:
        for raw in (
            '{"E3D21":"http://first","E3D21":"http://second"}',
            '{" E3D21 ":"http://first","E3D21":"http://second"}',
            '{"E3D21":"http://first"," E3D21 ":"http://second"}',
            '{" E3D21 ":"http://first"," E3D21 ":"http://second"}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "E3D_BRIDGES contains duplicate version label"):
                    self.settings(E3D_BRIDGES=raw)

    def test_bridge_mapping_preserves_distinct_case_sensitive_labels(self) -> None:
        settings = self.settings(E3D_BRIDGES='{"E3D21":"http://first/","e3d21":"http://second/"}',
                                 E3D_MCP_VERSION="e3d21")
        self.assertEqual(settings.bridge_urls, {"E3D21": "http://first", "e3d21": "http://second"})
        self.assertEqual(settings.versions(), ["e3d21"])
        self.assertEqual(settings.url_for(), ("http://second", "e3d21"))

    def test_bridge_mapping_rejects_invalid_objects_and_labels(self) -> None:
        for raw in ("not json", "{}", "[]", "null", '{"":"http://localhost"}', '{"  ":"http://localhost"}'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "E3D_BRIDGES"):
                    self.settings(E3D_BRIDGES=raw)


if __name__ == "__main__":
    unittest.main()
