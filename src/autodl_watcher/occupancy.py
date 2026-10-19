"""本轮占用核验及跨轮确认状态；不拥有独立计时器或连接。"""
from dataclasses import replace
from datetime import datetime
import time

from .collectors.platform import PlatformAuthenticationError, PlatformTransientError
from .monitor_state import (_INSTANCE_UUID, _advance_absence_confirmation, _fresh_gpu_indices,
                            _instance_bindings_from_cell, _occupancy_display_name,
                            _owned_instances_from_records)


class OccupancyObserver:
    """由平台总循环调用一次；失败/停止不能把用户判为空闲或下机。"""
    def __init__(self, config, collector, evaluator, logger, host, auto_start_enabled):
        self.config, self.collector, self.evaluator, self.logger = config, collector, evaluator, logger
        self.selected_host, self.self_user = host, config.occupancy.self_user.strip()
        self.auto_start_enabled = auto_start_enabled
        self.targets = sorted(
            (item for item in config.auto_start.targets if item.enabled and item.host == host),
            key=lambda item: (item.priority, item.machine_name, item.instance_uuid),
        )
        self.owned_instances = []
        self.self_occupancy_known = False
        self.pending_start_until = 0.0
        self.pending_start_machine = ""
        self.absence_confirmations = 0
        self.account_occupancy_conflict = False
        self.last_occupancy_failed = False
        self.occupancy_identity_unresolved = False

    def blocks_start(self, account_clear: bool, fresh_positive: bool = False) -> bool:
        """用当前占用事实做门控；采集前后都调用，不复用上一阶段的判定。"""
        owned_or_unknown = (  # 完整账号列表可以覆盖旧占用，但不能覆盖新冲突和未确认身份。
            (bool(self.targets) and not self.self_occupancy_known)
            or (self.self_occupancy_known and bool(self.owned_instances))
        )
        return (fresh_positive or self.account_occupancy_conflict  # 本轮直接看到本人有卡时始终阻断。
                or self.occupancy_identity_unresolved
                or (owned_or_unknown and not account_clear))

    def collect(self, platform_host, selected_gpu_samples, selected_account_clear, stop_requested):
        """返回 (入口摘要, 本轮本人有卡证据)，停止则返回 None 并丢弃不完整轮次。"""
        fresh_occupancy_positive = False  # 当前轮弹窗若直接看到本人有卡，优先于实例列表的相反结果。
        occupancy_summary = "无可核验入口"
        if self.targets:  # 占用与平台、显存统一按采样间隔读取。
            raw_occupancy = []  # 每轮重新收集原始入口视图，不把上一轮记录当作本轮证据。
            entry_summaries: dict[str, str] = {}
            failed_entries: list[str] = []
            slot_map = (
                {name: (idle, total) for name, idle, total in platform_host.source_slots}
                if platform_host is not None
                else {}
            )
            for occupancy_target in self.targets:  # 逐个入口点击“查看占用”。v0.5.4 会同时校验弹窗标题和 machine/list 的 idle/total，宁可采集失败也不允许 203-2 串到 203-1。
                if stop_requested():
                    return None  # 停止轮次不生成缺席或空闲证据。
                entry_name = occupancy_target.machine_name
                expected_idle = None
                expected_total = None
                if entry_name in slot_map:
                    expected_idle, expected_total = slot_map[entry_name]
                try:
                    entry_records = self.collector.collect_occupancy(
                        entry_name,
                        expected_idle=expected_idle,
                        expected_total=expected_total,
                        expected_gpu_indices=_fresh_gpu_indices(
                            selected_gpu_samples, datetime.now(), self.config.monitor.stale_after_seconds),
                    )
                    raw_occupancy.extend(replace(item, display_name=item.display_name or item.user)
                                         for item in entry_records)  # 旧七列用户名保留为展示名。
                    entry_summaries[entry_name] = ""  # 拆分多实例后再生成带名字的入口摘要。
                except PlatformAuthenticationError:
                    raise
                except Exception as entry_exc:
                    failed_entries.append(f"{entry_name.removeprefix('autodl-')}:{entry_exc}")
                    entry_summaries[entry_name] = "采集失败"
                    self.logger.exception(
                        "occupancy entry capture failed host=%s entry=%s",
                        self.selected_host,
                        entry_name,
                    )

            if stop_requested():
                return None  # 停止轮次不生成缺席或空闲证据。
            self.last_occupancy_failed = bool(failed_entries)
            unknown_rows = [item for item in raw_occupancy if item.occupied and not item.user.strip()]
            identity_unresolved_now = False
            if unknown_rows:  # 六列括号名不是身份证据，仍查询完整账号 UUID 列表。
                account_ids: set[str] = set()
                try:
                    account_rows = self.collector.get_account_instances()
                    if not isinstance(account_rows, list):
                        raise PlatformTransientError("账号实例列表不完整")
                    for row in account_rows:
                        instance_id = row.get("instance_uuid") if isinstance(row, dict) else None
                        if not isinstance(instance_id, str) or not _INSTANCE_UUID.fullmatch(instance_id):
                            raise PlatformTransientError("账号实例列表含无法识别的 UUID")
                        if instance_id.lower() in account_ids:
                            raise PlatformTransientError("账号实例列表有重复 UUID")
                        account_ids.add(instance_id.lower())
                except PlatformAuthenticationError:
                    raise
                except Exception as exc:
                    account_ids.clear()  # 部分列表不参与本人归属判断。
                    identity_unresolved_now = True
                    self.logger.warning("occupancy identity query failed host=%s: %s", self.selected_host, exc)
                identity_label = self.self_user or "当前账号"
                resolved_occupancy = []
                for item in raw_occupancy:
                    if not item.occupied or item.user.strip():  # 旧版七列的明确用户名继续沿用。
                        resolved_occupancy.append(item)
                        continue
                    bindings, complete_ids = _instance_bindings_from_cell(item.instance_id)
                    for instance_id, display_name in bindings:
                        resolved_occupancy.append(replace(
                            item, instance_id=instance_id, display_name=display_name,
                            user=identity_label if instance_id in account_ids else ""))
                    if not complete_ids:  # 原始畸形单元格保留供核验，但不向终端输出 ID。
                        identity_unresolved_now = True
                        resolved_occupancy.append(item)
                raw_occupancy = resolved_occupancy
            if identity_unresolved_now:
                self.occupancy_identity_unresolved = True
            try:  # 连续可靠空结果才确认本人已下机，瞬时缺行不能作为缺席证据。
                complete_occupancy_snapshot = not failed_entries and not identity_unresolved_now
                captured_owned = _owned_instances_from_records(
                    raw_occupancy,
                    self.self_user or "当前账号",
                    self.selected_host,
                )
                previous_known = self.self_occupancy_known
                previous_active = self.self_occupancy_known and bool(self.owned_instances)
                self.absence_confirmations, confirmed_absence = (
                    _advance_absence_confirmation(
                        previous_known=previous_known,
                        previous_active=previous_active,
                        complete_snapshot=complete_occupancy_snapshot,
                        captured_owned=bool(captured_owned),
                        current_streak=self.absence_confirmations,
                        required=self.config.occupancy.absent_confirmations_required,
                    )
                )

                if complete_occupancy_snapshot and (captured_owned or confirmed_absence):
                    if self.occupancy_identity_unresolved:
                        self.evaluator.rearm_host(self.selected_host)
                    self.occupancy_identity_unresolved = False
                if captured_owned:
                    fresh_occupancy_positive = True
                    if selected_account_clear:
                        self.account_occupancy_conflict = True  # 同轮两份正向/负向证据冲突，不能在下一次弹窗失败后贸然关机。
                    self.owned_instances = captured_owned  # 实时看到本人实例就是正向证据，无需等待多轮缺席确认。
                    self.self_occupancy_known = True
                    self.pending_start_until = 0.0
                    self.pending_start_machine = ""
                elif complete_occupancy_snapshot:
                    if confirmed_absence:  # 连续可靠空快照达到门槛后，才能解除本人占用状态。
                        if self.account_occupancy_conflict:
                            self.account_occupancy_conflict = False
                            self.evaluator.rearm_host(self.selected_host)  # 冲突消除后下一轮重新评估先前被拦截的空闲事件。
                        self.owned_instances = []
                        self.self_occupancy_known = True
                        pending_still_valid = (
                            self.pending_start_until > 0 and time.monotonic() < self.pending_start_until
                        )
                        if previous_active:
                            rearmed_gpu_count = self.evaluator.rearm_host(self.selected_host)
                            self.pending_start_until = 0.0
                            self.pending_start_machine = ""
                            rearm_line = (
                                f"[{datetime.now():%H:%M:%S}] 本人占用已连续确认结束 | "
                                f"主机={self.selected_host} | "
                                f"确认={self.absence_confirmations}/{self.config.occupancy.absent_confirmations_required} | "
                                f"已清除alerted={rearmed_gpu_count} | "
                                f"自动开机={'重新武装' if self.auto_start_enabled else '已关闭（仅监控）'}"
                            )
                            print(rearm_line, flush=True)
                            self.logger.warning(
                                "self occupancy ended after repeated confirmation; evaluator rearmed host=%s reset_alerted=%d user=%s confirmations=%d",
                                self.selected_host,
                                rearmed_gpu_count,
                                self.self_user,
                                self.absence_confirmations,
                            )
                        elif not previous_known and not pending_still_valid:
                            self.evaluator.rearm_host(self.selected_host)

                for entry, error in entry_summaries.items():
                    if not error:
                        entry_summaries[entry] = ",".join(
                            f"#{item.gpu_index}:{_occupancy_display_name(item)}"
                            for item in raw_occupancy if item.machine_name == entry and item.occupied) or "无"
                occupancy_summary = "; ".join(
                    f"{entry.removeprefix('autodl-')}[{text}]"
                    for entry, text in entry_summaries.items()) or "无数据"
            except Exception:
                occupancy_summary = "占用信息处理失败，本轮不可用"
                self.last_occupancy_failed = True
                self.occupancy_identity_unresolved = True
                self.logger.exception("live occupancy processing failed")

        return occupancy_summary, fresh_occupancy_positive
