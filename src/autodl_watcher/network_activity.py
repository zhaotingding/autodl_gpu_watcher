"""只观察业务已经发出的请求；不探测、不解析凭据、不改变请求次数。"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
import threading
import time

from .network_attribution import parse_observation

NETWORK_PREFIX = "WATCHER_NETWORK "  # worker向GUI发送脱敏观察事件，不写监控日志。
_observer = None
_lock = threading.Lock()


def set_observer(callback):
    global _observer
    with _lock:
        previous, _observer = _observer, callback  # 临时入口恢复原观察器，避免测试或嵌入调用串扰。
    return previous


def emit_observation(value):
    print(NETWORK_PREFIX + json.dumps(value, ensure_ascii=False), flush=True)


def publish(url, purpose, **fields):
    callback = _observer
    if callback is None:
        return  # 普通CLI不增加输出或保存请求资料。
    try:
        observation = parse_observation({"pid": os.getpid(), "observed_at": time.time(),
            "url": url, "purpose": purpose, **fields})  # URL先脱敏，再跨进程传递或加入有界内存。
        if observation is not None:
            callback(asdict(observation))
    except Exception:
        pass  # 诊断回调不能使原本成功的业务请求失败。


def attach_requests(session, purpose):
    hooks = getattr(session, "hooks", None)
    if not isinstance(hooks, dict):
        return  # 模拟或非requests会话不强行改写。

    def observed(response, *args, **kwargs):
        if _observer is None:
            return
        fields = {"evidence": "process-request"}
        try:
            connection = getattr(response.raw, "_connection", None)
            sock = getattr(connection, "sock", None)
            if sock is not None:
                local, peer = sock.getsockname(), sock.getpeername()
                fields.update(evidence="requests", local_address=local[0], local_port=local[1],
                              remote_address=peer[0], remote_port=peer[1])  # 响应钩子在消费响应体前读取实际socket。
        except (AttributeError, OSError, ValueError):
            pass  # 连接已释放则只保留实际请求，不能猜测TCP四元组。
        publish(response.url, purpose, **fields)

    hooks.setdefault("response", []).append(observed)  # 不消费正文，不改流式模式、重试或连接池策略。


def observe_playwright(response, *, context=None, browser_process_name=""):
    if _observer is None:
        return
    fields = {"evidence": "process-request", "scope": "descendants"}
    try:
        url = response.url
        if context is not None:
            fields["process_name"] = "node.exe" if os.name == "nt" else "node"
            try:
                driver = context._impl_obj._connection._transport._proc
                if type(driver.pid) is int and driver.pid > 0:
                    fields.update(pid=driver.pid, scope="process")  # API请求由Playwright的Node驱动发出。
            except AttributeError:
                pass  # 驱动内部接口变化时，保留worker后代范围的候选证据。
        else:
            fields["process_name"] = browser_process_name or ("msedge.exe" if os.name == "nt" else "msedge")
        try:
            server_addr = getattr(response, "server_addr", None)
            peer = server_addr() if callable(server_addr) else None  # APIResponse从Playwright1.61才提供该接口。
        except Exception:
            peer = None  # 已释放或老版本响应没有对端，仍展示确实发生过的请求网址。
        if isinstance(peer, dict) and peer.get("ipAddress") and peer.get("port"):
            fields.update(evidence="browser", remote_address=peer["ipAddress"], remote_port=peer["port"])
        publish(url, "AutoDL平台API" if context is not None else "AutoDL浏览器", **fields)
    except Exception:
        pass  # 老版或已释放响应只缺少观察证据，业务照常进行。
