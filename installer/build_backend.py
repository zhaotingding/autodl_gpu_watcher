"""在现有项目Conda环境构建安装包需要的Python后台目录。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autodl_watcher.launcher import check_environment


def main():
    check_environment()
    build = ROOT / "installer/dist/backend-build"
    build.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-s", "-m", "PyInstaller", "--noconfirm", "--onedir", "--console",
        "--name", "autodl-backend", "--distpath", str(ROOT / "installer/dist/backend"),
        "--workpath", str(build / "build"), "--specpath", str(build), "--paths", str(ROOT / "src"),
        "--collect-data", "playwright", "--collect-data", "autodl_watcher",
        "--add-data", str(ROOT / "src/autodl_watcher/remote_probe.py") + ";autodl_watcher"]
    # 默认Electron后台使用无界面发现模块，旧Tk兼容界面仍在开发源码中维护。
    for module in ("tkinter", "autodl_watcher.gui", "autodl_watcher.theme", "autodl_watcher.app_icon",
                   "autodl_watcher.training_ui", "autodl_watcher.training_ai_ui", "autodl_watcher.settings_ui"):
        command.extend(("--exclude-module", module))
    command.append(str(ROOT / "installer/backend_entry.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    print("安装版后台已生成 " + str(ROOT / "installer/dist/backend/autodl-backend"))


if __name__ == "__main__":
    main()
