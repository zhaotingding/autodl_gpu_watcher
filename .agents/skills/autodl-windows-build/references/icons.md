# 窗口、Shell与EXE图标

仅在导入图标、修改图标转换/加载/资源更新或诊断显示问题时读取。适用v0.9.9；整理核对日期2026-10-15。历史实测保持原验证日期，其他DPI或当前入口的显示须另验。证据映射按需见[历史证据定位](../../autodl-maintenance/references/windows-and-data.md#历史证据定位)。

## 图标的四个环节

- 分清原文件、转换、框架加载与系统显示。ICO原样保留所有作者帧；PNG每个尺寸直接从原图生成并保留比例/透明度。
- 窗口/Tk、任务栏、Shell快捷方式与EXE资源来源不同，单改窗口或API成功不能证明其他位置已更新。核查首次启动和运行中换图，保留PhotoImage生命周期。
- Windows WM_GETICON为空时查窗口类GCLP_HICONSM/HICON，不能直接认定加载失败；必要时比对句柄对应像素与实际截图。
- v0.9.0起窗口先热更新，自有worker等待GUI退出后改EXE副本RT_ICON/RT_GROUP_ICON；必须保留并在新节区末尾重附PyInstaller归档，否则EXE可能不能启动。备份为`.ui/icon-executable-backup.exe`，通知Windows后刷新显示，不删除系统缓存或固定项。
- 旧v0.8.9“运行时不改写EXE”的限制已被上条流程替代。重新生成发行程序时，公开干净包不嵌个人图标；本机已配置图标和所有导入副本保留，旧副本可能仍被快捷方式引用。

证据：2026-10-07v0.8.9实际16/32帧及首次/热更换验证`icons-development/`；2026-10-10实际worker更新/重开、Shell和窗口像素`v090/hot-report.json`、helper-shell-report.json、reset-report.json、explorer-final.png。0.9.9本机EXE沿用个人图标且归档不变见同级`2026-10-15-v099/local-install.yaml`。回归test_app_icon.py、test_v090.py；不从历史某DPI成功推断其他DPI均正确。
