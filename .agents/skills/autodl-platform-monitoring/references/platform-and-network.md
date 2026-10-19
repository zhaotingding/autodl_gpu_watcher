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
- 入口空闲INDEX必须与新鲜Telemetry显存达标INDEX相交；空位、显存和账号额度不能互代。Telemetry按接收时间判陈旧。指定某个INDEX开机未获验证，实际分配GPU可能不同。
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

## Telemetry断连的判断边界

- `RemoteDisconnected`本身不证明SSH断连、登录失效或暴力扫描。2026-10-07日志中连续6轮断连后恢复，当时本机自有连接快照未见积压、无429证据，断连来源仍未确认。
- 正常每轮一次GET，瞬时失败最多两次；连续失败按60/120/240/300秒退避，429不立即重试并遵守Retry-After，冷却期零请求。失败轮不评估开机；成功复用健康Session，退出关闭。
- requests默认非stream响应会消费响应体，旧代码未显式close不能单独证明泄漏。平台控制面直连与Telemetry可使用本机HTTP_PROXY是两套既有策略；`trust_env=False`不能绕过系统TUN，也无法证明历史GUI实际路由。

证据：2026-10-07`telemetry-recovery/诊断报告.md`（实际日志/连接快照）与v0.8.10离线回归`tests/test_telemetry_recovery.py`；网络源头归因为待验证，不写成强制修复结论。
