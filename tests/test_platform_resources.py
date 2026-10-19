import unittest
from unittest.mock import Mock

from playwright.sync_api import Error

from autodl_watcher.collectors.platform import PlatformBrowserCollector, PlatformTransientError
import test_collectors


class PlatformResponseResourcesTest(unittest.TestCase):
    def collector(self):
        collector = PlatformBrowserCollector(test_collectors.PlatformClassificationV052Test._config())
        collector.start = Mock()
        collector._context = Mock()
        collector._authorization = "Bearer local-test"
        collector._machine_list_payload = {}
        return collector

    def test_reused_context_releases_each_read_response(self):
        collector = self.collector()
        responses = [Mock(status=200, ok=True) for _ in range(100)]
        for response in responses:
            response.json.return_value = {"code": "Success", "data": {"list": []}}
        collector._context.request.post.side_effect = responses
        for _ in responses:
            collector._collect_machine_list_direct()
        self.assertEqual(collector._context.request.post.call_count, 100)
        for response in responses:
            response.dispose.assert_called_once()
        collector.start.assert_called()  # 复用同一 context，不用反复重启浏览器掩盖响应体保留。

    def test_api_response_is_released_when_json_read_fails_without_retry(self):
        collector = self.collector()
        response = collector._context.request.post.return_value
        response.status, response.ok = 200, True
        response.json.side_effect = Error("response unavailable")
        with self.assertRaises(PlatformTransientError):
            collector.post_api_json("/api/v2/instance/power_on", {})
        response.dispose.assert_called_once()
        collector._context.request.post.assert_called_once()

    def test_http_and_business_errors_also_release_response(self):
        for status, payload in ((503, {}), (200, {"code": "Failed"})):
            with self.subTest(status=status):
                collector = self.collector()
                response = collector._context.request.post.return_value
                response.status, response.ok = status, status == 200
                response.json.return_value = payload
                try:
                    collector.post_api_json("/query", {})
                except RuntimeError:
                    pass
                response.dispose.assert_called_once()
