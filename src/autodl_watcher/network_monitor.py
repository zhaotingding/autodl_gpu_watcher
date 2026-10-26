"""把本机连接快照限定到本实例，保留有界的观察历史；不建立任何网络连接。"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
import threading
import time

from .network_system import read_system_snapshot


@dataclass(frozen=True, slots=True)
class ConnectionRecord:
    id: str
    pid: int
    process_name: str
    process_started_at: float
    protocol: str
    local_address: str
    local_port: int
    remote_address: str
    remote_port: int
    state: str
    first_seen: float
    last_seen: float
    closed_at: float | None = None


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    captured_at: float
    entries: tuple[ConnectionRecord, ...]
    errors: tuple[str, ...]
    elapsed_seconds: float


class NetworkMonitor:
    def __init__(self, root_pid=None, *, reader=read_system_snapshot, history_limit=300):
        self.root_pid = os.getpid() if root_pid is None else root_pid  # 只追踪当前 GUI 实例，不按程序名扩大范围。
        self.reader, self.history_limit = reader, max(0, history_limit)
        self._root_started = None
        self._owned = {}  # PID 必须与创建时间一起匹配，防止操作系统复用 PID 后误归属。
        self._current, self._history = {}, []
        self._sequence = 0
        self._lock = threading.Lock()  # 面板反复打开也不能并发读取或写坏历史。

    def _processes(self, processes):
        current = {process.pid: process for process in processes}
        root = current.get(self.root_pid)
        if root is None or root.created_at <= 0:
            return {}, ["无法核对本实例进程身份，连接归属暂不可用。"]
        if self._root_started is None:
            self._root_started = root.created_at
        if root.created_at != self._root_started:
            return {}, ["本实例进程身份发生变化，已停止接纳新连接。"]
        owned = {self.root_pid: root}
        for pid, started in self._owned.items():
            process = current.get(pid)
            if process is not None and process.created_at == started:
                owned[pid] = process  # 父 worker 已退出的浏览器子进程，仍按此前核对的身份保留。
        while True:
            added = False
            for process in current.values():
                parent = owned.get(process.parent_pid)
                if (process.pid not in owned and parent is not None and process.created_at > 0
                        and process.created_at >= parent.created_at):
                    owned[process.pid] = process
                    added = True
            if not added:
                break
        unknown = sum(process.created_at <= 0 and process.parent_pid in owned
                      for process in current.values())
        self._owned = {pid: process.created_at for pid, process in owned.items()}
        return owned, ([f"有 {unknown} 个子进程无法核对创建时间，列表可能不完整。"] if unknown else [])

    def poll(self):
        with self._lock:
            started = time.monotonic()
            try:
                system = self.reader()
            except Exception as exc:
                system = None  # 读取失败只能表示未知，不能把全部连接标为已关闭。
                errors = [f"本机连接读取失败：{type(exc).__name__}"]
            if system is None:
                captured_at, owned, sockets = time.time(), {}, ()
            else:
                captured_at, sockets = system.captured_at, system.sockets
                owned, identity_errors = self._processes(system.processes)
                errors = [*system.errors, *identity_errors]
                changed = {pid for pid, process in owned.items() if process.created_at > captured_at}
                if any(item.pid in changed for item in sockets):
                    errors.append("部分子进程在读表期间启动，本轮暂不归属其连接，等待下一次核对。")
                owned = {pid: process for pid, process in owned.items() if pid not in changed}
            observed = {}
            for item in sockets:
                process = owned.get(item.pid)
                if process is None:
                    continue
                key = (item.pid, process.created_at, item.protocol, item.local_address,
                       item.local_port, item.remote_address, item.remote_port)
                old = self._current.get(key)
                if old is None:
                    self._sequence += 1
                    old = ConnectionRecord(f"connection-{self._sequence}", item.pid, process.name,
                        process.created_at, item.protocol, item.local_address, item.local_port,
                        item.remote_address, item.remote_port, item.state, captured_at, captured_at)
                observed[key] = replace(old, state=item.state, last_seen=captured_at)
            for key, old in self._current.items():
                if key in observed:
                    continue
                if errors:
                    observed[key] = replace(old, state="UNKNOWN")  # 部分表不可读时保留最后一次事实及时间。
                else:
                    self._history.append(replace(old, closed_at=captured_at))  # 只说明本轮未再观察到。
            self._current = observed
            self._history = self._history[-self.history_limit:] if self.history_limit else []
            entries = tuple(sorted(observed.values(), key=lambda row: row.first_seen, reverse=True))
            entries += tuple(reversed(self._history))  # 历史上限仅约束结束记录，不截断当前连接。
            return NetworkSnapshot(captured_at, entries, tuple(errors), time.monotonic() - started)
