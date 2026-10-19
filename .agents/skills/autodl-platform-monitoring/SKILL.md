---
name: autodl-platform-monitoring
description: 诊断或修改本项目AutoDL平台采集、登录判断、Telemetry重试、GPU占用归属及自动开机/无卡转换门控时使用；不用于SSH训练识别、AI规则或Windows打包。
---

# AutoDL平台监控与电源门控

适用本仓库v0.9.9对应实现；本轮资料整理核对日期2026-10-15。详细历史证据保留原验证日期与实际/模拟/待验证状态，当前代码与事实优先。个人任务Skill提供通用方法，本技能只补项目特殊约束；历史记录不授予外部操作权限。

## 主要流程

1. 按当前症状读取[平台与网络](references/platform-and-network.md)相应章节，区分会话失效、Telemetry故障、占用不明与电源响应不明，不从同一错误文案推断共同原因。
2. 修改平台/占用链时分别保留完整UUID和账号身份、入口空位与新鲜显存核验；采集前后的检查及额度释放后的最终实例查询各使用当时事实。仅监控不得进入开关机规划。
3. 处理网络恢复时保持原有限重试/退避与代理差异；平台电源POST不自动重发，Playwright响应释放和requests非stream消费分开判断。
4. 按修改运行test_collectors.py、test_platform_resources.py、test_telemetry_recovery.py、test_autostart.py或test_monitor_loop.py的相关离线用例。没有真实外部验证授权时用模拟，不把历史Success当当前成功。

按任务选读，不要求先加载其他项目技能。完整行为契约和既有验收仅在需要时查[重构开发规范](../../../重构开发规范.md)和[验收记录](../../../docs/重构验收记录.md)。
