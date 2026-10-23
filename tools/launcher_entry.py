"""轻量EXE只选择Conda环境并启动源码；此文件只能依赖Python标准库。"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


DEFAULT_ENVIRONMENT = "auto-gpu"  # 无显式参数时固定使用项目环境，不继承其他项目。


class LaunchError(RuntimeError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LaunchError(message)  # 无控制台EXE也由统一错误窗口说明参数问题。


def selected_environment(arguments):
    parser = Parser(description="通过专用Conda环境启动Auto算力与训练调度", add_help=False)
    parser.add_argument("--env")
    parser.add_argument("--conda")
    options, forwarded = parser.parse_known_args(arguments)
    name = options.env if options.env is not None else DEFAULT_ENVIRONMENT  # 只有显式参数能改变目标环境。
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.lower() in {"base", "root"}:
        raise LaunchError("--env 必须是有效的专用Conda环境名，不能指定base或root；无参数双击默认使用auto-gpu。")
    manager = options.conda or os.environ.get("CONDA_EXE") or shutil.which("conda.exe")
    if not manager or not Path(manager).is_file():
        raise LaunchError("未找到Conda管理器；请先安装Conda，或用 --conda 指定conda.exe的完整路径。")
    return name, str(Path(manager).resolve()), forwarded


def launch_environment(root, name, manager):
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)  # 不将启动器或其它项目的模块路径带入业务解释器。
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        bundle = Path(bundle).resolve()
        env["PATH"] = os.pathsep.join(item for item in env.get("PATH", "").split(os.pathsep)
                                      if item and not Path(item).resolve().is_relative_to(bundle))
    env.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1", AUTODL_CONDA_ENV=name,
               AUTODL_CONDA_EXE=manager)
    if getattr(sys, "frozen", False):
        env["AUTODL_LAUNCHER_EXE"] = str(Path(sys.executable).resolve())
    env.setdefault("AUTODL_APP_ROOT", str(root))
    return env


def start(arguments=None):
    name, manager, forwarded = selected_environment(sys.argv[1:] if arguments is None else arguments)
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    entry = root / "tools/gui_entry.py"
    if not entry.is_file() or not (root / "src/autodl_watcher").is_dir():
        raise LaunchError("未找到项目源码；请将AutoDLWatcher.exe放在包含src和tools的仓库根目录。")
    if os.name == "nt" and getattr(sys, "frozen", False):
        if not ctypes.windll.kernel32.SetDllDirectoryW(None):
            raise LaunchError("无法恢复系统DLL搜索路径，已停止启动外部Conda环境。")
    env = launch_environment(root, name, manager)
    command = [manager, "run", "-n", name, "--no-capture-output", "python", "-s", str(entry)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run([*command, "--check-environment"], cwd=root, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=60, creationflags=flags, close_fds=True)
    if result.returncode:
        raise LaunchError((result.stderr or result.stdout).strip() or "Conda环境检查失败")
    try:
        actual = json.loads(result.stdout)
    except ValueError:
        raise LaunchError("Conda环境检查未返回有效结果：" + result.stdout[-1000:]) from None
    if actual.get("environment") != name:
        raise LaunchError("Conda实际选择的环境与指定名称不一致，已停止启动。")
    subprocess.Popen([*command, *forwarded], cwd=root, env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)  # GUI异常写入自己的startup.log；启动器不常驻。


def main():
    try:
        start()
    except (LaunchError, OSError, subprocess.TimeoutExpired) as exc:
        message = (f"无法启动：{exc}\n\n请在PowerShell进入项目目录，按README核对所选环境与依赖。\n"
                   f"默认环境为 {DEFAULT_ENVIRONMENT}；已有环境直接复用，仅在确实缺失时创建。\n"
                   "需要安装或更新依赖时，在所选环境执行（自定义环境请替换名称）：\n"
                   f"conda run -n {DEFAULT_ENVIRONMENT} --no-capture-output python -s -m pip install --no-user -e .\n"
                   "安装需要环境写权限；拒绝访问时核查权限及文件占用，不要改用 --user。\n"
                   "双击还需能从 CONDA_EXE 或 PATH 找到管理器；否则用 --conda 指定完整路径。")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "Auto算力与训练调度 · 启动失败", 16)
        elif sys.stderr:
            print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
