---
name: autodl-electron-desktop
description: 维护本项目默认 Electron 页面、主进程与 preload、Python 桌面桥接和关闭生命周期时使用；主题、安装交付及平台／训练／AI业务分别按对应技能处理。
---

# Electron桌面与Python后台

默认界面自1.1.0起为Electron。2026-11-16按现行源码和安装入口校准；不能把源码窗口、旧Tk或本地安装验收互相替代。

1. 先确定当前是源码／轻量EXE、安装版还是`--legacy-tk`。定位`desktop/main.cjs`、`preload.cjs`、`renderer.js`与`desktop_backend.py`。源码将所选Conda解释器传给后台；安装版运行随包`autodl-backend.exe`，目录与更新读[Windows交付](../autodl-windows-build/SKILL.md)。
2. 渲染层负责展示交互，主进程负责窗口及受控系统操作，Python后台复用TrainingService、AIService和既有worker。普通快照刷新只读内存，平台／SSH采样仍由业务服务按间隔调度；网络页本机读表有独立入口。
3. 标准输入输出使用JSONL业务消息，普通打印进入诊断流。进程身份、退出等待和错误反馈由实际父子关系管理；不能按Python／SSH名称全机终止。窗口退出沿用业务收尾，不能把关窗当成远端训练已停或AI已免计费。
4. 保留contextIsolation、sandbox、CSP及业务方法白名单。preload不暴露Node、任意命令或文件接口；日志作为文本展示，文件导入使用实际原生选择结果授权范围。
5. 个人资料沿用当前资料根下的`.ui/`、`runtime/`、`config.local.yaml`与Windows凭据；密钥不返回界面快照，保存设置不覆盖未知键。主题问题读[主题技能](../autodl-ui-theme/SKILL.md)；AI发送与真实电源操作保留各自确认语义。
6. 受影响入口用`test_desktop_backend.py`与`tests/electron_smoke.cjs`核对，再实际打开对应窗口验证调用、持久化、失败反馈、缩放与退出。源码、轻量EXE和安装版分别选择入口；用隔离资料，不默认连接真实SSH／平台或发收费请求。

## 兼容与历史验证

旧Tk仅由`--legacy-tk`显式打开，兼容回归不能替代默认Electron。新增默认界面能力不应隐藏为跳回旧Tk窗口。

2026-11-15在Electron45.3.2观察到初始化拼写词典可能联网，当前主进程关闭会话拼写并将下载器指向本地拒绝路径；当时隔离窗口HTTP请求为0。该证据不保证未来版本或已配置业务零网络。

同日记录625项Python回归及实际Electron、源码、旧EXE隔离入口；`--source-entry`和`--launcher`区分后两者，测试复制EXE并显式解除测试junction再清理。这些是历史范围，本次文档校准未重跑。后续安装版证据与缺口见[安装参考](../autodl-windows-build/references/installer.md)，使用命令见[开发与协作](../../../docs/开发与协作.md)。
