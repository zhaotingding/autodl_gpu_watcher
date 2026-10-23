# Conda 安装与轻量 Windows 启动器

安装、构建、启动入口或后台环境问题时读取。2026-10-19 确认的新设计以专用 Conda 环境运行源码；旧 venv 和完整冻结成品流程仅作为历史证据保留。新链已于同日完成 Windows 11 / Python 3.11 专用 Conda、源码/轻量 EXE/worker、wheel 轮替安装及 440 项离线回归；此结果不代表其他平台或云端 CI，实际执行结果见[重构验收记录](../../../../docs/重构验收记录.md)。

## 唯一项目环境与依赖来源

- 依赖唯一来源为 `pyproject.toml`，构建 extra 保留明确固定版本，包版本动态读取源码 `__version__`。不复制已安装环境，不另维护 requirements，也不把作者的 Conda 名称、绝对路径或历史归档变成协作者条件。
- 2026-10-20 用户确认默认项目环境名为 `auto-gpu`；文档与 CI 使用该名称，显式选择其他专用环境时须一致替换。先查看 `conda env list`，有效环境直接复用；只在缺失或迁移确有必要时创建。禁止 base、全局 Python、用户 site 及叠加 venv/.venv。
- Conda 环境可以位于用户可写目录；这与 `pip --user` 的用户 site 安装不同。环境目录不可写时先核实真实权限，再选择可写的 Conda 环境位置；不因安装失败改用 `--user`、全局 Python 或修改 base。
- 开发者自行准备 Conda，项目不提供安装 EXE、CMD/BAT 包装或自动安装依赖的启动器。命令统一在 PowerShell 中执行；已有命令足够时不增加 PS1。

文档中的安装命令前必须说明权限条件、执行目录及成功判据。2026-10-20 已确认管理员拥有的环境更新 editable `.pth` 时，普通窗口可报 `WinError 5`；创建/安装/卸载需要环境写权限，必要时以管理员身份打开 PowerShell 并重新进入仓库。提示符 `(base)` 不代表管理员权限，显式 `conda run -n auto-gpu` 仍选择项目环境。正常运行应用不应一概要求管理员权限；不可建议 `--user` 绕过。

仅在专用环境不存在时创建，然后在仓库根安装；环境不可写时使用管理员 PowerShell，安装返回非 0 则停止后续启动：

```powershell
conda create -n auto-gpu python=3.11 pip
conda run -n auto-gpu --no-capture-output python -s -m pip install --no-user -e .
conda run -n auto-gpu --no-capture-output python -s -m pip check
conda run -n auto-gpu --no-capture-output python -s -m autodl_watcher --check-environment
conda run -n auto-gpu --no-capture-output python -s -m autodl_watcher
```

`-s` 禁用用户 site，`--no-user` 防止 pip 写入用户 site；两者都不能单独证明环境隔离。核对 `sys.executable`、`sys.prefix`、`CONDA_PREFIX`、项目源码位置及第三方 distribution/module 来源，清除继承的 `PYTHONHOME` 和无关 `PYTHONPATH`。业务包在 editable 安装时来自当前源码，第三方依赖须来自选定 Conda 环境；不能借用另一个项目或用户目录中的包。检查环境必须不发平台/SSH/AI 请求。

回归入口仍使用同一个环境：

```powershell
conda run -n auto-gpu --no-capture-output python -s -m unittest discover -s tests -q
```

wheel 兼容检查仅在包安装/资源改动需要时做：在同一项目环境中临时替换 editable 为本轮 wheel，从项目外目录检查实际加载位置，完成后恢复 editable；不为此叠加 venv 或保留第二套运行环境。具体 PowerShell 操作见[开发与协作](../../../../docs/开发与协作.md)。

## 启动器与后台入口

- `tools/launcher_entry.py` 仅使用标准库，选择明确的 Conda 环境并启动 `tools/gui_entry.py`。2026-10-20 修正环境继承：无 `--env` 时固定选择 `auto-gpu`，不继承 `CONDA_DEFAULT_ENV`；显式指定 base/root 仍拒绝。管理器优先级为 `--conda`、`CONDA_EXE`、`PATH` 中的 `conda.exe`；找不到时清楚报错，不把某个终端能用 Conda 当作 Explorer 已能定位。不支持 `--python`，不自动搜索其他 Python、回退 base 或调用旧冻结 worker。
- 主入口可直接双击；命令行形式 `conda run -n auto-gpu --no-capture-output .\AutoDLWatcher.exe` 仍支持。验收必须覆盖清除 Conda 激活变量、无参数 Windows Shell 打开 EXE；用外层 conda run 启动成功不能代替双击验收。环境缺失、依赖缺失、选择 base 或管理器不可用时给出安装指引，不能静默降级。启动器与源码须配套，EXE 本身不是可脱离仓库的完整应用。
- GUI 使用选定环境运行源码；内部 worker 复用当前 `sys.executable`（需要控制台解释器时取同目录 `python.exe`），沿用同一项目环境和资料根，不再冻结第二套 worker 或解析 PATH 中的 Python。它们不能继承无关 `PYTHONHOME`、用户 site 或旧项目 `PYTHONPATH`。
- `AUTODL_APP_ROOT` 仍决定显式资料根；源码默认资料根与代码根规则沿用现有路径服务。`AUTODL_LAUNCHER_EXE` 标识真正启动的 EXE，供图标任务使用，不能用 `sys.executable` 代替它。
- PyInstaller Windows 启动器调用外部 Conda/Python 前须撤销自身 `SetDllDirectoryW`，仅清理 PATH 中属于自身 `_MEIPASS` 的项；保留 Conda 和系统需要的 PATH。只改子进程环境不能撤销父进程设置的 DLL 搜索目录。参考 [PyInstaller 外部程序调用说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)。
- 系统 PowerShell 辅助调用继续用 `SystemRoot/System32/WindowsPowerShell/v1.0/powershell.exe`。验证精简 PATH 时限制实际父进程的继承环境，不能只给其中一次 `subprocess.run` 换 env 就宣称全链独立。

## 暂存构建、验收与替换

仅需要 EXE 时在同一个项目环境安装构建 extra：

```powershell
conda run -n auto-gpu --no-capture-output python -s -m pip install --no-user -e ".[build]"
conda run -n auto-gpu --no-capture-output python -s tools/build_exe.py --stage-only
```

当前构建产物只暂存到 `.tools/build/launcher/AutoDLWatcher.exe`；不自动覆盖根目录程序，不生成邻接 `_internal/`、业务依赖副本或安装包。单文件内部仍有 PyInstaller 启动代码所需的最小运行时，它不作为业务环境。不要把“无需邻接运行时目录”写成“EXE 内不含任何 Python 引导运行时”。普通业务源码修改重开即可生效；启动器实现或嵌入图标变化才需重建。

验收按本次变化选择有判断价值的检查：

1. 从无个人配置、会话或规则的源码副本验证安装与 `--check-environment`；第三方依赖来自所选环境，默认不启用电源操作或收费 AI。
2. 分别验证 PowerShell 源码入口、暂存 EXE 对应的真实 GUI 和同环境 worker；核对实际解释器、资料位置、停止与退出。模拟网络验证不能替代真实平台登录成功。
3. 验证明确环境选择、环境/依赖缺失时报错且无隐式回退。修改测试副本的源码后复用同一 EXE，确认运行的是新源码。构建归档中不得收集业务包、Pillow、Tk 或 Playwright 等业务依赖。
4. 正常关闭旧程序并确认相关本地进程退出后，才归档旧完整 EXE 与配套 `_internal/`、替换本地启动器；不覆盖 `config.local.yaml`、`.ui/`、`runtime/`、`.env` 或系统凭据。旧环境的清理须在新链验收之后，并核对真实位置、权限及是否在用；不确定或权限不足时保留并报告。

窗口出现、构建退出码为零或文件变小都不能单独证明整条链路正确。Windows 实测、其他操作系统和 CI 云端任务分别报告，不从文档或离线回归推断未执行的结果。

## 手动验收与升级保留

适用：用户亲自执行安装、启动与设置恢复。2026-10-20 确认采用 B 路线复用既有 `auto-gpu`，不重建环境；本机路径属于该用户的选择，不作为协作者默认路径。

- 先列出源码、管理器、环境名与前缀，得到用户选择后再编写对应命令。源码普通更新不要求安装；依赖/安装配置变化或 editable 源码位置改变才更新目标环境。
- 安装、启动由用户执行时，Agent 限于静态及临时目录/模拟接口回归。不得为满足上述真实启动验收而擅自停止应用、轮替 wheel、安装构建依赖或覆盖 EXE；未做的步骤标为待用户验证。
- `--check-environment` 分别检查依赖发行元数据和实际导入模块路径，输出 `user_site_enabled` 与 `module_paths`；`pip check` 另核对安装依赖约束。两者都不代表平台连接成功。
- A 使用无个人文件的独立检出与新环境；B 保留现有环境和资料。若只做本地提交，A 应检出该提交，不能拿远程旧提交代验。B 的凭据、会话及规则恢复条件见[设置和资料根](../../autodl-maintenance/references/windows-and-data.md#设置和资料根)。
- 回归证据入口：`test_conda_launcher.py`、`test_portable_startup.py`、`test_package_layout.py`；模拟环境和后台探针不能代替用户实际双击或真实业务验收。修改启动器代码后，未重建/替换的旧 EXE 不会自动获得修复。

## 已被替代的历史流程与有效证据

- 2026-10-13 发现部分有效依赖来自用户 site，复制 Conda prefix 会漏包；旧 distribution metadata 也不等于实际源码/EXE 版本。这个故障支持检查实际来源，不能推广为所有 Conda 环境都如此。
- 2026-10-14/15 的新 venv、wheel 和冻结 GUI/worker 验收属于当时设计；2026-10-19 已替换其安装/启动要求，不应照旧再建 venv。旧产物目录为 `.tools/build/release/AutoDLWatcher`，主 EXE 与 `_internal/AutoDLWorker.exe` 配套，含业务 Python/Tk/Playwright；旧构建收集 Conda `Library/bin` 的 libssl/libcrypto 也只属于该冻结流程。
- 历史证据：2026-09-28 精简 PATH 只读流程 `v0.7.0-path-repair/gui-live-verification.json`；2026-10-13 独立依赖归档 `2026-10-13-config-consolidation/verification.json`、`effective-dependencies.json`；2026-10-14/15 记录见[重构验收记录](../../../../docs/重构验收记录.md)。归档根按需查[历史证据定位](../../autodl-maintenance/references/windows-and-data.md#历史证据定位)。这些历史结果不代表新 Conda 启动链、其他操作系统或尚未执行的 CI 已通过。
