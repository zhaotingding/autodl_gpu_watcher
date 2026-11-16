"""平台主机发现的无界面入口，桌面和随包worker共用。"""
import json
from .config import load_config, validate_telemetry_endpoint
from .login import LocalStartupError
from .network_activity import emit_observation, set_observer
from .paths import default_config_path
from .session import HOSTS_PREFIX as DATA_PREFIX, emit_gpu_samples, emit_session


def discovery_rows(config, hosts, instances):  # 只有账号真实实例UUID与入口/配置同时相符，才显示可开机资格。
    targets = {item.machine_name: item for item in config.auto_start.targets if item.enabled and item.instance_uuid}
    rows = []
    for host in hosts:
        for name, idle, total in host.source_slots:
            existing = [item for item in (instances or []) if item.get("machine_name") == name]
            target = targets.get(name)
            if instances is None:
                label = "未核验"
            elif not existing:
                label = "无实例"
            elif not target or not config.auto_start.enabled:
                label = "待配置"
            elif not any(item.get("instance_uuid") == target.instance_uuid for item in existing):
                label = "实例已变更"
            else:
                label = "可开机"
            rows.append((name, f"{idle}/{total}", label))
    return rows


def discover(root=None, config=None):  # 子进程模式：采集结果编码为一行 JSON，供父进程更新服务器表格。
    from .collectors import PlatformBrowserCollector, TelemetryApiCollector
    from .collectors.telemetry import TelemetryUnavailable
    from .collectors.platform import PlatformAuthenticationError
    config = config or load_config(default_config_path(root))
    collector = PlatformBrowserCollector(config.platform)
    previous = set_observer(emit_observation)  # 一次性刷新同样报告已发生的真实请求。
    try:
        emit_session("checking", "正在核验真实主机接口")
        hosts = collector.collect()
        try:
            instances = collector.get_account_instances()
            if not isinstance(instances, list):
                raise ValueError("实例列表格式错误")
        except PlatformAuthenticationError:
            raise
        except Exception:
            instances = None
            print("容器实例查询失败，自动开机资格未核验；平台仍可只读监控。", flush=True)
        rows = discovery_rows(config, hosts, instances)
        print(DATA_PREFIX + json.dumps(rows, ensure_ascii=False), flush=True)
        emit_session("valid", "主机接口核验成功")
        if hosts:
            try:
                validate_telemetry_endpoint(config.telemetry.endpoint)
            except ValueError as exc:
                emit_gpu_samples([], hosts, config.monitor.stale_after_seconds, str(exc))
                print(str(exc), flush=True)
                return  # 平台发现仍成功；缺少GPU数据不能把已确认的登录会话判为失效。
            telemetry = TelemetryApiCollector(config.telemetry)
            try:
                emit_gpu_samples(telemetry.collect(), hosts, config.monitor.stale_after_seconds)
            except TelemetryUnavailable as exc:
                emit_gpu_samples([], hosts, config.monitor.stale_after_seconds, str(exc))
                print("WATCHER_STATUS " + str(exc), flush=True)  # 保留采集站认证原因，不否定平台已验证会话。
            except Exception:
                emit_gpu_samples([], hosts, config.monitor.stale_after_seconds, "GPU 数据采集失败；可重试刷新")
                print("GPU 数据采集失败，平台登录会话已核验有效。", flush=True)
            finally:
                telemetry.close()  # 一次性刷新不保留HTTP连接；图表失败不误报登录过期。

    except Exception as exc:
        if isinstance(exc, LocalStartupError):
            message = f"服务器刷新失败，本地启动错误：{exc}"
        elif isinstance(exc, PlatformAuthenticationError):
            message = f"登录已失效，请点击登录 / 更新会话：{exc}"
        else:
            message = f"服务器刷新失败，无法确认会话，请重试：{exc}"
        emit_session("invalid" if isinstance(exc, PlatformAuthenticationError) else "unknown", message)
        print(message, flush=True)
        raise SystemExit(1) from None  # 预期刷新错误不显示PyInstaller未处理异常堆栈。
    finally:
        try:
            collector.close()  # 刷新成功与否都释放浏览器上下文，供后续任务使用。
        except Exception as exc:
            print(f"刷新浏览器清理失败：{exc}", flush=True)
        set_observer(previous)
