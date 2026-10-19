import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autodl_watcher.yaml_settings import read_settings, settings_path, write_settings
from autodl_watcher.training_config import load_servers, save_servers
from autodl_watcher.training_rules import RuleStore


class YamlSettingsTest(unittest.TestCase):
    def test_legacy_json_is_read_only_and_yaml_takes_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preferences.yaml"
            legacy = path.with_suffix(".json")
            value = {"user": "姜尚", "poll": "120", "live": False, "convert_no_gpu": False}
            legacy.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            before = legacy.read_bytes()
            self.assertEqual(read_settings(path), value)
            write_settings(path, dict(value, poll="180"))
            self.assertEqual(legacy.read_bytes(), before)
            self.assertEqual(read_settings(legacy)["poll"], "180")
            self.assertFalse(read_settings(path)["live"])

    def test_types_multiline_prompt_and_rules_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ai.yaml"
            value = {"prompt": "读取日志\n保留未知指标\n", "strings": ["on", "001", "2026-10-13", "null"],
                     "enabled": False, "optional": None, "tokens": 6954, "cost": .000504,
                     "rules": {json.dumps(["s", "pid/start"]): {"attempted": True}}}
            write_settings(path, value)
            self.assertEqual(read_settings(path), value)
            self.assertIn("prompt: |", path.read_text(encoding="utf-8"))

    def test_broken_yaml_does_not_fall_back_to_old_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ai.yaml"
            path.with_suffix(".json").write_text('{"model":"old"}', encoding="utf-8")
            path.write_text("model: [", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_settings(path)

    def test_broken_legacy_json_is_not_interpreted_as_yaml(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ai.yaml"
            path.with_suffix(".json").write_text("{bad json", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_settings(path)
            self.assertFalse(path.exists())

    def test_failed_replace_keeps_previous_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preferences.yaml"
            write_settings(path, {"live": False})
            before = path.read_bytes()
            with patch.object(Path, "replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    write_settings(path, {"live": True})
            self.assertEqual(path.read_bytes(), before)

    def test_server_and_rule_consumers_preserve_legacy_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / "servers.json"
            legacy.write_text(json.dumps([{"id":"s", "name":"203-1", "ssh_alias":"alias"}]), encoding="utf-8")
            servers = load_servers(root / "servers.yaml")
            save_servers(root / "servers.yaml", servers)
            self.assertEqual(load_servers(root / "servers.yaml"), servers)
            rules = root / "training_rules.json"
            entry = {"attempted": True, "profile": {"name":"kept", "rules":[]}, "call": {"usage": None}}
            rules.write_text(json.dumps({"task": entry}), encoding="utf-8")
            store = RuleStore(root / "training_rules.yaml")
            self.assertEqual(store.entries["task"], entry)
            store.put("task", entry)
            self.assertEqual(RuleStore(store.path).entries["task"], entry)
            self.assertEqual(json.loads(rules.read_text()), {"task": entry})


if __name__ == "__main__":
    unittest.main()
