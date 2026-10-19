"""训练事实和日志事件合成卡片，不执行 SSH 或访问 Tk。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time

from .training_events import ANSI_ESCAPE, TIMESTAMP, log_text, parse_progress
from .training_rules import parse_rules


class TrainingTracker:
    def __init__(self, server, rule_lookup=None):
        self.server = server
        self.rule_lookup = rule_lookup
        self.known = {}
        self.references = {}

    def fork(self):
        """后台解析拥有独立历史；过时代次结果不能修改当前跟踪器。"""
        tracker = TrainingTracker(self.server, self.rule_lookup)
        tracker.known = self.known.copy()
        tracker.references = self.references.copy()
        return tracker

    def previous(self):
        return list(self.references.values())[-32:]

    def update(self, snapshot):
        now = snapshot["observed_at"]
        cards = []
        for task in snapshot["tasks"]:
            key = task["key"]
            previous = self.known.get(key, {})
            logs = [item for item in task.get("logs", []) if "error" not in item]
            parsed = [(log, parse_progress(log_text(log), snapshot.get("utc_offset", "+0000"), expected_pid=task["pid"])) for log in logs]
            if parsed:
                primary, progress = max(parsed, key=lambda pair: (pair[1]["phase"] != "unknown", pair[1]["progress_line"] is not None, Path(pair[0]["path"]).name == "training.log", pair[0]["modified_at"]))
            else:
                primary, progress = None, deepcopy(previous["progress"]) if previous.get("progress") else parse_progress("")
            rule_status = "默认监控"
            entry = self.rule_lookup(dict(task, log_path=primary["path"] if primary else "")) if self.rule_lookup else None
            if entry:
                rule_status = entry.get("message", "默认监控")
            if entry and entry.get("profile") and (not primary or entry.get("log_path") != primary["path"]):
                rule_status = "默认监控 · 日志来源已变化，请更新规则"
            if primary and entry and entry.get("profile") and entry.get("log_path") == primary["path"]:
                try:
                    text = log_text(primary)
                    events = parse_rules(entry["profile"], text, task["pid"], with_lines=True)
                    if not events:
                        raise ValueError("当前片段未匹配规则")
                    lines = text.replace("\r", "\n").splitlines()
                    # 在原记录位置替换提取结果，保留未覆盖阶段及统一的外层时间。
                    # 同行 JSON time 与日志前缀的微小差异不能使规则失效。
                    for index, event in events:
                        stamp = TIMESTAMP.search(lines[index])
                        lines[index] = (stamp[1] + " " if stamp else "") + json.dumps(event)
                    adapted = parse_progress("\n".join(lines), snapshot.get("utc_offset", "+0000"), task["pid"])
                    adapted["error"] = progress["error"] or adapted["error"]
                    adapted["completed"] = progress["completed"]
                    if "peak_memory_bytes" in progress:
                        adapted["peak_memory_bytes"] = progress["peak_memory_bytes"]
                    partial = adapted["progress_line"] is not None and adapted["progress_line"] not in {index for index, _ in events}
                    progress = adapted
                    rule_status = "专用规则已启用：" + entry["profile"]["name"]
                    if entry.get("message", "").startswith(("更新失败", "正在更新", "规则保存失败")):
                        rule_status += " · " + entry["message"]
                    if partial:
                        rule_status += " · 最新未覆盖行使用默认解析"
                except (ValueError, TypeError, KeyError) as exc:
                    rule_status = "默认监控 · 专用规则不适用：" + str(exc)
            for log, extra in parsed:
                if log is not primary:
                    if extra["error"] and log["modified_at"] >= task.get("started_at", 0) and (
                            extra["progress_at"] is None or extra["progress_at"] >= task.get("started_at", 0) - 1):
                        progress["error"] = extra["error"]
                    if extra["completed"] and log["modified_at"] >= (primary["modified_at"] if primary else task.get("started_at", 0)):
                        progress["completed"] = True
            indicator = {k: progress[k] for k in ("phase", "epoch", "step", "completed", "error")}
            indicator["observations"] = {k: v for k, v in progress["observations"].items() if k != "time"}
            indicator["train"] = {k: v for k, v in progress["train"].items() if k != "time"}
            indicator["tests"] = {name: {k: v for k, v in value.items() if k != "time"} for name, value in progress["tests"].items()}
            marker = json.dumps(indicator, sort_keys=True)
            advanced_at = progress["progress_at"] or (primary["modified_at"] if primary else task.get("started_at", now))
            if previous.get("marker") == marker:
                advanced_at = previous["advanced_at"]  # 重复打印同一指标不是训练进展。
            if previous.get("progress", {}).get("error"):
                progress["error"] = previous["progress"]["error"]  # 同一进程已确认的错误保留为红色，滚动日志或随后断连不能抹掉。
            alive = task.get("alive", False)
            if progress["error"]:
                level, status = "error", "错误已记录 · 进程仍存活" if alive else "异常中止"
            elif progress["completed"]:
                level, status = "idle", ("冒烟检查已完成" if progress.get("smoke") else "已完成") + (" · 收尾中" if alive else "")
            elif not alive:
                level, status = "error", "进程已退出 · 未确认正常完成"
            elif progress["phase"] == "between":
                level, status = "warning", "轮次结束 · 等待后续日志"
            elif not primary or progress["phase"] == "unknown":
                level, status = "warning", "进度可读 · 阶段未确认" if primary and progress["progress_line"] is not None else "进程存活 · 训练进度不可读"
            elif now - advanced_at > self.server.stalled_minutes * 60:
                level, status = "warning", "长时间无新进度 · 待核查"
            else:
                level, status = "success", "正在测试" if progress["phase"] == "test" else "正在训练"
            card = dict(task, progress=progress, level=level, status=status, marker=marker,
                        last_alive_at=now if alive else previous.get("last_alive_at"),
                        advanced_at=advanced_at, rule_status=rule_status, log_path=primary["path"] if primary else "",
                        details=(primary["tail"] if primary.get("gap") else primary["head"] + primary["tail"]) if primary else "")
            if primary:
                preview = ANSI_ESCAPE.sub("", card["details"]).replace("\r", "\n")
                card["preview"] = "\n".join(line[:240] for line in preview.splitlines()[-3:] if line.strip())
            else:
                card["preview"] = "未找到可读日志；进程存在不代表训练进展正常。"
            card.pop("logs", None)
            self.known[key] = card
            self.references[key] = {k: task[k] for k in ("key", "pid", "name", "started_at", "gpu_indices", "gpu_memory_mb", "user", "cwd", "tmux_session") if k in task}
            self.references[key]["log_paths"] = [log["path"] for log in task.get("logs", [])]
            cards.append(card)
        if not snapshot["scan_ok"]:
            existing = {card["key"] for card in cards}
            cards.extend(dict(card, level="error" if card["level"] == "error" else "warning",
                              status=card["status"] if card["level"] == "error" else "进程列表不完整 · 状态待确认")
                         for key, card in self.known.items() if key not in existing)
        for card in cards:
            card["history"] = bool(not card.get("alive") and card.get("log_path") and any(
                other.get("alive") and other.get("log_path") == card["log_path"]
                and other.get("started_at", 0) > card.get("started_at", 0) for other in cards))
            if card["history"]:
                card["status"] = "历史运行 · " + card["status"]
        # 探针最多回查最近 32 条引用；更早运行只保留卡片和错误事实，不保留大原文。
        self.references = dict(list(self.references.items())[-32:])
        keep_raw = set(self.references) | {card["key"] for card in cards if card.get("alive")}
        note = "此历史运行已超出最近32条原文保留范围；进度与错误摘要仍保留，请按日志路径查看服务器原文。"
        for key, card in self.known.items():
            if key not in keep_raw and card.get("details"):
                self.known[key] = dict(card, details="", details_note=note)  # 写时复制，不改动主线程仍在展示的旧快照。
        for card in cards:
            if card["key"] not in keep_raw and card.get("details"):
                card.update(details="", details_note=note)
        return dict(snapshot, cards=sorted(cards, key=lambda card: (not card.get("alive", False), -card.get("started_at", 0))),
                    received_at=time.time(), error="" if snapshot["scan_ok"] else "进程列表读取不完整")
