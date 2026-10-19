from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime
import io
from pathlib import Path
import unittest
from unittest.mock import Mock

from autodl_watcher.config import load_config
from autodl_watcher.models import OccupancyRecord, PlatformHost
from autodl_watcher.occupancy import OccupancyObserver


class OccupancyObserverTest(unittest.TestCase):
    def test_repeated_absence_rearms_without_losing_identity_or_reporting_failure(self):
        config = load_config(Path(__file__).parent / "fixtures/monitor.yaml")
        target = config.auto_start.targets[0]
        config = replace(config, auto_start=replace(config.auto_start, targets=(target,)))
        host = PlatformHost("gpu-203", 0, 2, (target.machine_name,), ((target.machine_name, 0, 2),))
        def record(user, index):
            return OccupancyRecord(datetime.now(), host.host, target.machine_name, index,
                                   f"GPU-{index}", "test GPU", True, target.instance_uuid, user, "")
        collector, evaluator = Mock(), Mock()
        collector.collect_occupancy.side_effect = [[record(config.occupancy.self_user, 0), record("other", 1)],
                                                   [record("other", 0), record("other", 1)],
                                                   [record("other", 0), record("other", 1)]]
        observer = OccupancyObserver(config, collector, evaluator, Mock(), host.host, False)
        with redirect_stdout(io.StringIO()) as output:
            observer.collect(host, [], False, lambda: False)
            self.assertTrue(observer.owned_instances)
            observer.collect(host, [], False, lambda: False)
            self.assertTrue(observer.owned_instances)  # 一次缺席不能解除本人占用。
            observer.collect(host, [], False, lambda: False)
        self.assertFalse(observer.owned_instances)
        self.assertTrue(observer.self_occupancy_known)
        self.assertFalse(observer.last_occupancy_failed)
        self.assertFalse(observer.occupancy_identity_unresolved)
        self.assertIn("本人占用已连续确认结束", output.getvalue())
        self.assertIn("已关闭（仅监控）", output.getvalue())
        evaluator.rearm_host.assert_called_once_with(host.host)
