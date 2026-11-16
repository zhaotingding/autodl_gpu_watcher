---
name: autodl-windows-build
description: 维护本项目Windows安装包、目录选择迁移、应用更新、随包后台，以及源码Conda/轻量EXE启动和图标资源时使用；不用于平台故障或远端训练诊断。
---

# Conda 安装与 Windows 启动

1.1.3起提供当前用户Windows x64安装版，随附Electron及Python后台，源码开发继续使用项目Conda环境和轻量EXE。2026-11-16核对，安装交付任务先读取[安装、资料与更新](references/installer.md)，源码入口问题读取下述packaging参考。实际验证范围按版本记录。

## 主要流程

1. 安装、构建或入口问题读取[Conda 安装与轻量启动器](references/packaging.md)；涉及图标转换、加载或资源修改时才读取[图标四环节](references/icons.md)。
2. `pyproject.toml` 是Python依赖声明，Electron由`desktop/package.json`及锁文件声明，源码 `__version__` 是版本来源。使用开发者自己的专用 Conda 环境；不创建或依赖 venv/.venv，不安装到 base、全局 Python 或用户 site。保留有效项目环境；迁移时先验证新链路，再处理旧环境。
3. 源码安装、运行和测试使用 `conda run -n <项目环境>`，实时输出加 `--no-capture-output`；Python使用`-s`，pip使用`--no-user`。源码轻量EXE默认选择auto-gpu，管理器由CONDA_EXE或PATH定位。源码worker核对同环境解释器，安装worker核对随包EXE及模块来源。
4. 源码轻量EXE启动源码；安装版主EXE调用随包autodl-backend.exe，内部worker按bridge/monitor/discover/login/exe-icon模式复用同一后台。安装与源码分别验收解释器、依赖、资源根和资料根。
5. 图标变化分原件、转换、框架加载、系统显示核对，ICO保留作者帧，PNG各尺寸从原图生成。源码模式可按原流程修改已确认的轻量启动器，安装版磁盘EXE图标由安装构建写入，自定义图标用于窗口和快捷方式。分别检查首次、热更换和用户实际入口。
6. 本地替换先验收并保留个人资料。安装、卸载与更新实现跟踪在installer/，产物在忽略的installer/dist/，本机程序放installed/。按范围运行源码入口、安装后台、资料选择、真实NSIS和应用内更新测试。旧完整成品的归档或删除仍按具体授权处理。
7. 用户保留安装和启动验收时，只做获批静态/隔离测试；不代建环境、安装、重开应用或替换现有 EXE。按参考中的“手动验收与升级保留”区分干净安装与原环境升级，报告 Agent 检查和用户实测，不以历史记录补齐未验项目。

常规安装命令见 [README](../../../README.md) 和[开发与协作](../../../docs/开发与协作.md)；需要对照已完成验证时再读[验收记录](../../../docs/重构验收记录.md)。不要求先加载其他项目技能或读取全部历史证据。
