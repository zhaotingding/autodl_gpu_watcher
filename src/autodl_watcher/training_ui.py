from __future__ import annotations

from datetime import datetime
import time
import unicodedata
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from .cli import positive_seconds  # 与 CLI 共用间隔校验，视图无需导入监控入口。


COLORS = {"success": ("#10372a", "#8bf0ad"), "error": ("#481f26", "#ff9ca8"),
          "warning": ("#40361f", "#ffe09a"), "idle": ("#27313e", "#f2f4f7")}


def metrics_text(summary):
    names = {"loss": "Loss", "acc": "ACC", "auc": "AUC", "video_auc": "视频AUC", "eer": "EER", "ap": "AP", "ce": "CE"}
    metrics = summary.get("metrics", {})
    parts = []
    for name, value in metrics.items():
        metric, separator, branch = name.partition("/")
        number = f"{value:.4g}" if 0 < abs(value) < 0.0001 else f"{value:.4f}"
        parts.append(f"{names.get(metric, metric)}{separator}{branch} {number}")
    return " · ".join(parts) or "—"



def _column(value, width):  # 中文姓名按显示宽度补空格，黑框列对齐且不截断原始采集数据。
    result, used = "", 0
    for char in str(value).replace("\n", " ").replace("\r", " "):
        size = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + size > width:
            break
        result, used = result + char, used + size
    return result + " " * (width - used)


def gpu_table(snapshot, name):
    stamp = datetime.fromtimestamp(snapshot["received_at"]).strftime("%H:%M:%S")
    lines = [f"===== {stamp} | {name} | 显存：整卡 / 进程分列 =====",
             "GPU   VRAM整卡(GiB)  UTIL  TEMP  USER                 PID       进程GiB  PROCESS / CONFIG · 开始时间 · 持续时间"]
    for gpu in snapshot.get("gpus", []):
        rows = [row for row in snapshot.get("gpu_processes", []) if row.get("gpu_index") == gpu["index"]]
        for row in rows or [{}]:
            total = f"{gpu['memory_used']/1024:.2f}/{gpu['memory_total']/1024:.2f}"
            memory = row.get("memory_used")
            process = row.get("process", "未读到GPU进程")
            if row.get("config"):
                process += " [" + row["config"] + "]"
            lines.append(_column(("容器" if snapshot.get("container") else "GPU") + str(gpu["index"]), 6) + _column(total, 15) + _column(f"{gpu['util']:.0f}%", 6)
                         + _column(f"{gpu['temperature']:.0f}°C", 6) + _column(row.get("user", "—"), 21)
                         + _column(row.get("pid", "—"), 10) + _column(f"{memory/1024:.2f}" if memory is not None else "—", 9) + process
                         + (" · " + process_time(dict(row, last_alive_at=snapshot.get("observed_at"))) if row else ""))
    if snapshot.get("gpu_error"):
        lines.append(snapshot["gpu_error"])
    if snapshot.get("container"):
        lines.append("容器编号不等于平台INDEX；宿主PID与用户归属未确认。")
    if not snapshot.get("gpus"):
        lines.append("GPU数据不可用")
    return "\n".join(lines) + "\n"


def task_memory(card):
    value = card.get("gpu_memory_mb")
    if not card.get("alive"):
        return "显存 —（已退出）"
    if value is None:
        peak = card.get("progress", {}).get("peak_memory_bytes")
        return f"显存峰值 {peak/1024**3:.2f} GiB（日志）" if peak is not None else "显存 无法核验"
    return f"显存 {value/1024:.2f} GiB"

def process_time(card):
    started = card.get("started_at")
    if started is None:
        return "进程开始 — · 持续时间 —"
    stamp = datetime.fromtimestamp(started).strftime("%m-%d %H:%M:%S")
    observed = card.get("last_alive_at")
    if observed is None:
        return f"进程开始 {stamp} · 持续时间未确认"
    elapsed = max(0, int(observed - started))
    days, rest = divmod(elapsed, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    duration = (f"{days}天 " if days else "") + f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"进程开始 {stamp} · 已运行 {duration}（截至最近存活采样）"


class TrainingPane(ttk.Frame):
    def __init__(self, parent, service, configure_server, interval, show_gpus=True):
        super().__init__(parent)
        self.service, self.configure_server, self.interval = service, configure_server, interval
        self.show_gpus, self.server_id = show_gpus, None
        self.render_key = None
        self.gpu_log_key = None
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 5))
        self.start_button = ttk.Button(bar, text="开始训练监控", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="停止", command=self.stop)
        self.stop_button.pack(side="left", padx=4)
        self.summary = ttk.Label(self, text="尚未配置训练连接", wraplength=560)
        self.summary.pack(fill="x", pady=(0, 5))
        if show_gpus:
            output = ttk.Frame(self)
            output.pack(fill="x", pady=(0, 5))
            horizontal = ttk.Scrollbar(output, orient="horizontal")
            horizontal.pack(side="bottom", fill="x")
            self.gpu_log = ScrolledText(output, height=5, width=1, wrap="none", state="disabled", font=("Consolas", 9),
                                       background="#0e151d", foreground="#8bf0ad", xscrollcommand=horizontal.set, padx=5, pady=3)
            self.gpu_log.pack(fill="x")
            horizontal.configure(command=self.gpu_log.xview)
            self.gpu_log.tag_configure("error", foreground="#ff9ca8")
            self.gpu_log.tag_configure("warning", foreground="#ffe09a")
        self.canvas = tk.Canvas(self, background="#17202e", highlightthickness=0, width=300, height=180)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.content = tk.Frame(self.canvas, background="#17202e")
        self.content_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))

    def _resize(self, event):
        self.canvas.itemconfigure(self.content_window, width=event.width)
        self.summary.configure(wraplength=max(200, event.width))
        for frame in self.content.winfo_children():
            for child in frame.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(wraplength=max(160, event.width - 32))

    def select(self, server_id):
        if self.server_id != server_id:
            self.server_id, self.render_key, self.gpu_log_key = server_id, None, None
            if self.show_gpus:
                self.gpu_log.configure(state="normal")
                self.gpu_log.delete("1.0", "end")
                self.gpu_log.configure(state="disabled")
            self.canvas.yview_moveto(0)
        self.render()

    def start(self):
        if self.server_id is None:
            self.configure_server()
            return
        try:
            self.service.start(self.server_id, positive_seconds(str(self.interval())))
            self.service.tick()
            self.render()
        except Exception as exc:
            self.summary.configure(text=str(exc), foreground="#b42318")

    def stop(self):
        if self.server_id is not None:
            self.service.stop(self.server_id)
            self.render()

    def render(self):
        state = self.service.status(self.server_id) if self.server_id is not None else None  # 只读取服务公开的轻量状态。
        snapshot = self.service.snapshot(self.server_id) if state else None  # 保留快照对象身份，避免无数据变化时重建卡片。
        server = self.service.server(self.server_id) if state else None  # 活动配置与待生效配置由服务区分。
        closing = bool(state and state["closing"])  # 停止状态与计数来自同一次状态读取。
        expired = bool(snapshot and time.time() - snapshot["received_at"] > max(90, state["interval"] * 2))
        # 请求在途和其他服务器的 AI 变化不改变本页卡片；只更新按钮状态。
        self.start_button.configure(state="disabled" if state and (state["active"] or state["busy"] or closing) else "normal")
        self.stop_button.configure(state="normal" if state and state["active"] else "disabled")
        key = (self.server_id, state["active"] if state else False, closing,
               id(snapshot), expired, state.get("pending") if state else None,
               state["generation"] if state else None, getattr(self, "show_history", False))
        if key == self.render_key:
            return
        self.render_key = key
        warning = (snapshot.get("error", "") if snapshot else "") or ("采样已过期" if expired else "")
        if not state:
            text = "此入口尚未配置SSH训练连接；平台监控仍可使用。"
        elif closing:
            text = "正在停止训练采集，回收本程序的SSH连接；下面保留最近结果。"
        elif not state["active"]:
            text = "训练采集已停止，下面保留最近结果。" if snapshot else "尚未开始训练监控（只读）"
        elif warning:
            text = warning
        elif snapshot:
            text = f"{server.name} · SSH用户 {snapshot.get('user', '—')} · 最近采集 {datetime.fromtimestamp(snapshot['received_at']):%H:%M:%S}"
        else:
            text = "正在连接，读取本人训练进程与日志…"
        if state and state["active"]:
            text += f" · 采集{state['samples']}次 / 连接尝试{state['attempts']}次"  # 显示连接计数，不持有连接对象。
        if state and state.get("pending"):
            text += " · 新连接已保存，下次启动训练监控生效"
        self.summary.configure(text=text, foreground="#9a6700" if warning else "#245c45")
        position = self.canvas.yview()[0]
        for child in self.content.winfo_children():
            child.destroy()
        if self.show_gpus and snapshot:
            log_key = (snapshot["received_at"], warning)
            if log_key != self.gpu_log_key:
                self.gpu_log_key = log_key
                text = f"[{datetime.now():%H:%M:%S}] {warning}；上次数据不作为当前状态。\n" if warning else gpu_table(snapshot, server.name)
                self.gpu_log.configure(state="normal")
                self.gpu_log.insert("end", text, "warning" if warning else "normal")
                if int(self.gpu_log.index("end-1c").split(".")[0]) > 1000:
                    self.gpu_log.delete("1.0", "201.0")
                self.gpu_log.see("end")
                self.gpu_log.configure(state="disabled")
        cards = snapshot.get("cards", []) if snapshot else []
        if not cards:
            frame = self._card("warning" if warning else "idle")
            self._label(frame, "无法确认训练状态" if warning else "无匹配训练进程" if snapshot and state["active"] else "等待训练采集", bold=True)
            if snapshot and state["active"] and not warning:
                self._label(frame, f"脚本匹配：{server.scripts} · 目录：{server.project or '当前SSH用户'}；可在全局设置中修改。")
        history_count = sum(bool(card.get("history")) for card in cards)
        if history_count:
            def toggle_history():
                self.show_history = not getattr(self, "show_history", False)
                self.render()
            ttk.Button(self.content, text=("收起" if getattr(self, "show_history", False) else "查看") + f"历史运行（{history_count}）", command=toggle_history).pack(anchor="e", padx=6)
        for card in cards:
            if card.get("history") and not getattr(self, "show_history", False):
                continue
            level = "error" if card["level"] == "error" else "warning" if warning or not state["active"] else card["level"]
            frame = self._card(level)
            when = datetime.fromtimestamp(card["advanced_at"]).strftime("%m-%d %H:%M:%S")
            suffix = " · 最近结果" if warning or not state["active"] else ""
            self._label(frame, f"{card['name']} · {card['status']} · 最近进展 {when}{suffix}", bold=True, color=COLORS[level][1])
            gpu = ",".join(f"#{index}" for index in card.get("gpu_indices", [])) or "待确认"
            self._label(frame, f"PID {card['pid']} · 用户 {card.get('user', '—')} · GPU {gpu} · {task_memory(card)}"
                        + (" · 上次采样" if warning or not state["active"] else ""))
            self._label(frame, process_time(card))
            if card.get("tmux_session"):
                self._label(frame, f"tmux：{card['tmux_session']}")
            self._label(frame, card.get("rule_status", "默认监控"), color=COLORS["warning"][1] if "不适用" in card.get("rule_status", "") else "#c0d6cd")
            progress = card["progress"]
            epoch, total = progress.get("epoch"), progress.get("total_epochs")
            raw = f" · 日志Epoch {progress['raw_epoch']}" if progress.get("raw_epoch") is not None else ""
            self._label(frame, f"轮次 {epoch if epoch is not None else '—'}/{total if total is not None else '—'}{raw} · 全局Step {progress.get('step') if progress.get('step') is not None else '—'}" + (f"/{progress['total_steps']}" if progress.get('total_steps') else ""))
            if epoch is not None and total and total > 0:
                ttk.Progressbar(frame, maximum=total, value=total if progress.get("completed") else min(total, max(0, epoch - 1)), style="Error.Horizontal.TProgressbar" if level == "error" else "Memory.Horizontal.TProgressbar").pack(fill="x", padx=8, pady=3)  # 条形仅表示此前已完成轮次，不猜当前轮内进度。
            train = progress.get("train", {})
            self._label(frame, f"训练（第{train.get('epoch') if train.get('epoch') is not None else '—'}轮，Step {train.get('step') if train.get('step') is not None else '—'}）：{metrics_text(train)}")
            for dataset, summary in progress.get("tests", {}).items():
                self._label(frame, f"测试 {dataset}（第{summary.get('epoch') if summary.get('epoch') is not None else '—'}轮，Step {summary.get('step') if summary.get('step') is not None else '—'}）：{metrics_text(summary)}")
            if progress.get("observations"):
                self._label(frame, "指标（阶段未注明）：" + metrics_text(progress["observations"]))
            if progress.get("phase") == "unknown":
                self._label(frame, "原文摘要（未解释）：\n" + card.get("preview", "没有可读输出"))
            if progress.get("error"):
                self._label(frame, progress["error"], color=COLORS["error"][1])
            actions = ttk.Frame(frame)
            actions.pack(anchor="e", padx=8, pady=(2, 6))
            ttk.Button(actions, text="训练原日志", command=lambda item=card: self.details(item)).pack(side="right")
            if getattr(self.service, "ai", None):
                ttk.Button(actions, text="生成 / 更新监控规则", command=lambda item=card: self.service.ai.explain(self.server_id, item)).pack(side="right", padx=4)

        self.content.update_idletasks()
        self.canvas.yview_moveto(position)

    def _card(self, level):
        frame = tk.Frame(self.content, background=COLORS[level][0], highlightbackground=COLORS[level][1], highlightthickness=1)
        frame.pack(fill="x", padx=6, pady=5)
        return frame

    def _label(self, frame, text, bold=False, color="#eef2f6"):
        label = tk.Label(frame, text=text, background=frame.cget("background"), foreground=color, anchor="w", justify="left",
                 wraplength=max(160, self.canvas.winfo_width() - 32), font=("Microsoft YaHei UI", 9, "bold" if bold else "normal"))
        label.pack(fill="x", padx=8, pady=2)
        label.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))

    def details(self, card):
        window = tk.Toplevel(self)
        window.title(f"{card['name']} · PID {card['pid']} · 训练原日志")
        window.geometry("780x440")
        ttk.Label(window, text=card.get("log_path") or "未找到训练日志", wraplength=740).pack(anchor="w", padx=8, pady=4)
        ttk.Label(window, text=card.get("details_note") or "最近采样的原文片段；长日志显示末尾64 KiB，可横向滚动。", wraplength=740).pack(anchor="w", padx=8)
        horizontal = ttk.Scrollbar(window, orient="horizontal")
        horizontal.pack(side="bottom", fill="x")
        text = ScrolledText(window, wrap="none", font=("Consolas", 10), xscrollcommand=horizontal.set)
        horizontal.configure(command=text.xview)
        text.pack(fill="both", expand=True)
        text.insert("end", card.get("details") or card.get("details_note") or "没有可读取的日志")
        text.see("end")
        text.configure(state="disabled")
