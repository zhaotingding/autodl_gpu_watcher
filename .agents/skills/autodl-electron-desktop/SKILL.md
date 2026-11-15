---
name: autodl-electron-desktop
description: 修改本项目Electron页面、主进程/preload通信、Python桌面后台及桌面启动验收时使用；平台采集、SSH解析和AI请求算法仍按各自领域Skill处理。
---

# Electron桌面与Python后台

适用1.1.0起的默认Electron入口，2026-11-15核对。实现和验证状态以当前源码、测试及CHANGELOG为准。

1. 先定位`desktop/main.cjs`、`preload.cjs`、`renderer.js`与`desktop_backend.py`的实际调用。安装及启动细节见[开发与协作](../../../docs/开发与协作.md)，按任务读相应章节。
2. 界面展示和交互由渲染层负责，主进程管理窗口及受控系统操作，Python后台复用TrainingService、AIService和既有worker。界面刷新只读快照，业务采样仍由既有间隔调度。
3. 标准输入输出传JSONL业务消息，普通业务打印转到诊断流。进程归属和退出等待显式管理，不能按Python/SSH名称全机终止。源码与轻量EXE都把已选Conda解释器传给Electron及后台。
4. 保留contextIsolation、sandbox、CSP及业务方法白名单。preload不暴露Node或任意命令接口，日志内容作为文本展示；文件导入通过实际原生选择入口授权对应文件。
5. 个人资料沿用.ui、runtime、config.local.yaml和Windows凭据。主题读取ui.theme，密钥不返回界面快照；AI发送和真实电源操作保留原确认语义。
6. 对修改运行Python后台相关用例及`tests/electron_smoke.cjs`。实际打开Electron，检查从页面发起调用、持久化、错误反馈、缩放和正常退出；测试使用隔离根，真实SSH/平台/收费调用须有相应授权。

Electron初始化会尝试下载拼写词典，单独设置窗口spellcheck:false仍可能出站。当前主进程同时关闭会话拼写，并将词典下载器指向本地拒绝路径。2026-11-15在Electron45.3.2实测，未配置业务的隔离窗口网络日志HTTP请求为0，已加入桌面回归断言。

已验证625项Python回归及实际Electron、默认源码、现有EXE隔离入口。`--source-entry`和`--launcher`分别覆盖后两者，EXE测试只复制程序到临时根并显式解除测试junction后清理。真实平台/远端/收费调用及云端CI另验，格式校验不代表未来会话已自动选用技能。

旧Tk显式兼容入口为`--legacy-tk`。它的回归只证明兼容路径行为，不能代替默认Electron功能验收。新增界面能力优先使用默认入口，避免把迁移后的操作留成打开旧Tk窗口。
