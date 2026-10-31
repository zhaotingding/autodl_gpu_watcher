"""请求观察只使用模拟响应及本机HTTP；不连接平台、SSH、AI或代理。"""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import requests

from autodl_watcher import network_activity as activity


PRIVATE_URL = "https://private-user:private-password@gpu.example.invalid/api/latest?token=private-token#private-fragment"
CLEAN_URL = "https://gpu.example.invalid/api/latest"


@contextmanager
def loopback_http():
    peers, paths = [], []
    body = b'{"items":[1,2],"source":"loopback"}'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # 两次业务GET应复用同一TCP连接。

        def log_message(self, *_):
            pass  # 不把含模拟token的请求行输出到测试终端。

        def do_GET(self):
            peers.append(self.client_address)
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        yield server, peers, paths, body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise AssertionError("本机HTTP测试服务器未正常退出")


def driver_context(pid=23456):
    process = SimpleNamespace(pid=pid)
    transport = SimpleNamespace(_proc=process)
    connection = SimpleNamespace(_transport=transport)
    return SimpleNamespace(_impl_obj=SimpleNamespace(_connection=connection))


class NetworkActivityTest(unittest.TestCase):
    def setUp(self):
        previous = activity.set_observer(None)
        self.addCleanup(activity.set_observer, previous)  # 每个用例恢复原观察器，不污染其他业务测试。

    def test_real_keepalive_requests_observe_exact_socket_without_extra_request_or_body_consumption(self):
        from autodl_watcher.network_monitor import NetworkMonitor
        events = []
        monitor = NetworkMonitor()
        def observed(event):
            events.append(event)
            monitor.add_observation(event)
        activity.set_observer(observed)
        with loopback_http() as (server, peers, paths, body), requests.Session() as session:
            session.trust_env = False  # 回环验收明确不继承本机HTTP代理。
            activity.attach_requests(session, "测试GPU采集")
            url = f"http://private-user:private-password@127.0.0.1:{server.server_port}/latest?token=private-token"
            for _ in range(2):
                with session.get(url, timeout=3) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, body)
                    self.assertEqual(response.json(), {"items": [1, 2], "source": "loopback"})
            self.assertEqual(len(paths), 2)  # 钩子观察业务原请求，不能偷偷追加HEAD/GET。
            self.assertEqual(len(events), 2)
            self.assertEqual(peers[0], peers[1])
            for event, peer in zip(events, peers):
                self.assertEqual((event["pid"], event["evidence"], event["application_protocol"]),
                                 (os.getpid(), "requests", "HTTP"))
                self.assertEqual((event["local_address"], event["local_port"]), peer)
                self.assertEqual((event["remote_address"], event["remote_port"]), ("127.0.0.1", server.server_port))
                self.assertEqual(event["url"], f"http://127.0.0.1:{server.server_port}/latest")
                self.assertEqual(event["purpose"], "测试GPU采集")
            self.assertNotIn("private-", json.dumps(events))
            if os.name == "nt":
                snapshot = monitor.poll()  # 实际Windows连接表与真实HTTP钩子联通验证，不以模拟端点冒充。
                self.assertFalse(snapshot.errors, snapshot.errors)
                row = next(item for item in snapshot.entries if item.pid == os.getpid()
                           and item.local_port == peers[0][1] and item.remote_port == server.server_port)
                self.assertEqual(row.request_urls, (f"http://127.0.0.1:{server.server_port}/latest",))
                self.assertEqual(row.application_protocol, "HTTP")
                self.assertIn("已匹配", row.attribution)

    def test_observer_failure_does_not_break_successful_real_http_response(self):
        def broken_observer(_):
            raise RuntimeError("模拟观察器失败")
        activity.set_observer(broken_observer)
        output = io.StringIO()
        with loopback_http() as (server, _, paths, body), requests.Session() as session, \
                redirect_stdout(output), redirect_stderr(output):
            session.trust_env = False
            activity.attach_requests(session, "回调失败测试")
            with session.get(f"http://127.0.0.1:{server.server_port}/ok", timeout=3) as response:
                self.assertEqual(response.content, body)
            self.assertEqual(paths, ["/ok"])
        self.assertEqual(output.getvalue(), "")

    def test_publish_redacts_before_cross_process_output(self):
        output = io.StringIO()
        activity.set_observer(activity.emit_observation)
        with redirect_stdout(output):
            activity.publish(PRIVATE_URL, "GPU采集")
        line = output.getvalue().strip()
        self.assertTrue(line.startswith(activity.NETWORK_PREFIX))
        value = json.loads(line[len(activity.NETWORK_PREFIX):])
        self.assertEqual(value["url"], CLEAN_URL)
        for secret in ("private-user", "private-password", "private-token", "private-fragment"):
            self.assertNotIn(secret, line)

    def test_temporarily_replaced_observer_is_restored(self):
        first, second = [], []
        first_callback, second_callback = first.append, second.append
        self.assertIsNone(activity.set_observer(first_callback))
        previous = activity.set_observer(second_callback)
        self.assertIs(previous, first_callback)
        activity.publish(CLEAN_URL, "second")
        self.assertIs(activity.set_observer(previous), second_callback)
        activity.publish(CLEAN_URL, "first")
        self.assertEqual([item["purpose"] for item in first], ["first"])
        self.assertEqual([item["purpose"] for item in second], ["second"])

    def test_no_observer_keeps_cli_silent_and_skips_playwright_metadata(self):
        response = Mock()
        output = io.StringIO()
        with redirect_stdout(output):
            activity.publish(PRIVATE_URL, "无需记录")
            activity.observe_playwright(response)
        response.server_addr.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    def test_mock_or_non_requests_sessions_are_not_modified(self):
        for session in (Mock(), SimpleNamespace(), SimpleNamespace(hooks=None)):
            activity.attach_requests(session, "模拟会话")
        session = Mock(hooks={})
        activity.attach_requests(session, "已知hooks映射")
        self.assertEqual(len(session.hooks["response"]), 1)

    def test_released_requests_socket_keeps_process_request_without_inventing_endpoints(self):
        events = []
        activity.set_observer(events.append)
        session = SimpleNamespace(hooks={})
        activity.attach_requests(session, "连接已回收")
        response = SimpleNamespace(url=PRIVATE_URL, raw=SimpleNamespace())
        self.assertIsNone(session.hooks["response"][0](response))
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["url"], events[0]["evidence"]), (CLEAN_URL, "process-request"))
        self.assertEqual((events[0]["local_address"], events[0]["remote_address"]), ("", ""))

    def test_playwright_api_response_uses_driver_pid_and_peer_when_supported(self):
        events = []
        activity.set_observer(events.append)
        response = SimpleNamespace(url=PRIVATE_URL,
            server_addr=lambda: {"ipAddress": "198.18.0.10", "port": 443})
        activity.observe_playwright(response, context=driver_context())
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event["pid"], event["scope"]), (23456, "process"))
        self.assertEqual((event["remote_address"], event["remote_port"]), ("198.18.0.10", 443))
        self.assertEqual((event["evidence"], event["url"], event["application_protocol"]), ("browser", CLEAN_URL, "HTTPS"))
        self.assertEqual(event["local_port"], 0)  # API未给本机端点，不能凭远端字段伪造精确TCP匹配。
        self.assertEqual(event["process_name"], "node.exe" if os.name == "nt" else "node")

    def test_old_playwright_api_without_server_addr_keeps_actual_url_and_driver_identity(self):
        events = []
        activity.set_observer(events.append)
        activity.observe_playwright(SimpleNamespace(url=PRIVATE_URL), context=driver_context())
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["url"], events[0]["pid"], events[0]["evidence"]),
                         (CLEAN_URL, 23456, "process-request"))
        self.assertEqual((events[0]["remote_address"], events[0]["remote_port"]), ("", 0))

    def test_released_playwright_peer_metadata_keeps_request_and_driver_identity(self):
        events = []
        activity.set_observer(events.append)
        response = SimpleNamespace(url=PRIVATE_URL,
            server_addr=Mock(side_effect=RuntimeError("response released")))
        activity.observe_playwright(response, context=driver_context())
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["url"], events[0]["pid"], events[0]["evidence"]),
                         (CLEAN_URL, 23456, "process-request"))
        self.assertEqual((events[0]["remote_address"], events[0]["remote_port"]), ("", 0))

    def test_changed_playwright_driver_metadata_falls_back_to_worker_descendants(self):
        events = []
        activity.set_observer(events.append)
        activity.observe_playwright(SimpleNamespace(url=PRIVATE_URL), context=SimpleNamespace())
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["url"], events[0]["pid"], events[0]["scope"]),
                         (CLEAN_URL, os.getpid(), "descendants"))
        self.assertEqual(events[0]["process_name"], "node.exe" if os.name == "nt" else "node")

    def test_browser_response_records_descendant_candidate_not_a_fake_local_socket(self):
        events = []
        activity.set_observer(events.append)
        response = SimpleNamespace(url=PRIVATE_URL,
            server_addr=lambda: {"ipAddress": "198.18.0.107", "port": 443})
        activity.observe_playwright(response)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event["pid"], event["scope"], event["purpose"]),
                         (os.getpid(), "descendants", "AutoDL浏览器"))
        self.assertEqual((event["url"], event["remote_address"], event["local_port"]),
                         (CLEAN_URL, "198.18.0.107", 0))
        self.assertEqual(event["process_name"], "msedge.exe" if os.name == "nt" else "msedge")
        activity.observe_playwright(response, browser_process_name="chrome.exe")
        self.assertEqual(events[-1]["process_name"], "chrome.exe")  # 使用Chrome配置时不硬套Edge进程名。


if __name__ == "__main__":
    unittest.main()
