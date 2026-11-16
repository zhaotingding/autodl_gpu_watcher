---
name: autodl-ui-theme
description: 修改本项目Electron或旧Tk界面的深浅主题、外观YAML和窗口配色时使用；不用于平台请求、训练解析或EXE图标资源。
---

# 深浅主题与窗口配色

1.1.0起默认Electron，配色由`desktop/styles.css`统一变量及renderer主题切换实现，保存仍为`.ui/preferences.yaml`的`ui.theme`。旧Tk使用下述theme.py路径，仅在显式兼容入口适用。约定核对日期2026-11-15。

Electron外观按用户确认参考消息萃取当前窗口，使用中性黑灰。主背景`#1b1b1b`、面板`#222222`、标题栏`#242424`，浅色标题栏`#ececec`。主进程采用hidden标题栏和44像素titleBarOverlay，主题热切时更新覆盖区颜色。顶部文件/编辑/视图/帮助菜单保留实际操作，菜单区no-drag，空白标题区可拖动。改动需查看包含原生窗口控制的完整窗口截图。

1. 外观共用`theme.py`，个人选择合并到`.ui/preferences.yaml`的`ui.theme`，默认dark，可选light。改保存/恢复或控件细节时读[主题实现与验证](references/theme.md)。
2. 复用共享语义颜色和`theme.Label`，运行中的错误/成功/提醒更新也须适配；训练卡片保留状态含义。不要只在启动时换底色，也不逐个窗口复制主题实现。
3. 使用现有资料根和YAML原子写入；只保存主题字段，不夹带未保存的监控设置，不覆盖认证/账号/未知键。切换主题不触发采集、重连或付费请求。
4. 验证首次启动、已开窗口切换、新开窗口、动态状态及保存失败；核对真实Tk显示与对比度。`test_theme.py`配合相关GUI用例，不能把保存成功视为显示成功。

Electron标题栏由主进程和CSS共同配色，文件选择框及消息框使用系统提供的窗口样式。旧Tk验证与Electron窗口验证分别适用。
