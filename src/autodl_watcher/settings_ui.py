"""所有服务器共用的设置窗口。"""
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
        app.build_monitor_settings(monitor)
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
