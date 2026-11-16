# Auto算力与训练调度

## 项目约定

先遵循根目录 [AGENTS.md](../AGENTS.md)：其中包含按任务选择的项目技能和个人数据、Conda、真实操作授权约束。详细业务契约见 [重构开发规范](../docs/重构开发规范.md)，安装与协作见 [开发与协作](../docs/开发与协作.md)。

## 构建与测试

在仓库根目录的 PowerShell 中使用项目专用 Conda 环境；以下假设环境名为 `auto-gpu`，实际名称不同时替换。

```powershell
# 离线测试
conda run -n auto-gpu --no-capture-output python -s -m unittest discover -s tests -q
# 单个测试文件；用 -k 指定方法名或名称子串运行单个测试
conda run -n auto-gpu --no-capture-output python -s -m unittest discover -s tests -p test_evaluator.py -k test_no_alert_when_platform_has_no_slot -v
# 构建 wheel
conda run -n auto-gpu --no-capture-output python -s -m pip wheel --no-deps . -w dist
# 可选 Windows 轻量启动器，仅生成暂存产物；需要项目 build extra
conda run -n auto-gpu --no-capture-output python -s tools\build_exe.py --stage-only
```

依赖及 build extra 统一声明在 `pyproject.toml`。CI 在 Windows 上以 Python 3.10 和 3.12 运行离线测试及 wheel 检查。源码轻量启动器使用项目 Conda 环境；安装版主程序和后台使用随包运行时及业务依赖。

## 架构和数据流

- `launcher.py` 是 GUI、CLI 和同环境子进程的启动边界；`python -m autodl_watcher` 默认启动 Electron 界面，`--legacy-tk` 保留旧 Tk 入口。GUI 启动平台监控子进程，CLI 与该子进程均经 `main.py` 进入 `monitor.run_monitor`，不要复制监控业务链。
- 平台监控循环组合 Playwright 平台采集、HTTP Telemetry、`AvailabilityEvaluator`、占用观察和 `AutoStartCoordinator`；通过 `session.py` 的消息协议将结果交给 GUI。平台空位、采样新鲜度、占用归属和电源资格是不同事实，不能用 GPU 利用率或显存空闲单独推断开机资格。
- SSH 训练监控独立于平台监控：`TrainingService` 按服务器调度并维护代次，`TrainingConnection` 复用其拥有的 SSH 进程，将标准库探针 `remote_probe.py` 发往远端；`TrainingTracker` 和规则解析生成快照。探针不能依赖本机 GUI 或第三方包。
- Electron 通过 JSONL 接收 Python 后台结果，旧 Tk 入口使用队列和主线程渲染；后台操作和资源收尾不能阻塞界面，也不能让旧代次结果覆盖新状态。AI 规则是可选功能，日常解析可使用本地缓存规则；不得执行模型返回的代码或用它决定电源操作。

## 持久化与验证边界

- `paths.py` 区分源码根和资料根；`AUTODL_APP_ROOT` 可覆盖资料根，`config.local.yaml` 优先于公开 `config.yaml`。设置使用现有 `yaml_settings.py` 原子写入；运行状态沿用 JSON。不要覆盖个人配置、`.ui/`、`runtime/` 或凭据。
- 测试使用临时目录及模拟平台、SSH 和 AI。真实训练、开关机、收费 AI 请求和远程 push 需要明确授权；采集失败或过期不能当成空闲、无训练或确认离线。
