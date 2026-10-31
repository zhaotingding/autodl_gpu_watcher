"""平台空位与新鲜采样确认；显存和利用率只作展示。"""
from dataclasses import replace
from datetime import datetime, timedelta
import unittest

from autodl_watcher.config import MonitorConfig
from autodl_watcher.evaluator import AvailabilityEvaluator
from autodl_watcher.models import GpuSample, PlatformHost


class EvaluatorTest(unittest.TestCase):
    def setUp(self):
        self.monitor = MonitorConfig(10, 0, 1, 20, 90, 1)
        self.base = datetime(2026, 7, 20, 11, 0, 0)
        self.platform = [PlatformHost("gpu-202", 1, 1)]

    def sample(self, offset=0, used=24576, util=100, index=1):
        return GpuSample("gpu-202", index, "NVIDIA TITAN RTX", util, used, 24576,
                         self.base + timedelta(seconds=offset))

    def test_full_memory_and_high_util_do_not_override_platform_slot(self):
        evaluator = AvailabilityEvaluator(self.monitor)
        alerts = evaluator.evaluate(self.platform, [self.sample()], self.base)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].confirmed_gpus[0].memory_used_mb, 24576)
        self.assertEqual(alerts[0].confirmed_gpus[0].util_pct, 100)

    def test_no_alert_when_platform_has_no_slot(self):
        evaluator = AvailabilityEvaluator(self.monitor)
        self.assertEqual(evaluator.evaluate([PlatformHost("gpu-202", 0, 1)], [self.sample()], self.base), [])

    def test_stale_or_future_samples_cannot_confirm_availability(self):
        for offset in (-100, 10):
            with self.subTest(offset=offset):
                evaluator = AvailabilityEvaluator(self.monitor)
                self.assertEqual(evaluator.evaluate(self.platform, [self.sample(offset)], self.base), [])

    def test_continuous_confirmation_and_sample_gap_still_apply(self):
        evaluator = AvailabilityEvaluator(replace(self.monitor, confirmation_seconds=10, min_idle_samples=2))
        self.assertEqual(evaluator.evaluate(self.platform, [self.sample()], self.base), [])
        self.assertEqual(len(evaluator.evaluate(self.platform, [self.sample(10)], self.base + timedelta(seconds=10))), 1)
        evaluator = AvailabilityEvaluator(replace(self.monitor, confirmation_seconds=10, min_idle_samples=2))
        evaluator.evaluate(self.platform, [self.sample()], self.base)
        self.assertEqual(evaluator.evaluate(self.platform, [self.sample(30)], self.base + timedelta(seconds=30)), [])

    def test_deduplicates_same_available_episode(self):
        evaluator = AvailabilityEvaluator(self.monitor)
        total = sum(len(evaluator.evaluate(self.platform, [self.sample(offset)],
                                          self.base + timedelta(seconds=offset)))
                    for offset in (0, 10, 20))
        self.assertEqual(total, 1)

    def test_platform_becoming_full_rearms_next_available_episode(self):
        evaluator = AvailabilityEvaluator(self.monitor)
        self.assertEqual(len(evaluator.evaluate(self.platform, [self.sample()], self.base)), 1)
        evaluator.evaluate([PlatformHost("gpu-202", 0, 1)], [], self.base + timedelta(seconds=10))
        self.assertEqual(len(evaluator.evaluate(self.platform, [self.sample(20)], self.base + timedelta(seconds=20))), 1)

    def test_rearm_host_allows_second_alert(self):
        evaluator = AvailabilityEvaluator(self.monitor)
        evaluator.evaluate(self.platform, [self.sample()], self.base)
        self.assertEqual(evaluator.rearm_host("gpu-202"), 1)
        self.assertEqual(len(evaluator.evaluate(self.platform, [self.sample(10)], self.base + timedelta(seconds=10))), 1)

    def test_rearm_host_does_not_change_other_hosts(self):
        evaluator = AvailabilityEvaluator(self.monitor)
        evaluator.evaluate(self.platform, [self.sample()], self.base)
        self.assertEqual(evaluator.rearm_host("gpu-203"), 0)
        self.assertEqual(evaluator.evaluate(self.platform, [self.sample(10)], self.base + timedelta(seconds=10)), [])

    def test_all_sample_indices_are_candidates_regardless_of_memory_usage(self):
        evaluator = AvailabilityEvaluator(self.monitor)
        alerts = evaluator.evaluate(self.platform, [self.sample(index=index) for index in range(4)], self.base)
        self.assertEqual(alerts[0].physical_total_count, 4)
        self.assertEqual(alerts[0].actionable_count, 4)
        self.assertEqual([gpu.gpu_index for gpu in alerts[0].confirmed_gpus], [0, 1, 2, 3])

    def test_unrelated_telemetry_host_never_alerts(self):
        evaluator = AvailabilityEvaluator(self.monitor)
        other = GpuSample("gpu-176", 0, "A100", 0, 300, 40960, self.base)
        self.assertEqual(evaluator.evaluate(self.platform, [other], self.base), [])


if __name__ == "__main__":
    unittest.main()
