from __future__ import annotations  # 登录启动方式的单元测试。

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autodl_watcher.login import edge_executable_candidates, find_edge_executable


class LoginTest(unittest.TestCase):
    def test_candidates_include_standard_program_files_location(self) -> None:
        with patch.dict( os.environ, {"PROGRAMFILES(X86)": r"C:\Program Files (x86)"}, clear=True, ), patch("autodl_watcher.login.shutil.which", return_value=None):
            candidates = edge_executable_candidates()

        self.assertIn(
            Path(r"C:\Program Files (x86)")
            / "Microsoft"
            / "Edge"
            / "Application"
            / "msedge.exe",
            candidates,
        )

    def test_find_edge_returns_first_existing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            edge = Path(temp_dir) / "msedge.exe"
            edge.write_bytes(b"")
            with patch(
                "autodl_watcher.login.edge_executable_candidates",
                return_value=(Path(temp_dir) / "missing.exe", edge),
            ):
                self.assertEqual(find_edge_executable(), edge)

    def test_find_edge_returns_none_when_not_installed(self) -> None:
        with patch(
            "autodl_watcher.login.edge_executable_candidates",
            return_value=(Path("missing-edge.exe"),),
        ):
            self.assertIsNone(find_edge_executable())


class LoginProfileCleanupV051Test(unittest.TestCase):
    def test_remove_stale_profile_locks(self) -> None:
        from autodl_watcher.login import remove_stale_profile_locks

        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            for name in ("SingletonLock", "SingletonCookie", "lockfile", "DevToolsActivePort"):
                (profile / name).write_text("stale", encoding="utf-8")
            (profile / "Cookies").write_text("keep", encoding="utf-8")

            remove_stale_profile_locks(profile)

            self.assertFalse((profile / "SingletonLock").exists())
            self.assertFalse((profile / "SingletonCookie").exists())
            self.assertFalse((profile / "lockfile").exists())
            self.assertFalse((profile / "DevToolsActivePort").exists())
            self.assertTrue((profile / "Cookies").exists())



class LoginV051Test(unittest.TestCase):
    def test_login_profile_is_separate_from_browser_profile(self) -> None:
        from autodl_watcher.login import login_profile_dir

        browser = Path("runtime") / "browser_profile"
        self.assertEqual(login_profile_dir(browser), Path("runtime") / "native_login_profile")

    def test_automation_flags_detected_from_edge_command_lines(self) -> None:
        from autodl_watcher.login import automation_flags_present

        with patch(
            "autodl_watcher.login.edge_process_command_lines",
            return_value=[ r'"msedge.exe" --user-data-dir=D:\runtime\login_profile --new-window' ],
        ):
            self.assertFalse(automation_flags_present(Path(r"D:\runtime\login_profile")))

        with patch(
            "autodl_watcher.login.edge_process_command_lines",
            return_value=[
                r'"msedge.exe" --no-sandbox --remote-debugging-pipe --user-data-dir=D:\runtime\login_profile'
            ],
        ):
            self.assertTrue(automation_flags_present(Path(r"D:\runtime\login_profile")))

class LoginWorkflowV051Test(unittest.TestCase):
    def test_interactive_login_uses_clean_profile_then_syncs(self) -> None:
        from types import SimpleNamespace
        from autodl_watcher.login import interactive_login

        with tempfile.TemporaryDirectory() as temp_dir:
            browser_profile = Path(temp_dir) / "browser_profile"
            config = SimpleNamespace(
                platform=SimpleNamespace(
                    user_data_dir=browser_profile,
                    page_url="https://private.autodl.test/landing",
                )
            )
            edge_path = Path(temp_dir) / "msedge.exe"
            edge_path.write_bytes(b"")

            with patch("autodl_watcher.login.find_edge_executable", return_value=edge_path), \
                 patch("autodl_watcher.login.terminate_profile_edge_processes") as terminate, \
                 patch("autodl_watcher.login.wait_for_profile_edge_exit", return_value=True), \
                 patch("autodl_watcher.login.launch_native_edge") as launch, \
                 patch("autodl_watcher.login.automation_flags_present", return_value=False), \
                 patch("autodl_watcher.login.sync_login_profile_to_browser") as sync, \
                 patch("autodl_watcher.login.time.sleep"), \
                 patch("builtins.input", return_value=""):
                interactive_login(config, reason="manual")

            clean_profile = browser_profile.parent / "native_login_profile"
            launch.assert_called_once_with(edge_path, clean_profile, config.platform.page_url)
            sync.assert_called_once_with(clean_profile, browser_profile)
            self.assertEqual([call.args[0] for call in terminate.call_args_list],
                             [browser_profile, clean_profile])  # 强制清理只发生在登录前，确认后不再杀源浏览器。

    def test_sync_login_profile_replaces_browser_profile_without_lock_files(self) -> None:
        from autodl_watcher.login import sync_login_profile_to_browser

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            login_profile = root / "login_profile"
            browser_profile = root / "browser_profile"
            login_profile.mkdir()
            browser_profile.mkdir()
            (login_profile / "Cookies").write_text("fresh", encoding="utf-8")
            (login_profile / "SingletonLock").write_text("lock", encoding="utf-8")
            (browser_profile / "Cookies").write_text("stale", encoding="utf-8")

            with patch("autodl_watcher.login.terminate_profile_edge_processes"), \
                 patch("autodl_watcher.login.close_profile_edge_windows"), \
                 patch("autodl_watcher.login.wait_for_profile_edge_exit", return_value=True):
                sync_login_profile_to_browser(login_profile, browser_profile)

            self.assertEqual((browser_profile / "Cookies").read_text(encoding="utf-8"), "fresh")
            self.assertFalse((browser_profile / "SingletonLock").exists())


class LoginProfileHandoffTest(unittest.TestCase):
    def test_running_login_profile_is_never_killed_or_copied(self):
        from autodl_watcher.login import sync_login_profile_to_browser
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "native", root / "monitor"
            source.mkdir()
            target.mkdir()
            (source / "Cookies").write_text("new", encoding="utf-8")
            (source / "SingletonLock").write_text("active", encoding="utf-8")
            (target / "Cookies").write_text("previous", encoding="utf-8")
            with patch("autodl_watcher.login.wait_for_profile_edge_exit", return_value=False), \
                 patch("autodl_watcher.login.close_profile_edge_windows"), \
                 patch("autodl_watcher.login.terminate_profile_edge_processes") as terminate, \
                 patch("autodl_watcher.login.shutil.copytree") as copy:
                with self.assertRaisesRegex(RuntimeError, "尚未退出"):
                    sync_login_profile_to_browser(source, target)
            terminate.assert_not_called()  # 不强杀刚完成认证的浏览器，也不触碰旧监控资料。
            copy.assert_not_called()
            self.assertEqual((target / "Cookies").read_text(encoding="utf-8"), "previous")
            self.assertTrue((source / "SingletonLock").exists())

    def test_login_window_disables_background_mode_without_automation_flags(self):
        from autodl_watcher.login import launch_native_edge, _AUTOMATION_FLAGS
        with tempfile.TemporaryDirectory() as directory, \
             patch("autodl_watcher.login.subprocess.Popen") as launch:
            launch_native_edge(Path("msedge.exe"), Path(directory), "https://private.autodl.test/landing")
        command = launch.call_args.args[0]
        self.assertIn("--disable-background-mode", command)
        self.assertFalse(any(flag in argument for flag in _AUTOMATION_FLAGS for argument in command))

    def test_process_query_failure_cannot_claim_profile_has_exited(self):
        from autodl_watcher.login import LocalStartupError, edge_process_command_lines
        with patch("autodl_watcher.login.os.name", "nt"), \
             patch("autodl_watcher.login._powershell_executable", return_value="powershell.exe"), \
             patch("autodl_watcher.login.subprocess.run") as run:
            run.return_value.returncode = 1
            with self.assertRaises(LocalStartupError):
                edge_process_command_lines(Path("unused-profile"))

    @unittest.skipUnless(os.name == "nt", "Windows专用窗口正常关闭")
    def test_normal_close_uses_owned_profile_without_force_or_public_browser(self):
        from autodl_watcher.login import close_profile_edge_windows
        with patch("autodl_watcher.login.subprocess.run") as run:
            run.return_value.returncode = 0
            close_profile_edge_windows(Path("watcher-login-only"))
        command = run.call_args.args[0]
        self.assertEqual(Path(command[0]).name, "powershell.exe")
        self.assertIn("CloseMainWindow()", command[-1])
        self.assertIn("watcher-login-only", command[-1])
        self.assertNotIn("Stop-Process", command[-1])
        self.assertEqual(run.call_args.kwargs["creationflags"], __import__('subprocess').CREATE_NO_WINDOW)

    def test_exclusive_cleanup_does_not_remove_locks_until_exit_is_confirmed(self):
        from autodl_watcher.login import prepare_profile_for_exclusive_use
        with patch("autodl_watcher.login.terminate_profile_edge_processes"), \
             patch("autodl_watcher.login.wait_for_profile_edge_exit", return_value=False), \
             patch("autodl_watcher.login.remove_stale_profile_locks") as cleanup:
            with self.assertRaisesRegex(RuntimeError, "尚未退出"):
                prepare_profile_for_exclusive_use(Path("unused-profile"))
        cleanup.assert_not_called()

    def test_synced_login_storage_can_be_read_after_normal_exit(self):
        from autodl_watcher.login import sync_login_profile_to_browser
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "native", root / "monitor"
            storage = source / "Default/Local Storage/leveldb"
            storage.mkdir(parents=True)
            (storage / "000001.log").write_bytes(b"synthetic fresh authentication")
            with patch("autodl_watcher.login.wait_for_profile_edge_exit", return_value=True), \
                 patch("autodl_watcher.login.close_profile_edge_windows") as close, \
                 patch("autodl_watcher.login.terminate_profile_edge_processes") as terminate:
                sync_login_profile_to_browser(source, target)
            self.assertEqual((target / "Default/Local Storage/leveldb/000001.log").read_bytes(),
                             b"synthetic fresh authentication")
            close.assert_called_once_with(source)  # 确认后主动请求正常关窗，结束后再同步。
            self.assertEqual([call.args[0] for call in terminate.call_args_list], [target])


class LoginSessionVerificationTest(unittest.TestCase):
    def verify_login(self, error=None, exit_code=None):
        import io
        import json
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from autodl_watcher.login import main
        from autodl_watcher.session import SESSION_PREFIX

        config = SimpleNamespace(platform=SimpleNamespace())
        with redirect_stdout(io.StringIO()) as output, \
             patch("autodl_watcher.login.load_config", return_value=config), \
             patch("autodl_watcher.login.interactive_login") as login, \
             patch("autodl_watcher.collectors.PlatformBrowserCollector") as collector:
            collector.return_value.collect.side_effect = error
            if exit_code is None:
                main()
            else:
                with self.assertRaises(SystemExit) as stopped:
                    main()
                self.assertEqual(stopped.exception.code, exit_code)
            collector.return_value.collect.assert_called_once()
            login.assert_called_once_with(config, reason="manual")
            collector.return_value.close.assert_called_once()
        return [json.loads(line[len(SESSION_PREFIX):]) for line in output.getvalue().splitlines()
                if line.startswith(SESSION_PREFIX)]

    def test_copied_profile_requires_successful_api_verification(self):
        statuses = self.verify_login()
        self.assertEqual([item["status"] for item in statuses], ["checking", "valid"])
        self.assertTrue(statuses[-1]["checked_at"])

    def test_expired_profile_reports_invalid_without_claiming_success(self):
        from autodl_watcher.collectors import PlatformAuthenticationError
        statuses = self.verify_login(PlatformAuthenticationError("HTTP 401"), 3)
        self.assertEqual([item["status"] for item in statuses], ["checking", "invalid"])

    def test_network_failure_cannot_be_classified_as_expired_login(self):
        from autodl_watcher.collectors import PlatformTransientError
        statuses = self.verify_login(PlatformTransientError("response body unavailable"), 4)
        self.assertEqual([item["status"] for item in statuses], ["checking", "unknown"])


    def test_profile_sync_failure_reports_unknown_without_unhandled_worker_error(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from autodl_watcher.login import main
        with redirect_stdout(io.StringIO()) as output, \
             patch("autodl_watcher.login.load_config", return_value=SimpleNamespace(platform=SimpleNamespace())), \
             patch("autodl_watcher.login.interactive_login", side_effect=EOFError("login confirmation closed")), \
             patch("autodl_watcher.collectors.PlatformBrowserCollector") as collector:
            with self.assertRaises(SystemExit) as stopped:
                main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertIn('"status": "unknown"', output.getvalue())
            self.assertNotIn('"status": "valid"', output.getvalue())
            collector.assert_not_called()


class PowerShellPathTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows PowerShell 进程测试")
    def test_profile_helpers_work_without_powershell_on_path(self):
        from autodl_watcher.login import edge_process_command_lines, terminate_profile_edge_processes
        with tempfile.TemporaryDirectory(prefix="autodl-path-regression-") as directory:
            profile = Path(directory) / "unused-profile"  # 专用空目录，不匹配任何用户浏览器。
            with patch.dict(os.environ, {"PATH": str(Path(os.environ["SystemRoot"]) / "System32")}):
                self.assertEqual(edge_process_command_lines(profile), [])
                terminate_profile_edge_processes(profile)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell 路径测试")
    def test_both_profile_helpers_use_system_path_and_hidden_window(self):
        import subprocess
        from autodl_watcher.login import edge_process_command_lines, terminate_profile_edge_processes
        expected = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        with patch("autodl_watcher.login.subprocess.run") as run:
            run.return_value.returncode, run.return_value.stdout = 0, b""
            edge_process_command_lines(Path("unused-profile"))
            terminate_profile_edge_processes(Path("unused-profile"))
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(Path(call.args[0][0]), expected)
            self.assertEqual(call.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell 缺失测试")
    def test_missing_powershell_reports_path_before_touching_profile(self):
        from autodl_watcher.login import LocalStartupError, prepare_profile_for_exclusive_use
        with patch("autodl_watcher.login.Path.is_file", return_value=False), \
             patch("autodl_watcher.login.subprocess.run") as run, \
             patch("autodl_watcher.login.remove_stale_profile_locks") as cleanup:
            with self.assertRaises(LocalStartupError) as error:
                prepare_profile_for_exclusive_use(Path("unused-profile"))
        self.assertIn("powershell.exe", str(error.exception))
        self.assertIn("未找到 Windows PowerShell", str(error.exception))
        run.assert_not_called()
        cleanup.assert_not_called()

    def test_login_reports_local_startup_failure_without_marking_session_expired(self):
        from contextlib import redirect_stdout
        import io
        from autodl_watcher.login import LocalStartupError, main
        with redirect_stdout(io.StringIO()) as output, \
             patch("autodl_watcher.login.interactive_login", side_effect=LocalStartupError("未找到 Windows PowerShell：missing/powershell.exe")), \
             patch("autodl_watcher.collectors.PlatformBrowserCollector") as factory:
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 5)
        self.assertIn("本地启动失败", output.getvalue())
        self.assertIn("powershell.exe", output.getvalue())
        self.assertNotIn('"status": "invalid"', output.getvalue())
        self.assertNotIn("网络", output.getvalue())
        factory.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows PowerShell 编码边界")
class PowerShellEncodingTest(unittest.TestCase):
    def test_unicode_profile_and_diagnostics_round_trip_without_reader_thread_crash(self):
        import threading
        from autodl_watcher import login
        profile = Path("C:/测试 用户/O'Connor/训练🚀/native_login_profile")
        line = f'msedge.exe --user-data-dir="{profile}" --new-window'
        original = login._edge_process_query_script
        def script(path, **kwargs):
            # 只在独立PowerShell中提供合成进程，不查询或操作用户浏览器。
            fixture = ("function Get-CimInstance { [Console]::Error.WriteLine('中文诊断：仅测试'); "
                       "[pscustomobject]@{Name='msedge.exe'; CommandLine='" + login._ps_quote(line) + "'} }; ")
            return fixture + original(path, **kwargs)
        errors = []
        with patch.object(login, "_edge_process_query_script", side_effect=script), \
             patch.object(threading, "excepthook", side_effect=lambda event: errors.append(event.exc_value)):
            try:
                self.assertEqual(login.edge_process_command_lines(profile), [line])
            finally:
                self.assertEqual(errors, [], "子进程读取线程不应自行崩溃")

    def test_localized_close_error_is_reported_without_reader_thread_crash(self):
        import threading
        from autodl_watcher import login
        original = login._edge_process_query_script
        def script(path, **kwargs):
            # 合成CIM错误直接返回非零；不会调用真实CloseMainWindow。
            return "function Get-CimInstance { throw '查询失败：仅测试'; }; " + original(path, **kwargs)
        errors = []
        with patch.object(login, "_edge_process_query_script", side_effect=script), \
             patch.object(threading, "excepthook", side_effect=lambda event: errors.append(event.exc_value)):
            with self.assertRaises(login.LocalStartupError):
                login.close_profile_edge_windows(Path("unused-profile"))
        self.assertEqual(errors, [], "中文错误也必须安全交回调用线程")

    def test_undecodable_process_query_preserves_both_profiles(self):
        import subprocess
        from autodl_watcher import login
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/"source", Path(directory)/"target"
            source.mkdir(); target.mkdir()
            (source/"Cookies").write_bytes(b"new-auth-test-data")
            (target/"Cookies").write_bytes(b"previous-auth-test-data")
            result = subprocess.CompletedProcess([], 0, stdout=b"\xd3", stderr=b"")
            with patch.object(login, "close_profile_edge_windows"), \
                 patch.object(login.subprocess, "run", return_value=result), \
                 patch.object(login, "remove_stale_profile_locks") as remove, \
                 patch.object(login.shutil, "copytree") as copy:
                with self.assertRaises(login.LocalStartupError):
                    login.sync_login_profile_to_browser(source, target)
            remove.assert_not_called(); copy.assert_not_called()
            self.assertEqual((source/"Cookies").read_bytes(), b"new-auth-test-data")
            self.assertEqual((target/"Cookies").read_bytes(), b"previous-auth-test-data")


if __name__ == "__main__":
    unittest.main()
