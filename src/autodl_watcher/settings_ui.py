"""所有服务器共用的设置窗口。"""
from dataclasses import replace  # 编辑时创建新配置，保留正在使用的配置快照。
import tkinter as tk
from tkinter import ttk
import weakref

from .app_icon import IconSettings
from .training_ai_ui import AISettings
from . import ai_credentials, telemetry_auth


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
        self.build_telemetry_settings(parent)

    def build_telemetry_settings(self, parent):
        app = self.app
        try:
            saved = telemetry_auth.settings(app.config.telemetry.endpoint, app.local.parent)
            error = ""
        except (OSError, ValueError) as exc:
            saved = {"endpoint": app.config.telemetry.endpoint, "username": "", "password_auth": False, "auth_mode": "form"}
            error = str(exc)
        box = ttk.LabelFrame(parent, text="GPU 采集站", padding=8)
        box.pack(fill="x", pady=(14, 0))
        box.columnconfigure(1, weight=1)
        self.telemetry_endpoint = tk.StringVar(value=saved["endpoint"])
        self.telemetry_username = tk.StringVar(value=saved["username"])
        self.telemetry_password = tk.StringVar(value="")  # 不把已保存的密码回填到界面变量。
        self.telemetry_password_auth = tk.BooleanVar(value=saved["password_auth"])
        self.telemetry_mode = tk.StringVar(value=telemetry_auth.AUTH_MODES[saved["auth_mode"]])
        password_ref, enabled_ref = weakref.ref(self.telemetry_password), weakref.ref(self.telemetry_password_auth)
        def enable_password(*_):
            password, enabled = password_ref(), enabled_ref()
            if password is not None and enabled is not None and password.get():
                enabled.set(True)  # 输入密码即启用；用户仍可明确取消勾选。
        self.telemetry_password.trace_add("write", enable_password)  # 弱引用不形成Tk变量循环，避免延迟到工作线程回收。
        for row, (label, variable, secret) in enumerate((("采集 API 地址", self.telemetry_endpoint, False),
                ("用户名（仅Basic）", self.telemetry_username, False), ("站点密码", self.telemetry_password, True))):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(box, textvariable=variable, show="●" if secret else "").grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Checkbutton(box, text="启用站点密码认证", variable=self.telemetry_password_auth,
                        style="Watcher.TCheckbutton").grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(box, text="认证方式").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Combobox(box, textvariable=self.telemetry_mode, values=list(telemetry_auth.AUTH_MODES.values()),
                     state="readonly").grid(row=4, column=1, sticky="ew", pady=3)
        ttk.Label(box, text="此GPU站点选“站点密码登录”，不需要用户名；输入新密码会勾选启用。\n"
                  "密码留空保留已有密码，只存 Windows 凭据管理器；登录会话在内存中复用。\n"
                  "保存后在空闲时刷新服务器；监控中的采集地址下次开始生效。保存不代表认证已验证。", wraplength=720).grid(
                      row=5, column=0, columnspan=2, sticky="w", pady=3)
        self.telemetry_reason = ttk.Label(box, text=error, foreground="#b42318", wraplength=720)
        self.telemetry_reason.grid(row=6, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(box)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(7, 0))
        ttk.Button(buttons, text="删除此站点密码", command=self.delete_telemetry_password).pack(side="left", padx=5)
        ttk.Button(buttons, text="保存采集站设置", command=self.save_telemetry_settings).pack(side="left")

    def save_telemetry_settings(self):
        from .config import load_config
        from .paths import default_config_path
        try:
            mode = next(key for key, label in telemetry_auth.AUTH_MODES.items() if label == self.telemetry_mode.get())
            telemetry_auth.save_settings(self.telemetry_endpoint.get(), self.telemetry_username.get(),
                self.telemetry_password_auth.get(), self.telemetry_password.get(), self.app.local.parent, auth_mode=mode)
            self.app.config = load_config(default_config_path(self.app.local.parent))
        except (OSError, ValueError, ai_credentials.CredentialError) as exc:
            self.telemetry_reason.configure(text=str(exc), foreground="#b42318")
            return False
        self.telemetry_password.set("")
        self.telemetry_reason.configure(text="采集站设置已保存；请刷新服务器核验。", foreground="#18733a")
        return True

    def delete_telemetry_password(self):
        try:
            telemetry_auth.delete_password(self.telemetry_endpoint.get())
        except (ValueError, ai_credentials.CredentialError) as exc:
            self.telemetry_reason.configure(text=str(exc), foreground="#b42318")
            return
        self.telemetry_password.set("")
        self.telemetry_reason.configure(text="此站点密码已删除；需要时重新保存。", foreground="#245cc4")

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
        pinned = tk.BooleanVar(value=server.pinned)
        pin_check = ttk.Checkbutton(form, text="固定到顶部标签（点击即保存）", variable=pinned, style="Watcher.TCheckbutton")
        pin_check.grid(row=6, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(form, text="已有服务器的固定选项点击立即保存；新服务器先保存连接。\n固定、收起不停止监控；SSH连接修改仍需点击“保存连接”，下次启动生效。\n使用已有SSH密钥和已核验主机；首次连接先确认SSH可用。", wraplength=570).grid(row=7, column=0, columnspan=2, sticky="w", pady=8)
        reason = ttk.Label(form, foreground="#b42318", wraplength=570)
        reason.grid(row=8, column=0, columnspan=2, sticky="w")
        def pin_changed():
            current = next((item for item in self.app.servers if item.id == server.id), None)
            if current is None:
                reason.configure(text="固定选项已选择；请先保存新服务器连接。", foreground="#8a5b11")
                return
            try:
                self.app.save_server(replace(current, pinned=pinned.get()))  # 只保存已保存配置的固定字段，不提交尚未保存的SSH文本。
            except (ValueError, OSError) as exc:
                pinned.set(current.pinned)  # 写入失败恢复勾选状态，避免界面与持久配置不一致。
                reason.configure(text="固定设置保存失败：" + str(exc), foreground="#b42318")
                return
            message = "已固定到顶部标签。" if pinned.get() else "已取消固定；正在运行的监控继续。"
            reason.configure(text=message, foreground="#245c45")
        pin_check.configure(command=pin_changed)
        def apply():  # 表单负责输入转换，应用统一协调保存、服务注册与页面刷新。
            try:
                updated = replace(server, **{key: value.get().strip() for key, value in values.items() if key != "stalled_minutes"},
                                  stalled_minutes=float(values["stalled_minutes"].get()), pinned=pinned.get()).validate()
                self.app.save_server(updated)  # 成功写入后才更新运行服务的待生效配置。
                reason.configure(text="连接已保存；固定选项已生效，监控中的连接修改下次启动生效。", foreground="#245c45")
            except (ValueError, OSError) as exc:
                reason.configure(text=str(exc), foreground="#b42318")  # 校验或写入失败留在当前表单供修改。
        actions = ttk.Frame(form)
        actions.grid(row=9, column=0, columnspan=2, sticky="e", pady=8)
        if any(item.id == server.id for item in self.app.servers):
            ttk.Button(actions, text="打开训练页", command=lambda: self.app.open_training(server.id)).pack(side="left", padx=4)
        ttk.Button(actions, text="保存连接", command=apply).pack(side="left", padx=4)

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
