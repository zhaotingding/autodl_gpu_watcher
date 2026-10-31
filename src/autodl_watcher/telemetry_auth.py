"""GPU 采集站的密码表单/Basic认证；密码只进入 Windows 凭据管理器。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import ai_credentials
from .yaml_settings import read_settings, settings_source, write_settings


class TelemetryAuthError(ValueError):
    pass


AUTH_MODES = {"form": "站点密码登录（/login）", "basic": "HTTP Basic（用户名和密码）"}


@dataclass(frozen=True)
class SiteAuthentication:
    mode: str
    username: str
    password: str = field(repr=False)  # 诊断对象也不能在repr中暴露密码。


def _preferences(root):
    path = Path(root) / ".ui/preferences.yaml"
    if not settings_source(path).exists():
        return path, {}
    value = read_settings(path)
    if not isinstance(value, dict):
        raise TelemetryAuthError("本机监控设置格式无效，请检查 preferences.yaml")
    return path, value


def origin(endpoint):
    try:
        parts = urlsplit(endpoint.strip())
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or parts.password is not None or any(character.isspace() or ord(character) < 32 for character in endpoint)):
            raise ValueError
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        host = parts.hostname.lower()
        host = f"[{host}]" if ":" in host else host
        return f"{parts.scheme}://{host}:{port}"  # 不把 token 查询参数或路径作为凭据目标。
    except (AttributeError, ValueError):
        raise TelemetryAuthError("GPU 采集地址无效；请填写 HTTP/HTTPS 地址，不要在地址中填写用户名或密码") from None


def settings(endpoint, root):
    _, preferences = _preferences(root)
    saved = preferences.get("telemetry", {})
    if not isinstance(saved, dict):
        raise TelemetryAuthError("GPU 采集站设置格式无效")
    mode = saved.get("auth_mode", "basic" if saved.get("password_auth") is True else "form")
    if mode not in AUTH_MODES:
        raise TelemetryAuthError("GPU 采集站认证方式无效，请重新选择")
    return {"endpoint": str(saved.get("endpoint", endpoint)).strip(), "auth_mode": mode,
            "username": str(saved.get("username", "")),
            "password_auth": saved.get("password_auth") is True}


def effective_endpoint(fallback, root):
    try:
        return settings(fallback, root)["endpoint"]  # GUI/CLI/worker 都从同一资料根读取个人覆盖。
    except (OSError, ValueError, TypeError):
        return fallback  # 保留 GUI 原有的坏偏好降级能力；发请求时另行严格检查认证。


def request_auth(endpoint, root=None):
    if root is None:
        return None  # 无资料根的独立采集器不隐式读取维护者的个人凭据。
    try:
        saved = settings(endpoint, root)
    except (OSError, ValueError, TypeError):
        raise TelemetryAuthError("GPU 采集站个人设置读取失败，请检查 preferences.yaml") from None
    if not saved["password_auth"]:
        return None
    if origin(saved["endpoint"]) != origin(endpoint):
        return None  # 个人密码不能附到其他来源的请求。
    if urlsplit(endpoint).scheme != "https":
        raise TelemetryAuthError("站点密码认证需要 HTTPS 地址")
    try:
        password = ai_credentials.read_secret("telemetry", origin(endpoint))
    except ai_credentials.CredentialError:
        raise TelemetryAuthError("无法读取 GPU 采集站密码，请在当前 Windows 用户下重新保存") from None
    if not password:
        raise TelemetryAuthError("尚未保存 GPU 采集站密码，请在全局设置中填写")
    return SiteAuthentication(saved["auth_mode"], saved["username"], password)


def save_settings(endpoint, username, enabled, password, root, auth_mode=None):
    endpoint, username = endpoint.strip(), username.strip()
    if auth_mode is None:
        auth_mode = settings(endpoint, root)["auth_mode"]  # 未指定方式时保留已有选择；旧启用配置仍按Basic读取。
    if auth_mode not in AUTH_MODES:
        raise TelemetryAuthError("GPU 采集站认证方式无效，请重新选择")
    source = origin(endpoint) if endpoint else None
    if source is None and (enabled or password):
        raise TelemetryAuthError("保存站点密码前请先填写采集 API 地址")
    if any(character in username for character in "\r\n\x00"):
        raise TelemetryAuthError("GPU 采集站用户名含无效字符")
    if enabled and urlsplit(endpoint).scheme != "https":
        raise TelemetryAuthError("站点密码认证需要 HTTPS 地址")
    if password:
        ai_credentials.save_secret("telemetry", source, password)  # 空输入保留当前来源的已有密码。
    path, preferences = _preferences(root)
    preferences["telemetry"] = {"endpoint": endpoint, "username": username, "password_auth": bool(enabled),
                                "auth_mode": auth_mode}
    write_settings(path, preferences)  # 合并原偏好，不能丢掉用户名、间隔和电源开关。


def delete_password(endpoint):
    ai_credentials.delete_secret("telemetry", origin(endpoint))
