"""请求退出和结果落盘均在隔离目录测试，不连接模型或用户账本。"""
from pathlib import Path
import tempfile
import shutil
import tkinter as tk
import threading
import time
import unittest
from unittest.mock import patch

from autodl_watcher.ai_service import AIService
from autodl_watcher.training_config import TrainingServer
from autodl_watcher.training_ai import AnalysisControl, AnalysisError, analyze, interrupted_call, settings
from autodl_watcher.training_rules import RuleStore, task_identity
from test_training_ai import CONFIG, RESULT, SOURCE, sse
from test_training_ai_usage import CONFIG_PRICE, USAGE


class AILifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ai.yaml"
        self.server = TrainingServer("s", "server", "mock-host")
        self.card = {"key": "boot:10:20", "pid": 10, "log_path": "/mock/train.log"}
        self.release = threading.Event()
        self.finished = threading.Event()

    def tearDown(self):
        self.release.set()
        self.finished.wait(.2)
        self.temp.cleanup()

    def wait_for(self, predicate, seconds=2):
        due = time.monotonic() + seconds
        while not predicate() and time.monotonic() < due:
            time.sleep(.005)
        self.assertTrue(predicate())

    def call(self):
        return interrupted_call(settings(CONFIG_PRICE), time.time(), {
            "time": time.time(), "model": CONFIG["model"], "base_url": CONFIG["base_url"],
            "requested_tier": "flex", "tokens": {"input": 1000, "output": 300, "cached": 200, "write": 100}})

    def test_completed_request_is_saved_before_any_ui_poll(self):
        def analyzer(config, source, pid, **kwargs):
            return {"profile": RESULT, "call": self.call()}
        ai = AIService(self.path, analyzer)
        identity = ai.generate(self.server, self.card, SOURCE, CONFIG)
        self.wait_for(lambda: not ai.busy)
        self.assertEqual(RuleStore(ai.store.path).entries[identity]["profile"], RESULT)
        self.assertEqual(len(ai.usage.records()), 1)
        self.assertEqual(ai.poll(), {"s"})
        self.assertEqual(ai.poll(), set())

    def test_shutdown_rejects_new_requests_and_accepts_inflight_completion(self):
        def analyzer(config, source, pid, **kwargs):
            self.release.wait(2)
            return {"profile": RESULT, "call": self.call()}
        ai = AIService(self.path, analyzer)
        identity = ai.generate(self.server, self.card, SOURCE, CONFIG)
        ai.begin_shutdown()
        with self.assertRaisesRegex(AnalysisError, "退出"):
            ai.generate(self.server, self.card, SOURCE, CONFIG)
        self.assertFalse(ai.shutdown_complete())
        self.release.set()
        self.wait_for(ai.shutdown_complete)
        self.assertEqual(ai.store.entries[identity]["request_state"], "completed")
        self.assertEqual(len(ai.usage.records()), 1)

    def test_stalled_request_close_preserves_old_rule_and_known_usage(self):
        started = threading.Event()
        def analyzer(config, source, pid, control=None):
            control.publish(self.call())
            started.set()
            self.release.wait(3)  # 模拟尚无响应头、底层暂不能被取消的请求。
            self.finished.set()
            return {"profile": {"name": "late-result"}, "call": self.call()}
        ai = AIService(self.path, analyzer)
        identity = task_identity(self.server, self.card)
        ai.store.put(identity, {"profile": RESULT, "log_path": self.card["log_path"]})
        ai.generate(self.server, self.card, SOURCE, CONFIG_PRICE)
        self.assertTrue(started.wait(1))
        ai.begin_shutdown(grace_seconds=0)
        self.wait_for(ai.shutdown_complete)
        entry = RuleStore(ai.store.path).entries[identity]
        self.assertEqual(entry["request_state"], "interrupted")
        self.assertEqual(entry["profile"], RESULT)
        self.assertEqual(entry["call"]["tokens"]["input"], 1000)
        self.assertTrue(entry["attempted"])
        self.release.set(); self.assertTrue(self.finished.wait(1)); time.sleep(.02)
        self.assertEqual(len(ai.usage.records()), 1)
        self.assertEqual(ai.store.entries[identity]["profile"], RESULT)

    def test_cancelled_request_without_usage_is_unknown_and_not_retried(self):
        started = threading.Event()
        def analyzer(config, source, pid, control=None):
            started.set(); self.release.wait(3); self.finished.set()
            raise AnalysisError("late failure")
        ai = AIService(self.path, analyzer)
        identity = ai.generate(self.server, self.card, SOURCE, CONFIG_PRICE)
        self.assertTrue(started.wait(1))
        ai.begin_shutdown(grace_seconds=0)
        self.wait_for(ai.shutdown_complete)
        call = ai.usage.records()[0]
        self.assertEqual(call["status"], "请求中断")
        self.assertIsNone(call["tokens"]["input"])
        self.assertIsNone(call["cny"])
        self.assertEqual(ai.rule_entry(self.server, self.card)["request_state"], "interrupted")
        self.assertEqual(ai.store.entries[identity]["attempted"], True)

    def test_usage_metadata_is_captured_before_stream_failure_and_own_socket_shutdown(self):
        control = AnalysisControl()
        captured = []
        control.on_call = captured.append
        def lines():
            yield sse({"type": "response.in_progress", "response": {"usage": USAGE}})
            control.cancel()
            yield b"data: {}"
        with patch("autodl_watcher.training_ai.ai_credentials.read", return_value="mock-key"), patch("autodl_watcher.training_ai.requests.Session") as session:
            response = session.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_lines.side_effect = lines
            with self.assertRaises(AnalysisError) as error:
                analyze(CONFIG_PRICE, SOURCE, control=control)
            response.raw._connection.sock.shutdown.assert_called_once()
            self.assertEqual(session.return_value.__enter__.return_value.post.call_count, 1)
        self.assertEqual(error.exception.call["tokens"]["input"], 1000)
        self.assertEqual(error.exception.call["status"], "请求中断")
        self.assertEqual(captured[-1]["tokens"]["output"], 300)

    def test_slow_local_result_save_does_not_block_shutdown_poll(self):
        started = threading.Event()
        def analyzer(config, source, pid, **kwargs):
            return {"profile": RESULT, "call": self.call()}
        ai = AIService(self.path, analyzer)
        original = ai.usage.record
        def slow_record(call):
            started.set(); self.release.wait(2); original(call)
        with patch.object(ai.usage, "record", side_effect=slow_record):
            ai.generate(self.server, self.card, SOURCE, CONFIG_PRICE)
            self.assertTrue(started.wait(1))
            before = time.monotonic(); ai.begin_shutdown(0); self.assertFalse(ai.shutdown_complete())
            self.assertLess(time.monotonic() - before, .1)
            self.release.set(); self.wait_for(ai.shutdown_complete)
        self.assertEqual(len(ai.usage.records()), 1)


class AIGuiShutdownTest(unittest.TestCase):
    def test_actual_window_close_waits_for_billing_without_ai_tick_or_poll(self):
        from autodl_watcher import gui
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            shutil.copyfile(Path(__file__).parent / "fixtures/monitor.yaml", folder / "config.yaml")
            started, release, request_ended, destroyed = (threading.Event() for _ in range(4))
            def analyzer(config, source, pid, control=None):
                started.set()
                release.wait(4)
                request_ended.set()
                return {"profile": RESULT, "call": interrupted_call(settings(CONFIG_PRICE), time.time())}
            with patch.object(gui, "ROOT", folder), patch.object(gui.WatcherWindow, "refresh"), patch("autodl_watcher.training_ai_ui.analyze", side_effect=analyzer) as api:
                root = tk.Tk(); root.withdraw()
                window = gui.WatcherWindow(root)
                window.icons.sync_executable = lambda: None
                server = TrainingServer("s", "server", "mock-host")
                window.training.register(server)
                card = {"key": "boot:10:20", "pid": 10, "log_path": "/mock/train.log"}
                ai = window.training.ai
                ai.generate("s", card, SOURCE, CONFIG_PRICE)
                self.assertTrue(started.wait(1))
                original_destroy = root.destroy
                def destroy():
                    destroyed.set(); original_destroy()
                root.destroy = destroy
                try:
                    with patch.object(ai, "tick", side_effect=AssertionError("closing must not tick")), patch.object(ai, "poll", side_effect=AssertionError("billing must not require UI poll")):
                        before = time.monotonic(); window.close()
                        self.assertLess(time.monotonic() - before, .2)
                        self.assertFalse(destroyed.is_set())
                        release.set()
                        due = time.monotonic() + 3
                        while not destroyed.is_set() and time.monotonic() < due:
                            root.update(); time.sleep(.01)
                    self.assertTrue(destroyed.is_set())
                    self.assertEqual(len(ai.usage.records()), 1)
                    self.assertTrue(ai.store.entries[task_identity(server, card)]["profile"])
                    api.assert_called_once()
                finally:
                    release.set(); request_ended.wait(1)
                    if not destroyed.is_set():
                        for timer in root.tk.call("after", "info"):
                            root.after_cancel(timer)
                        original_destroy()


if __name__ == "__main__":
    unittest.main()
