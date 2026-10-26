"""原生表解码及失败可见性；不向任何服务器建立连接。"""
import ctypes
import os
import socket
import struct
import unittest
from unittest.mock import Mock, patch

from autodl_watcher import network_system as system


def table(*rows):
    return struct.pack("<I", len(rows)) + b"".join(bytes(row) for row in rows)


class NetworkSystemTest(unittest.TestCase):
    def test_native_owner_pid_layout_has_expected_row_sizes(self):
        self.assertEqual([ctypes.sizeof(row) for row in (system._Tcp4, system._Tcp6,
                                                       system._Udp4, system._Udp6)], [24, 56, 12, 28])

    def test_ipv4_tcp_decodes_addresses_ports_pid_and_state(self):
        row = system._Tcp4(5, int.from_bytes(socket.inet_aton("127.0.0.1"), "little"), socket.htons(50123),
                           int.from_bytes(socket.inet_aton("192.0.2.1"), "little"), socket.htons(22), 321)
        record, = system._decode_table(table(row), "TCP", system._AF_INET)
        self.assertEqual(record, system.SocketInfo(321, "TCP", "127.0.0.1", 50123, "192.0.2.1", 22, "ESTABLISHED"))

    def test_ipv6_tcp_scope_and_network_byte_order(self):
        row = system._Tcp6()
        row.local_address[:] = socket.inet_pton(socket.AF_INET6, "fe80::1")
        row.remote_address[:] = socket.inet_pton(socket.AF_INET6, "2001:db8::2")
        row.local_scope, row.local_port = socket.htonl(7), socket.htons(443)
        row.remote_port, row.state, row.pid = socket.htons(52001), 8, 456
        record, = system._decode_table(table(row), "TCP", system._AF_INET6)
        self.assertEqual((record.local_address, record.local_port, record.remote_address,
                          record.remote_port, record.state), ("fe80::1%7", 443, "2001:db8::2", 52001, "CLOSE_WAIT"))

    def test_udp_reports_bound_endpoint_without_invented_peer(self):
        row = system._Udp6()
        row.local_address[:] = socket.inet_pton(socket.AF_INET6, "::")
        row.local_port, row.pid = socket.htons(5353), 88
        record, = system._decode_table(table(row), "UDP", system._AF_INET6)
        self.assertEqual((record.protocol, record.local_address, record.local_port,
                          record.remote_address, record.remote_port, record.state), ("UDP", "::", 5353, "", 0, "BOUND"))

    def test_tcp_listen_does_not_present_meaningless_peer_as_connection(self):
        row = system._Tcp4(2, 0, socket.htons(1234), 0xffffffff, socket.htons(999), 12)
        record, = system._decode_table(table(row), "TCP", system._AF_INET)
        self.assertEqual((record.state, record.remote_address, record.remote_port), ("LISTEN", "", 0))

    def test_truncated_tables_are_errors_not_empty_success(self):
        for data in (b"", b"\x01\x00\x00\x00", struct.pack("<I", 10) + b"x" * 24):
            with self.subTest(data=data), self.assertRaises(ValueError):
                system._decode_table(data, "TCP", system._AF_INET)

    def test_buffer_resize_is_bounded_and_uses_owner_pid_class(self):
        payload = table(system._Udp4(0, socket.htons(5353), 11))
        calls = []
        def api(buffer, size, ordered, family, kind, reserved):
            calls.append((family, kind, reserved))
            ctypes.cast(size, ctypes.POINTER(system._DWORD)).contents.value = len(payload)
            if buffer is None:
                return 122
            ctypes.memmove(buffer, payload, len(payload))
            return 0
        result = system._socket_table(api, "UDP", system._AF_INET)
        self.assertEqual(result[0].pid, 11)
        self.assertEqual(calls, [(2, 1, 0), (2, 1, 0)])
        def growing(buffer, size, *args):
            ctypes.cast(size, ctypes.POINTER(system._DWORD)).contents.value = 128
            return 122
        with self.assertRaisesRegex(OSError, "连续变化"):
            system._socket_table(growing, "TCP", system._AF_INET6)

    def test_process_creation_time_converts_filetime_and_closes_handle(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 77
        expected = 1_700_000_000
        ticks = (expected + 11_644_473_600) * 10_000_000
        def times(handle, created, *unused):
            value = ctypes.cast(created, ctypes.POINTER(system._FileTime)).contents
            value.low, value.high = ticks & 0xffffffff, ticks >> 32
            return True
        kernel.GetProcessTimes.side_effect = times
        self.assertEqual(system._creation_time(kernel, 123), expected)
        kernel.OpenProcess.assert_called_once_with(0x1000, False, 123)
        kernel.CloseHandle.assert_called_once_with(77)
        kernel.reset_mock()
        kernel.GetProcessTimes.side_effect = None
        kernel.GetProcessTimes.return_value = False
        self.assertEqual(system._creation_time(kernel, 123), 0)
        kernel.CloseHandle.assert_called_once_with(77)

    def test_incomplete_process_enumeration_raises_and_releases_snapshot(self):
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = 66
        kernel.Process32FirstW.return_value = True
        kernel.Process32NextW.return_value = False
        with patch.object(system, "_creation_time", return_value=0), \
             patch.object(system.ctypes, "get_last_error", return_value=5, create=True):
            with self.assertRaisesRegex(OSError, "Process32NextW"):
                system._processes(kernel)
        kernel.CloseHandle.assert_called_once_with(66)

    def test_read_failure_is_visible_and_does_not_discard_other_tables(self):
        process = system.ProcessInfo(os.getpid(), 1, "python.exe", 100.0)
        row = system.SocketInfo(os.getpid(), "TCP", "127.0.0.1", 100, "127.0.0.1", 101, "ESTABLISHED")
        with patch.object(system.os, "name", "nt"), patch.object(system, "_libraries", return_value=(Mock(), Mock())), \
             patch.object(system, "_socket_table", side_effect=[(row,), OSError(5, "access denied"), (), ()]), \
             patch.object(system, "_processes", return_value=(process,)):
            snapshot = system.read_system_snapshot()
        self.assertEqual(snapshot.sockets, (row,))
        self.assertEqual(snapshot.processes, (process,))
        self.assertEqual(len(snapshot.errors), 1)
        self.assertIn("TCP/IPv6", snapshot.errors[0])

    def test_capture_timestamp_precedes_tables_and_is_not_replaced_at_completion(self):
        order = []
        process = system.ProcessInfo(os.getpid(), 1, "python.exe", 100.0)
        def clock():
            order.append("clock")
            return 123.0
        def read_table(*unused):
            order.append("table")
            return ()
        with patch.object(system.os, "name", "nt"), patch.object(system.time, "time", side_effect=clock) as now, \
             patch.object(system, "_libraries", return_value=(Mock(), Mock())), \
             patch.object(system, "_socket_table", side_effect=read_table), \
             patch.object(system, "_processes", return_value=(process,)):
            snapshot = system.read_system_snapshot()
        self.assertEqual(snapshot.captured_at, 123.0)
        self.assertEqual(order, ["clock", "table", "table", "table", "table"])
        now.assert_called_once_with()

    def test_process_table_failure_is_not_silently_zero_processes(self):
        with patch.object(system.os, "name", "nt"), patch.object(system, "_libraries", return_value=(Mock(), Mock())), \
             patch.object(system, "_socket_table", return_value=()), \
             patch.object(system, "_processes", side_effect=OSError(5, "access denied")):
            snapshot = system.read_system_snapshot()
        self.assertEqual(snapshot.processes, ())
        self.assertIn("进程快照", snapshot.errors[0])

    def test_unsupported_platform_is_explicit(self):
        with patch.object(system.os, "name", "posix"), patch.object(system, "_libraries") as load:
            snapshot = system.read_system_snapshot()
        self.assertTrue(snapshot.errors)
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
