"use strict";

// 渲染层仅通过preload的业务方法访问后台，日志和服务器内容始终作为文本显示。
const api = window.watcher;
const state = {
  snapshot: null,
  page: "platform",
  settingsTab: "monitor",
  editingServer: null,
  temporaryServers: new Set(),
  showHistory: new Set(),
  network: null,
  networkFilter: "",
  networkView: "current",
  networkAutomatic: true,
  refreshing: false,
  renderingKey: "",
  navigationKey: "",
  aiSettings: null,
  toastTimer: null,
  desktopClosing: false,
  desktopMessage: "",
  dialogGeneration: 0,
  monitorEntry: "",
  updates: null,
  setupShown: false,
  setupConfig: null,
  dirtyForms: new WeakSet(),
  formSavePromise: null,
};
const $ = (selector, parent = document) => parent.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);
const number = (value, digits = 0) => Number.isFinite(Number(value)) && value !== null && value !== ""
  ? Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits }) : "—";
const dateTime = (value) => {
  if (!value) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { hour12: false });
};
const clock = (value) => {
  if (!value) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("zh-CN", { hour12: false });
};
const elapsed = (start, end) => {
  if (!start || !end) return "—";
  let seconds = Math.max(0, Math.floor(end - start));
  const days = Math.floor(seconds / 86400);
  seconds %= 86400;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return `${days ? `${days}天 ` : ""}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
};
const empty = (title, detail = "") => `<div class="empty-state"><strong>${escapeHtml(title)}</strong>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</div>`;
const button = (action, label, options = "") => `<button type="button" data-action="${action}" ${options}>${label}</button>`;
const field = (id, label, value = "", type = "text", extra = "") => `<label for="${id}">${escapeHtml(label)}</label><input id="${id}" name="${id}" type="${type}" value="${escapeHtml(value)}" ${extra}>`;
const check = (id, label, checked = false) => `<label class="check-label full"><input type="checkbox" id="${id}" ${checked ? "checked" : ""}><span>${label}</span></label>`;
const select = (id, label, choices, value) => `<label for="${id}">${label}</label><select id="${id}">${choices.map(([key, text]) => `<option value="${escapeHtml(key)}" ${key === value ? "selected" : ""}>${escapeHtml(text)}</option>`).join("")}</select>`;

function toast(message, error = false) {
  const target = $("#toast");
  target.textContent = String(message);
  target.classList.toggle("error", error);
  target.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { target.hidden = true; }, error ? 9000 : 4000);
}

async function call(method, params = {}, clicked = null) {
  if (clicked) clicked.disabled = true;
  try {
    return await api.invoke(method, params);
  } catch (error) {
    toast(error.message || error, true);
    throw error;
  } finally {
    if (clicked?.isConnected) clicked.disabled = false;
  }
}

async function confirmAction(title, message) {
  return api.invoke("desktop.confirm", { title, message });
}

function updateStatus(value) {
  state.updates = value;
  const available = Boolean(value.canInstall && value.version);
  $("#app-menu-help .update-dot").hidden = !available;
  $("#app-menu-help").title = available ? `可更新到 ${value.version}` : "帮助";
  $("#update-status").textContent = value.message;
  $("#update-version").textContent = value.version ? `可用版本 ${value.version}` : "";
  const busy = ["checking", "downloading", "restarting"].includes(value.phase);
  $("#update-install").disabled = !value.canInstall;
  $("#update-check").disabled = busy;
  $("#update-close").disabled = ["downloading", "restarting"].includes(value.phase);
  $("#update-progress").hidden = !Number.isFinite(value.percent);
  if (Number.isFinite(value.percent)) $("#update-progress").value = value.percent;
}

async function checkUpdates() {
  if (!$("#update-dialog").open) $("#update-dialog").showModal();
  updateStatus(await call("desktop.update.check"));
}

async function requestUpdate() {
  const form = $("#settings-content form");
  if (form && state.dirtyForms.has(form)) {
    const choice = await call("desktop.confirmUnsaved");
    if (choice === 2) return;
    if (choice === 0) {
      if (!form.reportValidity()) return;
      state.formSavePromise = null;
      form.requestSubmit();
      if (!await state.formSavePromise) return;
    } else state.dirtyForms.delete(form);
  }
  if (!$("#update-dialog").open) $("#update-dialog").showModal();
  updateStatus(await call("desktop.update.install"));
}

async function showBasicSetup() {
  const response = await call("ai.settings");
  const config = response.settings || response.config;
  state.setupConfig = config;
  $("#setup-content").innerHTML = `<p class="detail-meta">已有设置和历史资料继续使用。可以调整主题，或填写自己的模型接口密钥。</p><form id="basic-setup-form"><div class="form-grid">${select("setup-theme", "界面主题", [["dark", "深色"], ["light", "浅色"]], state.snapshot.theme)}${field("setup-base", "API 基础地址", config.base_url || "https://api.openai.com/v1", "url")}${field("setup-model", "模型 ID", config.model || "")}${field("setup-key", "API 密钥", "", "password", `autocomplete="new-password" placeholder="${response.has_key ? "留空保留已存密钥" : "可稍后配置"}"`)}</div><p class="form-note">保存密钥时请填写对应模型ID。留空或跳过会沿用已有凭据，日常监控按原设置运行。</p><div class="form-actions"><button type="button" id="setup-skip" data-action="setup-skip">跳过并进入应用</button><button type="submit" id="setup-complete" class="primary">保存并进入应用</button></div></form>`;
  $("#setup-dialog").showModal();
}

async function finishSetup(save) {
  if (save) {
    const key = $("#setup-key").value;
    const base = $("#setup-base").value.trim();
    const model = $("#setup-model").value.trim();
    const apiChanged = base !== state.setupConfig.base_url || model !== (state.setupConfig.model || "");
    if (key.trim() || apiChanged) {
      if (!model) { toast("请输入密钥对应的模型 ID，或跳过后在全局设置中配置。", true); return false; }
      await call("ai.save", { settings: { ...state.setupConfig, base_url: base, model }, key });
      $("#setup-key").value = "";
    }
    const theme = $("#setup-theme").value;
    await call("preferences.save", { preferences: { ui: { theme } } });
    applyTheme(theme);
  }
  await call("desktop.setup.complete");
  $("#setup-key").value = "";
  $("#setup-dialog").close();
  await refresh();
  return true;
}

function applyTheme(theme) {
  const choice = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = choice;
  $("#theme-toggle").title = choice === "dark" ? "切换为浅色主题" : "切换为深色主题";
  api.invoke("desktop.theme", { theme: choice }).catch(() => {});
}

function navigate(page) {
  state.page = page;
  state.renderingKey = "";
  state.navigationKey = "";
  $("#page").scrollTop = 0;
  render();
  if (page === "network") refreshNetwork();
}

function renderNavigation(snapshot) {
  const servers = snapshot.servers || [];
  const visible = servers.filter((server) => server.pinned || state.temporaryServers.has(server.id));
  const key = JSON.stringify([state.page, visible.map((server) => [server.id, server.name, server.status?.active])]);
  if (key === state.navigationKey) return;
  state.navigationKey = key;
  $("#navigation").innerHTML = `<button type="button" class="nav-item ${state.page === "platform" ? "active" : ""}" data-page="platform"><span aria-hidden="true">▦</span>AutoDL 平台</button>
    ${visible.length ? '<div class="nav-divider"></div><div class="nav-section">训练服务器</div>' : ""}
    ${visible.map((server) => `<button type="button" class="nav-item ${state.page === server.id ? "active" : ""}" data-page="${escapeHtml(server.id)}"><span class="dot ${server.status?.active ? "running" : ""}"></span>${escapeHtml(server.name)}</button>`).join("")}
    ${!servers.length ? `<button type="button" class="nav-item" data-action="add-server">＋ 添加服务器</button>` : ""}`;
  $("#settings-nav").classList.toggle("active", state.page === "settings");
  $("#network-nav").classList.toggle("active", state.page === "network");
}

function meter(value, blue = false) {
  const percent = Number.isFinite(Number(value)) && value !== null ? Math.max(0, Math.min(100, Number(value))) : null;
  if (percent === null) return '<span class="subtle">—</span>';
  return `<span class="meter-wrap"><progress class="native-meter ${blue ? "blue" : ""}" max="100" value="${percent}"></progress><span class="meter-value">${number(percent)}%</span></span>`;
}

function platformPage(snapshot) {
  const hosts = snapshot.platform?.hosts || [];
  const gpuCount = hosts.reduce((total, host) => total + (host.rows || []).length, 0);
  const available = hosts.reduce((total, host) => total + Number(String(host.slots || "0/").split("/")[0] || 0), 0);
  const worker = snapshot.worker || {};
  const session = snapshot.session || {};
  const busy = Boolean(worker.running);
  const monitoring = busy && worker.job === "monitor";
  const statusClass = session.status === "valid" ? "success" : session.status === "invalid" ? "error" : "warning";
  const selectedEntry = state.monitorEntry || snapshot.preferences?.entry;
  const choices = hosts.map((host) => `<option value="${escapeHtml(host.entry)}" ${selectedEntry === host.entry ? "selected" : ""}>${escapeHtml(host.entry)}</option>`).join("");
  return `<div class="toolbar">
      ${button("monitor-start", "开始 GPU 监控", `class="primary" ${busy || !hosts.length ? "disabled" : ""}`)}
      ${button("monitor-stop", "停止监控", monitoring ? "" : "disabled")}
      <select id="monitor-entry" aria-label="监控入口">${choices || '<option value="">选择入口</option>'}</select>
      <span class="spacer"></span>${button("platform-refresh", "刷新服务器", busy ? "disabled" : "")}
      ${button("login-start", "登录 / 更新会话", busy ? "disabled" : "")}
      ${worker.job === "login" && busy ? button("login-finish", "完成登录并关闭窗口", 'class="primary"') : ""}
    </div>
    <div class="inline-status ${statusClass}"><span class="signal"></span>${escapeHtml(session.message || "AutoDL 尚未配置，可先添加 SSH 服务器")}${session.checked_at ? `<span class="subtle">· ${clock(session.checked_at)}</span>` : ""}</div>
    <div class="summary-grid top-gap"><div class="summary-cell"><div class="summary-label">账号可见服务器</div><div class="summary-value">${hosts.length}<small>个入口</small></div></div><div class="summary-cell"><div class="summary-label">已取得 GPU 数据</div><div class="summary-value">${gpuCount}<small>张</small></div></div><div class="summary-cell"><div class="summary-label">平台空闲算力</div><div class="summary-value">${available}<small>个配额</small></div></div></div>
    <section class="panel"><div class="panel-header"><h2>服务器资源</h2><span class="subtle">${monitoring ? `采样间隔 ${escapeHtml(snapshot.preferences?.poll || "—")} 秒` : "平台与 GPU 同启停"}</span></div>
    <div class="table-scroll resource-table"><table><thead><tr><th>服务器 / GPU</th><th>空闲 / 总数</th><th>显存占用</th><th>GPU 利用率</th><th>显存 / GiB</th><th>数据时间</th><th>采样状态</th></tr></thead><tbody>
    ${hosts.map((host) => `<tr class="server-row"><td>${escapeHtml(host.entry)}</td><td>${escapeHtml(host.slots)}</td><td colspan="4"></td><td>${escapeHtml(host.state || "等待采样")}</td></tr>${(host.rows || []).map((gpu) => `<tr><td class="gpu-name">GPU #${escapeHtml(gpu.gpu_index)}</td><td></td><td>${meter(gpu.memory_pct)}</td><td>${meter(gpu.util_pct, true)}</td><td>${gpu.used_mb == null ? "—" : `${number(gpu.used_mb / 1024, 1)} / ${number(gpu.total_mb / 1024, 1)}`}</td><td>${clock(gpu.observed_at)}</td><td class="${gpu.state === "正常" ? "" : "warning-text"}">${escapeHtml(gpu.state)}${gpu.physical ? " · 物理机" : ""}</td></tr>`).join("")}`).join("")}
    </tbody></table>${!hosts.length ? empty("等待平台资源", "完成 AutoDL 登录后刷新服务器，或在设置中配置自己的 GPU 采集站。") : ""}</div></section>
    <section class="panel"><div class="panel-header"><h2>实时监控</h2><span class="subtle">${escapeHtml(worker.status || (monitoring ? "正在采样" : "尚未运行"))}</span></div><div class="terminal" id="realtime-output" aria-label="实时监控输出">${(snapshot.logs || []).map((line) => `<div class="log-line ${escapeHtml(line.level || "")}">${escapeHtml(line.text ?? line)}</div>`).join("") || '<span class="subtle">开始监控后，这里显示每轮采样与占用信息。</span>'}</div></section>`;
}

function metrics(summary) {
  return Object.entries(summary?.metrics || {}).map(([name, value]) => `${name} ${number(value, Math.abs(value) < .0001 ? 8 : 4)}`).join(" · ") || "—";
}

function metricLine(label, summary) {
  return `<div class="metric-line"><span class="metric-label">${escapeHtml(label)}</span><div class="metric-values">${escapeHtml(metrics(summary))}<span class="subtle">${summary?.epoch != null ? ` · Epoch ${escapeHtml(summary.epoch)}` : ""}${summary?.step != null ? ` / Step ${number(summary.step)}` : ""}</span></div></div>`;
}

function trainingPage(server) {
  const status = server.status || {};
  const snapshot = server.snapshot;
  const cards = snapshot?.cards || [];
  const visible = cards.filter((card) => !card.history || state.showHistory.has(server.id));
  const history = cards.filter((card) => card.history).length;
  const processes = snapshot?.gpu_processes || [];
  const gpuRows = (snapshot?.gpus || []).flatMap((gpu) => {
    const gpuProcesses = processes.filter((process) => process.gpu_index === gpu.index);
    return (gpuProcesses.length ? gpuProcesses : [{}]).map((process) => `<tr><td>${snapshot.container ? "容器" : "GPU"} #${escapeHtml(gpu.index)}</td><td>${number(gpu.memory_used / 1024, 2)} / ${number(gpu.memory_total / 1024, 2)}</td><td>${meter(gpu.util, true)}</td><td>${number(gpu.temperature)}°C</td><td>${escapeHtml(process.user || "—")}</td><td>${escapeHtml(process.pid || "—")}</td><td>${process.memory_used == null ? "—" : number(process.memory_used / 1024, 2)}</td><td>${escapeHtml(process.process || "未读到 GPU 进程")}${process.config ? ` [${escapeHtml(process.config)}]` : ""}</td><td>${dateTime(process.started_at)}</td><td title="截至最近存活采样">${elapsed(process.started_at, snapshot.observed_at)}</td></tr>`);
  }).join("");
  const warning = snapshot?.error || (status.closing ? "正在回收本程序的 SSH 连接" : !status.active && snapshot ? "采集已停止，显示最近结果" : "");
  return `<div class="toolbar">${button("training-start", "开始训练监控", `class="primary" ${status.active || status.busy || status.closing ? "disabled" : ""}`)}${button("training-stop", "停止", status.active ? "" : "disabled")}
    <label>采样间隔 <input id="training-interval" type="number" min="1" value="${escapeHtml(status.interval || 60)}" aria-label="训练采样间隔"> 秒</label><span class="spacer"></span>${button("server-settings", "连接设置")}${!server.pinned ? button("hide-server", "收起页面") : ""}</div>
    <div class="inline-status training-status ${warning ? "warning" : status.active ? "success" : ""}"><span class="signal"></span>${escapeHtml(warning || (status.active ? `SSH 用户 ${snapshot?.user || "待确认"} · 最近采集 ${clock(snapshot?.received_at)}` : "尚未开始训练监控"))}<span class="subtle">· 采集 ${status.samples || 0} 次 / 连接尝试 ${status.attempts || 0} 次${status.pending ? " · 新连接下次启动生效" : ""}</span></div>
    <section class="panel"><div class="panel-header"><h2>GPU 与进程</h2><span class="subtle">${snapshot?.container ? "容器编号与平台 GPU 编号独立" : "整卡与进程显存分列"}</span></div><div class="table-scroll training-output" aria-label="SSH监控面板"><table><thead><tr><th>GPU</th><th>整卡显存 / GiB</th><th>利用率</th><th>温度</th><th>用户</th><th>PID</th><th>进程 / GiB</th><th>进程 / 配置</th><th>进程开始</th><th>已运行</th></tr></thead><tbody>${gpuRows}</tbody></table>${!gpuRows ? empty(snapshot?.gpu_error || "等待 SSH 采样", snapshot ? "采集器未返回可用 GPU 信息，训练进程状态见下方。" : "从 SSH 读取 GPU、本人进程和训练日志。") : ""}</div></section>
    ${history ? button("toggle-history", `${state.showHistory.has(server.id) ? "收起" : "查看"}历史运行（${history}）`, 'class="quiet history-toggle"') : ""}
    <div class="training-cards">${visible.map((card) => taskCard(card, server)).join("") || `<section class="panel">${empty(snapshot ? "无匹配训练进程" : "等待训练采集", `范围 ${server.project || "当前 SSH 用户"} · 脚本 ${server.scripts}`)}</section>`}</div>`;
}

function taskCard(card, server) {
  const progress = card.progress || {};
  const completed = progress.completed ? progress.total_epochs : Math.max(0, Number(progress.epoch || 1) - 1);
  const level = ["success", "warning", "error", "idle"].includes(card.level) ? card.level : "warning";
  const memory = card.gpu_memory_mb == null ? "显存待核验" : `显存 ${number(card.gpu_memory_mb / 1024, 2)} GiB`;
  return `<article class="task-card ${level}"><div class="task-header"><h3>${escapeHtml(card.name)}</h3><span class="task-status">${escapeHtml(card.status)}</span></div>
    <div class="task-meta"><span>PID ${escapeHtml(card.pid)}</span><span>用户 ${escapeHtml(card.user || "—")}</span><span>GPU ${(card.gpu_indices || []).map((index) => `#${escapeHtml(index)}`).join(", ") || "待确认"}</span><span>${memory}</span><span>最近进展 ${clock(card.advanced_at)}</span></div>
    <div class="task-meta"><span>开始 ${dateTime(card.started_at)}</span><span>已运行 ${elapsed(card.started_at, card.last_alive_at)}</span>${card.tmux_session ? `<span>tmux ${escapeHtml(card.tmux_session)}</span>` : ""}</div>
    <div class="task-progress"><span class="subtle">Epoch ${number(progress.epoch)} / ${number(progress.total_epochs)} · Step ${number(progress.step)}${progress.total_steps ? ` / ${number(progress.total_steps)}` : ""}</span>${progress.total_epochs ? `<progress class="epoch-meter" max="${Number(progress.total_epochs)}" value="${completed}"></progress>` : ""}</div>
    ${metricLine("训练", progress.train)}${Object.entries(progress.tests || {}).map(([dataset, summary]) => metricLine(`测试 ${dataset}`, summary)).join("")}${Object.keys(progress.observations?.metrics || {}).length ? metricLine("其它指标", progress.observations) : ""}
    ${progress.phase === "unknown" ? `<pre class="preview">${escapeHtml(card.preview || "没有可读输出")}</pre>` : ""}${progress.error ? `<pre class="preview error-text">${escapeHtml(progress.error)}</pre>` : ""}
    <div class="task-actions"><span class="subtle ${String(card.rule_status).includes("不适用") ? "warning-text" : ""}">${escapeHtml(card.rule_status || "默认监控")}</span><div class="actions-group">${button("ai-explain", "生成 / 更新规则", `data-task="${escapeHtml(card.key)}"`)}${button("training-log", "训练原日志", `data-task="${escapeHtml(card.key)}"`)}</div></div></article>`;
}

function settingsPage() {
  const tabs = [["monitor", "AutoDL 监控"], ["servers", "服务器连接"], ["ai", "AI 规则"], ["usage", "用量与消费"], ["icon", "应用图标"]];
  return `<div class="settings-tabs" role="tablist">${tabs.map(([id, label]) => `<button type="button" role="tab" aria-selected="${state.settingsTab === id}" class="tab-button ${state.settingsTab === id ? "active" : ""}" data-settings="${id}">${label}</button>`).join("")}</div><div class="settings-content" id="settings-content"></div>`;
}

function monitorSettings(snapshot) {
  const preferences = snapshot.preferences || {};
  const telemetry = preferences.telemetry || {};
  return `<form id="monitor-form"><section class="form-section"><h2>监控与调度</h2><div class="form-grid">${field("monitor-user", "本人用户名", preferences.user)}${field("monitor-poll", "采样间隔 / 秒", preferences.poll || 60, "number", 'min="1" required')}
    ${check("monitor-live", "启用真实自动开机（会产生费用）", preferences.live)}${check("monitor-convert", "关闭本账号全部无卡实例（会中断实例）", preferences.convert_no_gpu)}</div><p class="form-note">已运行的监控沿用启动时配置，保存后下次启动生效。</p></section>
    <section class="form-section"><h2>GPU 采集站</h2><div class="form-grid">${field("telemetry-endpoint", "API 地址", telemetry.endpoint || "", "url")}${select("telemetry-mode", "认证方式", [["form", "站点密码登录（/login）"], ["basic", "HTTP Basic"]], telemetry.auth_mode || "form")}${field("telemetry-user", "用户名（Basic）", telemetry.username)}${field("telemetry-password", "站点密码", "", "password", 'autocomplete="new-password" placeholder="留空保留已保存密码"')}${check("telemetry-enabled", "启用站点密码认证", telemetry.password_auth)}</div><p class="form-note">密码保存在 Windows 凭据管理器，输入新密码后启用认证。保存完成后刷新服务器核验。</p></section>
    <div class="toolbar">${button("telemetry-delete-password", "删除此站点密码", 'class="quiet danger"')}</div>
    <section class="form-section"><h2>外观</h2><div class="form-grid">${select("theme-choice", "界面主题", [["dark", "深色"], ["light", "浅色"]], snapshot.theme || "dark")}</div></section><div class="form-actions"><button class="primary" type="submit">保存监控设置</button></div></form>`;
}

function serverSettings(snapshot) {
  const servers = snapshot.servers || [];
  const existing = servers.find((server) => server.id === state.editingServer);
  const server = existing || { id: "", name: "", ssh_alias: "", python: "python3", project: "", entry: "", scripts: "train.py,trainer.py,train_*.py", stalled_minutes: 120, pinned: false };
  return `<div class="server-layout"><div class="server-list">${servers.map((item) => `<button type="button" class="${item.id === state.editingServer ? "active" : ""}" data-edit-server="${escapeHtml(item.id)}">${escapeHtml(item.name)}</button>`).join("")}${button("new-server", "＋ 添加服务器", 'class="add-server"')}</div><div><h2 class="form-section">${existing ? escapeHtml(existing.name) : "添加服务器"}</h2><form id="server-form"><div class="form-grid">${field("server-name", "显示名称", server.name, "text", "required")}${field("server-alias", "SSH 别名", server.ssh_alias, "text", "required")}${field("server-python", "远程 Python", server.python, "text", "required")}${field("server-project", "远程项目目录", server.project, "text", 'placeholder="可选，远程绝对路径"')}${field("server-entry", "AutoDL 入口", server.entry, "text", 'placeholder="可选，例如 autodl-203-1"')}${field("server-scripts", "脚本 / 通配符", server.scripts, "text", "required")}${field("server-stalled", "无进展提醒 / 分钟", server.stalled_minutes, "number", 'min="1" required')}${check("server-pinned", "固定在导航栏", server.pinned)}</div><p class="form-note">SSH 凭据使用系统 OpenSSH 配置。连接字段修改在下次启动训练监控时生效。</p><div class="form-actions">${existing ? button("open-server", "打开训练页") : ""}<button type="submit" class="primary">保存连接</button></div></form></div></div>`;
}

function aiSettingsForm(config, hasKey) {
  const pricing = config.pricing || {};
  return `<form id="ai-form"><section class="form-section"><h2>全局模型接口</h2><div class="form-grid">${field("ai-base", "API 基础地址", config.base_url || "https://api.openai.com/v1", "url", "required")}${select("ai-protocol", "接口协议", [["responses", "Responses"], ["chat", "Chat Completions"]], config.protocol || "responses")}${field("ai-model", "模型 ID", config.model || "", "text", "required")}${select("ai-tier", "响应等级", [["flex", "Flex"], ["default", "Standard"]], config.service_tier || "flex")}${field("ai-key", "API 密钥", "", "password", `autocomplete="new-password" placeholder="${hasKey ? "留空保留此地址的已存密钥" : "输入自己的 API 密钥"}"`)}${check("ai-auto", "新训练自动生成监控规则（每任务一次，会外发日志并产生费用）", config.auto_generate)}</div><p class="form-note">这些设置适用于所有服务器。保存完成后，生成规则时才会调用模型。</p></section>
    <section class="form-section"><h2>规则生成指令</h2><div class="form-grid">${field("ai-max", "日志字符预算", config.max_log_chars || 200000, "number", 'min="1000" max="2000000" required')}<label for="ai-prompt">全局提示词</label><textarea id="ai-prompt" rows="7" required>${escapeHtml(config.prompt || "")}</textarea></div><p class="form-note">专用规则在本地复用于后续采样。手动发送前显示所选训练原日志，超出预算时明确拒绝。</p></section>
    <section class="form-section"><h2>估计消费单价</h2><div class="form-grid">${field("price-input", "输入 / 百万 Token", pricing.input ?? "", "number", 'min="0" step="any"')}${field("price-output", "输出 / 百万 Token", pricing.output ?? "", "number", 'min="0" step="any"')}${field("price-cached", "缓存命中 / 百万", pricing.cached ?? "", "number", 'min="0" step="any"')}${field("price-write", "缓存写入 / 百万", pricing.write ?? "", "number", 'min="0" step="any"')}${field("price-flex", "Flex 系数", pricing.flex_multiplier ?? "", "number", 'min="0.001" step="any"')}${field("price-cny", "美元 / 人民币汇率", pricing.usd_cny ?? "", "number", 'min="0.001" step="any"')}</div><p class="form-note">单价单位为美元。使用你的服务商实际计价，留空的金额显示为未知。</p></section><div class="form-actions"><span class="key-state">${hasKey ? "当前地址已保存密钥" : "当前地址尚未配置密钥"}</span>${button("ai-delete-key", "删除此地址密钥", 'class="quiet danger"')}<button type="submit" class="primary">保存 AI 设置</button></div></form>`;
}

async function renderSettingsContent() {
  const target = $("#settings-content");
  if (!target || !state.snapshot) return;
  if (state.settingsTab === "monitor") target.innerHTML = monitorSettings(state.snapshot);
  if (state.settingsTab === "servers") target.innerHTML = serverSettings(state.snapshot);
  if (state.settingsTab === "ai") {
    target.innerHTML = empty("正在读取 AI 设置");
    try {
      const response = await call("ai.settings");
      if (state.page !== "settings" || state.settingsTab !== "ai") return;
      state.aiSettings = response.settings || response.config || response;
      target.innerHTML = `${response.error ? `<p class="warning-text form-section">${escapeHtml(response.error)}</p>` : ""}${aiSettingsForm(state.aiSettings, response.has_key || response.key_saved)}`;
    } catch (_) { target.innerHTML = empty("AI 设置读取失败", "请检查底部错误信息后重新打开此页。"); }
  }
  if (state.settingsTab === "usage") {
    target.innerHTML = empty("正在读取用量账本");
    try {
      const usage = await call("ai.usage");
      if (state.page !== "settings" || state.settingsTab !== "usage") return;
      renderUsage(target, usage);
    } catch (_) { target.innerHTML = empty("用量读取失败"); }
  }
  if (state.settingsTab === "icon") {
    const icon = state.snapshot.icon || {};
    target.innerHTML = `<section class="panel"><div class="panel-header"><h2>应用程序图标</h2></div><div class="panel-content"><p>使用项目默认图标，或导入自己的 ICO / PNG。</p><p class="detail-meta top-gap">${escapeHtml(icon.path || icon.message || "项目默认图标")}</p><div class="toolbar">${button("icon-import", "选择图标", 'class="primary"')}${button("icon-reset", "恢复项目默认")}${button("icon-shortcut", "导出启动快捷方式")}</div><p class="form-note">ICO 保留作者提供的全部尺寸。窗口与磁盘 EXE 图标由各自入口加载。</p></div></section>`;
  }
}

function renderUsage(target, usage) {
  target.innerHTML = `<div class="toolbar"><h2>Token 与估计消费</h2><span class="spacer"></span>${button("usage-refresh", "刷新用量")}</div><pre class="detail-pre wrap-pre">${escapeHtml(usage.summary)}</pre>`;
}

function renderNetwork() {
  const snapshot = state.network;
  const entries = snapshot?.entries || [];
  const query = state.networkFilter.trim().toLowerCase();
  const rows = entries.filter((entry) => {
    const closed = entry.closed_at != null;
    return (state.networkView === "all" || (state.networkView === "closed" ? closed : !closed)) && (!query || JSON.stringify(entry).toLowerCase().includes(query));
  });
  const current = entries.filter((entry) => entry.closed_at == null);
  const targets = snapshot?.targets || [];
  const counts = { remote: 0, internal: 0, listener: 0, udp: 0 };
  for (const entry of current) {
    if (Object.hasOwn(counts, entry.classification)) counts[entry.classification] += 1;
  }
  const observations = [...new Map((snapshot?.observations || []).map((item) => [`${item.pid}/${item.url}`, item])).values()];
  return `<div class="toolbar"><select id="network-view" aria-label="连接记录范围"><option value="current" ${state.networkView === "current" ? "selected" : ""}>当前记录</option><option value="closed" ${state.networkView === "closed" ? "selected" : ""}>未再观察到</option><option value="all" ${state.networkView === "all" ? "selected" : ""}>全部记录</option></select><input id="network-filter" class="filter-input" placeholder="筛选用途、网址、进程或地址" aria-label="筛选网络连接" value="${escapeHtml(state.networkFilter)}"><label><input id="network-auto" type="checkbox" ${state.networkAutomatic ? "checked" : ""}>每 3 秒刷新</label>${button("network-refresh", "立即刷新")}</div>
    <div class="inline-status"><span class="signal"></span>远端 TCP ${counts.remote} · 回环 TCP ${counts.internal} · 监听 ${counts.listener} · UDP ${counts.udp} · 未再观察 ${entries.length - current.length} · 快照 ${clock(snapshot?.captured_at)}${snapshot?.elapsed_seconds != null ? ` · 读取 ${number(snapshot.elapsed_seconds, 3)} 秒` : ""}</div>
    ${snapshot?.errors?.length ? `<p class="warning-text top-gap">${escapeHtml(snapshot.errors.join("；"))}</p>` : ""}
    <section class="panel top-gap"><div class="panel-header"><h2>本实例连接</h2><span class="subtle">系统表与请求观测</span></div><div class="table-scroll network-table"><table><thead><tr><th>调用用途</th><th>传输 / 上层</th><th>请求网址 / SSH 目标</th><th>状态</th><th>进程 / PID</th><th>源地址</th><th>目标地址</th><th>归因依据</th></tr></thead><tbody>${rows.map((record) => `<tr class="network-row" data-network-id="${escapeHtml(record.id)}" tabindex="0"><td>${escapeHtml(record.purpose || record.process_name)}</td><td>${escapeHtml(record.protocol)} / ${escapeHtml(record.application_protocol || "未核对")}${String(record.attribution).startsWith("候选") ? "（候选）" : ""}</td><td class="service" title="${escapeHtml((record.request_urls || []).join("\n"))}">${escapeHtml(record.request_urls?.[0] || record.logical_target || "未核对")}</td><td>${record.closed_at != null ? "未再观察到" : escapeHtml(record.state)}</td><td>${escapeHtml(record.process_name)} (${record.pid})</td><td>${escapeHtml(endpoint(record.local_address, record.local_port))}</td><td>${escapeHtml(endpoint(record.remote_address, record.remote_port))}</td><td>${escapeHtml(record.attribution || "仅系统连接表")}</td></tr>`).join("")}</tbody></table>${!rows.length ? empty(snapshot ? "当前没有匹配记录" : "正在读取本机连接") : ""}</div></section>
    <details class="panel network-note"><summary>服务地址与请求观测</summary><div class="table-scroll top-gap"><table><thead><tr><th>用途</th><th>协议</th><th>配置地址</th></tr></thead><tbody>${targets.map((target) => `<tr><td>${escapeHtml(target.purpose)}</td><td>${escapeHtml(target.kind)}</td><td>${escapeHtml(target.target)}</td></tr>`).join("")}</tbody></table></div>${observations.length ? `<div class="table-scroll top-gap"><table><thead><tr><th>请求用途</th><th>PID</th><th>已观测请求网址</th><th>最近观测</th></tr></thead><tbody>${observations.map((item) => `<tr><td>${escapeHtml(item.purpose)}</td><td>${escapeHtml(item.pid)}</td><td>${escapeHtml(item.url)}</td><td>${clock(item.observed_at)}</td></tr>`).join("")}</tbody></table></div>` : ""}<p class="top-gap">配置地址与逐连接请求证据分别显示。连接数按本机系统快照统计，HTTP 可复用 TCP，本机回环也显示 TCP。候选及未核对会明确标注。</p></details>`;
}

function endpoint(address, port) {
  if (!address) return "—";
  return `${String(address).includes(":") ? `[${address}]` : address}:${port}`;
}

function render() {
  const snapshot = state.snapshot;
  if (!snapshot) return;
  renderNavigation(snapshot);
  $("#version").textContent = `v${snapshot.app?.version || snapshot.version || ""}`;
  $("#backend-status").textContent = "Python 后台已就绪";
  $("#global-status").textContent = state.desktopMessage || snapshot.worker?.status || "后台就绪";
  const selected = (snapshot.servers || []).find((server) => server.id === state.page);
  const titles = { platform: "AutoDL 平台算力", settings: "全局设置", network: "网络连接详情" };
  $("#page-title").textContent = titles[state.page] || selected?.name || "服务器";
  $("#eyebrow").textContent = selected ? "SSH 训练监控" : state.page === "settings" ? "配置与偏好" : "资源与训练";
  if (state.page === "settings") {
    if (state.renderingKey !== "settings") {
      $("#page").innerHTML = settingsPage();
      state.renderingKey = "settings";
      renderSettingsContent();
    }
    return;
  }
  const key = JSON.stringify(state.page === "platform" ? [state.page, snapshot.platform, snapshot.logs, snapshot.worker, snapshot.session]
    : state.page === "network" ? [state.page, state.network, state.networkView, state.networkFilter, state.networkAutomatic]
      : [state.page, selected, state.showHistory.has(state.page)]);
  if (state.renderingKey === key) return;
  state.renderingKey = key;
  const current = $("#realtime-output");
  const followLog = !current || current.scrollTop + current.clientHeight >= current.scrollHeight - 30;
  const logPosition = current?.scrollTop || 0;
  const intervalValue = $("#training-interval")?.value;
  const filterFocused = document.activeElement?.id === "network-filter";
  const selection = filterFocused ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
  $("#page").innerHTML = state.page === "platform" ? platformPage(snapshot) : state.page === "network" ? renderNetwork()
    : selected ? trainingPage(selected) : empty("服务器已移除", "在全局设置中添加连接。");
  const output = $("#realtime-output");
  if (output) output.scrollTop = followLog ? output.scrollHeight : logPosition;
  if (intervalValue != null && $("#training-interval")) $("#training-interval").value = intervalValue;
  if (filterFocused) {
    $("#network-filter").focus();
    $("#network-filter").setSelectionRange(...selection);
  }
}

async function refresh() {
  if (state.refreshing || state.desktopClosing) return;
  state.refreshing = true;
  try {
    const snapshot = await api.invoke("snapshot");
    const oldTheme = state.snapshot?.theme;
    state.snapshot = snapshot;
    if (snapshot.theme !== oldTheme) applyTheme(snapshot.theme);
    render();
    if (snapshot.setupPending && !state.setupShown) {
      state.setupShown = true;
      await showBasicSetup();
    }
  } catch (error) {
    $("#backend-status").textContent = "后台连接异常";
    $("#global-status").textContent = error.message || String(error);
    if (!state.snapshot) $("#page").innerHTML = empty("后台连接失败", error.message || String(error));
  } finally {
    state.refreshing = false;
  }
}

async function refreshNetwork() {
  if (state.page !== "network" || state.networkBusy || state.desktopClosing) return;
  state.networkBusy = true;
  try {
    state.network = await call("network.snapshot");
    if (state.page === "network") render();
  } finally {
    state.networkBusy = false;
  }
}

function openDialog(title, content) {
  state.dialogGeneration += 1;
  $("#dialog-title").textContent = title;
  $("#dialog-content").innerHTML = content;
  if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
}

async function showTrainingLog(server, card, explain = false) {
  const choices = (card.log_sources || []).map((source) => source.path);
  if (card.log_path && !choices.includes(card.log_path)) choices.unshift(card.log_path);
  openDialog(`${card.name} · PID ${card.pid} · ${explain ? "生成监控规则" : "训练原日志"}`,
    `<div class="detail-meta">${escapeHtml(card.log_path || "未找到训练日志")}</div><div class="empty-state">${explain ? "正在通过现有 SSH 会话读取所选日志…" : "正在读取最近采样的日志片段…"}</div>`);
  const generation = state.dialogGeneration;
  const response = await call("training.log", { server_id: server.id, task_key: card.key, log_path: card.log_path || "", full: explain });
  if (!$("#detail-dialog").open || generation !== state.dialogGeneration) return;
  renderLogDialog(server, card, response, explain ? choices : [], explain);
}

function renderLogDialog(server, card, result, choices, explain) {
  const text = result.text ?? result.source ?? "";
  const path = result.path || card.log_path || "";
  const info = `${path} · ${result.note ? `${result.note} · ` : ""}${number(result.characters ?? text.length)} 字符${explain ? ` · 约 ${number(result.estimated_tokens ?? Math.ceil(text.length / 3))} Token` : ""}`;
  $("#dialog-content").innerHTML = `${choices.length ? `<div class="toolbar"><label for="log-path">训练日志</label><select id="log-path">${choices.map((value) => `<option value="${escapeHtml(value)}" ${value === path ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}</select>${button("reload-log", "读取所选日志")}</div>` : ""}<p class="detail-meta">${escapeHtml(info)}</p>${explain ? `<textarea id="rule-source" aria-label="发送给AI的日志内容" spellcheck="false">${escapeHtml(text)}</textarea><p class="form-note">核对完整日志后生成规则。调用会发送上面的内容并产生费用，后续监控复用已生成的规则。</p><div class="form-actions">${button("generate-rule", "生成并应用规则（会产生费用）", 'class="primary"')}</div>` : `<pre class="detail-pre">${escapeHtml(text || card.details || "没有可读日志")}</pre>`}`;
  $("#reload-log")?.addEventListener("click", async (event) => {
    try {
      const generation = state.dialogGeneration;
      const response = await call("training.log", { server_id: server.id, task_key: card.key, log_path: $("#log-path").value, full: true }, event.currentTarget);
      if ($("#detail-dialog").open && generation === state.dialogGeneration) renderLogDialog(server, card, response, choices, explain);
    } catch (_) { /* 读取失败保留已显示日志，具体原因由call显示。 */ }
  });
  $("#generate-rule")?.addEventListener("click", async (event) => {
    try {
      if (!await confirmAction("生成并应用监控规则", `将向配置的 AI 接口发送 ${number($("#rule-source").value.length)} 字符训练日志，可能产生费用。确认开始？`)) return;
      await call("ai.generate", { server_id: server.id, task_key: card.key, log_path: path, source: $("#rule-source").value, confirmed: true }, event.currentTarget);
      toast("规则生成请求已启动，结果会更新到训练卡片。后续采样复用规则。");
      $("#detail-dialog").close();
      await refresh();
    } catch (_) { /* 生成失败保留待发送内容供用户修正。 */ }
  });
}

function selectedServer() {
  return state.snapshot?.servers?.find((server) => server.id === state.page);
}

function collectMonitorForm() {
  const password = $("#telemetry-password").value;
  return {
    ...state.snapshot.preferences,
    user: $("#monitor-user").value.trim(), poll: $("#monitor-poll").value,
    live: $("#monitor-live").checked, convert_no_gpu: $("#monitor-live").checked && $("#monitor-convert").checked,
    ui: { ...state.snapshot.preferences?.ui, theme: $("#theme-choice").value },
    telemetry: { endpoint: $("#telemetry-endpoint").value.trim(), username: $("#telemetry-user").value.trim(),
      auth_mode: $("#telemetry-mode").value, password_auth: $("#telemetry-enabled").checked || Boolean(password), password },
  };
}

// 全局事件委托保持表格刷新后的按钮可用，表单在后台状态轮询时保持用户输入。
document.addEventListener("click", async (event) => {
  const target = event.target.closest("button,[data-network-id]");
  if (!target || target.disabled) return;
  if (state.desktopClosing && target.id !== "dialog-close") return;
  try {
    if (target.dataset.page) return navigate(target.dataset.page);
    if (target.dataset.settings) {
      state.settingsTab = target.dataset.settings;
      state.renderingKey = "";
      return render();
    }
    if (target.dataset.editServer) {
      state.editingServer = target.dataset.editServer;
      return renderSettingsContent();
    }
    if (target.dataset.networkId) {
      const record = state.network.entries.find((item) => item.id === target.dataset.networkId);
      const details = [`用途 ${record.purpose || "—"}`, `进程 ${record.process_name} · PID ${record.pid}`, `协议 ${record.protocol} / ${record.application_protocol || "未核对"}`, `状态 ${record.state}`, `源地址 ${endpoint(record.local_address, record.local_port)}`, `目标地址 ${endpoint(record.remote_address, record.remote_port)}`, `归因 ${record.attribution || "仅系统表"}`, `首次观察 ${dateTime(record.first_seen)}`, `最近观察 ${dateTime(record.last_seen)}`, `已观察 ${elapsed(record.first_seen, record.closed_at || record.last_seen)}`, record.network_hint || "", ...(record.request_urls || [])];
      return openDialog("连接详情", `<pre class="detail-pre">${escapeHtml(details.filter(Boolean).join("\n"))}</pre>`);
    }
    const action = target.dataset.action;
    if (action === "setup-skip") return await finishSetup(false);
    if (action === "update-check") return await checkUpdates();
    if (action === "update-install") return await requestUpdate();
    const server = selectedServer();
    if (action === "monitor-start") {
      const preferences = state.snapshot.preferences || {};
      if (preferences.live && !await confirmAction("启动自动开机监控", "当前已开启真实自动开机，满足条件时会执行电源操作并可能产生费用。确认使用当前配置启动？")) return;
      state.monitorEntry = $("#monitor-entry").value;
      await call("preferences.save", { preferences: { entry: state.monitorEntry } });
      await call("monitor.start", { ...preferences, entry: state.monitorEntry }, target);
    }
    if (action === "monitor-stop") await call("monitor.stop", {}, target);
    if (action === "platform-refresh") await call("monitor.refresh", {}, target);
    if (action === "login-start") await call("login.start", {}, target);
    if (action === "login-finish") await call("login.finish", {}, target);
    if (action === "training-start") await call("training.start", { server_id: server.id, interval: Number($("#training-interval").value) }, target);
    if (action === "training-stop") await call("training.stop", { server_id: server.id }, target);
    if (action === "server-settings") {
      state.editingServer = server.id;
      state.settingsTab = "servers";
      return navigate("settings");
    }
    if (action === "hide-server") {
      state.temporaryServers.delete(server.id);
      return navigate("platform");
    }
    if (action === "toggle-history") {
      if (state.showHistory.has(server.id)) state.showHistory.delete(server.id);
      else state.showHistory.add(server.id);
      return render();
    }
    if (action === "training-log" || action === "ai-explain") {
      const card = server.snapshot.cards.find((item) => item.key === target.dataset.task);
      return await showTrainingLog(server, card, action === "ai-explain");
    }
    if (action === "new-server" || action === "add-server") {
      state.editingServer = null;
      state.settingsTab = "servers";
      if (state.page !== "settings") return navigate("settings");
      return renderSettingsContent();
    }
    if (action === "open-server") {
      state.temporaryServers.add(state.editingServer);
      return navigate(state.editingServer);
    }
    if (action === "network-refresh") return await refreshNetwork();
    if (action === "usage-refresh") return await renderSettingsContent();
    if (action === "telemetry-delete-password") {
      const endpoint = $("#telemetry-endpoint").value.trim();
      if (!await confirmAction("删除采集站密码", "确认删除当前站点在 Windows 凭据管理器中保存的密码？")) return;
      await call("telemetry.delete_password", { endpoint }, target);
      toast("此站点密码已删除。");
    }
    if (action === "ai-delete-key") {
      if (!await confirmAction("删除 API 密钥", "确认删除当前接口地址已保存的 API 密钥？")) return;
      await call("ai.delete_key", { base_url: $("#ai-base").value.trim() }, target);
      toast("此地址密钥已删除。");
      return renderSettingsContent();
    }
    if (action === "icon-import") {
      const path = await api.chooseFile({ kind: "icon" });
      if (!path) return;
      await call("icon.import", { path }, target);
      toast("图标已导入。");
    }
    if (action === "icon-reset") {
      await call("icon.reset", {}, target);
      toast("已恢复项目默认图标。");
    }
    if (action === "icon-shortcut") {
      const path = await api.chooseFile({ kind: "shortcut" });
      if (!path) return;
      await call("icon.shortcut", { path }, target);
      toast("启动快捷方式已导出。");
    }
    if (action) await refresh();
    if (action?.startsWith("icon-")) renderSettingsContent();
  } catch (_) { /* call已显示具体错误，保留页面和输入供用户修正。 */ }
});

async function saveSettingsForm(event) {
  event.preventDefault();
  if (state.desktopClosing) return false;
  const submitter = event.submitter;
  try {
    if (event.target.id === "basic-setup-form") return await finishSetup(true);
    if (event.target.id === "monitor-form") {
      const preferences = collectMonitorForm();
      if (preferences.live && !state.snapshot.preferences.live && !await confirmAction("启用真实自动开机", "启用后下次启动平台监控可能执行真实开关机并产生费用。确认保存？")) return false;
      await call("preferences.save", { preferences }, submitter);
      $("#telemetry-password").value = "";
      applyTheme(preferences.ui.theme);
      toast("监控设置已保存，当前任务下次启动时采用新配置。");
    }
    if (event.target.id === "server-form") {
      const old = state.snapshot.servers.find((server) => server.id === state.editingServer);
      const server = { id: old?.id || `server-${Date.now()}`, name: $("#server-name").value.trim(), ssh_alias: $("#server-alias").value.trim(), python: $("#server-python").value.trim(), project: $("#server-project").value.trim(), entry: $("#server-entry").value.trim(), scripts: $("#server-scripts").value.trim(), stalled_minutes: Number($("#server-stalled").value), pinned: $("#server-pinned").checked };
      await call("servers.save", { server }, submitter);
      state.editingServer = server.id;
      toast("服务器连接已保存。");
    }
    if (event.target.id === "ai-form") {
      const settings = { ...state.aiSettings, base_url: $("#ai-base").value.trim(), protocol: $("#ai-protocol").value, model: $("#ai-model").value.trim(), service_tier: $("#ai-tier").value, auto_generate: $("#ai-auto").checked, prompt: $("#ai-prompt").value, max_log_chars: Number($("#ai-max").value) };
      if (settings.auto_generate && !state.aiSettings.auto_generate && !await confirmAction("自动生成训练规则", "新任务会自动向 AI 接口发送日志并产生费用，每任务首次调用。确认启用？")) return false;
      settings.pricing = { model: settings.model, base_url: settings.base_url };
      for (const [key, id] of [["input", "price-input"], ["output", "price-output"], ["cached", "price-cached"], ["write", "price-write"], ["flex_multiplier", "price-flex"], ["usd_cny", "price-cny"]]) settings.pricing[key] = $("#" + id).value === "" ? null : Number($("#" + id).value);
      await call("ai.save", { settings, key: $("#ai-key").value }, submitter);
      $("#ai-key").value = "";
      state.aiSettings = settings;
      toast("AI 设置已保存。");
    }
    await refresh();
    state.dirtyForms.delete(event.target);
    if (event.target.id === "server-form") renderSettingsContent();
    return true;
  } catch (_) { return false; /* 保持表单内容，凭据只存在于当前输入框和本次IPC调用。 */ }
}
document.addEventListener("submit", (event) => {
  state.formSavePromise = saveSettingsForm(event);
});

document.addEventListener("change", async (event) => {
  const form = event.target.closest("form");
  const autoPin = event.target.id === "server-pinned" && state.snapshot.servers.some(server => server.id === state.editingServer);
  if (form && !autoPin && !["theme-choice", "setup-theme"].includes(event.target.id)) state.dirtyForms.add(form);
  if (event.target.id === "monitor-entry") state.monitorEntry = event.target.value;
  if (event.target.id === "network-view") {
    state.networkView = event.target.value;
    render();
  }
  if (event.target.id === "network-auto") state.networkAutomatic = event.target.checked;
  if (event.target.id === "monitor-live" && !event.target.checked) $("#monitor-convert").checked = false;
  if (event.target.id === "telemetry-password" && event.target.value) $("#telemetry-enabled").checked = true;
  if (["theme-choice", "setup-theme"].includes(event.target.id)) {
    try {
      const theme = event.target.value;
      await call("preferences.save", { preferences: { ui: { theme } } });
      applyTheme(theme);
      await refresh();
      toast("主题已保存。");
    } catch (_) { event.target.value = state.snapshot.theme; }
  }
  if (event.target.id === "server-pinned" && state.editingServer) {
    const saved = state.snapshot.servers.find((server) => server.id === state.editingServer);
    if (!saved) return;
    const { status, snapshot, ...server } = saved;
    try {
      await call("servers.save", { server: { ...server, pinned: event.target.checked } });
      await refresh();
      toast(event.target.checked ? "已固定到导航栏。" : "已取消固定，监控继续。");
    } catch (_) { event.target.checked = saved.pinned; }
  }
});

document.addEventListener("input", (event) => {
  const form = event.target.closest("form");
  if (form) state.dirtyForms.add(form);
  if (event.target.id === "network-filter") {
    state.networkFilter = event.target.value;
    render();
  }
});
document.addEventListener("keydown", (event) => {
  const menu = event.altKey && !event.ctrlKey && !event.metaKey
    ? { f: "file", e: "edit", v: "view", h: "help" }[event.key.toLowerCase()] : null;
  if (menu) {
    event.preventDefault();
    $("#app-menu-" + menu).click();
    return;
  }
  if (event.target.dataset.networkId && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    event.target.click();
  }
});
for (const trigger of document.querySelectorAll(".titlebar-menu")) {
  trigger.addEventListener("mousedown", (event) => event.preventDefault()); // 保留编辑框焦点，让原生复制、粘贴和撤销作用于当前输入。
  trigger.addEventListener("click", async () => {
    const bounds = trigger.getBoundingClientRect();
    trigger.setAttribute("aria-expanded", "true");
    try {
      await call("desktop.menu", { menu: trigger.dataset.menu, x: Math.round(bounds.left), y: Math.round(bounds.bottom) });
    } catch (_) { /* 原生菜单失败沿用统一错误反馈。 */ }
    finally { trigger.setAttribute("aria-expanded", "false"); }
  });
}
$("#settings-nav").addEventListener("click", () => navigate("settings"));
$("#network-nav").addEventListener("click", () => navigate("network"));
$("#dialog-close").addEventListener("click", () => $("#detail-dialog").close());
$("#detail-dialog").addEventListener("close", () => { state.dialogGeneration += 1; });
$("#setup-dialog").addEventListener("cancel", (event) => { event.preventDefault(); void finishSetup(false).catch(() => {}); });
$("#update-close").addEventListener("click", () => { if (!$("#update-close").disabled) $("#update-dialog").close(); });
$("#update-dialog").addEventListener("cancel", (event) => {
  if (["downloading", "restarting"].includes(state.updates?.phase)) event.preventDefault();
});
$("#theme-toggle").addEventListener("click", async () => {
  try {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    await call("preferences.save", { preferences: { ui: { theme } } });
    applyTheme(theme);
    await refresh();
    if (state.page === "settings" && $("#theme-choice")) $("#theme-choice").value = theme;
  } catch (_) { /* 保存失败保留原主题。 */ }
});

if (!api) {
  $("#backend-status").textContent = "桌面通信不可用";
  $("#page").innerHTML = empty("请通过 Electron 入口启动应用", "当前页面没有加载桌面通信接口。");
} else {
  api.onEvent((event) => {
    const name = event.event || event.type;
    const message = event.data?.message || event.message;
    if (name === "update") updateStatus(event.data);
    if (name === "check-update") checkUpdates().catch(() => {});
    if (name === "request-update") requestUpdate().catch(() => {});
    if (name === "navigate" && ["platform", "network", "settings"].includes(event.data?.page)) {
      navigate(event.data.page);
    }
    if (name === "toggle-theme") $("#theme-toggle").click();
    if (name === "closing") {
      state.desktopClosing = true;
      state.desktopMessage = message || "正在停止采集并等待 AI 用量入账，请稍候…";
      document.body.classList.add("closing");
    }
    if (name === "close.cancelled") {
      state.desktopClosing = false;
      state.desktopMessage = "";
      document.body.classList.remove("closing");
      toast(message || "请先完成正在进行的操作后再退出。", true);
    }
    if (["error", "backend.error", "backend.exit"].includes(name)) {
      state.desktopMessage = message || "Python 后台连接已结束，请重新启动应用。";
      $("#backend-status").textContent = "后台连接异常";
      toast(state.desktopMessage, true);
    }
    $("#global-status").textContent = state.desktopMessage || state.snapshot?.worker?.status || "后台就绪";
  });
  refresh();
  call("desktop.update.status").then(updateStatus).catch(() => {});
  setInterval(refresh, 1500); // 读取后台内存状态，实际平台与SSH采样由Python服务按用户间隔调度。
  setInterval(() => {
    if (state.page === "network" && state.networkAutomatic) refreshNetwork().catch(() => {});
  }, 3000);
}
