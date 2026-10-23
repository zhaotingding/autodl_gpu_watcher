# 窗口、Shell 与 EXE 图标

仅在导入图标、修改转换/加载/资源更新或诊断显示问题时读取。2026-10-19 按 Conda 源码与轻量 EXE 设计更新；新入口的显示须单独验收，历史实测保持原日期与范围。归档位置按需见[历史证据定位](../../autodl-maintenance/references/windows-and-data.md#历史证据定位)。

## 图标的四个环节

- 分清原文件质量、导入转换、框架加载与系统最终显示。用户 ICO 原样保留全部作者帧和小尺寸专用帧；PNG 的 16、20、24、32、40、48、64、128、256 等需要尺寸直接从原图生成，保持比例和透明度，不连续缩小。
- Tk 窗口、任务栏、Shell 快捷方式与磁盘 EXE 是不同来源；保存成功、API 无报错或尺寸正确不能证明最终显示正确。首次启动和运行中更换分别检查；解码 ICO 各帧后用 `ImageTk.PhotoImage` 与 `root.iconphoto(False, *frames)` 设置窗口，管理 PhotoImage 生命周期。
- Windows `WM_GETICON` 为空时继续查窗口类 `GCLP_HICONSM/HICON`，不能直接判加载失败；必要时比对图标句柄对应像素与实际截图。按用户实际入口与 DPI 验证，不把一次窗口截图当作所有 Shell 入口成功。

## Conda 源码启动后的目标与生命周期

- 当前 GUI 的 `sys.executable` 是项目 Conda Python，不是主程序 EXE。EXE 图标更新只允许针对 `AUTODL_LAUNCHER_EXE` 标识的实际轻量启动器；不得改写 `python.exe`、`pythonw.exe`，也不能猜测根目录旧 `AutoDLWatcher.exe` 就是当前入口。
- 直接源码启动、没有实际启动器标识时，仍可设置窗口图标并保存偏好；不能因此修改任意 EXE。导出的启动快捷方式须保存当前专用 Conda 环境与管理器选择（`--env`、`--conda`），不能在下次双击时落到 base/全局 Python。
- 窗口先热更新；EXE 同步任务通过同环境的 `python -s -m autodl_watcher.exe_icon` 运行，接收明确 GUI PID 与 EXE 路径，沿用同一资料根，等待 GUI 退出后再改资源。不能再依赖 `_internal/AutoDLWorker.exe`；具体环境检查见[安装与启动参考](packaging.md)。
- 修改目标副本的 `RT_ICON/RT_GROUP_ICON` 时保留并在新节区末尾重附 PyInstaller 归档，否则单文件启动器可能失效。上次 EXE 备份为资料根下 `.ui/icon-executable-backup.exe`；资源更新后通知 Windows，要求重开或刷新时说明实际生效路径，不删除系统缓存或任务栏固定项。
- 公开干净启动器使用默认图标；本机已配置图标及导入副本保留，旧副本可能仍被已有快捷方式引用。EXE 替换与图标同步并行时要先确认本地任务退出，不能用新构建覆盖正在等待写入的文件。

## 针对实际入口的验证

按改动使用 `test_app_icon.py`、`test_v090.py` 并核对真实窗口/图标；测试需要 EXE 时选择本轮暂存轻量启动器或显式 `AUTODL_TEST_LAUNCHER`，不要用历史完整冻结 EXE 的成功结果替代。

分别记录原件帧、转换副本、Tk 首次与热更新、窗口/任务栏、用户快捷方式及磁盘 EXE 的实际结果。EXE 修改后除核查资源像素，还须再次启动并确认归档可读、同一 Conda 环境生效；没有测到的 DPI、入口或缓存刷新结果明确标为未验。API 成功、单元测试或 Skill 文件写入成功都不能替代这些显示和启动结果。

## 历史证据

- 2026-10-07 v0.8.9 实际 16/32 帧及首次/热更换验证在 `icons-development/`。当时“运行时不改写 EXE”的限制从 v0.9.0 起已被安全退出后同步流程替代。
- 2026-10-10 旧冻结 worker 更新/重开、Shell 与窗口像素记录为 `v090/hot-report.json`、`helper-shell-report.json`、`reset-report.json`、`explorer-final.png`。
- v0.9.9 本机 EXE 沿用个人图标且归档不变见同级 `2026-10-15-v099/local-install.yaml`。这些历史结果不证明 Conda 源码/轻量启动器已完成同等验收，也不能从当时某 DPI 成功推断其他 DPI 均正确。

2026-10-19 新入口验证：21 项图标相关测试无跳过通过；Tk 首次/热换和 LNK/Shell 像素、同环境模块 helper、资源更新后 CArchive 内容与再次启动均已实际验证。最终根入口的 Shell 16/32 像素与旧配置成品一致。半透明原 ICO 与 Win32 DIB 可能有细微通道表示差异，不能仅据原始 RGBA 不完全相等判定选错帧；本例同时比较实际旧/新 Shell 句柄像素。证据与边界见[本轮验收](../../../../docs/重构验收记录.md#2026-10-19项目专用-conda-与源码启动迁移)。
