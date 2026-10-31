"""TCP上层归因只用合成请求及系统表，不探测网址或读取个人配置。"""
import unittest
from unittest.mock import Mock

from autodl_watcher.network_attribution import parse_observation
from autodl_watcher.network_monitor import NetworkMonitor
from autodl_watcher.network_system import ProcessInfo, SocketInfo, SystemSnapshot


def process(pid, parent, name="python.exe", born=10):
    return ProcessInfo(pid, parent, name, born)


def connection(pid, local=50000, remote="198.18.0.249", port=443):
    return SocketInfo(pid, "TCP", "198.18.0.1", local, remote, port, "ESTABLISHED")


def snapshot(processes, sockets, now=100):
    return SystemSnapshot(tuple(processes), tuple(sockets), (), now)


def observation(**changes):
    return dict(pid=2, observed_at=99, url="https://user:secret@gpu.example.invalid/api/latest?token=private#fragment",
                purpose="GPU采集站", evidence="requests", local_address="198.18.0.1", local_port=50000,
                remote_address="198.18.0.249", remote_port=443) | changes


class NetworkAttributionTest(unittest.TestCase):
    def monitor(self, sockets=(), processes=None):
        reader = Mock(return_value=snapshot(processes or [process(1, 0), process(2, 1, born=11)], sockets))
        monitor = NetworkMonitor(1, reader=reader)
        return monitor, reader

    def test_request_parser_redacts_secret_and_uses_actual_url_scheme(self):
        parsed = parse_observation(observation(application_protocol="SSH"))
        self.assertEqual(parsed.application_protocol, "HTTPS")
        self.assertEqual(parsed.url, "https://gpu.example.invalid/api/latest")
        self.assertNotIn("private", repr(parsed))
        self.assertNotIn("secret", repr(parsed))
        mapped = parse_observation(observation(remote_address="::ffff:192.0.2.10"))
        self.assertEqual(mapped.remote_address, "192.0.2.10")
        for values in (observation(pid=0), observation(observed_at=float("nan")),
                       observation(url="file:///etc/passwd"), observation(remote_port=70000)):
            self.assertIsNone(parse_observation(values))

    def test_requests_match_pid_and_both_tcp_endpoints_instead_of_port_443_guess(self):
        monitor, reader = self.monitor([connection(2), connection(2, local=50001), connection(1)])
        self.assertTrue(monitor.add_observation(observation()))
        result = monitor.poll()
        exact = next(row for row in result.entries if row.pid == 2 and row.local_port == 50000)
        self.assertEqual(exact.application_protocol, "HTTPS")
        self.assertEqual(exact.request_urls, ("https://gpu.example.invalid/api/latest",))
        self.assertIn("源/目标IP和端口", exact.attribution)
        self.assertIn("198.18/15", exact.network_hint)
        self.assertEqual(exact.classification, "remote")
        self.assertTrue(all(not row.application_protocol for row in result.entries if row.id != exact.id))
        reader.assert_called_once_with()  # 归因不额外DNS查询或发HTTP。

    def test_browser_remote_scope_is_explicit_candidate_and_does_not_cross_process_tree(self):
        processes = [process(1, 0), process(2, 1, "node.exe", 11), process(3, 2, "msedge.exe", 12)]
        monitor, _ = self.monitor([connection(3), connection(1), connection(3, local=50001, remote="192.0.2.20")], processes)
        monitor.add_observation(observation(evidence="browser", scope="descendants", local_address="", local_port=0,
            url="https://private.example.invalid/console"))
        result = monitor.poll()
        candidate = next(row for row in result.entries if row.pid == 3 and row.remote_address == "198.18.0.249")
        self.assertEqual(candidate.application_protocol, "HTTPS")
        self.assertIn("候选", candidate.attribution)
        self.assertIn("本机端口未核对", candidate.attribution)
        self.assertTrue(all(not row.application_protocol for row in result.entries if row.id != candidate.id))

    def test_request_with_missing_remote_endpoint_is_observed_but_not_assigned_to_arbitrary_tcp(self):
        monitor, _ = self.monitor([connection(2)])
        monitor.add_observation(observation(evidence="browser", remote_address="", remote_port=0, local_port=0))
        result = monitor.poll()
        self.assertFalse(result.entries[0].application_protocol)
        self.assertEqual(result.observations[0].url, "https://gpu.example.invalid/api/latest")

    def test_browser_process_hint_also_limits_remote_endpoint_candidates(self):
        processes = [process(1, 0), process(2, 1, "node.exe", 11), process(3, 2, "msedge.exe", 12)]
        monitor, _ = self.monitor([connection(2), connection(3)], processes)
        monitor.add_observation(observation(pid=1, evidence="browser", scope="descendants",
            process_name="msedge.exe", local_address="", local_port=0))
        rows = monitor.poll().entries
        self.assertFalse(next(row for row in rows if row.pid == 2).application_protocol)
        self.assertEqual(next(row for row in rows if row.pid == 3).application_protocol, "HTTPS")

    def test_process_request_without_endpoints_is_only_candidate_for_matching_external_process(self):
        processes = [process(1, 0), process(2, 1, "node.exe", 11), process(3, 2, "msedge.exe", 12)]
        sockets = [connection(2), connection(3),
            SocketInfo(2, "TCP", "127.0.0.1", 50002, "127.0.0.1", 50003, "ESTABLISHED"),
            SocketInfo(2, "TCP", "0.0.0.0", 50004, "", 0, "LISTEN"),
            SocketInfo(2, "UDP", "198.18.0.1", 50005, "", 0, "BOUND")]
        monitor, _ = self.monitor(sockets, processes)
        monitor.add_observation(observation(pid=1, evidence="process-request", scope="descendants",
            process_name="node.exe", local_address="", local_port=0, remote_address="", remote_port=0))
        rows = monitor.poll().entries
        candidate = next(row for row in rows if row.pid == 2 and row.local_port == 50000)
        self.assertEqual(candidate.application_protocol, "HTTPS")
        self.assertIn("进程请求记录，未核对端点", candidate.attribution)
        self.assertIn("不能证明", candidate.attribution)
        self.assertFalse(any(row.request_urls for row in rows if row.id != candidate.id))

    def test_exact_socket_evidence_takes_precedence_over_same_process_candidate(self):
        monitor, _ = self.monitor([connection(2)])
        monitor.add_observation(observation(evidence="process-request", url="https://other.example.invalid/",
            local_address="", local_port=0, remote_address="", remote_port=0))
        monitor.add_observation(observation())
        row = monitor.poll().entries[0]
        self.assertIn("源/目标IP和端口", row.attribution)
        self.assertEqual(row.request_urls, ("https://gpu.example.invalid/api/latest",))

    def test_process_creation_identity_and_observation_time_reject_old_pid_claims(self):
        for values in (observation(process_started_at=10), observation(observed_at=5), observation(observed_at=20)):
            monitor, _ = self.monitor([connection(2)])
            monitor.add_observation(values)
            self.assertFalse(monitor.poll().entries[0].application_protocol)
        monitor, _ = self.monitor([connection(2)])
        monitor.add_observation(observation(process_started_at=11))
        self.assertEqual(monitor.poll().entries[0].application_protocol, "HTTPS")

    def test_two_observed_urls_can_share_one_keepalive_connection(self):
        monitor, _ = self.monitor([connection(2)])
        monitor.add_observation(observation(url="https://gpu.example.invalid/login?secret=redacted", observed_at=98))
        monitor.add_observation(observation())
        record = monitor.poll().entries[0]
        self.assertEqual(record.request_urls, ("https://gpu.example.invalid/api/latest", "https://gpu.example.invalid/login"))
        self.assertEqual(record.request_observed_at, 99)

    def test_same_pid_loopback_pair_is_internal_without_inventing_https(self):
        pair = [SocketInfo(2, "TCP", "127.0.0.1", 56561, "127.0.0.1", 56562, "ESTABLISHED"),
                SocketInfo(2, "TCP", "127.0.0.1", 56562, "127.0.0.1", 56561, "ESTABLISHED")]
        monitor, _ = self.monitor(pair)
        rows = monitor.poll().entries
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.application_protocol == "本机内部通信" for row in rows))
        self.assertTrue(all(row.classification == "internal" for row in rows))
        self.assertTrue(all(not row.request_urls and "成对" in row.attribution for row in rows))

    def test_ssh_context_labels_actual_ssh_even_on_nonstandard_port(self):
        monitor, _ = self.monitor([connection(2, port=6012)], [process(1, 0), process(2, 1, "ssh.exe", 11)])
        monitor.set_targets([dict(pid=2, kind="SSH", purpose="训练SSH · 4090", target="ssh://demo-server")])
        row = monitor.poll().entries[0]
        self.assertEqual((row.application_protocol, row.logical_target), ("SSH", "ssh://demo-server"))
        self.assertFalse(row.request_urls)

    def test_observations_are_bounded_and_do_not_relabel_reopened_old_tuple(self):
        monitor, reader = self.monitor([connection(2)])
        for index in range(600):
            monitor.add_observation(observation(url=f"https://gpu.example.invalid/api/{index}"))
        result = monitor.poll()
        self.assertEqual(len(result.observations), 512)
        self.assertEqual(len(result.entries[0].request_urls), 8)
        reader.return_value = snapshot([process(1, 0), process(2, 1, born=11)], [], 103)
        monitor.poll()
        reader.return_value = snapshot([process(1, 0), process(2, 1, born=11)], [connection(2)], 200)
        current = next(row for row in monitor.poll().entries if row.closed_at is None)
        self.assertFalse(current.application_protocol)  # 端点重用不能拿旧请求当新连接证据。

    def test_observation_arriving_during_table_read_is_retained_for_next_snapshot(self):
        monitor, reader = self.monitor([connection(2)])
        monitor.add_observation(observation(observed_at=102))
        first = monitor.poll()
        self.assertFalse(first.entries[0].application_protocol)
        self.assertEqual(len(first.observations), 1)
        reader.return_value = snapshot([process(1, 0), process(2, 1, born=11)], [connection(2)], 103)
        self.assertEqual(monitor.poll().entries[0].application_protocol, "HTTPS")

    def test_active_tuple_keeps_stronger_endpoint_evidence_after_observations_expire(self):
        for evidence, strength in (("requests", 3), ("browser", 2)):
            with self.subTest(evidence=evidence):
                monitor, reader = self.monitor([connection(2)])
                monitor.add_observation(observation(evidence=evidence))
                original = monitor.poll().entries[0]
                reader.return_value = snapshot([process(1, 0), process(2, 1, born=11)], [connection(2)], 2000)
                monitor.add_observation(observation(observed_at=1999, evidence="process-request",
                    url="https://candidate.example.invalid/new", purpose="较弱进程候选",
                    local_address="", local_port=0, remote_address="", remote_port=0))
                current = monitor.poll().entries[0]
                self.assertEqual((current.id, current.last_seen), (original.id, 2000))
                self.assertEqual(current.attribution_strength, strength)
                self.assertEqual((current.request_urls, current.attribution, current.request_observed_at, current.purpose),
                                 (original.request_urls, original.attribution, original.request_observed_at, original.purpose))

                reader.return_value = snapshot([process(1, 0), process(2, 1, born=11)], [], 2001)
                monitor.poll()
                reader.return_value = snapshot([process(1, 0), process(2, 1, born=11)], [connection(2)], 2002)
                reopened = next(row for row in monitor.poll().entries if row.closed_at is None)
                self.assertNotEqual(reopened.id, original.id)
                self.assertEqual(reopened.attribution_strength, 1)  # 重新出现的端点不继承旧连接的较强事实。
                self.assertEqual(reopened.request_urls, ("https://candidate.example.invalid/new",))


if __name__ == "__main__":
    unittest.main()
