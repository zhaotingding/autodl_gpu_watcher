from __future__ import annotations  # 源码入口、轻量启动器暂存及资料隔离回归。

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from autodl_watcher import launcher
from autodl_watcher.gui import worker_command


class PackageLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    def source_entry(self):
        original = sys.path[:]
        self.addCleanup(lambda: setattr(sys, "path", original))  # runpy脚本兼容导入不会污染后续测试路径。
        return runpy.run_path(str(self.root / "tools/gui_entry.py"))["main"]

    @unittest.skipUnless(os.name == "nt", "无控制台启动错误由Windows弹窗报告")
    def test_source_startup_error_preserves_nonzero_exit_and_data_root(self):
        entry = self.source_entry()
        original_streams = sys.stdout, sys.stderr
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, AUTODL_APP_ROOT=directory), \
                 patch.object(sys, "argv", ["gui_entry.py"]), \
                 patch.object(launcher, "check_environment", return_value={}), \
                 patch("autodl_watcher.gui.main", side_effect=RuntimeError("startup-test")), \
                 patch("ctypes.windll.user32.MessageBoxW") as dialog:
                with self.assertRaises(SystemExit) as failure:
                    entry()  # 轻量EXE必须能收到失败，不能把启动失败吞成0。
                self.assertEqual(failure.exception.code, 1)
                dialog.assert_called_once()
                self.assertIn("startup-test", dialog.call_args.args[1])
                self.assertIn("startup-test", (Path(directory) / ".ui/startup.log").read_text(encoding="utf-8"))
                self.assertEqual((sys.stdout, sys.stderr), original_streams)
                self.assertEqual(os.environ["AUTODL_APP_ROOT"], directory)

    @unittest.skipUnless(os.name == "nt", "无控制台环境错误由Windows弹窗报告")
    def test_invalid_environment_prevents_gui_and_configuration_creation(self):
        entry = self.source_entry()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, AUTODL_APP_ROOT=directory), \
                 patch.object(sys, "argv", ["gui_entry.py"]), \
                 patch.object(launcher, "check_environment", side_effect=RuntimeError("需要专用Conda环境")), \
                 patch("autodl_watcher.gui.main") as gui, \
                 patch("ctypes.windll.user32.MessageBoxW") as dialog:
                with self.assertRaises(SystemExit) as failure:
                    entry()
                self.assertEqual(failure.exception.code, 1)
                gui.assert_not_called()
                dialog.assert_called_once()
                self.assertFalse((Path(directory) / "config.yaml").exists())

    def test_launcher_build_is_onefile_and_preserves_old_program_and_personal_data(self):
        build = runpy.run_path(str(self.root / "tools/build_exe.py"))["main"]
        original_path = sys.path[:]
        self.addCleanup(lambda: setattr(sys, "path", original_path))
        with tempfile.TemporaryDirectory(prefix="autodl launcher build ") as directory:
            root = Path(directory)
            preserved = {"AutoDLWatcher.exe": b"old main", "_internal/AutoDLWorker.exe": b"old worker",
                         "config.local.yaml": b"user settings", ".ui/preferences.yaml": b"user preferences",
                         "runtime/browser_profile/preserved.txt": b"login data", ".env": b"local environment"}
            for name, content in preserved.items():
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)
            commands = []
            def fake_build(command, **kwargs):
                commands.append(command)
                staged = root / ".tools/build/launcher"
                staged.mkdir(parents=True, exist_ok=True)
                (staged / "AutoDLWatcher.exe").write_bytes(b"new launcher")
                return subprocess.CompletedProcess(command, 0)
            with patch.dict(build.__globals__, ROOT=root), \
                 patch.object(launcher, "check_environment", return_value={}), \
                 patch("subprocess.run", side_effect=fake_build):
                for stage_only in (True, False):
                    self.assertEqual(build(stage_only=stage_only), root / ".tools/build/launcher")
            for command in commands:
                self.assertEqual(command[:4], [sys.executable, "-s", "-m", "PyInstaller"])
                self.assertIn("--onefile", command)
                self.assertEqual(command[-1], str(root / "tools/launcher_entry.py"))
                self.assertNotIn("--add-data", command)  # 不把业务包和个人资料收进启动器。
                self.assertNotIn("--collect-all", command)
            for name, content in preserved.items():
                self.assertEqual((root / name).read_bytes(), content, name)
            staged = root / ".tools/build/launcher"
            self.assertEqual([path.name for path in staged.iterdir()], ["AutoDLWatcher.exe"])

    def test_source_worker_uses_same_project_environment_without_user_site(self):
        with tempfile.TemporaryDirectory(prefix="autodl worker environment ") as directory:
            env = launcher.child_environment(Path(directory))
            code = "import json,sys,site; print(json.dumps({'python':sys.executable,'prefix':sys.prefix,'user_site':site.ENABLE_USER_SITE}))"
            result = subprocess.run([launcher.project_python(), "-s", "-c", code], cwd=directory,
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(Path(report["python"]).resolve(), Path(launcher.project_python()).resolve())
            self.assertEqual(Path(report["prefix"]).resolve(), Path(sys.prefix).resolve())
            self.assertFalse(report["user_site"])

    def test_stopped_source_worker_never_samples_and_writes_only_selected_data_root(self):
        with tempfile.TemporaryDirectory(prefix="autodl source worker ") as directory:
            folder = Path(directory)
            (folder / "config.yaml").write_bytes((self.root / "tests/fixtures/monitor.yaml").read_bytes())
            stop = folder / "stop"
            stop.touch()  # 首次采样前即停止，测试不得访问真实平台或SSH。
            command = worker_command("monitor", "--monitor-only", "--no-login", "--stop-file", str(stop),
                                     "--runtime-dir", str(folder))
            result = subprocess.run(command, cwd=folder, env=launcher.child_environment(folder),
                                    capture_output=True, text=True, encoding="utf-8", timeout=30,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((folder / "state.json").is_file())
            self.assertIn(str(folder / "config.yaml"), result.stdout)
            self.assertFalse((folder / "runtime/browser_profile").exists())
            self.assertFalse((folder / "_internal").exists())


if __name__ == "__main__":
    unittest.main()
