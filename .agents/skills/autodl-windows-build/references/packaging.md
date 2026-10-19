# 独立安装与Windows成品

适用v0.9.9的依赖打包、wheel及冻结GUI/worker验收。整理核对日期2026-10-15；历史实际/模拟验证保持原日期，不表示本轮重新构建。证据映射按需见[历史证据定位](../../autodl-maintenance/references/windows-and-data.md#历史证据定位)。

## 安装与打包

- 依赖唯一来源pyproject.toml，构建extra保留其明确固定版本，包版本动态读源码__version__；不把维护者Conda绝对路径写成贡献者必需条件。
- 2026-10-13曾发现部分有效依赖来自用户site，单独复制Conda prefix会漏包，且旧distribution metadata版本不等于正在运行的源码/EXE版本。做严格独立安装时使用新venv、禁usersite、核对实际依赖来源和版本；这个历史环境快照不表示所有机器同样布局。
- tools/build_exe.py先生成`.tools/build/release/AutoDLWatcher`。根AutoDLWatcher.exe为GUI，_internal/AutoDLWorker.exe为worker；只合并程序依赖，保留外部资料，干净包只取未混入个人资料的暂存目录。保留旧可用程序后再替换。
- Conda构建需要其Library/bin的libssl/libcrypto，并给构建设置匹配的DLL路径；当前脚本按实际文件存在处理。普通Python贡献者不要求安装作者的Conda环境。
- 成品含Python/Tk和Playwright驱动，平台登录使用系统Edge。须在无外部Python PATH、项目外cwd验证GUI/worker、实际资料定位与停止退出；窗口出现不能替代刷新/登录功能验收。
- PowerShell辅助调用使用SystemRoot/System32/WindowsPowerShell/v1.0/powershell.exe绝对路径。PATH问题要限制父进程/成品继承环境验证，仅给某个subprocess.run改env不能证明全链修复。

证据：2026-09-28精简PATH实际只读流程`v0.7.0-path-repair/gui-live-verification.json`；2026-10-13依赖独立验收归档`2026-10-13-config-consolidation/verification.json`、effective-dependencies.json；2026-10-14/15两套新venv、wheel、冻结程序及窗口结果见[重构验收记录](../../../../docs/重构验收记录.md)。当前回归test_package_layout.py、test_login.py、test_first_start.py；Windows/Python3.11已实际验收，其他操作系统和CI矩阵未据此宣称通过。
