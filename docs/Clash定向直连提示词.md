# Clash 定向直连提示词

需要把本项目的 SSH、AutoDL 或 GPU 采集连接改为直连时，可把下面的提示词交给处理 Clash Verge / Mihomo 配置的对话。项目的“网络连接详情”只观察连接，不会修改 Clash 规则。

先区分三件事：

| 目标 | 对应层次 | 如何判断 |
| --- | --- | --- |
| 不经过代理节点 | Mihomo 出站规则，例如 `DIRECT` | 对应连接实际命中的规则及出站链 |
| 域名返回真实 IP | DNS 的 Fake-IP 过滤 | 当前生效的 DNS 模式、规则和新查询结果 |
| 目的连接不进入 TUN | 系统路由及 TUN 排除范围 | 真实目的 IP、系统路由和实际连接端点 |

`DIRECT` 表示直接出站，不代表连接从未进入 TUN；`dns.fake-ip-filter` 控制 Fake-IP 映射，不代替出站规则。`198.18.*` 地址是 Fake-IP / TUN 的线索，单凭地址不能判断是否经过代理节点。应检查当前生效配置和具体连接。[DIRECT 说明](https://wiki.metacubex.one/config/proxies/built-in/)、[DNS 配置](https://wiki.metacubex.one/config/dns/)

## 可复制的提示词

以下提示词不包含个人服务器地址、凭据或固定的维护者目录。请在处理配置的对话中补充实际项目目录。

```text
请为 Windows 上的“Auto算力与训练调度”配置 Clash Verge / Mihomo 定向直连。先只读核查，提出最小修改与影响范围，获得确认后再执行。

目标：本项目监控使用的 SSH、AutoDL HTTPS 和 GPU 采集 HTTPS 不经过代理节点。另请评估这些目标绕过 TUN 的可行性，将“命中 DIRECT”和“未进入 TUN”分开说明、分开验收。

一、确定实际范围
1. 先确认项目目录，按当前代码的配置加载顺序读取有效设置；不要假定共享 config.yaml 就是个人正在使用的配置。检查有效的平台与 GPU 采集站地址，以及个人界面设置中的覆盖项，只提取必要的域名、端口、路径和认证方式，不输出认证内容。
2. 从 .ui/servers.yaml 获取本项目的 ssh_alias，再核对用户 SSH 配置中对应 Host 的 HostName、Port、ProxyJump、ProxyCommand、Include 和 Match。IP、端口可能变化，不照抄历史截图。不要读取私钥；如解析 SSH 配置会执行 Match exec 或产生外部访问，先说明并采用安全的只读方式。
3. 查明正在运行的 Clash/Mihomo 实例、版本、规则模式、实际生效 profile、DNS/Fake-IP 模式、TUN 状态以及持久覆写入口。不要将未生效的源文件当作运行配置，不猜测控制器端口或管道名。
4. 结合项目进程树、实际可执行文件路径、PID 与创建时间、本机连接以及 Mihomo 对应连接记录，区分训练 SSH、平台 HTTPS、GPU 采集 HTTPS、浏览器/Playwright 网络和本机回环通信。确认进程归属，不因进程叫 python、node、Edge 或 ssh 就把它算成本项目。
5. 核对相关 HTTP(S)_PROXY、系统代理、项目浏览器代理参数和 SSH 代理配置。检查真实请求证据，不根据端口 443 猜网址，不根据 TCP 数量推断请求次数或扫描次数。无法确认的上层协议或网址明确标为未知。
6. 不输出密码、Token、Cookie、私钥、订阅密钥、控制器 secret 或完整敏感配置；不扫描端口、不批量重连、不启动训练、不执行电源操作或付费 AI 调用。

二、提出最小持久修改
1. 先列出将修改的精确文件和字段、备份位置及恢复方法。使用当前客户端支持的持久 merge/override/script 机制，不直接修改下次订阅更新就会覆盖的托管输出文件。
2. 仅针对确认的目标配置 DIRECT，检查规则顺序。需要区分项目时，评估准确进程路径与目标域名/IP/端口的组合匹配，并核实当前版本支持的语法。
3. 禁止将所有 python.exe、node.exe、msedge.exe、ssh.exe、全部 TCP 或全部 443 强制直连。项目启动 EXE 不一定拥有真正的网络连接，后台 Python、浏览器或 SSH 子进程也不会自动继承按父进程设置的规则。
4. 保持 ChatGPT、Codex、AI API 和其他应用的现有路由。不要默认把 AI 接口直连；同一项目 Python 可能同时发起 AI 请求，整个进程直连会扩大范围。
5. 需要真实 DNS 时，先核对 fake-ip-filter-mode，再使用相应的 blacklist、whitelist 或 rule 语法。Fake-IP 过滤不等于 DIRECT，也不等于绕过 TUN。
6. 若要绕过 TUN，按当前 Windows/Mihomo 版本评估 route-exclude-address，以真实目的 IP 的最小范围配置。不要排除整个 198.18 网段、整个公网或全部 443。不要把仅支持 Linux 的字段照搬到 Windows。
7. 系统 IP 路由排除会影响其他访问同一 IP 的应用；域名可能使用 CDN、共用 IP 或变化的解析结果。若严格的项目隔离与完全绕过 TUN 无法同时满足，明确说明限制并提交选择，不擅自扩大排除范围。
8. 说明新增服务器、SSH 映射变更、IPv4/IPv6 或站点地址变化时，要维护的精确位置。不要把当前 IP 或端口写成永久规则。
9. 不关闭整个 TUN，不切换全局直连，不杀死正在使用的 SSH、浏览器、Clash 或其他应用。需要应用配置、刷新 DNS 或重连时先说明影响；不通过清空全部连接来验收。

三、执行与验收
1. 确认获批范围后再备份、修改并验证配置语法，核对持久配置到实际运行配置的生效链。遇到拒绝访问先诊断具体权限，不一律要求管理员。
2. 在获准的新连接上核对目标、进程、规则和出站链，证明目标实际走 DIRECT；保存必要的脱敏证据。
3. 对“绕过 TUN”另行核对真实 DNS、Windows 路由和实际连接端点。DIRECT 本身不是绕过 TUN 的证明，Clash 里看不到记录也不能单独证明已绕过。
4. 旧连接和 DNS 映射可能仍保留旧状态；只对获准的项目连接做必要刷新，不影响其他会话。
5. 检查 ChatGPT、Codex、AI 等原有路由未被改变；不产生付费调用。
6. 保持项目正常监控间隔与 SSH 连接复用，不用高频轮询、连续重连或扫描进行验证。
7. 交付时分别列出已验证、未验证、变更文件、恢复步骤及今后目标变更的维护方法。未执行真实路由验收，不宣称直连已完成。
```

## 配置依据与边界

规则从上到下匹配；Mihomo 提供 `PROCESS-PATH` 和 `AND` 等组合方式。应根据实际目标和进程确定最小范围，而不是复制通用进程名直连规则。[路由规则](https://wiki.metacubex.one/config/rules/)

DNS 过滤模式有 `blacklist`、`whitelist` 和 `rule`，含义和写法不同。修改前必须检查实际模式。[Fake-IP 过滤说明](https://wiki.metacubex.one/config/dns/)

启用 `auto-route` 时，`route-exclude-address` 用于排除指定网段；`route-exclude-address-set` 则限定 Linux、nftables 和相应自动路由条件，不能直接当作 Windows 方案。系统路由排除是否达到目标仍须实测。[TUN 路由排除说明](https://wiki.metacubex.one/config/inbound/tun/)

文档依据核对日期：2026-10-31。此文件仅提供处理流程；未修改或验证任何读者机器上的 Clash 路由。
