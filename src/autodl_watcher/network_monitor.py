"""把本机连接快照限定到本实例，保留有界的观察历史；不建立任何网络连接。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import deque
import os
import threading
import time

from .network_attribution import (address_hint, is_loopback, normalized_address,
                                  observation_process_matches, parse_observation, safe_target)
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
    purpose: str = ""
    logical_target: str = ""
    application_protocol: str = ""
    request_urls: tuple[str, ...] = ()
    attribution: str = ""
    network_hint: str = ""
    classification: str = ""
    request_observed_at: float = 0
    attribution_strength: int = 0


@dataclass(frozen=True, slots=True)
class ServiceTarget:
    pid: int
    kind: str
    purpose: str
    target: str


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    captured_at: float
    entries: tuple[ConnectionRecord, ...]
    errors: tuple[str, ...]
    elapsed_seconds: float
    targets: tuple[ServiceTarget, ...] = ()
    observations: tuple = ()


class NetworkMonitor:
    def __init__(self, root_pid=None, *, reader=read_system_snapshot, history_limit=300):
        self.root_pid = os.getpid() if root_pid is None else root_pid  # 只追踪当前 GUI 实例，不按程序名扩大范围。
        self.reader, self.history_limit = reader, max(0, history_limit)
        self._root_started = None
        self._owned = {}  # PID 必须与创建时间一起匹配，防止操作系统复用 PID 后误归属。
        self._current, self._history = {}, []
        self._sequence = 0
        self._targets = ()
        self._incoming = deque(maxlen=512)
        self._observations = deque(maxlen=512)  # 请求证据只在内存保留有界窗口，不能成为无限日志。
        self._observation_lock = threading.Lock()
        self._lock = threading.Lock()  # 面板反复打开也不能并发读取或写坏历史。

    def set_targets(self, targets):
        self._targets = tuple(ServiceTarget(int(item.get("pid", 0)), str(item["kind"]),
            str(item["purpose"]), safe_target(item["target"]) if item.get("target") else "")
            for item in targets)  # 主线程只换不可变快照，不等待后台原生读表锁。

    def add_observation(self, value):
        observation = parse_observation(value)
        if observation is None:
            return False
        with self._observation_lock:
            self._incoming.append(observation)  # 不等待读取系统连接表的锁，GUI接收请求事件仍及时。
        return True

    def _request_evidence(self, process, record, owned, captured_at):
        candidates, exact, process_requests = [], [], []
        for observation in self._observations:
            if (not record.protocol.startswith("TCP") or observation.observed_at > captured_at + 1
                    or observation.observed_at < record.first_seen - 30
                    or not observation_process_matches(observation, process, owned)
                    or (observation.process_name and observation.process_name.casefold() != process.name.casefold())):
                continue
            if observation.evidence == "process-request":
                if record.remote_port and record.state != "LISTEN" and not is_loopback(record.remote_address):
                    process_requests.append(observation)  # 只证明该进程发过请求，不能断言命中了这个TCP。
                continue
            if (observation.remote_port != record.remote_port
                    or normalized_address(observation.remote_address) != normalized_address(record.remote_address)
                    or not observation.remote_address):
                continue
            if observation.evidence == "requests" and observation.local_address and observation.local_port:
                if (observation.scope == "process" and observation.local_port == record.local_port
                        and normalized_address(observation.local_address) == normalized_address(record.local_address)):
                    exact.append(observation)
                # 已知本机端点却不匹配，应排除此连接，不能降为同对端候选。
            else:
                candidates.append(observation)  # 缺少本机端口，不能把浏览器对端候选写成精确连接映射。
        matches = sorted(exact or candidates or process_requests, key=lambda item: item.observed_at)
        if not matches:
            return {}
        urls = tuple(dict.fromkeys(item.url for item in reversed(matches)))[:8]
        latest = matches[-1]
        return dict(application_protocol=" / ".join(dict.fromkeys(item.application_protocol for item in matches)),
                    request_urls=urls, request_observed_at=latest.observed_at, purpose=latest.purpose,
                    attribution_strength=3 if exact else 2 if candidates else 1,
                    logical_target=urls[0], attribution="已匹配：请求PID及TCP源/目标IP和端口" if exact else
                    "候选：请求进程范围及对端；本机端口未核对" if candidates else
                    "候选：进程请求记录，未核对端点（不能证明此TCP访问了该网址）")

    @staticmethod
    def _context(process, owned, targets):
        visited = set()
        while process is not None and process.pid not in visited:
            visited.add(process.pid)
            if process.pid in targets:
                return targets[process.pid]
            process = owned.get(process.parent_pid)
        return None

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
            with self._observation_lock:
                self._observations.extend(self._incoming)
                self._incoming.clear()
            self._observations = deque((item for item in self._observations
                if captured_at - 900 <= item.observed_at <= time.time() + 5), maxlen=512)  # 读表期间新来的证据留给下轮，不能误删。
            targets = self._targets
            by_pid = {item.pid: item for item in targets if item.pid}
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
                context = self._context(process, owned, by_pid)
                classification = ("listener" if item.state == "LISTEN" else "udp" if item.protocol.startswith("UDP")
                                  else "internal" if is_loopback(item.remote_address) else "remote")
                attributes = dict(purpose=context.purpose if context else "系统子进程",
                    logical_target=context.target if context and context.kind == "SSH" else "",
                    classification=classification, network_hint=address_hint(item.local_address, item.remote_address))
                if context and context.kind == "SSH":
                    attributes.update(application_protocol="SSH", attribution="应用管理的SSH进程及连接配置")
                else:
                    attributes.update(self._request_evidence(process, old, owned, captured_at))
                    if (old.attribution_strength > attributes.get("attribution_strength", 0)
                            or (not attributes.get("application_protocol") and old.application_protocol)):
                        # 同一持续活动连接保留更强的已知事实；新弱候选不能覆盖较早的端点匹配。
                        attributes.update(application_protocol=old.application_protocol, request_urls=old.request_urls,
                            request_observed_at=old.request_observed_at, logical_target=old.logical_target,
                            attribution=old.attribution, attribution_strength=old.attribution_strength, purpose=old.purpose)
                    if classification == "internal" and not attributes.get("application_protocol"):
                        paired = any(other.pid == item.pid and other.protocol == item.protocol
                            and other.local_port == item.remote_port and other.remote_port == item.local_port
                            and normalized_address(other.local_address) == normalized_address(item.remote_address)
                            and normalized_address(other.remote_address) == normalized_address(item.local_address)
                            for other in sockets)
                        attributes.update(application_protocol="本机内部通信" if paired else "回环上层未核对",
                            attribution="系统表：同PID成对回环端点" if paired else "系统表：本机回环端点，具体调用未核对")
                observed[key] = replace(old, state=item.state, last_seen=captured_at, **attributes)
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
            return NetworkSnapshot(captured_at, entries, tuple(errors), time.monotonic() - started,
                                   targets, tuple(self._observations))
