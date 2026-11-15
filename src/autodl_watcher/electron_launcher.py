"""用已选项目解释器启动Electron，桌面后台继承同一个Conda环境。"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .paths import source_root


def launch(arguments=()):
    from .launcher import child_environment, project_python

    checkout = source_root()
    if checkout is None:
        raise RuntimeError("Electron界面需要项目源码。请克隆仓库，在项目Conda环境安装并按README启动。")
    desktop = checkout / "desktop"
    executable = desktop / "node_modules/electron/dist" / (
        "electron.exe" if os.name == "nt" else "Electron.app/Contents/MacOS/Electron" if sys.platform == "darwin" else "electron")
    if not executable.is_file():
        raise RuntimeError("缺少本项目Electron运行时。请在仓库根的PowerShell依次执行 npm.cmd ci --prefix desktop "
                           "和 npm.cmd run setup-runtime --prefix desktop，然后重新打开程序。")
    environment = child_environment()
    environment.update(AUTODL_PYTHON=project_python(), PYTHONIOENCODING="utf-8")
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    # Electron拥有窗口，Python后台与已有采集worker共用明确的项目解释器。
    result = subprocess.run([str(executable), str(desktop), *arguments], cwd=checkout,
                            env=environment, stdout=sys.stdout, stderr=sys.stderr, check=False)
    if result.returncode:
        raise RuntimeError(f"Electron界面退出，状态码{result.returncode}，请查看.ui/startup.log。")
    return result.returncode
