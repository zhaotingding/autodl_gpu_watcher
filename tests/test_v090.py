import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock, patch
import tempfile
import unittest

from autodl_watcher.training_config import TrainingServer
from autodl_watcher.training_tracker import TrainingTracker
from autodl_watcher.training_ui import process_time
from test_training import snapshot


class ProcessTimeTest(unittest.TestCase):
    def test_alive_duration_advances_but_exit_freezes_at_last_live_sample(self):
        tracker = TrainingTracker(TrainingServer("s", "server", "alias"))
        value = snapshot(now=10000)
        value["tasks"][0]["started_at"] = 9000
        first = tracker.update(value)["cards"][0]
        self.assertIn("00:16:40", process_time(first))
        value["observed_at"] = 10060
        second = tracker.update(value)["cards"][0]
        self.assertIn("00:17:40", process_time(second))
        value["tasks"][0]["alive"] = False
        value["observed_at"] = 11000
        exited = tracker.update(value)["cards"][0]
        self.assertEqual(process_time(exited), process_time(second))

    def test_gpu_console_includes_visible_process_start_and_sample_duration(self):
        from autodl_watcher.training_ui import gpu_table
        value = dict(received_at=9030, observed_at=9030, gpus=[dict(index=0, memory_used=6000, memory_total=12000, util=80, temperature=60)],
                     gpu_processes=[dict(gpu_index=0, pid=10, user="other-user", process="custom.py", started_at=9001)])
        text = gpu_table(value, "4090")
        self.assertIn("custom.py", text)
        self.assertIn("开始时间", text)
        self.assertIn("00:00:29", text)
        value["gpu_processes"][0].pop("started_at")
        self.assertIn("持续时间 —", gpu_table(value, "203-1"))

    def test_day_duration_and_unknown_start_are_explicit(self):
        self.assertIn("2天 01:01:01", process_time({"started_at": 100, "last_alive_at": 176561}))
        self.assertIn("持续时间 —", process_time({}))
        self.assertIn("持续时间未确认", process_time({"started_at": 100}))


class SourceIconWorkerTest(unittest.TestCase):
    def test_explicit_target_is_separate_from_the_data_root(self):
        from autodl_watcher import exe_icon
        from autodl_watcher.yaml_settings import read_settings, write_settings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root, code_root = root / 'personal data', root / 'checkout'
            data_root.mkdir()
            code_root.mkdir()
            target = code_root / 'selected launcher.exe'
            target.write_bytes(b'launcher placeholder; never execute')
            icon = data_root / '.ui' / 'icons' / 'chosen.ico'
            icon.parent.mkdir(parents=True)
            icon.write_bytes(b'icon bytes read only by mocked resource writer')
            write_settings(data_root / '.ui/icon.yaml', {'file': 'icons/chosen.ico'})
            kernel = Mock()
            kernel.OpenProcess.return_value, kernel.WaitForSingleObject.return_value = 1, 0
            with patch.dict(os.environ, AUTODL_APP_ROOT=str(data_root)), \
                 patch.object(sys, 'argv', ['autodl_watcher.exe_icon', '12345', str(target)]), \
                 patch.object(exe_icon.ctypes, 'WinDLL', return_value=kernel, create=True), \
                 patch.object(exe_icon, 'replace_icon') as replace:
                exe_icon.main()
            replace.assert_called_once_with(target, icon, backup_dir=data_root / '.ui')
            kernel.WaitForSingleObject.assert_called_once_with(1, 120000)
            self.assertIn('已同步', read_settings(data_root / '.ui/icon-executable-status.yaml')['message'])
            self.assertFalse((code_root / '.ui').exists())

    def test_python_interpreter_cannot_be_rewritten_by_the_icon_worker(self):
        from autodl_watcher import exe_icon
        from autodl_watcher.yaml_settings import read_settings, write_settings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / 'python.exe'
            original = b'project interpreter; never execute'
            interpreter.write_bytes(original)
            write_settings(root / '.ui/icon.yaml', {'file': None})
            kernel = Mock()
            kernel.OpenProcess.return_value, kernel.WaitForSingleObject.return_value = 1, 0
            with patch.dict(os.environ, AUTODL_APP_ROOT=str(root)), \
                 patch.object(sys, 'argv', ['autodl_watcher.exe_icon', '12345', str(interpreter)]), \
                 patch.object(exe_icon.ctypes, 'WinDLL', return_value=kernel, create=True), \
                 patch.object(exe_icon, 'replace_icon') as replace:
                exe_icon.main()
            replace.assert_not_called()
            self.assertEqual(interpreter.read_bytes(), original)
            self.assertIn('失败', read_settings(root / '.ui/icon-executable-status.yaml')['message'])


    @unittest.skipUnless(os.name == 'nt', 'Windows 图标模块入口')
    def test_module_entry_runs_and_reports_rejected_interpreter_target(self):
        from autodl_watcher.yaml_settings import read_settings
        checkout = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / 'python.exe'
            original = b'fake interpreter; never execute or rewrite'
            interpreter.write_bytes(original)
            env = os.environ.copy()
            env.update(AUTODL_APP_ROOT=str(root), PYTHONPATH=str(checkout / 'src'), PYTHONUTF8='1')
            result = subprocess.run([sys.executable, '-m', 'autodl_watcher.exe_icon', '12345', str(interpreter)],
                                    cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15,
                                    creationflags=subprocess.CREATE_NO_WINDOW)  # 真正执行模块入口，不启动 GUI 或任何 EXE。
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(interpreter.read_bytes(), original)
            self.assertIn('失败', read_settings(root / '.ui/icon-executable-status.yaml')['message'])


@unittest.skipUnless(os.name == "nt", "Windows EXE资源")
class ExecutableIconTest(unittest.TestCase):
    def test_resource_update_keeps_program_archive_and_authored_frames(self):
        root = Path(__file__).resolve().parents[1]
        staged = Path(os.environ.get("AUTODL_TEST_LAUNCHER", root / ".tools/build/launcher/AutoDLWatcher.exe"))
        if not staged.is_file():
            self.skipTest("尚未构建轻量启动器；不使用保留的根目录旧 EXE 代替验收")
        try:
            from PyInstaller.archive.readers import CArchiveReader
        except ImportError:
            self.skipTest("安装 build extra 后可验证 EXE 归档及图标")
        from autodl_watcher.exe_icon import replace_icon
        from test_app_icon import authored_icon, shell_icon
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            exe = folder / "AutoDLWatcher.exe"
            shutil.copyfile(staged, exe)  # 只改临时副本，保留旧成品与新暂存入口。
            original = CArchiveReader(str(exe))
            toc = original.toc
            content = {key: original.extract(key) for key in toc}
            icon = folder / "author.ico"
            authored_icon(icon)
            replace_icon(exe, icon)
            updated = CArchiveReader(str(exe))
            self.assertEqual(updated.toc, toc)
            for key in toc:
                self.assertEqual(updated.extract(key), content[key])
            for small, color in ((True, (239, 32, 48)), (False, (32, 80, 237))):
                frame, meta = shell_icon(exe, small)
                self.assertEqual(frame.getpixel((frame.width // 2, frame.height // 2))[:3], color, meta)
            self.assertTrue((folder / ".ui/icon-executable-backup.exe").is_file())
