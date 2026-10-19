import queue
import threading
import time
import unittest
from unittest.mock import patch

from autodl_watcher.training_config import TrainingServer
from autodl_watcher.training_tracker import TrainingTracker
from autodl_watcher.training_service import TrainingService
from training_helpers import wait_training
from test_training import LOG, snapshot


class BackgroundTrainingTest(unittest.TestCase):
    def setUp(self):
        self.service = TrainingService(queue.Queue())
        self.server = TrainingServer("s", "offline", "offline")
        self.service.register(self.server)
        self.addCleanup(lambda: wait_training(self.service))

    def test_stop_does_not_wait_for_owned_connection_and_busy_tracks_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        connection = self.service.states["s"]["connection"]
        def close():
            entered.set()
            release.wait(2)
            connection.closed = True
        with patch.object(connection, "close", side_effect=close):
            try:
                self.service.stop("s")
                self.assertTrue(entered.wait(1))
                self.assertFalse(release.is_set())
                self.assertTrue(self.service.busy())
                self.assertTrue(self.service.stopping("s"))
                with self.assertRaisesRegex(ValueError, "收尾"):
                    self.service.start("s", 60)
            finally:
                release.set()
                wait_training(self.service)
        self.assertFalse(self.service.busy())
        self.service.start("s", 60)
        self.assertFalse(self.service.states["s"]["connection"].closed)

    def test_raw_response_parses_off_caller_thread_and_notifies_on_receive_thread(self):
        main_thread = threading.get_ident()
        parsed_threads, applied_threads = [], []
        original = TrainingTracker.update
        def update(tracker, value):
            parsed_threads.append(threading.get_ident())
            return original(tracker, value)
        self.service.on_snapshot = lambda server_id, value: applied_threads.append(threading.get_ident())
        with patch.object(TrainingTracker, "update", update):
            self.service.receive(("s", 0, snapshot(), ""))
            wait_training(self.service)
        self.assertTrue(parsed_threads)
        self.assertNotIn(main_thread, parsed_threads)
        self.assertEqual(applied_threads, [main_thread])
        self.assertEqual(self.service.snapshot("s")["cards"][0]["progress"]["step"], 2696)

    def test_stop_during_parse_discards_tracker_changes_and_old_error(self):
        state = self.service.states["s"]
        state["snapshot"] = state["tracker"].update(snapshot())
        prior_tracker, prior_snapshot = state["tracker"], state["snapshot"]
        entered, release = threading.Event(), threading.Event()
        original = TrainingTracker.update
        def update(tracker, value):
            entered.set()
            release.wait(2)
            return original(tracker, value)
        with patch.object(TrainingTracker, "update", update):
            self.service.receive(("s", 0, snapshot(LOG + "RuntimeError: old failure"), ""))
            try:
                self.assertTrue(entered.wait(1))
                self.service.stop("s")
                self.assertTrue(self.service.busy())
            finally:
                release.set()
                wait_training(self.service)
        self.assertIs(state["tracker"], prior_tracker)
        self.assertIs(state["snapshot"], prior_snapshot)
        self.assertFalse(state["snapshot"]["cards"][0]["progress"]["error"])

    def test_reparse_coalesces_during_sample_and_never_collects_again(self):
        state = self.service.states["s"]
        state["snapshot"] = state["tracker"].update(snapshot())
        self.service.start("s", 60)
        connection = state["connection"]
        entered, release = threading.Event(), threading.Event()
        def collect(previous):
            entered.set()
            release.wait(2)
            return snapshot(LOG.replace("2696", "3000"))
        with patch.object(connection, "collect", side_effect=collect) as sample:
            self.service.tick()
            try:
                self.assertTrue(entered.wait(1))
                for _ in range(10):
                    self.service.reparse("s")
                    self.service.tick()
            finally:
                release.set()
                wait_training(self.service)
            sample.assert_called_once()
        self.assertEqual(state["snapshot"]["cards"][0]["progress"]["step"], 3000)
        self.assertFalse(state["busy"])
        self.assertGreater(state["due"], time.monotonic())

    def test_rule_reparse_requested_while_ssh_closes_runs_after_cleanup(self):
        state = self.service.states["s"]
        state["snapshot"] = state["tracker"].update(snapshot())
        original_stamp = state["snapshot"]["received_at"]
        entered, release = threading.Event(), threading.Event()
        connection = state["connection"]
        def close():
            entered.set()
            release.wait(2)
        with patch.object(connection, "close", side_effect=close), patch.object(connection, "collect") as collect:
            self.service.stop("s")
            try:
                self.assertTrue(entered.wait(1))
                self.service.reparse("s")
                self.assertTrue(state["reparse_pending"])
            finally:
                release.set()
                wait_training(self.service)
            collect.assert_not_called()
        self.assertFalse(state["reparse_pending"])
        self.assertEqual(state["snapshot"]["received_at"], original_stamp)
        self.assertEqual(state["snapshot"]["cards"][0]["progress"]["step"], 2696)

    def test_history_trims_only_old_raw_text_and_preserves_error_facts(self):
        tracker = TrainingTracker(self.server)
        for index in range(40):
            value = snapshot(LOG + "RuntimeError: failure-" + str(index), False)
            value["tasks"][0].update(key=f"boot:{index}:100", pid=index)
            tracker.update(value)
        self.assertEqual(len(tracker.references), 32)
        self.assertEqual(len(tracker.previous()), 32)
        self.assertEqual(len(tracker.known), 40)
        old = tracker.known["boot:0:100"]
        self.assertFalse(old["details"])
        self.assertIn("最近32条", old["details_note"])
        self.assertIn("failure-0", old["progress"]["error"])
        self.assertEqual(old["level"], "error")
        self.assertTrue(tracker.known["boot:39:100"]["details"])
        partial = tracker.update(dict(snapshot(), tasks=[], scan_ok=False))
        self.assertEqual(len(partial["cards"]), 40)
        self.assertTrue(all(card["level"] == "error" for card in partial["cards"]))

    def test_fork_preserves_failure_when_log_rolls_without_sharing_mutable_history(self):
        tracker = TrainingTracker(self.server)
        tracker.update(snapshot(LOG + "RuntimeError: CUDA out of memory"))
        copied = tracker.fork()
        value = snapshot();value["tasks"][0]["logs"] = []
        card = copied.update(value)["cards"][0]
        self.assertEqual(card["level"], "error")
        card["progress"]["error"] = "changed only in fork"
        self.assertIn("out of memory", tracker.known[card["key"]]["progress"]["error"])


if __name__ == "__main__":
    unittest.main()
