"""所有服务器共用的设置窗口。"""
from dataclasses import replace  # 编辑时创建新配置，保留正在使用的配置快照。
import tkinter as tk
from tkinter import ttk

from .app_icon import IconSettings
from .training_ai_ui import AISettings


class GlobalSettings(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("全局设置 · Auto算力与训练调度")
        self.geometry("850x700")
        self.minsize(780, 630)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=8)
        servers = ttk.Frame(self.tabs, padding=8)
        monitor = ttk.Frame(self.tabs, padding=8)
        self.build_monitor_settings(monitor)  # 全局设置拥有全部监控参数控件。
        ai = AISettings(self.tabs, app.training.ai.path, app.training.ai.reload)
        icon = IconSettings(self.tabs, app.icons)
        self.pages = {"servers": servers, "ai": ai, "icon": icon, "monitor": monitor}
        for key, label in (("monitor", "AutoDL监控"), ("servers", "服务器连接"), ("ai", "AI规则与消费"), ("icon", "应用图标")):
            self.tabs.add(self.pages[key], text=label)
        left = ttk.Frame(servers)
        left.pack(side="left", fill="y", padx=(0, 10))
        self.servers = ttk.Treeview(left, show="tree", selectmode="browse", height=14)
        self.servers.column("#0", width=145)
        self.servers.pack(fill="both", expand=True)
        self.servers.bind("<<TreeviewSelect>>", self.select_server)
        ttk.Button(left, text="添加服务器", command=app.edit_server).pack(fill="x", pady=8)
        self.server_form = ttk.Frame(servers)
        self.server_form.pack(side="left", fill="both", expand=True)
        self.server_form.columnconfigure(1, weight=1)
        ttk.Label(self.server_form, text="选择服务器配置SSH连接；未配置的AutoDL入口也可设置。", wraplength=470).pack(anchor="w")
        self.refresh_servers()

    def build_monitor_settings(self, parent):  # 控件属于设置窗口；输入变量沿用应用的待生效配置。
        app = self.app
        options = ttk.LabelFrame(parent, text="AutoDL 监控设置", padding=8)
        options.pack(fill="x", pady=3)
        self.inputs = []
        for col, (label, value) in enumerate([("用户", app.user), ("间隔/秒", app.poll)]):
            ttk.Label(options, text=label).grid(row=0, column=col * 2, sticky="w", padx=2)
            entry = ttk.Entry(options, textvariable=value, width=12 if col == 0 else 6)
            entry.grid(row=0, column=col * 2 + 1, sticky="ew", padx=2, pady=3)
            options.columnconfigure(col * 2 + 1, weight=1)
            self.inputs.append(entry)
        self.live_check = ttk.Checkbutton(options, text="启用真实自动开机（会产生费用）", variable=app.live, style="Watcher.TCheckbutton",
                                          command=lambda: app.convert_no_gpu.set(False) if not app.live.get() else None)
        self.live_check.grid(row=1, column=0, columnspan=4, sticky="w", pady=2)
        self.convert_check = ttk.Checkbutton(options, text="关闭本账号全部无卡实例（会中断）", variable=app.convert_no_gpu, style="Watcher.TCheckbutton")
        self.convert_check.grid(row=2, column=0, columnspan=4, sticky="w", pady=2)
        self.save_button = ttk.Button(parent, text="保存监控设置", command=app.save)
        self.save_button.pack(anchor="e", padx=8, pady=8)
        ttk.Label(parent, text="监控中可以编辑并保存；当前监控保持原设置，下次开始监控生效。", wraplength=700).pack(anchor="w", padx=8)

    def edit_server(self, server):  # AutoDL 入口和独立服务器使用同一表单与校验流程。
        form = self.server_form
        for child in form.winfo_children():
            child.destroy()  # 切换编辑对象时释放前一份表单，不积累输入控件。
        values = {}
        fields = [("name", "显示名称"), ("ssh_alias", "SSH别名（用户.ssh/config）"), ("python", "远程Python路径"),
                  ("project", "项目目录（可选，绝对路径）"), ("scripts", "脚本名/通配符（默认另识别tmux/日志）"), ("stalled_minutes", "无进展提醒（分钟）")]
        for row, (key, label) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4)
            values[key] = tk.StringVar(value=str(getattr(server, key)))  # 仅编辑本次表单，不直接改活动服务配置。
            ttk.Entry(form, textvariable=values[key], width=42).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(form, text="使用已有SSH密钥和已核验主机；不会保存密码，也不会修改远端训练。\n首次连接请先在终端确认SSH可用。监控中可保存，新配置下次启动本服务器训练监控时生效。", wraplength=570).grid(row=6, column=0, columnspan=2, sticky="w", pady=8)
        reason = ttk.Label(form, foreground="#b42318", wraplength=570)
        reason.grid(row=7, column=0, columnspan=2, sticky="w")
        def apply():  # 表单负责输入转换，应用统一协调保存、服务注册与页面刷新。
            try:
                updated = replace(server, **{key: value.get().strip() for key, value in values.items() if key != "stalled_minutes"},
                                  stalled_minutes=float(values["stalled_minutes"].get())).validate()
                self.app.save_server(updated)  # 成功写入后才更新运行服务的待生效配置。
                reason.configure(text="连接已保存；监控中的连接下次启动生效。", foreground="#245c45")
            except (ValueError, OSError) as exc:
                reason.configure(text=str(exc), foreground="#b42318")  # 校验或写入失败留在当前表单供修改。
        ttk.Button(form, text="保存连接", command=apply).grid(row=8, column=1, sticky="e", pady=8)

    def refresh_servers(self):
        self.servers.delete(*self.servers.get_children())
        for server in self.app.servers:
            self.servers.insert("", "end", iid=server.id, text=server.name)
        configured = {s.entry for s in self.app.servers}
        for entry in self.app.table.get_children():
            if entry not in configured:
                self.servers.insert("", "end", iid="entry:" + entry, text=entry.removeprefix("autodl-"))

    def select_server(self, event=None):
        selection = self.servers.selection()
        if selection:
            key = selection[0]
            self.app.edit_server(None, key[6:]) if key.startswith("entry:") else self.app.edit_server(key)
