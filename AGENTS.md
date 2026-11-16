# Auto算力与训练调度：项目约定

本文件补充全局工作约定；只保留本项目的必要约束和技能入口。

## 项目技能索引

| 用途或触发条件 | 入口 |
| --- | --- |
| AutoDL平台采集、登录判断、Telemetry、占用归属及电源门控 | [autodl-platform-monitoring](.agents/skills/autodl-platform-monitoring/SKILL.md) |
| SSH采集、训练发现、默认日志进度、显存归属和训练状态 | [autodl-training-monitoring](.agents/skills/autodl-training-monitoring/SKILL.md) |
| 专用规则、AI设置/请求、Token费用和退出记账 | [autodl-ai-rules](.agents/skills/autodl-ai-rules/SKILL.md) |
| Windows安装/卸载/更新、源码Conda与轻量EXE、随包worker和图标 | [autodl-windows-build](.agents/skills/autodl-windows-build/SKILL.md) |
| 本机网络连接详情、进程归属、系统表采集及观察历史 | [autodl-network-observability](.agents/skills/autodl-network-observability/SKILL.md) |
| 深浅主题、外观YAML、Tk控件配色和窗口切换适配 | [autodl-ui-theme](.agents/skills/autodl-ui-theme/SKILL.md) |
| Electron界面、preload通信、Python桌面后台及桌面验收 | [autodl-electron-desktop](.agents/skills/autodl-electron-desktop/SKILL.md) |
| 配置兼容、资料根、个人数据迁移/恢复和历史证据定位 | [autodl-maintenance](.agents/skills/autodl-maintenance/SKILL.md) |

按当前任务选读匹配技能；索引不要求全部读取，也没有统一必读总技能。仅在当前步骤需要时读对应references。长期知识按相同触发条件更新或按独立用途拆分，不重建旧笔记入口。

## 必要约束

- 先核对实际Git根目录与已有改动。保留config.local.yaml、.ui/、runtime/、.env、系统凭据和无关用户改动；干净包不得包含个人资料。
- 新配置默认仅监控、自动AI关闭；保留既有业务契约。具体领域约束与验证按任务读取对应技能，不在索引复制全部经验。
- 验证默认使用临时目录和模拟平台/SSH/AI；真实训练、开关机、收费调用及远程push须有对应明确授权。
- Python依赖统一在pyproject.toml，Electron及安装工具使用desktop/package.json和锁文件。源码开发使用一个独立Conda环境；安装版使用随包运行时。installer/跟踪安装/卸载/更新实现，产物installer/dist/及本机installed/忽略。个人资料与程序目录分别管理，源码及安装版复用业务服务。
- 按改动选必要验证。完整离线回归命令为 `conda run -n auto-gpu --no-capture-output python -s -m unittest discover -s tests -q`（环境名按本机专用环境一致替换）；文档迁移检查内容、引用和技能结构，无需重建EXE或重启监控。

安装与协作命令见 [开发与协作](docs/开发与协作.md)，业务契约见 [重构开发规范](docs/重构开发规范.md)。
