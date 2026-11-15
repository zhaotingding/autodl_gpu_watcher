"""Electron源码入口共用已选解释器，缺失依赖须给出安装指引。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from autodl_watcher import electron_launcher, launcher


class ElectronLauncherTest(unittest.TestCase):
    def test_selected_python_and_data_root_reach_electron_without_foreign_python_path(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            runtime = checkout / "desktop/node_modules/electron/dist/electron.exe"
            runtime.parent.mkdir(parents=True)
            runtime.touch()
            data = checkout / "personal"
            with patch.object(electron_launcher, "source_root", return_value=checkout), \
                 patch.object(launcher, "project_python", return_value="C:/project-env/python.exe"), \
                 patch.dict(os.environ, {"AUTODL_APP_ROOT": str(data), "PYTHONHOME": "foreign", "ELECTRON_RUN_AS_NODE": "1"}), \
                 patch.object(electron_launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertEqual(electron_launcher.launch(["--debug"]), 0)
            command = run.call_args.args[0]
            self.assertEqual(command, [str(runtime), str(checkout / "desktop"), "--debug"])
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["AUTODL_PYTHON"], "C:/project-env/python.exe")
            self.assertEqual(environment["AUTODL_APP_ROOT"], str(data))
            self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
            self.assertNotIn("PYTHONHOME", environment)
            self.assertNotIn("ELECTRON_RUN_AS_NODE", environment)

    def test_missing_runtime_reports_repo_install_command_without_spawning(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(electron_launcher, "source_root", return_value=Path(directory)), \
             patch.object(electron_launcher.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "npm.cmd ci --prefix desktop"):
                electron_launcher.launch()
            run.assert_not_called()

    def test_electron_failure_is_not_reported_as_successful_source_start(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            runtime = checkout / "desktop/node_modules/electron/dist/electron.exe"
            runtime.parent.mkdir(parents=True)
            runtime.touch()
            with patch.object(electron_launcher, "source_root", return_value=checkout), \
                 patch.object(electron_launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 3)):
                with self.assertRaisesRegex(RuntimeError, "状态码3"):
                    electron_launcher.launch()
