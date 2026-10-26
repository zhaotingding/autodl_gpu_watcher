"""连接窗口只使用合成快照；不启动业务监控，不读取任何个人设置。"""
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from autodl_watcher.network_ui import NetworkPanel, duration, endpoint


def record(identifier="ssh-1", **changes):
    values = dict(id=identifier, pid=121, process_name="ssh.exe", process_started_at=1700000000,
                  protocol="TCP4", local_address="127.0.0.1", local_port=50123,
                  remote_address="192.0.2.10", remote_port=22, state="ESTABLISHED",
                  first_seen=1700000100, last_seen=1700000160, closed_at=None)
    values.update(changes)
    return SimpleNamespace(**values)


def snapshot(*entries, errors=(), captured_at=1700000160):
    return SimpleNamespace(captured_at=captured_at, entries=entries, errors=errors, elapsed_seconds=0.01)


class NetworkPanelTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.panels = []
        self.releases = []

    def tearDown(self):
        for release in self.releases:
            release.set()  # 测试失败也释放合成等待线程，避免把后台悬挂带入后续用例。
        for panel in self.panels:
            if panel.winfo_exists():
                panel.destroy()
        self.root.destroy()

    def panel(self, monitor):
        panel = NetworkPanel(self.root, monitor)
        panel.withdraw()
        panel.automatic.set(False)
        self.panels.append(panel)
        return panel

    def pump(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(condition(), "窗口没有及时接收后台快照")

    def test_filter_views_and_detail_retain_observation_meaning(self):
        monitor = Mock()
        monitor.poll.return_value = snapshot(record(), record("udp", pid=122, process_name="browser.exe",
            protocol="UDP6", local_address="::", local_port=5353, remote_address="", remote_port=0, state="BOUND"),
            record("old", closed_at=1700000150))
        panel = self.panel(monitor)
        self.pump(lambda: panel._snapshot is not None)
        self.assertEqual(set(panel.table.get_children()), {"ssh-1", "udp"})
        self.assertEqual(panel.table.set("udp", "target"), "—")
        self.assertEqual(panel.table.set("ssh-1", "duration"), "00:01:00")
        panel.view.set("closed")
        panel._render()
        self.assertEqual(panel.table.get_children(), ("old",))
        panel.view.set("all")
        panel.filter_text.set("192.0.2.10")
        self.assertEqual(set(panel.table.get_children()), {"ssh-1", "old"})
        panel.table.selection_set("ssh-1")
        panel._render_detail()
        text = panel.detail.get("1.0", "end")
        self.assertIn("PID：121", text)
        self.assertIn("首次观察", text)
        self.assertIn("不证明业务已经结束", text)
        self.assertEqual(monitor.poll.call_count, 1)  # 筛选及切页不触发额外采样。

    def test_partial_failure_shows_unknown_and_preserves_old_record(self):
        monitor = Mock()
        monitor.poll.return_value = snapshot(record(state="UNKNOWN"), errors=("TCP6 读取失败",))
        panel = self.panel(monitor)
        self.pump(lambda: panel._snapshot is not None)
        self.assertIn("读取不完整", panel.summary.get())
        self.assertIn("TCP6 读取失败", panel.status.get())
        self.assertIn("未确认", panel.table.set("ssh-1", "state"))
        monitor.poll.side_effect = OSError("access denied")
        panel.refresh()
        self.pump(lambda: not panel._busy)
        self.assertIn("access denied", panel.status.get())
        self.assertTrue(panel.table.exists("ssh-1"))  # 读取失败不得伪造连接已消失。
        self.assertIn("未确认", panel.table.set("ssh-1", "state"))

    def test_first_read_failure_is_unknown_instead_of_zero_connections(self):
        panel = self.panel(Mock(poll=Mock(side_effect=OSError("access denied"))))
        self.pump(lambda: not panel._busy)
        self.assertIn("状态未知", panel.summary.get())
        self.assertIn("access denied", panel.status.get())
        self.assertEqual(panel.table.get_children(), ())

    def test_empty_partial_snapshot_does_not_claim_zero_connections(self):
        panel = self.panel(Mock(poll=Mock(return_value=snapshot(errors=("系统表不可读",)))))
        self.pump(lambda: panel._snapshot is not None)
        self.assertIn("状态未知", panel.summary.get())
        self.assertNotIn("当前记录 0", panel.summary.get())

    def test_close_and_reopen_during_read_does_not_overlap_and_discards_old_result(self):
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)
        calls = []

        def read():
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                started.set()
                release.wait(3)
                return snapshot(record("old-result"))
            return snapshot(record("new-result"))

        panel = self.panel(SimpleNamespace(poll=read))
        self.assertTrue(started.wait(1))
        self.assertFalse(panel.refresh())
        panel.close()
        self.assertIsNone(panel._drain_after)
        self.assertIsNone(panel._refresh_after)
        panel.show()
        self.root.update()
        self.assertEqual(calls, [1])
        release.set()
        self.pump(lambda: panel.table.exists("new-result"))
        self.assertFalse(panel.table.exists("old-result"))
        self.assertEqual(calls, [1, 2])

    def test_close_cancels_scheduled_refresh_without_stopping_business(self):
        monitor = Mock()
        monitor.poll.return_value = snapshot()
        panel = self.panel(monitor)
        panel.automatic.set(True)
        self.pump(lambda: panel._refresh_after is not None)
        panel.close()
        self.assertIsNone(panel._drain_after)
        self.assertIsNone(panel._refresh_after)
        self.assertFalse(panel.refresh())
        self.assertEqual(monitor.poll.call_count, 1)
        self.assertEqual(set(name for name, _, _ in monitor.mock_calls), {"poll"})

    def test_sorting_uses_numeric_observation_duration(self):
        monitor = Mock()
        monitor.poll.return_value = snapshot(record("short", first_seen=1700000150),
                                             record("long", first_seen=1699900000))
        panel = self.panel(monitor)
        self.pump(lambda: panel._snapshot is not None)
        panel._sort("duration")
        self.assertEqual(panel.table.get_children(), ("short", "long"))
        panel._sort("duration")
        self.assertEqual(panel.table.get_children(), ("long", "short"))

    def test_unknown_duration_freezes_at_last_seen_and_sorts_consistently(self):
        monitor = Mock()
        monitor.poll.return_value = snapshot(record("unknown", state="UNKNOWN", last_seen=1700000110),
                                             record("known", state="SYN_RCVD"), captured_at=1700001000)
        panel = self.panel(monitor)
        self.pump(lambda: panel._snapshot is not None)
        self.assertEqual(panel.table.set("unknown", "duration"), "00:00:10")
        self.assertEqual(panel.table.set("known", "state"), "等待握手")
        panel._sort("duration")
        self.assertEqual(panel.table.get_children(), ("unknown", "known"))

    def test_endpoint_and_duration_do_not_invent_unavailable_values(self):
        self.assertEqual(endpoint("2001:db8::1", 443), "[2001:db8::1]:443")
        self.assertEqual(endpoint("", 0), "—")
        self.assertEqual(duration(-1), "00:00:00")
        self.assertEqual(duration(90061), "1天 01:01:01")


if __name__ == "__main__":
    unittest.main()
