const MENUS = [
  "总览",
  "运行记录",
  "触发配置",
  "回复策略",
  "模型与运行",
  "工具与集成",
  "存储配置",
  "部署状态",
  "审计日志"
];

const SUBTITLES = {
  "总览": "服务健康、今日处理和关键状态摘要",
  "运行记录": "每次 agent 处理工单的完整日志中心",
  "触发配置": "webhook、轮询、@mention 和触发判定规则",
  "回复策略": "自动 public reply、ask、handoff 和等待策略",
  "模型与运行": "模型、Base URL、API key、并发和运行护栏",
  "工具与集成": "Zendesk、DB、SigNoz、GitHub、Docs、Sandbox 等远程服务",
  "存储配置": "配置运行记录、审计日志、附件缓存和配置持久化路径",
  "部署状态": "镜像、服务状态、重载状态和后台访问方式",
  "审计日志": "所有配置、密钥、登录方式和部署变更记录"
};

const INTEGRATIONS = [
  ["public_reply_browser_relay", "Browser Relay", "客户可见 public reply"],
  ["zendesk_mcp", "Zendesk MCP", "工单读取、内部评论、标签和关注"],
  ["db_gateway", "DB Gateway", "生产库只读查询"],
  ["signoz", "SigNoz MCP", "日志、指标、trace 和 ClickHouse 查询"],
  ["source_mcp", "Source MCP", "线上源码搜索和读取"],
  ["github_mcp", "GitHub MCP", "PR、Issue、revert 和 follow-up 校验"],
  ["docs_tool_service", "Docs Service", "官方文档缓存和限域读取"],
  ["notification_service", "Notification Service", "飞书通知和重试"],
  ["sandbox_worker", "Sandbox Worker", "隔离文件读写、搜索和执行"]
];

const state = {
  activeMenu: "总览",
  schema: { fields: [], groups: [] },
  values: {},
  runRecords: [],
  runRecordsPayload: null,
  runDisplayRecords: [],
  runLimit: 80,
  expandedRuns: new Set(),
  auditLogs: [],
  deployStatus: null,
  runFilters: {},
  auditFilters: {},
  integrationChecks: {}
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options
  });
  const data = await response.json();
  if (response.status === 401) {
    showLogin();
    throw new Error("unauthorized");
  }
  if (response.status === 403 && data.error === "password_change_required") {
    showPasswordChange();
    throw new Error("password_change_required");
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "request_failed");
  }
  return data;
}

function showLogin() {
  document.getElementById("login").classList.remove("hidden");
  document.getElementById("password-change").classList.add("hidden");
  document.getElementById("app").classList.add("hidden");
}

function showPasswordChange() {
  document.getElementById("login").classList.add("hidden");
  document.getElementById("password-change").classList.remove("hidden");
  document.getElementById("app").classList.add("hidden");
}

function showApp() {
  document.getElementById("login").classList.add("hidden");
  document.getElementById("password-change").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
}

function showSsoHint(provider) {
  const msg = document.getElementById("login-msg");
  msg.textContent = `${provider} SSO 需要先在「部署状态」里配置并启用。`;
}

async function login() {
  const msg = document.getElementById("login-msg");
  msg.textContent = "";
  try {
    const result = await api("/admin/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: document.getElementById("login-user").value,
        password: document.getElementById("login-pass").value
      })
    });
    if (result.force_password_change) {
      document.getElementById("change-current").value = document.getElementById("login-pass").value;
      showPasswordChange();
      return;
    }
    await init();
  } catch {
    msg.textContent = "登录失败，请检查账号和密码";
  }
}

async function completePasswordChange() {
  const msg = document.getElementById("change-msg");
  const currentPassword = document.getElementById("change-current").value;
  const newPassword = document.getElementById("change-new").value;
  const confirmPassword = document.getElementById("change-confirm").value;
  msg.textContent = "";
  if (newPassword.length < 8) {
    msg.textContent = "新密码至少需要 8 位";
    return;
  }
  if (newPassword !== confirmPassword) {
    msg.textContent = "两次输入的新密码不一致";
    return;
  }
  try {
    await api("/admin/api/auth/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword
      })
    });
    document.getElementById("login-pass").value = "";
    document.getElementById("change-current").value = "";
    document.getElementById("change-new").value = "";
    document.getElementById("change-confirm").value = "";
    await init();
  } catch {
    msg.textContent = "密码修改失败，请确认当前密码是否正确";
  }
}

async function logout() {
  await api("/admin/api/auth/logout", { method: "POST", body: "{}" }).catch(() => null);
  showLogin();
}

async function init() {
  let me;
  try {
    me = await api("/admin/api/auth/me");
  } catch {
    return;
  }
  if (me.force_password_change) {
    showPasswordChange();
    return;
  }
  showApp();
  renderNav();
  setContent(loadingHtml("正在加载配置"));
  await loadConfig();
  await render();
}

async function loadConfig() {
  const [schemaResponse, effectiveResponse] = await Promise.all([
    api("/admin/api/config/schema"),
    api("/admin/api/config/effective")
  ]);
  state.schema = schemaResponse;
  state.values = effectiveResponse.values || {};
}

async function loadDeployStatus() {
  state.deployStatus = await api("/admin/api/deploy/status");
  const tag = state.deployStatus.image?.tag || "latest";
  document.getElementById("side-image-tag").textContent = `zd-agent:${tag}`;
  return state.deployStatus;
}

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  MENUS.forEach(menu => {
    const btn = document.createElement("button");
    btn.textContent = menu;
    btn.className = menu === state.activeMenu ? "active" : "";
    btn.onclick = () => {
      state.activeMenu = menu;
      renderNav();
      void render();
    };
    nav.appendChild(btn);
  });
}

async function render() {
  document.getElementById("page-title").textContent = state.activeMenu;
  document.getElementById("page-subtitle").textContent = SUBTITLES[state.activeMenu] || "";
  setPageActions();
  setContent(loadingHtml("加载中"));
  try {
    if (state.activeMenu === "总览") {
      await renderOverview();
    } else if (state.activeMenu === "运行记录") {
      await renderRunRecords();
    } else if (state.activeMenu === "审计日志") {
      await renderAuditLogs();
    } else if (state.activeMenu === "部署状态") {
      await renderDeployStatus();
    } else if (state.activeMenu === "工具与集成") {
      renderIntegrations();
    } else if (state.activeMenu === "存储配置") {
      await renderStorageConfig();
    } else {
      renderConfigPage(state.activeMenu);
    }
  } catch (err) {
    setContent(errorHtml(err.message || "加载失败"));
  }
}

function setPageActions() {
  const actions = document.getElementById("page-actions");
  const editable = !["总览", "运行记录", "审计日志"].includes(state.activeMenu);
  if (!editable) {
    actions.innerHTML = `<button class="btn secondary" onclick="render()">刷新</button>`;
    return;
  }
  actions.innerHTML = `
    <button class="btn secondary" onclick="previewChanges()">预览变更</button>
    <button class="btn primary" onclick="saveChanges()">保存配置</button>
  `;
}

function setContent(html) {
  document.getElementById("content").innerHTML = html;
}

async function renderOverview() {
  const [runs, deploy] = await Promise.all([
    loadRunRecords(20, {}),
    loadDeployStatus()
  ]);
  const summary = runs.summary || {};
  const latest = buildRunDisplayRecords(runs.items || []).slice(0, 5);
  const total = summary.today_tickets || latest.length || summary.filtered || 0;
  const errors = summary.error_events || 0;
  const successRate = total ? `${Math.max(0, Math.round((1 - errors / total) * 100))}%` : "--";
  setContent(`
    <div class="metric-grid">
      ${metricCard("Agent 服务", deploy.agent?.status === "running" ? "运行中" : "异常", "当前进程", deploy.agent?.status === "running" ? "success-text" : "warn-text")}
      ${metricCard("今日处理", total || "--", `${summary.today_events || 0} 个事件`)}
      ${metricCard("成功率", successRate, `${errors} 个错误事件`, errors ? "warn-text" : "success-text")}
      ${metricCard("配置状态", deploy.pending_restart ? "待重启" : "已同步", deploy.pending_restart ? "存在需重启配置" : "当前配置已生效", deploy.pending_restart ? "warn-text" : "success-text")}
    </div>
    <div class="overview-grid">
      <section class="card">
        <div class="card-header">
          <span>最近运行</span>
          <button class="btn secondary" onclick="goMenu('运行记录')">查看全部</button>
        </div>
        <div class="card-body mini-list">
          ${latest.length ? latest.map((record, index) => `
            <button class="mini-row" onclick="goRunDetail(${index})">
              <span class="event-name">${escapeHtml(record.ticketLabel)}</span>
              <span>${renderStatusBadge(record.status)}</span>
              <span class="truncate">${escapeHtml(record.summary || "查看完整日志")}</span>
            </button>
          `).join("") : emptyHtml(runRecordsEmptyText(runs))}
        </div>
      </section>
      <section class="card">
        <div class="card-header">
          <span>关键配置</span>
          <span class="chip">${deploy.pending_restart ? "待重启" : "已同步"}</span>
        </div>
        <div class="card-body kv-grid">
          <strong>镜像标签</strong><span>${escapeHtml(deploy.image?.tag || "latest")}</span>
          <strong>提交</strong><span>${escapeHtml(shortSha(deploy.image?.commit_sha) || "--")}</span>
          <strong>账号密码登录</strong><span>${deploy.admin_auth?.password_login?.enabled ? "开启" : "关闭"}</span>
          <strong>Google SSO</strong><span>${authStatus(deploy.admin_auth?.google_sso)}</span>
          <strong>飞书 SSO</strong><span>${authStatus(deploy.admin_auth?.feishu_sso)}</span>
        </div>
      </section>
    </div>
  `);
  state.runDisplayRecords = latest;
}

function goMenu(menu) {
  state.activeMenu = menu;
  renderNav();
  void render();
}

function goRunDetail(index) {
  const key = state.runDisplayRecords[index]?.key;
  if (!key) return;
  state.activeMenu = "运行记录";
  renderNav();
  void renderRunRecords().then(() => {
    const targetIndex = state.runDisplayRecords.findIndex(record => record.key === key);
    openRunRecordDetail(targetIndex >= 0 ? targetIndex : 0);
  });
}

async function loadRunRecords(limit = 100, filters = state.runFilters) {
  const params = new URLSearchParams({ limit: String(limit) });
  Object.entries(filters || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const data = await api(`/admin/api/run-records?${params.toString()}`);
  state.runRecords = data.items || [];
  state.runRecordsPayload = data;
  return data;
}

async function renderRunRecords(options = {}) {
  const data = options.reload === false && state.runRecordsPayload
    ? state.runRecordsPayload
    : await loadRunRecords(state.runLimit);
  const items = data.items || [];
  let records = buildRunDisplayRecords(items);
  if (state.runFilters.status === "success") {
    records = records.filter(record => record.status === "success");
  } else if (state.runFilters.status === "failed") {
    records = records.filter(record => record.status !== "success");
  }
  state.runDisplayRecords = records;
  const summary = buildRunSummary(data, records);
  setContent(`
    <div class="metric-grid run-metrics">
      ${metricCard("总处理数", summary.totalRuns, `运行中 ${summary.running}`)}
      ${metricCard("成功率", summary.successRate, `失败 ${summary.failed} / 超时 ${summary.timeout}`, summary.failed ? "warn-text" : "success-text")}
      ${metricCard("平均耗时", summary.avgDuration, "最近匹配记录")}
      ${metricCard("Token 消耗", summary.totalTokens, `入 ${summary.inputTokens} / 出 ${summary.outputTokens}`)}
    </div>
    <section class="filter-panel">
      <input id="run-ticket" placeholder="工单号筛选" value="${escapeHtml(state.runFilters.ticket_id || "")}">
      <select id="run-status">
        <option value="">全部状态</option>
        <option value="success" ${state.runFilters.status === "success" ? "selected" : ""}>成功</option>
        <option value="failed" ${state.runFilters.status === "failed" ? "selected" : ""}>失败</option>
      </select>
      <input id="run-event" placeholder="事件" value="${escapeHtml(state.runFilters.event || "")}">
      <input id="run-module" placeholder="模块" value="${escapeHtml(state.runFilters.module || "")}">
      <button class="btn primary" onclick="applyRunFilters()">查询</button>
      <button class="btn secondary" onclick="resetRunFilters()">重置</button>
    </section>
    <section class="card run-record-card">
      <div class="card-header">
        <span>运行记录</span>
        <div class="header-actions">
          <span class="chip">显示 ${records.length} / ${data.summary?.filtered ?? records.length}</span>
          <span class="chip">JSONL ${data.jsonl_enabled ? "已开启" : "未开启"}</span>
          ${(data.summary?.filtered || 0) > state.runLimit
            ? `<button class="btn secondary compact" onclick="loadMoreRuns()">加载更多</button>`
            : ""}
        </div>
      </div>
      <div class="run-table">
        <div class="run-table-header">
          <span></span>
          <span>工单</span>
          <span>状态</span>
          <span>模型</span>
          <span>轮次</span>
          <span>Token</span>
          <span>耗时</span>
          <span>触发方式</span>
          <span>时间</span>
          <span>操作</span>
        </div>
        ${records.length ? records.map((record, index) => renderRunRecordRow(record, index)).join("") : emptyHtml(runRecordsEmptyText(data))}
      </div>
    </section>
  `);
}

function buildRunDisplayRecords(items) {
  return items.map((item, index) => {
    const status = runStatus(item);
    const rounds = extractRounds(item);
    const rawEvents = Array.isArray(item.events) ? item.events : [];
    const inputTokens = numberFromKeys(item, ["input_tokens", "prompt_tokens", "tokens_in"]);
    const outputTokens = numberFromKeys(item, ["output_tokens", "completion_tokens", "tokens_out"]);
    const totalTokens = numberFromKeys(item, ["total_tokens", "tokens", "token_count"]) || inputTokens + outputTokens;
    const durationMs = durationMsFromItem(item);
    const ticket = item.ticket_id || item.ticket || "";
    const key = [
      item._source_file || "events",
      item.session_id || "",
      item.timestamp || item.ts || index,
      item.event || "event",
      ticket || index
    ].join(":");
    return {
      key,
      raw: item,
      rawEvents,
      ticketLabel: ticket ? `#${ticket}` : "-",
      status,
      model: item.model || item.llm_model || item.provider_model || item.provider || "-",
      rounds,
      roundsCount: numberFromKeys(item, ["rounds", "round_count"]) || rounds.length || 1,
      inputTokens,
      outputTokens,
      totalTokens,
      durationMs,
      trigger: item.trigger || item.trigger_type || item.source || item.event || "-",
      time: item.timestamp || item.ts || item.time || item.created_at,
      summary: logSummary(item),
      eventCount: Number(item.event_count || rawEvents.length || 0),
      isExpanded: state.expandedRuns.has(key)
    };
  });
}

function buildRunSummary(data, records) {
  const totalRuns = records.length || data.summary?.filtered || 0;
  const failed = records.filter(record => record.status === "failed").length;
  const timeout = records.filter(record => record.status === "timeout").length;
  const running = records.filter(record => record.status === "running").length;
  const completed = Math.max(0, totalRuns - failed - timeout - running);
  const successRate = totalRuns ? `${((completed * 100) / totalRuns).toFixed(1)}%` : "--";
  const durations = records.map(record => record.durationMs).filter(value => value > 0);
  const avgMs = durations.length
    ? durations.reduce((sum, value) => sum + value, 0) / durations.length
    : 0;
  const inputTokens = records.reduce((sum, record) => sum + record.inputTokens, 0);
  const outputTokens = records.reduce((sum, record) => sum + record.outputTokens, 0);
  const totalTokens = records.reduce((sum, record) => sum + record.totalTokens, 0);
  return {
    totalRuns,
    failed,
    timeout,
    running,
    successRate,
    avgDuration: avgMs ? formatDuration(avgMs) : "--",
    inputTokens: formatTokenCount(inputTokens),
    outputTokens: formatTokenCount(outputTokens),
    totalTokens: formatTokenCount(totalTokens)
  };
}

function renderRunRecordRow(record, index) {
  const expanded = record.isExpanded;
  return `
    <div class="run-record-item">
      <div class="run-record-row">
        <button class="record-expand" aria-label="${expanded ? "收起" : "展开"}" onclick="toggleRunRecord(${index})">${expanded ? "⌄" : "›"}</button>
        <span class="ticket-cell">${escapeHtml(record.ticketLabel)}</span>
        <span>${renderStatusBadge(record.status)}</span>
        <span class="truncate muted">${escapeHtml(record.model)}</span>
        <span>${escapeHtml(record.roundsCount)}</span>
        <span class="truncate">${escapeHtml(formatTokenPair(record))}</span>
        <span>${escapeHtml(record.durationMs ? formatDuration(record.durationMs) : "--")}</span>
        <span><span class="trigger-chip" title="${escapeHtml(record.trigger)}">${escapeHtml(formatTrigger(record.trigger))}</span></span>
        <span class="muted">${escapeHtml(relativeTime(record.time))}</span>
        <button class="btn secondary compact" onclick="openRunRecordDetail(${index})">日志</button>
      </div>
      ${expanded ? renderRunRecordDetail(record, index) : ""}
    </div>
  `;
}

function renderRunRecordDetail(record, index) {
  return `
    <div class="run-record-detail">
      <div class="detail-summary">
        <span class="chip">${escapeHtml(record.model)}</span>
        <span>${escapeHtml(formatTokenPair(record))}</span>
        <span>${escapeHtml(record.durationMs ? formatDuration(record.durationMs) : "耗时未知")}</span>
        <span>${escapeHtml(record.eventCount ? `${record.eventCount} 条事件` : "无事件链路")}</span>
        <button class="btn secondary compact" onclick="openRunRecordDetail(${index})">查看完整日志</button>
      </div>
      <div class="round-timeline">
        ${record.rounds.map((round, roundIndex) => renderRound(round, roundIndex)).join("")}
      </div>
      ${renderEventTimeline(record, 8)}
    </div>
  `;
}

function renderRound(round, index) {
  const tools = round.tools || [];
  const roundNumber = round.round || round.index || index + 1;
  return `
    <div class="round-block">
      <div class="round-meta">
        <span class="round-badge">R${escapeHtml(roundNumber)}</span>
        <span>${escapeHtml(round.model || "-")}</span>
        <span>${escapeHtml(formatRoundTokens(round))}</span>
        <span>${escapeHtml(round.durationMs ? formatDuration(round.durationMs) : "--")}</span>
        <span class="chip">${escapeHtml(round.type || "tool_calls")}</span>
        <span class="chip">${tools.length} 工具</span>
      </div>
      <div class="tool-call-list">
        <div class="tool-call-title">工具调用</div>
        ${tools.length ? tools.map(tool => renderToolCall(tool)).join("") : `<div class="tool-call-row muted"><span>暂无工具调用</span><span></span><span></span><span></span></div>`}
      </div>
    </div>
  `;
}

function renderToolCall(tool) {
  const ok = tool.status !== "failed";
  return `
    <div class="tool-call-row">
      <span class="truncate"><span class="${ok ? "success-text" : "danger-text"}">${ok ? "✓" : "×"}</span> ${escapeHtml(tool.name || "unknown_tool")}</span>
      <span>${escapeHtml(tool.duration || "--")}</span>
      <span class="${ok ? "muted" : "danger-text"} truncate">${escapeHtml(tool.message || tool.error || "")}</span>
      <span>›</span>
    </div>
  `;
}

function renderEventTimeline(record, limit = 0) {
  const events = record.rawEvents || [];
  if (!events.length) return "";
  const visibleEvents = limit > 0 ? events.slice(-limit) : events;
  const hiddenCount = Math.max(0, events.length - visibleEvents.length);
  return `
    <div class="event-trace">
      <div class="event-trace-title">
        <span>事件链路</span>
        ${hiddenCount ? `<span class="muted">已省略较早 ${hiddenCount} 条，完整内容见日志抽屉</span>` : ""}
      </div>
      ${visibleEvents.map(event => renderEventTraceRow(event)).join("")}
    </div>
  `;
}

function renderEventTraceRow(event) {
  const status = runStatus(event);
  const eventName = event.event || "event";
  const detail = event.error || event.reason || event.status || event.module || event._source_file || "";
  return `
    <div class="event-trace-row">
      <span class="event-time">${escapeHtml(formatTime(event.timestamp || event.ts || event.time || event.created_at))}</span>
      <span class="event-dot ${status}"></span>
      <span class="event-name truncate">${escapeHtml(eventName)}</span>
      <span class="muted truncate">${escapeHtml(detail)}</span>
    </div>
  `;
}

function extractRounds(item) {
  const directRounds = Array.isArray(item.rounds)
    ? item.rounds
    : Array.isArray(item.llm_rounds)
      ? item.llm_rounds
      : [];
  if (directRounds.length) {
    return directRounds.map(round => normalizeRound(round, item));
  }
  const traceTools = extractTraceTools(item.events || []);
  if (traceTools.length) {
    return [normalizeRound({ round: 1, type: "tool_calls", tools: traceTools }, item)];
  }
  return [normalizeRound(item, item)];
}

function normalizeRound(round, parent) {
  const tools = extractToolCalls(round);
  if (!tools.length) {
    const name = round.tool || round.tool_name || parent.tool || parent.event;
    if (name) {
      tools.push({
        name,
        status: runStatus(round),
        duration: formatDuration(durationMsFromItem(round)),
        message: round.error || round.reason || round.status || ""
      });
    }
  }
  return {
    model: round.model || round.llm_model || parent.model || parent.llm_model || "-",
    inputTokens: numberFromKeys(round, ["input_tokens", "prompt_tokens", "tokens_in"]),
    outputTokens: numberFromKeys(round, ["output_tokens", "completion_tokens", "tokens_out"]),
    totalTokens: numberFromKeys(round, ["total_tokens", "tokens", "token_count"]),
    durationMs: durationMsFromItem(round),
    type: round.type || round.finish_reason || (tools.length ? "tool_calls" : parent.event || "event"),
    tools
  };
}

function extractToolCalls(item) {
  const rawTools = Array.isArray(item.tool_calls)
    ? item.tool_calls
    : Array.isArray(item.tools)
      ? item.tools
      : [];
  return rawTools.map(tool => ({
    name: tool.name || tool.tool || tool.tool_name || tool.function?.name || "unknown_tool",
    status: runStatus(tool),
    duration: tool.duration || tool.elapsed || formatDuration(durationMsFromItem(tool)),
    message: tool.error || tool.message || tool.summary || ""
  }));
}

function extractTraceTools(events) {
  if (!Array.isArray(events)) return [];
  return events
    .filter(event => event && typeof event === "object" && (event.event === "tool_call" || event.tool || event.tool_name))
    .map(event => ({
      name: event.tool || event.tool_name || event.event || "unknown_tool",
      status: runStatus(event),
      duration: event.duration || event.elapsed || formatDuration(durationMsFromItem(event)),
      message: event.error || event.status || event.module || ""
    }));
}

function toggleRunRecord(index) {
  const record = state.runDisplayRecords[index];
  if (!record) return;
  if (state.expandedRuns.has(record.key)) {
    state.expandedRuns.delete(record.key);
  } else {
    state.expandedRuns.add(record.key);
  }
  void renderRunRecords({ reload: false });
}

function applyRunFilters() {
  const status = document.getElementById("run-status").value;
  state.runLimit = 80;
  state.runFilters = {
    ticket_id: document.getElementById("run-ticket").value.trim(),
    event: document.getElementById("run-event").value.trim(),
    module: document.getElementById("run-module").value.trim(),
    errors_only: status === "failed" ? "true" : "",
    status
  };
  state.expandedRuns.clear();
  void renderRunRecords();
}

function resetRunFilters() {
  state.runFilters = {};
  state.runLimit = 80;
  state.expandedRuns.clear();
  void renderRunRecords();
}

function loadMoreRuns() {
  state.runLimit += 80;
  void renderRunRecords();
}

function renderConfigPage(menu) {
  const fields = fieldsFor(menu);
  if (!fields.length) {
    setContent(emptyHtml("当前模块没有可编辑配置"));
    return;
  }
  setContent(`${renderModuleOverview(menu, fields)}${renderForm(menu, fields)}`);
}

async function renderStorageConfig() {
  const deploy = await loadDeployStatus().catch(() => null);
  const fields = fieldsFor("存储配置");
  const baseDir = fieldValue("resources.base_dir") || deploy?.storage?.base_dir || "-";
  const attachmentsDir = fieldValue("resources.attachments_dir") || "-";
  const stateDir = fieldValue("resources.state_dir") || deploy?.storage?.state_dir || "-";
  const logDir = deploy?.storage?.log_dir || "-";
  setContent(`
    <div class="metric-grid storage-metrics">
      ${metricCard("运行记录 JSONL", "已启用", "单次处理日志", "success-text")}
      ${metricCard("审计日志", "已启用", "配置和密钥变更", "success-text")}
      ${metricCard("附件缓存", "30 天", "按策略清理")}
      ${metricCard("配置存储", "已同步", "overrides / secrets", "success-text")}
    </div>
    <div class="storage-grid">
      ${storageCard("运行记录存储", "保存 agent 每次处理工单的事件、轮次和工具调用摘要。", logDir, "JSONL")}
      ${storageCard("审计日志存储", "记录所有配置保存、密钥替换、登录方式和连通性检测。", `${stateDir}/admin_audit.jsonl`, "JSONL")}
      ${storageCard("附件与截图缓存", "保存 Zendesk 附件、图片和处理过程中的临时截图。", attachmentsDir, "缓存")}
      ${storageCard("配置持久化", "保存非密钥覆盖配置和单独加权限的密钥文件。", `${stateDir}/admin_overrides.yaml`, "YAML")}
    </div>
    ${renderForm("存储配置", fields)}
    <section class="card">
      <div class="card-header">
        <span>存储检查结果</span>
        <span class="chip">读取当前配置</span>
      </div>
      <div class="card-body">
        <div class="storage-check-list">
          ${storageCheck("资源根目录", baseDir, "可配置")}
          ${storageCheck("运行日志目录", logDir, "只读展示")}
          ${storageCheck("状态目录", stateDir, "需重启生效")}
          ${storageCheck("附件目录", attachmentsDir, "立即生效")}
        </div>
      </div>
    </section>
  `);
}

function storageCard(title, description, path, type) {
  return `
    <section class="storage-card">
      <div class="storage-card-head">
        <strong>${escapeHtml(title)}</strong>
        <span class="chip">${escapeHtml(type)}</span>
      </div>
      <p>${escapeHtml(description)}</p>
      <code>${escapeHtml(path || "-")}</code>
    </section>
  `;
}

function storageCheck(label, value, status) {
  return `
    <div class="storage-check-row">
      <span>${escapeHtml(label)}</span>
      <code>${escapeHtml(value || "-")}</code>
      <span class="muted">${escapeHtml(status)}</span>
    </div>
  `;
}

function renderIntegrations() {
  const fields = fieldsFor("工具与集成");
  setContent(`
    <section class="card">
      <div class="card-header">
        <span>工具与集成</span>
        <span class="chip">检测会写入审计日志</span>
      </div>
      <div class="card-body integration-grid">
        ${INTEGRATIONS.map(([name, label, description]) => renderIntegrationCard(name, label, description)).join("")}
      </div>
    </section>
    ${renderForm("工具与集成", fields)}
  `);
}

function renderIntegrationCard(name, label, description) {
  const result = state.integrationChecks[name];
  const status = result
    ? `${result.status} · ${result.detail || ""}`
    : "尚未检测";
  const cls = result?.reachable ? "success-text" : result ? "warn-text" : "muted";
  return `
    <div class="integration-card">
      <div>
        <strong>${escapeHtml(label)}</strong>
        <div class="muted">${escapeHtml(description)}</div>
      </div>
      <div class="${cls}">${escapeHtml(status)}</div>
      <button class="btn secondary" onclick="checkIntegration('${name}')">测试连接</button>
    </div>
  `;
}

async function checkIntegration(name) {
  state.integrationChecks[name] = { status: "checking", detail: "检测中", reachable: false };
  renderIntegrations();
  try {
    const result = await api("/admin/api/integrations/check", {
      method: "POST",
      body: JSON.stringify({ integration: name })
    });
    state.integrationChecks[name] = result;
  } catch (err) {
    state.integrationChecks[name] = {
      status: "failed",
      detail: err.message || "检测失败",
      reachable: false
    };
  }
  renderIntegrations();
}

function fieldsFor(menu) {
  return (state.schema.fields || []).filter(field => field.group === menu);
}

function renderForm(title, fields) {
  return `
    <section class="card config-card">
      <div class="card-header">
        <span>${escapeHtml(title)}</span>
        <span class="chip">保存前会生成审计记录</span>
      </div>
      <div class="card-body form-grid">
        ${fields.map(field => `
          <label for="field-${field.path}">${escapeHtml(field.label)}</label>
          ${renderInput(field)}
          <span class="field-help ${field.restart_required ? "warn-text" : "muted"}">
            ${field.secret ? "可替换密钥" : field.restart_required ? "需重启生效" : field.protected ? "受保护" : "立即生效"}
          </span>
        `).join("")}
      </div>
    </section>
  `;
}

function renderModuleOverview(menu, fields) {
  const secretCount = fields.filter(field => field.secret).length;
  const restartCount = fields.filter(field => field.restart_required).length;
  const immediateCount = Math.max(0, fields.length - restartCount);
  return `
    <div class="module-overview">
      ${metricCard("可配置项", fields.length, `${menu} 当前纳入后台管理`)}
      ${metricCard("立即生效", immediateCount, "保存后写入运行配置", "success-text")}
      ${metricCard("需重启", restartCount, "路径、监听和部分轮询配置", restartCount ? "warn-text" : "")}
      ${metricCard("密钥字段", secretCount, "只显示配置状态和指纹")}
    </div>
  `;
}

function renderInput(field) {
  const value = fieldValue(field.path);
  const id = `field-${field.path}`;
  if (field.secret) {
    return `
      <div class="secret-input">
        <input id="${id}" disabled value="${escapeHtml(value)}">
        <input data-path="${field.path}" type="password" placeholder="输入新密钥后保存">
      </div>
    `;
  }
  if (field.input === "boolean") {
    const checked = state.values[field.path] === true || state.values[field.path] === "true";
    return `
      <select id="${id}" data-path="${field.path}">
        <option value="true" ${checked ? "selected" : ""}>开启</option>
        <option value="false" ${!checked ? "selected" : ""}>关闭</option>
      </select>
    `;
  }
  if (field.input === "textarea") {
    return `<textarea id="${id}" data-path="${field.path}" class="prompt-textarea" rows="7">${escapeHtml(value)}</textarea>`;
  }
  const inputType = field.input === "number" ? "number" : "text";
  return `<input id="${id}" data-path="${field.path}" type="${inputType}" value="${escapeHtml(value)}">`;
}

function fieldValue(path) {
  const value = state.values[path];
  if (value && typeof value === "object" && "configured" in value) {
    return value.configured ? `已配置 · ${value.fingerprint || ""}` : "未配置";
  }
  if (value === null || value === undefined) return "";
  return value;
}

function collectChanges() {
  const changes = {};
  document.querySelectorAll("[data-path]").forEach(input => {
    const path = input.getAttribute("data-path");
    if (!path) return;
    if (input.type === "password" && !input.value) return;
    const oldValue = state.values[path];
    const nextValue = input.value;
    if (typeof oldValue === "object" && oldValue && "configured" in oldValue) {
      if (nextValue) changes[path] = nextValue;
    } else if (String(oldValue ?? "") !== String(nextValue)) {
      changes[path] = nextValue;
    }
  });
  return changes;
}

async function previewChanges() {
  const changes = collectChanges();
  if (!Object.keys(changes).length) {
    showModal("预览变更", emptyHtml("没有检测到配置变更"), [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
    return;
  }
  try {
    const result = await api("/admin/api/config/preview", {
      method: "POST",
      body: JSON.stringify({ changes, reason: "admin console preview" })
    });
    showModal("配置变更预览", renderDiff(result.diff || [], result.restart_required), [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
  } catch (err) {
    showModal("配置变更预览", errorHtml(err.message || "预览失败"), [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
  }
}

async function saveChanges() {
  const changes = collectChanges();
  if (!Object.keys(changes).length) {
    showModal("保存配置", emptyHtml("没有检测到配置变更"), [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
    return;
  }
  showModal("保存配置", `
    <p class="muted">请输入本次变更原因。保存后会写入审计日志。</p>
    <textarea id="change-reason" placeholder="例如：调整 public reply 等待窗口"></textarea>
    <div id="save-error" class="danger-text"></div>
  `, [
    { label: "取消", className: "secondary", onClick: closeModal },
    { label: "保存", className: "primary", onClick: () => applyChanges(changes) }
  ]);
}

async function applyChanges(changes) {
  const reason = document.getElementById("change-reason").value.trim();
  const error = document.getElementById("save-error");
  if (!reason) {
    error.textContent = "变更原因不能为空";
    return;
  }
  try {
    const result = await api("/admin/api/config/apply", {
      method: "POST",
      body: JSON.stringify({ changes, reason })
    });
    await loadConfig();
    closeModal();
    await render();
    showModal("保存成功", `
      <p>配置已保存。</p>
      <p><strong>Audit ID</strong> ${escapeHtml(result.audit_id || "-")}</p>
      ${result.restart_required ? "<p class=\"warn-text\">本次变更包含需重启生效的配置。</p>" : ""}
    `, [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
  } catch (err) {
    error.textContent = err.message || "保存失败";
  }
}

function renderDiff(diff, restartRequired) {
  if (!diff.length) {
    return emptyHtml("没有产生有效变更");
  }
  return `
    ${restartRequired ? "<p class=\"warn-text\">包含需重启生效的配置。</p>" : ""}
    <div class="table">
      <div class="table-grid audit-table">
        <div class="table-header audit-table">
          <span>模块</span><span>字段</span><span>旧值</span><span>新值</span><span>生效</span>
        </div>
        ${diff.map(item => `
          <div class="table-row audit-table">
            <span class="cell">${escapeHtml(item.group || "-")}</span>
            <span class="cell">${escapeHtml(item.label || item.path)}</span>
            <span class="cell">${escapeHtml(formatValue(item.old))}</span>
            <span class="cell">${escapeHtml(formatValue(item.new))}</span>
            <span class="cell">${item.restart_required ? "需重启" : "立即"}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

async function renderDeployStatus() {
  const deploy = await loadDeployStatus();
  setContent(`
    <div class="metric-grid">
      ${metricCard("Agent 服务", deploy.agent?.status || "unknown", "当前进程", deploy.agent?.status === "running" ? "success-text" : "warn-text")}
      ${metricCard("镜像标签", deploy.image?.tag || "latest", deploy.image?.name || "zd-agent")}
      ${metricCard("提交", shortSha(deploy.image?.commit_sha) || "--", "当前部署版本")}
      ${metricCard("待重启", deploy.pending_restart ? "有" : "无", deploy.pending_restart ? "存在需重启配置" : "配置已生效", deploy.pending_restart ? "warn-text" : "success-text")}
    </div>
    <div class="auth-method-grid">
      ${authMethodCard("账号密码登录", deploy.admin_auth?.password_login?.enabled, "默认账号 admin，初始密码 admin123")}
      ${authMethodCard("Google SSO", deploy.admin_auth?.google_sso?.enabled, authStatus(deploy.admin_auth?.google_sso))}
      ${authMethodCard("飞书 SSO", deploy.admin_auth?.feishu_sso?.enabled, authStatus(deploy.admin_auth?.feishu_sso))}
    </div>
    <section class="card">
      <div class="card-header">部署路径</div>
      <div class="card-body kv-grid">
        <strong>资源目录</strong><span>${escapeHtml(deploy.storage?.base_dir || "-")}</span>
        <strong>状态目录</strong><span>${escapeHtml(deploy.storage?.state_dir || "-")}</span>
        <strong>日志目录</strong><span>${escapeHtml(deploy.storage?.log_dir || "-")}</span>
      </div>
    </section>
    ${renderForm("部署状态", fieldsFor("部署状态"))}
  `);
}

function authMethodCard(label, enabled, description) {
  return `
    <section class="auth-method-card">
      <div>
        <strong>${escapeHtml(label)}</strong>
        <p>${escapeHtml(description || "-")}</p>
      </div>
      <span class="chip ${enabled ? "enabled" : ""}">${enabled ? "已开启" : "未开启"}</span>
    </section>
  `;
}

function authStatus(item) {
  if (!item) return "-";
  if (!item.enabled) return "未开启";
  return item.configured ? "已开启且已配置" : "已开启但缺少配置";
}

async function renderAuditLogs() {
  const params = new URLSearchParams({ limit: "200" });
  Object.entries(state.auditFilters || {}).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const data = await api(`/admin/api/audit-logs?${params.toString()}`);
  state.auditLogs = data.items || [];
  const summary = data.summary || {};
  setContent(`
    <div class="metric-grid">
      ${metricCard("匹配记录", summary.filtered ?? 0, "当前筛选")}
      ${metricCard("全部记录", summary.total ?? 0, "审计总量")}
      ${metricCard("最近操作", state.auditLogs[0]?.action || "--", "最新变更")}
      ${metricCard("最近操作人", state.auditLogs[0]?.actor || "--", "admin")}
    </div>
    <section class="filter-panel">
      <input id="audit-action" placeholder="动作" value="${escapeHtml(state.auditFilters.action || "")}">
      <input id="audit-actor" placeholder="操作人" value="${escapeHtml(state.auditFilters.actor || "")}">
      <button class="btn primary" onclick="applyAuditFilters()">查询</button>
      <button class="btn secondary" onclick="resetAuditFilters()">重置</button>
    </section>
    <section class="card">
      <div class="card-header">
        <span>审计日志</span>
        <span class="chip">点击行查看详情</span>
      </div>
      <div class="card-body table">
        <div class="table-grid audit-table">
          <div class="table-header audit-table">
            <span>时间</span><span>操作人</span><span>动作</span><span>摘要</span><span>结果</span>
          </div>
          ${state.auditLogs.map((item, index) => `
            <button class="table-row row-button audit-table" onclick="openAuditDetail(${index})">
              <span class="cell">${escapeHtml(formatAuditTime(item.ts))}</span>
              <span class="cell">${escapeHtml(item.actor || "-")}</span>
              <span class="cell event-name">${escapeHtml(item.action || "-")}</span>
              <span class="cell">${escapeHtml(auditSummary(item))}</span>
              <span class="cell success-text">${escapeHtml(item.status || "success")}</span>
            </button>
          `).join("") || emptyHtml("暂无审计记录")}
        </div>
      </div>
    </section>
  `);
}

function applyAuditFilters() {
  state.auditFilters = {
    action: document.getElementById("audit-action").value.trim(),
    actor: document.getElementById("audit-actor").value.trim()
  };
  void renderAuditLogs();
}

function resetAuditFilters() {
  state.auditFilters = {};
  void renderAuditLogs();
}

function openRunRecordDetail(index) {
  const record = state.runDisplayRecords[index];
  if (!record) return;
  const item = record.raw;
  openDrawer(
    `${record.ticketLabel} 运行日志`,
    `${formatTime(record.time)} · ${record.eventCount || 0} 条事件`,
    `
      <div class="kv-grid">
        <strong>工单</strong><span>${escapeHtml(item.ticket_id || item.ticket || "-")}</span>
        <strong>Session</strong><span>${escapeHtml(item.session_id || "-")}</span>
        <strong>状态</strong><span>${renderStatusBadge(record.status)}</span>
        <strong>轮次</strong><span>${escapeHtml(record.roundsCount)}</span>
        <strong>Token</strong><span>${escapeHtml(formatTokenPair(record))}</span>
        <strong>耗时</strong><span>${escapeHtml(record.durationMs ? formatDuration(record.durationMs) : "-")}</span>
        <strong>模块</strong><span>${escapeHtml(item.module || "-")}</span>
        <strong>来源文件</strong><span>${escapeHtml(item._source_file || "-")}</span>
      </div>
      ${renderEventTimeline(record)}
      <pre>${escapeHtml(JSON.stringify(redactLogValue(item), null, 2))}</pre>
    `
  );
}

function openAuditDetail(index) {
  const item = state.auditLogs[index];
  if (!item) return;
  openDrawer(
    item.action || "审计详情",
    formatAuditTime(item.ts),
    `
      <div class="kv-grid">
        <strong>操作人</strong><span>${escapeHtml(item.actor || "-")}</span>
        <strong>来源 IP</strong><span>${escapeHtml(item.ip || "-")}</span>
        <strong>原因</strong><span>${escapeHtml(item.reason || "-")}</span>
        <strong>结果</strong><span>${escapeHtml(item.status || "success")}</span>
      </div>
      <pre>${escapeHtml(JSON.stringify(redactLogValue(item), null, 2))}</pre>
    `
  );
}

function openDrawer(title, subtitle, body) {
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("drawer-subtitle").textContent = subtitle || "";
  document.getElementById("drawer-body").innerHTML = body;
  document.getElementById("detail-drawer").classList.remove("hidden");
}

function closeDrawer() {
  document.getElementById("detail-drawer").classList.add("hidden");
}

function showModal(title, body, actions) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = body;
  const footer = document.getElementById("modal-actions");
  footer.innerHTML = "";
  actions.forEach(action => {
    const button = document.createElement("button");
    button.className = `btn ${action.className || "secondary"}`;
    button.textContent = action.label;
    button.onclick = action.onClick;
    footer.appendChild(button);
  });
  document.getElementById("modal-backdrop").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-backdrop").classList.add("hidden");
}

function metricCard(label, value, description = "", className = "") {
  return `
    <div class="metric-card">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value ${className}">${escapeHtml(value)}</div>
      <div class="metric-description">${escapeHtml(description)}</div>
    </div>
  `;
}

function loadingHtml(text) {
  return `
    <div class="loading-state" role="status" aria-live="polite">
      <div class="loading-copy">
        <strong>${escapeHtml(text)}</strong>
        <span>正在读取线上配置和运行数据</span>
      </div>
      <div class="skeleton-grid">
        <span></span><span></span><span></span><span></span>
      </div>
      <div class="skeleton-panel">
        <span></span><span></span><span></span><span></span>
      </div>
    </div>
  `;
}

function errorHtml(text) {
  return `<div class="empty-state danger-text">${escapeHtml(text)}</div>`;
}

function emptyHtml(text) {
  return `<div class="empty-state">${escapeHtml(text)}</div>`;
}

function runRecordsEmptyText(data) {
  return data.exists
    ? "当前日志目录暂无匹配的 JSONL 运行事件"
    : `日志目录不存在：${data.log_dir || ""}`;
}

function runStatus(item) {
  const text = [
    item.event,
    item.status,
    item.level,
    item.error,
    item.exception,
    item.traceback
  ].filter(Boolean).join(" ").toLowerCase();
  if (text.includes("timeout")) return "timeout";
  if (["error", "failed", "failure", "exception", "traceback"].some(token => text.includes(token))) {
    return "failed";
  }
  if (text.includes("running") || text.includes("started") || text.includes("processing")) return "running";
  return "success";
}

function renderStatusBadge(status) {
  const labels = {
    success: "成功",
    failed: "失败",
    timeout: "超时",
    running: "运行中"
  };
  return `<span class="status-badge ${status}">${labels[status] || status}</span>`;
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleString();
  }
  return String(value).replace("T", " ");
}

function relativeTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return formatTime(value);
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return formatTime(value);
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `大约 ${hours} 小时前`;
  return formatTime(value);
}

function formatAuditTime(value) {
  if (!value) return "-";
  return new Date(Number(value) * 1000).toLocaleString();
}

function formatValue(value) {
  if (value && typeof value === "object") {
    return JSON.stringify(value);
  }
  if (value === undefined || value === null || value === "") return "-";
  return String(value);
}

function formatTrigger(value) {
  const raw = String(value || "-");
  const labels = {
    messaging_webhook: "消息 webhook",
    zendesk_webhook: "工单 webhook",
    webhook: "webhook",
    "webhook-mention": "@mention",
    mention_poller: "@mention 轮询",
    silence_waker: "静默唤醒",
    waiting_poller: "等待轮询",
    session_finished: "会话完成",
    agent_done: "处理完成"
  };
  return labels[raw] || raw;
}

function logSummary(item) {
  const parts = [];
  ["status", "reason", "error", "reply_type", "tool", "rounds", "_source_file"].forEach(key => {
    if (item[key] !== undefined && item[key] !== null && item[key] !== "") {
      const value = item[key];
      if (Array.isArray(value)) {
        parts.push(`${key}: ${value.length}`);
      } else if (value && typeof value === "object") {
        parts.push(`${key}: ${Object.keys(value).length}`);
      } else {
        parts.push(`${key}: ${formatValue(value)}`);
      }
    }
  });
  return parts.join(" · ");
}

function auditSummary(item) {
  const paths = (item.diff || []).map(diff => diff.path).filter(Boolean).join(", ");
  return paths || item.reason || "-";
}

function shortSha(value) {
  return value ? String(value).slice(0, 7) : "";
}

function numberFromKeys(item, keys) {
  for (const key of keys) {
    const value = Number(item[key]);
    if (Number.isFinite(value) && value > 0) return value;
  }
  return 0;
}

function durationMsFromItem(item) {
  const ms = numberFromKeys(item, ["duration_ms", "elapsed_ms", "latency_ms"]);
  if (ms) return ms;
  const seconds = numberFromKeys(item, ["duration_seconds", "elapsed_seconds", "latency_seconds"]);
  if (seconds) return seconds * 1000;
  return 0;
}

function formatDuration(ms) {
  if (!ms) return "--";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  return `${(seconds / 60).toFixed(1)}min`;
}

function formatTokenCount(value) {
  if (!value) return "--";
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

function formatTokenPair(record) {
  const total = record.totalTokens || record.inputTokens + record.outputTokens;
  if (!total && !record.inputTokens && !record.outputTokens) return "--";
  return `${formatTokenCount(total)} / ${formatTokenCount(record.outputTokens || total)}`;
}

function formatRoundTokens(round) {
  const total = round.totalTokens || round.inputTokens + round.outputTokens;
  if (!total && !round.inputTokens && !round.outputTokens) return "--";
  return `${formatTokenCount(round.inputTokens)} / ${formatTokenCount(round.outputTokens || total)} tokens`;
}

function redactLogValue(value, key = "") {
  if (Array.isArray(value)) {
    return value.map(item => redactLogValue(item));
  }
  if (value && typeof value === "object") {
    const out = {};
    Object.entries(value).forEach(([entryKey, entryValue]) => {
      out[entryKey] = redactLogValue(entryValue, entryKey);
    });
    return out;
  }
  if (/password|token|secret|api[_-]?key|apikey|authorization/i.test(key)) {
    return value ? "[REDACTED]" : value;
  }
  return value;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    closeDrawer();
    closeModal();
  }
});

init();
