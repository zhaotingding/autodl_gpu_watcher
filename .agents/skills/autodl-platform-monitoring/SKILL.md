---
name: autodl-platform-monitoring
description: 维护本项目 AutoDL 平台采集、站点认证、Telemetry 恢复、GPU 资源与占用归属、电源门控时使用；SSH训练监控、AI规则和桌面布局分别使用对应技能。
---

# AutoDL平台采集与电源门控

2026-11-16按现行服务及默认Electron入口校准。这里维护采集与开关机判定，不把旧Tk布局、个人通用训练方法或历史运行结果当成当前平台状态。

1. 先分清会话失效、Telemetry认证／传输故障、资源归属不明和电源响应未知，再读[平台与网络](references/platform-and-network.md)的相应章节。Electron页面通过`desktop_backend.py`复用既有worker；界面刷新不能变成额外平台轮询。
2. 维持完整UUID、账号身份、入口空位与新鲜GPU采样核验。1.0.1已按用户要求移除显存／利用率门槛，不从旧说明恢复。采集前后及额度释放后的最终实例查询各按当时事实判断；仅监控不得进入电源规划。
3. 站点401/403与AutoDL会话失效分开，配置目标缺失保持可见的未配置状态。网络恢复沿用有界重试／退避及既有代理差异；电源POST不得因超时或结果不明自动重发。Playwright响应释放与requests非stream消费分别处理。
4. 资源展示保留样本来源、采集时间和未知状态；视图的显示宽限不改变业务新鲜度门槛。物理机回退数据须标明来源，错误不伪造成功采样时间。旧Treeview样式只适用于`--legacy-tk`，当前Electron外观从[桌面技能](../autodl-electron-desktop/SKILL.md)定位。
5. 按变更选择`test_collectors.py`、`test_platform_resources.py`、`test_telemetry_recovery.py`、`test_autostart.py`或`test_monitor_loop.py`。改Electron入口再核对后台与实际页面；安装链的数据到达验证读取[安装参考](../autodl-windows-build/references/installer.md)。不把Tk、模拟或历史Success当作当前安装版业务通过。

保留[重构开发规范](../../../docs/重构开发规范.md)中的业务契约。真实平台连接、电源操作和训练各按任务授权，历史实测只作[验收证据](../../../docs/重构验收记录.md)。
