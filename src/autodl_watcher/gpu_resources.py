"""资源视图的逐GPU数据与更新时间；不参与开机判定，也不触发采集。"""
from __future__ import annotations

from datetime import datetime, timedelta
import math

from .cli import normalize_host


def _moment(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return stamp.astimezone().replace(tzinfo=None) if stamp.tzinfo is not None else stamp
    except (TypeError, ValueError):
        return None  # 时间未知不能假作本次采集时间。


def _positive(value, fallback=0):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _update_timing(snapshot, stale_after, now):
    collected = _moment(snapshot.get("collected_at"))
    poll = _positive(snapshot.get("poll_seconds"))  # 来自运行中的worker，不读取待保存的界面输入。
    deadline = _moment(snapshot.get("next_sample_at"))
    if collected is not None and poll and deadline is None:
        deadline = collected + timedelta(seconds=poll)
    grace = max(5.0, min(30.0, stale_after))  # 下轮采集本身允许少量耗时，不在计划时刻立刻报延迟。
    delayed = bool(collected and now > (max(deadline, collected) + timedelta(seconds=grace)
                                       if deadline else collected + timedelta(seconds=stale_after)))
    return collected, poll, delayed


def resource_rows(entry, snapshot, stale_after, now=None):
    now = _moment(now) if now is not None else datetime.now()
    stale_after = _positive(snapshot.get("stale_after_seconds"), _positive(stale_after, 90))
    collected, poll, delayed = _update_timing(snapshot, stale_after, now)
    exact = [item for item in snapshot.get("samples", ()) if item.get("host") == entry]
    samples = exact or [item for item in snapshot.get("samples", ()) if item.get("host") == normalize_host(entry)]
    rows = []
    for sample in sorted(samples, key=lambda item: item["gpu_index"]):
        stamp = _moment(sample.get("observed_at"))
        invalid = stamp is None
        try:
            util, used, total = (float(sample[key]) for key in ("util_pct", "memory_used_mb", "memory_total_mb"))
            if not (all(math.isfinite(value) for value in (util, used, total))
                    and 0 <= util <= 100 and 0 <= used <= total and total > 0):
                raise ValueError("GPU指标无效")
            memory = used / total * 100
        except (KeyError, TypeError, ValueError):
            util = used = total = memory = None
            invalid = True  # 无效指标保持未知，不能伪装为0%。
        source_age = (collected - stamp).total_seconds() if collected and stamp else None
        clock_skew = bool(stamp and ((now - stamp).total_seconds() < -5
                                   or source_age is not None and source_age < -5))
        clock_skew = clock_skew or bool(collected and (now - collected).total_seconds() < -5)
        source_stale = source_age is not None and source_age > stale_after
        update_delayed = delayed
        if collected is None and stamp is not None:
            update_delayed = (now - stamp).total_seconds() > max(stale_after, poll)
        state = ("数据无效" if invalid else "时间异常" if clock_skew else
                 "上次数据 · 采集失败" if snapshot.get("error") else
                 "采集中 · 上次数据" if snapshot.get("refreshing") else
                 "数据源延迟" if source_stale else "等待更新" if update_delayed else "正常")
        rows.append({"gpu_index": sample["gpu_index"], "memory_pct": memory, "util_pct": util,
                     "used_mb": used, "total_mb": total, "observed_at": stamp, "state": state,
                     "physical": not bool(exact), "invalid": invalid, "clock_skew": clock_skew,
                     "source_stale": source_stale, "update_delayed": update_delayed,
                     "stale": bool(source_stale or update_delayed or clock_skew)})
    return rows  # 数值始终保留；陈旧或失败只改变状态，不能把整行换成“已过期”。


def server_state(entry, snapshot, stale_after, now=None, rows=None):
    rows = resource_rows(entry, snapshot, stale_after, now) if rows is None else rows
    if snapshot.get("error"):
        if rows:
            return "采集失败 · 保留上次数据"
        error = snapshot["error"]
        return "需配置认证" if "认证" in error or "401" in error else "采集不可用"
    if snapshot.get("refreshing"):
        return "采集中 · 上次数据" if rows else "采集中"
    if not rows:
        return "等待采样"
    state = ("部分数据无效" if any(row["invalid"] for row in rows) else
             "时间异常" if any(row["clock_skew"] for row in rows) else
             "数据源延迟" if any(row["source_stale"] for row in rows) else
             "等待更新" if any(row["update_delayed"] for row in rows) else "正常")
    return state + (" · 物理机数据" if rows[0]["physical"] else "")
