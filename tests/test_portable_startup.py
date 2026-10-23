"""不同安装布局均可启动，默认配置不会引用作者的账号或算力服务。"""
from importlib.resources import files
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from autodl_watcher import launcher, paths
from autodl_watcher.config import load_config


class PortableStartupTest(unittest.TestCase):
    def test_explicit_data_root_wins_for_source_and_wheel_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, AUTODL_APP_ROOT=directory):
                self.assertEqual(paths.application_root(), Path(directory).resolve())
                with patch.object(paths, "source_root", return_value=None):
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
                 patch.object(launcher, "check_environment", return_value={}), \
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


class CondaEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="autodl conda check ")
        self.addCleanup(self.folder.cleanup)
        self.prefix = Path(self.folder.name) / "envs/project"
        (self.prefix / "conda-meta").mkdir(parents=True)
        self.python = self.prefix / "python.exe"
        self.python.touch()
        for target, value in (("prefix", str(self.prefix)), ("base_prefix", str(self.prefix)),
                              ("executable", str(self.python))):
            patcher = patch.object(sys, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = patch.dict(os.environ, {"CONDA_PREFIX": str(self.prefix),
            "CONDA_DEFAULT_ENV": "project", "CONDA_EXE": str(Path(self.folder.name) / "Scripts/conda.exe")}, clear=True)
        environment.start()  # 只模拟本测试的专用环境，不依赖运行测试者当前激活的环境名。
        self.addCleanup(environment.stop)
        requirements = patch.object(launcher.importlib.metadata, "requires", return_value=[
            "PyYAML>=6,<7", "Pillow>=12,<13", "PyInstaller==6.22.3; extra == 'build'"])
        self.requires = requirements.start()
        self.addCleanup(requirements.stop)
        distribution = Mock(version="test")
        distribution.locate_file.return_value = self.prefix / "Lib/site-packages"
        distributions = patch.object(launcher.importlib.metadata, "distribution", return_value=distribution)
        self.distribution = distributions.start()
        self.addCleanup(distributions.stop)
        imports = patch.object(launcher, "import_module", create=True, side_effect=lambda name:
            SimpleNamespace(__file__=str(self.prefix / "Lib/site-packages" / name / "__init__.py")))
        self.import_module = imports.start()  # 模拟实际导入位置，与发行包元数据分别验证。
        self.addCleanup(imports.stop)
        user_site = patch("site.ENABLE_USER_SITE", False)
        user_site.start()  # 模拟文档中 python -s 的隔离状态。
        self.addCleanup(user_site.stop)

    def test_project_conda_dependencies_are_required_inside_selected_prefix(self):
        result = launcher.check_environment()
        self.assertEqual(result["environment"], "project")
        self.assertEqual(result["python"], str(self.python))
        self.assertEqual(set(result["dependencies"]), {"PyYAML", "Pillow"})
        self.assertNotIn("PyInstaller", result["dependencies"])  # 运行源码不要求安装可选构建工具。
        self.assertFalse(result["user_site_enabled"])
        self.assertEqual(result["dependencies"]["PyYAML"]["module_paths"],
                         [str(self.prefix / "Lib/site-packages/yaml/__init__.py")])

    def test_imported_dependency_outside_prefix_is_rejected_even_with_valid_metadata(self):
        self.import_module.return_value = SimpleNamespace(__file__=str(self.prefix.parent / "foreign/yaml.py"))
        self.import_module.side_effect = None  # 元数据在目标环境，但实际导入被其他目录遮蔽。
        with self.assertRaisesRegex(RuntimeError, "实际导入.*项目环境之外"):
            launcher.check_environment()

    def test_user_site_flag_is_reported_without_breaking_existing_console_entry(self):
        with patch("site.ENABLE_USER_SITE", True):
            result = launcher.check_environment()  # 已安装入口未必携带 -s，实际依赖来源仍逐项核验。
        self.assertTrue(result["user_site_enabled"])

    def test_dependency_import_failure_has_repair_guidance(self):
        self.import_module.side_effect = ImportError("missing native dependency")
        with self.assertRaisesRegex(RuntimeError, "无法导入.*pip install --no-user"):
            launcher.check_environment()

    def test_namespace_dependency_locations_are_checked(self):
        folder = str(self.prefix / "Lib/site-packages/playwright")
        self.import_module.side_effect = None
        self.import_module.return_value = SimpleNamespace(__file__=None, __path__=[folder])
        result = launcher.check_environment()
        self.assertEqual(result["dependencies"]["PyYAML"]["module_paths"], [folder])

    def test_base_missing_conda_activation_and_nested_venv_are_rejected(self):
        for values, base_prefix in (({"CONDA_DEFAULT_ENV": "base"}, str(self.prefix)),
                                    ({"CONDA_PREFIX": ""}, str(self.prefix)),
                                    ({"CONDA_PREFIX": str(self.prefix.parent)}, str(self.prefix)),
                                    ({}, str(self.prefix.parent))):
            with self.subTest(values=values, base_prefix=base_prefix):
                with patch.dict(os.environ, values), patch.object(sys, "base_prefix", base_prefix):
                    with self.assertRaisesRegex(RuntimeError, "专用Conda"):
                        launcher.check_environment()

    def test_base_prefix_cannot_be_disguised_by_changing_environment_name(self):
        with patch.dict(os.environ, CONDA_EXE=str(self.prefix / "Scripts/conda.exe")):
            with self.assertRaisesRegex(RuntimeError, "Conda base"):
                launcher.check_environment()

    def test_explicit_environment_name_must_match_actual_conda_environment(self):
        with patch.dict(os.environ, AUTODL_CONDA_ENV="another-project"):
            with self.assertRaisesRegex(RuntimeError, "环境.*不一致"):
                launcher.check_environment()

    def test_missing_project_or_dependency_has_install_guidance(self):
        with patch.object(launcher.importlib.metadata, "requires",
                          side_effect=launcher.importlib.metadata.PackageNotFoundError("autodl-gpu-watcher")):
            with self.assertRaisesRegex(RuntimeError, "项目尚未安装.*conda run"):
                launcher.check_environment()
        self.distribution.side_effect = launcher.importlib.metadata.PackageNotFoundError("Pillow")
        with self.assertRaisesRegex(RuntimeError, "缺少依赖.*pip install --no-user"):
            launcher.check_environment()

    def test_dependency_from_user_site_or_another_environment_is_rejected(self):
        self.distribution.return_value.locate_file.return_value = self.prefix.parent / "other/Lib/site-packages"
        with self.assertRaisesRegex(RuntimeError, "来自项目环境之外"):
            launcher.check_environment()  # 包存在并不意味着来源符合项目隔离要求。

    def test_child_environment_drops_foreign_python_paths_and_preserves_conda(self):
        checkout, data = self.prefix.parent / "checkout", self.prefix.parent / "data"
        with patch.dict(os.environ, PYTHONPATH="foreign-project", PYTHONHOME="global-python"), \
             patch.object(launcher, "source_root", return_value=checkout):
            child = launcher.child_environment(data)
        self.assertEqual(child["PYTHONPATH"], str(checkout / "src"))
        self.assertNotIn("PYTHONHOME", child)
        self.assertEqual(child["PYTHONNOUSERSITE"], "1")
        self.assertEqual(child["CONDA_PREFIX"], str(self.prefix))
        self.assertEqual(child["CONDA_DEFAULT_ENV"], "project")
        self.assertEqual(child["AUTODL_APP_ROOT"], str(data))
        self.assertEqual(child["PYTHONUTF8"], "1")

    def test_wheel_child_does_not_inherit_a_foreign_source_path(self):
        with patch.dict(os.environ, PYTHONPATH="foreign-project"), \
             patch.object(launcher, "source_root", return_value=None):
            self.assertNotIn("PYTHONPATH", launcher.child_environment(self.prefix / "data"))


if __name__ == "__main__":
    unittest.main()
