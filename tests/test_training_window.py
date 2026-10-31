from training_helpers import wait_training
import json
import queue
import shutil
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from autodl_watcher import gui
from autodl_watcher.training_config import TrainingServer, save_servers
from autodl_watcher.training_tracker import TrainingTracker
from autodl_watcher.training_events import parse_progress
from autodl_watcher.training_ui import COLORS
from test_training import snapshot, LOG


def descendants(widget):
    return [child for item in widget.winfo_children() for child in [item, *descendants(item)]]


class TrainingWindowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        shutil.copyfile(Path(__file__).parent / "fixtures/monitor.yaml", self.folder / "config.yaml")
        self.servers = [TrainingServer("s", "203-1", "server", entry="autodl-203-1", pinned=True), TrainingServer("external", "4090", "other", pinned=True)]
        save_servers(self.folder / ".ui/servers.yaml", self.servers)
        self.location = patch.object(gui, "ROOT", self.folder)
        self.location.start()
        self.refresh = patch.object(gui.WatcherWindow, "refresh")
        self.refresh.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = gui.WatcherWindow(self.root, debug=True)
        self.window.table.selection_set("autodl-203-1")
        self.pane = self.window.external_panes["s"][2]  # 绑定入口也有独立训练页，不再放进平台资源页。

    def tearDown(self):
        self.window.training.stop_all()
        wait_training(self.window.training)
        try:
            for timer in self.root.tk.call("after", "info"):
                self.root.after_cancel(timer)
            self.root.destroy()
        except tk.TclError:
            pass
        self.location.stop()
        self.refresh.stop()
        self.temp.cleanup()

    def texts(self, pane):
        return [str(item.cget("text")) for item in descendants(pane) if isinstance(item, (tk.Label, gui.ttk.Label, gui.ttk.Button))]

    def test_tabs_and_selection_do_not_start_network_or_power(self):
        self.assertEqual([self.window.notebook.tab(tab, "text") for tab in self.window.notebook.tabs()], ["AutoDL 平台", "4090", "203-1"])
        self.assertEqual(self.window.title_text.get(), "AutoDL 平台算力")
        self.assertEqual(self.pane.server_id, "s")
        self.assertFalse(hasattr(self.window, "training_pane"))
        self.window.notebook.select(self.window.external_panes["external"][0])
        self.assertFalse(any(state["active"] for state in self.window.training.states.values()))
        self.assertFalse(any("开机" in text for text in self.texts(self.window.external_panes["external"][2])))
        self.window.table.selection_set("autodl-203-2")
        self.window._render_resources()
        self.assertEqual(self.window.title_text.get(), "AutoDL 平台算力")
        self.assertEqual(self.pane.server_id, "s")  # 资源行选择不会切换训练页的服务器。

    def test_new_unpinned_server_is_available_in_settings_without_creating_a_tab(self):
        from unittest.mock import Mock
        server = TrainingServer("new", "任意新增服务器", "new-host")
        self.window.open_settings("servers")
        with patch.object(self.window.training, "start") as start:
            self.window.save_server(server)
        self.assertFalse(server.pinned)
        self.assertNotIn(server.id, self.window.external_panes)
        self.assertTrue(self.window.settings_window.servers.exists(server.id))
        self.assertEqual(self.window.settings_window.servers.item(server.id, "text"), server.name)
        start.assert_not_called()

    def test_pin_and_unpin_preserve_active_connection_and_snapshot(self):
        from dataclasses import replace
        self.window.open_settings("servers")
        state = self.window.training.states["s"]
        state.update(active=True, due=float("inf"), snapshot=state["tracker"].update(snapshot()))
        connection, current = state["connection"], state["snapshot"]
        with patch.object(self.window.training, "register") as register, patch.object(self.window.training, "stop") as stop:
            self.window.save_server(replace(self.servers[0], pinned=False))
            self.assertEqual(self.window.notebook.tab(self.window.external_panes["s"][0], "state"), "hidden")
            self.assertTrue(state["active"])
            self.assertIs(state["connection"], connection)
            self.assertIs(state["snapshot"], current)
            self.assertIsNone(state["pending"])
            self.window.save_server(replace(self.servers[0], pinned=True))
            self.assertEqual(self.window.notebook.tab(self.window.external_panes["s"][0], "state"), "normal")
            register.assert_not_called()
            stop.assert_not_called()

    def test_unpinned_server_can_be_opened_temporarily_and_hidden_without_starting(self):
        from autodl_watcher.training_config import load_servers
        server = TrainingServer("temporary", "临时查看", "temporary-host")
        self.window.open_settings("servers")
        self.window.save_server(server)
        with patch.object(self.window.training, "start") as start, patch.object(self.window.training, "stop") as stop:
            self.window.open_training(server.id)
            page, _, pane = self.window.external_panes[server.id]
            self.assertEqual(self.window.notebook.select(), str(page))
            self.assertEqual(pane.server_id, server.id)
            self.assertFalse(next(item for item in load_servers(self.window.server_file) if item.id == server.id).pinned)
            self.window.hide_training(server.id)
            self.assertEqual(self.window.notebook.tab(page, "state"), "hidden")
            self.window.open_training(server.id)
            self.assertIs(self.window.external_panes[server.id][2], pane)
            start.assert_not_called()
            stop.assert_not_called()

    def test_server_form_pin_click_saves_immediately_for_arbitrary_server(self):
        from autodl_watcher.training_config import load_servers
        self.window.edit_server("s")
        dialog = self.window.settings_window
        pin = next(item for item in descendants(dialog.server_form)
                   if isinstance(item, gui.ttk.Checkbutton) and item.cget("text").startswith("固定到顶部标签"))
        pin.invoke()
        self.assertFalse(load_servers(self.window.server_file)[0].pinned)
        self.assertEqual(self.window.notebook.tab(self.window.external_panes["s"][0], "state"), "hidden")
        self.assertTrue(any("已取消固定" in text for text in self.texts(dialog)))

    def test_pin_click_does_not_submit_unsaved_connection_fields_or_replace_pending(self):
        from dataclasses import replace
        from autodl_watcher.training_config import load_servers
        state = self.window.training.states["s"]
        pending = replace(state["server"], ssh_alias="pending-host")
        state.update(active=True, due=float("inf"), pending=pending)
        connection = state["connection"]
        self.window.edit_server("s")
        dialog = self.window.settings_window
        fields = [item for item in descendants(dialog.server_form) if isinstance(item, gui.ttk.Entry)]
        fields[1].delete(0, "end"); fields[1].insert(0, "unsaved-new-host")
        pin = next(item for item in descendants(dialog.server_form)
                   if isinstance(item, gui.ttk.Checkbutton) and item.cget("text").startswith("固定到顶部标签"))
        with patch.object(self.window.training, "register") as register, patch.object(self.window.training, "stop") as stop:
            pin.invoke()
        saved = load_servers(self.window.server_file)[0]
        self.assertFalse(saved.pinned)
        self.assertEqual(saved.ssh_alias, "server")
        self.assertEqual(fields[1].get(), "unsaved-new-host")
        self.assertTrue(state["active"])
        self.assertIs(state["connection"], connection)
        self.assertIs(state["pending"], pending)
        register.assert_not_called()
        stop.assert_not_called()

    def test_failed_immediate_pin_save_restores_checkbox_and_saved_state(self):
        from autodl_watcher.training_config import load_servers
        self.window.edit_server("s")
        dialog = self.window.settings_window
        pin = next(item for item in descendants(dialog.server_form)
                   if isinstance(item, gui.ttk.Checkbutton) and item.cget("text").startswith("固定到顶部标签"))
        with patch.object(gui, "save_servers", side_effect=OSError("模拟无法保存固定设置")):
            pin.invoke()
        self.assertTrue(pin.instate(("selected",)))
        self.assertTrue(load_servers(self.window.server_file)[0].pinned)
        self.assertEqual(self.window.notebook.tab(self.window.external_panes["s"][0], "state"), "normal")
        self.assertTrue(any("固定设置保存失败" in text for text in self.texts(dialog)))

    def test_new_server_pin_is_only_persisted_after_connection_save(self):
        from autodl_watcher.training_config import load_servers
        before = self.window.server_file.read_bytes()
        self.window.edit_server()
        dialog = self.window.settings_window
        pin = next(item for item in descendants(dialog.server_form)
                   if isinstance(item, gui.ttk.Checkbutton) and item.cget("text").startswith("固定到顶部标签"))
        pin.invoke()
        self.assertEqual(self.window.server_file.read_bytes(), before)
        self.assertTrue(any("请先保存新服务器连接" in text for text in self.texts(dialog)))
        fields = [item for item in descendants(dialog.server_form) if isinstance(item, gui.ttk.Entry)]
        fields[1].insert(0, "new-host")
        next(item for item in descendants(dialog.server_form)
             if isinstance(item, gui.ttk.Button) and item.cget("text") == "保存连接").invoke()
        saved = next(item for item in load_servers(self.window.server_file) if item.ssh_alias == "new-host")
        self.assertTrue(saved.pinned)
        self.assertEqual(self.window.notebook.tab(self.window.external_panes[saved.id][0], "state"), "normal")

    def test_card_states_preserve_metrics_and_disconnection_is_not_idle(self):
        pane = self.pane
        state = self.window.training.states["s"]
        state["active"] = True
        for text, alive, level, label in [(LOG, True, "success", "正在测试"),
                                         (LOG + "CUDA out of memory", False, "error", "异常中止"),
                                         (LOG + "Stop Training on best Testing metric", False, "idle", "已完成")]:
            with self.subTest(label=label):
                state["snapshot"] = TrainingTracker(self.servers[0]).update(snapshot(text, alive, now=time.time()))
                state["snapshot"]["cards"][0]["advanced_at"] = time.time()
                if level == "success":
                    state["snapshot"]["cards"][0].update(level="success", status="正在测试")
                pane.render()
                self.assertTrue(any(label in text for text in self.texts(pane)))
                card = pane.content.winfo_children()[-1]
                self.assertEqual(card.cget("background"), COLORS[level][0])
                self.assertTrue(any("AUC 0.8800" in value for value in self.texts(pane)))
                if level == "idle":
                    bar = next(item for item in descendants(card) if isinstance(item, gui.ttk.Progressbar))
                    self.assertEqual(float(bar["value"]), float(bar["maximum"]))
        self.window.training.receive(("s", state["generation"], None, "SSH连接失败"))
        pane.render()
        self.assertIn("SSH连接失败", pane.summary.cget("text"))
        self.assertEqual(pane.content.winfo_children()[-1].cget("background"), COLORS["warning"][0])
        self.assertNotIn("无匹配训练进程", self.texts(pane))
        state["snapshot"] = TrainingTracker(self.servers[0]).update(dict(snapshot(), tasks=[]))
        pane.render()
        self.assertIn("无匹配训练进程", self.texts(pane))

    def test_platform_start_and_stop_do_not_start_or_stop_training_pages(self):
        from unittest.mock import Mock
        state = self.window.training.states["s"]
        state.update(active=True, due=float("inf"))
        def launched(command, job):
            self.window.process, self.window.job = Mock(), job
        with patch.object(self.window, "_launch", side_effect=launched) as launch, \
             patch.object(self.window.training, "start") as start_training, \
             patch.object(self.window.training, "stop") as stop_training:
            self.window.start()
            launch.assert_called_once()
            self.window.stop()
            self.assertTrue(self.window.stop_file.exists())
            self.assertTrue(state["active"])
            start_training.assert_not_called()
            stop_training.assert_not_called()
        self.window.process = None  # 此用例未创建真实平台进程，避免收尾等待一个 Mock。

    def test_stop_one_page_does_not_stop_another_and_close_waits_for_read(self):
        for state in self.window.training.states.values():
            state.update(active=True, due=float("inf"))
        self.pane.stop()
        self.assertFalse(self.window.training.states["s"]["active"])
        self.assertTrue(self.window.training.states["external"]["active"])
        state = self.window.training.states["external"]
        state["busy"] = True
        generation = state["generation"]
        self.window.close()
        self.assertTrue(self.window.closing)
        self.assertTrue(self.root.winfo_exists())
        self.window.training.receive(("external", generation, snapshot(), ""));wait_training(self.window.training)
        self.assertFalse(state["busy"])
        self.assertIsNone(state["snapshot"])
        self.assertFalse(any(state["active"] for state in self.window.training.states.values()))

    def test_busy_and_other_ai_revision_do_not_rebuild_visible_cards(self):
        from types import SimpleNamespace
        pane = self.pane
        service = self.window.training
        pane.service = SimpleNamespace(status=service.status, snapshot=service.snapshot, server=service.server, ai=service.ai)  # 视图仅需公开查询接口。
        state = service.states["s"]
        state.update(active=True, snapshot=state["tracker"].update(snapshot(now=time.time())))
        state["connection"].samples, state["connection"].attempts = 7, 1  # 复用连接的计数必须继续呈现在摘要中。
        pane.render()
        self.assertIn("采集7次 / 连接尝试1次", pane.summary.cget("text"))
        frames = pane.content.winfo_children()
        state["busy"] = True
        self.window.training.ai.revision += 1
        pane.render()
        self.assertEqual(pane.content.winfo_children(), frames)
        self.assertEqual(str(pane.start_button.cget("state")), "disabled")
        state["busy"] = False
        self.window.training.reparse("s")
        wait_training(self.window.training)
        pane.render()
        self.assertNotEqual(pane.content.winfo_children(), frames)

    def test_black_gpu_log_and_failure_card_stay_visible_after_disconnect_and_stop(self):
        pane = self.pane
        state = self.window.training.states["s"]
        state["active"] = True
        value = snapshot(LOG + "RuntimeError: CUDA out of memory")
        value["tasks"][0]["gpu_memory_mb"] = 6144
        state["snapshot"] = state["tracker"].update(value)
        pane.render()
        self.assertTrue(any("6.00 GiB" in text for text in self.texts(pane)))
        self.assertEqual(pane.gpu_log.cget("wrap"), "none")
        self.assertIn("VRAM整卡", pane.gpu_log.get("1.0", "end"))
        first = pane.gpu_log.get("1.0", "end")
        state["busy"] = True
        pane.render()
        self.assertEqual(pane.gpu_log.get("1.0", "end"), first)  # 请求进行中不能重复追加旧快照。
        self.window.training.receive(("s", state["generation"], None, "SSH断连"))
        pane.render()
        self.assertEqual(pane.content.winfo_children()[-1].cget("background"), COLORS["error"][0])
        pane.stop()
        self.assertEqual(pane.content.winfo_children()[-1].cget("background"), COLORS["error"][0])
        self.assertIn("上次数据不作为当前状态", pane.gpu_log.get("1.0", "end"))

    def test_power_eligibility_is_independent_from_resource_columns_and_training(self):
        window = self.window
        window._populate([("autodl-203-1", "0/2", "可开机"), ("autodl-203-2", "0/2", "可开机"), ("autodl-202-2", "3/3", "无实例")])
        self.assertNotIn("training", window.table.cget("columns"))
        self.assertNotIn("target", window.table.cget("columns"))
        window.live.set(True); window.convert_no_gpu.set(True)
        window.table.selection_set("autodl-202-2")
        self.assertIn("--monitor-only", window._command())
        self.assertNotIn("--convert-no-gpu", window._command())
        window.table.selection_set("autodl-203-1")
        self.assertIn("--live", window._command())
        state = window.training.states["s"]
        state.update(active=True, snapshot=state["tracker"].update(snapshot()))
        self.pane.render()
        self.assertEqual(self.pane.server_id, "s")
        self.assertEqual(window.power_eligibility["autodl-203-1"], "可开机")

    def test_pending_config_can_be_saved_while_platform_and_training_are_running(self):
        from unittest.mock import Mock
        from autodl_watcher.training_config import load_servers
        window = self.window
        state = window.training.states["s"]
        state.update(active=True, busy=True, snapshot=state["tracker"].update(snapshot(now=time.time())))
        window.process, window.job = Mock(), "monitor"
        old = state["server"]
        window.edit_server("s")
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        fields = [child for child in descendants(dialog.server_form) if isinstance(child, gui.ttk.Entry)]
        fields[1].delete(0, "end"); fields[1].insert(0, "replacement")
        next(child for child in descendants(dialog) if isinstance(child, gui.ttk.Button) and child.cget("text") == "保存连接").invoke()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(load_servers(window.server_file)[0].ssh_alias, "replacement")
        self.assertEqual(state["server"], old)
        self.assertTrue(state["active"] and state["busy"])
        self.assertEqual(state["pending"].ssh_alias, "replacement")
        self.assertIn("下次启动训练监控生效", self.pane.summary.cget("text"))
        window.process = None; state["busy"] = False

    def test_resource_selection_does_not_edit_or_retarget_running_training(self):
        from unittest.mock import Mock
        window = self.window
        window.process = Mock()
        with patch.object(window, "edit_server") as editor:
            window.table.selection_set("autodl-203-2")
            window._render_resources()
            editor.assert_not_called()
        self.assertEqual(window.selected_entry(), "autodl-203-2")
        self.assertEqual(self.pane.server_id, "s")
        window.process = None

    def test_failed_config_write_does_not_change_running_or_saved_settings(self):
        window = self.window
        state = window.training.states["s"]
        state["active"] = True
        before = window.server_file.read_bytes()
        window.edit_server("s")
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        with patch.object(gui, "save_servers", side_effect=OSError("模拟写入失败")):
            next(child for child in descendants(dialog) if isinstance(child, gui.ttk.Button) and child.cget("text") == "保存连接").invoke()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(window.server_file.read_bytes(), before)
        self.assertIsNone(state["pending"])
        self.assertTrue(state["active"])
        self.assertTrue(any("模拟写入失败" in text for text in self.texts(dialog)))
        dialog.destroy()

    def test_card_packs_status_timestamp_and_memory_into_existing_rows(self):
        pane = self.pane
        state = self.window.training.states["s"]
        value = snapshot(now=time.time()); value["tasks"][0]["gpu_memory_mb"] = 6144
        state.update(active=True, snapshot=state["tracker"].update(value))
        state["snapshot"]["cards"][0].update(status="正在测试", level="success", advanced_at=time.time())
        pane.render()
        labels = [child.cget("text") for child in pane.content.winfo_children()[-1].winfo_children() if isinstance(child, tk.Label)]
        self.assertIn(state["snapshot"]["cards"][0]["name"], labels[0])
        self.assertIn("正在测试", labels[0]); self.assertIn("最近进展", labels[0])
        self.assertIn("PID", labels[1]); self.assertIn("用户", labels[1]); self.assertIn("GPU", labels[1]); self.assertIn("显存 6.00 GiB", labels[1])
        self.assertFalse(any(line.startswith(("最近进展", "本任务显存")) for line in labels))

    def test_structured_branch_metrics_peak_memory_and_error_color(self):
        from test_v083 import TRAIN, NOW
        pane = self.pane
        state = self.window.training.states["s"]
        state["active"] = True
        state["snapshot"] = state["tracker"].update(snapshot(TRAIN, now=NOW))
        pane.render()
        self.assertEqual(pane.content.winfo_children()[-1].cget("background"), COLORS["success"][0])
        texts = self.texts(pane)
        self.assertTrue(any("Loss/paired" in text and "Loss/rolled" in text for text in texts))
        self.assertTrue(any("PID" in text and "显存峰值" in text and "日志" in text for text in texts))
        state["snapshot"] = state["tracker"].update(snapshot(TRAIN.replace('1.0937', 'NaN'), now=NOW+1))
        pane.render()
        self.assertEqual(pane.content.winfo_children()[-1].cget("background"), COLORS["error"][0])
        self.assertTrue(any("NaN/Inf" in text for text in self.texts(pane)))

    def test_empty_match_explains_current_script_and_directory_filters(self):
        pane = self.pane
        state = self.window.training.states["s"]
        value = snapshot(); value["tasks"] = []
        state.update(active=True, snapshot=state["tracker"].update(value))
        pane.render()
        self.assertTrue(any("脚本匹配" in text and "train_*.py" in text for text in self.texts(pane)))

    def test_malformed_json_metrics_cannot_break_following_progress(self):
        text = '{"event":"train","metrics":null}\n' + '{"event":"train","epoch":3,"metrics":{"loss":0.5}}'
        result = parse_progress(text)
        self.assertEqual(result["train"]["metrics"], {"loss": .5})
        self.assertEqual(result["epoch"], 3)


if __name__ == "__main__":
    unittest.main()
