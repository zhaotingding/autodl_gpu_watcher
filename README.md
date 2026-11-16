# Auto算力与训练调度

在一个桌面窗口中查看 GPU 资源、监控自己的训练进度，并按需使用 AutoDL 自动开机和 AI 日志解析。
- **训练监控**：通过 SSH 读取进程、GPU、训练日志和指标，可独立使用，不要求 AutoDL 账号。
- **AutoDL 监控**：查看平台资源及实例状态；自动开机需要另行配置并明确开启。
- **AI 专用规则**：可选地分析日志并生成解析规则；日常采样在本地执行规则，不必每轮调用 AI。

日常使用通过 **Windows 安装包** 部署 Electron 界面及随包 Python 后台。源码开发继续使用独立 Conda 环境 `auto-gpu`。

## Windows安装版

V1.2.0 本地安装包位于 `installer/dist/release/1.2.0-project-local/autodl-setup-project-1.2.0-x64-unsigned.exe`，适用 Windows 10/11 x64。该产物为未签名本地验收版。安装目录可写时使用普通权限，目录受限时按实际权限处理。

1. 双击安装包，在目录页使用输入框或“浏览”选择程序目录。本机约定为 `D:/Dev/MCP/autodl_gpu_watcher_git/installed`。
2. 安装完成页默认勾选“打开Auto算力与训练调度并进行基础设置”，可调整主题、填写已有模型接口的密钥或跳过，密钥留空保留已有值。
3. 日常从开始菜单、桌面快捷方式或安装目录的 `autodl-desktop.exe` 打开。安装内容包含前后端运行时与依赖。

已有安装时目录页预填原路径，保留路径即可覆盖升级。已有本应用程序目录支持直接覆盖；安装登记缺失时会核验主程序和安装包信息，按原目录修复安装并重新登记。手动换到新建或空目录时，确认迁移后先移除旧程序，再安装到新位置。程序资料原地使用，迁移后检查开始菜单指向新地址。

已有源码用户从本仓库 `installer/` 下运行安装包时，可自动识别并沿用仓库中的 `.ui/`、`runtime/`、`config.local.yaml`。如需明确选择资料根，可给安装主程序传 `--autodl-data-root "已有资料根的绝对路径"`。新用户默认资料在 `%LOCALAPPDATA%/autodl-watcher/profile`，资料位置选择会记住。程序目录与个人资料分别管理，项目直属 `installed/` 是程序专用目录。

沿用旧资料会恢复原电源和自动AI设置，开始相应监控前请确认这些开关符合当前意图。首次切换到安装版时，先正常退出原源码窗口，再从安装入口打开。

卸载可使用 Windows“设置 → 应用 → 已安装的应用”，或从仓库先执行 `./installer/uninstall.ps1 -WhatIf` 核对目标，再执行卸载并确认。卸载默认保留个人资料。覆盖、迁移或卸载前正常退出目标应用。

安装版帮助菜单提供检查更新与更新重启。正式更新源配置为本仓库 GitHub Releases，用户确认后下载、校验并安装。当前本地未签名验收包关闭在线更新，正式签名和发布后才启用公开更新链。源码开发的安装和运行步骤如下。

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
| Node.js 22.12 及以上与 npm | 安装仓库内 `desktop/` 的 Electron 运行时 |
| Git | 克隆仓库、更新源码和参与协作时需要 |
| Microsoft Edge | 使用 AutoDL 平台登录时需要 |
| Windows OpenSSH 客户端及自己的 SSH 密钥 | 使用训练监控时需要 |

任意目录、普通 PowerShell 中检查：

```powershell
conda --version
git --version
node --version
npm.cmd --version
```
以上命令应输出版本号。Node.js 需达到 22.12。如果提示找不到 `conda`，先看[常见问题](#常见问题)。


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

先在仓库根目录安装界面依赖。普通 PowerShell 执行，项目目录须可写，首次需要下载锁定的 Electron。

```powershell
npm.cmd ci --prefix desktop
npm.cmd run setup-runtime --prefix desktop
```

两条命令的退出码都应为 `0`。第二条命令准备 `desktop/node_modules/electron/dist/autodl-watcher-desktop.exe`，Windows 任务管理器会显示项目名称「Auto算力与训练调度」和默认图标。如果失败，请保留报错命令和完整输出。Python 依赖由 `pyproject.toml` 声明，界面依赖与精确版本由 `desktop/package.json` 和 `desktop/package-lock.json` 声明。
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
默认打开 Electron 窗口，显示平台资源、服务器训练页、网络详情和设置。窗口的后台状态反馈 Python 服务是否就绪，连接与采集结果在相应页面查看。

旧 Tk 界面保留显式兼容入口，便于核对迁移前后的行为。

```powershell
conda run -n auto-gpu --no-capture-output python -s -m autodl_watcher --legacy-tk
```
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

界面默认深色，可在全局设置切换浅色。偏好继续合并保存到 `.ui/preferences.yaml` 的 `ui.theme`，重开恢复。Electron 后台继续读取原有服务器、规则、账本、登录资料和 Windows 凭据。

右上角“全局设置”旁的“网络连接详情”统一查看本实例各服务器监控及子进程的 TCP/UDP 记录，支持筛选、手动刷新和每 3 秒自动刷新。关闭详情暂停观察；不会停止训练监控。显示系统状态、进程/PID、源/目标地址和观察时间，不估算流量或请求次数。具体范围见[网络连接详情](docs/使用说明.md#6-网络连接详情)。

监控链路需要定向直连时，参阅[Clash 定向直连提示词](docs/Clash定向直连提示词.md)，先核对实际目标，再分别验证出站规则与 TUN 路由。

| 你的需求 | 下一步 |
| --- | --- |
| 只看远端训练，不使用 AutoDL | 先确认终端 `ssh 你的别名` 能用密钥登录，再进入“全局设置 → 服务器连接”，添加连接并开始训练监控 |
| 查看 AutoDL 资源与实例 | 在“全局设置 → AutoDL监控”填写 GPU 采集站 API 地址；本站选择“站点密码登录（/login）”，保存密码并启用认证，再完成 AutoDL 平台登录 |
| 需要自动开机 | 先核对自己的实例映射及资源，再明确开启“启用真实自动开机”；新配置默认关闭 |
| 需要 AI 解析训练日志 | 在“全局设置 → AI规则与消费”配置自己的接口和密钥；手动调用或另行开启自动生成规则 |

**仅监控时不要勾选自动开机和关闭无卡实例。** 新配置默认不执行电源操作，自动 AI 也默认关闭；已有个人设置会被恢复，使用前检查自己的勾选状态。

AutoDL 页展示平台资源和底部实时监控信息，不混入训练卡片。进入“全局设置 → 服务器连接”，勾选“固定到顶部标签”并保存，该服务器就会常驻在 AutoDL 右侧；名称与数量由自己的配置决定。未固定的服务器可用“打开训练页”临时查看；取消固定或收起页面不会停止监控。各服务器与平台的开始/停止互不联动。

资源表按“服务器 → 每张 GPU”展开，显存占用率、GPU 利用率、显存用量和数据时间分列显示；点击左侧箭头可折叠服务器，拖动资源区与黑框之间的分隔线可调整空间。正常等待下一轮采样会保留读数，采集源延迟或请求失败则单独标明状态，不再整格替换成“已过期”。优先使用入口自身样本，只有物理机样本时明确标注。

1.0.1 取消显存/利用率门槛，不统计 GPU 占用时长，也不新写 `watcher.log`；实时黑框仅在内存保留最近 2000 行，训练日志、AI 账本及已有历史文件继续保留。自动开机仍检查平台空位、账号与实例身份、INDEX 和新鲜数据。

新克隆默认使用仓库内置的绿色服务器图标，无需复制维护者的 `.ui/`。可以在“全局设置 → 应用图标”换成自己的图标，或恢复项目默认。

生成专用规则时，选择该任务的训练日志并等待完整预览，再手动发送。默认输入预算 200000 字符，可在 AI 全局设置调整；单文件读取上限 4 MiB，超限会说明原因，不静默截断。自动生成继续使用原有小片段预算，避免手动全文功能扩大自动消费。

### 哪些文件是你的个人设置

| 文件或目录 | 用途 |
| --- | --- |
| `config.yaml` | 仓库提供的公开安全示例 |
| `config.local.yaml` | 自己的配置，优先于 `config.yaml`；两者不逐字段合并 |
| `.ui/` | 服务器连接、界面偏好、AI 规则与用量、图标 |
| `runtime/` | 登录会话、采样状态及已有历史资料 |
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
| `desktop/package-lock.json` 变化或首次升级 Electron 界面 | 关闭本项目窗口，在仓库根依次执行 `npm.cmd ci --prefix desktop` 和 `npm.cmd run setup-runtime --prefix desktop`，然后重开应用 | 项目目录写权限 |
| Electron 进程名称或默认图标变化 | 关闭本项目窗口，在仓库根执行 `npm.cmd run setup-runtime --prefix desktop`，然后重开应用 | 项目目录写权限 |
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
| 缺少 Electron 运行时 | 尚未安装完整界面依赖 | 执行本页的两条 npm 安装命令，核对 Node 版本、完整输出及退出码 |
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
