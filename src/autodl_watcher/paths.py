"""启动布局与本机资料根：源码、Windows 成品和 wheel 安装共用同一规则。"""
from __future__ import annotations

from importlib.resources import files
import os
from pathlib import Path
import sys


def source_root() -> Path | None:
    """返回实际源码检出位置；与用户选择的资料目录无关，wheel/成品返回 None。"""
    if getattr(sys, "frozen", False):
        return None
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file() and (checkout / "src/autodl_watcher").is_dir():
        return checkout
    return None


def application_root() -> Path:
    """返回可写资料位置；显式 AUTODL_APP_ROOT 对所有启动方式生效。"""
    override = os.environ.get("AUTODL_APP_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        # 内部 worker 与公开 GUI 使用同一根；通常也由 GUI 显式传入环境变量。
        return executable.parent.parent if executable.stem == "AutoDLWorker" else executable.parent
    checkout = source_root()
    if checkout is not None:
        return checkout
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "autodl-watcher"  # wheel 不能把个人资料写入 site-packages。


def default_config_path(root: Path | None = None) -> Path:
    """本机覆盖配置优先；未配置的新安装使用公开安全示例。此函数不写盘。"""
    root = Path(root) if root is not None else application_root()
    local = root / "config.local.yaml"
    return local if local.is_file() else root / "config.yaml"


def prepare_application(root: Path | None = None) -> Path:
    """首次启动只创建缺失的配置，不覆盖已有用户内容。"""
    root = Path(root) if root is not None else application_root()
    root.mkdir(parents=True, exist_ok=True)
    config = default_config_path(root)
    if not config.exists():
        config.write_bytes(files("autodl_watcher").joinpath("defaults.yaml").read_bytes())
    return root
