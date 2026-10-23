# 配置迁移、资料保护与历史证据

适用v0.9.9应用设置兼容、资料根选择和历史资料恢复。整理核对日期2026-10-15；历史实际/模拟证据保持原验证日期，本轮未迁移个人设置或重启程序。

## 设置和资料根

- 应用设置使用`.ui/*.yaml`；yaml_settings仅兼容读取旧JSON，新保存写YAML、同名YAML优先，损坏YAML不回退旧内容。多行提示词保持原文，规则身份字符串不变，API密钥仍由Windows凭据管理器保存。
- 协议/运行状态JSON和SQL账本不转YAML，pyproject.toml、编辑器及Git文件保留工具要求的格式。不要为了“统一格式”改变存储语义。
- source_root是代码根，application_root是资料根。AUTODL_APP_ROOT只改资料位置，源码worker的PYTHONPATH来自已验证代码根；wheel使用用户资料目录，不写site-packages。
- config.local.yaml存在时整份采用，不与config.yaml逐字段合并；仓库config.yaml和包内defaults.yaml是无个人端点/实例的安全示例。只初始化缺失文件，不能用旧EXE把安全示例误当作维护者原配置。
- 默认仅监控，旧偏好缺电源字段也按关闭；用户名、间隔和电源选项正常模式重开恢复，debug模式强制关闭。仅监控传--monitor-only，不用--dry-run冒充关闭电源规划。监控中保存对下次开始生效，打开程序只读核验会话，监控需手动开始。

证据：v0.9.0.2电源偏好与仅监控模拟/隔离Tk，`v0902/full-tests.txt`、`ui-report.json`和restart-settings.png；2026-10-13YAML实际关闭后迁移及重开，外部归档`2026-10-13-yaml-settings/`保留verification.yaml、migration.yaml及图证；2026-10-14/15安装和GUI验收见仓库[重构验收记录](../../../../docs/重构验收记录.md)。业务回归入口为test_yaml_settings.py、test_gui_window.py、test_portable_startup.py。

2026-10-20 源码核对补充（非新安装实测）：GUI 的 `.ui/preferences.yaml` 覆盖用户/间隔/入口和电源偏好，保存不改写基础 YAML；直接 CLI 不读取该偏好，应显式使用 `--monitor-only`。恢复旧 `.ui/ai.yaml` 可能恢复自动规则生成；开始训练监控、发现新任务时可能计费，debug 不关闭此项。新配置默认关闭不能用于推断旧配置。

同日配置边界回归：空或无效 `telemetry.endpoint` 在功能入口显示“Telemetry 待配置”，不发 Telemetry 请求、不进入网络退避或电源初始化。配置加载仍接受空值以供新用户打开 GUI 和配置 SSH；手动平台发现可以保留已验证会话及主机列表，同时将 GPU 标为待配置。证据为 `test_telemetry_recovery.py`、`test_gui.py`、`test_first_start.py` 的隔离模拟通过，未执行真实平台验收。`Invalid URL` 不能直接归因为 TCP 积压或暴力扫描。

升级保留 `config.local.yaml`、`.ui/`（尤其 `training_rules.yaml`、`ai_usage.sqlite3` 与图标）、`runtime/`、可选 `.env`。数据库备份须由用户在写入者停止后进行，或使用一致性快照；正在写入时直接复制单个 SQLite 文件不构成可靠备份。Windows 凭据不随 YAML 恢复，账号/电脑或 API 地址变化需重配；浏览器资料复制不等于会话有效，绝对路径和 SSH 密钥引用需另核对。源代码更新不主动搬动这些资料，未知停止文件保留。恢复实际结果由用户确认，不能以路径存在或文件哈希不变冒充业务恢复成功。

## 历史证据定位

旧笔记有效知识已按领域迁入项目技能，完整原文保留在Git提交`e0339a0:Agent_MCP_Notes.md`；Project_Skill.md在迁移前与该原文逐字节相同。当前按知识域更新对应技能及references，不恢复旧笔记入口。

本机归档根为仓库同级`../autodl_gpu_watcher_archive/`，不作为克隆后运行或测试的前提：

| 旧证据写法 | 当前定位 |
| --- | --- |
| runtime/validation/X 或省略前缀的版本目录/诊断目录 | 2026-10-13-before-refactor/files/runtime/validation/X |
| .tools/backups/X、runtime/usage/X、旧dist内容 | 2026-10-13-before-refactor/files/下保留原相对路径 |
| 依赖、YAML整理与正式重构记录 | 2026-10-13-config-consolidation/、2026-10-13-yaml-settings/、2026-10-14-refactor/、2026-10-14-refactor-portability/ |
| 0.9.9源码/冻结/本机安装记录 | 2026-10-15-v099/及portable/；before-update含个人资料，只供本机恢复 |

迁移时抽查account-scope、response-recovery、Telemetry诊断、SSH加固、Q10日志、图标和规则UI证据路径存在；未重放历史服务器操作。新协作者没有本机归档时使用相应仓库测试，并明确缺少历史实测原件。

保护.ui、runtime/browser_profile、native_login_profile、当前日志及轮转、账本和全部图标，不以文件老或未跟踪为由删除。归档旧程序可按原相对路径找回；恢复前关闭应用并保留当前资料，不把旧.ui/runtime整体覆盖当前状态。仅清理明确无活动进程引用的停止文件或本轮可再生构建产物。

历史特殊维护：若另行获准改写Git说明，先保留git bundle --all和原refs，核对代码树、作者时间及父关系映射；v0.7.0旧备份位于上述files/.tools/backups/v0.7.0-before/history.bundle。该历史过程不授权当前改写历史或远程推送。
