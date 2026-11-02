"""隔离个人资料检查主题持久化、已开/新开窗口和动态状态；不启动业务。"""
from pathlib import Path
import shutil
import tempfile
import tkinter as tk
from tkinter import ttk
from unittest.mock import Mock, patch
import unittest

from autodl_watcher import gui
from autodl_watcher.theme import PALETTES, ThemeManager, Label, card_colors
from autodl_watcher.training_config import TrainingServer
from autodl_watcher.yaml_settings import read_settings, write_settings
from autodl_watcher.network_monitor import NetworkSnapshot


class ThemeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        shutil.copyfile(Path(__file__).parent / "fixtures/monitor.yaml", self.folder / "config.yaml")
        self.location = patch.object(gui, "ROOT", self.folder)
        self.refresh = patch.object(gui.WatcherWindow, "refresh")
        self.location.start()
        self.refresh.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = gui.WatcherWindow(self.root)

    def tearDown(self):
        for child in list(self.root.winfo_children()):
            if isinstance(child, tk.Toplevel):
                child.destroy()  # 子窗口先取消自己的定时器，避免跨解释器遗留回调。
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.location.stop()
        self.refresh.stop()
        self.temp.cleanup()

    def test_choice_saves_only_theme_restores_and_preserves_other_preferences(self):
        path = self.folder / ".ui/preferences.yaml"
        preserved = {"poll": "120", "live": False, "telemetry": {"enabled": True}, "custom": "keep", "ui": {"scale": 1.2}}
        write_settings(path, preserved)
        dialog = self.window.open_settings()
        dialog.theme_choice.set("浅色")
        dialog.theme_combo.event_generate("<<ComboboxSelected>>")
        self.root.update()
        actual = read_settings(path)
        self.assertEqual(actual, dict(preserved, ui={"scale": 1.2, "theme": "light"}))
        self.assertEqual(self.window.theme.name, "light")
        restored = ThemeManager(self.root, path)
        self.assertEqual(restored.name, "light")
        self.window.theme.save("dark")
        self.assertEqual(read_settings(path)["ui"]["theme"], "dark")

    def test_write_failure_keeps_old_theme_and_reports_reason(self):
        dialog = self.window.open_settings()
        with patch("autodl_watcher.theme.write_settings", side_effect=OSError("只读文件")):
            dialog.theme_choice.set("浅色")
            dialog.theme_combo.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.window.theme.name, "dark")
        self.assertEqual(dialog.theme_choice.get(), "深色")
        self.assertIn("只读文件", dialog.theme_status.cget("text"))

    def test_live_switch_updates_fields_logs_status_and_new_windows(self):
        dialog = self.window.open_settings("ai")
        self.window.network_monitor = Mock(poll=Mock(return_value=NetworkSnapshot(1, (), (), 0)))
        panel = self.window.open_network()
        before = self.window.log.get("1.0", "end")
        for name in ("light", "dark", "light"):
            with self.subTest(theme=name):
                self.window.theme.apply(name)
                self.window.status_label.configure(foreground="#b42318", text="模拟连接错误")
                self.assertEqual(str(self.window.status_label.cget("foreground")), PALETTES[name]["error"])
                self.assertEqual(panel.detail.cget("background"), PALETTES[name]["field"])
                self.assertEqual(self.window.log.cget("background"), PALETTES[name]["output"])
                self.assertEqual(self.window.log.get("1.0", "end"), before)
                dropdown = dialog.theme_combo.tk.call("ttk::combobox::PopdownWindow", str(dialog.theme_combo))
                self.assertEqual(dialog.tk.call(str(dropdown) + ".f.l", "cget", "-background"), PALETTES[name]["field"])
                self.assertIsInstance(self.window.log.vbar, ttk.Scrollbar)
        later = tk.Toplevel(self.root)
        note = Label(later, text="新窗口警告", foreground="#8a5b11")
        note.pack()
        text = tk.Text(later)
        text.pack()
        self.root.update()
        self.assertEqual(text.cget("background"), PALETTES["light"]["field"])
        self.assertEqual(str(note.cget("foreground")), PALETTES["light"]["warning"])

    def test_training_split_grows_ssh_and_theme_keeps_card_state(self):
        self.window.open_settings("servers").withdraw()
        server = TrainingServer(id="demo", name="示例服务器", ssh_alias="example", pinned=True)
        self.window.save_server(server)
        pane = self.window.external_panes["demo"][2]
        self.window.notebook.select(self.window.external_panes["demo"][0])
        self.root.deiconify()
        self.root.update()
        self.assertGreaterEqual(pane.gpu_log.winfo_height(), 180)
        self.assertEqual(len(pane.split.panes()), 2)
        frame = pane._card("error")
        pane._label(frame, "OOM错误证据", color="#ff9ca8")
        for name in ("light", "dark"):
            self.window.theme.apply(name)
            self.assertEqual(frame.cget("background"), card_colors(pane, "error")[0])
            self.assertEqual(frame.winfo_children()[0].cget("foreground"), PALETTES[name]["error"])

    def test_theme_text_and_semantic_colors_have_readable_contrast(self):
        def luminance(value):
            numbers = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in numbers]
            return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
        for name, palette in PALETTES.items():
            for role in ("fg", "muted", "accent", "success", "warning", "error"):
                a, b = sorted((luminance(palette["bg"]), luminance(palette[role])))
                self.assertGreaterEqual((b + .05) / (a + .05), 4.5, (name, role))


if __name__ == "__main__":
    unittest.main()
