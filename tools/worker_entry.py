"""冻结 worker 及源码调试共用包内任务分派。"""
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autodl_watcher.launcher import worker_main as main

if __name__ == "__main__":
    main()
