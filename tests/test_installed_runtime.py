"""安装版资源与worker边界采用临时路径验证，不启动实际业务。"""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from autodl_watcher import launcher, paths
from autodl_watcher.desktop_backend import DesktopBackend
from autodl_watcher import desktop_backend


class InstalledRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.resources = self.root / "program/resources"
        self.executable = self.resources / "backend/autodl-backend.exe"
        self.executable.parent.mkdir(parents=True)
        self.executable.touch()
        self.bundle = self.executable.parent / "_internal"
        self.bundle.mkdir()
        self.profile = self.root / "profile"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (("frozen", True), ("executable", str(self.executable)), ("_MEIPASS", str(self.bundle))):
            self.stack.enter_context(patch.object(sys, name, value, create=True))
        self.stack.enter_context(patch.object(launcher, "__file__", str(self.bundle / "autodl_watcher/launcher.py")))
        self.stack.enter_context(patch.object(launcher, "import_module",
            side_effect=lambda name: SimpleNamespace(__file__=str(self.bundle / name / "__init__.py"))))
        self.stack.enter_context(patch.dict(os.environ, {"AUTODL_PROGRAM_RESOURCES": str(self.resources),
            "AUTODL_APP_ROOT": str(self.profile)}, clear=True))

    def test_frozen_runtime_does_not_require_conda_and_reports_bundle_origin(self):
        result = launcher.check_environment()
        self.assertEqual(result["environment"], "installed")
        self.assertEqual(result["python"], str(self.executable))
        self.assertEqual(result["resources"], str(self.resources))
        self.assertEqual(result["prefix"], str(self.bundle))
        self.assertEqual(set(result["dependencies"]), {"yaml", "dotenv", "playwright", "requests", "PIL"})

    def test_internal_workers_use_frozen_modes_and_keep_task_whitelist(self):
        for task in ("monitor", "discover", "login", "exe-icon"):
            self.assertEqual(launcher.worker_command(task, "--help"), [str(self.executable), task, "--help"])
            self.assertEqual(DesktopBackend._worker_command(task, "--help"), [str(self.executable), task, "--help"])
        with self.assertRaises(KeyError):
            launcher.worker_command("arbitrary-module")
        environment = launcher.child_environment(self.profile)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["AUTODL_PROGRAM_RESOURCES"], str(self.resources))

    def test_installed_resources_must_match_owned_backend_executable(self):
        with patch.dict(os.environ, AUTODL_PROGRAM_RESOURCES=str(self.root / "other/resources")):
            with self.assertRaisesRegex(RuntimeError, "资源目录不匹配"):
                launcher.check_environment()
        with patch.object(launcher, "__file__", str(self.root / "foreign/launcher.py")):
            with self.assertRaisesRegex(RuntimeError, "随包运行时"):
                launcher.check_environment()

    def test_profile_cannot_default_into_program_or_use_relative_root(self):
        with patch.dict(os.environ, AUTODL_APP_ROOT=str(self.resources.parent / "personal")):
            with self.assertRaisesRegex(RuntimeError, "资料目录"):
                launcher.check_environment()
        with patch.dict(os.environ, AUTODL_APP_ROOT="relative-profile"):
            with self.assertRaisesRegex(RuntimeError, "绝对路径"):
                launcher.check_environment()
        with patch.dict(os.environ, clear=True):
            with self.assertRaisesRegex(RuntimeError, "个人资料目录"):
                paths.application_root()

    def test_bridge_root_argument_cannot_override_main_process_profile_selection(self):
        output, errors = io.StringIO(), io.StringIO()
        bad = self.resources.parent / "profile"
        with patch.object(sys, "argv", [str(self.executable), "--root", str(bad)]), \
             patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors), \
             patch.object(desktop_backend, "DesktopBackend") as service:
            with self.assertRaises(SystemExit) as failure:
                desktop_backend.main()
        self.assertEqual(failure.exception.code, 2)
        service.assert_not_called()
        self.assertIn("主程序指定", json.loads(output.getvalue())["data"]["message"])
        self.assertFalse(bad.exists())


if __name__ == "__main__":
    unittest.main()
