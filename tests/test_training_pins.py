"""固定标签只影响个人界面配置，不访问服务器。"""
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from autodl_watcher.training_config import TrainingServer, load_servers, save_servers
from autodl_watcher.yaml_settings import write_settings


class TrainingPinConfigTest(unittest.TestCase):
    def test_new_servers_default_to_unpinned_and_roundtrip_explicit_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "servers.yaml"
            servers = [TrainingServer("first", "新训练机", "first-host"),
                       TrainingServer("second", "固定训练机", "second-host", pinned=True)]
            save_servers(path, servers)
            self.assertEqual([item.pinned for item in load_servers(path)], [False, True])

    def test_old_files_keep_visible_tabs_without_rewriting_or_name_assumptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "servers.yaml"
            item = asdict(TrainingServer("legacy", "自定义名称", "legacy-host"))
            item.pop("pinned")
            write_settings(path, [item])
            before = path.read_bytes()
            self.assertTrue(load_servers(path)[0].pinned)
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_pin_value_is_not_treated_as_checked(self):
        with self.assertRaisesRegex(ValueError, "布尔"):
            TrainingServer("bad", "错误设置", "bad-host", pinned="false").validate()
