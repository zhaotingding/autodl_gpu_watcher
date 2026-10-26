"""仅用本机回环验证原生表和观察历史；不连接外部平台、SSH 或 AI。"""
import os
import socket
import time
import unittest

from autodl_watcher.network_monitor import NetworkMonitor


@unittest.skipUnless(os.name == "nt", "连接系统表仅实现 Windows")
class NetworkLoopbackTest(unittest.TestCase):
    def test_real_tcp_udp_endpoints_and_disappearance_are_observed(self):
        monitor = NetworkMonitor()
        with socket.socket() as listener, socket.socket() as client, socket.socket(type=socket.SOCK_DGRAM) as udp:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(3)
            udp.bind(("127.0.0.1", 0))  # 仅绑定，不发 UDP 数据包。
            client.settimeout(3)
            client.connect(listener.getsockname())
            accepted, _ = listener.accept()
            try:
                result = monitor.poll()
                self.assertFalse(result.errors, result.errors)
                tcp = next(row for row in result.entries if row.protocol == "TCP"
                           and row.local_port == client.getsockname()[1]
                           and row.remote_port == listener.getsockname()[1])
                self.assertEqual((tcp.pid, tcp.local_address, tcp.remote_address, tcp.state),
                                 (os.getpid(), "127.0.0.1", "127.0.0.1", "ESTABLISHED"))
                bound = next(row for row in result.entries if row.protocol == "UDP"
                             and row.local_port == udp.getsockname()[1])
                self.assertEqual((bound.remote_address, bound.remote_port, bound.state), ("", 0, "BOUND"))
            finally:
                accepted.close()
        deadline = time.monotonic() + 3
        while True:
            result = monitor.poll()
            ended = next(row for row in result.entries if row.id == tcp.id)
            if ended.closed_at is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        self.assertIsNotNone(ended.closed_at)
