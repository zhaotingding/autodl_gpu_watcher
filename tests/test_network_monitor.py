"""连接归属和历史只使用合成快照，不建立网络或读取个人配置。"""
from unittest import TestCase
from unittest.mock import Mock

from autodl_watcher.network_monitor import NetworkMonitor, safe_target
from autodl_watcher.network_system import ProcessInfo, SocketInfo, SystemSnapshot


def process(pid, parent, name="python.exe", born=10):
    return ProcessInfo(pid, parent, name, born)


def connection(pid, port=443, state="ESTABLISHED", local_port=50000):
    return SocketInfo(pid, "TCP", "127.0.0.1", local_port, "192.0.2.1", port, state)


def snapshot(processes, sockets=(), now=100, errors=()):
    return SystemSnapshot(tuple(processes), tuple(sockets), tuple(errors), now)


class NetworkMonitorTest(TestCase):
    def test_logical_ssh_target_and_configured_http_urls_are_not_conflated(self):
        reader = Mock(return_value=snapshot([process(1, 0), process(2, 1, "ssh.exe", 11)],
                                           [connection(2, 41942), connection(1)], 100))
        monitor = NetworkMonitor(1, reader=reader)
        monitor.set_targets([{"pid": 2, "kind": "SSH", "purpose": "训练SSH · 203-1", "target": "ssh://server203"},
            {"pid": 0, "kind": "HTTPS", "purpose": "GPU采集站", "target": "https://user:password@example.invalid/api/latest?token=secret#fragment"}])
        result = monitor.poll()
        ssh = next(row for row in result.entries if row.pid == 2)
        self.assertEqual(ssh.logical_target, "ssh://server203")
        self.assertIn("203-1", ssh.purpose)
        self.assertEqual(next(row for row in result.entries if row.pid == 1).logical_target, "")
        self.assertEqual(result.targets[1].target, "https://example.invalid/api/latest")
        self.assertEqual(safe_target("https://[::1]:8443/api?key=secret"), "https://[::1]:8443/api")

    def test_only_current_instance_tree_is_included_not_other_python_or_ssh(self):
        processes = [process(3, 2, "ssh.exe", 12), process(1, 0), process(2, 1, born=11),
                     process(5, 0, "ssh.exe"), process(6, 5, "python.exe", 13)]
        reader = Mock(return_value=snapshot(processes, [connection(3, 41942), connection(5, 22), connection(6)]))
        result = NetworkMonitor(1, reader=reader).poll()
        self.assertEqual([row.pid for row in result.entries], [3])
        self.assertEqual(result.entries[0].process_name, "ssh.exe")
        self.assertFalse(result.errors)
        reader.assert_called_once_with()

    def test_process_identity_prevents_pid_reuse_and_impossible_ancestry(self):
        reader = Mock(side_effect=[
            snapshot([process(1, 0), process(2, 1, "ssh.exe", 11)], [connection(2)], 100),
            snapshot([process(1, 0), process(2, 999, "other.exe", 20)], [connection(2)], 103)])
        monitor = NetworkMonitor(1, reader=reader)
        before = monitor.poll().entries[0]
        after = monitor.poll().entries[0]
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.process_started_at, 11)
        self.assertEqual(after.closed_at, 103)
        reader.return_value = snapshot([process(1, 0, born=20), process(7, 1, born=15)], [connection(7)])
        reader.side_effect = None
        self.assertFalse(NetworkMonitor(1, reader=reader).poll().entries)

    def test_previously_observed_child_is_retained_after_parent_exits(self):
        child = process(3, 2, "msedge.exe", 12)
        reader = Mock(side_effect=[
            snapshot([process(1, 0), process(2, 1, born=11), child], [connection(3)], 100),
            snapshot([process(1, 0), child], [connection(3)], 103)])
        monitor = NetworkMonitor(1, reader=reader)
        original = monitor.poll().entries[0]
        current = monitor.poll().entries[0]
        self.assertEqual(current.id, original.id)
        self.assertIsNone(current.closed_at)

    def test_incomplete_snapshot_and_exception_do_not_invent_closed_connections(self):
        reader = Mock(side_effect=[
            snapshot([process(1, 0)], [connection(1)], 100),
            snapshot([process(1, 0)], (), 103, ["TCP/IPv4：拒绝访问"]),
            OSError("native table failure"), snapshot([process(1, 0)], (), 109)])
        monitor = NetworkMonitor(1, reader=reader)
        monitor.poll()
        for _ in range(2):
            unknown = monitor.poll()
            self.assertTrue(unknown.errors)
            self.assertEqual(unknown.entries[0].state, "UNKNOWN")
            self.assertEqual(unknown.entries[0].last_seen, 100)
            self.assertIsNone(unknown.entries[0].closed_at)
        self.assertEqual(monitor.poll().entries[0].closed_at, 109)

    def test_missing_or_reused_root_cannot_claim_empty_success(self):
        reader = Mock(side_effect=[snapshot([process(1, 0)], [connection(1)], 100),
            snapshot([], [], 103), snapshot([process(1, 0, born=20)], [connection(1)], 106)])
        monitor = NetworkMonitor(1, reader=reader)
        monitor.poll()
        for _ in range(2):
            result = monitor.poll()
            self.assertTrue(result.errors)
            self.assertIsNone(result.entries[0].closed_at)

    def test_unreadable_child_identity_is_reported_without_adopting_its_connections(self):
        reader = Mock(return_value=snapshot([process(1, 0), process(2, 1, "ssh.exe", 0)], [connection(2)]))
        result = NetworkMonitor(1, reader=reader).poll()
        self.assertFalse(result.entries)
        self.assertTrue(result.errors)

    def test_process_created_during_socket_read_cannot_own_old_pid_rows(self):
        reader = Mock(side_effect=[
            snapshot([process(1, 0), process(2, 1, "ssh.exe", 101)], [connection(2)], 100),
            snapshot([process(1, 0), process(2, 1, "ssh.exe", 101)], [connection(2)], 103)])
        monitor = NetworkMonitor(1, reader=reader)
        uncertain = monitor.poll()
        self.assertTrue(uncertain.errors)
        self.assertFalse(uncertain.entries)
        confirmed = monitor.poll()
        self.assertFalse(confirmed.errors)
        self.assertEqual(confirmed.entries[0].pid, 2)

    def test_history_is_bounded_and_reappearing_tuple_creates_new_observation(self):
        reader = Mock(side_effect=[snapshot([process(1, 0)], [connection(1)], 100),
            snapshot([process(1, 0)], [], 103), snapshot([process(1, 0)], [connection(1)], 106),
            snapshot([process(1, 0)], [], 109)])
        monitor = NetworkMonitor(1, reader=reader, history_limit=1)
        original = monitor.poll().entries[0]
        monitor.poll()
        reopened = monitor.poll().entries[0]
        self.assertNotEqual(reopened.id, original.id)
        self.assertEqual(reopened.first_seen, 106)
        ended = monitor.poll().entries
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].id, reopened.id)

    def test_state_change_retains_first_observed_time_and_udp_has_no_invented_peer(self):
        udp = SocketInfo(1, "UDP", "::", 5353, "", 0, "BOUND")
        reader = Mock(side_effect=[snapshot([process(1, 0)], [connection(1, state="SYN_SENT"), udp], 100),
            snapshot([process(1, 0)], [connection(1), udp], 103)])
        monitor = NetworkMonitor(1, reader=reader)
        monitor.poll()
        entries = monitor.poll().entries
        self.assertEqual([row.first_seen for row in entries], [100, 100])
        self.assertEqual(next(row for row in entries if row.protocol == "TCP").state, "ESTABLISHED")
        self.assertEqual(next(row for row in entries if row.protocol == "UDP").remote_address, "")
