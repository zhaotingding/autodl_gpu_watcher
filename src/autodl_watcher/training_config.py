"""服务器连接范围及 YAML 持久化；不创建网络连接。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re

from .yaml_settings import read_settings, settings_source, write_settings


DEFAULT_SCRIPTS = "train.py,trainer.py,train_*.py"


@dataclass(frozen=True)
class TrainingServer:  # SSH凭据仍由用户的OpenSSH管理，本地配置只保存连接别名与读取范围。
    id: str
    name: str
    ssh_alias: str
    python: str = "python3"
    project: str = ""
    entry: str = ""  # 非空时绑定AutoDL入口；空值表示独立服务器页。
    scripts: str = DEFAULT_SCRIPTS
    stalled_minutes: float = 120

    def validate(self):
        if not self.id or not self.name.strip() or not re.fullmatch(r"[\w.@:-]+", self.ssh_alias) or self.ssh_alias.startswith("-"):
            raise ValueError("请填写服务器名称和有效SSH别名")
        if not self.python.strip() or any(char in self.python for char in "\r\n\0"):
            raise ValueError("请填写远程Python程序路径")
        if self.project and not self.project.startswith("/"):
            raise ValueError("项目目录须为远程绝对路径，留空表示当前SSH用户的训练")
        if self.entry and not re.fullmatch(r"autodl-\d+-\d+", self.entry):
            raise ValueError("AutoDL入口应为autodl-203-1这样的编号")
        if not self.scripts.strip() or not math.isfinite(float(self.stalled_minutes)) or float(self.stalled_minutes) <= 0:
            raise ValueError("训练脚本名及无进展提醒时间必须有效")
        return self


def load_servers(path):
    if not settings_source(path).exists():
        return []
    data = read_settings(path)
    for item in data:
        if {name.strip() for name in item.get("scripts", "").split(",")} == {"train.py", "trainer.py", "train_net.py"}:
            item["scripts"] = DEFAULT_SCRIPTS  # 仅迁移旧默认值，不放宽用户自定义范围，也不改写文件。
    servers = [TrainingServer(**item).validate() for item in data]
    if len({item.id for item in servers}) != len(servers) or len({item.entry for item in servers if item.entry}) != sum(bool(item.entry) for item in servers):
        raise ValueError("服务器ID或AutoDL绑定入口重复")
    return servers


def save_servers(path, servers):
    for server in servers:
        server.validate()
    write_settings(path, [asdict(item) for item in servers])
