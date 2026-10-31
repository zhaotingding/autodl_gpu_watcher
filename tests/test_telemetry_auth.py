"""站点认证只用临时偏好、模拟 HTTP 和模拟凭据管理器，不保存真实密码。"""
import ctypes
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from autodl_watcher import ai_credentials, telemetry_auth
from autodl_watcher.collectors.telemetry import TelemetryApiCollector, TelemetryAuthenticationRequired, TelemetryUnavailable
from autodl_watcher.yaml_settings import read_settings, write_settings
from autodl_watcher.settings_ui import GlobalSettings


ENDPOINT = "https://gpu.example.invalid/api/latest?token=private-query"


class TelemetryAuthTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def save(self, password=" pass with spaces ", enabled=True, endpoint=ENDPOINT):
        with patch.object(ai_credentials, "save_secret") as store:
            telemetry_auth.save_settings(endpoint, "monitor-user", enabled, password, self.root, auth_mode="basic")
        return store

    def collector(self, endpoint=ENDPOINT):
        config = SimpleNamespace(endpoint=endpoint, settings_root=self.root, timeout_seconds=2,
                                 retry_delay_seconds=0, max_attempts=2, only_online=True)
        collector = TelemetryApiCollector(config)
        collector._session = Mock(headers={})
        self.addCleanup(collector.close)
        return collector

    def test_save_merges_preferences_and_keeps_password_out_of_yaml(self):
        path = self.root / ".ui/preferences.yaml"
        write_settings(path, {"user": "已有用户", "poll": "120", "live": False})
        store = self.save()
        saved = read_settings(path)
        self.assertEqual((saved["user"], saved["poll"], saved["live"]), ("已有用户", "120", False))
        self.assertEqual(saved["telemetry"]["endpoint"], ENDPOINT)
        self.assertNotIn("pass with spaces", path.read_text(encoding="utf-8"))
        store.assert_called_once_with("telemetry", "https://gpu.example.invalid:443", " pass with spaces ")
        self.assertEqual(telemetry_auth.effective_endpoint("https://old.example.invalid", self.root), ENDPOINT)

    def test_empty_password_preserves_secret_and_unconfigured_source_can_be_saved(self):
        store = self.save(password="")
        store.assert_not_called()
        with patch.object(ai_credentials, "save_secret") as store:
            telemetry_auth.save_settings("", "", False, "", self.root)
        self.assertEqual(telemetry_auth.effective_endpoint(ENDPOINT, self.root), "")
        store.assert_not_called()

    def test_auth_is_disabled_without_explicit_root_or_for_different_origin(self):
        self.save()
        with patch.object(ai_credentials, "read_secret") as read:
            self.assertIsNone(telemetry_auth.request_auth(ENDPOINT))
            self.assertIsNone(telemetry_auth.request_auth("https://other.example.invalid/api", self.root))
            read.assert_not_called()
        self.save(enabled=False)
        with patch.object(ai_credentials, "read_secret") as read:
            self.assertIsNone(telemetry_auth.request_auth(ENDPOINT, self.root))
            read.assert_not_called()

    def test_auth_uses_same_origin_across_path_query_and_default_port(self):
        self.save()
        with patch.object(ai_credentials, "read_secret", return_value=" pass with spaces ") as read:
            result = telemetry_auth.request_auth("https://gpu.example.invalid:443/other?token=another", self.root)
        self.assertEqual((result.mode, result.username, result.password), ("basic", "monitor-user", " pass with spaces "))
        self.assertNotIn("pass with spaces", repr(result))
        read.assert_called_once_with("telemetry", "https://gpu.example.invalid:443")

    def test_invalid_url_userinfo_and_plain_http_password_auth_are_rejected(self):
        for endpoint in ("https://name:password@gpu.example.invalid/", "https://bad host/", "invalid"):
            with self.subTest(endpoint=endpoint), self.assertRaises(telemetry_auth.TelemetryAuthError):
                self.save(endpoint=endpoint)
        with self.assertRaisesRegex(telemetry_auth.TelemetryAuthError, "HTTPS"):
            self.save(endpoint="http://gpu.example.invalid/api")

    def test_remove_only_current_site_password_in_separate_namespace(self):
        with patch.object(ai_credentials, "delete_secret") as remove:
            telemetry_auth.delete_password(ENDPOINT)
        remove.assert_called_once_with("telemetry", "https://gpu.example.invalid:443")
        self.assertNotEqual(ai_credentials._secret_target("telemetry", "https://gpu.example.invalid:443"),
                            ai_credentials._target("https://gpu.example.invalid:443"))

    def test_missing_saved_password_never_sends_http(self):
        self.save(password="")
        collector = self.collector()
        with patch.object(ai_credentials, "read_secret", return_value=""):
            with self.assertRaisesRegex(TelemetryAuthenticationRequired, "尚未保存"):
                collector.collect()
        collector._session.get.assert_not_called()

    def test_broken_preferences_allow_gui_config_fallback_but_auth_fails_closed(self):
        path = self.root / ".ui/preferences.yaml"
        path.parent.mkdir()
        path.write_text("telemetry: [\n", encoding="utf-8")
        self.assertEqual(telemetry_auth.effective_endpoint(ENDPOINT, self.root), ENDPOINT)
        collector = self.collector()
        with self.assertRaisesRegex(TelemetryAuthenticationRequired, "个人设置读取失败"):
            collector.collect()
        collector._session.get.assert_not_called()

    def test_basic_auth_applies_only_to_request_and_secret_is_not_in_headers(self):
        self.save()
        collector = self.collector()
        response = Mock(status_code=200)
        response.json.return_value = {"items": []}
        collector._session.get.return_value = response
        with patch.object(ai_credentials, "read_secret", return_value="private-password"):
            self.assertEqual(collector.collect(), [])
        self.assertEqual(collector._session.get.call_args.kwargs["auth"], ("monitor-user", "private-password"))
        self.assertNotIn("Authorization", collector._session.headers)
        response.close.assert_called_once()

    def test_401_is_site_auth_error_one_request_and_no_url_or_password_echo(self):
        collector = self.collector()
        response = Mock(status_code=401, headers={})
        response.raise_for_status.side_effect = requests.HTTPError("secret-password " + ENDPOINT)
        collector._session.get.return_value = response
        with self.assertRaises(TelemetryAuthenticationRequired) as error:
            collector.collect()
        self.assertIn("HTTP 401", str(error.exception))
        self.assertNotIn("private-query", str(error.exception))
        self.assertNotIn("secret-password", str(error.exception))
        collector._session.get.assert_called_once()
        response.raise_for_status.assert_not_called()
        response.close.assert_called_once()
        with self.assertRaisesRegex(TelemetryUnavailable, "本轮未发送请求"):
            collector.collect()
        collector._session.get.assert_called_once()

    def test_network_exception_does_not_echo_credential_or_query(self):
        collector = self.collector()
        collector._session.get.side_effect = requests.ConnectionError("secret-password " + ENDPOINT)
        with self.assertRaises(TelemetryUnavailable) as error:
            collector.collect()
        self.assertNotIn("secret-password", str(error.exception))
        self.assertNotIn("private-query", str(error.exception))

    def test_namespaced_secret_preserves_password_spaces_and_ai_token_wrapper(self):
        api = Mock()
        captured = []
        def write(pointer, flags):
            credential = ctypes.cast(pointer, ctypes.POINTER(ai_credentials._Credential)).contents
            captured.append((credential.TargetName,
                             ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)))
            return True
        api.CredWriteW.side_effect = write
        with patch.object(ai_credentials, "_api", return_value=api):
            ai_credentials.save_secret("telemetry", "https://gpu.example.invalid:443", " pass with spaces ")
        self.assertEqual(captured, [("autodl-watcher/telemetry/https://gpu.example.invalid:443", b" pass with spaces ")])
        with patch.object(ai_credentials, "save_secret") as save:
            ai_credentials.save("https://ai.example.invalid/v1", "  key-value  ")
        save.assert_called_once_with("llm", "https://ai.example.invalid/v1", "key-value")

    def test_settings_password_input_is_masked_without_reading_saved_secret(self):
        root = tk.Tk()
        root.withdraw()
        try:
            parent = ttk.Frame(root)
            host = SimpleNamespace(app=SimpleNamespace(config=SimpleNamespace(
                telemetry=SimpleNamespace(endpoint=ENDPOINT)), local=self.root / ".ui"),
                save_telemetry_settings=lambda: None, delete_telemetry_password=lambda: None)
            with patch.object(ai_credentials, "read_secret") as read:
                GlobalSettings.build_telemetry_settings(host, parent)
            entries = [child for box in parent.winfo_children() for child in box.winfo_children()
                       if isinstance(child, ttk.Entry)]
            password_entry = next(child for child in entries
                                  if str(child.cget("textvariable")) == str(host.telemetry_password))
            self.assertEqual(password_entry.cget("show"), "●")
            self.assertEqual(host.telemetry_password.get(), "")
            read.assert_not_called()
        finally:
            root.destroy()
