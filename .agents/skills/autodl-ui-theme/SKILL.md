---
name: autodl-ui-theme
description: 修改本项目 Electron 默认界面或旧 Tk 兼容界面的深浅主题、YAML 外观偏好和窗口配色时使用；不负责平台请求、训练解析或 EXE 图标资源。
---

# 深浅主题与窗口配色

默认界面自1.1.0起为Electron；2026-11-16核对两种入口的职责。主题规则独立于业务采集，不能用旧Tk显示验证代替默认界面。

| 改动位置 | 实际入口与所需参考 |
| --- | --- |
| Electron页面、菜单、标题栏与缩放 | `desktop/styles.css`统一变量、`renderer.js`主题切换、`main.cjs`原生标题覆盖；读[Electron外观](references/theme.md#electron外观) |
| `--legacy-tk`控件、弹窗和状态色 | `theme.py`、`ThemeManager`及共享控件；读[旧Tk兼容界面](references/theme.md#旧tk兼容界面) |
| 桌面进程、IPC或退出逻辑 | 按需结合[Electron桌面](../autodl-electron-desktop/SKILL.md)，不由主题代码接管业务服务 |

- 两种入口都保留`.ui/preferences.yaml`的`ui.theme`，默认dark、可选light。只合并本次外观字段，保存前读取最新个人内容，不夹带未保存的监控设置，不覆盖认证或未知键；资料根沿用当前入口。
- 现有中性黑灰基准、标题覆盖高度及菜单拖动区域见参考，复用现有变量。窗口热切、新开弹窗、动态错误／成功状态和图表语义都须一致；主题切换不触发采样、重连或付费请求。
- Electron同时核对CSS与原生控制区颜色、键盘焦点、窄窗口和实际截图。截图确认目标PID与遮挡，不能用其他窗口像素判定主题。旧Tk用`test_theme.py`及相关GUI用例核对既开／新开窗口、状态色和保存失败。
- 验证保存与重新打开后的读回，并实际操作受影响入口。历史截图和像素记录只证明记录时的环境，不把本机DPI尺寸固定为所有设备要求。纯文档整理不重开用户在用窗口。
