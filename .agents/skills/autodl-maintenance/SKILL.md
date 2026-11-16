---
name: autodl-maintenance
description: 维护本项目 YAML/旧 JSON 兼容、源码与安装版资料根、用户设置保留和历史资料恢复时使用；不作为总入口，安装器实现与发布另用 autodl-windows-build。
---

# 配置、资料根与历史恢复

本技能维护项目配置与数据契约。2026-11-16按现行源码和安装参考校准；旧实测保留原日期，本次文档维护未迁移资料或重启应用。

1. 先判断实际入口是源码、wheel、旧成品还是当前安装版，再核对代码／资源根、可写资料根和当前设置来源。读[设置和资料根](references/windows-and-data.md#设置和资料根)；安装版的目录选择、注册、升级或卸载另读[安装、资料与更新](../autodl-windows-build/references/installer.md)。不把维护者路径或旧源码布局套给新安装。
2. `source_root()`定位源码，`application_root()`定位资料；`AUTODL_APP_ROOT`仅覆盖资料。当前安装后台由主程序传入资料根，不能缺参后悄悄改用安装目录。源码、随包后台与 wheel 的实际分支以`paths.py`及安装入口为准。
3. 区分应用 YAML、兼容旧 JSON、协议／运行状态 JSON 与 SQLite。新设置写 YAML，同名 YAML 优先；损坏 YAML 不回退旧内容。保留未知键、规则身份和凭据边界；配置源的整份覆盖与偏好字段合并各按已有实现，不混成一套规则。
4. 迁移或恢复先核对旧输入、当前有效资料和目标范围，用隔离样本验证。保护`config.local.yaml`、`.ui/`、`runtime/`、可选`.env`、账本、图标和用户修改；不因旧或未跟踪而删除。旧偏好可能恢复电源与自动 AI 选项，恢复文件不等于获准执行这些操作。
5. 只有查找旧证据或恢复历史版本时读[证据定位](references/windows-and-data.md#历史证据定位)。归档缺失则说明证据缺口，不能生成新目录冒充旧证据，不能以旧资料覆盖当前有效设置。
6. 配置兼容用`test_yaml_settings.py`，源码资料入口用`test_portable_startup.py`，安装资料入口用`test_installed_runtime.py`与`test_installed_profile.cjs`的相关用例。只维护文档时检查内容与引用；不因此运行程序、安装器或业务回归。

业务契约见[重构开发规范](../../../docs/重构开发规范.md)，历史验证见[验收记录](../../../docs/重构验收记录.md)；其他领域从[项目索引](../../../AGENTS.md)按需选择。
