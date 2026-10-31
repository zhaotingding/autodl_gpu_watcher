"""逐GPU展示与实际采样周期回归；不访问平台或训练服务器。"""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autodl_watcher.gpu_resources import resource_rows, server_state
from autodl_watcher.models import GpuSample, PlatformHost
from autodl_watcher.session import GPU_PREFIX, emit_gpu_samples


NOW = datetime(2026, 10, 31, 12, 0)
ENTRY = "autodl-203-1"


def sample(index=0, **updates):
    result = dict(host=ENTRY, gpu_index=index, util_pct=75, memory_used_mb=8192,
                  memory_total_mb=32768, observed_at=(NOW - timedelta(seconds=5)).isoformat())
    return result | updates


def snapshot(**updates):
    return dict(samples=[sample()], stale_after_seconds=90, poll_seconds=120,
                collected_at=NOW.isoformat(), next_sample_at=(NOW + timedelta(seconds=115)).isoformat(),
                error="") | updates


class GpuResourceDisplayTest(unittest.TestCase):
    def test_120_second_poll_keeps_values_after_90_seconds_without_false_expiry(self):
        value = snapshot()
        row = resource_rows(ENTRY, value, 90, NOW + timedelta(seconds=110))[0]
        self.assertEqual((row["memory_pct"], row["util_pct"], row["used_mb"], row["total_mb"]),
                         (25, 75, 8192, 32768))
        self.assertFalse(row["stale"])
        self.assertEqual(row["state"], "正常")
        self.assertEqual(server_state(ENTRY, value, 90, NOW + timedelta(seconds=110)), "正常")

    def test_delayed_next_round_warns_without_erasing_last_gpu_numbers(self):
        value = snapshot()
        row = resource_rows(ENTRY, value, 90, NOW + timedelta(seconds=146))[0]
        self.assertTrue(row["update_delayed"])
        self.assertFalse(row["source_stale"])
        self.assertEqual((row["state"], row["memory_pct"], row["util_pct"]), ("等待更新", 25, 75))

    def test_provider_already_stale_at_fetch_is_not_hidden_by_long_poll(self):
        value = snapshot(samples=[sample(observed_at=(NOW - timedelta(seconds=200)).isoformat())], poll_seconds=7200)
        row = resource_rows(ENTRY, value, 90, NOW)[0]
        self.assertTrue(row["source_stale"])
        self.assertEqual(row["state"], "数据源延迟")
        self.assertEqual(row["memory_pct"], 25)

    def test_clock_anomaly_is_distinct_even_after_future_sample_time_passes(self):
        value = snapshot(samples=[sample(observed_at=(NOW + timedelta(seconds=8)).isoformat())])
        row = resource_rows(ENTRY, value, 90, NOW + timedelta(seconds=20))[0]
        self.assertTrue(row["clock_skew"])
        self.assertEqual(row["state"], "时间异常")
        self.assertFalse(row["source_stale"])

    def test_failure_and_refresh_keep_previous_values_and_original_fetch_metadata(self):
        for changes, expected in (({"error": "HTTP 401认证未通过"}, "采集失败 · 保留上次数据"),
                                  ({"refreshing": True}, "采集中 · 上次数据")):
            value = snapshot(**changes)
            row = resource_rows(ENTRY, value, 90, NOW + timedelta(seconds=10))[0]
            self.assertEqual((row["util_pct"], row["memory_pct"]), (75, 25))
            self.assertEqual(server_state(ENTRY, value, 90, NOW + timedelta(seconds=10)), expected)
            self.assertEqual(value["collected_at"], NOW.isoformat())

    def test_manual_or_legacy_snapshots_wait_for_refresh_without_claiming_provider_delay(self):
        for value in (snapshot(poll_seconds=None, next_sample_at=None), snapshot(collected_at=None, next_sample_at=None)):
            row = resource_rows(ENTRY, value, 90, NOW + timedelta(seconds=130))[0]
            self.assertTrue(row["update_delayed"])
            self.assertFalse(row["source_stale"])
            self.assertEqual((row["state"], row["memory_pct"]), ("等待更新", 25))

    def test_every_gpu_is_sorted_and_entry_samples_override_physical_fallback(self):
        physical = sample(9, host="gpu-203", util_pct=99)
        value = snapshot(samples=[sample(2), sample(0), sample(1), physical])
        rows = resource_rows(ENTRY, value, 90, NOW)
        self.assertEqual([row["gpu_index"] for row in rows], [0, 1, 2])
        self.assertFalse(any(row["physical"] for row in rows))
        fallback = snapshot(samples=[physical])
        self.assertTrue(resource_rows(ENTRY, fallback, 90, NOW)[0]["physical"])
        self.assertIn("物理机", server_state(ENTRY, fallback, 90, NOW))

    def test_invalid_metrics_are_unknown_and_do_not_discard_other_gpus(self):
        value = snapshot(samples=[sample(0, util_pct=float("nan")), sample(1)])
        invalid, valid = resource_rows(ENTRY, value, 90, NOW)
        self.assertTrue(invalid["invalid"])
        self.assertIsNone(invalid["util_pct"])
        self.assertIsNone(invalid["memory_pct"])
        self.assertEqual(valid["memory_pct"], 25)
        self.assertEqual(server_state(ENTRY, value, 90, NOW), "部分数据无效")

    def test_no_gpu_rows_do_not_invent_zero_percent_and_auth_reason_is_clear(self):
        self.assertEqual(resource_rows(ENTRY, snapshot(samples=[]), 90, NOW), [])
        self.assertEqual(server_state(ENTRY, snapshot(samples=[]), 90, NOW), "等待采样")
        self.assertEqual(server_state(ENTRY, snapshot(samples=[], error="HTTP 401认证未通过"), 90, NOW), "需配置认证")

    def test_timezone_aware_source_times_are_compared_as_actual_instants(self):
        local = NOW.astimezone()
        utc = (local - timedelta(seconds=5)).astimezone(timezone.utc)
        row = resource_rows(ENTRY, snapshot(samples=[sample(observed_at=utc.isoformat())]), 90, NOW)[0]
        self.assertFalse(row["stale"])


class GpuSnapshotTimingTest(unittest.TestCase):
    def test_snapshot_preserves_source_time_and_explicit_runtime_schedule(self):
        samples = [GpuSample("gpu-203", 0, "Test GPU", 50, 1000, 32000, NOW - timedelta(seconds=5))]
        with redirect_stdout(io.StringIO()) as output:
            emit_gpu_samples(samples, [PlatformHost("gpu-203", 0, 2)], 90, collected_at=NOW,
                             poll_seconds=120, next_sample_at=NOW + timedelta(seconds=115))
        data = json.loads(output.getvalue()[len(GPU_PREFIX):])
        self.assertEqual(data["samples"][0]["observed_at"], samples[0].observed_at.isoformat())
        self.assertEqual((data["collected_at"], data["poll_seconds"], data["next_sample_at"]),
                         (NOW.isoformat(), 120, (NOW + timedelta(seconds=115)).isoformat()))

    def test_failure_message_does_not_claim_a_new_successful_fetch_or_deadline(self):
        with redirect_stdout(io.StringIO()) as output:
            emit_gpu_samples([], [], 90, "采集失败", collected_at=NOW, poll_seconds=120,
                             next_sample_at=NOW + timedelta(seconds=120))
        data = json.loads(output.getvalue()[len(GPU_PREFIX):])
        self.assertEqual(data["samples"], [])
        for field in ("collected_at", "poll_seconds", "next_sample_at"):
            self.assertNotIn(field, data)

    def test_monitor_records_fetch_before_later_occupancy_work_and_uses_real_poll(self):
        from autodl_watcher.main import main
        clock = {"now": NOW}
        class WallClock:
            @staticmethod
            def now():
                return clock["now"]
        def collect_gpu():
            clock["now"] = NOW + timedelta(seconds=5)
            return [GpuSample("gpu-999", 0, "Test GPU", 50, 1000, 32000, NOW)]
        def collect_occupancy(*args, **kwargs):
            clock["now"] = NOW + timedelta(seconds=30)
            return []
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as output, \
             patch("autodl_watcher.monitor.datetime", WallClock), \
             patch("autodl_watcher.monitor.PlatformBrowserCollector") as platform, \
             patch("autodl_watcher.monitor.TelemetryApiCollector") as telemetry:
            platform.return_value.collect.return_value = [PlatformHost("gpu-999", 0, 2,
                ("autodl-999-1",), (("autodl-999-1", 0, 2),))]
            platform.return_value.collect_occupancy.side_effect = collect_occupancy
            telemetry.return_value.collect.side_effect = collect_gpu
            main(["--config", str(Path(__file__).parent / "fixtures/monitor.yaml"), "--host", "999", "--entry", "1",
                  "--user", "测试用户", "--poll-seconds", "120", "--ui", "--monitor-only", "--max-cycles", "1",
                  "--runtime-dir", directory, "--no-login"])
        data = next(json.loads(line[len(GPU_PREFIX):]) for line in output.getvalue().splitlines() if line.startswith(GPU_PREFIX))
        self.assertEqual(data["collected_at"], (NOW + timedelta(seconds=5)).isoformat())
        self.assertEqual(data["next_sample_at"], (NOW + timedelta(seconds=120)).isoformat())
        self.assertEqual(data["poll_seconds"], 120)
        self.assertEqual(clock["now"], NOW + timedelta(seconds=30))
        telemetry.return_value.collect.assert_called_once()  # 更新时间元数据不增加采集请求。


if __name__ == "__main__":
    unittest.main()
