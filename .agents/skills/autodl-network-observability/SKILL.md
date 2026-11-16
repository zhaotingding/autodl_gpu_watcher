---
name: autodl-network-observability
description: 维护本项目网络详情、Windows TCP/UDP 本机采集、进程树归属和请求证据时使用，含 Electron 默认界面与旧 Tk 兼容入口；不用于远端采样、业务重试或代理配置。
---

# 本机网络连接与请求证据

2026-11-16按默认 Electron 链校准。这里展示操作系统快照、已有请求观察和配置目标，不提供抓包、全量请求审计或代理路径证明；旧实测按原日期解释。

1. 先区分系统表缺项、进程归属、网址关联和界面显示，按问题读取[实现边界与验证](references/connections.md)。默认入口为`renderer.js → network.snapshot → desktop_backend.py → NetworkMonitor`；旧`network_ui.py`只用于显式 Tk 兼容入口。
2. `network_system`只读本机表，`network_activity`观察已有请求，`network_attribution`保存脱敏证据，`network_monitor`管理实例归属和有界历史。Electron由主进程传`AUTODL_DESKTOP_PID`作为根身份；不得只跟踪后台 Python 或按同名进程扩大到其他实例。
3. PID与创建时间共同核对。读表失败、进程身份不可读或未采到短连接只能表示未知，不写成零连接、真实握手时间或完整请求数。UDP只有绑定端点；TCP已连接不等于SSH已认证。
4. PID及本地／对端地址和端口全匹配才标“已匹配”；只有对端或进程证据标候选。配置目标不代表已请求，443端口不能证明HTTPS或完整网址；198.18/15只能提示TUN/Fake-IP可能。
5. 默认Electron网络页进入或手动刷新可读本机表，自动刷新由页面开关控制且每次仅一项在途；离开页面不继续网络页轮询。筛选和排序只处理已有记录，不触发平台、SSH、DNS、出站探测或断开连接。旧Tk生命周期按参考中的兼容段处理。
6. 原生采集／归因改动用对应`test_network_*`，必要时用本机回环验证；默认界面改动结合`test_desktop_backend.py`和`electron_smoke.cjs`，旧Tk才使用`test_network_ui.py`。分别报告模拟、本机原生、界面及真实业务观察，不互相替代。

用户操作见[网络连接详情](../../../docs/使用说明.md)。真实服务器、在用应用和个人资料不是默认验收对象。
