"""源码 CLI 的兼容入口；安装后可直接运行 autodl-watcher-monitor。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autodl_watcher.launcher import monitor_main

if __name__ == "__main__":
    monitor_main()
