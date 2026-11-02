"""监控浏览器过滤与请求去重；真实网页仅由127.0.0.1提供。"""
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
import threading
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import Error, sync_playwright
from autodl_watcher.collectors.platform import PlatformBrowserCollector
from autodl_watcher.login import find_edge_executable
import test_collectors


class BrowserNetworkTest(unittest.TestCase):
    def test_filter_is_once_per_page_and_does_not_turn_off_http_cache(self):
        collector = PlatformBrowserCollector(test_collectors.PlatformClassificationV052Test._config())
        collector._context = Mock()
        page = Mock()
        collector._filter_monitor_page(page)
        collector._filter_monitor_page(page)
        collector._context.new_cdp_session.assert_called_once_with(page)
        calls = collector._context.new_cdp_session.return_value.send.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["Network.enable", "Network.setBlockedURLs"])
        self.assertEqual(calls[1].args[1], {"urls": ["*://hm.baidu.com/*"]})
        collector._context.route.assert_not_called()
        page.on.call_args.args[1]()
        self.assertEqual(collector._page_filters, {})

    def test_unsupported_filter_reports_limit_without_claiming_blocked_or_breaking_collection(self):
        collector = PlatformBrowserCollector(test_collectors.PlatformClassificationV052Test._config())
        collector._context = Mock()
        collector._context.new_cdp_session.return_value.send.side_effect = Error("unsupported")
        output = io.StringIO()
        with redirect_stdout(output):
            collector._filter_monitor_page(Mock())
        self.assertIn("过滤未启用", output.getvalue())
        self.assertEqual(collector._page_filters, {})
        collector._context.new_cdp_session.return_value.detach.assert_called_once()

    def test_same_round_occupancy_reuses_hosts_but_still_validates_each_modal(self):
        collector = PlatformBrowserCollector(test_collectors.PlatformClassificationV052Test._config())
        collector._page = Mock()
        modal = Mock()
        modal.locator.return_value.count.return_value = 0
        with patch.object(collector, "collect") as hosts, \
             patch.object(collector, "_find_occupancy_action") as action, \
             patch.object(collector, "_find_visible_occupancy_modal", return_value=modal), \
             patch.object(collector, "_close_occupancy_modal"), \
             patch("autodl_watcher.collectors.platform.validate_occupancy_snapshot", return_value=[]) as validate:
            for entry in ("autodl-203-1", "autodl-203-2"):
                collector.collect_occupancy(entry, expected_idle=0, expected_total=2, refresh_hosts=False)
            hosts.assert_not_called()  # 去掉两个无效的主机列表重复请求，未跳过占用接口。
            self.assertEqual(action.return_value.click.call_count, 2)
            self.assertEqual(validate.call_count, 2)
            self.assertEqual([call.kwargs["machine_name"] for call in validate.call_args_list], ["autodl-203-1", "autodl-203-2"])
            collector.collect_occupancy("autodl-203-1")
            hosts.assert_called_once()  # 独立入口保留原初始化行为。

    @unittest.skipUnless(os.name == "nt", "真实浏览器验收使用Windows Edge")
    def test_local_browser_blocks_analytics_keeps_api_and_http_cache(self):
        edge = find_edge_executable()
        if not edge:
            self.skipTest("未安装Edge；仍执行模拟生命周期测试")
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                requests.append(self.path)
                if self.path == "/":
                    body = b'<link rel="stylesheet" href="/style.css"><script>fetch("/api").then(r=>r.text()).then(t=>window.result=t);fetch("/analytics").catch(()=>window.blocked=true);</script>'
                    content = "text/html"
                elif self.path == "/style.css":
                    body, content = b"body{background:rgb(12,34,56)}", "text/css"
                else:
                    body, content = b"ok", "text/plain"
                self.send_response(200)
                self.send_header("Content-Type", content)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=3600" if self.path == "/style.css" else "no-store")
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = f"http://127.0.0.1:{server.server_port}"
        collector = PlatformBrowserCollector(test_collectors.PlatformClassificationV052Test._config())
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=str(edge), headless=True,
                    args=["--no-proxy-server", "--disable-background-networking", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"])
                try:
                    context = browser.new_context()
                    collector._context = context
                    with patch("autodl_watcher.collectors.platform._MONITOR_BLOCKED_URLS", (address + "/analytics*",)):
                        for _ in range(2):
                            page = context.new_page()
                            collector._filter_monitor_page(page)
                            page.goto(address)
                            page.wait_for_function("window.result === 'ok' && window.blocked === true")
                            self.assertEqual(page.evaluate("getComputedStyle(document.body).backgroundColor"), "rgb(12, 34, 56)")
                            page.close()
                            self.assertEqual(collector._page_filters, {})
                    self.assertEqual(requests.count("/api"), 2)
                    self.assertEqual(requests.count("/analytics"), 0)
                    self.assertEqual(requests.count("/style.css"), 1)  # 新页面复用缓存，没有因拦截而重复下载。
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
