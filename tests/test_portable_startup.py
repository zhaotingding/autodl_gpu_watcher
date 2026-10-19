"""不同安装布局均可启动，默认配置不会引用作者的账号或算力服务。"""
from importlib.resources import files
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from autodl_watcher import launcher, paths
from autodl_watcher.config import load_config


class PortableStartupTest(unittest.TestCase):
    def test_explicit_root_wins_for_source_and_frozen_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, AUTODL_APP_ROOT=directory):
                self.assertEqual(paths.application_root(), Path(directory).resolve())
                with patch.object(sys, "frozen", True, create=True):
                    self.assertEqual(paths.application_root(), Path(directory).resolve())

    def test_wheel_layout_uses_user_directory_not_site_packages_or_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory, "XDG_DATA_HOME": directory}), \
                 patch.object(paths, "__file__", str(Path(directory) / "site-packages/autodl_watcher/paths.py")), \
                 patch.object(sys, "platform", "win32"):
                os.environ.pop("AUTODL_APP_ROOT", None)
                self.assertEqual(paths.application_root(), Path(directory) / "autodl-watcher")

    def test_source_code_root_is_independent_from_overridden_data_root(self):
        checkout = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, AUTODL_APP_ROOT=directory):
                self.assertEqual(paths.source_root(), checkout)
                self.assertNotEqual(paths.application_root(), paths.source_root())

    def test_default_configuration_is_safe_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = paths.prepare_application(Path(directory))
            config = load_config(paths.default_config_path(root))
            self.assertFalse(config.auto_start.enabled)
            self.assertTrue(config.auto_start.dry_run)
            self.assertEqual(config.auto_start.targets, ())
            self.assertEqual(config.occupancy.self_user, "")
            self.assertEqual(config.telemetry.endpoint, "")
            self.assertEqual(config.runtime.state_file, root / "runtime/state.json")
            self.assertTrue(files("autodl_watcher").joinpath("remote_probe.py").is_file())
            self.assertTrue(files("autodl_watcher").joinpath("default.ico").is_file())

    def test_existing_local_configuration_is_preferred_without_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "config.local.yaml"
            original = b"existing local settings\n"
            local.write_bytes(original)
            paths.prepare_application(root)
            self.assertEqual(paths.default_config_path(root), local)
            self.assertEqual(local.read_bytes(), original)
            self.assertFalse((root / "config.yaml").exists())

    def test_tk_missing_has_actionable_message_and_failing_status(self):
        real_import = __import__
        def without_tk(name, *args, **kwargs):
            if name == "tkinter":
                raise ImportError("tk missing")
            return real_import(name, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            with patch("builtins.__import__", side_effect=without_tk), \
                 patch.object(sys, "argv", ["autodl-watcher"]), \
                 patch.object(launcher.ctypes, "windll", create=True) as windows:
                with self.assertRaises(SystemExit) as exc:
                    launcher.gui_main(root=Path(directory))
                self.assertEqual(exc.exception.code, 1)
                log = (Path(directory) / ".ui/startup.log").read_text(encoding="utf-8")
                self.assertIn("Tcl/Tk", log)
                self.assertIn("python3-tk", log)

    def test_repository_sample_matches_packaged_defaults(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8")),
                         yaml.safe_load(files("autodl_watcher").joinpath("defaults.yaml").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
