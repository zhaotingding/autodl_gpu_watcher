"""密码表单认证的离线回归：只用临时偏好和模拟 HTTP，不访问真实站点。"""
import gc
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
import weakref

import requests

from autodl_watcher import ai_credentials, telemetry_auth
from autodl_watcher.collectors.telemetry import TelemetryApiCollector, TelemetryUnavailable
from autodl_watcher.settings_ui import GlobalSettings
from autodl_watcher.yaml_settings import read_settings, write_settings


ENDPOINT = "https://gpu.example.invalid/api/latest?token=private-query"
PASSWORD = " saved form password "


def response(status, payload=None, headers=None):
    result = requests.Response()  # 使用真实响应的状态处理，避免把302误当普通200模拟。
    result.status_code = status
    result.headers.update(headers or {})
    result._content = json.dumps(payload if payload is not None else {"items": []}).encode()
    result._content_consumed = True  # 普通requests非stream响应已消费正文，close不再访问网络raw。
    result.url = ENDPOINT
    return result


class TelemetryFormAuthTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = SimpleNamespace(endpoint=ENDPOINT, settings_root=self.root, timeout_seconds=2,
                                      retry_delay_seconds=0, max_attempts=2, only_online=True)
        self.session = Mock(headers={}, cookies=requests.cookies.RequestsCookieJar())
        session_factory = patch("autodl_watcher.collectors.telemetry.requests.Session", return_value=self.session)
        session_factory.start()
        self.addCleanup(session_factory.stop)
        secret = patch.object(ai_credentials, "read_secret", return_value=PASSWORD)
        secret.start()
        self.addCleanup(secret.stop)
        self.save()
        self.before_login = []

        def login(url, **kwargs):
            self.before_login.append(dict(self.session.cookies))
            self.session.cookies.set("session", "mock-cookie", domain=urlsplit(url).hostname, path="/")
            return response(302, headers={"Location": "/api/latest"})  # 模拟Requests接收Set-Cookie到Session。

        self.session.post.side_effect = login
        self.session.get.side_effect = lambda *args, **kwargs: response(200)
        self.collector = TelemetryApiCollector(self.config)
        self.addCleanup(self.collector.close)

    def save(self, enabled=True, mode="form", endpoint=ENDPOINT):
        with patch.object(ai_credentials, "save_secret"):
            telemetry_auth.save_settings(endpoint, "", enabled, "", self.root, auth_mode=mode)

    def test_new_settings_default_to_form_and_old_enabled_settings_keep_basic(self):
        other = self.root / "new-app"
        self.assertEqual(telemetry_auth.settings(ENDPOINT, other)["auth_mode"], "form")
        path = self.root / ".ui/preferences.yaml"
        write_settings(path, {"telemetry": {"endpoint": ENDPOINT, "username": "old-user", "password_auth": True}})
        saved = telemetry_auth.request_auth(ENDPOINT, self.root)
        self.assertEqual((saved.mode, saved.username, saved.password), ("basic", "old-user", PASSWORD))
        self.assertNotIn(PASSWORD, repr(saved))
        telemetry_auth.save_settings(ENDPOINT, "old-user", True, "", self.root)
        self.assertEqual(read_settings(path)["telemetry"]["auth_mode"], "basic")

    def test_initial_form_login_then_normal_polls_reuse_cookie_without_basic_header(self):
        self.assertEqual(self.collector.collect(), [])
        self.assertEqual(self.collector.collect(), [])
        self.session.post.assert_called_once_with("https://gpu.example.invalid/login",
            data={"password": PASSWORD, "next": "/api/latest"}, timeout=2, allow_redirects=False)
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(self.before_login, [{}])
        for call in self.session.get.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertNotIn("auth", call.kwargs)
        self.assertNotIn("Authorization", self.session.headers)

    def test_wrong_password_posts_once_and_never_tries_api_or_basic(self):
        self.session.post.side_effect = lambda *args, **kwargs: response(200, {"error": "bad password"})
        with self.assertRaises(TelemetryUnavailable) as failed:
            self.collector.collect()
        self.assertIn("密码", str(failed.exception))
        self.assertNotIn(PASSWORD, str(failed.exception))
        self.assertNotIn("private-query", str(failed.exception))
        self.session.post.assert_called_once()
        self.session.get.assert_not_called()
        with self.assertRaisesRegex(TelemetryUnavailable, "本轮未发送请求"):
            self.collector.collect()
        self.session.post.assert_called_once()
        self.session.get.assert_not_called()

    def test_login_timeout_is_one_post_with_cooldown_and_no_get(self):
        self.session.post.side_effect = requests.Timeout(PASSWORD + ENDPOINT)
        with self.assertRaises(TelemetryUnavailable) as failed:
            self.collector.collect()
        self.assertIn("未自动重试", str(failed.exception))
        self.assertNotIn(PASSWORD, str(failed.exception))
        self.assertNotIn("private-query", str(failed.exception))
        with self.assertRaisesRegex(TelemetryUnavailable, "本轮未发送请求"):
            self.collector.collect()
        self.session.post.assert_called_once()
        self.session.get.assert_not_called()

    def test_login_rate_limit_respects_retry_after_without_guessing_another_protocol(self):
        self.session.post.side_effect = lambda *args, **kwargs: response(429, headers={"Retry-After": "180"})
        with patch("autodl_watcher.collectors.telemetry.time.monotonic", return_value=100):
            with self.assertRaises(TelemetryUnavailable):
                self.collector.collect()
        with patch("autodl_watcher.collectors.telemetry.time.monotonic", return_value=200):
            with self.assertRaisesRegex(TelemetryUnavailable, "本轮未发送请求"):
                self.collector.collect()
        self.session.post.assert_called_once()
        self.session.get.assert_not_called()

    def test_expired_cookie_waits_before_one_new_login_then_resumes_polling(self):
        self.session.get.side_effect = [response(200), response(401, {"error": "login required"}), response(200)]
        with patch("autodl_watcher.collectors.telemetry.time.monotonic", return_value=100):
            self.collector.collect()
            with self.assertRaisesRegex(TelemetryUnavailable, "HTTP 401"):
                self.collector.collect()
            with self.assertRaisesRegex(TelemetryUnavailable, "本轮未发送请求"):
                self.collector.collect()
        self.assertEqual((self.session.post.call_count, self.session.get.call_count), (1, 2))
        with patch("autodl_watcher.collectors.telemetry.time.monotonic", return_value=161):
            self.collector.collect()
        self.assertEqual((self.session.post.call_count, self.session.get.call_count), (2, 3))
        self.assertEqual(self.before_login, [{}, {}])  # 失效会话不掩盖下一次错误密码。

    def test_redirect_outside_origin_after_login_is_not_followed(self):
        def unsafe_login(*args, **kwargs):
            self.session.cookies.set("session", "mock-cookie")
            return response(302, headers={"Location": "https://other.example.invalid/landing"})
        self.session.post.side_effect = unsafe_login
        with self.assertRaisesRegex(TelemetryUnavailable, "其他来源"):
            self.collector.collect()
        self.session.post.assert_called_once()
        self.session.get.assert_not_called()
        self.assertFalse(self.session.post.call_args.kwargs["allow_redirects"])

    def test_api_redirect_does_not_follow_or_send_credentials_to_other_origin(self):
        redirect = response(302, headers={"Location": "https://other.example.invalid/landing"})
        redirect._content = b"<html>login</html>"
        self.session.get.side_effect = lambda *args, **kwargs: redirect
        with self.assertRaises(TelemetryUnavailable):
            self.collector.collect()
        self.session.get.assert_called_once()
        self.assertEqual(self.session.get.call_args.args[0], ENDPOINT)
        self.assertFalse(self.session.get.call_args.kwargs["allow_redirects"])

    def test_disabling_authentication_clears_cookie_and_does_not_log_in_again(self):
        self.collector.collect()
        self.assertTrue(self.session.cookies)
        self.save(enabled=False)
        self.collector.collect()
        self.assertFalse(self.session.cookies)
        self.session.post.assert_called_once()
        self.assertNotIn("auth", self.session.get.call_args.kwargs)

    def test_switching_to_basic_clears_form_cookie_and_uses_explicit_basic(self):
        self.collector.collect()
        self.save(mode="basic")
        self.collector.collect()
        self.assertFalse(self.session.cookies)
        self.session.post.assert_called_once()
        self.assertEqual(self.session.get.call_args.kwargs["auth"], ("", PASSWORD))

    def test_origin_change_clears_old_cookie_before_logging_in_to_new_origin(self):
        self.collector.collect()
        endpoint = "https://new-gpu.example.invalid/api/latest"
        self.save(endpoint=endpoint)
        self.config.endpoint = endpoint
        self.collector.collect()
        self.assertEqual(self.before_login, [{}, {}])
        self.assertEqual(self.session.post.call_args.args[0], "https://new-gpu.example.invalid/login")
        self.assertEqual(self.session.get.call_args.args[0], endpoint)
        self.assertNotIn("gpu.example.invalid", {cookie.domain for cookie in self.session.cookies})

    def test_changed_password_reauthenticates_without_waiting_for_old_failure_cooldown(self):
        self.session.post.side_effect = requests.Timeout("old login")
        with self.assertRaises(TelemetryUnavailable):
            self.collector.collect()
        self.session.post.side_effect = None
        self.session.post.return_value = response(302, headers={"Location": "/api/latest"})
        def new_login(*args, **kwargs):
            self.session.cookies.set("session", "new-cookie")
            return self.session.post.return_value
        self.session.post.side_effect = new_login
        with patch.object(ai_credentials, "read_secret", return_value="new-password"):
            self.collector.collect()
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(self.session.post.call_args.kwargs["data"]["password"], "new-password")

    def test_typing_password_in_ui_enables_auth_and_explicit_uncheck_is_retained(self):
        self.save(enabled=False)
        root = tk.Tk()
        root.withdraw()
        try:
            parent = ttk.Frame(root)
            host = SimpleNamespace(app=SimpleNamespace(config=SimpleNamespace(
                telemetry=SimpleNamespace(endpoint=ENDPOINT)), local=self.root / ".ui"),
                save_telemetry_settings=lambda: None, delete_telemetry_password=lambda: None)
            GlobalSettings.build_telemetry_settings(host, parent)
            self.assertFalse(host.telemetry_password_auth.get())
            host.telemetry_password.set("new-user-password")
            self.assertTrue(host.telemetry_password_auth.get())
            host.telemetry_password_auth.set(False)
            self.assertFalse(host.telemetry_password_auth.get())
            host.telemetry_password.set("")
            self.assertFalse(host.telemetry_password_auth.get())
        finally:
            root.destroy()

    def test_destroyed_settings_release_password_variables_without_cyclic_gc(self):
        was_enabled = gc.isenabled()
        gc.disable()  # 旧trace若捕获host，循环不能靠偶然GC消失后蒙混通过。
        root = None
        destroyed = False
        try:
            root = tk.Tk()
            root.withdraw()
            parent = ttk.Frame(root)
            host = SimpleNamespace(app=SimpleNamespace(config=SimpleNamespace(
                telemetry=SimpleNamespace(endpoint=ENDPOINT)), local=self.root / ".ui"),
                save_telemetry_settings=lambda: None, delete_telemetry_password=lambda: None)
            GlobalSettings.build_telemetry_settings(host, parent)
            password_ref = weakref.ref(host.telemetry_password)
            enabled_ref = weakref.ref(host.telemetry_password_auth)
            root.destroy()
            destroyed = True
            del host  # 不保留Tk变量强引用；窗口销毁后应立即由主线程释放。
            self.assertIsNone(password_ref())
            self.assertIsNone(enabled_ref())
        finally:
            if root is not None and not destroyed:
                root.destroy()
            gc.collect()  # 即使旧实现断言失败，也在当前主线程清理Tk循环。
            if was_enabled:
                gc.enable()
