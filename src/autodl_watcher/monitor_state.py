"""监控状态的纯计算：占用身份、缺席确认、容量展示与近期状态恢复。

这里不创建浏览器、线程或磁盘文件；判定仅使用调用方传入的本轮事实。
"""
from dataclasses import dataclass
from datetime import datetime
import re

from .evaluator import AvailabilityEvaluator, sample_meets_capacity

_INSTANCE_UUID = re.compile(r"[0-9a-fA-F]{10}-[0-9a-fA-F]{8}")
_OCCUPANCY_ID_LINE = re.compile(r"([0-9a-fA-F]{10}-[0-9a-fA-F]{8})(?:[ \t]*(?:\((.*)\)|（(.*)）))?")
_STATE_RESTORE_MAX_AGE_SECONDS = 300

def _instance_bindings_from_cell(value: str) -> tuple[list[tuple[str, str]], bool]:  # 名字仅用于显示，归属仍核验完整 UUID。
    if not isinstance(value, str):
        return [], False
    bindings: list[tuple[str, str]] = []
    complete = True
    for line in value.splitlines():
        if not line.strip():
            continue
        match = _OCCUPANCY_ID_LINE.fullmatch(line.strip())
        if match is None:
            complete = False  # 畸形或截断 ID 不能证明本人缺席。
        else:
            name = (match.group(2) or match.group(3) or "").strip()
            bindings.append((match.group(1).lower(), "" if name in {"-", "—"} else name))
    return bindings, complete and bool(bindings)


def _occupancy_display_name(item) -> str:  # 昵称与 user 身份字段分开，缺名时不把 UUID 填回界面。
    return item.display_name or "用户名未知"


@dataclass(frozen=True, slots=True)
class _OwnedInstance:  # 当前账号本人已经占用的一个实例摘要。

    gpu_index: int
    instance_id: str


def _owned_instances_from_records(
    records,
    self_user: str,
    host: str,
) -> list[_OwnedInstance]:  # 从本轮占用明细中筛出当前账号本人正在运行的实例。
    if not self_user:
        return []

    latest_by_instance = {}  # 同一实例可能出现在多个入口，按实例 ID 保留最新观察。
    for item in records:
        if (
            not item.occupied or item.host != host or item.user.strip() != self_user or not item.instance_id
        ):
            continue
        previous = latest_by_instance.get(item.instance_id)
        if previous is None or item.observed_at >= previous.observed_at:
            latest_by_instance[item.instance_id] = item

    return sorted(
        (
            _OwnedInstance(
                gpu_index=item.gpu_index,
                instance_id=item.instance_id,
            )
            for item in latest_by_instance.values()
        ),
        key=lambda item: (item.gpu_index, item.instance_id),
    )


def _advance_absence_confirmation(
    *,
    previous_known: bool,
    previous_active: bool,
    complete_snapshot: bool,
    captured_owned: bool,
    current_streak: int,
    required: int,
) -> tuple[int, bool]:  # 推进“本人已消失”的连续确认状态。
    required = max(1, int(required))
    if captured_owned:
        return 0, False
    if not complete_snapshot:  # 缺入口或采集失败不能作为本人下机的证据，并打断连续确认。
        return 0, False
    if previous_known and not previous_active:
        return max(current_streak, required), True
    streak = current_streak + 1
    confirmed = streak >= required
    return streak, confirmed  # 每轮均复核，返回连续次数及是否确认缺席。


def _format_entry_slots(platform_host) -> str:  # 格式化各入口的 GPU ID 空位，用于控制台展示，如 '203-1=0/2,203-2=1/2'。
    if platform_host is None or not platform_host.source_slots:
        return "无"
    return ",".join(
        f"{name.removeprefix('autodl-')}={idle}/{total}"
        for name, idle, total in platform_host.source_slots
    )


def _ready_samples(samples, thresholds):  # 筛选出满足显存容量条件的 GPU 样本，按 INDEX 排序。
    return [
        sample
        for sample in sorted(samples, key=lambda item: item.gpu_index)
        if sample_meets_capacity(sample, thresholds)
    ]


def _fresh_gpu_indices(samples, now, stale_after_seconds):  # 只有本轮全部样本新鲜时，才允许辅助校验占用表中的额外空行。
    if not samples or any(not -5 <= (now - item.observed_at).total_seconds() <= stale_after_seconds for item in samples):
        return None
    indices = {item.gpu_index for item in samples}
    return indices if len(indices) == len(samples) else None  # 重复 INDEX 表明快照不可靠，不能据此删掉占用表的额外行。


def _format_ready_indices(samples, thresholds) -> str:  # 格式化达标 GPU 索引，用于控制台展示，如 '#0(21728MB),#1(21736MB)'。
    ready = _ready_samples(samples, thresholds)
    if not ready:
        return "无"
    return ",".join( f"#{sample.gpu_index}({sample.memory_free_mb}MB)" for sample in ready )


def _parse_local_iso(value: object) -> datetime | None:  # 解析 state 中的本地 ISO 时间；无效值返回 None。
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _persisted_state_is_fresh(
    persisted: dict,
    now: datetime,
    max_age_seconds: int = _STATE_RESTORE_MAX_AGE_SECONDS,
) -> bool:  # 仅恢复很近的 evaluator 状态，避免数小时/数天前的 alerted 永久锁死。
    saved_at = _parse_local_iso(persisted.get("saved_at"))
    if saved_at is None:
        return False
    age = (now - saved_at).total_seconds()
    return -5 <= age <= max_age_seconds


def _restore_recent_evaluator_state(
    evaluator: AvailabilityEvaluator,
    persisted: dict,
    capacity_fingerprint: str,
    now: datetime,
    host: str,
) -> tuple[bool, int]:  # 恢复近期连续采样，但无条件清除跨进程 alerted 锁。
    if persisted.get("capacity_fingerprint") != capacity_fingerprint:  # 用户、目标或阈值变了，旧连续达标记录就不能沿用。
        return False, 0
    if not _persisted_state_is_fresh(persisted, now):
        return False, 0
    evaluator.import_state(persisted.get("evaluator", {}))
    return True, evaluator.rearm_host(host)  # 保留近期采样连续性，但清除上次进程的已触发锁。
