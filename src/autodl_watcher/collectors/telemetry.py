from __future__ import annotations  # Telemetry API 采集器 — 通过 HTTP GET 请求自建 API 获取物理 GPU 显存快照。

import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from ..config import TelemetryConfig, validate_telemetry_endpoint
from ..models import GpuSample, PlatformHost
from ..telemetry_auth import TelemetryAuthError, origin, request_auth
from ..network_activity import attach_requests


def _parse_received_at(value: str) -> datetime:  # 解析 Telemetry API 返回的时间戳字符串。
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    aware = datetime.fromisoformat(text)
    if aware.tzinfo is None:
        return aware
    return aware.astimezone().replace(tzinfo=None)  # 统一为本地无时区时间，与主循环 datetime.now() 比较新鲜度。


def parse_telemetry_payload(payload: dict[str, Any], only_online: bool = True) -> list[GpuSample]:  # 解析 Telemetry API 的 JSON 响应，返回 GpuSample 列表。
    result: list[GpuSample] = []
    for item in payload.get("items", []):
        host_status = str(item.get("host_status", "")).lower()
        if only_online and host_status != "online":
            continue
        try:
            result.append(
                GpuSample(
                    host=str(item["host"]),
                    gpu_index=int(item["gpu_index"]),
                    gpu_name=str(item["name"]),
                    util_pct=float(item["util_gpu"]),
                    memory_used_mb=int(round(float(item["mem_used_mb"]))),
                    memory_total_mb=int(round(float(item["mem_total_mb"]))),
                    observed_at=_parse_received_at(str(item["received_at"])),  # 采用服务端记录的接收时间，不能用本次拉取时间掩盖旧数据。
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # 单条解析失败不影响整体
    return result


def filter_samples_to_platform_candidates(
    samples: list[GpuSample],
    platform_hosts: list[PlatformHost],
) -> tuple[list[GpuSample], tuple[str, ...], tuple[str, ...]]:  # 两层门控过滤 — 这是整个系统最关键的交叉验证步骤。
    visible = {item.host for item in platform_hosts}  # 以当前账号平台实际可见的主机作为访问范围。
    candidates = {item.host for item in platform_hosts if item.free_count > 0}  # 物理显存空闲还不够，平台必须同时存在可分配入口空位。
    accepted = [sample for sample in samples if sample.host in candidates]
    no_slot = tuple(sorted(visible - candidates))  # 区分平台可见但没空位，与平台根本不可见这两种排除原因。
    unauthorized = tuple(sorted({sample.host for sample in samples if sample.host not in visible}))
    return accepted, no_slot, unauthorized


class TelemetryUnavailable(RuntimeError):
    """GPU HTTP 快照不可用或处于退避期，不能用于开机判断。"""


class TelemetryAuthenticationRequired(TelemetryUnavailable):
    """GPU 采集站需要认证；与 AutoDL 平台登录会话无关。"""


class TelemetryApiCollector:  # Telemetry API 采集器 — 通过 HTTP GET 拉取物理 GPU 实时数据。

    def __init__(self, config: TelemetryConfig) -> None:  # 初始化 Telemetry API 采集器和可复用的 HTTP 会话。
        self.config = config
        self._failed_rounds = 0
        self._retry_at = 0.0
        self._site_auth = None
        self._form_authenticated = False  # 只复用此Session的内存Cookie，不写个人浏览器或磁盘。
        self._session = requests.Session()  # 复用 HTTP 连接；默认仍遵循此进程的代理环境。
        attach_requests(self._session, "GPU采集站")  # 登录与采样复用原Session，记录请求实际端点且不读取凭据。
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "User-Agent": "autodl-gpu-watcher/0.5.1",
            }
        )

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _retry_after(response: requests.Response | None) -> float:
        value = response.headers.get("Retry-After") if response is not None else None
        if not isinstance(value, str):
            return 0.0
        if value.strip().isdigit():
            return float(value.strip())
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        return (isinstance(exc, requests.HTTPError) and exc.response is not None
                and exc.response.status_code >= 500)

    def collect(self) -> list[GpuSample]:
        endpoint = validate_telemetry_endpoint(self.config.endpoint)  # 本地配置错误不计入网络退避。
        try:
            auth = request_auth(endpoint, getattr(self.config, "settings_root", None))
        except TelemetryAuthError as exc:
            raise TelemetryAuthenticationRequired(f"GPU 采集站认证待配置：{exc}") from None
        identity = (origin(endpoint), auth)  # 同站点密码、方式或开关变化后，不得沿用旧身份Cookie。
        if self._site_auth is not None and identity != self._site_auth:
            self._session.cookies.clear()
            self._form_authenticated = False
            self._retry_at = 0.0  # 新设置可以立即尝试，不继续等待旧密码的冷却。
        self._site_auth = identity
        remaining = self._retry_at - time.monotonic()
        if remaining > 0:
            raise TelemetryUnavailable(
                f"Telemetry HTTP 冷却中，至少 {math.ceil(remaining)} 秒后重试；本轮未发送请求")
        if auth is not None and auth.mode == "form" and not self._form_authenticated:
            self._login(endpoint, auth)  # 每轮最多一次登录POST；失败不进入下面的GET重试循环。
        attempts = min(self.config.max_attempts, 2)
        for attempt in range(1, attempts + 1):
            response = None
            try:
                try:
                    response = self._session.get(
                        endpoint,
                        params={"_t": int(time.time() * 1000)},
                        timeout=self.config.timeout_seconds,
                        allow_redirects=False,  # 不把认证Cookie带往服务器指定的其他目标。
                        **({"auth": (auth.username, auth.password)} if auth is not None and auth.mode == "basic" else {}),
                    )
                    if response.status_code in (401, 403):
                        self._form_authenticated = False
                        reason = ("需要认证；请在全局设置中启用正确的站点认证方式" if auth is None else
                                  "认证未通过；请核对认证方式和密码" if response.status_code == 401 else
                                  "拒绝访问；请检查站点账号权限")
                        self._auth_failure(f"GPU 采集站{reason}（HTTP {response.status_code}）；至少60秒后重试")
                    if response.status_code in (301, 302, 303, 307, 308):
                        self._form_authenticated = False
                        self._auth_failure("GPU 采集API返回跳转；请核对API地址及认证方式，未跟随跳转，至少60秒后重试")
                    response.raise_for_status()
                    samples = parse_telemetry_payload(response.json(), only_online=self.config.only_online)
                finally:
                    if response is not None:
                        response.close()
                self._failed_rounds = 0
                self._retry_at = 0.0
                return samples
            except (requests.RequestException, ValueError) as exc:
                retry_after = self._retry_after(response)
                if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
                    # 仅清除此采集器的连接池；Session仍可重用，不影响SSH或平台会话。
                    self._session.close()
                if attempt < attempts and self._is_retryable(exc) and not retry_after:
                    if self.config.retry_delay_seconds > 0:
                        time.sleep(self.config.retry_delay_seconds)
                    continue
                self._failed_rounds += 1
                delay = max(min(60 * 2 ** min(self._failed_rounds - 1, 3), 300), retry_after)
                self._retry_at = time.monotonic() + delay
                # 成功响应也可能返回损坏 JSON；保留格式错误类型，不显示正文、URL 或凭据。
                status = (_safe_error(exc) if isinstance(exc, ValueError) else
                          f"HTTP {response.status_code}" if response is not None else _safe_error(exc))
                raise TelemetryUnavailable(
                    f"Telemetry HTTP 采集失败（本轮 {attempt} 次请求，连续 {self._failed_rounds} 轮失败；"
                    f"至少 {math.ceil(delay)} 秒后重试）：{status}") from exc

    def _auth_failure(self, message):
        self._failed_rounds += 1
        self._retry_at = time.monotonic() + 60  # 认证失败不在本轮重试，也不自动尝试其他认证方式。
        raise TelemetryAuthenticationRequired(message)

    def _login(self, endpoint, auth):
        response = None
        self._session.cookies.clear()  # 重新登录必须建立新会话，不能用旧Cookie掩盖错误密码。
        try:
            response = self._session.post(urljoin(endpoint, "/login"),
                data={"password": auth.password, "next": urlsplit(endpoint).path or "/"},
                timeout=self.config.timeout_seconds, allow_redirects=False)  # 密码仅发送到已配置HTTPS同源。
            if response.status_code in (401, 403):
                self._auth_failure(f"GPU 采集站密码认证未通过（HTTP {response.status_code}）；未自动重试，至少60秒后重试")
            response.raise_for_status()
            location = response.headers.get("Location", "")
            if response.status_code not in (302, 303) or not location or not self._session.cookies:
                self._auth_failure("GPU 采集站密码认证未建立会话；请检查密码和认证方式，至少60秒后重试")
            try:
                same_origin = origin(urljoin(endpoint, location)) == origin(endpoint)
            except TelemetryAuthError:
                same_origin = False  # 站点返回损坏的Location也只能停止，不能绕过冷却。
            if not same_origin:
                self._auth_failure("GPU 采集站登录跳转到其他来源，已停止；未跟随跳转，至少60秒后重试")
            self._form_authenticated = True  # 此标记仅表示有登录Cookie，仍须下一步API JSON采集成功。
        except requests.RequestException as exc:
            self._failed_rounds += 1
            delay = max(60, self._retry_after(response))
            self._retry_at = time.monotonic() + delay
            status = f"HTTP {response.status_code}" if response is not None else _safe_error(exc)
            raise TelemetryUnavailable(f"GPU 采集站密码登录失败（本轮1次登录，未自动重试；至少{math.ceil(delay)}秒后重试）：{status}") from None
        finally:
            if response is not None:
                response.close()


def _safe_error(exc):
    if isinstance(exc, requests.Timeout):
        return "HTTP 请求超时"
    if isinstance(exc, requests.ConnectionError):
        return "HTTP 连接失败（RemoteDisconnected）" if "RemoteDisconnected" in repr(exc.args) else "HTTP 连接失败"
    if isinstance(exc, ValueError):
        return "响应不是有效 JSON 或 GPU 数据格式无效（invalid JSON）"
    return type(exc).__name__  # 异常原文可能含带 token 的网址或代理认证信息，不能直接显示。
