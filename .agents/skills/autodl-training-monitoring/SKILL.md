---
name: autodl-training-monitoring
description: 维护本项目 SSH 只读采集、训练任务识别、PID与显存归属、日志进度、采样服务及固定服务器页面时使用；不用于启动训练、科研方案判断或生成收费AI规则。
---

# 训练发现、日志进度与SSH采集

本技能控制的是监控程序。页面“开始／停止训练监控”只管理本应用采集，不能解释为启动或终止模型训练。2026-11-16核对现行Electron、服务与旧Tk入口；旧证据保持原验证日期。

1. 识别或进度问题读[任务身份与日志协议](references/training-and-ssh.md)对应章节；只有接入故障才读其中“SSH接入历史故障与回退线索”。从实际连接、进程与日志证据判断，不将某次主机故障泛化为通用SSH配置。
2. 用服务器范围及boot_id/PID/start_ticks标识任务，核对本人／项目范围。tmux存在不证明存活；启动器与真实训练子进程分开，同入口DataLoader不重复计数。容器宿主PID、整卡显存与任务显存不混用，无法对应则未知。
3. 默认解析复用`training_events.py`；各日志族的epoch起点、steps含义和结束标志按实际协议解释，不套固定偏移。已确认错误在断连、任务替换或历史切换后仍保留；原日志读取须对应当前任务身份。
4. `TrainingService`复用已认证SSH，每主机至多一项在途采集；失败由服务按max(间隔,退避)调度。后台结果按代次整份接纳或丢弃，停止仅释放本服务拥有的连接，不按Python／SSH名称结束其他进程。
5. 默认Electron由`desktop_backend.py`驱动服务并返回快照，`renderer.js`展示；固定、临时打开、收起和普通切页不启动／停止采样。用户明确请求原日志时可走独立只读日志入口，不将其混为周期采样。旧Tk布局和窗口测试仅在`--legacy-tk`适用。
6. 按变更使用任务／解析、`test_training_service.py`、`test_training_pins.py`相关用例；Electron再走后台与`electron_smoke.cjs`，旧Tk才用`test_training_window.py`。AI规则另匹配[专用规则技能](../autodl-ai-rules/SKILL.md)，普通查看不加载收费生成流程。

业务基线与旧实测见[重构开发规范](../../../docs/重构开发规范.md)及[验收记录](../../../docs/重构验收记录.md)。源码核对、离线回归、真实SSH观察分别报告。
