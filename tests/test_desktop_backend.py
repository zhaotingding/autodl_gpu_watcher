"""Electron桥接用临时资料及假worker验证；不连接真实平台、SSH或AI。"""
from dataclasses import asdict, replace
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from autodl_watcher.desktop_backend import DesktopBackend, _json_value
from autodl_watcher.launcher import child_environment
from autodl_watcher.session import GPU_PREFIX, HOSTS_PREFIX, REPORT_PREFIX, SESSION_PREFIX
from autodl_watcher.training_config import TrainingServer
from autodl_watcher.yaml_settings import read_settings, write_settings


class DesktopBackendTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copyfile(Path(__file__).parent / "fixtures/monitor.yaml", self.root / "config.yaml")
        self.backend = DesktopBackend(self.root, start_scheduler=False)
        self.credentials = patch("autodl_watcher.desktop_backend.ai_credentials.read", return_value="")
        self.credentials.start()

    def tearDown(self):
        self.backend.process = None
        self.backend.close()
        self.credentials.stop()
        self.temporary.cleanup()

    def test_first_snapshot_is_safe_serializable_and_does_not_start_business(self):
        state = self.backend.dispatch("snapshot")
        self.assertFalse(state["worker"]["running"])
        self.assertFalse(state["preferences"]["live"])
        self.assertEqual(state["servers"], [])
        self.assertEqual(state["theme"], "dark")
        self.assertEqual(state["platform"]["hosts"], [])
        self.assertEqual(state["app"]["python"], sys.executable)
        json.dumps(state, default=_json_value)
        probe = subprocess.run([sys.executable, "-s", "-c",
            "import sys; import autodl_watcher.desktop_backend; assert 'tkinter' not in sys.modules"],
            env=child_environment(self.root), capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(probe.returncode, 0, probe.stderr)  # 独立进程核对导入边界，不受Tk旧测试顺序影响。

    def test_theme_and_monitor_options_preserve_existing_preferences_and_secrets_are_not_returned(self):
        path = self.root / ".ui/preferences.yaml"
        write_settings(path, {"unknown": "keep", "ui": {"zoom": 1.1}, "poll": "120", "live": False})
        result = self.backend.dispatch("preferences.save", {"preferences": {"ui": {"theme": "light"}, "user": "本人"}})
        saved = read_settings(path)
        self.assertEqual(saved["unknown"], "keep")
        self.assertEqual(saved["ui"], {"zoom": 1.1, "theme": "light"})
        self.assertEqual(result["user"], "本人")
        self.assertNotIn("password", result["telemetry"])
        with self.assertRaises(argparse.ArgumentTypeError):
            self.backend.dispatch("preferences.save", {"preferences": {"poll": "0"}})
        self.assertEqual(read_settings(path), saved)

    def test_pin_changes_persist_without_registering_or_resetting_active_training(self):
        original = self.backend.dispatch("servers.save", {"server": {"name": "测试", "ssh_alias": "example"}})
        identifier = original["id"]
        self.backend.training.states[identifier]["active"] = True
        with patch.object(self.backend.training, "register") as register:
            result = self.backend.dispatch("servers.pin", {"server_id": identifier, "pinned": True})
        self.assertTrue(result["pinned"])
        register.assert_not_called()
        self.assertTrue(self.backend.training.status(identifier)["active"])
        self.assertTrue(read_settings(self.root / ".ui/servers.yaml")[0]["pinned"])

    def test_invalid_telemetry_does_not_save_unrelated_preferences_before_validation(self):
        self.backend.dispatch("preferences.save", {"preferences": {"user": "原用户"}})
        with self.assertRaises(ValueError):
            self.backend.dispatch("preferences.save", {"preferences": {
                "user": "新用户", "telemetry": {"endpoint": "invalid", "password_auth": True}}})
        self.assertEqual(self.backend.preferences["user"], "原用户")
        self.assertEqual(read_settings(self.backend.preference_path)["user"], "原用户")

    def test_source_launch_cannot_export_shortcut_for_unknown_or_global_executable(self):
        with patch.dict(os.environ, {"AUTODL_LAUNCHER_EXE": ""}):
            with self.assertRaisesRegex(ValueError, "轻量启动器"):
                self.backend.dispatch("icon.shortcut", {"path": str(self.root / "demo.lnk")})

    def test_ico_import_keeps_authored_frames_and_reset_preserves_original_file(self):
        import autodl_watcher
        original = Path(autodl_watcher.__file__).with_name("default.ico")
        result = self.backend.dispatch("icon.import", {"path": str(original)})
        saved = Path(result["path"])
        self.assertEqual(saved.read_bytes(), original.read_bytes())
        self.assertTrue(self.backend.icon_changed)
        restored = self.backend.dispatch("icon.reset")
        self.assertEqual(Path(restored["path"]), original)
        self.assertTrue(saved.exists())  # 恢复默认只改选择，不删除旧自定义图标或快捷方式资源。

    def test_worker_protocol_preserves_last_good_gpu_data_and_bounded_realtime_output(self):
        self.backend.accept_line(HOSTS_PREFIX + json.dumps([["autodl-203-1", "0/2", "可开机"]]))
        self.backend.accept_line(HOSTS_PREFIX + json.dumps([["autodl-203-1", "1/2", None]]))
        self.assertEqual(self.backend.hosts[0]["eligibility"], "可开机")
        self.backend.accept_line(SESSION_PREFIX + json.dumps({"status": "valid", "message": "已核验", "checked_at": "now"}))
        good = {"samples": [{"host": "gpu-203", "gpu_index": 0}], "collected_at": "old-time"}
        self.backend.accept_line(GPU_PREFIX + json.dumps(good))
        self.backend.accept_line(GPU_PREFIX + json.dumps({"samples": [], "error": "HTTP失败"}))
        self.assertEqual(self.backend.gpu_snapshot["samples"], good["samples"])
        self.assertEqual(self.backend.gpu_snapshot["collected_at"], "old-time")
        self.backend.accept_line(REPORT_PREFIX + json.dumps({"level": "success", "lines": ["监控正常", "用户甲"]}))
        self.assertEqual(self.backend.status, "监控正常")
        for number in range(2100):
            self.backend.accept_line(f"line {number}\n")
        self.assertEqual(len(self.backend.logs), 2000)
        self.assertEqual(self.backend.logs[-1]["text"], "line 2099")

    def test_monitor_stops_only_owned_worker_and_requires_valid_selection(self):
        self.backend.config = replace(self.backend.config,
            telemetry=replace(self.backend.config.telemetry, endpoint="https://gpu.example.invalid/api/latest"))
        with self.assertRaises(ValueError):
            self.backend._monitor_command({"entry": "", "user": "user"})
        self.backend.preferences.update(entry="autodl-203-1", user="user", live=True)
        command = self.backend._monitor_command({})
        self.assertIn("--monitor-only", command)  # 未核验入口维持既有只读门控。
        self.assertNotIn("--live", command)
        self.assertEqual(command[0], sys.executable)
        self.backend.process, self.backend.job = Mock(), "monitor"
        with patch.object(self.backend.training, "stop_all") as training_stop:
            self.backend.dispatch("monitor.stop")
        self.assertTrue(self.backend.stop_file.exists())
        training_stop.assert_not_called()

    def test_login_finish_writes_newline_and_shutdown_keeps_unfinished_login_visible(self):
        process = Mock(stdin=io.StringIO())
        process.poll.return_value = None
        self.backend.process, self.backend.job = process, "login"
        self.backend.dispatch("login.finish")
        self.assertEqual(process.stdin.getvalue(), "\n")
        with self.assertRaisesRegex(ValueError, "完成登录"):
            self.backend.dispatch("shutdown")
        self.assertFalse(self.backend.closing)
        process.terminate.assert_not_called()

    def test_ai_generation_needs_confirmed_current_task_and_associated_log(self):
        server = TrainingServer("demo", "测试", "example")
        self.backend.save_server(asdict(server))
        card = {"key": "boot:1:2", "pid": 1, "alive": True, "log_path": "/project/log.txt", "log_identity": "dev:inode",
                "log_sources": [{"path": "/project/log.txt", "identity": "dev:inode"}]}
        self.backend.training.states["demo"]["snapshot"] = {"cards": [card]}
        with patch.object(self.backend.ai, "generate", return_value="identity") as generate, \
             patch.object(self.backend.training, "reparse") as reparse:
            with self.assertRaisesRegex(ValueError, "确认"):
                self.backend.dispatch("ai.generate", {"server_id": "demo", "task_key": card["key"], "source": "train"})
            with self.assertRaisesRegex(ValueError, "日志不属于"):
                self.backend.dispatch("ai.generate", {"server_id": "demo", "task_key": card["key"], "log_path": "/other", "confirmed": True})
            generate.assert_not_called()
            self.backend.dispatch("ai.generate", {"server_id": "demo", "task_key": card["key"], "source": "train", "confirmed": True})
            generate.assert_called_once()
            reparse.assert_called_once_with("demo")

    def test_log_read_uses_prepared_existing_connection_and_returns_redacted_full_source(self):
        server = TrainingServer("demo", "测试", "example")
        self.backend.save_server(asdict(server))
        card = {"key": "boot:1:2", "pid": 1, "alive": True, "log_path": "/project/log.txt", "log_identity": "dev:inode"}
        self.backend.training.states["demo"]["snapshot"] = {"cards": [card]}
        with patch.object(self.backend.training, "prepare_log_read", return_value=lambda: {
            "path": card["log_path"], "size": 30000, "content": "a" * 20000 + "\npassword=private-value"}) as prepare:
            result = self.backend.dispatch("training.log", {"server_id": "demo", "task_key": card["key"]})
        prepare.assert_called_once()
        self.assertGreater(result["characters"], 20000)
        self.assertNotIn("private-value", result["text"])
        self.assertEqual(result["path"], card["log_path"])

    def test_shutdown_waits_for_own_training_and_restores_observer(self):
        self.backend.save_server({"id": "demo", "name": "测试", "ssh_alias": "example"})
        result = self.backend.dispatch("shutdown")
        self.assertTrue(result["closed"])
        self.assertTrue(self.backend.closed)
        self.assertFalse(self.backend.training.busy())
        self.assertFalse(self.backend.stop_file.exists())

    def test_stopped_training_can_show_cached_original_log_without_live_ssh(self):
        self.backend.save_server({"id": "demo", "name": "测试", "ssh_alias": "example"})
        card = {"key": "boot:1:2", "pid": 1, "alive": False, "log_path": "/project/training.log",
                "details": "epoch=3 loss=0.02", "preview": "short preview"}
        self.backend.training.states["demo"].update(active=False, snapshot={"cards": [card], "error": "SSH已断开"})
        with patch.object(self.backend.training, "prepare_log_read") as prepare:
            result = self.backend.dispatch("training.log", {"server_id": "demo", "task_key": card["key"], "full": False})
        self.assertEqual(result["text"], card["details"])
        self.assertEqual(result["path"], card["log_path"])
        self.assertFalse(result["full"])
        self.assertIn("最近采样", result["note"])
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
