"""轻量启动器只选择专用Conda，检查通过后才启动现有源码GUI。"""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class CondaLauncherTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="autodl conda launcher ")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "checkout"
        (self.root / "tools").mkdir(parents=True)
        (self.root / "src/autodl_watcher").mkdir(parents=True)
        (self.root / "tools/gui_entry.py").touch()
        self.manager = Path(self.folder.name) / "conda/Scripts/conda.exe"
        self.manager.parent.mkdir(parents=True)
        self.manager.touch()
        source = Path(__file__).resolve().parents[1] / "tools/launcher_entry.py"
        self.module = runpy.run_path(str(source))
        self.start = self.module["start"]
        self.start.__globals__["__file__"] = str(self.root / "tools/launcher_entry.py")
        env = patch.dict(os.environ, {"CONDA_EXE": str(self.manager), "CONDA_DEFAULT_ENV": "project"}, clear=True)
        env.start()  # 环境选择不依赖测试者真实激活了哪个Conda。
        self.addCleanup(env.stop)
        frozen = patch.object(sys, "frozen", False, create=True)
        frozen.start()
        self.addCleanup(frozen.stop)

    def test_explicit_environment_and_manager_take_priority_and_forward_gui_options(self):
        other = self.manager.with_name("other-conda.exe")
        other.touch()
        selected = self.module["selected_environment"](["--env", "custom-project", "--conda", str(other), "--debug"])
        self.assertEqual(selected, ("custom-project", str(other.resolve()), ["--debug"]))
        self.assertEqual(self.module["selected_environment"]([]), ("auto-gpu", str(self.manager.resolve()), []))

    def test_double_click_without_activation_uses_project_default(self):
        for inherited in ("", "base", "root", "another-project"):
            with self.subTest(inherited=inherited), patch.dict(os.environ, CONDA_DEFAULT_ENV=inherited):
                selected = self.module["selected_environment"]([])
                self.assertEqual(selected, ("auto-gpu", str(self.manager.resolve()), []))
        with patch.dict(os.environ, clear=True), patch("shutil.which", return_value=str(self.manager)):
            self.assertEqual(self.module["selected_environment"]([])[0], "auto-gpu")

    def test_explicit_base_root_and_invalid_environment_are_rejected(self):
        for name in ("base", "root", "", "../base"):
            with self.subTest(name=name):
                with self.assertRaises(self.module["LaunchError"]):
                    self.module["selected_environment"](["--env", name])

    def test_explicit_missing_manager_never_falls_back_to_inherited_manager(self):
        with self.assertRaisesRegex(self.module["LaunchError"], "未找到Conda"):
            self.module["selected_environment"](["--conda", str(self.manager.with_name("missing.exe"))])

    def test_missing_conda_reports_guidance_instead_of_searching_for_python(self):
        with patch.dict(os.environ, CONDA_EXE=""), patch("shutil.which", return_value=None) as search:
            with self.assertRaisesRegex(self.module["LaunchError"], "未找到Conda"):
                self.module["selected_environment"]([])
            search.assert_called_once_with("conda.exe")  # 只定位管理器，不搜索全局Python。

    def test_launcher_environment_removes_bundle_and_foreign_python_paths(self):
        bundle = Path(self.folder.name) / "_MEI123"
        ordinary = Path(self.folder.name) / "conda/Scripts"
        path = os.pathsep.join((str(bundle), str(bundle / "bin"), str(ordinary)))
        with patch.dict(os.environ, PYTHONPATH="other-project", PYTHONHOME="global-python", PATH=path,
                        AUTODL_APP_ROOT=str(self.root / "personal-data")), \
             patch.object(sys, "_MEIPASS", str(bundle), create=True), \
             patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", str(self.root / "AutoDLWatcher.exe")):
            child = self.module["launch_environment"](self.root, "project", str(self.manager))
        self.assertNotIn("PYTHONHOME", child)
        self.assertNotIn("PYTHONPATH", child)
        self.assertEqual(child["PATH"], str(ordinary))
        self.assertEqual(child["AUTODL_APP_ROOT"], str(self.root / "personal-data"))
        self.assertEqual(child["AUTODL_CONDA_ENV"], "project")
        self.assertEqual(child["PYTHONNOUSERSITE"], "1")
        self.assertEqual(child["AUTODL_LAUNCHER_EXE"], str((self.root / "AutoDLWatcher.exe").resolve()))

    def test_missing_source_prevents_environment_checks_and_gui_spawn(self):
        (self.root / "tools/gui_entry.py").unlink()
        with patch("subprocess.run") as check, patch("subprocess.Popen") as spawn:
            with self.assertRaisesRegex(self.module["LaunchError"], "未找到项目源码"):
                self.start([])
            check.assert_not_called()
            spawn.assert_not_called()

    def test_preflight_failure_or_invalid_report_prevents_gui_spawn(self):
        cases = ((2, "", "missing dependency"), (0, "not json", ""),
                 (0, json.dumps({"environment": "another-project"}), ""))
        for code, output, error in cases:
            with self.subTest(code=code, output=output):
                report = subprocess.CompletedProcess([], code, stdout=output, stderr=error)
                with patch("subprocess.run", return_value=report), patch("subprocess.Popen") as spawn:
                    with self.assertRaises(self.module["LaunchError"]):
                        self.start([])
                    spawn.assert_not_called()  # 缺环境或依赖时不启动业务，也不悄悄换环境。

    def test_success_checks_then_launches_same_conda_with_forwarded_options(self):
        report = subprocess.CompletedProcess([], 0, stdout=json.dumps({"environment": "auto-gpu"}), stderr="")
        with patch("subprocess.run", return_value=report) as check, patch("subprocess.Popen") as spawn:
            self.start(["--debug"])
        expected = [str(self.manager.resolve()), "run", "-n", "auto-gpu", "--no-capture-output",
                    "python", "-s", str(self.root / "tools/gui_entry.py")]
        self.assertEqual(check.call_args.args[0], [*expected, "--check-environment"])
        self.assertEqual(spawn.call_args.args[0], [*expected, "--debug"])
        self.assertEqual(check.call_args.kwargs["env"], spawn.call_args.kwargs["env"])
        self.assertEqual(spawn.call_args.kwargs["cwd"], self.root)
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)
        spawn.return_value.wait.assert_not_called()  # 启动器交接后退出，不再常驻一个调度循环。

    @unittest.skipUnless(os.name == "nt", "Windows无控制台错误消息")
    def test_main_returns_failure_and_displays_install_guidance(self):
        main = self.module["main"]
        error = self.module["LaunchError"]("missing environment")
        with patch.dict(main.__globals__, start=Mock(side_effect=error)), \
             patch("ctypes.windll.user32.MessageBoxW") as dialog:
            self.assertEqual(main(), 1)
        self.assertIn("conda run -n auto-gpu", dialog.call_args.args[1])
        self.assertIn("pip install --no-user", dialog.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
