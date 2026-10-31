from __future__ import annotations

import argparse
import json
from dataclasses import replace
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import uuid
import weakref
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .launcher import child_environment, project_python as worker_python
from .app_icon import IconManager
from .cli import normalize_host, positive_seconds
from .config import load_config, validate_telemetry_endpoint
from .gpu_resources import resource_rows, server_state
from .login import LocalStartupError
from .training_config import TrainingServer, load_servers, save_servers  # GUI 只读取训练配置边界，不加载解析聚合模块。
from .training_ui import TrainingPane
from .training_service import TrainingService
from .training_ai_ui import AIAssistant
from .session import GPU_PREFIX, REPORT_PREFIX, SESSION_PREFIX, emit_gpu_samples, emit_session
from .paths import application_root, default_config_path, prepare_application
from .yaml_settings import read_settings, settings_source, write_settings

ROOT = application_root()  # 所有启动入口共用源码/安装包/成品布局规则。
DATA_PREFIX = "WATCHER_HOSTS "  # 约定的结构化输出前缀；主机列表与普通日志共用 stdout 管道。


def worker_command(task, *args):  # 主程序始终运行源码，后台复用同一个Conda解释器。
    modules = {"monitor": "autodl_watcher.main", "discover": "autodl_watcher.gui",
               "login": "autodl_watcher.login"}
    module = modules[task]  # 仅允许这三个应用任务，不执行任意模块。
    return [worker_python(), "-s", "-u", "-m", module, *(["--discover"] if task == "discover" else []), *args]


def status_level(line):  # 当前状态与实时输出统一用相同的颜色判断。
    lowered = line.strip().lower()
    if lowered.startswith("telemetry容错："):
        return "normal"
    if any(word in lowered for word in ("失败", "断连", "断开", "异常", "错误", "error", "traceback", "timeout", "超时", "未通过", "失效", "额度不足", "拒绝", "暂时不可用",
                                        "不允许开机", "无法核对", "暂缓释放额度", "暂停释放无卡实例")):
        return "error"
    if "受理" in lowered or "验证成功后" in lowered:
        return "normal"  # 电源请求受理不等于实例已占用GPU。
    if any(word in lowered for word in ("已开机", "已连接", "连接正常", "会话有效", "采集恢复", "已占用", "有卡运行",
                                        "本人 gpu 占用 有", "本人gpu占用 有", "本人占用 有")):
        return "success"
    if "成功" in lowered:
        return "success"
    return "normal"


def monitor_command(config, entry, user, poll, live, stop_file, convert_no_gpu=False):  # 构造与 CLI 相同的监控命令。
    validate_telemetry_endpoint(config.telemetry.endpoint)
    if not entry:  # 没选入口就拒绝启动，避免误用命令行默认主机。
        raise ValueError("请先选择服务器入口")
    if not user.strip():  # 此名称用于占用归属匹配，不是登录账号或 SSH 用户名。
        raise ValueError("请填写占用列表中的本人用户名")
    poll = positive_seconds(str(poll))  # 在创建子进程前完成数值验证。
    target = next((item for item in config.auto_start.targets
                   if item.machine_name == entry and item.enabled and item.instance_uuid), None)
    if live and (target is None or not config.auto_start.enabled):  # 可见主机不等于已有可开机实例，真实模式需要有效配置。
        raise ValueError("此入口尚未配置启用的开机实例，请先只读监控或在 config.yaml 配置 targets")
    if convert_no_gpu and not live:
        raise ValueError("无卡转有卡须同时启用真实自动开机")
    command = worker_command("monitor", "--config", str(default_config_path(ROOT)),
            "--host", normalize_host(entry), "--entry", entry, "--user", user.strip(),
            "--poll-seconds", str(poll), "--ui",
            "--live" if live else "--monitor-only", "--no-login", "--stop-file", str(stop_file))  # 参数列表直接传给进程，不经 shell 拼接。
    return command + (["--convert-no-gpu"] if convert_no_gpu else [])


def gpu_row_id(entry, index):
    return f"{entry}::gpu:{index}"  # 稳定身份用于刷新保留选中GPU，不把行号当作服务器入口。


def resource_meter(percent):
    if percent is None:
        return "—"
    filled = max(0, min(10, int(percent / 10 + .5)))
    return "█" * filled + "░" * (10 - filled) + f" {percent:3.0f}%"  # 固定十格和三位数字，逐列保持等宽。


def resource_state(text):
    return text.replace("采集失败 · 保留上次数据", "采集失败（上次值）").replace(
        "上次数据 · 采集失败", "采集失败（上次值）").replace("采集中 · 上次数据", "采集中（上次值）")


class WatcherWindow:  # 只负责交互与进程管理，监控业务仍由 main.run_monitor 执行。
    def __init__(self, root, debug=False):  # 先读基础配置和本地偏好，再创建控件与消息轮询。
        prepare_application(ROOT)
        self.root = root  # Tk 主窗口及事件循环的拥有者，子线程不直接操作它。
        self.config = load_config(default_config_path(ROOT))  # YAML 提供目标实例、监控周期、浏览器资料和运行路径。
        self.local = ROOT / ".ui"  # 不纳入 Git 的个人偏好、停止信号和启动日志目录。
        self.local.mkdir(exist_ok=True)
        self.stop_file = self.local / f"stop-{os.getpid()}"  # 以 GUI 进程号区分停止信号，避免读取上一个窗口的信号。
        self.process = None  # None 表示空闲；每个窗口同一时间只管理一个后台任务。
        self.job = ""  # 区分 discover、monitor、login，决定按钮与结束行为。
        self.events = queue.Queue()  # 读管道线程向 Tk 主线程发送 (消息类型, 内容)。
        self.closing = False  # 请求关窗后先等后台任务安全结束，再销毁 Tk。
        self.user = tk.StringVar(value=self.config.occupancy.self_user)  # StringVar 将输入框内容与 Python 变量绑定。
        self.poll = tk.StringVar(value=str(self.config.monitor.poll_seconds))
        self.debug = debug  # 调试模式强制仅监控；普通模式恢复已保存的电源选项。
        self.live = tk.BooleanVar(value=False)
        self.convert_no_gpu = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="未运行 · 选择入口后开始监控")
        self.session_status = "unknown"
        self.session_message = "登录会话尚未核验"
        self.session_text = tk.StringVar(value="会话未核验 · 核验时间：—")
        self.gpu_snapshot = {"samples": [], "error": "尚未采样"}
        self.power_eligibility = {}  # 开机资格保留独立状态，不再借用资源表的显示列。
        self.saved_entry = ""
        self.training = TrainingService(self.events)
        self.training.ai = AIAssistant(root, self.local / "ai.yaml", self.training)
        self.server_file = self.local / "servers.yaml"
        self.servers, self.external_panes = [], {}
        self.temporary_training_tabs = set()  # 未固定页面仅在用户主动打开后临时显示。
        self.title_text = tk.StringVar(value="AutoDL 平台")
        try:
            self.servers = load_servers(self.server_file)
            for server in self.servers:
                self.training.register(server)
        except (OSError, ValueError, TypeError) as exc:
            self.status.set(f"训练连接配置读取失败：{exc}")
        preferences = self.local / "preferences.yaml"
        if settings_source(preferences).exists():
            try:
                saved = read_settings(preferences)  # 缺失字段继续使用 YAML 默认值。
                self.user.set(saved.get("user", self.user.get()))
                self.poll.set("60" if "usage" in saved else saved.get("poll", self.poll.get()))  # 旧双间隔偏好一次迁移为60秒。
                self.saved_entry = saved.get("entry", "")
                self.live.set(not debug and saved.get("live", False) is True)
                self.convert_no_gpu.set(self.live.get() and saved.get("convert_no_gpu", False) is True)
            except (ValueError, OSError, AttributeError):
                self.status.set("本地偏好读取失败，已使用默认配置")
        self.icons = IconManager(root, self.local)
        self.settings_window = None
        self.network_window = self.network_monitor = None  # 连接详情按需创建，主窗口启动不增加系统轮询。
        self.training.ai.open_settings = self.open_settings
        self.icons.open_settings = self.open_settings
        self._build()
        self._populate([(item.machine_name, "待刷新", "未核验")
                        for item in self.config.auto_start.targets if item.enabled])
        self.root.after(100, self._drain)  # 将首次消息处理排入 Tk 事件队列，而不是阻塞等待后台输出。
        try:
            validate_telemetry_endpoint(self.config.telemetry.endpoint)
        except ValueError as exc:
            self.session_text.set("AutoDL 尚未配置 · 可先在全局设置中添加自己的 SSH 服务器")
            self.status.set(str(exc))
        else:
            self.root.after(0, self.refresh)  # 已配置平台仍沿用启动时只读核验。
        self.root.protocol("WM_DELETE_WINDOW", self.close)  # 标题栏关闭按钮也走安全停止流程。

    def _build(self):  # 从上到下构建工具栏、服务器表、配置区、操作按钮和日志区。
        self.root.title(f"Auto算力与训练调度 · v{__version__}")
        self.root.geometry("1000x800")  # 保留用户设置的初始窗口高度；单位为像素。
        self.root.minsize(820, 660)  # 限制手动缩小时的最小尺寸，避免控件完全挤出窗口。
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=22, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9))
        style.configure("TButton", padding=(10, 5))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        self.check_images = []  # Tk不会持有Python图片引用；保留它们，防止勾选标记被回收。
        for selected in (False, True):
            indicator = tk.PhotoImage(master=self.root, width=16, height=16)
            indicator.put("#ffffff", to=(1, 1, 15, 15))
            for border in ((1, 1, 15, 2), (1, 14, 15, 15), (1, 1, 2, 15), (14, 1, 15, 15)):
                indicator.put("#727272", to=border)
            if selected:
                for x, y in ((4, 8), (5, 9), (6, 10), (7, 11), (8, 10), (9, 9), (10, 8), (11, 7), (12, 6), (12, 5)):
                    indicator.put("#245cc4", to=(x, y, x + 2, y + 2))  # 画✓，替换clam主题默认的X。
            self.check_images.append(indicator)
        style.element_create("Watcher.indicator", "image", self.check_images[0], ("selected", self.check_images[1]))
        style.layout("Watcher.TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Watcher.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [("Checkbutton.label", {"sticky": "nswe"})]})]})])
        frame = ttk.Frame(self.root, padding=8)
        frame.pack(fill="both", expand=True)
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill="both", expand=True, pady=(3, 0))
        style.configure("GlobalAI.TButton", padding=(7, 1))
        self.settings_button = ttk.Button(frame, text="全局设置", style="GlobalAI.TButton", command=self.open_settings)
        self.settings_button.place(relx=1, y=0, anchor="ne")
        self.network_button = ttk.Button(frame, text="网络连接详情", style="GlobalAI.TButton", command=self.open_network)
        self.network_button.place(relx=1, x=-self.settings_button.winfo_reqwidth() - 6, y=0, anchor="ne")
        self.settings_button.bind("<Configure>", lambda event: self.network_button.place_configure(
            x=-self.settings_button.winfo_reqwidth() - 6))  # 按实际按钮宽度避让，适配字体和 DPI。
        self.auto_page = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.auto_page, text="AutoDL 平台")
        page = self.auto_page
        ttk.Label(page, textvariable=self.title_text, style="Title.TLabel").pack(anchor="w")
        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x", pady=4)
        self.refresh_button = ttk.Button(toolbar, text="刷新服务器", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.login_button = ttk.Button(toolbar, text="登录 / 更新会话", command=self.login)
        self.login_button.pack(side="left", padx=5)
        self.login_done = ttk.Button(toolbar, text="完成登录并关闭窗口", command=self.finish_login, state="disabled")
        self.login_done.pack(side="left")
        self.session_label = ttk.Label(page, textvariable=self.session_text, foreground="#7a5a18", wraplength=920)
        self.session_label.pack(anchor="w", pady=(0, 4))
        page.bind("<Configure>", lambda event: self.session_label.configure(wraplength=max(200, event.width - 20)))
        self.resource_split = ttk.Panedwindow(page, orient="vertical")
        self.resource_split.pack(fill="both", expand=True)
        resource_area = ttk.Frame(self.resource_split)
        self.resource_split.add(resource_area, weight=4)
        body = ttk.Frame(resource_area)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(body, columns=("slots", "memory", "util", "capacity", "observed", "state"),
                                 show="tree headings", height=16, selectmode="browse")
        self.table.heading("#0", text="服务器 / GPU", anchor="w")
        self.table.column("#0", width=160, minwidth=145, stretch=True, anchor="w")
        for column, title, width in [("slots", "空闲/总数", 70), ("memory", "显存占用率", 170),
                                     ("util", "GPU 利用率", 170), ("capacity", "显存用量 / GiB", 135),
                                     ("observed", "数据时间", 85), ("state", "采样状态 / 来源", 150)]:
            self.table.heading(column, text=title, anchor="center")
            self.table.column(column, width=width, minwidth=width, stretch=False, anchor="center")
        self.table.tag_configure("server", background="#edf2f7", font=("Microsoft YaHei UI", 9, "bold"))
        self.table.tag_configure("gpu", font=("Consolas", 10), foreground="#245c6b")
        self.table.tag_configure("delayed", font=("Consolas", 10), foreground="#8a5b11")
        self.table.tag_configure("invalid", font=("Consolas", 10), foreground="#b42318")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(body, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        controls = ttk.Frame(resource_area)
        controls.pack(fill="x", pady=4)
        self.start_button = ttk.Button(controls, text="开始监控", command=self.start, width=8)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="停止监控", command=self.stop, state="disabled", width=8)
        self.stop_button.pack(side="left", padx=4)
        self.status_label = ttk.Label(resource_area, textvariable=self.status, foreground="#245cc4", wraplength=930)
        self.status_label.pack(anchor="w", fill="x", pady=4)
        resource_area.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(200, event.width - 10)))
        window_ref = weakref.ref(self)
        def update_status(*_):
            window = window_ref()
            if window is not None:
                window.status_label.configure(foreground={"success": "#18733a", "error": "#b42318"}.get(
                    status_level(window.status.get()), "#245cc4"))
        self.status.trace_add("write", update_status)  # 不让Tcl变量回调强持整个窗口，避免循环延迟到后台回收。
        output = ttk.LabelFrame(self.resource_split, text="实时监控", padding=2)
        self.resource_split.add(output, weight=1)  # 可拖动分隔线，资源区优先使用空白，黑框仍可查看。
        horizontal = ttk.Scrollbar(output, orient="horizontal")
        horizontal.pack(side="bottom", fill="x")
        self.log = ScrolledText(output, height=5, wrap="none", state="disabled", font=("Microsoft YaHei UI", 9),
                                xscrollcommand=horizontal.set, background="#17202e", foreground="#e2e8f0", padx=8, pady=6)
        horizontal.configure(command=self.log.xview)
        self.log.tag_configure("success", foreground="#79e69d")
        self.log.tag_configure("error", foreground="#ff8181")
        self.log.pack(fill="both", expand=True)  # 仅在窗口内保留最近输出，不保存文件或累计占用时长。
        self._server_tabs()

    def _append_output(self, value, level="normal"):
        if not value.strip():
            return
        follow = self.log.yview()[1] >= .995
        self.log.configure(state="normal")
        self.log.insert("end", value.rstrip("\r\n") + "\n", level)
        excess = int(self.log.index("end-1c").split(".")[0]) - 2001
        if excess > 0:
            self.log.delete("1.0", f"{excess + 1}.0")  # 固定内存上限，长期监控不无限堆积文本。
        if follow:
            self.log.see("end")
        self.log.configure(state="disabled")

    def _server_tabs(self):
        for server in sorted(self.servers, key=lambda item: bool(item.entry)):
            if server.pinned or server.id in self.temporary_training_tabs:
                self._show_training_tab(server)
            elif server.id in self.external_panes:
                self.notebook.hide(self.external_panes[server.id][0])  # 隐藏视图，保留服务与当前连接。

    def _show_training_tab(self, server):
        if server.id not in self.external_panes:
            page = ttk.Frame(self.notebook, padding=10)
            title = ttk.Label(page, text=server.name, style="Title.TLabel")
            title.pack(anchor="w")
            options = ttk.Frame(page)
            options.pack(fill="x", pady=4)
            ttk.Label(options, text="采样间隔（秒）").pack(side="left")
            interval = tk.StringVar(value="60")
            ttk.Entry(options, textvariable=interval, width=9).pack(side="left", padx=5)
            ttk.Button(options, text="收起页面（不停止监控）", command=lambda key=server.id: self.hide_training(key)).pack(side="right")
            pane = TrainingPane(page, self.training, lambda key=server.id: self.edit_server(key), interval.get, show_gpus=True)
            pane.pack(fill="both", expand=True)
            pane.select(server.id)
            self.external_panes[server.id] = (page, title, pane)
        page = self.external_panes[server.id][0]
        self.notebook.add(page, text=server.name)  # 已隐藏的页面原样恢复，不重建控件或重复连接。
        return page

    def open_training(self, server_id):
        server = next(item for item in self.servers if item.id == server_id)
        self.temporary_training_tabs.add(server_id)
        self.notebook.select(self._show_training_tab(server))
        if self.settings_window is not None:
            self.settings_window.withdraw()  # 切到主窗口查看，不隐式开始采集。
        self.root.deiconify()
        self.root.lift()

    def hide_training(self, server_id):
        self.temporary_training_tabs.discard(server_id)
        self.notebook.hide(self.external_panes[server_id][0])  # 固定设置仍保留；重开应用按固定状态恢复。

    def open_settings(self, section="servers"):
        from .settings_ui import GlobalSettings
        if self.settings_window is None or not self.settings_window.winfo_exists():
            self.settings_window = GlobalSettings(self)
        window = self.settings_window
        window.tabs.select(window.pages[section])
        window.deiconify()
        window.lift()
        return window

    def open_network(self):
        from .network_monitor import NetworkMonitor
        from .network_ui import NetworkPanel
        if self.network_monitor is None:
            self.network_monitor = NetworkMonitor()  # 后台只读取本机状态，不触发平台或 SSH 采样。
        if self.network_window is None or not self.network_window.winfo_exists():
            self.network_window = NetworkPanel(self.root, self.network_monitor, targets_provider=self._network_targets)
        self.network_window.show()  # 单窗口复用；关闭详情不会断开业务连接。
        return self.network_window

    def _network_targets(self):
        targets = [dict(value, pid=pid) for pid, value in self.training.network_targets().items()]
        targets.append({"pid": os.getpid(), "kind": "process", "purpose": "主程序", "target": ""})
        if self.process is not None and self.process.poll() is None:
            targets.append({"pid": self.process.pid, "kind": "process", "purpose": "AutoDL " + self.job, "target": ""})
        for purpose, address in (("AutoDL 平台", self.config.platform.api_base_url),
                                 ("GPU 采集站", self.config.telemetry.endpoint),
                                 ("AI 接口", self.training.ai.config.get("base_url", ""))):
            if address:
                targets.append({"pid": 0, "kind": "HTTPS" if address.lower().startswith("https:") else "HTTP", "purpose": purpose, "target": address})
        return targets  # HTTP网址单独列为配置目标，不冒充系统TCP表解析出的请求URL。

    def edit_server(self, server_id=None, entry=""):  # 所有入口共用全局设置中的连接编辑表单。
        server = next((item for item in self.servers if item.id == server_id), None)
        server = server or TrainingServer(entry or uuid.uuid4().hex, entry.removeprefix("autodl-") or "新服务器", "", entry=entry)
        self.open_settings("servers").edit_server(server)  # 主窗口仅选择编辑对象，控件由设置窗口拥有。

    def save_server(self, updated):  # 先持久化，再通知训练服务；保存失败不能改变活动连接。
        previous = next((item for item in self.servers if item.id == updated.id), None)
        servers = [updated if item.id == updated.id else item for item in self.servers]  # 更新已有连接的静态配置。
        if not any(item.id == updated.id for item in self.servers):
            servers.append(updated)  # 新服务器通过同一保存路径加入。
        save_servers(self.server_file, servers)  # 沿用原子 YAML 写入与配置校验。
        self.servers = servers
        if previous is None or replace(updated, pinned=previous.pinned) != previous:
            self.training.register(updated)  # 仅连接配置变化才通知服务；固定开关不重置快照或连接。
        if previous is not None and previous.pinned and not updated.pinned:
            self.temporary_training_tabs.discard(updated.id)  # 明确取消固定应立即收起原常驻页。
        self._server_tabs()
        if updated.id in self.external_panes:
            page, title, pane = self.external_panes[updated.id]
            self.notebook.tab(page, text=updated.name)
            title.configure(text=updated.name)
            pane.render()  # pending 或 generation 的改变会触发卡片更新，无需改视图内部缓存。
        self._render_resources()
        self.settings_window.refresh_servers()  # 已保存的连接立即进入全局连接列表。

    def _render_resources(self):  # 仅格式化本轮缓存，全部入口一起更新，不另发采集请求。
        self.title_text.set("AutoDL 平台算力")
        for entry in self.table.get_children():
            rows = resource_rows(entry, self.gpu_snapshot, self.config.monitor.stale_after_seconds)
            state = server_state(entry, self.gpu_snapshot, self.config.monitor.stale_after_seconds, rows=rows)
            stamps = [row["observed_at"] for row in rows if row["observed_at"] is not None]
            observed = min(stamps).strftime("%H:%M:%S") if stamps else "—"
            values = (self.table.set(entry, "slots"), "", "", "", observed,
                      resource_state(state.removesuffix(" · 物理机数据")))
            if self.table.item(entry, "values") != values:
                self.table.item(entry, values=values)
            children = []
            for row in rows:
                item = gpu_row_id(entry, row["gpu_index"])
                children.append(item)
                capacity = "—" if row["used_mb"] is None else f"{row['used_mb'] / 1024:.1f} / {row['total_mb'] / 1024:.1f}"
                stamp = row["observed_at"].strftime("%H:%M:%S") if row["observed_at"] is not None else "—"
                origin = "物理机" if row["physical"] else "入口"
                short_state = resource_state(row["state"]).replace("（上次值）", "")
                values = ("", resource_meter(row["memory_pct"]), resource_meter(row["util_pct"]), capacity,
                          stamp, f"{short_state} · {origin}")
                tag = "invalid" if row["invalid"] or row["clock_skew"] else "delayed" if row["state"] != "正常" else "gpu"
                if not self.table.exists(item):
                    self.table.insert(entry, "end", iid=item, text=f"GPU #{row['gpu_index']}", values=values, tags=(tag,))
                else:
                    if self.table.item(item, "values") != values:
                        self.table.item(item, values=values)
                    if self.table.item(item, "tags") != (tag,):
                        self.table.item(item, tags=(tag,))
            for item in self.table.get_children(entry):
                if item not in children:
                    if item in self.table.selection():
                        self.table.selection_set(entry)  # GPU消失时回到同一服务器，不改成其他入口。
                    self.table.delete(item)
            if self.table.get_children(entry) != tuple(children):
                for index, item in enumerate(children):
                    self.table.move(item, entry, index)

    def _accept_gpu_snapshot(self, data):
        if data.get("error") and not data.get("samples") and self.gpu_snapshot.get("samples"):
            self.gpu_snapshot = dict(self.gpu_snapshot, error=data["error"], refreshing=False)
        else:
            self.gpu_snapshot = dict(data, refreshing=False)  # 新成功快照替换旧数据；错误不能刷新成功采集时间。
        self._render_resources()

    def _populate(self, rows):  # rows 中每项为 (入口名, 空闲/总数文本, 开机配置状态)。
        selected = self.selected_entry() or self.saved_entry  # 刷新时优先保留本次选择，其次恢复上次保存的入口。
        selected_item = next(iter(self.table.selection()), "")
        old_eligibility = self.power_eligibility
        self.power_eligibility = {}
        rows = list(rows)
        wanted = {row[0] for row in rows}
        for entry in self.table.get_children():
            if entry not in wanted:
                self.table.delete(entry)
        for index, (entry, slots, target) in enumerate(rows):
            self.power_eligibility[entry] = old_eligibility.get(entry, "未核验") if target is None else target
            if self.table.exists(entry):
                self.table.set(entry, "slots", slots)
                self.table.move(entry, "", index)
            else:
                self.table.insert("", index, iid=entry, text=entry, values=(slots, "", "", "", "—", "等待采样"),
                                  open=True, tags=("server",))
        entries = self.table.get_children()
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.refresh_servers()
        self._render_resources()
        if entries:
            self.table.selection_set(selected_item if selected_item and self.table.exists(selected_item)
                                     else selected if selected in entries else entries[0])

    def selected_entry(self):  # Treeview 返回选中 ID 元组；单选表最多有一项。
        selection = self.table.selection()
        item = selection[0] if selection else ""
        while item and self.table.parent(item):
            item = self.table.parent(item)
        return item  # 选中GPU子行时，监控命令和偏好仍使用父服务器入口。

    def _command(self):  # 从控件快照生成启动参数；不改写受 Git 管理的 YAML。
        entry = self.selected_entry()
        verified = self.power_eligibility.get(entry) == "可开机"
        live = self.live.get() and verified  # 本地旧UUID不代表实际存在容器；未核验入口始终只读。
        return monitor_command(self.config, entry, self.user.get(), self.poll.get(), live,
                               self.stop_file, self.convert_no_gpu.get() and live)

    def save(self):  # 保存输入前复用启动校验，避免把明显无效的设置留到下次。
        try:
            command = self._command()
            preference_path = self.local / "preferences.yaml"
            preferences = read_settings(preference_path) if settings_source(preference_path).exists() else {}
            preferences.pop("usage", None)  # 仅移除已弃用的双采样间隔，其余个人配置（含认证）保留。
            preferences.update({"entry": self.selected_entry(), "user": self.user.get().strip(),
                           "poll": self.poll.get(), "live": self.live.get(),
                           "convert_no_gpu": self.live.get() and self.convert_no_gpu.get()})
            write_settings(preference_path, preferences)
            mode = "自动开机已开启" if "--live" in command else "仅监控，不执行开机或关机"
            self.status.set(f"设置已保存 · {mode}" + (" · 当前监控保持原设置，下次开始生效" if self.job == "monitor" else ""))
            return True
        except (ValueError, argparse.ArgumentTypeError, OSError) as exc:
            messagebox.showerror("设置无效", str(exc), parent=self.root)
            return False

    def _busy(self, value):  # 统一切换操作权限；监控时能停止，登录时能确认同步。
        for button in [self.refresh_button, self.login_button, self.start_button]:
            button.configure(state="disabled" if value else "normal")
        self.stop_button.configure(state="normal" if value and self.job == "monitor" else "disabled")
        self.login_done.configure(state="normal" if value and self.job == "login" else "disabled")

    def _session(self, status, message, checked_at=""):
        labels = {"checking": "会话核验中", "valid": "登录会话有效", "invalid": "登录已失效", "unknown": "无法确认登录会话"}
        if status not in labels or not isinstance(message, str) or not isinstance(checked_at, str):
            raise ValueError("会话消息格式无效")
        self.session_status, self.session_message = status, message
        detail = "" if status == "valid" else f" · {message}"
        self.session_text.set(f"{labels[status]}{detail} · {checked_at or '—'}")
        self.session_label.configure(foreground={"valid": "#18733a", "invalid": "#b42318"}.get(status, "#7a5a18"))

    def _launch(self, command, job):  # 为所有任务建立相同的环境、日志管道和退出通知。
        if self.process is not None:  # 禁止同一窗口重复启动任务，避免同时占用浏览器资料。
            return
        env = child_environment(ROOT)  # 所有后台任务共用项目环境和资料位置。
        try:
            self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",  # 错误输出也进入底部日志；stdin 管道用于确认人工登录。
                errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc), parent=self.root)
            return
        self.job = job
        self.gpu_snapshot = dict(self.gpu_snapshot, refreshing=True)  # 请求在途保留上次读数，不清空整张资源表。
        self._render_resources()
        self._busy(True)
        if job in {"discover", "monitor", "login"}:
            self._session("checking", "等待人工登录及同步后核验" if job == "login" else "正在核验真实主机接口")
        mode = "自动开机已开启" if "--live" in command else "仅监控，不执行开机或关机"
        self.status.set({"monitor": f"监控进程已启动 · {mode} · 等待首次采集", "discover": "正在读取实际服务器列表…",
                         "login": "请在专用 Edge 完成登录，再点击“完成登录并关闭窗口”"}[job])
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()  # 管道 readline 可以阻塞，因此放入后台线程。

    def _read(self, process):  # 子线程只传递消息，所有控件更新留在 Tk 主线程。
        try:
            for line in process.stdout:
                self.events.put(("line", line))
        except (OSError, ValueError) as exc:
            self.events.put(("line", f"后台日志读取失败：{exc}\n"))
            if self.job == "monitor":
                self.stop_file.touch()  # 长期监控先请求现有安全停止，不能只等坏管道自行恢复。
            elif self.job == "discover" and process.poll() is None:
                process.terminate()  # 仅结束自有只读任务，不中断登录资料同步或正常监控。
            if self.job != "login":
                try:
                    process.stdout.close()  # 释放损坏的读端，避免worker写满管道后阻塞。
                except (OSError, ValueError):
                    pass
        finally:
            self.events.put(("exit", process.wait()))  # 管道异常仍等待真实退出，再恢复操作按钮。

    def _drain(self):  # 仅由 Tk 的 after 回调调用，在主线程消费跨线程消息。
        reschedule = True
        try:
            for _ in range(200):  # 限制处理量，让鼠标、重绘和停止按钮仍有机会响应。
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "training":
                        self.training.receive(value)
                    elif kind == "exit":
                        process, self.process = self.process, None  # 真实退出先解除任务锁，流清理异常不再卡住按钮。
                        self._busy(False)
                        if process is not None:
                            for stream in (process.stdout, process.stdin):
                                try:
                                    stream.close()
                                except (OSError, ValueError):
                                    pass
                        if self.job in {"discover", "monitor", "login"} and self.session_status == "checking":
                            self._session("unknown", "核验未完成，后台任务失败，请重试" if value else "核验未完成，后台任务已结束，请重试")
                        if self.gpu_snapshot.get("refreshing"):
                            self.gpu_snapshot = dict(self.gpu_snapshot, refreshing=False)
                            if value and self.job in {"discover", "monitor"}:
                                self.gpu_snapshot["error"] = "本轮采集未完成"  # 后台已退出，不能永久显示正在采集。
                        gpu_error = self.gpu_snapshot.get("error", "")
                        meaningful_gpu_error = gpu_error not in {"", "等待采样", "尚未采样"}
                        self.status.set(self.session_message if self.session_status in {"invalid", "unknown"}
                                        else gpu_error if meaningful_gpu_error else "任务已结束" if value == 0
                                        else "任务失败，请重试或检查连接设置")
                        if self.closing and self._shutdown_ready():
                            reschedule = False
                            self.root.destroy()
                            return
                    elif value.startswith(SESSION_PREFIX):
                        data = json.loads(value[len(SESSION_PREFIX):])
                        self._session(data["status"], data["message"], data["checked_at"])
                        if data["status"] in {"invalid", "unknown"}:
                            self.status.set(data["message"])
                            self._append_output(data["message"], "error")
                    elif value.startswith(GPU_PREFIX):
                        self._accept_gpu_snapshot(json.loads(value[len(GPU_PREFIX):]))
                        if self.gpu_snapshot.get("error"):
                            self.status.set(self.gpu_snapshot["error"])
                    elif value.startswith(DATA_PREFIX):
                        self._populate(json.loads(value[len(DATA_PREFIX):]))
                        self.status.set("服务器列表已刷新")
                    else:
                        tag = status_level(value)
                        if value.startswith(REPORT_PREFIX):
                            report = json.loads(value[len(REPORT_PREFIX):])
                            tag = report["level"]
                            if tag not in {"normal", "success", "error"} or len(report["lines"]) != 2:
                                raise ValueError("监控消息格式无效")
                            value = "\n".join(report["lines"]) + "\n"
                        if value.startswith("WATCHER_STATUS "):
                            value = value.removeprefix("WATCHER_STATUS ")
                            self.status.set(value.strip())
                        if re.match(r"^\[\d{2}:\d{2}:\d{2}\] 监控", value):
                            self.status.set(value.splitlines()[0])  # 顶部摘要复用首行；整组颜色由采集结果决定，不从用户名猜测。
                            self.status_label.configure(foreground={"success": "#18733a", "error": "#b42318"}.get(tag, "#245cc4"))
                        elif tag == "error":
                            self.status.set(value.strip().splitlines()[0][:240])
                        self._append_output(value, tag)  # 保留解码后的两行监控及后台实时信息，不显示内部JSON协议。
                except Exception as exc:
                    self.status.set(f"后台消息处理失败：{exc}")
        finally:
            if reschedule:
                if self.closing and self._shutdown_ready():
                    self.root.destroy()
                    return
                if not self.closing:
                    self.training.tick()
                for _, _, pane in self.external_panes.values():
                    pane.render()
                try:
                    self._render_resources()
                except (KeyError, TypeError, ValueError) as exc:
                    self.status.set(f"GPU 图表数据不可用：{exc}")
                self.root.after(100, self._drain)  # 坏消息不能中断后续退出通知和按钮恢复。

    def start(self):  # 每次监控使用当前设置启动新进程，状态与上一轮任务隔离。
        if self.process is not None or not self.save():
            return
        self.stop_file.unlink(missing_ok=True)  # 清除该窗口上一次停止留下的信号，否则新进程会立即退出。
        self._launch(self._command(), "monitor")

    def stop(self):  # 请求协作退出，不强杀正在操作浏览器或数据库的进程。
        if self.process is not None and self.job == "monitor":
            self.stop_file.touch()  # 核心在采集步骤之间及轮询等待期间检查文件是否存在。
            self.stop_button.configure(state="disabled")
            self.status.set("正在安全停止 · 等待当前网络请求完成并保存状态")

    def refresh(self):  # 独立任务读取当前账号的主机列表；不会发起实例开机。
        self._launch(worker_command("discover"), "discover")

    def login(self):  # 浏览器由登录模块打开，验证码仍由用户手动完成。
        self._launch(worker_command("login"), "login")

    def finish_login(self):  # 按钮代替控制台 Enter，让登录模块正常关窗、等待保存并核验会话。
        if self.process is not None and self.job == "login" and self.process.poll() is None:
            try:
                self.process.stdin.write("\n")
                self.process.stdin.flush()  # 立即把换行发送给 input()，不能让它停留在 Python 缓冲区。
                self.login_done.configure(state="disabled")
                self.status.set("正在关闭专用登录窗口并核验会话…")
            except (OSError, ValueError) as exc:
                self.status.set(f"登录同步失败：{exc}")

    def _shutdown_ready(self):
        return (self.process is None and not self.training.busy()
                and self.training.ai.shutdown_complete())

    def close(self):  # 空闲时立即关闭；任务运行中等待资源释放；登录中提示先完成同步。
        if self.process is not None and self.job == "login":
            messagebox.showinfo("登录尚未完成", "请先完成登录，再点击“完成登录并关闭窗口”，等待会话核验结束。", parent=self.root)
        else:
            if not self.closing:
                try:
                    self.icons.sync_executable()
                except OSError as exc:
                    messagebox.showerror("EXE图标未同步", str(exc), parent=self.root)
                    return
            self.closing = True
            if self.network_window is not None and self.network_window.winfo_exists():
                self.network_window.destroy()  # 先取消详情窗口定时器，后台读表不阻塞正常收尾。
            self.training.ai.begin_shutdown()  # 停止接单并有界收尾，不自动重发收费请求。
            self.training.stop_all()
            self.stop()
            if self._shutdown_ready():
                self.root.destroy()
            else:
                self.status.set("正在等待后台任务安全结束后关闭窗口…")



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


def discover():  # 子进程模式：采集结果编码为一行 JSON，供父进程更新服务器表格。
    from .collectors import PlatformBrowserCollector, TelemetryApiCollector
    from .collectors.telemetry import TelemetryUnavailable
    from .collectors.platform import PlatformAuthenticationError
    config = load_config(default_config_path(ROOT))
    collector = PlatformBrowserCollector(config.platform)
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


def main():  # 同一模块既可作为桌面入口，也可作为无界面的主机发现子进程。
    if "--discover" in sys.argv:
        discover()
        return
    parser = argparse.ArgumentParser(description="Auto算力与训练调度 图形界面")
    parser.add_argument("--debug", action="store_true", help="调试开发模式，默认只读且不切换无卡实例")
    args = parser.parse_args()
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoDL.GPUWatcher")
    root = tk.Tk()
    WatcherWindow(root, debug=args.debug)
    root.mainloop()  # Tk 处理用户输入、重绘和 after 回调，直到窗口被销毁。


if __name__ == "__main__":
    main()
