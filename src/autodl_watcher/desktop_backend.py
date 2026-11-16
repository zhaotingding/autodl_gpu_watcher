"""Electron拥有的Python业务进程，通过JSON行协议提供既有监控服务。"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from PIL import Image, ImageOps

from . import __version__, ai_credentials, telemetry_auth
from .ai_service import AIService
from .cli import normalize_host, positive_seconds
from .config import load_config, validate_telemetry_endpoint
from .exe_icon import launcher_target
from .gpu_resources import resource_rows, server_state
from .launcher import check_environment, child_environment, installed_runtime, project_python, worker_command
from .network_activity import NETWORK_PREFIX, set_observer
from .network_monitor import NetworkMonitor
from .paths import application_root, default_config_path, prepare_application
from .session import GPU_PREFIX, HOSTS_PREFIX, REPORT_PREFIX, SESSION_PREFIX
from .training_ai import DEFAULT_LOG_CHARS, DEFAULT_PROMPT, excerpt, save_settings as save_ai_settings
from .training_config import TrainingServer, load_servers, save_servers
from .training_service import TrainingService
from .yaml_settings import read_settings, settings_source, write_settings


def _json_value(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (Path, datetime)):
        return str(value) if isinstance(value, Path) else value.isoformat()
    if isinstance(value, set):
        return list(value)
    raise TypeError(f"无法序列化：{type(value).__name__}")


class DesktopAI(AIService):
    def __init__(self, path, training):
        self.training = training
        super().__init__(path)

    def tick(self):
        for server_id in self.poll():
            self.training.reparse(server_id)
        if self.closing or self.busy or not self.config.get("auto_generate") or self.store.error or not self.config.get("model"):
            return
        for server_id, snapshot in self.training.monitored_snapshots():
            server = self.training.server(server_id)
            for card in snapshot.get("cards", []):
                if not card.get("alive") or not card.get("details") or self.rule_entry(server, card):
                    continue
                try:
                    budget = min(12000, self.config.get("max_log_chars", DEFAULT_LOG_CHARS))
                    self.generate(server, card, card["details"][-budget:])
                    self.training.reparse(server_id)
                except (ValueError, OSError) as exc:
                    self.store.error = "默认监控 · 自动生成已暂停：" + str(exc)
                return


class DesktopBackend:
    def __init__(self, root=None, *, popen=subprocess.Popen, start_scheduler=True):
        self.root = prepare_application(root or application_root())
        self.local = self.root / ".ui"
        self.local.mkdir(exist_ok=True)
        self.config = load_config(default_config_path(self.root))
        self.debug = os.environ.get("AUTODL_DEBUG") == "1"
        self.preference_path = self.local / "preferences.yaml"
        self.preferences = self._read_preferences()
        self.server_path = self.local / "servers.yaml"
        self.servers = load_servers(self.server_path)
        self.events = queue.Queue()
        self.training = TrainingService(self.events)
        self.ai = DesktopAI(self.local / "ai.yaml", self.training)
        self.training.ai = self.ai
        for server in self.servers:
            self.training.register(server)
        desktop_pid = os.environ.get("AUTODL_DESKTOP_PID", "")
        self.network = NetworkMonitor(root_pid=int(desktop_pid) if desktop_pid else os.getpid())
        self._previous_observer = set_observer(self.network.add_observation)
        self.logs = deque(maxlen=2000)
        self.session = dict(status="unknown", message="会话尚未核验", checked_at="")
        self.hosts = []
        self.gpu_snapshot = dict(samples=[], error="尚未采样")
        self.process = None
        self.job = ""
        self.status = "等待配置或开始监控"
        self.closing = False
        self.closed = False
        self.icon_changed = False
        self._credential_status = None
        self._popen = popen
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.stop_file = self.local / f"stop-desktop-{os.getpid()}"
        self.scheduler = None
        if start_scheduler:
            self.scheduler = threading.Thread(target=self._schedule, name="desktop-monitor-scheduler", daemon=True)
            self.scheduler.start()

    def _read_preferences(self):
        saved = read_settings(self.preference_path) if settings_source(self.preference_path).exists() else {}
        if not isinstance(saved, dict):
            raise ValueError("个人偏好须为YAML对象，请检查preferences.yaml")
        return {"user": self.config.occupancy.self_user, "poll": str(self.config.monitor.poll_seconds),
                "entry": "", "live": False, "convert_no_gpu": False, **saved}

    def _schedule(self):
        while not self._stop.wait(.1):
            try:
                with self._lock:
                    for _ in range(200):
                        try:
                            kind, value = self.events.get_nowait()
                        except queue.Empty:
                            break
                        if kind == "training":
                            self.training.receive(value)
                    if not self.closing:
                        self.training.tick()
            except Exception as exc:
                self._append("后台调度失败：" + str(exc), "error")

    def _append(self, text, level="normal"):
        with self._lock:
            for line in str(text).strip().splitlines():
                self.logs.append({"text": line, "level": level})

    @staticmethod
    def _worker_command(task, *args):
        return worker_command(task, *args)

    def _launch(self, task, command):
        if self.process is not None:
            raise ValueError("已有平台任务运行，请先停止或完成登录")
        process = self._popen(command, cwd=self.root, env=child_environment(self.root), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.process, self.job = process, task
        self.session = dict(status="checking", message="等待人工登录后核验" if task == "login" else "正在核验平台接口", checked_at="")
        self.status = {"login": "请在专用Edge完成登录，再完成登录同步", "monitor": "监控已启动，等待首次采集", "discover": "正在刷新服务器"}[task]
        threading.Thread(target=self._read_worker, args=(process, task), name="desktop-worker-output", daemon=True).start()

    def _read_worker(self, process, task):
        try:
            for line in process.stdout:
                self.accept_line(line)
        except (OSError, ValueError) as exc:
            self._append("后台输出读取失败：" + str(exc), "error")
            if task == "monitor":
                self.stop_file.touch()
        finally:
            result = process.wait()
            for stream in (process.stdout, process.stdin):
                if stream:
                    stream.close()
            with self._lock:
                if self.process is process:
                    self.process, self.job = None, ""
                    if self.session["status"] == "checking":
                        self.session.update(status="unknown", message="后台结束，接口核验未完成")
                    self.status = "任务已结束" if result == 0 else "任务失败，请查看实时输出"

    def accept_line(self, line):
        with self._lock:
            if line.startswith(NETWORK_PREFIX):
                self.network.add_observation(json.loads(line[len(NETWORK_PREFIX):]))
            elif line.startswith(SESSION_PREFIX):
                self.session = json.loads(line[len(SESSION_PREFIX):])
            elif line.startswith(HOSTS_PREFIX):
                previous = {item["entry"]: item for item in self.hosts}
                self.hosts = [{"entry": entry, "slots": slots,
                    "eligibility": eligibility or previous.get(entry, {}).get("eligibility", "未核验")}
                    for entry, slots, eligibility in json.loads(line[len(HOSTS_PREFIX):])]
            elif line.startswith(GPU_PREFIX):
                incoming = json.loads(line[len(GPU_PREFIX):])
                self.gpu_snapshot = (dict(self.gpu_snapshot, error=incoming["error"])
                    if incoming.get("error") and not incoming.get("samples") and self.gpu_snapshot.get("samples") else incoming)
            elif line.startswith(REPORT_PREFIX):
                report = json.loads(line[len(REPORT_PREFIX):])
                self._append("\n".join(report["lines"]), report["level"])
                self.status = report["lines"][0]
            else:
                text = line.removeprefix("WATCHER_STATUS ").strip()
                self._append(text, "error" if any(word in text.lower() for word in ("失败", "error", "断开", "失效")) else "normal")
                if line.startswith("WATCHER_STATUS "):
                    self.status = text

    def _public_preferences(self):
        values = {key: self.preferences.get(key) for key in ("user", "poll", "entry", "live", "convert_no_gpu", "ui")}
        if self.debug:
            values.update(live=False, convert_no_gpu=False)
        values["telemetry"] = telemetry_auth.settings(self.config.telemetry.endpoint, self.root)
        return values

    def _ai_settings(self, refresh=False):
        values = dict(self.ai.config)
        values.setdefault("prompt", DEFAULT_PROMPT)
        values.setdefault("max_log_chars", DEFAULT_LOG_CHARS)
        if refresh or self._credential_status is None or self._credential_status[0] != values["base_url"]:
            try:
                has_key, error = bool(ai_credentials.read(values["base_url"])), ""
            except ai_credentials.CredentialError as exc:
                has_key, error = False, str(exc)
            self._credential_status = (values["base_url"], has_key, error)  # 状态刷新不反复读取秘密，仅缓存是否已保存。
        _, has_key, error = self._credential_status
        return {"settings": values, "config": values, "has_key": has_key, "key_saved": has_key, "busy": self.ai.busy, "error": error}

    def _icon_settings(self):
        path, message = Path(__file__).with_name("default.ico"), "使用项目默认图标"
        saved = self.local / "icon.yaml"
        if settings_source(saved).exists():
            try:
                name = (read_settings(saved) or {}).get("file")
                if name:
                    candidate = (self.local / name).resolve()
                    if candidate.is_relative_to(self.local.resolve()) and candidate.is_file():
                        path, message = candidate, "使用自定义图标"
                    else:
                        message = "自定义图标不可用，使用项目默认图标"
            except (OSError, ValueError, TypeError, AttributeError):
                message = "自定义图标配置无法读取，使用项目默认图标"
        status = self.local / "icon-executable-status.yaml"
        if settings_source(status).exists():
            try:
                message += "\n" + read_settings(status)["message"]
            except (OSError, ValueError, KeyError, TypeError):
                message += "\nEXE图标同步记录无法读取，请检查目录图标"
        return {"path": str(path), "message": message}

    def snapshot(self):
        with self._lock:
            hosts = []
            for item in self.hosts:
                rows = resource_rows(item["entry"], self.gpu_snapshot, self.config.monitor.stale_after_seconds)
                hosts.append(dict(item, rows=rows, state=server_state(item["entry"], self.gpu_snapshot,
                    self.config.monitor.stale_after_seconds, rows=rows)))
            servers = [dict(asdict(server), status=self.training.status(server.id), snapshot=self.training.snapshot(server.id))
                       for server in self.servers]
            preferences = self._public_preferences()
            choice = (preferences.get("ui") or {}).get("theme", "dark")
            return {"app": {"name": "Auto算力与训练调度", "version": __version__, "python": project_python(), "root": str(self.root)},
                "theme": choice if choice in {"dark", "light"} else "dark", "preferences": preferences,
                "session": dict(self.session), "platform": {"hosts": hosts, "gpu_snapshot": self.gpu_snapshot},
                "worker": {"job": self.job, "running": self.process is not None, "status": self.status},
                "servers": servers, "logs": list(self.logs), "ai": self._ai_settings(), "icon": self._icon_settings(), "closing": self.closing}

    def save_preferences(self, values):
        with self._lock:
            saved = self._read_preferences()
            for key in ("user", "entry", "poll", "live", "convert_no_gpu"):
                if key in values:
                    saved[key] = values[key]
            saved["poll"] = str(positive_seconds(str(saved["poll"])))
            if not isinstance(saved["user"], str) or not isinstance(saved["entry"], str):
                raise ValueError("用户和入口须为文本")
            for key in ("live", "convert_no_gpu"):
                if not isinstance(saved[key], bool):
                    raise ValueError("电源选项须为布尔值")
            saved["convert_no_gpu"] = saved["live"] and saved["convert_no_gpu"]
            if "ui" in values:
                choice = values["ui"].get("theme")
                if choice not in {"dark", "light"}:
                    raise ValueError("主题须为dark或light")
                saved["ui"] = dict(saved.get("ui") or {}, theme=choice)
            try:
                if "telemetry" in values:
                    site = values["telemetry"]
                    telemetry_auth.save_settings(site.get("endpoint", ""), site.get("username", ""),
                        site.get("password_auth", False) is True, site.get("password", ""), self.root, auth_mode=site.get("auth_mode", "form"))
                    latest = read_settings(self.preference_path)
                    saved["telemetry"] = latest["telemetry"]  # 认证先验证再保存；后续偏好写入不能覆盖其最新值。
                write_settings(self.preference_path, saved)
            finally:
                self.preferences = self._read_preferences()  # 任何磁盘部分成功都反映到内存，错误继续向界面报告。
                self.config = load_config(default_config_path(self.root))
            return self._public_preferences()

    def save_server(self, value):
        with self._lock:
            names = TrainingServer.__dataclass_fields__
            fields = {key: item for key, item in value.items() if key in names}
            fields.setdefault("id", uuid.uuid4().hex)
            previous = next((server for server in self.servers if server.id == fields["id"]), None)
            server = replace(previous, **fields).validate() if previous else TrainingServer(**fields).validate()
            servers = [server if old.id == server.id else old for old in self.servers]
            if previous is None:
                servers.append(server)
            if len({item.entry for item in servers if item.entry}) != sum(bool(item.entry) for item in servers):
                raise ValueError("AutoDL绑定入口重复")
            save_servers(self.server_path, servers)
            self.servers = servers
            if previous is None or replace(server, pinned=previous.pinned) != previous:
                self.training.register(server)
            return asdict(server)

    def _monitor_command(self, params):
        validate_telemetry_endpoint(self.config.telemetry.endpoint)
        values = {**self.preferences, **params}
        entry, user = str(values.get("entry", "")), str(values.get("user", "")).strip()
        if not entry or not user:
            raise ValueError("请选择服务器入口并填写占用列表中的本人用户名")
        poll = positive_seconds(str(values.get("poll", 60)))
        verified = any(item["entry"] == entry and item["eligibility"] == "可开机" for item in self.hosts)
        live = not self.debug and values.get("live") is True and verified
        target = next((item for item in self.config.auto_start.targets if item.machine_name == entry and item.enabled and item.instance_uuid), None)
        if live and (target is None or not self.config.auto_start.enabled):
            raise ValueError("此入口尚未配置启用的开机实例")
        command = self._worker_command("monitor", "--config", str(default_config_path(self.root)), "--host", normalize_host(entry),
            "--entry", entry, "--user", user, "--poll-seconds", str(poll), "--ui", "--live" if live else "--monitor-only",
            "--no-login", "--stop-file", str(self.stop_file))
        if values.get("convert_no_gpu") is True and live:
            command.append("--convert-no-gpu")
        return command

    def _card(self, params):
        server_id = str(params["server_id"])
        snapshot = self.training.snapshot(server_id) or {}
        key = params.get("task_key", params.get("key"))
        card = next((item for item in snapshot.get("cards", []) if item["key"] == key), None)
        if card is None:
            raise ValueError("所选训练任务已变化，请刷新后重试")
        card = dict(card)
        if params.get("log_path"):
            source = next((source for source in card.get("log_sources", []) if source["path"] == params["log_path"]), None)
            if source is None:
                raise ValueError("日志不属于此训练任务")
            card.update(log_path=source["path"], log_identity=source["identity"], log_selected=True)
        return server_id, card

    def _network_snapshot(self):
        with self._lock:
            targets = [dict(value, pid=pid) for pid, value in self.training.network_targets().items()]
            if self.process is not None:
                targets.append(dict(pid=self.process.pid, kind="process", purpose="AutoDL " + self.job, target=""))
            for name, endpoint in (("AutoDL平台", self.config.platform.api_base_url), ("GPU采集站", self.config.telemetry.endpoint),
                                   ("AI接口", self.ai.config.get("base_url", ""))):
                if endpoint:
                    targets.append(dict(pid=0, kind="HTTPS" if endpoint.startswith("https:") else "HTTP", purpose=name, target=endpoint))
            self.network.set_targets(targets)
        return asdict(self.network.poll())

    def _import_icon(self, path):
        data = Path(path).read_bytes()
        with Image.open(BytesIO(data)) as source:
            if source.format == "ICO":
                for index in range(len(source.ico.entry)):
                    source.ico.frame(index).convert("RGBA")  # 校验作者的全部帧，保存仍使用原始ICO字节。
                message = "ICO原样保留全部尺寸帧"
            elif source.format == "PNG":
                original = source.convert("RGBA")
                sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
                frames = []
                for size in sizes:
                    image = ImageOps.contain(original, (size, size), Image.Resampling.LANCZOS)
                    frame = Image.new("RGBA", (size, size))
                    frame.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
                    frames.append(frame)
                output = BytesIO()
                frames[-1].save(output, format="ICO", sizes=[(size, size) for size in sizes], append_images=frames[:-1])
                data, message = output.getvalue(), "PNG已按原图分别生成图标帧"
            else:
                raise ValueError("请选择ICO或PNG")
        destination = self.local / "icons" / (uuid.uuid4().hex + ".ico")
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(data)
        write_settings(self.local / "icon.yaml", {"file": destination.relative_to(self.local).as_posix()})
        self.icon_changed = True
        return {"path": str(destination), "message": message}

    def _save_shortcut(self, path):
        executable = launcher_target(os.environ.get("AUTODL_INSTALLED_EXE") if installed_runtime()
                                     else os.environ.get("AUTODL_LAUNCHER_EXE"))
        if executable is None:
            raise ValueError("请从轻量启动器打开应用后再导出快捷方式")
        environment = os.environ.get("AUTODL_CONDA_ENV") or os.environ.get("CONDA_DEFAULT_ENV", "")
        manager = os.environ.get("AUTODL_CONDA_EXE") or os.environ.get("CONDA_EXE", "")
        conda = Path(manager) if manager else None
        if not installed_runtime() and (environment.lower() in {"", "base", "root"} or conda is None or not conda.is_absolute() or not conda.is_file()):
            raise ValueError("未识别专用Conda环境或管理器，请从Conda轻量启动器打开应用后重试")
        target = Path(path)
        if target.suffix.lower() != ".lnk":
            raise ValueError("快捷方式须保存为.lnk")
        env = os.environ.copy()
        arguments = ["--autodl-data-root", str(self.root)] if installed_runtime() else ["--env", environment, "--conda", str(conda)]
        env.update(AUTODL_LINK=str(target), AUTODL_EXE=str(executable), AUTODL_ICON=self._icon_settings()["path"],
                   AUTODL_ARGS=subprocess.list2cmdline(arguments))
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        script = "$ErrorActionPreference='Stop'; $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:AUTODL_LINK); $s.TargetPath=$env:AUTODL_EXE; $s.Arguments=$env:AUTODL_ARGS; $s.WorkingDirectory=Split-Path -LiteralPath $env:AUTODL_EXE; $s.IconLocation=$env:AUTODL_ICON+',0'; $s.Save()"
        result = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", script], env=env,
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, timeout=15)
        if result.returncode:
            raise OSError("快捷方式创建失败，请检查保存目录")
        return {"path": str(target), "message": "快捷方式已写入"}

    def dispatch(self, method, params=None):
        params = params or {}
        if not isinstance(params, dict):
            raise ValueError("操作参数须为对象")
        if method == "snapshot":
            return self.snapshot()
        if method == "preferences.save":
            return self.save_preferences(params.get("preferences", params))
        if method in {"servers.save", "servers.upsert"}:
            return self.save_server(params.get("server", params))
        if method == "servers.pin":
            return self.save_server({"id": params["server_id"], "pinned": params["pinned"]})
        if method in {"monitor.start", "monitor.refresh", "login.start"}:
            with self._lock:
                if self.closing:
                    raise ValueError("应用正在退出")
                task = {"monitor.start": "monitor", "monitor.refresh": "discover", "login.start": "login"}[method]
                if task == "monitor":
                    command = self._monitor_command(params)
                    self.stop_file.unlink(missing_ok=True)
                else:
                    command = self._worker_command(task)
                self._launch(task, command)
            return {"started": True}
        if method == "monitor.stop":
            with self._lock:
                if self.process is not None and self.job == "monitor":
                    self.stop_file.touch()
                    self.status = "正在安全停止，等待当前采集完成"
            return {"stopping": True}
        if method == "login.finish":
            with self._lock:
                if self.process is None or self.job != "login" or self.process.poll() is not None:
                    raise ValueError("没有等待完成的登录任务")
                self.process.stdin.write("\n")
                self.process.stdin.flush()
                self.status = "正在正常关闭登录窗口并核验会话"
            return {"checking": True}
        if method in {"training.start", "training.stop"}:
            with self._lock:
                server_id = params["server_id"]
                if self.closing:
                    raise ValueError("应用正在退出")
                if method == "training.start":
                    self.training.start(server_id, positive_seconds(str(params.get("interval", 60))))
                else:
                    self.training.stop(server_id)
            return {"accepted": True}
        if method == "training.log":
            with self._lock:
                if params.get("full", True) is False:
                    server_id, card = self._card({key: value for key, value in params.items() if key != "log_path"})
                    if params.get("log_path") and params["log_path"] != card.get("log_path"):
                        raise ValueError("所选日志没有缓存片段，请使用全文读取")
                    source = excerpt(card.get("details") or card.get("preview") or card.get("details_note") or "没有可读取的日志")
                    return {"text": source, "source": source, "path": card.get("log_path", ""), "size": None,
                            "characters": len(source), "estimated_tokens": (len(source) + 3) // 4, "card": card,
                            "full": False, "note": card.get("details_note") or "最近采样的原文片段，停止或断连后保留上次采样内容"}
                server_id, card = self._card(params)
                job = self.training.prepare_log_read(server_id, card)
            log = job()  # 同一个已认证SSH的受控全文读取，不持应用状态锁。
            source = excerpt(log["content"])
            return {"text": source, "source": source, "path": log["path"], "size": log.get("size"),
                    "characters": len(source), "estimated_tokens": (len(source) + 3) // 4, "card": card, "full": True}
        if method == "network.snapshot":
            return self._network_snapshot()
        if method == "ai.settings":
            return self._ai_settings(refresh=True)
        if method == "ai.save":
            with self._lock:
                save_ai_settings(self.ai.path, params.get("settings", params.get("config", {})), params.get("key", ""))
                self.ai.reload()
            return self._ai_settings(refresh=True)
        if method == "ai.delete_key":
            ai_credentials.delete(params.get("base_url", self.ai.config["base_url"]))
            return self._ai_settings(refresh=True)
        if method == "ai.generate":
            if params.get("confirmed") is not True:
                raise ValueError("请先确认发送所选训练日志及可能产生的费用")
            with self._lock:
                server_id, card = self._card(params)
                identity = self.ai.generate(self.training.server(server_id), card, str(params.get("source", "")))
                self.training.reparse(server_id)
            return {"identity": identity, "message": "规则生成已开始"}
        if method == "ai.usage":
            return {"summary": self.ai.usage.summary()}
        if method == "telemetry.delete_password":
            telemetry_auth.delete_password(params["endpoint"])
            return {"deleted": True}
        if method == "icon.settings":
            return self._icon_settings()
        if method == "icon.import":
            with self._lock:
                return self._import_icon(params["path"])
        if method == "icon.reset":
            write_settings(self.local / "icon.yaml", {"file": None})
            self.icon_changed = True
            return self._icon_settings()
        if method == "icon.shortcut":
            return self._save_shortcut(params["path"])
        if method == "shutdown":
            return self.close()
        raise ValueError("未知桌面操作：" + str(method))

    def close(self):
        with self._lock:
            if self.closed:
                return {"closed": True}
            if self.process is not None and self.job == "login":
                raise ValueError("请先完成登录并关闭专用窗口，再退出应用")
            self.closing = True
            if self.process is not None:
                self.stop_file.touch()
            self.ai.begin_shutdown()
            self.training.stop_all()
        while True:
            with self._lock:
                for _ in range(200):
                    try:
                        kind, value = self.events.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "training":
                        self.training.receive(value)
                finished = self.process is None and not self.training.busy() and self.ai.shutdown_complete()
                if finished:
                    self.closed = True
                    break
            time.sleep(.05)
        self._stop.set()
        if self.scheduler and self.scheduler is not threading.current_thread():
            self.scheduler.join(timeout=1)
        set_observer(self._previous_observer)
        if self.icon_changed and os.name == "nt" and not installed_runtime():
            try:
                executable = launcher_target(os.environ.get("AUTODL_LAUNCHER_EXE"))
                if executable is not None:
                    owner = os.environ.get("AUTODL_DESKTOP_PID") or str(os.getpid())
                    self._popen(worker_command("exe-icon", owner, str(executable)),
                        env=child_environment(self.root), close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    self.icon_changed = False  # 辅助进程等待真正Electron窗口退出，沿用原EXE资源同步。
            except OSError as exc:
                write_settings(self.local / "icon-executable-status.yaml", {"message": "EXE图标同步未启动：" + str(exc)})
        self.stop_file.unlink(missing_ok=True)
        return {"closed": True}


def main():
    parser = argparse.ArgumentParser(description="Electron Python监控后台")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    output, output_lock = sys.stdout, threading.Lock()
    sys.stdout = sys.stderr  # 业务库的普通输出只能进入诊断流，JSON协议持有原始stdout。

    def send(value):
        line = json.dumps(value, ensure_ascii=False, default=_json_value)
        with output_lock:
            output.write(line + "\n")
            output.flush()

    try:
        check_environment()  # Electron显式解释器入口同样检查专用Conda及依赖实际来源。
        if installed_runtime() and args.root is not None and args.root.resolve() != application_root().resolve():
            raise ValueError("安装版资料目录由主程序指定，--root与已选择的资料目录不一致")
        backend = DesktopBackend(args.root)
    except Exception as exc:
        send({"event": "backend.error", "data": {"message": excerpt(str(exc))}})
        raise SystemExit(2) from None  # 配置读取失败有具体消息，不让桌面只看到退出码。

    def reply(request):
        try:
            result = backend.dispatch(request["method"], request.get("params"))
            send({"id": request["id"], "result": result})
        except Exception as exc:
            send({"id": request.get("id"), "error": excerpt(str(exc))})

    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="desktop-rpc")
    send({"event": "ready", "data": {"version": __version__, "python": project_python()}})
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or "id" not in request or not isinstance(request.get("method"), str):
                    raise ValueError("桌面请求格式无效")
            except (ValueError, TypeError) as exc:
                send({"id": None, "error": str(exc)})
                continue
            if request["method"] == "shutdown":
                reply(request)
                if backend.closed:
                    break
            else:
                pool.submit(reply, request)
    finally:
        if not backend.closed and backend.job != "login":
            backend.close()
        pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
