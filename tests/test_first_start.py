"""干净克隆可以先配置 SSH，无需已有平台账号或安装浏览器。"""
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from autodl_watcher import gui
from autodl_watcher.paths import prepare_application


class FirstStartTest(unittest.TestCase):
    def test_unconfigured_monitor_reports_setup_before_creating_network_resources(self):
        from autodl_watcher.main import main
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare_application(Path(directory))
            with patch("autodl_watcher.monitor.PlatformBrowserCollector") as platform, \
                 patch("autodl_watcher.monitor.TelemetryApiCollector") as telemetry, \
                 redirect_stderr(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as stopped:
                    main(["--config", str(folder / "config.yaml"), "--monitor-only", "--no-login",
                          "--runtime-dir", str(folder)])
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn("Telemetry 待配置", output.getvalue())
                platform.assert_not_called()
                telemetry.assert_not_called()
                self.assertFalse((folder / "state.json").exists())

    def test_safe_defaults_open_settings_without_browser_or_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare_application(Path(directory))
            with patch.object(gui, "ROOT", folder), patch.object(gui.WatcherWindow, "refresh") as refresh:
                root = tk.Tk()
                root.withdraw()
                try:
                    window = gui.WatcherWindow(root)
                    root.update()
                    self.assertIn("尚未配置", window.session_text.get())
                    self.assertIsNone(window.process)
                    self.assertFalse(window.live.get())
                    self.assertFalse(window.training.ai.config.get("auto_generate"))
                    self.assertIsNotNone(window.open_settings("servers"))
                    refresh.assert_not_called()
                finally:
                    for timer in root.tk.call("after", "info"):
                        root.after_cancel(timer)
                    root.destroy()
