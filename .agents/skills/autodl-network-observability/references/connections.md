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
- `test_network_ui.py`、`test_gui_window.py`：单项在途、旧结果丢弃、筛选、未知时长、按钮与窗口复用。
- `test_network_live.py`：仅本机 127.0.0.1 TCP 握手及 UDP 绑定，核对系统端点/PID与消失记录；不连接真实 SSH、平台或 AI。

2026-10-26 已验证：486 项完整离线回归通过；其中新增真实 Windows 回环核对 TCP/UDP 端点与消失记录。普通执行上下文读取原生快照成功；隔离 Tk 窗口使用明确标注的模拟行核对按钮、表格、筛选及详情显示。没有对在用应用或真实远端业务做新连接验收。Skill 元数据和新增引用已校验，这不证明新会话已自动选用。

实测时分别报告：静态/模拟回归、本机原生回环、隔离界面显示、真实业务观察。源码新增此面板无需重建轻量启动器，现有窗口需用户重开才能加载新代码。
