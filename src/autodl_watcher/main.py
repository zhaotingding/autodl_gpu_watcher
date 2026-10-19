"""命令行组合入口；采样与电源流程统一由 monitor.run_monitor 执行。"""
import argparse
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from .cli import positive_seconds
from .config import load_config
from .monitor import _enable_ansi_colors, run_monitor
from .paths import default_config_path, prepare_application

def _build_parser(default_host: str) -> argparse.ArgumentParser:  # 构建 CLI 参数解析器。
    parser = argparse.ArgumentParser( description="监控指定物理服务器，并按实时入口空位自动启动对应固定实例。" )
    parser.add_argument( "--host", default=default_host, help="目标物理服务器。可写 203、gpu-203；默认 %(default)s。", )
    parser.add_argument( "--entry", default="auto", help="入口选择。默认 auto；也可强制写 1、2、203-2。", )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--monitor-only", action="store_true", help="仅采集监控数据，不判定或执行开关机。")
    mode.add_argument("--dry-run", action="store_true", help="模拟开机判定，不真正开机。")
    mode.add_argument("--live", action="store_true", help="覆盖配置，真实发送开机请求。")
    parser.add_argument("--config", default=str(default_config_path()), help="配置文件路径")
    parser.add_argument("--user", help="占用列表中的本人用户名")
    parser.add_argument("--poll-seconds", type=positive_seconds, help="采样间隔（秒）")
    parser.add_argument("--ui", action="store_true", help=argparse.SUPPRESS)  # GUI接收图表及整组日志消息。
    parser.add_argument("--stop-file", type=Path, help="UI 的安全停止信号文件")
    parser.add_argument("--max-cycles", type=int, default=0, help="限定轮数，0 为持续运行")
    parser.add_argument("--no-login", action="store_true", help="登录失效时退出，由 UI 完成登录")
    parser.add_argument("--convert-no-gpu", action="store_true", help="目标达标时关闭当前账号全部无卡实例，再有卡开机；需固定 --entry 和 --live。")
    parser.add_argument("--runtime-dir", type=Path, help="独立运行数据目录")
    return parser


def apply_monitor_options(config, args):  # UI 与 CLI 共用配置转换。
    poll = args.poll_seconds if args.poll_seconds is not None else config.monitor.poll_seconds
    positive_seconds(str(poll))
    config = replace(config,  # 配置对象不可变；用副本承接本次 UI/CLI 覆盖值。
        monitor=replace(config.monitor, poll_seconds=poll,
                        max_sample_gap_seconds=max(config.monitor.max_sample_gap_seconds, poll * 2)),
        occupancy=replace(config.occupancy,
            self_user=args.user.strip() if args.user is not None else config.occupancy.self_user))
    if args.runtime_dir:  # 隔离诊断日志及连续采样状态。
        root = args.runtime_dir.resolve()
        config = replace(config, runtime=replace(config.runtime,
            state_file=root / "state.json", log_file=root / "watcher.log"))
    if args.max_cycles < 0:
        raise ValueError("max-cycles 不能为负数")
    return config


def main(argv=None) -> None:  # 主入口：初始化各组件后进入无限监控循环。
    _enable_ansi_colors()
    prepare_application()
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--config", default=str(default_config_path()))
    config_path = Path(preliminary.parse_known_args(argv)[0].config).resolve()  # 先取配置路径，再用文件内容构造完整参数默认值。
    load_dotenv(config_path.parent / ".env")
    config = load_config(config_path)
    parser = _build_parser(config.auto_start.default_host)
    args = parser.parse_args(argv)
    try:
        config = apply_monitor_options(config, args)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))

    run_monitor(config, args, config_path, parser)  # 所有启动方式最终复用这一条监控业务链。


if __name__ == "__main__":
    main()
