# Auto算力与训练调度

在一个桌面窗口中查看 GPU 资源、监控自己的训练进度，并按需使用 AutoDL 自动开机和 AI 日志解析。
- **训练监控**：通过 SSH 读取进程、GPU、训练日志和指标，可独立使用，不要求 AutoDL 账号。
- **AutoDL 监控**：查看平台资源及实例状态；自动开机需要另行配置并明确开启。
- **AI 专用规则**：可选地分析日志并生成解析规则；日常采样在本地执行规则，不必每轮调用 AI。

本项目面向开发者，使用 **源码 + 一个独立 Conda 环境 `auto-gpu`**。EXE 是可选启动入口，不是免安装的完整应用。

[首次安装](#首次安装) · [日常启动](#日常启动) · [首次使用](#首次使用) · [更新项目](#更新项目) · [恢复个人设置](#恢复个人设置) · [手动验收](#手动验收) · [常见问题](#常见问题) · [更多文档](#更多文档)

已有有效环境和个人设置时，直接看[更新项目](#更新项目)和[路线 B](#b升级保留)，不要重新创建环境。

以下命令使用 PowerShell。除另有说明，执行目录为**含 `pyproject.toml` 的仓库根目录**，使用**普通权限**。任一命令失败先停止后续步骤，按[排错信息](#排错信息)提供命令、目录、退出码和完整错误；不要改用 base、全局 Python、`pip --user` 或另建 venv。

## 首次安装

按顺序完成一次即可。已有仓库或已有有效环境时，跳过对应的创建步骤，不需要删除后重建。

### 1. 准备工具和源码
主要桌面验证环境为 Windows 11 / Python 3.11。Python 声明支持 3.10 及以上；其他系统的完整桌面、凭据和图标功能尚未完成实机验收。

| 工具 | 什么时候需要 |
| --- | --- |
| Conda（Anaconda 或 Miniconda） | 必需；用于创建项目 Python 环境 |
| Git | 克隆仓库、更新源码和参与协作时需要 |
| Microsoft Edge | 使用 AutoDL 平台登录时需要 |
| Windows OpenSSH 客户端及自己的 SSH 密钥 | 使用训练监控时需要 |

任意目录、普通 PowerShell 中检查：

```powershell
conda --version
git --version
```
两条都应输出版本号。如果提示找不到 `conda`，先看[常见问题](#常见问题)，不要继续运行安装命令。


没有源码时，在自己可写的开发目录中、用普通 PowerShell 克隆：
```powershell
git clone https://github.com/zhaotingding/autodl_gpu_watcher.git
Set-Location .\autodl_gpu_watcher
Test-Path .\pyproject.toml
```
已有源码时直接进入现有仓库，不必再次克隆。最后一条应返回 `True`。

### 2. 确认或创建项目环境

先确定 **Conda 管理器、环境名和目标绝对路径**。下面默认名称为 `auto-gpu`；使用别名时，后续所有 `-n` 和 EXE 的 `--env` 都保持一致。在仓库目录、普通 PowerShell 中查看：

```powershell
conda env list
conda config --show envs_dirs
```

预期能找到已有环境的实际路径，以及 Conda 的环境目录设置。已有 `auto-gpu` 且路径正是要保留的位置时直接复用；仅名称相同、路径不同不能视为同一环境。多套 Conda 并存时，用自己选定的 `conda.exe` 完整路径调用，见[常见问题](#常见问题)。

**只有缺少该环境时才创建。** 目标目录可写时用普通 PowerShell；若选择管理员拥有且普通用户不可写的目录，创建需要管理员权限。先确定目标路径，再执行：

```powershell
conda create -n auto-gpu python=3.11 pip
```

在输入确认前核对 Conda 显示的 `environment location` 必须等于选定路径；不一致时取消，先调整环境目录选择，不接受偶然默认位置。完成后再运行 `conda env list`，核对名称和路径。

### 3. 安装项目依赖
**目录：仓库根目录。权限：需要环境写权限；普通用户可写时不需要管理员。** 管理员拥有、普通用户不可写的环境，安装或更新才需管理员 PowerShell；重新打开窗口后要再次进入仓库。执行前由你正常关闭使用该环境的应用，不覆盖运行中的依赖。

```powershell
conda run -n auto-gpu --no-capture-output python -s -m pip install --no-user -e .
```
这些参数的含义：
| 参数 | 含义 |
| --- | --- |
| `conda run -n auto-gpu` | 使用指定的项目环境，不受提示符当前显示哪个环境影响 |
| `--no-capture-output` | 实时显示输出 |
| `python -s` | 不加载 Python 用户级 site-packages |
| `pip install --no-user` | 不向用户级 site-packages 安装依赖 |
| `-e .` | 将当前源码目录关联到环境；以后修改业务源码，重开程序即可生效 |
依赖统一声明在 `pyproject.toml`，无需另找 `requirements.txt` 或手动逐个安装包。
等待命令返回提示符，随后检查上一条命令的退出码：
```powershell
$LASTEXITCODE
```
应为 `0`。**不能仅凭中间出现 `Successfully built` 判断安装成功**，后面的安装或卸载环节仍可能报错。出现 `WinError 5 / 拒绝访问` 时，按下方排错步骤处理，不要加 `--user`。

### 4. 检查安装是否可用
**窗口权限：普通 PowerShell。** 仍在仓库目录执行；每条成功后再执行下一条：
```powershell
conda run -n auto-gpu --no-capture-output python -s -m pip check
conda run -n auto-gpu --no-capture-output python -s -m autodl_watcher --check-environment
conda run -n auto-gpu --no-capture-output python -s -m autodl_watcher --version
```
预期结果：

- `pip check` 输出 `No broken requirements found.`。
- 环境检查返回 `environment: auto-gpu`；`python` 和 `prefix` 是你确认的环境解释器与前缀，实际第三方模块路径也在该环境中，源码位置指向当前仓库，用户 site 未启用。仅看到包的安装元数据位置或提示符环境名，不足以证明实际来源正确。
- 版本命令输出当前源码版本。版本以此结果为准，不以旧截图或旧 EXE 文件名判断。

## 日常启动
**已完成安装后，直接使用源码命令，或双击仓库根目录的新版轻量 `AutoDLWatcher.exe`。** 日常运行用普通权限；应用需要能写入自己的 `.ui/` 和 `runtime/`。

### 源码启动

仓库根目录、普通 PowerShell：

```powershell
conda run -n auto-gpu --no-capture-output python -s -m autodl_watcher
```
看到“Auto算力与训练调度”窗口即表示界面已启动；这还不代表平台登录、SSH 连接或监控任务已经成功。
### 双击主程序 EXE

启动器默认选 `auto-gpu`，不继承终端里碰巧激活的其他项目环境。双击还要求 Windows 能通过 `CONDA_EXE` 或 `PATH` 找到所选 Conda 管理器；仅在某个终端里运行过 `conda activate`，不能证明资源管理器也找得到它。

仓库根目录、普通 PowerShell 的明确环境启动命令：

```powershell
conda run -n auto-gpu --no-capture-output .\AutoDLWatcher.exe
```

- Git 克隆后没有 EXE 是正常情况，直接使用源码命令即可。需要 EXE 时见[开发与协作中的构建说明](docs/开发与协作.md#验证与可选构建)。
- EXE 仍然需要源码、Conda 环境和已安装的项目依赖，不能单独复制到别处当完整应用运行。
- 自定义环境须给 EXE 明确传入 `--env 环境名`，必要时用 `--conda "conda.exe完整路径"` 选管理器；参数优先于继承值。找不到管理器、环境或依赖会报错，不会回退到全局 Python。双击报错时保存完整提示，并用源码命令区分启动器问题与环境问题。
- 业务源码变化后重开程序即可；启动器本身或嵌入图标变化时才需重建 EXE。更新仓库不会自动更新已有 EXE；构建先输出暂存产物，验证后再替换并保留旧成品。

## 首次使用

第一次打开、尚未配置服务器时，列表为空或显示“尚未配置”是正常现象。无需复制维护者的个人设置。

| 你的需求 | 下一步 |
| --- | --- |
| 只看远端训练，不使用 AutoDL | 先确认终端 `ssh 你的别名` 能用密钥登录，再进入“全局设置 → 服务器连接”，添加连接并开始训练监控 |
| 查看 AutoDL 资源与实例 | 配置自己的平台及 Telemetry 信息，完成平台登录，再以仅监控模式启动 |
| 需要自动开机 | 先核对自己的实例映射及资源，再明确开启“启用真实自动开机”；新配置默认关闭 |
| 需要 AI 解析训练日志 | 在“全局设置 → AI规则与消费”配置自己的接口和密钥；手动调用或另行开启自动生成规则 |

**仅监控时不要勾选自动开机和关闭无卡实例。** 新配置默认不执行电源操作，自动 AI 也默认关闭；已有个人设置会被恢复，使用前检查自己的勾选状态。

### 哪些文件是你的个人设置

| 文件或目录 | 用途 |
| --- | --- |
| `config.yaml` | 仓库提供的公开安全示例 |
| `config.local.yaml` | 自己的配置，优先于 `config.yaml`；两者不逐字段合并 |
| `.ui/` | 服务器连接、界面偏好、AI 规则与用量、图标 |
| `runtime/` | 登录会话、运行日志和采样状态 |
| `.env` | 可选的本机环境变量，例如邮件配置 |

需要编辑平台参数时，在仓库目录、普通 PowerShell 中创建个人配置；下面的命令不会覆盖已有文件：

```powershell
if (-not (Test-Path .\config.local.yaml)) {
    Copy-Item .\config.yaml .\config.local.yaml
}
```

预期 `config.local.yaml` 存在，之后只编辑自己的文件；若复制失败，检查仓库写权限。GUI 保存的部分监控偏好位于 `.ui/preferences.yaml`，会覆盖 GUI 中的对应配置；CLI 直接使用 YAML 和命令参数，不自动采用这些 GUI 偏好。

这些个人文件不能随意清理，也不要提交到公共仓库。API 密钥由 Windows 凭据管理器保存；应用不保存 SSH 密码，SSH 密钥由你自己的 SSH 配置管理。默认资料根是当前源码仓库；已有显式 `AUTODL_APP_ROOT` 时沿用该资料根，不因更新源码改动它。

## 更新项目

环境和个人设置原位保留。先按下一节核对备份范围，由你在合适时机正常关闭应用；随后在仓库根目录、普通 PowerShell 中检查本地改动：

```powershell
git status --short
```

预期输出能说明当前改动；有自己的未提交修改时，先按自己的协作流程处理，不要强制覆盖。确认可以更新后，在同一目录和窗口执行：

```powershell
git pull --ff-only
```

预期为快进更新或已是最新；若提示分叉、冲突或会覆盖文件，停止并保留完整 Git 输出，不执行强制重置。

| 更新内容 | 接下来做什么 | 权限 |
| --- | --- | --- |
| 只有普通业务源码变化 | 重开应用 | 普通 PowerShell |
| 依赖或项目安装配置变化 | 重新执行下方安装命令，再检查环境 | 需要环境写权限 |
| 启动器实现或嵌入图标变化 | 按构建说明生成并验证新 EXE | 安装构建依赖时按环境权限；构建本身通常普通窗口即可 |
| 移动了整个仓库目录 | 在新目录重新执行 editable 安装，使环境指向新源码 | 需要环境写权限 |

需要更新安装时，**在具有环境写权限的 PowerShell 中、从当前仓库根目录执行**：

```powershell
conda run -n auto-gpu --no-capture-output python -s -m pip install --no-user -e .
```

退出码为 `0` 后，重新执行[安装检查](#4-检查安装是否可用)，再回普通窗口启动。更新不要求删除环境、个人配置、登录资料或规则。

## 恢复个人设置

同一电脑、同一用户原位升级通常不需要恢复操作，保留这些文件即可。需要备份或恢复时，由你正常退出应用及其专用登录窗口后操作；正在写入的 SQLite 数据库或浏览器资料不能仅凭复制成功当作一致备份。

| 要保留的实际文件 | 内容 |
| --- | --- |
| `config.local.yaml`、可选 `.env` | 平台、账号、路径和本机变量 |
| `.ui/preferences.yaml`、`.ui/servers.yaml` | 监控偏好、电源开关及服务器连接 |
| `.ui/ai.yaml`、`.ui/training_rules.yaml` | AI 设置、自动生成开关、规则及任务关联；保留关联可避免把旧任务当作新任务 |
| `.ui/ai_usage.sqlite3` | Token 与估计费用历史 |
| `.ui/icon.yaml`、`.ui/icons/` | 图标选择与资源 |
| `runtime/browser_profile/`、`runtime/native_login_profile/` | 平台登录资料 |
| `runtime/logs/`、`runtime/state.json` | 历史日志与监控状态 |

**恢复前先确认自动行为：** 旧 `.ui/preferences.yaml` 可能恢复真实开机或关闭无卡实例；旧 YAML 也可能启用自动开机，CLI 不受 GUI 关闭开关保护。旧 `.ui/ai.yaml` 可能启用自动生成规则，在开始训练监控并发现新任务后发送日志并产生费用。初次恢复验收先由你确认仅监控、关闭自动 AI；已配置平台的界面启动也可能执行只读会话核验。

换电脑、Windows 用户或源码目录时，另行核对：

- Windows 凭据管理器中的 API 密钥不能仅靠复制 `.ui/` 恢复；更换 API 基础地址也可能需要重新保存对应密钥。
- 浏览器会话受本机用户和加密机制影响；恢复后需实际核验，失效则重新登录。
- SSH 私钥、SSH 配置和配置中的绝对路径需在目标机器有效；相对路径以所选配置文件所在目录为准。
- 源码目录移动后重新建立 editable 安装关联；不要剪切 Conda 环境目录代替环境迁移。

## 手动验收

### A：干净安装

另选源码目录和一个新环境的名称、路径，不复制旧个人配置、会话、密钥或规则。依次完成首次安装和环境检查，先确认未配置界面能打开，再自行配置所需功能。验收本地尚未推送的修改时，可从本地仓库克隆包含该修改的提交；远程旧提交不能代表本次修改。

### B：升级保留

复用已确认的环境和设置，按“更新项目”操作。检查更新前后的环境前缀、源码指向一致，服务器、间隔、电源开关、用户规则和费用历史仍在。无依赖变化且源码目录未移动时，不重复安装。先核对恢复的自动行为，再由你启动监控。

两条路线分别记录以下结果，不相互替代：

| 层次 | 成功证据 |
| --- | --- |
| 窗口打开 | 源码入口打开主窗口；需要 EXE 时再独立验证双击 |
| 后台就绪 | 启动所需监控后，本地 worker 正常启动、无环境错误；暂未连接服务器不算连接成功 |
| 连接成功 | 平台接口或 SSH 实际连通，登录资料仅保存成功不算此项通过 |
| 业务采集成功 | 新鲜 GPU、实例或训练采样到达；旧缓存和静态界面不能代替 |

默认验收不要求开关机或付费 AI；这些操作仍由你单独决定。Agent 的静态检查、模拟测试以及历史验收记录，与上述用户手动结果分别报告。

## 常见问题

| 现象 | 原因或检查方向 | 怎样处理 |
| --- | --- | --- |
| `WinError 5`、`拒绝访问`，路径在 `envs\auto-gpu\Lib\site-packages` | 可能是环境写权限、文件占用、只读属性或安全软件限制 | 先保留具体报错路径，正常退出相关应用并核实权限；确认是管理员目录权限时再用管理员 PowerShell 重试。不要加 `--user` |
| `EnvironmentNotWritableError` | Conda 目标环境目录不可写 | 同样检查环境路径和窗口权限；不要安装到 base 或另建 `.venv` 绕过 |
| 管理员窗口仍然报拒绝访问 | 可能仍有文件占用，或目录权限/安全软件另有限制 | 先确认没有使用该环境的应用，保留完整错误路径继续定位；不要反复卸载全部依赖或盲目修改整个磁盘权限 |
| `No module named autodl_watcher` | 环境里未安装本项目，或 editable 仍指向旧目录 | 在正确仓库目录、按所需权限重新安装，再运行 `--check-environment` |
| 找不到 `conda` | 当前 PowerShell 没有识别 Conda 命令 | 使用安装器提供的 Anaconda/Miniconda PowerShell 入口；或者找到自己的 `conda.exe`，按下方说明调用 |
| `EnvironmentNameNotFound` | 当前 Conda 管理器找不到 `auto-gpu` | 运行 `conda env list`；若有多套 Conda，确认用的是同一套。环境确实缺失才创建 |
| 提示当前目录不是 Python 项目、没有 `pyproject.toml` | 新开的管理员窗口没有进入仓库 | 执行 `Set-Location`，用 `Test-Path .\pyproject.toml` 确认返回 `True` |
| 没有 EXE，或双击报缺少环境/依赖 | 克隆不带 EXE，或本机尚未完成安装 | 没有 EXE 可直接运行源码；已有新版 EXE 会默认使用 `auto-gpu`，按具体错误修复安装即可 |
| 应用打开了，但没有主机或训练 | 尚未配置平台/SSH，或本轮未发现匹配进程 | 按[首次使用](#首次使用)检查所需配置，再看连接及采集日志；这与本地依赖是否安装成功是不同问题 |

如果需要通过完整路径调用 Conda，PowerShell 必须在带引号的程序路径前加 `&`。在仓库根目录、普通 PowerShell 中执行；以下仅为写法示例，先将路径替换为自己已选定的管理器位置。预期输出与安装检查相同：

```powershell
& "C:\Tools\Miniconda3\Scripts\conda.exe" run -n auto-gpu --no-capture-output python -s -m autodl_watcher --check-environment
```

### 排错信息

每组命令失败时请提供：**完整命令、执行目录、窗口是否为管理员、退出码、所选 Conda 管理器及 `conda env list` 中的环境路径、完整报错文本**。启动失败另附 `.ui/startup.log` 中对应错误；不要只截取最后一行，也不要提供密钥或私有日志正文。

## 更多文档

| 文档 | 什么时候看 |
| --- | --- |
| [开发与协作](docs/开发与协作.md) | 调试、离线测试、构建轻量 EXE、wheel 和贡献流程 |
| [项目文件与归档](docs/项目文件与归档.md) | 判断哪些文件能清理，理解历史成品与个人资料 |
| [邮件通知配置](docs/邮件通知配置.md) | 配置邮件通知 |
| [变更记录](CHANGELOG.md) | 查看版本变化 |
| [验收记录](docs/重构验收记录.md) | 查看各次验证的日期、范围与尚未验证项 |
| [项目约定与 Skill 索引](AGENTS.md) | 使用编码代理维护本项目 |

本页说明当前安装与操作流程。旧版完整冻结程序、`_internal`、旧环境名和特定机器的历史处置，不再作为新用户的安装步骤。
