# 平台、占用与网络故障

适用AutoDL私有云现有适配器和v0.9.9监控链。2026-10-15迁移时按当前代码核对边界；下列日期与“实际/模拟”来自原记录，不表示本轮重连验证。历史证据前缀及恢复方式见 [Windows与资料维护](../../autodl-maintenance/references/windows-and-data.md#历史证据定位)。

## 电源请求与额度

- 私有云开机为POST `/api/v2/instance/power_on`，载荷为 `{"instance_uuid":"完整ID","start_mode":"gpu"}`；无卡模式为`non_gpu`。关机为POST `/api/v2/instance/power_off`，载荷 `{"instance_uuid":"完整ID","release":"now"}`。网页Authorization仅存内存，不写日志/技能。
- 锁定完整UUID与入口后才请求；`code=Success`仅表示受理，还要确认同一UUID的`running/gpu`和实际占用。响应丢失时只读查询，不盲目重发或换目标。
- 无卡实例也可能占数量额度；遇`GpuStockReqNum`后本进程停止电源重试，没有已验证的额度预检接口可代替平台响应。
- 跨入口额度释放顺序：目标容量达标 → 完整个人实例列表 → 关闭本人其它无卡实例并确认全部shutdown → 转换/启动原固定目标前重新核验容量及身份。排除管理员、身份不明、列表不完整和有卡实例。不要在提取公共函数时省掉动作前的新查询。

证据：2026-09-26实际接口验证与授权转换，`authorized-conversion/evidence.json`；v0.6.10边界模拟见仓库`tests/test_autostart.py`。跨入口批量释放流程的整体验证为模拟，不推广为所有账号实测成功。当前实现：`autostart.py`、`collectors/platform.py`、`monitor.py`。

## 身份与物理GPU

- `/api/v2/user/get`返回user_uuid/tenant_uuid。批量电源要求API与浏览器身份、租户相同，浏览器`is_admin`和`is_platform_admin`两个标志严格为false。普通instance/list已验证为个人范围，管理员语义未验证。
- 1.0.1移除显存/利用率门槛；入口空闲INDEX仍须与新鲜Telemetry采样INDEX相交，身份与账号额度仍独立核验。Telemetry按接收时间判陈旧。指定某个INDEX开机未获验证，实际分配GPU可能不同。
- 占用表先核验入口、唯一INDEX与idle/total，再拆多实例绑定；202额外空行仅在Telemetry完整且额外行无占用证据时排除。
- `/api/v2/gpu_stock/list`载荷为`{"machine_id":"主机ID前缀"}`。六列版用户昵称在完整实例ID后的括号内，七列版独立成列；支持半角/全角括号及昵称嵌套括号。昵称用于显示，本人归属按完整实例列表核验。
- 同一203物理GPU可同时出现在203-1/203-2弹窗，弹窗入口不等于实例所属入口；页面标签保持用户所选，实例状态按所选UUID查询。
- 每轮按配置间隔共同采平台、Telemetry与名单。本人占用缺席须达到配置的连续可靠确认次数（默认2）；失败、缺入口、身份未知不算可靠缺席。图表使用过滤空位前的数据且按账号可见主机筛选，0空位不等于无数据。
- “可开机”须当前完整列表的入口/UUID匹配已启用目标；本地targets本身不证明容器仍在。SSH是否配置独立展示，“可开机”也不表示已经启用自动电源操作。

证据：2026-09-26实际只读`v0.6.10/account-scope.json`、`v0.6.11/live-names.json`；201/A100、202/TITAN RTX、203/V100占用回归见`tests/test_collectors.py`。2026-09-28实例入口与图表实际/模拟记录：`v0.7.0/verification.json`、`v0.7.0-dashboard/gui-live-verification.json`；2026-09-30`v0.8.1/platform-evidence.json`。当前昵称拆解已迁至`monitor_state.py:_instance_bindings_from_cell`，占用连续状态位于`occupancy.py`，展示资格位于`gui.py:discovery_rows`；回归`test_v070.py`、`test_gui.py`、`test_monitor_loop.py`。

## 登录与请求生命周期

- 登录页、401/403或明确业务登录超时才证明会话失效；HTTP200也可能携带登录超时。未知错误、超时、429与5xx不能统一判为登录过期。
- 同步登录资料后须真实接口核验，只证明检查时刻有效。GUI管道失败也要处理自有worker退出；旧后台未结束前不并发使用同一browser profile。
- `Network.getResponseBody: No resource with given identifier found`是响应体丢失；捕获请求上下文后用新API响应恢复，有限重试只读查询，不重发电源POST。
- Playwright Call log可能包含Authorization。平台电源传输失败仅输出固定安全消息。Playwright APIResponse的响应体会留在context中，当前`_api_response`在成功/异常路径均dispose，再复用context。

证据：2026-09-26隐私模拟`tests/test_collectors.py`；v0.6.9模拟及实际只读恢复`v0.6.9/response-recovery.json`；v0.7.0登录业务超时模拟`tests/test_v070.py`；2026-10-14响应释放模拟`tests/test_platform_resources.py`及`docs/重构验收记录.md`。不得把Playwright的资源生命周期与下面requests非stream行为混为一谈。

## 人工重新登录的资料交接（2026-10-16）

适用：普通 Edge 的 native_login_profile 交接给监控 browser_profile；仅在排查重新登录无效、profile 占用或修改 login.py 时读取。

- 用户点击“完成登录并关闭窗口”后，只向源profile所属Edge请求正常关闭，再等待正常退出（当前上限30秒）；按钮不能只发送Enter后等待。不要强杀刚认证的源进程。原生启动使用 `--disable-background-mode`，避免后台模式阻碍退出。
- 源仍未退出、旧目标仍占用或进程查询失败时，停止同步与删锁；源等待失败时不覆盖旧目标。不能把查询失败的空结果当成退出证据。
- 同步完成仍为checking，真实主机接口通过才发valid；认证错误为invalid，资料占用/本地组件错误为unknown。保留GUI从过期到重新核验的消息顺序。

已验证：旧代码确认后两次强杀源profile且忽略退出等待失败；新增回归先在旧实现失败。最终离线回归413项通过，登录与GUI相关42项通过。全新临时Edge离线写入虚拟localStorage认证数据，正常关闭、实际同步，经PlatformBrowserCollector.start重开后读回一致，临时资料已删除。GUI子进程回归覆盖invalid→checking→valid。

边界：本次真实旧profile未持久化可用认证，但未确定导致缺失的历史时序。强杀对照的两次合成样本均保留数据，不得写成“强杀一定丢token”；用户完成登录并点击确认后，最终正常关窗、同步及真实主机API核验通过（live-handoff.yaml）；最终本地GUI绿色会话状态已截图核对。隔离首次启动模态窗曾阻止正常退出，此时超时并禁止复制；应由用户先处理浏览器提示，不自动确认。未实测Edge右上角X和多窗口全覆盖，验证码仍需人工。验证记录见仓库docs/重构验收记录.md及同级归档2026-10-16-login-recovery。

## PowerShell 与 Python 的输出编码（2026-10-20）

适用：源码/Conda 启动后，登录收尾出现 subprocess `_readerthread` 的 UTF-8 `UnicodeDecodeError`。本机已复现 Windows PowerShell 按中文代码页输出中文命令行/诊断，而 Python UTF-8 模式用 UTF-8 解码的冲突；不是读取线程报错就能推断平台认证失败。

- 登录查询脚本显式设置 `[Console]::OutputEncoding` 和 `$OutputEncoding` 为无 BOM UTF-8；不能假定 Python 的 `PYTHONUTF8=1` 会改变 PowerShell 的输出编码。
- subprocess 捕获原始字节，查询命令行在调用线程严格按 UTF-8 解码。解码失败须转成 LocalStartupError 并停止交接，不用忽略错误或空列表冒充浏览器已退出。
- 关窗请求按字节接收诊断并检查退出码，随后仍需查询确认源profile进程退出。编码修复不能取消等待、扩大进程筛选或覆盖在用资料。
- 已验证：真实 PowerShell 下的合成中文/空格/单引号/emoji路径与中文 stderr；异常字节阻止删锁/复制并保留两份资料；25 项登录及 41 项 GUI 回归通过。本轮未操作真实登录资料或重验真实账号会话。

## Telemetry断连的判断边界

### 资源显示的采样计划（2026-10-31）

已确认旧GUI以now-observed_at直接套90秒新鲜度，并在每次重绘把数字换成已过期；worker实际120秒调度是正确的，显示会在两轮之间误报。修复通过成功GPU消息携带collected_at、poll_seconds、next_sample_at：collected_at在API返回后立即记录，不能推迟到占用/开机核验结束或消息发出时；周期取worker实际配置，不能读GUI中尚未生效的输入。

纯视图逻辑gpu_resources区分采集时已滞后的源数据、正常等待下轮、超过下轮计划及少量采集宽限的等待更新。显示宽限不参与evaluator/autostart/monitor_state的严格新鲜度门控，不为每次重绘发网络请求。失败消息不伪造成功时间；GUI保留旧samples及原采集/计划元数据，只附错误状态。数字不会因等待或失败被整格抹掉，未来超过5秒另标时间异常。

Treeview使用服务器父行和稳定entry+gpu_index子行，指标各列独立；等宽十格短条与百分比对齐，容量单位在列头GiB，去型号列。子行选择须映射父entry用于命令和偏好；增量更新保留展开、选中GPU和滚动，GPU消失回父项，全局设置只枚举根服务器。资源区与黑框可拖动分配空间。验证入口为test_gpu_resources.py和test_gui_window.py，同时保留旧样本不能开机的业务回归。

验证状态：566项完整离线回归通过，无Tk析构告警；包含120秒正常等待、API返回与发消息时刻分离、真实源滞后、失败保值、GPU子行入口映射/删除回退、折叠/滚动保留和实际tag字体列宽检查。隔离Tk实显6入口18张GPU，成功采样后100秒仍正常，失败读数保留并标明状态；本轮未新增真实网络请求，用户旧窗口重开效果仍待其确认。

2026-10-31新增站点认证：`telemetry_auth`在现有`.ui/preferences.yaml`合并个人地址/用户名/开关/认证方式，密码在独立`telemetry`凭据命名空间；AI旧凭据目标不变。只有相同HTTPS来源请求才附认证，空输入保留已有密码，显式删除限当前来源。401/403明确为采集站认证问题，单次失败后冷却，不能判AutoDL会话失效；刷新GUI也须保留这个原因。旧门槛/日志键只兼容忽略，不重写用户文件；训练日志/账本和历史保留。验证入口`test_telemetry_auth.py`、`test_telemetry_form_auth.py`、`test_v101_monitor_config.py`。

同日401根因已实证：密码已保存不等于已启用；本站API返回JSON `error/login`且无Basic挑战，公开`/login`为password/next表单。正确密码用POST建立会话后，同Session采集成功；不能仅凭401、浏览器保存密码提示或模拟Basic通过就选定认证协议。本站使用form，原显式Basic配置兼容保留，不做认证方式猜测或失败后自动切换。

表单首次1次同源POST，后续健康采样只GET并复用内存Cookie；POST失败不本轮重试，认证错误冷却60秒，429遵守Retry-After。POST/GET不跟随重定向；认证关闭或mode/来源/密码改变时清旧Cookie。输入新密码自动勾选启用，用户仍可明确取消；不擅自启用其他旧配置。27项认证专项及82项相关回归通过，实际采集器已验证1POST(302)+1GET(200)，解析17台主机49条GPU数据，密码/Cookie未输出或写明文。

显示范围（用户同日澄清）：实时监控黑框应保留；不统计占用时长不等于删除实时信息。后续清理或整理版本时，分别核对实时输出、文件日志和占用时长统计的职责，不能将它们视作同一个模块。

重整后验收（2026-10-31）：恢复两行监控及普通后台输出，黑框固定2000行内存上限，不落盘或累计GPU使用时长。训练进程开始/已运行、网络已观察时长和evaluator连续确认窗口有各自职责，不能以删除GPU占用统计为由一并移除。546项全量离线回归通过且无Tk析构告警，真实业务边界仍分别报告。

AutoDL资源表展示账号可见的全部入口；入口自身样本优先，缺少时物理机回退必须标注。平台监控每轮复用一次GPU采集与完整主机列表，不为每行新增轮询；训练页面独立复用4090布局，平台开始/停止不联动SSH。

初版验证（2026-10-31）：13项模拟Basic及521项离线回归通过，隔离Tk窗口核对6入口资源表、认证设置和独立203-1页。当时未验证本站实际协议，该记录不构成本站认证通过；其后的真实认证结果以上述实测为准。用户实际旧窗口重新加载修复仍需单独确认。

- `RemoteDisconnected`本身不证明SSH断连、登录失效或暴力扫描。2026-10-07日志中连续6轮断连后恢复，当时本机自有连接快照未见积压、无429证据，断连来源仍未确认。
- 正常每轮一次GET，瞬时失败最多两次；连续失败按60/120/240/300秒退避，429不立即重试并遵守Retry-After，冷却期零请求。失败轮不评估开机；成功复用健康Session，退出关闭。
- requests默认非stream响应会消费响应体，旧代码未显式close不能单独证明泄漏。平台控制面直连与Telemetry可使用本机HTTP_PROXY是两套既有策略；`trust_env=False`不能绕过系统TUN，也无法证明历史GUI实际路由。

证据：2026-10-07`telemetry-recovery/诊断报告.md`（实际日志/连接快照）与v0.8.10离线回归`tests/test_telemetry_recovery.py`；网络源头归因为待验证，不写成强制修复结论。
