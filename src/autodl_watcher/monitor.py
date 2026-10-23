"""平台监控用例：一条总循环拥有采集、核验、电源协调和资源收尾。"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


from . import __version__
from .autostart import AutoStartCoordinator, format_start_result, select_target
from .cli import normalize_entry, normalize_host, select_cli_targets
from .collectors import (
    PlatformAuthenticationError,
    PlatformBrowserCollector,
    PlatformTransientError,
    TelemetryApiCollector,
    filter_samples_to_platform_candidates,
)
from .collectors.telemetry import TelemetryUnavailable
from .collectors.platform import InstanceIdentityError
from .config import AutoStartTarget, validate_telemetry_endpoint
from .evaluator import AvailabilityEvaluator
from .login import LocalStartupError, interactive_login
from .notifiers import EmailNotifier
from .state_store import JsonStateStore
from .session import REPORT_PREFIX, emit_gpu_samples, emit_session


from .monitor_state import (_format_entry_slots, _format_ready_indices, _ready_samples,
                            _restore_recent_evaluator_state)
from .occupancy import OccupancyObserver

_ANSI_BRIGHT_RED = "\033[91m"
_ANSI_BRIGHT_GREEN = "\033[92m"
_ANSI_RESET = "\033[0m"
_POWER_ON_CONFIRM_GRACE_SECONDS = 120

def _enable_ansi_colors() -> None:  # 在 Windows 控制台中启用 ANSI 转义序列，使开机触发行能够显示颜色。
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        stdout_handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(stdout_handle, mode.value | 0x0004)
    except Exception:
        return  # 颜色只是辅助显示；启用失败不能影响监控和自动开机主链路。


def _red_terminal_text(text: str) -> str:  # 仅在交互式终端中为文本包裹亮红色 ANSI 控制码。
    if not sys.stdout.isatty():
        return text
    return f"{_ANSI_BRIGHT_RED}{text}{_ANSI_RESET}"


def _green_terminal_text(text: str) -> str:  # 仅在交互式终端中为文本包裹亮绿色 ANSI 控制码。
    if not sys.stdout.isatty():
        return text
    return f"{_ANSI_BRIGHT_GREEN}{text}{_ANSI_RESET}"


def _setup_logging(log_file: str) -> logging.Logger:  # 配置滚动日志文件，最大 2MB，保留 3 个备份。
    logger = logging.getLogger("autodl_watcher")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger  # 避免重复添加 handler

    handler = RotatingFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,  # 单文件上限 2MB
        backupCount=3,              # 保留 3 个备份
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


def run_monitor(config, args, config_path, parser):  # 独立监控入口，保留 CLI 和 UI 共用的业务链路。

    try:
        validate_telemetry_endpoint(config.telemetry.endpoint)  # 创建浏览器、日志和电源协调器之前拒绝缺失配置。
        selected_host = normalize_host(args.host)       # "203" → "gpu-203"
        selected_entry = normalize_entry(args.entry, selected_host)  # "auto" / "2" / "203-2"
        try:
            selected_targets = select_cli_targets(config.auto_start, selected_host, selected_entry)
        except ValueError:
            if args.monitor_only and selected_entry is None:
                selected_targets = ()  # 首次安装可只读查看主机；没有实例配置不赋予电源资格。
            else:
                if not (args.dry_run or args.monitor_only) or selected_entry is None:
                    raise
                target = AutoStartTarget(selected_host, selected_entry, "", "gpu", 100, True)
                selected_targets = (target,)
                config = replace(config, auto_start=replace(config.auto_start,
                    targets=(*config.auto_start.targets, target)))
    except ValueError as exc:
        parser.error(str(exc))

    if args.convert_no_gpu and (selected_entry is None or not args.live):
        parser.error("无卡转有卡须同时指定固定 --entry 和 --live")
    if args.convert_no_gpu and (len(selected_targets) != 1 or selected_targets[0].start_mode != "gpu"
                                or not config.auto_start.verify_before_start):
        parser.error("无卡转有卡仅支持一个有卡固定实例，并须启用开机前二次确认")
    dry_run = config.auto_start.dry_run
    if args.dry_run or args.monitor_only:
        dry_run = True
    elif args.live:
        dry_run = False

    selected_auto_start = replace(  # 用选中的 targets 替换配置中的全量 targets
        config.auto_start,
        enabled=config.auto_start.enabled and not args.monitor_only,
        dry_run=dry_run,
        targets=selected_targets,
    )

    config.runtime.log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = _setup_logging(str(config.runtime.log_file))

    evaluator = AvailabilityEvaluator(config.monitor, config.idle_thresholds)
    state_store = JsonStateStore(config.runtime.state_file)
    persisted = state_store.load()
    target_signature = ",".join(item.machine_name for item in selected_targets)
    capacity_fingerprint = (  # 拼接关键配置形成签名字符串，用于判断历史状态是否仍适用；不是哈希值。
        f"v0.6|user={config.occupancy.self_user}|host={selected_host}|targets={target_signature}|"
        f"util={config.idle_thresholds.gpu_util_check_enabled}:"
        f"{config.idle_thresholds.gpu_util_max_pct}|"
        f"free={config.idle_thresholds.memory_free_min_mb}:"
        f"{config.idle_thresholds.memory_free_min_ratio}|"
        f"confirm={config.monitor.confirmation_seconds}:"
        f"{config.monitor.min_idle_samples}|dry_run={dry_run}"
    )
    state_now = datetime.now()
    state_restored, restored_alerted = _restore_recent_evaluator_state(
        evaluator,
        persisted,
        capacity_fingerprint,
        state_now,
        selected_host,
    )
    if state_restored:
        if restored_alerted:
            logger.info(
                "restored evaluator continuity but reset stale alerted host=%s count=%d",
                selected_host,
                restored_alerted,
            )
    elif persisted:
        logger.info(
            "skip stale evaluator state saved_at=%s fingerprint_match=%s",
            persisted.get("saved_at"),
            persisted.get("capacity_fingerprint") == capacity_fingerprint,
        )

    email_notifier = None
    if config.notification.email_enabled:
        email_notifier = EmailNotifier(config.notification.subject_prefix)

    platform_collector = PlatformBrowserCollector(config.platform)   # 浏览器 → AutoDL 平台
    telemetry_collector = TelemetryApiCollector(config.telemetry)    # HTTP   → Telemetry API
    self_user = config.occupancy.self_user.strip()

    occupancy = OccupancyObserver(config, platform_collector, evaluator, logger, selected_host, selected_auto_start.enabled)
    login_validation_pending = False
    previous_account_clear = False

    starter = AutoStartCoordinator(
        selected_auto_start,
        config.monitor,
        config.idle_thresholds,
        platform_collector,
        telemetry_collector,
        convert_no_gpu=args.convert_no_gpu,
    )

    auto_mode = "仅监控（自动开机已关闭，不执行开机或关机）"
    if selected_auto_start.enabled:
        auto_mode = "DRY-RUN" if selected_auto_start.dry_run else "真实开机"
        if args.convert_no_gpu:
            auto_mode += "；目标达标时关闭本账号全部无卡实例释放额度"

    retry_action = "跳过开机，等待重试" if selected_auto_start.enabled else "等待重试（仅监控，不执行电源操作）"

    entry_mode = (
        "自动选择[" + ", ".join(item.machine_name for item in selected_targets) + "]"
        if selected_entry is None
        else f"固定 {selected_targets[0].machine_name}"
    )

    print(f"AutoDL GPU Watcher v{__version__}")
    print(f"代码路径：{Path(__file__).resolve().parent}")
    print(f"配置路径：{config_path}")
    print(f"目标主机：{selected_host}")
    print(f"入口策略：{entry_mode}")
    print(f"模式：{auto_mode}；每 {config.monitor.poll_seconds} 秒采样；Ctrl+C 安全停止。")
    print(
        "显存门槛：可用显存 >= "
        f"max({config.idle_thresholds.memory_free_min_mb} MB, "
        f"总显存×{config.idle_thresholds.memory_free_min_ratio:.0%})；"
        "GPU Util 仅记录。"
    )
    print(
        "Telemetry容错：单次超时 "
        f"{config.telemetry.timeout_seconds} 秒；瞬时失败后等待 "
        f"{config.telemetry.retry_delay_seconds:g} 秒重试；"
        f"每次采集最多 {min(config.telemetry.max_attempts, 2)} 次请求；连续失败退避60/120/240/300秒，限流遵循Retry-After。"
    )
    if config.platform.autodl_direct:
        print( "AutoDL网络：watcher 控制面直连，绕过系统代理；" "Telemetry 仍可使用本机 HTTP_PROXY。" )
    print(f"监控日志：{config.runtime.log_file}")
    print(f"实时占用：随每轮采样刷新；本人用户={self_user or '未配置'}")

    monitor_entry = selected_entry.removeprefix("autodl-") if selected_entry else f"{selected_host}（自动选择）"
    cycles = 0
    stop_requested = lambda: args.stop_file is not None and args.stop_file.exists()
    try:
        while not stop_requested():
            cycle_start = time.monotonic()  # 本轮开始时间（用于控制循环间隔）

            platform_session_verified = False
            try:
                all_platform_hosts = platform_collector.collect()  # 1a. 从 AutoDL 控制台采集平台级 GPU ID 空位（Playwright 控制浏览器）
                if stop_requested():
                    break
                platform_session_verified = True
                emit_session("valid", "登录会话有效，平台主机接口已核验")
                if login_validation_pending:
                    print( f"[{datetime.now():%H:%M:%S}] 登录验证成功，平台主机接口已恢复，继续监控。", flush=True, )
                    logger.info("platform login recovery verified by machine/list success")
                    login_validation_pending = False
                platform_hosts = [
                    item for item in all_platform_hosts if item.host == selected_host
                ]
                if not platform_hosts or (selected_entry and selected_entry not in platform_hosts[0].source_names):
                    raise PlatformTransientError("所选主机或入口不在当前账号的实时主机列表中")
                selected_instance_state = None
                selected_instance_error = False
                selected_instance_missing = False
                if selected_entry is not None and selected_targets[0].instance_uuid:  # 所选实例状态按UUID查询，不由弹窗来源推断。
                    target = selected_targets[0]
                    try:
                        selected_instance_state = platform_collector.get_instance_state(
                            target.instance_uuid, target.machine_name)
                    except PlatformAuthenticationError:
                        raise
                    except Exception as exc:
                        selected_instance_error = True
                        selected_instance_missing = isinstance(exc, InstanceIdentityError)
                        logger.warning("selected instance state query failed host=%s entry=%s: %s",
                                       selected_host, target.machine_name, exc)
                selected_account_clear = bool(
                    isinstance(selected_instance_state, dict)
                    and selected_instance_state.get("host_account_gpu_clear") is True
                    and (selected_instance_state.get("status") == "shutdown" or (
                        selected_instance_state.get("status") == "running"
                        and selected_instance_state.get("start_mode") == "non_gpu"))
                )
                if selected_account_clear and not previous_account_clear:
                    evaluator.rearm_host(selected_host)  # 先前被未知占用抑制的达标事件可再次触发。
                previous_account_clear = selected_account_clear
                if selected_instance_error:
                    selected_instance_text = (
                        "配置实例不存在或ID已过期" if selected_instance_missing else "查询失败"
                    )
                elif not isinstance(selected_instance_state, dict):
                    selected_instance_text = "未配置实例" if not selected_targets or not selected_targets[0].instance_uuid else "待确认"
                else:
                    status, mode = selected_instance_state.get("status"), selected_instance_state.get("start_mode")
                    selected_instance_text = (
                        "无卡运行" if (status, mode) == ("running", "non_gpu")
                        else "有卡运行" if (status, mode) == ("running", "gpu")
                        else "已关机" if status == "shutdown" else f"{status}/{mode}"
                    )
                all_gpu_samples = telemetry_collector.collect()  # 1b. 从 Telemetry API 采集物理 GPU 显存快照（HTTP GET）
                if stop_requested():
                    break
                selected_gpu_samples = [
                    item for item in all_gpu_samples if item.host == selected_host
                ]
                gpu_samples = filter_samples_to_platform_candidates(  # 1c. 平台有入口空位且当前账号可见的物理 GPU 才进入评估。
                    selected_gpu_samples, platform_hosts,
                )[0]

                now = datetime.now()
                trigger_events = evaluator.evaluate(platform_hosts, gpu_samples, now)  # evaluator 内部维护 idle_samples 队列，判断哪些 GPU 已连续达标

                platform_host = platform_hosts[0] if platform_hosts else None
                entry_slots = _format_entry_slots(platform_host)              # 各入口空闲 GPU ID 数
                ready_samples = _ready_samples(gpu_samples, config.idle_thresholds)  # 显存达标的 GPU
                self_active = occupancy.self_occupancy_known and bool(occupancy.owned_instances) and not selected_account_clear  # 新鲜完整的账号列表可覆盖旧弹窗记录；本轮正向弹窗仍在发送前阻断。
                pending_start = (
                    occupancy.pending_start_until > 0 and time.monotonic() < occupancy.pending_start_until
                )
                if occupancy.pending_start_until > 0 and not pending_start:
                    reset_count = evaluator.rearm_host(selected_host)  # 超过确认宽限期仍未看到本人实例：认为开机没有真正落地，重新武装。
                    logger.warning(
                        "power_on confirmation expired; rearmed host=%s machine=%s reset_alerted=%d",
                        selected_host,
                        occupancy.pending_start_machine or "unknown",
                        reset_count,
                    )
                    occupancy.pending_start_until = 0.0
                    occupancy.pending_start_machine = ""

                if occupancy.blocks_start(selected_account_clear) or pending_start or starter.pending_switch or starter.quota_blocked:  # 先按已知占用门控；下方采集后还会再核对。
                    trigger_events = []

                planned_target = None  # 按实时空位选择候选实例；状态未知时不执行开机。
                if selected_auto_start.enabled and platform_host is not None and not self_active:
                    planned_target = select_target(
                        selected_auto_start,
                        selected_host,
                        platform_slots=platform_host.source_slots,
                    )

                if trigger_events and planned_target is None and not self_active:  # v0.5.4：evaluator 看到的是物理主机聚合空位。固定 203-2 时， 203-1 有空位也可能生成 trigger；如果当前选定 targets 实际无可用实例， 必须在这里吃掉事件，不能每 10 秒刷“没有可用固定实例”。
                    evaluator.rearm_host(selected_host)
                    trigger_events = []

                observation = occupancy.collect(platform_host, selected_gpu_samples,
                                                selected_account_clear, stop_requested)
                if observation is None:
                    break
                occupancy_summary, fresh_occupancy_positive = observation

                pending_start = (  # 占用状态发生变化后再次做安全门控。首次 UNKNOWN、本人 ACTIVE、 或 power_on 等待确认期间都不允许发送新的开机请求。
                    occupancy.pending_start_until > 0 and time.monotonic() < occupancy.pending_start_until
                )
                if (
                    occupancy.blocks_start(selected_account_clear, fresh_occupancy_positive)  # 重新读取本轮采集后的事实，不能复用前一次结果。
                    or pending_start
                    or starter.pending_switch
                    or starter.quota_blocked
                ):
                    trigger_events = []

                if email_notifier is not None:
                    for event in trigger_events:
                        email_notifier.send(event)

                if selected_auto_start.enabled and not stop_requested():
                    results = []
                    if starter.pending_switch:  # 关机后的状态推进不依赖 evaluator 再次触发。
                        if not occupancy.account_occupancy_conflict and not occupancy.occupancy_identity_unresolved and not fresh_occupancy_positive and not (occupancy.owned_instances and not selected_account_clear) and not pending_start:  # 旧记录可覆盖，证据冲突仍阻断。
                            result = starter.continue_switch(stop_requested)
                            if result is not None:
                                results.append(result)
                    else:
                        results = [starter.attempt(event, stop_requested) for event in
                                   trigger_events[: selected_auto_start.max_starts_per_event]]
                    for result in results:
                        result_text = format_start_result(result)
                        if result.status in {"request_accepted", "gpu_start_observed"}:
                            if result.status == "gpu_start_observed":
                                result_text = _green_terminal_text(result_text)  # 受理只表示请求进入队列；实际有卡运行才显示成功色。
                            occupancy.pending_start_until = (
                                time.monotonic() + _POWER_ON_CONFIRM_GRACE_SECONDS
                            )
                            occupancy.pending_start_machine = result.machine_name or ""
                            logger.info(
                                "gpu start pending occupancy confirmation status=%s host=%s machine=%s instance=%s grace=%ss",
                                result.status,
                                result.host,
                                result.machine_name,
                                result.instance_uuid,
                                _POWER_ON_CONFIRM_GRACE_SECONDS,
                            )
                        elif (
                            not selected_auto_start.dry_run and not starter.pending_switch
                            and result.status in {"request_failed", "recheck_failed", "no_target", "shutdown_failed", "instance_state_blocked"}
                        ):
                            evaluator.rearm_host(selected_host)  # 请求根本没有成功落地时，不能让 evaluator 的 alerted 锁住后续重试。
                        if result.status in {"request_failed", "recheck_failed", "shutdown_failed", "switch_failed", "instance_state_blocked", "quota_blocked"}:
                            result_text = _red_terminal_text(result_text)
                        print(result_text, flush=True)
                        logger.info(
                            "auto_start status=%s host=%s machine=%s instance=%s before=%s/%s after=%s/%s code=%s msg=%s",
                            result.status,
                            result.host,
                            result.machine_name,
                            result.instance_uuid,
                            result.platform_free_before,
                            result.platform_total_before,
                            result.platform_free_after,
                            result.platform_total_after,
                            result.api_code,
                            result.api_msg,
                        )

                state_store.save(
                    {
                        "saved_at": now.isoformat(),
                        "capacity_fingerprint": capacity_fingerprint,
                        "evaluator": evaluator.export_state(),
                    }
                )
                if not selected_auto_start.enabled:
                    action_text = "仅监控，不执行开机或关机"
                    if occupancy.last_occupancy_failed:
                        action_text += "；占用采集失败，等待重试"
                    elif selected_instance_error:
                        action_text += "；实例查询失败，等待重试"
                elif starter.quota_blocked is not None:
                    action_text = "额度不足，暂停自动开机"
                elif occupancy.account_occupancy_conflict:
                    action_text = "占用证据冲突，暂停开机并复核"
                elif occupancy.occupancy_identity_unresolved:
                    action_text = "占用身份未确认，暂停开机并复核"
                elif starter.pending_switch:
                    action_text = "等待无卡实例关机，复核后有卡开机"
                elif occupancy.pending_start_until > time.monotonic():
                    action_text = "开机已受理，等待有卡运行确认"
                elif occupancy.last_occupancy_failed:
                    action_text = "占用采集失败，等待重试"
                elif selected_instance_text == "有卡运行":
                    action_text = "继续监控"
                elif selected_instance_error:
                    action_text = "实例查询失败，等待重试"
                elif occupancy.self_occupancy_known and occupancy.owned_instances and not selected_account_clear:
                    action_text = "本账号已有有卡实例，继续监控"
                elif platform_host is not None and (dict((name, idle) for name, idle, _ in platform_host.source_slots).get(selected_entry, platform_host.free_count) == 0):
                    action_text = "等待GPU空位"
                elif not ready_samples:
                    action_text = "等待显存达标"
                elif not occupancy.self_occupancy_known and not selected_account_clear:
                    action_text = "核对本人占用，暂缓开机"
                else:
                    action_text = "继续监控"
                status_line = (f"[{now:%H:%M:%S}] 监控{monitor_entry} | 实例状态：{selected_instance_text} | 动作：{action_text} | "
                               f"平台空闲：{entry_slots} | 显存达标：{_format_ready_indices(selected_gpu_samples, config.idle_thresholds)}")
                level = ("error" if starter.quota_blocked is not None or any(term in action_text for term in ("失败", "冲突", "未确认"))
                         else "success" if selected_instance_text == "有卡运行" else "normal")
                lines = [status_line, f"  {occupancy_summary}"]
                if args.ui:
                    emit_gpu_samples(all_gpu_samples, all_platform_hosts, config.monitor.stale_after_seconds)
                    print(REPORT_PREFIX + json.dumps({"lines": lines, "level": level}, ensure_ascii=False), flush=True)
                else:
                    text = "\n".join(lines)
                    print(_red_terminal_text(text) if level == "error" else _green_terminal_text(text) if level == "success" else text, flush=True)
                logger.info("monitor entry=%s instance_state=%s action=%s", monitor_entry, selected_instance_text, action_text)
            except PlatformAuthenticationError as exc:
                if args.ui:
                    emit_gpu_samples([], [], config.monitor.stale_after_seconds, "本轮采集不可用")
                emit_session("invalid", f"登录会话失效，请重新登录：{exc}")
                if args.no_login:
                    print(_red_terminal_text(f"[{datetime.now():%H:%M:%S}] 监控{monitor_entry} | 登录已失效 | 动作：监控已停止，请重新登录"), flush=True)
                    raise SystemExit(3) from None  # GUI收到明确状态，避免预期认证失败变成PyInstaller堆栈。
                now = datetime.now()  # v0.5.2：明确登录页、HTTP认证状态或业务登录超时才进入人工登录恢复。
                print(f"[{now:%H:%M:%S}] 登录会话确认失效：{exc}", flush=True)
                logger.warning("platform authentication expired: %s", exc)
                platform_collector.close()
                try:
                    interactive_login(config, reason="expired")
                    print( f"[{datetime.now():%H:%M:%S}] 登录资料已更新；下一轮将验证主机接口，验证成功后再恢复监控。", flush=True, )
                    login_validation_pending = True
                    occupancy.self_occupancy_known = False  # 登录会话更新后重新确认本人占用，不沿用更新前的判断。
                    occupancy.owned_instances = []
                    occupancy.occupancy_identity_unresolved = False
                    occupancy.pending_start_until = 0.0
                    occupancy.pending_start_machine = ""
                    evaluator.rearm_host(selected_host)
                except Exception as login_exc:
                    print( f"[{datetime.now():%H:%M:%S}] 登录恢复失败：{login_exc}", flush=True, )
                    logger.exception("interactive login recovery failed")
            except LocalStartupError as exc:
                if args.ui:
                    emit_gpu_samples([], [], config.monitor.stale_after_seconds, "本轮采集不可用")
                emit_session("unknown", f"本地启动失败：{exc}")
                print(_red_terminal_text(f"[{datetime.now():%H:%M:%S}] 监控{monitor_entry} | 本地启动失败 | 动作：监控已停止：{exc}"), flush=True)
                logger.error("local startup failed: %s", exc)
                raise SystemExit(5) from None  # 缺少系统组件须先修复，持续重试没有作用。
            except TelemetryUnavailable as exc:
                if args.ui:
                    emit_gpu_samples([], [], config.monitor.stale_after_seconds, str(exc))
                print(_red_terminal_text(f"[{datetime.now():%H:%M:%S}] 监控{monitor_entry} | {exc} | 动作：{retry_action}"), flush=True)
                logger.warning("telemetry unavailable: %s", exc)
            except PlatformTransientError as exc:
                if args.ui:
                    emit_gpu_samples([], [], config.monitor.stale_after_seconds, "本轮采集不可用")
                if not platform_session_verified:  # 后续入口或数据故障不否定本轮已成功的会话核验。
                    emit_session("unknown", f"平台请求异常，无法确认会话：{exc}")
                now = datetime.now()  # 页面/API 慢、超时、429、5xx 都属于数据采集瞬时故障。 不弹登录窗口，不修改本人占用状态，也绝不发送开机请求。
                print(_red_terminal_text(f"[{now:%H:%M:%S}] 监控{monitor_entry} | 本轮采集暂时失败 | 动作：{retry_action}：{exc}"), flush=True)
                logger.warning("platform transient failure: %s", exc)
            except Exception as exc:
                if args.ui:
                    emit_gpu_samples([], [], config.monitor.stale_after_seconds, "本轮采集不可用")
                if not platform_session_verified:
                    emit_session("unknown", f"主机接口核验失败，无法确认会话：{exc}")
                now = datetime.now()  # 本轮任意环节抛异常 → 打日志，不中断循环
                print(_red_terminal_text(f"[{now:%H:%M:%S}] 监控{monitor_entry} | 本轮采集失败 | 动作：等待重试：{exc}"), flush=True)
                logger.exception("collection/auto-start cycle failed")

            cycles += 1  # 控制循环间隔：睡眠到距离上次开始正好 poll_seconds
            if args.max_cycles and cycles >= args.max_cycles:
                break
            deadline = cycle_start + config.monitor.poll_seconds  # 采集耗时计入周期，避免每轮额外叠加完整等待时间。
            while not stop_requested() and time.monotonic() < deadline:
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))  # 分段等待以便及时响应 GUI 停止文件。
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在保存状态并退出……")  # Ctrl+C → 保存状态后优雅退出
        state_store.save(
            {
                "saved_at": datetime.now().isoformat(),
                "capacity_fingerprint": capacity_fingerprint,
                "evaluator": evaluator.export_state(),
            }
        )
    finally:
        try:
            state_store.save({"saved_at": datetime.now().isoformat(),
                              "capacity_fingerprint": capacity_fingerprint,
                              "evaluator": evaluator.export_state()})
        finally:
            telemetry_collector.close()
            platform_collector.close()
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)  # 释放处理器引用，后续同进程启动可重新绑定日志文件。
        print("监控已安全停止。")
