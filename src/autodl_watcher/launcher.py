"""源码GUI、CLI和同一Conda环境后台任务共用的启动边界。"""
from __future__ import annotations

import ctypes
from importlib import import_module
import importlib.metadata
import json
import re
from pathlib import Path
import os
import site
import sys
import traceback

from . import __version__
from .paths import application_root, prepare_application, source_root


def project_python() -> str:
    """后台始终使用当前项目Conda中的控制台解释器，以保留日志和输入管道。"""
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")  # 只换同目录程序，不从PATH寻找另一个环境。
    if not executable.is_file():
        raise FileNotFoundError(f"项目解释器不存在：{executable}；请按README修复专用Conda环境")
    return str(executable)


def child_environment(root: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)  # 防止继承全局Python的标准库位置。
    env.pop("PYTHONPATH", None)
    checkout = source_root()
    if checkout is not None:
        env["PYTHONPATH"] = str(checkout / "src")  # 仅增加当前源码，不带入其它项目的路径。
    env["AUTODL_APP_ROOT"] = str(root if root is not None else application_root())
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def check_environment() -> dict:
    """检查conda run实际选择的环境和依赖来源，不安装或修改任何配置。"""
    prefix = Path(sys.prefix).resolve()
    active = os.environ.get("CONDA_PREFIX", "")
    name = os.environ.get("CONDA_DEFAULT_ENV", "")
    hint = "请按README使用 conda run -n <项目环境> --no-capture-output python -s 启动，并在同环境执行 pip install --no-user -e ."
    if (not active or Path(active).resolve() != prefix or not (prefix / "conda-meta").is_dir()
            or name.lower() in {"", "base", "root"} or sys.prefix != sys.base_prefix):
        raise RuntimeError("需要专用Conda环境，不支持base、全局Python或venv。" + hint)
    manager = os.environ.get("CONDA_EXE", "")
    if manager and Path(manager).resolve().parent.parent == prefix:
        raise RuntimeError("当前解释器属于Conda base。" + hint)
    expected = os.environ.get("AUTODL_CONDA_ENV")
    if expected and expected != name:
        raise RuntimeError(f"Conda实际环境与指定环境不一致：{name} / {expected}。" + hint)
    if sys.version_info < (3, 10):
        raise RuntimeError("项目需要Python 3.10及以上，建议3.11。" + hint)
    try:
        requirements = importlib.metadata.requires("autodl-gpu-watcher") or []
    except importlib.metadata.PackageNotFoundError:
        raise RuntimeError("当前项目尚未安装。" + hint) from None
    dependencies = {}
    for requirement in requirements:
        if "extra ==" in requirement:
            continue  # 可选构建工具不属于应用运行所需依赖。
        match = re.match(r"[A-Za-z0-9_.-]+", requirement)
        if match is None:
            continue
        package = match.group()
        try:
            distribution = importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError:
            raise RuntimeError(f"项目环境缺少依赖：{package}。" + hint) from None
        location = Path(distribution.locate_file("")).resolve()
        if not location.is_relative_to(prefix):
            raise RuntimeError(f"依赖{package}来自项目环境之外：{location}。" + hint)
        module_name = {"PyYAML": "yaml", "python-dotenv": "dotenv", "Pillow": "PIL"}.get(
            package, package.replace("-", "_"))  # 仅转换发行名；依赖清单仍读取 pyproject 的安装元数据。
        try:
            module = import_module(module_name)  # 检查实际导入，避免正确元数据掩盖其他目录中的同名模块。
        except (ImportError, OSError) as exc:
            raise RuntimeError(f"无法导入依赖{package}：{exc}。" + hint) from exc
        origin = getattr(module, "__file__", None)
        module_paths = [Path(origin).resolve()] if origin else [
            Path(item).resolve() for item in getattr(module, "__path__", ())]  # 命名空间包检查每个搜索目录。
        if not module_paths or any(not item.is_relative_to(prefix) for item in module_paths):
            raise RuntimeError(f"依赖{package}实际导入来自项目环境之外或无法定位：{module_paths}。" + hint)
        dependencies[package] = {"version": distribution.version, "location": str(location),
                                 "module_paths": [str(item) for item in module_paths]}
    return {"environment": name, "prefix": str(prefix), "python": project_python(),
            "source_root": str(source_root() or Path(__file__).resolve().parent),
            "user_site_enabled": site.ENABLE_USER_SITE, "dependencies": dependencies}


def _version() -> bool:
    if "--version" not in sys.argv[1:]:
        return False
    print(f"Auto算力与训练调度 {__version__}")
    return True


def gui_main(*, root: Path | None = None) -> None:
    """延迟加载 Tk；pythonw 启动异常仍写日志并给出可操作说明。"""
    if "--check-environment" in sys.argv[1:]:
        try:
            print(json.dumps(check_environment(), ensure_ascii=False))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
        return
    if _version():
        return
    root = Path(root) if root is not None else application_root()
    log_path = root / ".ui/startup.log"
    streams = sys.stdout, sys.stderr
    previous_root = os.environ.get("AUTODL_APP_ROOT")
    try:
        check_environment()  # GUI启动前检查，错误沿用现有日志和窗口提示。
        root = prepare_application(root)
        log_path.parent.mkdir(exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            sys.stdout = sys.stderr = log  # GUI 的后台采样另有独立管道。
            try:
                try:
                    import tkinter  # noqa: F401；在导入整个界面前给出安装指导。
                except ImportError as exc:
                    raise RuntimeError("缺少 Tkinter：Windows 请重新安装带 Tcl/Tk 的 Python；"
                                       "Debian/Ubuntu 请安装 python3-tk 后再启动。") from exc
                os.environ["AUTODL_APP_ROOT"] = str(root)
                from .gui import main
                main()
            except Exception:
                traceback.print_exc()
                raise
    except Exception as exc:
        message = f"启动失败：{exc}\n详细信息：{log_path}"
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "Auto算力与训练调度", 16)
        elif streams[1] is not None:
            print(message, file=streams[1])
        raise SystemExit(1) from None
    finally:
        sys.stdout, sys.stderr = streams
        if previous_root is None:
            os.environ.pop("AUTODL_APP_ROOT", None)
        else:
            os.environ["AUTODL_APP_ROOT"] = previous_root


def monitor_main() -> None:
    if _version():
        return
    check_environment()
    prepare_application()
    from .main import main
    main()
