import json
import yaml
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from autodl_watcher import gui


class GuiWindowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        shutil.copyfile(Path(__file__).parent / "fixtures/monitor.yaml", self.folder / "config.yaml")
        self.location = patch.object(gui, "ROOT", self.folder)
        self.location.start()
        self.refresh = patch.object(gui.WatcherWindow, "refresh")
        self.refresh_mock = self.refresh.start()  # 窗口启动会自动只读刷新；本测试不得连接真实服务。
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = gui.WatcherWindow(self.root)
        self.window.open_settings("monitor").withdraw()

    def tearDown(self):
        if self.window.process is not None:
            self.window.stop()
            self.pump(lambda: self.window.process is None)
        self.destroy_root()
        self.location.stop()
        self.refresh.stop()
        self.temp.cleanup()

    def destroy_root(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)  # 测试反复创建解释器，先取消旧窗口回调再销毁。
        self.root.destroy()

    def pump(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.assertTrue(condition(), "UI 子进程未在期限内完成")


    def test_global_settings_outside_server_pages_and_single_window(self):
        button = self.window.settings_button
        self.assertIs(button.master, self.window.notebook.master)
        self.assertEqual(button.cget("text"), "全局设置")
        dialog = self.window.open_settings("ai")
        self.assertIs(dialog, self.window.icons.configure())
        self.assertEqual(len(dialog.pages), 4)
        self.assertEqual(len([c for c in self.root.winfo_children() if isinstance(c, tk.Toplevel)]), 1)
        self.assertIn("Auto算力与训练调度", self.root.title())

    def test_network_button_opens_global_single_panel_without_starting_business(self):
        from autodl_watcher.network_monitor import NetworkSnapshot
        self.assertEqual(self.window.network_button.cget("text"), "网络连接详情")
        self.assertIs(self.window.network_button.master, self.window.settings_button.master)
        self.assertIsNone(self.window.network_monitor)  # 未打开时不启动系统采样。
        monitor = Mock()
        monitor.poll.return_value = NetworkSnapshot(time.time(), (), (), 0.01)
        self.window.network_monitor = monitor
        with patch.object(self.window.training, "start") as training_start:
            dialog = self.window.open_network()
            dialog.automatic.set(False)
            self.pump(lambda: dialog._snapshot is not None)
            self.assertIs(dialog, self.window.open_network())
            self.assertIsNone(self.window.process)
            training_start.assert_not_called()
            dialog.close()
            self.assertEqual(dialog.state(), "withdrawn")
            self.assertIs(dialog, self.window.open_network())
        dialog.destroy()

    def test_network_entry_does_not_overlap_settings_at_minimum_window_width(self):
        self.root.geometry("820x660")
        self.root.deiconify()
        self.root.update()
        left, right = self.window.network_button, self.window.settings_button
        self.assertLessEqual(left.winfo_x() + left.winfo_width(), right.winfo_x())

    def test_ai_checkbox_uses_tick_indicator_and_keeps_opt_in_semantics(self):
        dialog=self.window.open_settings("ai")
        page=dialog.pages["ai"]
        def controls(widget):
            for child in widget.winfo_children():
                yield child
                yield from controls(child)
        check=next(child for child in controls(page) if isinstance(child, gui.ttk.Checkbutton))
        self.assertEqual(check.cget("style"),"Watcher.TCheckbutton")
        self.assertFalse(page.auto.get())
        check.invoke();self.assertTrue(page.auto.get())
        check.invoke();self.assertFalse(page.auto.get())
        self.assertIn('Watcher.indicator', str(gui.ttk.Style(self.root).layout(check.cget('style'))))

    def test_startup_schedules_readonly_session_check(self):
        self.root.update()
        self.refresh_mock.assert_called_once()
        self.assertEqual(self.window.session_status, "unknown")
        self.assertEqual(self.window.session_label.winfo_manager(), "pack")

    def test_authentication_and_transient_failures_restore_buttons_and_preserve_reason(self):
        for status, message in [("invalid", "登录已失效，请重新登录"),
                                ("unknown", "网络或响应错误，无法确认会话")]:
            with self.subTest(status=status):
                payload = json.dumps({"status": status, "message": message, "checked_at": "2026-09-26T16:00:00"})
                code = f"import sys; print({gui.SESSION_PREFIX + payload!r},flush=True); sys.exit(3)"
                self.window._launch([sys.executable, "-u", "-c", code], "discover")
                self.pump(lambda: self.window.process is None)
                self.assertEqual(self.window.session_status, status)
                self.assertEqual(self.window.status.get(), message)
                self.assertIn("2026-09-26T16:00:00", self.window.session_text.get())
                self.assertEqual(str(self.window.login_button["state"]), "normal")
                self.assertEqual(str(self.window.refresh_button["state"]), "normal")

    def test_pipe_read_and_cleanup_errors_cannot_leave_buttons_locked(self):
        class BrokenPipe:
            def __iter__(self):
                raise OSError("pipe read failed")
            def close(self):
                raise OSError("pipe close failed")
        process = Mock(stdout=BrokenPipe(), stdin=BrokenPipe())
        process.poll.return_value = None
        def stopped_wait():
            process.terminate.assert_called_once()
            return 7
        process.wait.side_effect = stopped_wait
        self.window.process, self.window.job = process, "discover"
        self.window._session("checking", "正在核验")
        self.window._busy(True)
        with patch.object(self.window.events, "put", wraps=self.window.events.put) as emitted:
            self.window._read(process)
        self.assertTrue(any(call.args[0][0] == "line" and "读取失败" in call.args[0][1]
                            for call in emitted.call_args_list))
        self.window._drain()
        self.assertIsNone(self.window.process)
        self.assertEqual(str(self.window.refresh_button["state"]), "normal")
        self.assertIn("读取失败", self.window.log.get("1.0", "end"))  # 实时错误可见，不会因为后台收尾而消失。
        self.assertEqual(self.window.session_status, "unknown")
        self.assertIn("核验未完成", self.window.session_text.get())
        process.wait.assert_called_once()

    def test_worker_exit_without_validation_cannot_leave_session_checking(self):
        for job, code in [("discover", 3), ("discover", 0), ("monitor", 0), ("login", 4)]:
            with self.subTest(job=job, code=code):
                self.window.process, self.window.job = Mock(), job
                self.window._session("checking", "正在核验")
                self.window._busy(True)
                self.window.events.put(("exit", code))
                self.window._drain()
                self.assertEqual(self.window.session_status, "unknown")
                self.assertIn("核验未完成", self.window.status.get())
                self.assertEqual(str(self.window.login_button["state"]), "normal")

    def test_monitor_pipe_failure_requests_safe_stop_before_waiting(self):
        stdout = Mock()
        stdout.__iter__ = Mock(side_effect=OSError("pipe read failed"))
        process = Mock(stdout=stdout)
        def stopped_wait():
            self.assertTrue(self.window.stop_file.exists())
            stdout.close.assert_called_once()
            return 7
        process.wait.side_effect = stopped_wait
        self.window.job = "monitor"
        self.window._read(process)
        process.terminate.assert_not_called()
        self.assertEqual(self.window.events.get()[0], "line")
        self.assertEqual(self.window.events.get(), ("exit", 7))

    def test_login_pipe_failure_does_not_interrupt_profile_sync(self):
        stdout = Mock()
        stdout.__iter__ = Mock(side_effect=OSError("pipe read failed"))
        process = Mock(stdout=stdout)
        process.wait.return_value = 7
        self.window.job = "login"
        self.window._read(process)
        process.terminate.assert_not_called()
        stdout.close.assert_not_called()
        self.assertFalse(self.window.stop_file.exists())

    def test_bad_message_does_not_stop_event_pump_or_lose_exit(self):
        self.window.process, self.window.job = Mock(), "discover"
        self.window._busy(True)
        self.window.events.put(("line", gui.DATA_PREFIX + "{broken"))
        self.window._drain()
        self.window.events.put(("line", gui.SESSION_PREFIX + json.dumps(
            {"status": "invalid", "message": "登录已失效", "checked_at": "2026-09-26T16:00:00"})))
        self.window.events.put(("exit", 3))
        self.pump(lambda: self.window.process is None)
        self.assertEqual(self.window.status.get(), "登录已失效")
        self.assertEqual(str(self.window.login_button["state"]), "normal")

    def test_login_profile_sync_is_pending_until_real_validation_message(self):
        self.window.session_status = "invalid"  # 从真实过期状态重登，成功结果须覆盖旧状态。
        self.window.session_text.set("登录会话已过期")
        checking = gui.SESSION_PREFIX + json.dumps(
            {"status": "checking", "message": "资料已同步，等待核验", "checked_at": "2026-09-26T16:00:00"})
        valid = gui.SESSION_PREFIX + json.dumps(
            {"status": "valid", "message": "主机接口核验成功", "checked_at": "2026-09-26T16:00:01"})
        code = f"print({checking!r},flush=True); input(); print({valid!r},flush=True)"
        self.window._launch([sys.executable, "-u", "-c", code], "login")
        self.pump(lambda: "资料已同步" in self.window.session_text.get())
        self.assertEqual(self.window.session_status, "checking")
        self.window.finish_login()
        self.pump(lambda: self.window.process is None)
        self.assertEqual(self.window.session_status, "valid")
        self.assertIn("16:00:01", self.window.session_text.get())

    def test_selection_and_custom_settings_are_saved(self):
        self.window._populate([("autodl-202-4", "3/3", "只读监控")])
        self.window.live.set(False)
        self.window.convert_no_gpu.set(False)
        self.window.user.set("任意用户名")
        self.window.poll.set("0.5")
        self.assertTrue(self.window.save())
        settings = yaml.safe_load((self.folder / ".ui/preferences.yaml").read_text(encoding="utf-8"))
        self.assertEqual(settings["entry"], "autodl-202-4")
        self.assertEqual(settings["user"], "任意用户名")
        self.assertEqual(settings["poll"], "0.5")
        self.assertFalse(self.window.live.get())

    def test_report_updates_status_and_realtime_output_without_usage_statistics(self):
        self.assertFalse(hasattr(self.window, "export_button"))
        self.assertFalse(hasattr(self.window, "usage_check"))
        self.assertTrue(hasattr(self.window, "log"))
        headline = "[12:32:02] 监控203-1 | 实例状态：有卡运行 | 动作：继续监控 | 平台空闲：203-1=0/2"
        self.window.events.put(("line", gui.REPORT_PREFIX + json.dumps({"lines": [headline, "  203-1[#0:失败也是名字]"], "level": "success"})))
        self.window._drain()
        self.assertEqual(self.window.status.get(), headline)
        self.assertEqual(str(self.window.status_label.cget("foreground")), "#18733a")
        output = self.window.log.get("1.0", "end")
        self.assertIn(headline, output)
        self.assertIn("203-1[#0:失败也是名字]", output)
        self.assertEqual(self.window.log.tag_names("2.3"), ("success",))
        self.assertNotIn(gui.REPORT_PREFIX, output)  # 内部JSON协议不作为界面输出。
        self.assertFalse(hasattr(self.window, "usage"))
        self.assertEqual(len(self.window.settings_window.inputs), 2)
        self.assertTrue(self.window.save())
        settings = yaml.safe_load((self.folder / ".ui/preferences.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("usage", settings)

    def test_report_levels_apply_to_status_and_realtime_group(self):
        for level in ("error", "normal"):
            self.window.events.put(("line", gui.REPORT_PREFIX + json.dumps({"lines": [
                "[12:32:02] 监控203-1 | 实例状态：无卡运行 | 动作：等待", "  203-1[#0:成功]"], "level": level})))
            self.window._drain()
            self.assertEqual(str(self.window.status_label.cget("foreground")),
                             "#b42318" if level == "error" else "#245cc4")
            self.assertNotIn("#0:成功", self.window.status.get())  # 当前状态不重复展开占用详情。
        self.assertEqual(self.window.log.tag_names("1.0"), ("error",))
        self.assertEqual(self.window.log.tag_names("3.0"), ("normal",))

    def test_realtime_output_is_bounded_and_does_not_write_monitor_history(self):
        self.window._append_output("\n".join(f"实时采样 {index}" for index in range(2050)))
        output = self.window.log.get("1.0", "end-1c").splitlines()
        self.assertEqual(len(output), 2000)
        self.assertEqual((output[0], output[-1]), ("实时采样 50", "实时采样 2049"))
        self.assertEqual(self.window.log.cget("state"), "disabled")
        self.assertFalse(list(self.folder.rglob("watcher.log")))

    def test_default_interval_and_legacy_preferences_migrate_once(self):
        self.assertEqual(float(self.window.poll.get()), 60)
        preferences = self.folder / ".ui/preferences.yaml"
        preferences.write_text(json.dumps({"poll": "30", "usage": "300", "user": "保留用户名", "entry": "autodl-203-1"}), encoding="utf-8")
        for expected in ("60", "120"):
            self.destroy_root()
            self.root = tk.Tk()
            self.root.withdraw()
            self.window = gui.WatcherWindow(self.root, debug=True)
            self.assertEqual(self.window.poll.get(), expected)
            self.assertEqual(self.window.user.get(), "保留用户名")
            self.window.poll.set("120")
            self.assertTrue(self.window.save())
        self.assertNotIn("usage", yaml.safe_load(preferences.read_text(encoding="utf-8")))

    def test_resource_table_updates_all_visible_servers_with_one_row_per_gpu(self):
        from datetime import datetime, timedelta
        entries = ("autodl-201-1", "autodl-201-2", "autodl-202-2", "autodl-202-4", "autodl-203-1", "autodl-203-2")
        self.window._populate([(entry, "0/4" if "201" in entry else "0/2", None) for entry in entries])
        samples = [{"host": host, "gpu_index": index, "gpu_name": "测试GPU", "util_pct": 30 + index,
                    "memory_used_mb": 16000, "memory_total_mb": 32000, "observed_at": datetime.now().isoformat()}
                   for host, count in (("gpu-203", 2), ("gpu-201", 4)) for index in range(count)]
        self.window._accept_gpu_snapshot({"samples": samples, "stale_after_seconds": 90})
        self.window._render_resources()
        self.assertEqual(self.window.table.get_children(), entries)
        for entry, count in (("autodl-203-1", 2), ("autodl-203-2", 2), ("autodl-201-1", 4), ("autodl-201-2", 4)):
            children = self.window.table.get_children(entry)
            self.assertEqual(children, tuple(gui.gpu_row_id(entry, index) for index in range(count)))
            self.assertEqual(self.window.table.set(entry, "memory"), "")
            self.assertEqual(self.window.table.set(entry, "util"), "")  # 父行不再拼接全部GPU长串。
            self.assertTrue(self.root.tk.getboolean(self.window.table.item(entry, "open")))
            for index, child in enumerate(children):
                self.assertEqual(self.window.table.item(child, "text"), f"GPU #{index}")
                self.assertIn("50%", self.window.table.set(child, "memory"))
                self.assertEqual(self.window.table.set(child, "util").count("%"), 1)
                self.assertRegex(self.window.table.set(child, "capacity"), r"^\d+\.\d / \d+\.\d$")
                self.assertIn("物理机", self.window.table.set(child, "state"))
        self.assertIn("GiB", self.window.table.heading("capacity", "text"))
        first = gui.gpu_row_id("autodl-203-1", 0)
        second = gui.gpu_row_id("autodl-203-2", 0)
        self.assertEqual(self.window.table.set(first, "memory"), self.window.table.set(second, "memory"))
        self.assertNotIn("model", self.window.table.cget("columns"))
        self.assertFalse(hasattr(self.window, "gpu_panel"))
        for sample in samples:
            sample["observed_at"] = (datetime.now() - timedelta(seconds=91)).isoformat()
        self.window._accept_gpu_snapshot({"samples": samples, "stale_after_seconds": 90})
        self.window._render_resources()
        self.assertIn("50%", self.window.table.set(first, "memory"))  # 延迟样本仍保留最后实测值，不整格替换成“已过期”。
        self.assertIn("%", self.window.table.set(gui.gpu_row_id("autodl-201-1", 0), "util"))
        self.window._accept_gpu_snapshot({"samples": [], "error": "采集失败"})
        self.window._render_resources()
        self.assertIn("50%", self.window.table.set(first, "memory"))
        self.assertIn("失败", self.window.table.set(first, "state"))

    def test_entry_resource_samples_take_priority_over_physical_host_fallback(self):
        from datetime import datetime
        self.window._populate([("autodl-203-1", "0/2", None), ("autodl-203-2", "0/2", None)])
        stamp = datetime.now().isoformat()
        self.window._accept_gpu_snapshot({"samples": [
            {"host": "gpu-203", "gpu_index": index, "gpu_name": "物理GPU", "util_pct": 30,
             "memory_used_mb": 16384, "memory_total_mb": 32768, "observed_at": stamp} for index in range(2)] + [
            {"host": "autodl-203-1", "gpu_index": 0, "gpu_name": "入口GPU", "util_pct": 93,
             "memory_used_mb": 8192, "memory_total_mb": 32768, "observed_at": stamp}]})
        self.window._render_resources()
        exact = gui.gpu_row_id("autodl-203-1", 0)
        self.assertIn("25%", self.window.table.set(exact, "memory"))
        self.assertIn("93%", self.window.table.set(exact, "util"))
        self.assertIn("入口", self.window.table.set(exact, "state"))
        self.assertEqual(self.window.table.get_children("autodl-203-1"), (exact,))
        self.assertFalse(self.window.table.exists(gui.gpu_row_id("autodl-203-1", 1)))
        for child in self.window.table.get_children("autodl-203-2"):
            self.assertIn("50%", self.window.table.set(child, "memory"))
            self.assertIn("物理机", self.window.table.set(child, "state"))

    def test_invalid_resource_values_do_not_display_zero_or_break_other_rows(self):
        from datetime import datetime
        self.window._populate([("autodl-203-1", "0/2", None), ("autodl-201-1", "0/4", None)])
        stamp = datetime.now().isoformat()
        self.window._accept_gpu_snapshot({"samples": [
            {"host": "autodl-203-1", "gpu_index": 0, "gpu_name": "坏样本", "util_pct": float("nan"),
             "memory_used_mb": 0, "memory_total_mb": 32768, "observed_at": stamp},
            {"host": "autodl-201-1", "gpu_index": 0, "gpu_name": "有效样本", "util_pct": 80,
             "memory_used_mb": 8192, "memory_total_mb": 32768, "observed_at": stamp}]})
        self.window._render_resources()
        invalid = gui.gpu_row_id("autodl-203-1", 0)
        valid = gui.gpu_row_id("autodl-201-1", 0)
        self.assertNotIn("%", self.window.table.set(invalid, "util"))
        self.assertNotIn("nan", self.window.table.set(invalid, "util").lower())
        self.assertIn("80%", self.window.table.set(valid, "util"))

    def test_selecting_gpu_uses_parent_entry_for_commands_and_saved_preferences(self):
        from datetime import datetime
        entry = "autodl-203-1"
        self.window._populate([(entry, "0/2", "可开机")])
        self.window._accept_gpu_snapshot({"samples": [dict(host=entry, gpu_index=1, util_pct=60,
            memory_used_mb=8192, memory_total_mb=32768, observed_at=datetime.now().isoformat())]})
        self.window._render_resources()
        child = gui.gpu_row_id(entry, 1)
        self.window.table.selection_set(child)
        self.assertEqual(self.window.selected_entry(), entry)
        command = self.window._command()
        self.assertEqual(command[command.index("--entry") + 1], entry)
        self.assertTrue(self.window.save())
        saved = yaml.safe_load((self.folder / ".ui/preferences.yaml").read_text(encoding="utf-8"))
        self.assertEqual(saved["entry"], entry)  # 不能把GPU行ID写成下次启动的服务器入口。

    def test_resource_refresh_preserves_selected_gpu_and_parent_expansion(self):
        from datetime import datetime
        rows = [("autodl-201-1", "0/4", None), ("autodl-203-1", "0/2", None)]
        self.window._populate(rows)
        self.window._accept_gpu_snapshot({"samples": [dict(host=entry, gpu_index=index, util_pct=20,
            memory_used_mb=8192, memory_total_mb=32768, observed_at=datetime.now().isoformat())
            for entry, _, _ in rows for index in range(2)]})
        self.window._render_resources()
        child = gui.gpu_row_id("autodl-203-1", 1)
        self.window.table.item("autodl-201-1", open=False)
        self.window.table.selection_set(child)
        self.window._populate(list(reversed(rows)))
        self.assertEqual(self.window.table.selection(), (child,))
        self.assertFalse(self.root.tk.getboolean(self.window.table.item("autodl-201-1", "open")))
        self.assertTrue(self.root.tk.getboolean(self.window.table.item("autodl-203-1", "open")))
        self.assertEqual(self.window.selected_entry(), "autodl-203-1")

    def test_removed_gpu_selection_falls_back_to_its_server(self):
        from datetime import datetime
        entry = "autodl-203-1"
        self.window._populate([(entry, "0/2", None)])
        samples = [dict(host=entry, gpu_index=index, util_pct=20, memory_used_mb=8192,
                        memory_total_mb=32768, observed_at=datetime.now().isoformat()) for index in range(2)]
        self.window._accept_gpu_snapshot({"samples": samples})
        self.window._render_resources()
        child = gui.gpu_row_id(entry, 1)
        self.window.table.selection_set(child)
        self.window._accept_gpu_snapshot({"samples": samples[:1]})
        self.window._render_resources()
        self.assertFalse(self.window.table.exists(child))
        self.assertEqual(self.window.table.selection(), (entry,))
        self.assertEqual(self.window.selected_entry(), entry)

    def test_failed_snapshot_message_keeps_last_gpu_values_with_failure_status(self):
        from datetime import datetime
        entry = "autodl-203-1"
        self.window._populate([(entry, "0/2", None)])
        self.window._accept_gpu_snapshot({"samples": [dict(host=entry, gpu_index=0, util_pct=73,
            memory_used_mb=16384, memory_total_mb=32768, observed_at=datetime.now().isoformat())]})
        self.window._render_resources()
        child = gui.gpu_row_id(entry, 0)
        before = {column: self.window.table.set(child, column) for column in ("memory", "util", "capacity", "observed")}
        self.window.events.put(("line", gui.GPU_PREFIX + json.dumps({"samples": [], "error": "HTTP采集失败"})))
        self.window._drain()
        self.assertEqual({column: self.window.table.set(child, column) for column in before}, before)
        self.assertIn("失败", self.window.table.set(child, "state"))

    def test_four_gpus_have_independent_short_cells_with_complete_percentages(self):
        from datetime import datetime
        entry = "autodl-201-1"
        self.window._populate([(entry, "0/4", None)])
        self.window._accept_gpu_snapshot({"samples": [dict(host=entry, gpu_index=index, util_pct=percent,
            memory_used_mb=32768 * percent / 100, memory_total_mb=32768,
            observed_at=datetime.now().isoformat()) for index, percent in enumerate((0, 25, 50, 100))]})
        self.window._render_resources()
        children = self.window.table.get_children(entry)
        self.assertEqual(len(children), 4)
        for child, percent in zip(children, (0, 25, 50, 100)):
            self.assertEqual(self.window.table.set(child, "memory").count("%"), 1)
            self.assertEqual(self.window.table.set(child, "util").count("%"), 1)
            self.assertIn(f"{percent}%", self.window.table.set(child, "memory"))
            self.assertIn(f"{percent}%", self.window.table.set(child, "util"))
            self.assertRegex(self.window.table.set(child, "capacity"), r"^\d+\.\d / \d+\.\d$")
        self.assertIn("GiB", self.window.table.heading("capacity", "text"))  # 单位在列头，数值列不重复堆叠文字。

    def test_resource_refresh_preserves_scroll_position(self):
        from datetime import datetime
        rows = [(f"autodl-500-{index}", "0/2", None) for index in range(1, 26)]
        self.window._populate(rows)
        self.window._accept_gpu_snapshot({"samples": [dict(host=entry, gpu_index=0, util_pct=20,
            memory_used_mb=8192, memory_total_mb=32768, observed_at=datetime.now().isoformat())
            for entry, _, _ in rows]})
        self.root.deiconify()
        self.root.update()
        self.window.table.yview_moveto(.5)
        self.root.update()
        before = self.window.table.yview()[0]
        self.assertGreater(before, .1)
        self.window._populate(rows)
        self.root.update()
        self.assertAlmostEqual(self.window.table.yview()[0], before, delta=.04)

    def test_percent_and_capacity_text_fit_their_columns_at_minimum_window_size(self):
        from datetime import datetime
        from tkinter import font as tkfont
        entry = "autodl-201-1"
        self.window._populate([(entry, "0/4", None)])
        self.window._accept_gpu_snapshot({"samples": [dict(host=entry, gpu_index=index, util_pct=percent,
            memory_used_mb=49152 * percent / 100, memory_total_mb=49152,
            observed_at=datetime.now().isoformat()) for index, percent in enumerate((0, 25, 50, 100))]})
        self.root.geometry("820x660")
        self.root.deiconify()
        self.root.update()
        table = self.window.table
        style_name = table.cget("style") or "Treeview"
        for child in table.get_children(entry):
            tag = table.item(child, "tags")[0]
            font = tkfont.Font(root=self.root, font=table.tag_configure(tag, "font")
                               or gui.ttk.Style(self.root).lookup(style_name, "font"))
            for column in ("memory", "util", "capacity"):
                self.assertLessEqual(font.measure(table.set(child, column)) + 12, table.column(column, "width"),
                                     f"{column}的实测文字宽度超过列宽，会造成截断")

    def test_initial_layout_shows_resource_table_and_realtime_output(self):
        self.root.deiconify()
        self.root.update()
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (1000, 800))
        self.assertGreater(self.window.table.winfo_width(), 800)
        self.assertGreater(self.window.table.winfo_height(), 175)
        self.assertGreaterEqual(self.window.log.winfo_height(), 100)
        self.assertGreater(self.window.log.winfo_rooty(), self.window.table.winfo_rooty())
        self.assertEqual(self.window.table.cget("columns"), ("slots", "memory", "util", "capacity", "observed", "state"))
        self.assertEqual(tuple(str(part) for part in self.window.table.cget("show")), ("tree", "headings"))
        for removed in ("gpu_panel", "training_pane"):
            self.assertFalse(hasattr(self.window, removed))

    def test_normal_mode_defaults_to_monitor_only_and_debug_stays_readonly(self):
        self.assertFalse(self.window.live.get())
        self.assertFalse(self.window.convert_no_gpu.get())
        self.window.power_eligibility[self.window.selected_entry()] = "可开机"
        self.assertIn("--monitor-only", self.window._command())
        self.window.settings_window.live_check.invoke()
        self.assertTrue(self.window.live.get())
        self.assertIn("--live", self.window._command())
        self.window.convert_no_gpu.set(True)
        self.window.settings_window.live_check.invoke()
        self.assertFalse(self.window.live.get())
        self.assertFalse(self.window.convert_no_gpu.get())
        self.assertIn("--monitor-only", self.window._command())

    def test_saved_power_options_and_interval_survive_restart(self):
        for live, convert in ((False, False), (True, False), (True, True)):
            with self.subTest(live=live, convert=convert):
                self.window.power_eligibility[self.window.selected_entry()] = "可开机"
                self.window.live.set(live)
                self.window.convert_no_gpu.set(convert)
                self.window.poll.set("120")
                self.window.user.set("保存用户名")
                self.assertTrue(self.window.save())
                saved = yaml.safe_load((self.folder / ".ui/preferences.yaml").read_text(encoding="utf-8"))
                self.assertEqual((saved["live"], saved["convert_no_gpu"]), (live, convert))
                self.destroy_root()
                self.root = tk.Tk()
                self.root.withdraw()
                self.window = gui.WatcherWindow(self.root)
                self.window.open_settings("monitor").withdraw()
                self.assertEqual((self.window.live.get(), self.window.convert_no_gpu.get()), (live, convert))
                self.assertEqual(self.window.poll.get(), "120")
                self.assertEqual(self.window.user.get(), "保存用户名")
        self.destroy_root()
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = gui.WatcherWindow(self.root, debug=True)
        self.assertFalse(self.window.live.get())
        self.assertFalse(self.window.convert_no_gpu.get())
        self.assertIn("--monitor-only", self.window._command())

    def test_checked_indicator_is_tick_and_errors_use_current_status(self):
        self.assertEqual(self.window.settings_window.live_check["style"], "Watcher.TCheckbutton")
        style = gui.ttk.Style(self.root)
        self.assertIn("Watcher.indicator", style.element_names())
        unchecked, checked = self.window.check_images
        self.assertEqual(unchecked.get(5, 9), (255, 255, 255))
        self.assertEqual(checked.get(5, 9), (36, 92, 196))
        self.assertEqual(checked.get(5, 5), (255, 255, 255))  # 左上角无斜线：不是原先的X图形。
        self.assertEqual(checked.get(12, 6), (36, 92, 196))
        self.window.events.put(("line", "Telemetry容错：单次超时20秒；瞬时失败重试\n"))
        self.window.events.put(("line", "本人 GPU 占用 有（本轮弹窗）\n"))
        self.window.events.put(("line", "连接断开，采集失败\n"))
        self.window._drain()
        self.assertEqual(self.window.status.get(), "连接断开，采集失败")
        self.assertEqual(str(self.window.status_label.cget("foreground")), "#b42318")
        self.assertIn("连接断开，采集失败", self.window.log.get("1.0", "end"))

    def test_background_output_stop_and_restart(self):
        code = ("import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                "print('WATCHER_STATUS test heartbeat', flush=True)\n"
                "while not p.exists(): time.sleep(0.02)\n")
        for _ in range(2):
            self.window.stop_file.unlink(missing_ok=True)
            self.window._launch([sys.executable, "-u", "-c", code, str(self.window.stop_file)], "monitor")
            self.pump(lambda: self.window.status.get() == "test heartbeat")
            self.assertEqual(str(self.window.start_button["state"]), "disabled")
            self.window.stop()
            self.pump(lambda: self.window.process is None)
            self.assertEqual(str(self.window.start_button["state"]), "normal")


if __name__ == "__main__":
    unittest.main()
