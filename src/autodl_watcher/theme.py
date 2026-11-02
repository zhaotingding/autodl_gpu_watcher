"""共享深浅主题；选择合并到现有个人YAML，界面变化不触发业务采样。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from .yaml_settings import read_settings, settings_source, write_settings

PALETTES = {
    "dark": dict(bg="#181c23", surface="#242a34", field="#202630", fg="#e4e9f2", muted="#a6b2c4",
                 border="#465163", selected="#355373", selected_fg="#ffffff", accent="#82b6ff",
                 success="#8bf0ad", error="#ff9ca8", warning="#ffe09a", terminal="#0e151d",
                 terminal_fg="#8bf0ad", output="#17202e", output_fg="#e2e8f0"),
    "light": dict(bg="#eceef1", surface="#e2e6ec", field="#ffffff", fg="#243247", muted="#59677a",
                  border="#b8c2cf", selected="#cce2ff", selected_fg="#142b48", accent="#245cc4",
                  success="#18733a", error="#b42318", warning="#8a5b11", terminal="#f3f6fa",
                  terminal_fg="#245c45", output="#f3f6fa", output_fg="#243247"),
}
CARD_COLORS = {
    "dark": {"success": ("#10372a", "#8bf0ad"), "error": ("#481f26", "#ff9ca8"),
             "warning": ("#40361f", "#ffe09a"), "idle": ("#27313e", "#f2f4f7")},
    "light": {"success": ("#e5f3e9", "#18733a"), "error": ("#fbe8eb", "#b42318"),
              "warning": ("#fff3d9", "#8a5b11"), "idle": ("#e9eef5", "#243247")},
}
_ROLES = {"#245cc4": "accent", "#b42318": "error", "#245c45": "success", "#18733a": "success",
          "#9a6700": "warning", "#7a5a18": "warning", "#8a5b11": "warning", "#667085": "muted",
          "#245c6b": "accent", "#c0d6cd": "muted", "#eef2f6": "fg", "#f2f4f7": "fg",
          "#79e69d": "success", "#ff8181": "error"}
for _colors in PALETTES.values():
    for _role in ("accent", "error", "success", "warning", "muted", "fg"):
        _ROLES.setdefault(_colors[_role], _role)  # 允许已打开窗口反复切换，不丢失状态语义。


def palette(widget):
    return getattr(widget._root(), "_watcher_palette", PALETTES["dark"])


def color(widget, value):
    return palette(widget).get(_ROLES.get(str(value).lower(), value), value)


def card_colors(widget, level):
    return CARD_COLORS[getattr(widget._root(), "_watcher_theme", "dark")][level]


class Label(ttk.Label):
    def __init__(self, master=None, **kwargs):
        self._semantic_foreground = kwargs.get("foreground", "")  # 保存原语义色，运行中更新仍随当前主题转换。
        if self._semantic_foreground:
            kwargs["foreground"] = color(master, self._semantic_foreground)
        super().__init__(master, **kwargs)

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = dict(cnf, **kwargs)
            cnf = None
        if "foreground" in kwargs:
            self._semantic_foreground = kwargs["foreground"]
            kwargs["foreground"] = color(self, self._semantic_foreground)
        return super().configure(cnf, **kwargs)

    config = configure


class ThemeManager:
    def __init__(self, root, path):
        self.root, self.path = root, path
        try:
            saved = read_settings(path) if settings_source(path).exists() else {}
            choice = (saved.get("ui") or {}).get("theme", "dark")
        except (OSError, ValueError, AttributeError):
            choice = "dark"  # 损坏配置不在启动时自动重写。
        self.name = choice if isinstance(choice, str) and choice in PALETTES else "dark"
        root._watcher_theme, root._watcher_palette = self.name, PALETTES[self.name]
        root.bind_all("<Map>", self._mapped, add="+")  # 只在窗口打开或主动切主题时处理，无定时遍历。

    def save(self, name):
        if name not in PALETTES:
            raise ValueError("主题须为dark或light")
        saved = read_settings(self.path) if settings_source(self.path).exists() else {}
        if not isinstance(saved, dict) or not isinstance(saved.get("ui", {}), dict):
            raise ValueError("个人偏好格式无效，请先修复YAML；未覆盖原文件")
        saved["ui"] = dict(saved.get("ui", {}), theme=name)  # 合并最新磁盘配置，保留监控/认证和未知字段。
        write_settings(self.path, saved)
        self.apply(name)

    def _mapped(self, event):
        if isinstance(event.widget, (tk.Toplevel, tk.Text)):
            event.widget.after_idle(lambda widget=event.widget: self.apply_tree(widget))

    def apply(self, name=None):
        self.name = name or self.name
        p = PALETTES[self.name]
        self.root._watcher_theme, self.root._watcher_palette = self.name, p
        style = ttk.Style(self.root)
        style.configure(".", background=p["bg"], foreground=p["fg"], bordercolor=p["border"],
                        lightcolor=p["surface"], darkcolor=p["border"], troughcolor=p["bg"], focuscolor=p["accent"])
        for kind in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label", "TCheckbutton", "TRadiobutton", "TPanedwindow"):
            style.configure(kind, background=p["bg"], foreground=p["fg"])
        for kind in ("TButton", "TCheckbutton", "TRadiobutton"):
            style.configure(kind, background=p["surface"] if kind == "TButton" else p["bg"], foreground=p["fg"])
            style.map(kind, background=[("active", p["selected"])], foreground=[("disabled", p["muted"])])
        style.configure("TRadiobutton", indicatorbackground=p["field"], indicatorforeground=p["accent"])
        for kind in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(kind, fieldbackground=p["field"], foreground=p["fg"], insertcolor=p["fg"],
                            background=p["surface"], arrowcolor=p["fg"], selectbackground=p["selected"], selectforeground=p["selected_fg"])
            style.map(kind, fieldbackground=[("readonly", p["field"]), ("disabled", p["surface"])],
                      foreground=[("disabled", p["muted"]), ("readonly", p["fg"])], arrowcolor=[("disabled", p["muted"])])
        style.configure("TNotebook", background=p["bg"])
        style.configure("TNotebook.Tab", background=p["surface"], foreground=p["muted"])
        style.map("TNotebook.Tab", background=[("selected", p["field"]), ("active", p["selected"])], foreground=[("selected", p["fg"])])
        style.configure("Treeview", background=p["field"], fieldbackground=p["field"], foreground=p["fg"])
        style.map("Treeview", background=[("selected", p["selected"])], foreground=[("selected", p["selected_fg"])])
        style.configure("Treeview.Heading", background=p["surface"], foreground=p["fg"])
        style.map("Treeview.Heading", background=[("active", p["selected"])])
        for kind in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(kind, background=p["surface"], arrowcolor=p["fg"], troughcolor=p["bg"])
            style.map(kind, background=[("active", p["selected"]), ("pressed", p["selected"])])
        style.configure("TProgressbar", background=p["accent"], troughcolor=p["field"], bordercolor=p["border"])
        style.configure("Memory.Horizontal.TProgressbar", background=p["success"], troughcolor=p["field"])
        style.configure("Error.Horizontal.TProgressbar", background=p["error"], troughcolor=p["field"])
        for selected, indicator in enumerate(getattr(self.root, "_watcher_check_images", ())):
            indicator.put(p["field"], to=(1, 1, 15, 15))
            for border in ((1, 1, 15, 2), (1, 14, 15, 15), (1, 1, 2, 15), (14, 1, 15, 15)):
                indicator.put(p["border"], to=border)
            if selected:
                for x, y in ((4, 8), (5, 9), (6, 10), (7, 11), (8, 10), (9, 9), (10, 8), (11, 7), (12, 6), (12, 5)):
                    indicator.put(p["accent"], to=(x, y, x + 2, y + 2))  # 复用已有✓图像，切主题不退回X。
        for level, (background, _) in CARD_COLORS[self.name].items():
            style.configure(f"Card.{level}.TFrame", background=background)
        for key, value in (("background", p["field"]), ("foreground", p["fg"]),
                           ("selectBackground", p["selected"]), ("selectForeground", p["selected_fg"])):
            self.root.option_add("*TCombobox*Listbox." + key, value)
        self.apply_tree(self.root)

    def apply_tree(self, widget):
        try:
            if not widget.winfo_exists():
                return
            p = palette(widget)
            if isinstance(widget, (tk.Tk, tk.Toplevel)):
                widget.configure(background=p["bg"])
            elif isinstance(widget, tk.Text):
                if isinstance(widget, ScrolledText) and not isinstance(widget.vbar, ttk.Scrollbar):
                    old = widget.vbar
                    options = {key: value for key, value in old.pack_info().items() if key != "in"}
                    widget.vbar = ttk.Scrollbar(widget.frame, orient="vertical", command=widget.yview)
                    widget.vbar.pack(before=widget._w, **options)
                    widget.configure(yscrollcommand=widget.vbar.set)
                    old.destroy()  # 原生滚动条不随clam配色，替换一次并保留正常滚动行为。
                role = getattr(widget, "_theme_role", "field")
                widget.configure(background=p[role], foreground=p.get(role + "_fg", p["fg"]), insertbackground=p["fg"],
                    selectbackground=p["selected"], selectforeground=p["selected_fg"], highlightbackground=p["border"])
                for tag in widget.tag_names():
                    value = widget.tag_cget(tag, "foreground")
                    if value:
                        widget.tag_configure(tag, foreground=color(widget, value))
            elif isinstance(widget, tk.Canvas):
                widget.configure(background=p["bg"], highlightbackground=p["border"])
            elif isinstance(widget, (tk.Frame, tk.Label)):
                level = getattr(widget, "_theme_card", getattr(widget.master, "_theme_card", None))
                background = card_colors(widget, level)[0] if level else p["bg"]
                widget.configure(background=background)
                if isinstance(widget, tk.Label):
                    widget.configure(foreground=color(widget, widget.cget("foreground")))
                if level and isinstance(widget, tk.Frame):
                    widget.configure(highlightbackground=card_colors(widget, level)[1])
            elif isinstance(widget, Label):
                widget.configure(foreground=widget._semantic_foreground)
            elif isinstance(widget, ttk.Treeview):
                for tag in widget.tk.call(widget, "tag", "names"):  # Treeview的标签枚举由Tcl提供。
                    values = widget.tag_configure(tag)
                    if values.get("foreground"):
                        widget.tag_configure(tag, foreground=color(widget, values["foreground"]))
                    if values.get("background"):
                        widget.tag_configure(tag, background=p["surface"])
            elif isinstance(widget, ttk.Combobox):
                dropdown = widget.tk.call("ttk::combobox::PopdownWindow", str(widget))
                widget.tk.call(str(dropdown) + ".f.l", "configure", "-background", p["field"], "-foreground", p["fg"],
                               "-selectbackground", p["selected"], "-selectforeground", p["selected_fg"])
            for child in widget.winfo_children():
                self.apply_tree(child)
        except tk.TclError:
            return  # 新窗口在idle回调前关闭时，不再访问已销毁控件。
