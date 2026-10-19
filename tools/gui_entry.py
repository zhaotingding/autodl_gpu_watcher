"""保留双击和 PyInstaller 脚本入口；启动逻辑统一在包内。"""
import os
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    from autodl_watcher.launcher import gui_main
    root = None if getattr(sys, "frozen", False) else Path(
        os.environ.get("AUTODL_APP_ROOT", Path(__file__).resolve().parents[1]))
    try:
        gui_main(root=root)
    except SystemExit as exc:
        if exc.code != 1:
            raise  # 脚本兼容原来的弹窗错误路径；安装命令仍返回失败状态。


if __name__ == "__main__":
    main()
