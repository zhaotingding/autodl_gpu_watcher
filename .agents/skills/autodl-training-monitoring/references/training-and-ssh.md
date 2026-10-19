# 训练发现、进度与SSH

适用本项目SSH只读采集、任务身份和默认日志进度维护，不授权启动或终止模型训练。2026-10-15迁移核对，当前模块为v0.9.9；历史实际/模拟状态按原记录保留。证据前缀见 [Windows与资料维护](../../autodl-maintenance/references/windows-and-data.md#历史证据定位)。

## 任务身份、发现与显存

- SSH使用已有别名、公钥非交互`BatchMode=yes`、`ClearAllForwardings=yes`和严格主机校验；stdin运行标准库只读探针，不写远端脚本。只按本人UID及项目范围观察训练，只有Python候选才读cwd；无关进程PermissionError不能直接判整个列表缺失。
- 身份为服务器范围与boot_id/PID/start_ticks。tmux会话存在本身不是训练存活证据；非train命名入口按tmux祖先、打开日志等实际证据发现，自定义脚本范围仍须保留。
- 不同Python入口的启动器与真正子训练要区分，同入口DataLoader不重复统计；父进程自身有GPU工作时仍保留。启动器不能从历史引用重新插回当前任务。日志PID不匹配在任何收费请求前拒绝。
- 容器NVML宿主PID不能直接对应容器/proc。203曾无法从NSpid/fdinfo及平台接口取得可验证任务显存，应显示未知；裸机也须PID、启动时间与GPU UUID相符。整卡显存、系统mem_usage不代表任务用量，日志中的peak_gpu_allocated只表示框架峰值。其他用户仅读GPU进程元信息，不读日志。

证据：2026-09-30实际`v0.8.0/gui-live-verification.json`；2026-09-30至10-03实际`v0.8.1/platform-evidence.json`、`v0.8.2/container-memory-evidence.json`；2026-10-08在4090确认Python启动器另起真正的训练子进程，旧祖先筛选遮蔽训练，记录`rules-4090-development/live-before.json`、`live-after.json`。非train普遍发现及规则按钮联动为模拟验证；旧失败缓存无原始AI响应，不能推断模型返回了什么。

当前实现：`remote_probe.py`发现与采集，`training_config.py`配置，`training_connection.py`SSH连接，`training_service.py`调度，`training_tracker.py`状态；旧training.py已删除。回归：`test_v081.py`、`test_v0811.py`、`test_v083.py`、`test_v085.py`。

## 日志协议不可混用

| 实际日志族 | 需要保留的解释 | 原验证 |
| --- | --- | --- |
| DeepfakeBench旧training/train.py | range(start_epoch,nEpochs+1)，0..30共31轮；Epoch[1]为第2轮。Test Done!不代表全程完成，每个测试集保留自身epoch/step。 | 2026-09-30日志/源码实测；`test_training.py`的OOM、未知退出和重复打印模拟 |
| P03-T01 | TRAIN {...}、EVALUATED 分支 {...}，steps_per_arm累计，Epoch从0开始需偏移；EPOCH_END不等于完成。日志峰值显存与实时显存分开。 | 2026-10-03实际`v0.8.3/live-snapshot.json`，`test_v083.py` |
| Q10 | steps/steps_total为当前/总步数，Epoch从1开始；EPOCH_DONE arm epoch steps ... val {...}沿用相邻事件PID归属，不能套用P03偏移。 | 2026-10-07实际`v0.8.5/live-before.jsonl`、`live-after.json` |

Q10曾出现旧PID已退出、新PID在tmux继续写同一追加日志；不能把历史退出当作当前失败。原日志详情只展示关联训练日志，不混入joint_batch_trace.jsonl。DFDC历史49分钟无新增日志不直接判失败，默认120分钟无进展只是提醒核查；配置可改变提醒阈值。

当前默认解析与规则共用`training_events.py`。需要保持通用键值文本对损坏花括号注释的兜底，而专用JSON规则仍拒绝坏字典；v0.9.9已用离线样本和旧新差分核对，见`test_general_progress.py`、`test_training_rules.py`及`docs/重构验收记录.md`。

## 服务结果与视图边界

解析与连接收尾在后台，Tk只接纳当前代次的整个tracker，过时结果不能污染当前错误事实。停止只回收本服务拥有的连接；切页和原日志查看不增加采样。规则更新在收尾期间保留reparse_pending，连接释放后只做本地补解析。证据：2026-10-14/15离线test_training_service.py、test_training_window.py与[重构验收记录](../../../../docs/重构验收记录.md)，不表示本轮重新测量性能。

## SSH接入历史故障与回退线索

- 2026-10-03在203-1实际看到Exceeded MaxStartups、配置10:30:100、未认证连接11～12个、轮换用户名的失败登录约250～300次/分钟；训练继续、OOM=0。这支持新连接准入被拒，不能解释已认证VSCode此前25秒失联。只见127.0.0.1，不能据此封禁来源；需平台转发前证据。
- 该次授权后仅203-1配置`/etc/ssh/sshd_config.d/00-autodl-watcher-key-only.conf`：公钥认证、禁密码/交互认证、PermitRootLogin prohibit-password、LoginGraceTime 20、MaxAuthTries 3，MaxStartups不变。原记录先核验主机/公钥并设180秒限时回退，sshd -t/-T -C验证后SIGHUP，确认两个新连接和旧会话/训练仍在。
- 历史备份位置为`/root/.autodl-watcher-backups/ssh-20261003-v084/sshd_config.before`。需要回退时先获得当前相应操作授权，仅移除该专用配置并校验，再重载当前已核验监听进程；不要沿用历史PID。重建或平台覆盖后需重验，该配置未部署至4090或其他服务器。
- v0.8.4起同一个已认证OpenSSH进程按行采样；主调度失败按max(用户间隔,60/120/240/300秒退避)重试，不叠加线程重拨。60秒采样和15秒保活都不是重新登录。MaxStartups诊断需-v保留有限stderr，停止仅关本对象SSH。

证据：实际`ssh-203-20261003/诊断报告.md`、同目录login-timeline记录、`v0.8.4/ssh-hardening.json`；同日203/4090一连接三采样和20秒未认证释放实测；模拟回归`test_v084.py`。这些是历史部署/观察，当前可达性待验证，也不授予本次维护服务器配置的权限。
