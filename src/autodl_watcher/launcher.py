"""源码脚本、安装入口和冻结 EXE 共用的启动边界。"""
from __future__ import annotations

import ctypes
from pathlib import Path
import os
import sys
import traceback

from . import __version__
from .paths import application_root, prepare_application


def _version() -> bool:
    if "--version" not in sys.argv[1:]:
        return False
    print(f"Auto算力与训练调度 {__version__}")
    return True


def gui_main(*, root: Path | None = None) -> None:
    """延迟加载 Tk；pythonw 启动异常仍写日志并给出可操作说明。"""
    if _version():
        return
    root = Path(root) if root is not None else application_root()
    log_path = root / ".ui/startup.log"
    streams = sys.stdout, sys.stderr
    previous_root = os.environ.get("AUTODL_APP_ROOT")
    try:
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
    prepare_application()
    from .main import main
    main()


def worker_main() -> None:
    """后台任务使用同一安装包与资料根；不另起调度循环。"""
    root = prepare_application()
    os.environ["AUTODL_APP_ROOT"] = str(root)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", line_buffering=True, write_through=True)
    task, *args = sys.argv[1:]
    sys.argv = [sys.argv[0], *args]
    if task == "monitor":
        from .main import main as run
    elif task == "discover":
        from .gui import discover as run
    elif task == "login":
        from .login import main as run
    elif task == "icon":
        from .exe_icon import main as run
    else:
        raise ValueError(f"Unknown worker task: {task}")
    run()
