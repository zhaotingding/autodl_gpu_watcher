import argparse
import io
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autodl_watcher.config import load_config
from autodl_watcher.gui import ROOT, SESSION_PREFIX, discover, log_tag, monitor_command, worker_command, worker_python, main as gui_main
from autodl_watcher.collectors.platform import PlatformAuthenticationError, PlatformTransientError
from autodl_watcher.main import _build_parser, apply_monitor_options, main, positive_seconds


class GuiOptionsTest(unittest.TestCase):
    def test_pythonw_gui_uses_same_environment_for_piped_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            console = Path(directory) / "python.exe"  # 必须使用同环境实际存在的控制台解释器。
            console.touch()
            with patch("sys.executable", str(console.with_name("pythonw.exe"))):
                self.assertEqual(worker_python(), str(console))

    def test_missing_console_python_is_reported_without_global_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("sys.executable", str(Path(directory) / "pythonw.exe")):
                with self.assertRaisesRegex(FileNotFoundError, "项目解释器不存在"):
                    worker_python()  # 同目录缺失时不能搜索PATH或改用base。

    def test_worker_commands_reuse_gui_interpreter_and_disable_user_site(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python.exe"
            executable.touch()
            with patch("sys.executable", str(executable)):
                self.assertEqual(worker_command("discover"),
                                 [str(executable), "-s", "-u", "-m", "autodl_watcher.gui", "--discover"])
                self.assertEqual(worker_command("monitor", "--dry-run"),
                                 [str(executable), "-s", "-u", "-m", "autodl_watcher.main", "--dry-run"])
                self.assertEqual(worker_command("login"),
                                 [str(executable), "-s", "-u", "-m", "autodl_watcher.login"])
                with self.assertRaises(KeyError):
                    worker_command("arbitrary-module")  # 保留任务白名单，不能借入口执行任意模块。

    def test_log_colors_prioritize_failures(self):
        self.assertEqual(log_tag("开机成功，但验证失败"), "error")
        self.assertEqual(log_tag("连接断开，正在重连"), "error")
        self.assertEqual(log_tag("GPU额度不足，暂停自动开机"), "error")
        self.assertEqual(log_tag("已开机，继续监控"), "success")
        self.assertEqual(log_tag("开机成功 · connected"), "success")
        self.assertEqual(log_tag("本人占用待确认"), "normal")
        self.assertEqual(log_tag("登录会话已同步，等待核验"), "normal")

    def test_log_colors_follow_events_instead_of_configuration_or_request_receipts(self):
        cases = [("Telemetry容错：单次超时20秒；瞬时失败后等待2秒重试；每轮最多2次请求。", "normal"),
                 ("连接平台，正在采集……", "normal"), ("开机请求成功受理；等待占用确认", "normal"),
                 ("无卡关机请求成功受理", "normal"), ("验证成功后再恢复监控", "normal"),
                 ("本人 GPU 占用 有（本轮弹窗）", "success"), ("预选入口=已占用203-1", "success"),
                 ("本人 GPU 占用 无（实例列表）", "normal"), ("本人 GPU 占用 待确认", "normal"),
                 ("本轮采集失败：Telemetry timeout", "error"), ("Telemetry请求超时，正在重试", "error"),
                 ("二次确认未通过，暂停开机", "error"), ("结果：实例状态不允许开机", "error"),
                 ("完整账号实例列表或关机前状态无法核对", "error"), ("账号列表中目标身份已变化，暂缓释放额度", "error"),
                 ("暂停释放无卡实例", "error"), ("正在有卡启动，尚未确认运行或占用", "normal"),
                 ("本人 GPU 占用 有 · GPU 占用弹窗采集失败", "error")]
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(log_tag(line), expected)

    def test_gui_debug_option_reaches_window_without_starting_monitor(self):
        with patch("sys.argv", ["AutoDLWatcher.exe", "--debug"]), patch("autodl_watcher.gui.tk.Tk") as tk_root, \
                patch("autodl_watcher.gui.WatcherWindow") as window:
            gui_main()
            window.assert_called_once_with(tk_root.return_value, debug=True)
            tk_root.return_value.mainloop.assert_called_once()

    def test_monitor_uses_one_interval_and_ui_protocol(self):
        command = monitor_command(self.config, "autodl-203-1", "user", 1, True, Path("stop"), True)
        self.assertNotIn("--occupancy-seconds", command)
        self.assertIn("--poll-seconds", command)
        self.assertIn("--ui", command)
        self.assertIn("--convert-no-gpu", command)

    def test_discover_reports_authentication_and_network_failures_distinctly(self):
        failures = [(PlatformAuthenticationError("HTTP 401"), "invalid"),
                    (PlatformTransientError("Network.getResponseBody: No resource"), "unknown")]
        for error, expected in failures:
            with self.subTest(status=expected), patch("autodl_watcher.collectors.PlatformBrowserCollector") as factory, \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                factory.return_value.collect.side_effect = error
                with self.assertRaises(SystemExit) as failure:
                    discover()
                self.assertNotEqual(failure.exception.code, 0)
                messages = [json.loads(line[len(SESSION_PREFIX):]) for line in output.getvalue().splitlines()
                            if line.startswith(SESSION_PREFIX)]
                self.assertEqual([item["status"] for item in messages], ["checking", expected])
                self.assertTrue(messages[-1]["checked_at"])
                factory.return_value.close.assert_called_once()
                self.assertNotIn("Traceback", output.getvalue())

    def test_discover_reports_missing_component_as_local_error(self):
        from autodl_watcher.login import LocalStartupError
        with patch("autodl_watcher.collectors.PlatformBrowserCollector") as factory, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            factory.return_value.collect.side_effect = LocalStartupError("未找到 Windows PowerShell：missing/powershell.exe")
            with self.assertRaises(SystemExit):
                discover()
        self.assertIn("本地启动错误", output.getvalue())
        self.assertIn("powershell.exe", output.getvalue())
        self.assertIn('"status": "unknown"', output.getvalue())
        self.assertNotIn("网络", output.getvalue())
        self.assertNotIn("登录已失效", output.getvalue())
        factory.return_value.close.assert_called_once()

    def test_discover_marks_session_valid_only_after_collection(self):
        with patch("autodl_watcher.collectors.PlatformBrowserCollector") as factory, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            factory.return_value.collect.return_value = []
            discover()
            messages = [json.loads(line[len(SESSION_PREFIX):]) for line in output.getvalue().splitlines()
                        if line.startswith(SESSION_PREFIX)]
            self.assertEqual([item["status"] for item in messages], ["checking", "valid"])
            self.assertIn("WATCHER_HOSTS []", output.getvalue())
            factory.return_value.close.assert_called_once()

    def test_discover_gpu_failure_does_not_invalidate_confirmed_platform_session(self):
        from autodl_watcher.models import PlatformHost
        from autodl_watcher.session import GPU_PREFIX
        with patch("autodl_watcher.collectors.PlatformBrowserCollector") as platform, \
             patch("autodl_watcher.collectors.TelemetryApiCollector") as telemetry, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            platform.return_value.collect.return_value = [PlatformHost("gpu-203", 0, 2, ("autodl-203-1",), (("autodl-203-1", 0, 2),))]
            telemetry.return_value.collect.side_effect = OSError("telemetry unavailable")
            discover()
        messages = [json.loads(line[len(SESSION_PREFIX):]) for line in output.getvalue().splitlines() if line.startswith(SESSION_PREFIX)]
        self.assertEqual([item["status"] for item in messages], ["checking", "valid"])
        gpu = [json.loads(line[len(GPU_PREFIX):]) for line in output.getvalue().splitlines() if line.startswith(GPU_PREFIX)][0]
        self.assertEqual(gpu["samples"], [])
        self.assertIn("失败", gpu["error"])
        telemetry.return_value.collect.assert_called_once()
        telemetry.return_value.close.assert_called_once()

    def test_discover_without_telemetry_preserves_platform_hosts_and_valid_session(self):
        from autodl_watcher.models import PlatformHost
        from autodl_watcher.session import GPU_PREFIX
        for endpoint in ("", "invalid-endpoint"):
            config = replace(self.config, telemetry=replace(self.config.telemetry, endpoint=endpoint))
            with self.subTest(endpoint=endpoint), \
                 patch("autodl_watcher.gui.load_config", return_value=config), \
                 patch("autodl_watcher.collectors.PlatformBrowserCollector") as platform, \
                 patch("autodl_watcher.collectors.TelemetryApiCollector") as telemetry, \
                 patch("sys.stdout", new_callable=io.StringIO) as output:
                platform.return_value.collect.return_value = [
                    PlatformHost("gpu-203", 0, 2, ("autodl-203-1",), (("autodl-203-1", 0, 2),))]
                platform.return_value.get_account_instances.return_value = []
                discover()
            lines = output.getvalue().splitlines()
            statuses = [json.loads(line[len(SESSION_PREFIX):])["status"]
                        for line in lines if line.startswith(SESSION_PREFIX)]
            self.assertEqual(statuses, ["checking", "valid"])
            self.assertIn("autodl-203-1", output.getvalue())
            gpu = next(json.loads(line[len(GPU_PREFIX):]) for line in lines if line.startswith(GPU_PREFIX))
            self.assertIn("Telemetry 待配置", gpu["error"])
            self.assertEqual(gpu["samples"], [])
            telemetry.assert_not_called()
            platform.return_value.close.assert_called_once()

    def test_gpu_message_includes_visible_full_host_but_excludes_other_hosts(self):
        from datetime import datetime
        from autodl_watcher.models import GpuSample, PlatformHost
        from autodl_watcher.session import GPU_PREFIX, emit_gpu_samples
        samples = [GpuSample(host, 0, "GPU", 40, 1000, 32000, datetime.now()) for host in ("gpu-203", "gpu-999")]
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            emit_gpu_samples(samples, [PlatformHost("gpu-203", 0, 2)], 90)
        data = json.loads(output.getvalue()[len(GPU_PREFIX):])
        self.assertEqual([sample["host"] for sample in data["samples"]], ["gpu-203"])
        self.assertEqual(data["samples"][0]["util_pct"], 40)
        self.assertEqual(data["stale_after_seconds"], 90)

    def test_discover_cleanup_failure_does_not_mask_authentication_message(self):
        with patch("autodl_watcher.collectors.PlatformBrowserCollector") as factory, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            factory.return_value.collect.side_effect = PlatformAuthenticationError("HTTP 401")
            factory.return_value.close.side_effect = OSError("close failed")
            with self.assertRaises(SystemExit):
                discover()
            self.assertIn('"status": "invalid"', output.getvalue())
            self.assertIn("清理失败", output.getvalue())

    def setUp(self):
        self.config = load_config(Path(__file__).parent / "fixtures/monitor.yaml")
        loader = patch("autodl_watcher.gui.load_config", return_value=self.config)
        loader.start()  # 主机发现测试只读夹具，不能隐式加载维护者个人配置。
        self.addCleanup(loader.stop)

    def test_monitor_command_rejects_missing_telemetry_before_starting_worker(self):
        config = replace(self.config, telemetry=replace(self.config.telemetry, endpoint=""))
        with patch("autodl_watcher.gui.worker_command") as worker:
            with self.assertRaisesRegex(ValueError, "Telemetry 待配置"):
                monitor_command(config, "autodl-203-1", "user", 60, False, Path("stop"))
            worker.assert_not_called()

    def test_project_local_runtime_paths(self):
        self.assertEqual(self.config.platform.user_data_dir, Path(__file__).parent / "fixtures/runtime" / "browser_profile")
        self.assertEqual(self.config.runtime.state_file, Path(__file__).parent / "fixtures/runtime" / "state.json")
        self.assertFalse(hasattr(self.config.occupancy, "database_path"))

    def test_invalid_intervals_are_rejected(self):
        for value in ("0", "-1", "nan", "inf", "-inf", "", "abc"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                positive_seconds(value)
        self.assertEqual(positive_seconds("0.5"), 0.5)

    def test_username_and_large_poll_interval_reach_core(self):
        args = _build_parser("gpu-203").parse_args(["--user", " 新用户 ", "--poll-seconds", "120"])
        config = apply_monitor_options(self.config, args)
        self.assertEqual(config.occupancy.self_user, "新用户")
        self.assertEqual(config.monitor.poll_seconds, 120)
        self.assertGreaterEqual(config.monitor.max_sample_gap_seconds, 240)
        self.assertFalse(hasattr(config.occupancy, "interval_seconds"))

    def test_unknown_server_allows_monitoring_but_blocks_live_start(self):
        command = monitor_command(self.config, "autodl-999-1", "测试用户", 1, False, Path("stop"))
        self.assertIn("--monitor-only", command)
        self.assertIn("gpu-999", command)
        with self.assertRaises(ValueError):
            monitor_command(self.config, "autodl-999-1", "测试用户", 1, True, Path("stop"))

    def test_no_gpu_conversion_requires_live_and_fixed_entry(self):
        with self.assertRaises(ValueError):
            monitor_command(self.config, "autodl-203-1", "user", 1, False, Path("stop"), True)
        command = monitor_command(self.config, "autodl-203-1", "user", 1, True, Path("stop"), True)
        self.assertIn("--convert-no-gpu", command)
        self.assertIn("--entry", command)
        self.assertEqual(command[command.index("--entry") + 1], "autodl-203-1")
        for args in (["--convert-no-gpu", "--live"], ["--convert-no-gpu", "--entry", "1", "--dry-run"]):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                main(["--config", str(Path(__file__).parent / "fixtures/monitor.yaml"), *args])

    def test_empty_username_is_rejected(self):
        with self.assertRaises(ValueError):
            monitor_command(self.config, "autodl-203-1", " ", 1, False, Path("stop"))

    def test_live_start_requires_enabled_configuration(self):
        config = replace(self.config, auto_start=replace(self.config.auto_start, enabled=False))
        with self.assertRaises(ValueError):
            monitor_command(config, "autodl-203-1", "user", 1, True, Path("stop"))

    def test_stopped_monitor_never_contacts_platform_and_saves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stop = root / "stop"
            stop.touch()
            with patch("autodl_watcher.monitor.PlatformBrowserCollector") as platform:
                main(["--config", str(Path(__file__).parent / "fixtures/monitor.yaml"), "--dry-run", "--stop-file", str(stop),
                      "--runtime-dir", str(root), "--no-login"])
                platform.return_value.collect.assert_not_called()
                platform.return_value.close.assert_called_once()
            self.assertTrue((root / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
