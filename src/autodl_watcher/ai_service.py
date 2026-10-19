"""全局 AI 请求服务：不依赖 Tk，拥有请求、规则结果及用量的收尾。"""
from __future__ import annotations

from dataclasses import dataclass
import queue
import sqlite3
import threading
import time

from . import ai_credentials
from .training_ai import (DEFAULTS, AnalysisControl, AnalysisError, analyze, excerpt,
                          interrupted_call, load_settings, settings)
from .training_ai_usage import UsageStore
from .training_rules import RuleError, RuleStore, task_identity


@dataclass
class _Request:
    identity: str
    server_id: str
    config: dict
    control: AnalysisControl
    started_at: float
    call: dict | None = None
    finished: bool = False
    settling: bool = False


class AIService:
    """线程只更新业务结果；视图通过 poll 取得需要重新解析的服务器 ID。"""
    def __init__(self, path, analyzer=analyze):
        self.path, self.analyzer = path, analyzer
        self.store = RuleStore(path.with_name("training_rules.yaml"))
        self.usage = UsageStore(path.with_name("ai_usage.sqlite3"))
        self.results = queue.Queue()
        self.busy = None
        self.revision = 0
        self.closing = False
        self._lock = threading.RLock()
        self._request = None
        self._shutdown_due = None
        self._cancelling = False
        self.reload()

    def reload(self):
        try:
            self.config = load_settings(self.path)
        except (OSError, ValueError):
            self.config = {**DEFAULTS, "auto_generate": False}
        self.revision += 1

    def rule_entry(self, server, task):
        """供后台解析器读取不可就地修改的条目快照；不访问任何控件。"""
        identity = task_identity(server, task)
        with self._lock:
            entry = dict(self.store.entries.get(identity, {}))
            if not entry and self.store.error:
                return {"message": "默认监控 · " + self.store.error}
            pending = entry.get("request_state") == "running"
            # 旧缓存只用文案记录在途状态；仅在兼容读取时映射为明确状态。
            if "request_state" not in entry:
                pending = entry.get("message") in {"正在更新专用规则", "默认监控 · 正在生成专用规则"}
            if pending and self.busy != identity:
                entry.update(request_state="interrupted", message=(
                    "更新失败，保留原专用规则 · 上次规则请求未完成，可手动重试" if entry.get("profile")
                    else "默认监控 · 上次规则请求未完成，可手动重试"))
            return entry

    def generate(self, server, card, source, config=None):
        config = settings(config or self.config)
        source = excerpt(source)
        if not source.strip() or not card.get("log_path"):
            raise AnalysisError("没有可读日志；继续默认监控")
        identity = task_identity(server, card)
        with self._lock:
            if self.closing:
                raise AnalysisError("应用正在退出，不再接受 AI 请求")
            if self.busy:
                raise AnalysisError("已有 AI 请求进行中；继续默认监控，不会重复发送")
            entry = dict(self.store.entries.get(identity, {}))
            if entry.get("log_path") != card["log_path"]:
                entry.pop("profile", None)
            entry.update(message="正在更新专用规则" if entry.get("profile") else "默认监控 · 正在生成专用规则",
                         attempted=True, request_state="running", log_path=card["log_path"])
            self.store.put(identity, entry)  # 发送前确认尝试状态落盘，重开不能自动重复收费。
            request = _Request(identity, server.id, config, AnalysisControl(), time.time())
            request.control.on_call = lambda call: self._capture(request, call)
            self._request, self.busy = request, identity
            self.revision += 1
        # 无响应头的 requests 无法从另一线程可靠中断；有界退出先落盘中断记录，
        # daemon 仅用于该最后兜底，不再承担正常结果或用量保存。
        threading.Thread(target=self._run, args=(identity, server.id, card["pid"], config, source, request),
                         name="ai-rule-request", daemon=True).start()
        return identity

    def _capture(self, request, call):
        with self._lock:
            if not request.finished:
                request.call = call

    def _run(self, identity, server_id, pid, config, source, request=None):
        try:
            value = self.analyzer(config, source, pid, control=request.control if request else None)
            error = ""
        except Exception as exc:
            known_call = getattr(exc, "call", None) or (request.call if request else None)
            value = {"call": known_call} if known_call else None
            error = str(exc) if isinstance(exc, (AnalysisError, RuleError, ai_credentials.CredentialError)) else "AI 规则生成失败"
        self._finish(identity, server_id, value, error, request)

    def _finish(self, identity, server_id, value, error, request=None):
        with self._lock:
            if request and (request.finished or request.settling):
                return  # 结束与取消只能由一个线程落盘，不能重复记账。
            if request:
                request.settling = True
            entry = dict(self.store.entries.get(identity, {}))
        # 请求仍占 busy；磁盘和 SQLite 写入不持状态锁，Tk 的退出检查不会等 IO。
        if value and value.get("call"):
            try:
                self.usage.record(value["call"])
            except (OSError, sqlite3.Error):
                value["usage_warning"] = "用量账本保存失败，本次用量仅随任务结果保存"
        if error:
            entry["message"] = ("更新失败，保留原专用规则 · " + error.replace("继续默认监控，", "").replace("；继续默认监控", "")
                                if entry.get("profile") else "默认监控 · " + error)
        else:
            entry["message"] = value.get("generation_note", "专用规则已生成")
        if value:
            if value.get("rule_origin") == "local" and entry.get("profile"):
                entry["message"] = "更新失败，AI返回空规则；保留原专用规则，未自动重试"
                value = {key: item for key, item in value.items() if key not in {"profile", "rule_origin", "generation_note"}}
            entry.update(value)
        entry.update(attempted=True, request_state="interrupted" if request and request.control.cancelled else "failed" if error else "completed")
        try:
            self.store.put(identity, entry)
        except OSError:
            retained = dict(self.store.entries.get(identity, {}), attempted=True, request_state="failed",
                            message="规则保存失败，保留原监控；请检查本地目录")
            if value and value.get("call"):
                retained["call"] = value["call"]
            self.store.entries = {**self.store.entries, identity: retained}
        with self._lock:
            if request:
                request.finished = True
            self.revision += 1
            self.results.put(server_id)
            self.busy = None

    def poll(self):
        changed = set()
        while True:
            try:
                changed.add(self.results.get_nowait())
            except queue.Empty:
                return changed

    def begin_shutdown(self, grace_seconds=2.0):
        """立即拒绝新请求；预算内让已开始请求完成，不阻塞 Tk。"""
        with self._lock:
            if self.closing:
                return
            self.closing = True
            self._shutdown_due = time.monotonic() + grace_seconds

    def shutdown_complete(self):
        """由主线程轮询；资源取消及必要落盘也交给后台，不卡住退出按钮。"""
        with self._lock:
            request = self._request
            if request is None or request.finished:
                return True
            if self._shutdown_due is None or time.monotonic() < self._shutdown_due:
                return False
            if not self._cancelling and not request.settling:
                self._cancelling = True
                threading.Thread(target=self._cancel_pending, args=(request,), name="ai-request-shutdown", daemon=True).start()
        return False

    def _cancel_pending(self, request):
        request.control.cancel()
        call = interrupted_call(request.config, request.started_at, request.call)
        self._finish(request.identity, request.server_id, {"call": call},
                     "应用关闭，请求已中断；费用可能已产生，未自动重试", request)
