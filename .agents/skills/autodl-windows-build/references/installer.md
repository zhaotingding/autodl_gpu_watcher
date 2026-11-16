# 安装版、目录与应用内更新

安装交付、路径选择、迁移或更新任务时读取。适用1.1.3起Windows x64当前用户安装版，原目录修复适用1.2.0，2026-11-16核对。

- installer/build_backend.py在项目开发环境构建onedir后台，包含业务依赖、Playwright驱动、默认配置、图标和远端只读探针。installer/backend_entry.py按模式分派。安装后的内部任务使用随包后台，外部OpenSSH/Edge使用系统已有程序。
- desktop/main.cjs同时检查app.isPackaged与autodlInstalled元数据。程序资源取resourcesPath，个人资料由installer/installed-profile.cjs选择；后台检查可执行文件、资源根、资料根及实际模块来源。冻结程序调用外部程序前恢复Windows DLL查找路径。
- NSIS使用交互目录页，带真实浏览按钮。覆盖预填原路径，用户改路径时预检旧卸载登记、目标写权限、空目录、资料重叠和联接，确认后移除旧程序，失败停止迁移。仅源码资料根直属installed/可作为程序专用子目录。
- 目录页进入和确认时读取当前HKCU安装位置。已有本应用非空目录可通过主程序PE与asar元数据核验，调用`validate-relocation.ps1 -Repair`原位修复，原卸载器和登记可以缺失。资料、目录联接和写权限检查继续生效。`verify_installer.ps1 -RepairRegistrationCase`使登记整个安装阶段缺失并核对安装器重建登记，`-ExplorerLaunch`核对实际Explorer父进程；`-RefreshRegistrationCase`只覆盖离页前恢复登记后的重读。
- 本机批准程序目标为D:/Dev/MCP/autodl_gpu_watcher_git/installed。安装器接受使用者自选位置；公共构建不写死维护者目录。默认新资料根为LOCALAPPDATA/autodl-watcher/profile，旧wheel根、记住的资料选择和可确认的源码资料可原地复用。安装包origin仅在来源确为源码installer子树时用于自动识别。
- 手动完成页以--autodl-open-setup打开基础设置，可调主题、填现有API密钥或跳过。更新重启--updated进入原工作台。安装、更新和卸载保护config.local.yaml、.ui、runtime及系统凭据。恢复旧偏好可能恢复电源或自动AI选项，用户开始相关任务前须明确其状态。
- 源码与local-preview不启用公开更新。正式源配置为zhaotingding/autodl_gpu_watcher GitHub Releases，正式构建要求签名及publisher身份。本地未签名验收使用--local-preview，双版本使用独立appId和127.0.0.1源；本地测试不能证明正式签名和公开发布可用。
- 更新启动10秒后检查，之后每6小时复查。autoDownload=false、autoInstallOnAppQuit=false，用户决定下载安装。正常退出与未保存编辑处理完成后才quitAndInstall。校验失败保留旧应用，重试使用已明确的状态。

验证入口包括test_installed_runtime.py、test_installed_profile.cjs、test_desktop_update.cjs、test_installer_relocation.ps1、test_installer_uninstall.ps1和verify_installer.ps1。实际随包UI、数据到达与更新分别使用installed_app_smoke.cjs、installed_platform_smoke.cjs、installed_update_smoke.cjs，资料限定installer/dist/verification。

2026-11-16已验证630项Python回归、17项资料/更新Node检查、实际D盘中文空格路径新装/覆盖/迁移/卸载保留、清除开发变量后的随包UI与本机平台GPU数据，以及1.2.90至1.2.91回环更新和错误摘要拒绝。正式签名、GitHub上传与真实账号服务另验。完整交付状态见CHANGELOG，技能格式检查不等于客户端已自动选用。

2026-11-16补验确认，本次Codex工具进程与Explorer派生进程的HKCU产品登记可见性不同。工具侧的安装记录仅证明该执行上下文的状态。整个安装验收驱动及其查询、缺失登记模拟、卸载均须在同一普通主机上下文执行；仅用`-ExplorerLaunch`启动安装器会混用两边的登记。具体隔离机制未确认。

普通主机驱动已实际完成`-RepairRegistrationCase -ExplorerLaunch`，登记全程缺失时原位修复并重新登记、迁移和卸载保留通过。正式1.2.0随后从Explorer完成指定installed/目录的修复安装，主机登记、开始菜单及原配置哈希核对通过。证据在本轮本机`installer/dist/repair-host-regression.log`和`installer/dist/install-formal-host.log`。账号与业务启动另验。
