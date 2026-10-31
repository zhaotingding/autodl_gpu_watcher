# 实现边界与验证

仅在维护本机网络详情时读取。2026-10-26 核对：以下是源码契约和隔离/回环验证，不代表真实远端连接已验收。

## 原生 API 与进程身份

- `network_system.py` 通过 Windows IP Helper 的 owner-PID 表读取 TCP/UDP IPv4/IPv6，Toolhelp32 枚举 PID/父 PID/名称，GetProcessTimes 读取创建时间。无第三方依赖、命令行采集、反向 DNS 或出站探测；成功打开的句柄必须释放。
- 进程归属由本 GUI PID 开始，PID+创建时间防止复用；已核对的浏览器子进程在父 worker 退出后仍可保留。首次观察前已脱离子树、无法读取身份或系统代理代发的连接不能声称完整覆盖。
- `captured_at` 固定在读表之前；晚于该时刻创建的进程不能认领本轮旧 PID 的连接行。原生结构和字节序改动必须使用模拟表及真实 Windows 回环核对。
- 部分表失败不能等于零连接或全部结束。tracker 保留未确认行及 `last_seen`；根身份不可核对时不可扩大到同名进程。UDP 仅绑定端点，TCP ESTABLISHED 不表示 SSH 已认证。

官方依据：[GetExtendedTcpTable](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable)、[TCP IPv6 owner-PID](https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcp6row_owner_pid)、[UDP IPv6 owner-PID](https://learn.microsoft.com/en-us/windows/win32/api/udpmib/ns-udpmib-mib_udp6row_owner_pid)、[PROCESSENTRY32W](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w)、[GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)。

## 展示及生命周期

- 面板打开时每 3 秒读取一次，只有一项在途操作；关闭隐藏并取消定时，重开复用实例。旧代次结果不展示，应用退出销毁定时器；后台不操作 Tk。
- 当前记录不截断；消失历史最多 300 条、仅存内存。时间为首次/最近观察和未再观察时间，未知时长停在 `last_seen`。短连接可能落在采样间隙，关闭面板不采样。
- 不估算流量、请求次数、域名和代理上游，不加入“关闭全部”或进程终止。筛选和排序只操作已有记录，不触发网络采样或训练采样。

1.0.1（2026-10-31）增加独立配置目标表和用途列：SSH服务器名/别名来自TrainingService.network_targets()的实际活动connection.server，不能用pending新配置；HTTP网址来自应用配置并单独标明，不宣称已核对某条TCP的请求URL。SSH不是HTTPS，不能靠IP反查完整网址。`safe_target`去掉用户名/口令、查询参数与片段；UI主线程只替换不可变目标快照，不等待原生读表锁。验证入口为network_monitor/UI和GUI回归。

本次验证：完整离线回归521项通过，包含Windows本机回环测试；隔离Tk窗口以模拟行核对目标表、用途列和详情。没有用真实SSH或HTTPS业务连接验收网址对应关系，配置目标也不表示已发生请求。

## 验证入口

- `test_network_system.py`：原生结构、IPv4/IPv6/端口、错误可见性、句柄和快照起始时间。
- `test_network_monitor.py`：本实例子树、PID复用、父进程退出、失败保留、有界历史。
- `test_network_attribution.py`：请求PID/端点精确关联、浏览器及进程候选边界、敏感URL脱敏、有界证据、端点重用和回环区分。
- `test_network_activity.py`：真实本机HTTP/1.1连接复用及响应钩子与系统表联通、正文完整、无额外请求、脱敏和Playwright元数据降级。
- `test_network_ui.py`、`test_gui_window.py`：单项在途、旧结果丢弃、筛选、未知时长、按钮与窗口复用。
- `test_network_live.py`：仅本机 127.0.0.1 TCP 握手及 UDP 绑定，核对系统端点/PID与消失记录；不连接真实 SSH、平台或 AI。

2026-10-26 已验证：486 项完整离线回归通过；其中新增真实 Windows 回环核对 TCP/UDP 端点与消失记录。普通执行上下文读取原生快照成功；隔离 Tk 窗口使用明确标注的模拟行核对按钮、表格、筛选及详情显示。没有对在用应用或真实远端业务做新连接验收。Skill 元数据和新增引用已校验，这不证明新会话已自动选用。

实测时分别报告：静态/模拟回归、本机原生回环、隔离界面显示、真实业务观察。源码新增此面板无需重建轻量启动器，现有窗口需用户重开才能加载新代码。

## 请求归因证据

1.0.2源码契约，2026-10-31核对；本节先记录实现边界，本轮隔离测试及真实业务状态以交付结果为准。

- requests响应钩子若能读到实际socket，记录请求URL、PID、本机IP/端口及对端IP/端口。观察时间和进程创建时间也须相容；PID及这些端点均匹配时，同行显示“已匹配”。一次HTTP连接可能对应多个观测URL，不能把连接数写成请求数。
- 浏览器响应只有对端IP/端口时，在已核对进程子树内匹配，标记“候选：请求进程范围及对端；本机端口未核对”。Playwright驱动请求如只能取得实际Node PID或受约束子树，则使用`process-request`，只给该进程的非回环、非监听TCP列出候选网址，明确“未核对端点”；不得给任意worker子进程套用为已匹配。进程名只能收窄已核对PID子树，不能用于全机搜索归属。
- 精确证据优先于对端候选，对端候选优先于进程候选；上层协议列自身须显示“候选”，不能只在可能横向隐藏的依据列说明。无匹配证据显示“未核对”。SSH来自应用管理的活动SSH进程与别名配置；同PID成对回环TCP标明本机内部通信，不虚构HTTPS网址。
- `safe_target`删除URL中的userinfo、查询参数和片段；只在内存保留最多512条、15分钟请求观测，每条连接最多列出8个不同网址。新连接不得仅凭旧的同PID/端点重用继承更早请求。异步读表期间晚到证据留待下轮匹配。
- 上方配置网址明确标“应用配置，不能证明已发生请求”；实际请求记录与逐TCP匹配等级分别展示。观察器不可增加DNS、抓包代理、出站探测、请求重试或业务连接。
- 汇总分开远端TCP、回环TCP、监听、UDP及未再观察历史；连接本身不能证明代理规则结果。198.18/15保留地址可提示TUN/Fake-IP可能，真实DIRECT/PROXY须由代理规则/日志另行核对。

`network_activity`只在GUI及其监控/发现worker设置观察器；CLI不增加协议输出。requests在响应钩子内、正文消费前读取原socket；Playwright APIResponse的`server_addr()`自1.61提供，较老允许版本缺失时保留实际Node请求候选，不升级依赖。浏览器按实际Edge/Chrome通道收窄；驱动私有PID元数据变化时退回worker后代范围候选。来源依据：[requests事件钩子](https://requests.readthedocs.io/en/latest/user/advanced/#event-hooks)、[Playwright APIResponse.server_addr](https://playwright.dev/python/docs/api/class-apiresponse#api-response-server-addr)，2026-10-31核对。

观察器不持有Tk对象；GUI线程接收worker事件后只加入独立有界队列，未打开详情不读系统表。同一持续活动TCP的已知强证据可保留至该连接消失；消失后同端点重用不继承旧证据。上层URL从实际请求取得，外部代理代发、未观察到的失败请求和部分浏览器端点仍有明确未核对边界。

2026-10-31实际验证：599项完整离线回归通过，含真实127.0.0.1 HTTP/1.1两次请求同连接、完整正文、准确端点及Windows系统表“已匹配”；回调不增加请求。合成网络窗口实看网址列、候选标记及详情，协议文字132像素落在175像素列内。没有对用户正在运行的远端连接做业务验收，没有修改或验证Clash路由；Skill结构校验不等于未来会话已自动发现或调用。
