"""安装版后台和内部worker的冻结程序入口。"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="autodl-backend")
    parser.add_argument("mode", choices=("bridge", "monitor", "discover", "login", "exe-icon", "check-environment"))
    args = parser.parse_args(sys.argv[1:2])
    remaining = sys.argv[2:]  # worker的--help和业务参数由该worker自己的解析器处理。
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    # 业务模块来自同一随包集合；先加载本地扩展，再恢复外部OpenSSH/Edge的DLL查找路径。
    from autodl_watcher import desktop_backend, discovery, exe_icon, login
    from autodl_watcher.main import main as monitor
    from autodl_watcher.launcher import check_installed_environment

    environment = check_installed_environment()
    if os.name == "nt" and not ctypes.windll.kernel32.SetDllDirectoryW(None):
        raise OSError("无法恢复外部程序DLL搜索路径")
    sys.argv = [sys.argv[0], *remaining]
    if args.mode == "check-environment":
        if remaining:
            parser.error("环境检查没有额外参数")
        print(json.dumps(environment, ensure_ascii=False))
        return
    if args.mode == "bridge":
        return desktop_backend.main()
    if args.mode == "monitor":
        return monitor(remaining)
    if args.mode in {"discover", "login"}:
        argparse.ArgumentParser(prog="autodl-backend " + args.mode).parse_args(remaining)
        return discovery.discover() if args.mode == "discover" else login.main()
    icon_parser = argparse.ArgumentParser(prog="autodl-backend exe-icon")
    icon_parser.add_argument("pid", type=int)
    icon_parser.add_argument("executable")
    icon_parser.parse_args(remaining)
    return exe_icon.main()


if __name__ == "__main__":
    raise SystemExit(main())
