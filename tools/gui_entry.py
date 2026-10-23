"""轻量启动器调用的源码入口；启动逻辑统一在包内。"""
import os
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    from autodl_watcher.launcher import gui_main
    root = None if getattr(sys, "frozen", False) else Path(
        os.environ.get("AUTODL_APP_ROOT", Path(__file__).resolve().parents[1]))
    gui_main(root=root)  # 保留失败退出码，供轻量启动器报告环境或启动错误。


if __name__ == "__main__":
    main()
