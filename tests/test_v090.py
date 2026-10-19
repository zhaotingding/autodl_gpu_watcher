import os
from pathlib import Path
import shutil
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


@unittest.skipUnless(os.name == "nt", "Windows EXE资源")
class ExecutableIconTest(unittest.TestCase):
    def test_resource_update_keeps_program_archive_and_authored_frames(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / "AutoDLWatcher.exe").is_file():
            self.skipTest("尚未构建 Windows 成品；源码检出不要求自带 EXE")
        try:
            from PyInstaller.archive.readers import CArchiveReader
        except ImportError:
            self.skipTest("安装 build extra 后可验证 EXE 归档及图标")
        from autodl_watcher.exe_icon import replace_icon
        from test_app_icon import authored_icon, shell_icon
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            exe = folder / "AutoDLWatcher.exe"
            shutil.copyfile(root / "AutoDLWatcher.exe", exe)
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
