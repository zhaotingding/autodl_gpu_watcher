"""本机连接快照面板；采样在后台执行，Tk 主线程只接收和展示结果。"""
from __future__ import annotations

from datetime import datetime
import queue
import threading
import tkinter as tk
from tkinter import ttk


STATE_LABELS = {  # TCP 状态取自系统，不把握手完成解释为 SSH 已认证。
    "ESTABLISHED": "已建立（TCP）", "SYN_SENT": "正在连接", "SYN_RECEIVED": "等待握手", "SYN_RCVD": "等待握手",
    "LISTEN": "监听", "CLOSE_WAIT": "对端已关闭，待本机关闭", "FIN_WAIT1": "等待关闭确认",
    "FIN_WAIT2": "等待对端关闭", "CLOSING": "双方正在关闭", "LAST_ACK": "等待最后确认",
    "TIME_WAIT": "关闭后等待回收", "CLOSED": "已关闭（TCP）", "DELETE_TCB": "待删除",
    "BOUND": "本机已绑定", "UNKNOWN": "未确认（上次观察）",
}


def endpoint(address, port):
    if not address:
        return "—"  # UDP 的对端并不包含在绑定表中，不能猜测目标。
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def timestamp(value, full=False):
    pattern = "%Y-%m-%d %H:%M:%S" if full else "%m-%d %H:%M:%S"
    return datetime.fromtimestamp(value).strftime(pattern) if value else "未知"


def duration(seconds):
    seconds = max(0, int(seconds))  # 首次观察时间不是实际握手时间，只展示已观察区间。
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return (f"{days}天 " if days else "") + f"{hours:02}:{minutes:02}:{seconds:02}"


def state_label(record, stale=False):
    if record.closed_at is not None:
        return "未再观察到"
    if stale:
        return "未确认（上次观察）"
    return STATE_LABELS.get(record.state, record.state)


class NetworkPanel(tk.Toplevel):
    REFRESH_MS = 3000  # 只在面板打开时进行本机快照采样，不影响业务采集间隔。
    DRAIN_MS = 80
    COLUMNS = ("purpose", "protocol", "state", "process", "source", "target", "first_seen", "duration")

    def __init__(self, master, monitor, targets_provider=None):
        super().__init__(master)
        self.monitor = monitor
        self.targets_provider = targets_provider
        self._snapshot = None
        self._records = {}
        self._messages = queue.Queue()
        self._closed, self._busy = False, False
        self._generation = 0
        self._drain_after = self._refresh_after = None
        self._needs_refresh = False
        self._last_error = ""
        self._sort_column, self._sort_reverse = "first_seen", True
        self.filter_text = tk.StringVar(self, value="")
        self.view = tk.StringVar(self, value="current")
        self.automatic = tk.BooleanVar(self, value=True)
        self.summary = tk.StringVar(self, value="正在读取本机连接快照…")
        self.status = tk.StringVar(self, value="")
        self.title("网络连接详情 · Auto算力与训练调度")
        self.geometry("1200x700")
        self.minsize(960, 550)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.filter_text.trace_add("write", lambda *_: self._render())
        self._start_drain()
        self.refresh()

    def _build(self):
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        bar = ttk.Frame(body)
        bar.pack(fill="x")
        ttk.Label(bar, text="网络连接详情", font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        self.refresh_button = ttk.Button(bar, text="立即刷新", command=self.refresh)
        self.refresh_button.pack(side="right")
        ttk.Checkbutton(bar, text="每3秒自动刷新", variable=self.automatic,
                        style="Watcher.TCheckbutton", command=self._automatic_changed).pack(side="right", padx=8)
        ttk.Label(body, textvariable=self.summary).pack(anchor="w", pady=(7, 4))
        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=(0, 7))
        for label, value in (("当前记录", "current"), ("未再观察到", "closed"), ("全部", "all")):
            ttk.Radiobutton(controls, text=label, value=value, variable=self.view,
                            command=self._render).pack(side="left", padx=(0, 10))
        ttk.Label(controls, text="筛选：").pack(side="left", padx=(10, 0))
        ttk.Entry(controls, textvariable=self.filter_text).pack(side="left", fill="x", expand=True)
        services = ttk.LabelFrame(body, text="服务网址 / SSH目标（来自应用配置，不是TCP反查结果）", padding=4)
        services.pack(fill="x", pady=(0, 6))
        self.target_table = ttk.Treeview(services, columns=("purpose", "kind", "address"), show="headings", height=4)
        for key, label, width in (("purpose", "用途 / 服务器", 230), ("kind", "协议", 80), ("address", "具体网址 / SSH别名", 720)):
            self.target_table.heading(key, text=label)
            self.target_table.column(key, width=width, minwidth=60, stretch=key == "address")
        self.target_table.pack(side="left", fill="both", expand=True)
        service_scroll = ttk.Scrollbar(services, orient="vertical", command=self.target_table.yview)
        service_scroll.pack(side="right", fill="y")
        self.target_table.configure(yscrollcommand=service_scroll.set)
        table_frame = ttk.Frame(body)
        table_frame.pack(fill="both", expand=True)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=self.COLUMNS, show="headings", selectmode="browse")
        for column, label, width in (("purpose", "用途 / 服务器", 180), ("protocol", "协议", 75), ("state", "系统状态 / 观察状态", 175),
                                      ("process", "进程（PID）", 165), ("source", "源地址", 190),
                                      ("target", "目标地址", 190), ("first_seen", "首次观察", 135),
                                      ("duration", "已观察时长", 105)):
            self.table.heading(column, text=label, command=lambda key=column: self._sort(key))
            self.table.column(column, width=width, minwidth=65, stretch=False)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.tag_configure("history", foreground="#667085")
        self.table.tag_configure("unknown", foreground="#8a5b11")
        self.table.bind("<<TreeviewSelect>>", lambda _: self._render_detail())
        details = ttk.LabelFrame(body, text="选中记录详情", padding=6)
        details.pack(fill="x", pady=(8, 4))
        self.detail = tk.Text(details, height=6, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
        self.detail.pack(side="left", fill="both", expand=True)
        detail_scroll = ttk.Scrollbar(details, orient="vertical", command=self.detail.yview)
        detail_scroll.pack(side="right", fill="y")
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.status_label = ttk.Label(body, textvariable=self.status, foreground="#8a5b11", wraplength=1120)
        self.status_label.pack(anchor="w", pady=(2, 4))
        notes = ttk.Label(body, text="每3秒本机快照，短连接可能遗漏。网址来自配置；SSH独立于HTTPS，IP不能直接反推出请求网址。", wraplength=1120)
        notes.pack(anchor="w")
        body.bind("<Configure>", lambda event: (notes.configure(wraplength=max(400, event.width - 10)),
            self.status_label.configure(wraplength=max(400, event.width - 10))))

    def refresh(self):
        if self._closed or self._busy:
            return False  # 手动刷新、定时刷新与重新打开共用同一个在途读取。
        self._cancel_timer("_refresh_after")
        self._busy = True
        self.refresh_button.configure(state="disabled")
        generation = self._generation
        self.status.set("正在后台读取本机状态…")
        if self.targets_provider is not None:
            self.monitor.set_targets(self.targets_provider())  # 只读主界面现有配置，不触发业务网络请求。
        messages, monitor = self._messages, self.monitor

        def read():
            try:
                messages.put((generation, monitor.poll(), ""))
            except Exception as exc:
                messages.put((generation, None, f"连接快照读取失败：{exc}"))
        threading.Thread(target=read, name="network-snapshot", daemon=True).start()  # 子线程不访问任何 Tk 控件。
        return True

    def _start_drain(self):
        if self._drain_after is None and not self._closed:
            self._drain_after = self.after(self.DRAIN_MS, self._drain)

    def _drain(self):
        self._drain_after = None
        if self._closed:
            return
        try:
            generation, snapshot, error = self._messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self._busy = False
            self.refresh_button.configure(state="normal")
            if generation == self._generation:
                self._last_error = error
                if snapshot is not None:
                    self._snapshot = snapshot
                self._render()
            if self._needs_refresh or generation != self._generation:
                self._needs_refresh = False
                self.refresh()  # 关闭期间完成的旧结果丢弃，重新打开后只接纳新一代观察。
            elif self.automatic.get():
                self._schedule_refresh()
        self._start_drain()

    def _schedule_refresh(self):
        self._cancel_timer("_refresh_after")
        if not self._closed and self.automatic.get():
            self._refresh_after = self.after(self.REFRESH_MS, self._timed_refresh)

    def _timed_refresh(self):
        self._refresh_after = None
        self.refresh()

    def _automatic_changed(self):
        self._cancel_timer("_refresh_after")
        if self.automatic.get():
            self.refresh()
        elif not self._busy:
            self.status.set("已暂停自动刷新，显示上次观察；仍可点击“立即刷新”。")

    def _sort(self, column):
        self._sort_reverse = not self._sort_reverse if column == self._sort_column else False
        self._sort_column = column
        self._render()

    def _values(self, record, captured_at):
        end = self._observed_until(record, captured_at)
        return (getattr(record, "purpose", "") or record.process_name, record.protocol,
                state_label(record, bool(self._last_error)), f"{record.process_name}（{record.pid}）",
                endpoint(record.local_address, record.local_port), endpoint(record.remote_address, record.remote_port),
                timestamp(record.first_seen), duration(end - record.first_seen))

    def _observed_until(self, record, captured_at):
        if record.closed_at is not None:
            return record.closed_at
        return record.last_seen if record.state == "UNKNOWN" or self._last_error else captured_at

    def _render(self):
        if self._closed:
            return
        snapshot = self._snapshot
        if snapshot is None:
            self.summary.set("状态未知，尚未取得连接快照" if self._last_error else "正在读取本机连接快照…")
            self.status.set(self._last_error)
            return
        entries = snapshot.entries
        self.target_table.delete(*self.target_table.get_children())
        for item in getattr(snapshot, "targets", ()):
            if item.target:
                self.target_table.insert("", "end", values=(item.purpose, item.kind, item.target))
        current = sum(record.closed_at is None for record in entries)
        warnings = tuple(snapshot.errors)
        count_text = ("状态未知，未取得可核验的连接记录" if not entries and (warnings or self._last_error) else
                      f"当前记录 {current}  ·  未再观察到 {len(entries) - current}")
        self.summary.set(f"{count_text}  ·  "
                         f"快照 {timestamp(snapshot.captured_at)}  ·  读取 {snapshot.elapsed_seconds:.2f} 秒"
                         + ("  ·  读取不完整" if warnings or self._last_error else ""))
        self.status.set((self._last_error + "；表格保留上次观察，不能代表当前状态。") if self._last_error else "；".join(warnings) or
                        ("已暂停自动刷新，显示上次观察。" if not self.automatic.get() else ""))
        query = self.filter_text.get().strip().casefold()
        visible = []
        self._records = {str(record.id): record for record in entries}
        for record in entries:
            if self.view.get() == "current" and record.closed_at is not None:
                continue
            if self.view.get() == "closed" and record.closed_at is None:
                continue
            values = self._values(record, snapshot.captured_at)
            if query and query not in " ".join(map(str, (*values, record.state))).casefold():
                continue
            column = self.COLUMNS.index(self._sort_column)
            sort_value = (record.first_seen if self._sort_column == "first_seen" else
                          self._observed_until(record, snapshot.captured_at) - record.first_seen
                          if self._sort_column == "duration" else values[column].casefold())
            visible.append((sort_value, str(record.id), record, values))
        visible.sort(key=lambda item: (item[0], item[1]), reverse=self._sort_reverse)
        wanted = {item[1] for item in visible}
        for item_id in self.table.get_children():
            if item_id not in wanted:
                self.table.delete(item_id)
        for index, (_, item_id, record, values) in enumerate(visible):
            tags = ("history",) if record.closed_at is not None else ("unknown",) if record.state == "UNKNOWN" or self._last_error else ()
            if self.table.exists(item_id):
                self.table.item(item_id, values=values, tags=tags)
                self.table.move(item_id, "", index)
            else:
                self.table.insert("", index, iid=item_id, values=values, tags=tags)
        self._render_detail()

    def _render_detail(self):
        selected = self.table.selection()
        record = self._records.get(selected[0]) if selected else None
        if record is None:
            text = "点击一条记录查看系统状态、PID、源/目标端点及观察时间。"
        else:
            text = (f"进程：{record.process_name}  PID：{record.pid}  进程启动：{timestamp(record.process_started_at, True)}\n"
                    f"用途：{getattr(record, 'purpose', '') or '系统连接'}  配置目标：{getattr(record, 'logical_target', '') or '未核对当前TCP对应网址'}\n"
                    f"协议：{record.protocol}  原始状态：{record.state}  观察状态：{state_label(record, bool(self._last_error))}\n"
                    f"源地址：{endpoint(record.local_address, record.local_port)}\n"
                    f"目标地址：{endpoint(record.remote_address, record.remote_port)}\n"
                    f"首次观察：{timestamp(record.first_seen, True)}  最近观察：{timestamp(record.last_seen, True)}\n"
                    f"未再观察到：{timestamp(record.closed_at, True) if record.closed_at is not None else '—'}\n"
                    f"记录标识：{record.id}\n"
                    "来源：本机系统连接表；记录消失只表示后续快照未再见到，不证明业务已经结束。")
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _cancel_timer(self, attribute):
        timer = getattr(self, attribute)
        setattr(self, attribute, None)
        if timer is not None:
            try:
                self.after_cancel(timer)
            except tk.TclError:
                pass  # 主窗口销毁期间，可能已由 Tk 一并撤销回调。

    def close(self):
        self._closed = True
        self._generation += 1
        self._cancel_timer("_drain_after")
        self._cancel_timer("_refresh_after")
        self.withdraw()  # 保留唯一后台读取；反复关闭/打开不堆积线程，也不关闭业务连接。

    def show(self):
        if self._closed:
            self._closed = False
            self._needs_refresh = self._busy
            self._start_drain()
            self.refresh()
        self.deiconify()
        self.lift()
        return self

    def destroy(self):
        self._closed = True
        self._generation += 1
        self._cancel_timer("_drain_after")
        self._cancel_timer("_refresh_after")
        super().destroy()  # 后台只向独立队列投递，销毁窗口后不会调用 Tk。
