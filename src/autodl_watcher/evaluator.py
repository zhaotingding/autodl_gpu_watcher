from __future__ import annotations  # 按平台空位和新鲜样本确认可用事件，不设置显存或利用率门槛。

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import MonitorConfig
from .models import AvailabilityAlert, ConfirmedGpu, GpuSample, PlatformHost


@dataclass(slots=True)
class _GpuRuntime:  # 每张 GPU 的运行时状态。
    idle_samples: deque[GpuSample]  # 连续满足条件的采样队列
    consecutive_busy: int = 0       # 连续不满足条件的次数
    alerted: bool = False           # 是否已告警（去重）


class AvailabilityEvaluator:  # 平台空位事件确认器；GPU 指标只用于展示和身份对齐。

    def __init__(self, monitor: MonitorConfig) -> None:
        self.monitor = monitor
        self._runtime: dict[tuple[str, int], _GpuRuntime] = defaultdict(  # _runtime[(host, gpu_index)] -> _GpuRuntime
            lambda: _GpuRuntime(idle_samples=deque())
        )

    def _record(self, sample: GpuSample, available: bool) -> None:  # 连续性只由平台空位与样本新鲜度决定。
        key = (sample.host, sample.gpu_index)  # 以物理主机和 GPU INDEX 联合标识一张卡，不能只用入口名或索引。
        runtime = self._runtime[key]  # 首次遇到该卡时 defaultdict 自动创建空队列和未告警状态。

        if available:
            if runtime.idle_samples:  # 满足条件 → 追加到空闲队列
                gap = (sample.observed_at - runtime.idle_samples[-1].observed_at).total_seconds()
                if gap > self.monitor.max_sample_gap_seconds:
                    runtime.idle_samples.clear()  # 间隔过大 → 重置（说明中间有数据缺失）
            runtime.idle_samples.append(sample)  # 保存达标样本的时间信息，后续同时检查样本数与持续时间。
            while len(runtime.idle_samples) > max(self.monitor.min_idle_samples + 2, 8):  # 限制队列长度，避免内存无限增长
                runtime.idle_samples.popleft()
            runtime.consecutive_busy = 0  # 新的达标状态或显式重新武装会清除连续忙碌计数。
        else:
            runtime.idle_samples.clear()  # 不满足条件 → 清空空闲记录
            runtime.consecutive_busy += 1
            if runtime.consecutive_busy >= self.monitor.reset_busy_samples:
                runtime.alerted = False  # 连续忙碌足够多次 → 重置告警状态，允许再次触发

    def _confirmed(self, sample: GpuSample, now: datetime) -> ConfirmedGpu | None:  # 确认新鲜样本的连续观察区间。
        key = (sample.host, sample.gpu_index)  # 以物理主机和 GPU INDEX 联合标识一张卡，不能只用入口名或索引。
        runtime = self._runtime[key]  # 首次遇到该卡时 defaultdict 自动创建空队列和未告警状态。

        age = (now - sample.observed_at).total_seconds()  # 比较采样时间与当前本地时间，拒绝陈旧或明显来自未来的数据。
        if age < -5 or age > self.monitor.stale_after_seconds:
            return None
        if len(runtime.idle_samples) < self.monitor.min_idle_samples:  # 平台空位仍须达到配置的连续确认次数。
            return None

        first = runtime.idle_samples[0]
        last = runtime.idle_samples[-1]
        available_seconds = int((last.observed_at - first.observed_at).total_seconds())  # 持续时间来自队列两端的采样时间差，不是轮数乘设定间隔。
        if available_seconds < self.monitor.confirmation_seconds:
            return None

        return ConfirmedGpu(
            host=last.host,
            gpu_index=last.gpu_index,
            gpu_name=last.gpu_name,
            util_pct=last.util_pct,
            memory_used_mb=last.memory_used_mb,
            memory_total_mb=last.memory_total_mb,
            idle_seconds=available_seconds,
            observed_at=last.observed_at,
        )

    def evaluate(
        self,
        platform_hosts: list[PlatformHost],
        gpu_samples: list[GpuSample],
        now: datetime,
    ) -> list[AvailabilityAlert]:  # 评估当前所有 GPU 的状态，生成开机事件列表。
        platform_by_host = {item.host: item for item in platform_hosts}  # 按物理主机核对账号可见的实时空位。
        latest_by_gpu: dict[tuple[str, int], GpuSample] = {}  # 本轮每张卡保留最后遇到的样本；并不在此按时间重新排序。

        for sample in gpu_samples:  # Step 1: 记录所有采样
            platform = platform_by_host.get(sample.host)
            fresh = -5 <= (now - sample.observed_at).total_seconds() <= self.monitor.stale_after_seconds
            self._record(sample, fresh and platform is not None and platform.free_count > 0)
            latest_by_gpu[(sample.host, sample.gpu_index)] = sample

        for key, runtime in self._runtime.items():
            platform = platform_by_host.get(key[0])
            if key not in latest_by_gpu and (platform is None or platform.free_count <= 0):
                runtime.idle_samples.clear()  # 平台无空位时，候选过滤器可能已经移除了全部样本。
                runtime.consecutive_busy += 1
                if runtime.consecutive_busy >= self.monitor.reset_busy_samples:
                    runtime.alerted = False  # 空位重新出现后可以形成新的可用事件。

        physical_indices_by_host: dict[str, set[int]] = defaultdict(set)  # Step 2: 统计各主机的物理 GPU INDEX 数
        for host, gpu_index in latest_by_gpu:
            physical_indices_by_host[host].add(gpu_index)

        confirmed_by_host: dict[str, list[ConfirmedGpu]] = defaultdict(list)  # Step 3: 检查各 GPU 是否已确认达标
        for key, sample in latest_by_gpu.items():
            confirmed = self._confirmed(sample, now)
            if confirmed is not None:
                confirmed_by_host[key[0]].append(confirmed)

        alerts: list[AvailabilityAlert] = []  # Step 4: 组装告警（双层门控 + 去重）
        for host, confirmed_gpus in confirmed_by_host.items():
            platform = platform_by_host.get(host)
            if platform is None or platform.free_count <= 0:
                continue  # 门控 1: 平台必须有空位

            unalerted = [  # 去重: 只取尚未 alert 的 GPU
                gpu
                for gpu in confirmed_gpus
                if not self._runtime[(gpu.host, gpu.gpu_index)].alerted
            ]
            if not unalerted:
                continue

            actionable_count = len(unalerted)  # 记录本次新达标的卡数；它不是要启动的实例数量。
            if actionable_count <= 0:
                continue

            alerts.append(
                AvailabilityAlert(
                    host=host,
                    platform_free_count=platform.free_count,
                    platform_total_count=platform.total_count,
                    physical_total_count=len(physical_indices_by_host.get(host, set())),
                    actionable_count=actionable_count,
                    confirmed_gpus=tuple(sorted(unalerted, key=lambda x: x.gpu_index)),
                    platform_sources=platform.source_names,
                    platform_slots=platform.source_slots,
                )
            )
            for gpu in unalerted:  # 标记已告警，防止重复触发
                self._runtime[(gpu.host, gpu.gpu_index)].alerted = True

        return alerts

    def rearm_host(self, host: str) -> int:  # 清除指定物理主机上所有 GPU 的 ``alerted`` 去重标志，
        reset_count = 0
        for (runtime_host, _gpu_index), runtime in self._runtime.items():
            if runtime_host != host:
                continue
            if runtime.alerted:
                reset_count += 1
            runtime.alerted = False  # 只解除该卡的告警去重，不删除已有的连续采样队列。
            runtime.consecutive_busy = 0  # 新的达标状态或显式重新武装会清除连续忙碌计数。
        return reset_count

    def export_state(self) -> dict[str, Any]:  # 将运行时状态导出为可序列化的字典（用于 state.json 持久化）。
        result: dict[str, Any] = {}
        for (host, gpu_index), runtime in self._runtime.items():
            result[f"{host}|{gpu_index}"] = {  # JSON 的键必须是字符串，因此把联合键编码为 host|index。
                "consecutive_busy": runtime.consecutive_busy,
                "alerted": runtime.alerted,
                "idle_samples": [
                    {
                        "host": sample.host,
                        "gpu_index": sample.gpu_index,
                        "gpu_name": sample.gpu_name,
                        "util_pct": sample.util_pct,
                        "memory_used_mb": sample.memory_used_mb,
                        "memory_total_mb": sample.memory_total_mb,
                        "observed_at": sample.observed_at.isoformat(),  # datetime 转为 ISO 文本后才能直接序列化为 JSON。
                    }
                    for sample in runtime.idle_samples
                ],
            }
        return result

    def import_state(self, state: dict[str, Any]) -> None:  # 从字典恢复运行时状态（进程重启后恢复连续采样上下文）。
        for key, raw in state.items():
            try:
                host, gpu_index_text = key.rsplit("|", 1)  # 恢复持久化联合键，并在创建运行状态时把索引转回整数。
                runtime = _GpuRuntime(
                    idle_samples=deque(
                        GpuSample(
                            host=item["host"],
                            gpu_index=int(item["gpu_index"]),
                            gpu_name=item["gpu_name"],
                            util_pct=float(item["util_pct"]),
                            memory_used_mb=int(item["memory_used_mb"]),
                            memory_total_mb=int(item["memory_total_mb"]),
                            observed_at=datetime.fromisoformat(item["observed_at"]),
                        )
                        for item in raw.get("idle_samples", [])
                    ),
                    consecutive_busy=int(raw.get("consecutive_busy", 0)),
                    alerted=bool(raw.get("alerted", False)),
                )
                self._runtime[(host, int(gpu_index_text))] = runtime
            except (KeyError, TypeError, ValueError):
                continue  # 单条恢复失败不影响其他 GPU
