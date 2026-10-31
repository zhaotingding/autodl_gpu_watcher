"""旧个人配置保留原文件，退役的阈值和日志项不再影响运行。"""
from pathlib import Path
import tempfile
import unittest

import yaml

from autodl_watcher.config import load_config


class MonitorConfigV101Test(unittest.TestCase):
    def test_legacy_threshold_and_log_keys_are_ignored_without_rewriting_file(self):
        sample = Path(__file__).parent / "fixtures/monitor.yaml"
        raw = yaml.safe_load(sample.read_text(encoding="utf-8"))
        raw["idle_thresholds"] = {"memory_free_min_mb": "obsolete-invalid-value",
                                  "memory_free_min_ratio": 99, "gpu_util_max_pct": 0}
        raw["runtime"]["log_file"] = "must-not-create/watcher.log"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.local.yaml"
            path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
            original = path.read_bytes()
            config = load_config(path)
            self.assertFalse(hasattr(config, "idle_thresholds"))
            self.assertFalse(hasattr(config.runtime, "log_file"))
            self.assertEqual(config.runtime.state_file, Path(directory) / "runtime/state.json")
            self.assertEqual(config.telemetry.settings_root, Path(directory))
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse((Path(directory) / "must-not-create").exists())
