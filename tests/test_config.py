from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest

from cyt_config import ConfigError, load_config, normalize_config, parse_config, validate_config


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "operator-config.json"
        self.document = json.loads((ROOT / "sample.config.json").read_text(encoding="utf-8"))

    def load(self):
        self.source.write_text(json.dumps(self.document, indent=2), encoding="utf-8")
        return load_config(self.source)

    def assert_invalid(self, field):
        with self.assertRaises(ConfigError) as caught:
            self.load()
        error = caught.exception
        self.assertEqual(error.filename, str(self.source))
        self.assertEqual(error.field, field)
        self.assertGreater(error.line, 0)
        self.assertGreater(error.column, 0)
        return error

    def test_sample_is_v2_and_nested_objects_are_immutable(self):
        config = load_config(ROOT / "sample.config.json")
        self.assertEqual(config.schema_version, 2)
        self.assertEqual(config.paths.log_dir, ROOT / "logs")
        self.assertEqual(config.paths.mac_ignore_list, ROOT / "ignore_lists" / "mac_list.json")
        self.assertEqual(config.timing.time_windows.oldest_minutes, 20)
        self.assertIsNone(config.search)
        with self.assertRaises(FrozenInstanceError):
            config.timing.time_windows.oldest_minutes = 99

    def test_v1_defaults_and_working_directory_paths(self):
        paths = self.document["paths"]
        paths["base_dir"] = "unused"
        external = self.root / "external.json"
        paths["ignore_lists"] = {"mac": "mac_list.py", "ssid": str(external)}
        self.document = {"paths": paths}
        self.load()
        cwd = self.root / "different-cwd"
        config = load_config(self.source, working_dir=cwd)
        self.assertEqual(config.schema_version, 1)
        self.assertEqual(config.paths.log_dir, cwd / "logs")
        self.assertEqual(config.paths.mac_ignore_list, cwd / "ignore_lists" / "mac_list.py")
        self.assertEqual(config.paths.ssid_ignore_list, external)
        self.assertEqual(config.timing.poll_interval_seconds, 60)
        self.assertEqual(config.timing.tracking_list_rotation_cycles, 5)
        self.assertEqual(config.timing.time_windows.medium_minutes, 10)
        self.document["schema_version"] = 1
        self.document["timing"] = {"check_interval": 7.5, "list_update_interval": 3}
        self.assertEqual(self.load().timing.poll_interval_seconds, 7.5)
        self.document["timing"]["time_windows"] = {"recent": 5}
        self.assert_invalid("$.timing.time_windows.medium")

    def test_v2_anchor_is_config_directory_and_no_side_effects(self):
        self.document["paths"]["kismet_logs"] += "/"
        before = set(self.root.iterdir())
        data = json.dumps(self.document)
        parsed = parse_config(data, self.source)
        typed = validate_config(parsed, self.source)
        config = normalize_config(typed, filename=self.source, working_dir=self.root / "elsewhere")
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(config.paths.log_dir, self.root / "logs")
        self.assertTrue(config.paths.kismet_logs.endswith("*.kismet/"))
        self.source.write_text(data, encoding="utf-8")
        loaded = load_config(self.source, working_dir=self.root / "elsewhere")
        self.assertEqual(loaded, config)
        self.assertEqual(self.source.read_text(encoding="utf-8"), data)
        self.assertEqual(set(self.root.iterdir()), {self.source})

    def test_json_diagnostics(self):
        cases = (
            ('{\n "paths": }', "$", 2, 11, "Expecting value"),
            ('{\n "paths": {"logs": 1,\n "logs": 2}}', "$.paths.logs", 3, 2, "duplicate"),
            ('{"search": {"lat_max": 1e999}}', "$.search.lat_max", 1, 13, "finite"),
            ('{"x": [NaN]}', "$.x[0]", 1, 8, "finite"),
            ('{"x": -Infinity}', "$.x", 1, 2, "finite"),
        )
        for text, field, line, col, message in cases:
            with self.subTest(text=text), self.assertRaises(ConfigError) as caught:
                parse_config(text, "operator.json")
            error = caught.exception
            self.assertEqual((error.field, error.line, error.column), (field, line, col))
            self.assertIn(message, str(error))

    def test_versions_and_invalid_types_fail(self):
        for value in (0, 3, 2.0, "2", True, None):
            with self.subTest(value=value):
                self.document["schema_version"] = value
                self.assert_invalid("$.schema_version")
        self.document["schema_version"] = 2
        self.document["paths"]["kismet_logs"] = []
        self.assert_invalid("$.paths.kismet_logs")
        self.document["paths"]["kismet_logs"] = " "
        self.assert_invalid("$.paths.kismet_logs")

    def test_required_paths_and_timing(self):
        lists = self.document["paths"]["ignore_lists"]
        ssid = lists.pop("ssid")
        self.assert_invalid("$.paths.ignore_lists.ssid")
        lists["ssid"] = ssid
        timing = self.document.pop("timing")
        self.assert_invalid("$.timing")
        self.document["timing"] = timing
        del timing["poll_interval_seconds"]
        self.assert_invalid("$.timing.poll_interval_seconds")

    def test_timing_invalid_values_are_not_replaced(self):
        timing = self.document["timing"]
        for value in (True, 0, -1, "60", None):
            with self.subTest(value=value):
                timing["poll_interval_seconds"] = value
                self.assert_invalid("$.timing.poll_interval_seconds")
        timing["poll_interval_seconds"] = 60
        timing["tracking_list_rotation_cycles"] = 2.5
        self.assert_invalid("$.timing.tracking_list_rotation_cycles")
        timing["tracking_list_rotation_cycles"] = 5
        timing["time_windows"]["medium_minutes"] = 5
        self.assert_invalid("$.timing.time_windows.medium_minutes")

    def test_optional_search_is_complete_ordered_and_box_only(self):
        self.document["search"] = {"lat_min": 0, "lat_max": 45, "lon_min": -120, "lon_max": 0}
        self.assertEqual(self.load().search.lat_min, 0)
        self.document["search"]["lon_min"] = 179
        self.assert_invalid("$.search.lon_max")
        self.document["search"]["lon_min"] = -181
        self.assert_invalid("$.search.lon_min")
        self.document["search"]["lon_min"] = -120
        del self.document["search"]["lon_max"]
        self.assert_invalid("$.search.lon_max")
        self.document["search"]["polygon"] = []
        self.assert_invalid("$.search.polygon")

    def test_secret_fields_rejected_without_echoing_values(self):
        self.document["api_keys"] = {"wigle": {"encoded_token": "private-value"}}
        for version in (1, 2):
            self.document["schema_version"] = version
            self.assertNotIn("private-value", str(self.assert_invalid("$.api_keys")))
        del self.document["api_keys"]
        self.document["schema_version"] = 2
        self.document["paths"]["ignore_lists"]["password"] = "private-value"
        self.assertNotIn("private-value", str(self.assert_invalid("$.paths.ignore_lists.password")))

    def test_missing_file_and_bad_encoding_report_filename(self):
        with self.assertRaisesRegex(ConfigError, "operator-config.json.*cannot read"):
            load_config(self.source)
        self.source.write_bytes(b'{\n "\xff"}')
        with self.assertRaisesRegex(ConfigError, r"operator-config.json:2:3.*UTF-8"):
            load_config(self.source)
