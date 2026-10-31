import json
from datetime import datetime
from dataclasses import asdict


SESSION_PREFIX = "WATCHER_SESSION "
GPU_PREFIX = "WATCHER_GPUS "
REPORT_PREFIX = "WATCHER_REPORT "
HOSTS_PREFIX = "WATCHER_HOSTS "


def emit_platform_hosts(hosts):
    rows = [(entry, f"{idle}/{total}", None) for host in hosts for entry, idle, total in host.source_slots]
    print(HOSTS_PREFIX + json.dumps(rows, ensure_ascii=False), flush=True)  # 资源更新不冒充新的开机资格核验。


def emit_session(status, message):  # 只传会话状态与核验时间，不传令牌或浏览器资料。
    payload = {"status": status, "message": message,
               "checked_at": datetime.now().isoformat(timespec="seconds")}
    print(SESSION_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def emit_gpu_samples(samples, hosts, stale_after_seconds, error="", *, collected_at=None,
                     poll_seconds=None, next_sample_at=None):  # 图表只展示当前账号可见主机；复用本轮数据，不额外轮询。
    visible = {host.host for host in hosts} | {entry for host in hosts for entry in host.source_names}
    rows = [dict(asdict(sample), observed_at=sample.observed_at.isoformat())
            for sample in samples if sample.host in visible]
    payload = {"samples": rows, "stale_after_seconds": stale_after_seconds, "error": error}
    if not error:
        payload["collected_at"] = (collected_at or datetime.now()).isoformat()  # 失败消息不能伪造新采集时间。
        if poll_seconds is not None:
            payload["poll_seconds"] = poll_seconds
        if next_sample_at is not None:
            payload["next_sample_at"] = next_sample_at.isoformat()
    print(GPU_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
