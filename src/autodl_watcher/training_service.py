"""每台服务器的单轮调度、后台解析和受控收尾；不依赖 Tk。"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from .training_connection import TrainingConnection
from .training_tracker import TrainingTracker


@dataclass(frozen=True)
class TrainingResult:
    snapshot: dict
    tracker: TrainingTracker
    reparse: bool = False


@dataclass(frozen=True)
class ConnectionClosed:
    error: str = ""


class TrainingService:
    def __init__(self, events):
        self.events = events
        self.states = {}
        self._closers = []
        self.on_snapshot = None  # 结果接纳后在主线程通知，不让后台作业访问控件。

    def server(self, server_id):
        return self.states[server_id]["server"]

    def snapshot(self, server_id):
        state = self.states.get(server_id)
        return state.get("snapshot") if state else None

    def monitored_snapshots(self):
        return [(key, state["snapshot"]) for key, state in self.states.items()
                if state["active"] and state["snapshot"] and not state["snapshot"].get("error")]

    def status(self, server_id):  # 返回轻量状态副本，界面无需访问连接或调度器内部字典。
        state = self.states[server_id]
        connection = state["connection"]  # 计数由已复用的连接对象提供，不触发采集。
        return {name: state[name] for name in ("active", "pending", "busy", "interval", "generation")} | {
            "closing": self._closing(state), "samples": connection.samples, "attempts": connection.attempts,
        }

    def stopping(self, server_id):
        state = self.states.get(server_id)
        return bool(state and self._closing(state))

    def register(self, server):
        state = self.states.get(server.id)
        if state and (state["active"] or state["busy"] or self._closing(state)):
            state["pending"] = server if server != state["server"] else None
            return
        if state:
            self._close_connection(server.id, state)
        self.states[server.id] = dict(server=server, tracker=TrainingTracker(server, lambda task: self.rule_entry(server, task)),
                                      active=False, busy=False, generation=(state["generation"] + 1 if state else 0),
                                      due=0, interval=60, snapshot=None, pending=None, reparse_pending=False,
                                      connection=TrainingConnection(server), failures=0, close_thread=None)

    def rule_entry(self, server, task):
        assistant = getattr(self, "ai", None)
        return assistant.rule_entry(server, task) if assistant else None

    def reparse(self, server_id):
        state = self.states.get(server_id)
        if not state or not state.get("snapshot"):
            return
        if state["busy"] or self._closing(state):
            state["reparse_pending"] = True  # 在途期间多次更新规则只排一次重新解析，不增加采样。
            return
        self._parse(server_id, state["generation"], state["snapshot"], reparse=True)

    def start(self, server_id, interval):
        state = self.states[server_id]
        if (state["busy"] or self._closing(state)) and not state["active"]:
            raise ValueError("前一次采集或连接收尾尚未结束，请稍后再开始")
        if state["active"]:
            return
        if state.get("pending"):
            self.register(state["pending"])
            state = self.states[server_id]
        if any(other["active"] and other["server"].ssh_alias.casefold() == state["server"].ssh_alias.casefold()
               for key, other in self.states.items() if key != server_id):
            raise ValueError("此SSH连接已在另一服务器页面监控，请先停止那一页")
        if state["connection"].closed:
            state["connection"] = TrainingConnection(state["server"])
        state.update(active=True, interval=interval, due=state["due"] if state["failures"] else 0)

    @staticmethod
    def _closing(state):
        thread = state.get("close_thread")
        return bool(thread and thread.is_alive())

    def _close_connection(self, server_id, state):
        if self._closing(state):
            return
        connection, generation = state["connection"], state["generation"]
        def close():
            error = ""
            try:
                connection.close()
            except Exception as exc:
                error = f"{type(exc).__name__}：{exc}"
            self.events.put(("training", (server_id, generation, ConnectionClosed(error), "")))
        thread = threading.Thread(target=close, daemon=True)
        state["close_thread"] = thread
        self._closers.append(thread)
        thread.start()

    def stop(self, server_id):
        state = self.states[server_id]
        state.update(active=False, generation=state["generation"] + 1, reparse_pending=False)
        self._close_connection(server_id, state)  # terminate/wait 只在收尾线程执行，按钮立即返回。

    def tick(self):
        if getattr(self, "ai", None):
            self.ai.tick()
        now = time.monotonic()
        for server_id, state in self.states.items():
            if state["active"] and not state["busy"] and now >= state["due"]:
                state.update(busy=True, due=now + state["interval"])
                threading.Thread(target=self._collect,
                                 args=(server_id, state["generation"], state["tracker"], state["connection"]), daemon=True).start()

    @staticmethod
    def _parsed(tracker, result, reparse=False):
        tracker = tracker.fork()
        snapshot = tracker.update(result)
        if reparse:
            snapshot.update(received_at=result["received_at"], error=result.get("error", ""))
        return TrainingResult(snapshot, tracker, reparse)

    def _collect(self, server_id, generation, tracker, connection):
        try:
            result, error = self._parsed(tracker, connection.collect(tracker.previous())), ""
        except Exception as exc:
            result, error = None, f"{type(exc).__name__}：{exc}"
        self.events.put(("training", (server_id, generation, result, error)))

    def _parse(self, server_id, generation, result, reparse=False):
        state = self.states[server_id]
        state["busy"] = True
        tracker = state["tracker"]
        def parse():
            try:
                parsed, error = self._parsed(tracker, result, reparse), ""
            except Exception as exc:
                parsed, error = None, f"训练信息解析失败：{exc}"
            self.events.put(("training", (server_id, generation, parsed, error)))
        threading.Thread(target=parse, daemon=True).start()

    def receive(self, value):
        server_id, generation, result, error = value
        state = self.states.get(server_id)
        if not state:
            return
        if isinstance(result, ConnectionClosed):
            if generation != state["generation"]:
                return
            state["close_thread"] = None
            if result.error:
                old = state["snapshot"] or {"cards": [], "gpus": []}
                state["snapshot"] = dict(old, error="训练连接收尾失败：" + result.error, received_at=time.time())
            self._run_pending_reparse(server_id, state)
            return
        state["busy"] = False
        if generation != state["generation"]:
            self._run_pending_reparse(server_id, state)
            return  # 丢弃整个临时 tracker，停止前的旧结果不能污染新任务或清除红色卡片。
        if not error and not isinstance(result, TrainingResult):
            self._parse(server_id, generation, result)  # 本地外部采样注入也必须走后台解析。
            return
        if not error:
            state["snapshot"], state["tracker"] = result.snapshot, result.tracker
            if not result.reparse:
                state.update(failures=0, due=time.monotonic() + state["interval"])
            if self.on_snapshot:
                self.on_snapshot(server_id, state["snapshot"])
        else:
            state["failures"] += 1
            delay = max(state["interval"], min(300, 60 * 2 ** min(state["failures"] - 1, 3)))
            state["due"] = time.monotonic() + delay
            old = state["snapshot"] or {"cards": [], "gpus": []}
            state["snapshot"] = dict(old, error=f"{error}；{delay:g}秒后重试", received_at=time.time())
        self._run_pending_reparse(server_id, state)

    def _run_pending_reparse(self, server_id, state):
        if state.get("reparse_pending") and not state["busy"] and not self._closing(state):
            state["reparse_pending"] = False
            self.reparse(server_id)

    def busy(self):
        self._closers = [thread for thread in self._closers if thread.is_alive()]
        return bool(self._closers) or any(state["busy"] for state in self.states.values())

    def stop_all(self):
        for key in self.states:
            self.stop(key)
