"""本应用的请求观测模型；只关联已有证据，不做DNS、探测或抓包。"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import math
from urllib.parse import urlsplit, urlunsplit


def safe_target(value):
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https", "ssh"} or not parts.hostname:
            return "地址未配置"
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        netloc = host + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))[:2048]  # 密码、查询参数及片段不进入记录。
    except (TypeError, ValueError):
        return "地址格式无效"


def normalized_address(value):
    try:
        address = ipaddress.ip_address(str(value))
        mapped = getattr(address, "ipv4_mapped", None)
        return str(mapped or address)  # 同一个IPv4端点可能由socket报告为IPv4-mapped IPv6。
    except ValueError:
        return ""


def is_loopback(value):
    try:
        return ipaddress.ip_address(normalized_address(value)).is_loopback
    except ValueError:
        return False


def address_hint(*addresses):
    benchmark = ipaddress.ip_network("198.18.0.0/15")
    for value in addresses:
        try:
            address = ipaddress.ip_address(normalized_address(value))
        except ValueError:
            continue
        if address.version == 4 and address in benchmark:
            return "含198.18/15保留地址，可能为TUN/Fake-IP；实际DIRECT/PROXY路由待核对"
    return ""


@dataclass(frozen=True, slots=True)
class RequestObservation:
    pid: int
    observed_at: float
    url: str
    application_protocol: str
    purpose: str
    evidence: str
    local_address: str = ""
    local_port: int = 0
    remote_address: str = ""
    remote_port: int = 0
    process_started_at: float = 0
    scope: str = "process"
    process_name: str = ""


def parse_observation(value):
    if isinstance(value, RequestObservation):
        value = {name: getattr(value, name) for name in value.__dataclass_fields__}
    if not isinstance(value, dict):
        return None
    try:
        pid = int(value["pid"])
        observed = float(value["observed_at"])
        born = float(value.get("process_started_at", 0))
        url = safe_target(value["url"])
        scheme = urlsplit(url).scheme
        evidence, scope = value.get("evidence", "requests"), value.get("scope", "process")
        local_port, remote_port = int(value.get("local_port", 0)), int(value.get("remote_port", 0))
        if (pid <= 0 or not math.isfinite(observed) or observed <= 0 or not math.isfinite(born) or born < 0
                or scheme not in {"http", "https"} or evidence not in {"requests", "browser", "process-request"}
                or scope not in {"process", "descendants"}
                or not 0 <= local_port <= 65535 or not 0 <= remote_port <= 65535):
            return None
        purpose = str(value.get("purpose", "应用请求")).replace("\r", " ").replace("\n", " ").replace("\0", "")[:120]
        process_name = str(value.get("process_name", "")).replace("\0", "")[:120]
        return RequestObservation(pid, observed, url, scheme.upper(), purpose, evidence,
            normalized_address(value.get("local_address", "")), local_port,
            normalized_address(value.get("remote_address", "")), remote_port, born, scope, process_name)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def observation_process_matches(observation, process, owned):
    current, visited = process, set()
    while current is not None and current.pid not in visited:
        visited.add(current.pid)
        if current.pid == observation.pid:
            return (observation.observed_at >= max(current.created_at, process.created_at)
                    and (not observation.process_started_at
                         or abs(observation.process_started_at - current.created_at) < .001))
        if observation.scope != "descendants":
            break
        current = owned.get(current.parent_pid)
    return False
