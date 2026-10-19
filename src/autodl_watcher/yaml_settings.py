"""应用设置使用 YAML；旧 JSON 只用于兼容读取，写入采用同目录原子替换。"""
import json
from pathlib import Path

import yaml


class _SettingsDumper(yaml.SafeDumper):
    pass


def _string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


_SettingsDumper.add_representer(str, _string)


def settings_path(path):
    path = Path(path)
    return path.with_suffix(".yaml") if path.suffix == ".json" else path


def settings_source(path):
    path = settings_path(path)
    legacy = path.with_suffix(".json")
    return legacy if not path.exists() and legacy.exists() else path


def read_settings(path):
    source = settings_source(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix == ".json":
        return json.loads(text)  # 旧配置严格按 JSON 读取，不能把损坏内容误当作 YAML 字符串。
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML配置无效：{source.name}") from exc


def write_settings(path, value):
    path = settings_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.dump(value, Dumper=_SettingsDumper, allow_unicode=True, sort_keys=False, default_flow_style=False)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)  # 同一目录替换，失败时保留原设置。
