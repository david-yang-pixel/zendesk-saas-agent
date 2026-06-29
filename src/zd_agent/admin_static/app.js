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
  "工具与集成": "Zendesk、DB、SigNoz、GitHub、Docs、Sandbox 等服务",
  "存储配置": "目录、附件缓存、状态文件和清理策略",
  "部署状态": "镜像、服务状态、重载状态和后台访问方式",
  "审计日志": "所有配置、密钥、登录方式和部署变更记录"
};

const INTEGRATIONS = [
  ["zendesk_mcp", "Zendesk MCP", "工单读取、内部评论、标签和关注"],
  ["db_gateway", "DB Gateway", "生产库只读查询"],
  ["signoz", "SigNoz MCP", "日志、指标、trace 和 ClickHouse 查询"],
  ["source_mcp", "Source MCP", "线上源码搜索和读取"],
  ["github_mcp", "GitHub MCP", "PR、Issue、revert 和 follow-up 校验"],
  ["docs_tool_service", "Docs Service", "官方文档缓存和限域读取"],
  ["notification_service", "Notification Service", "飞书通知和重试"],
  ["sandbox_worker", "Sandbox Worker", "隔离文件读写、搜索和执行"],
  ["public_reply_browser_relay", "Browser Relay", "客户可见 public reply"]
];

const state = {
  activeMenu: "总览",
  schema: { fields: [], groups: [] },
  values: {},
  runRecords: [],
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
    loadRunRecords(100, {}),
    loadDeployStatus()
  ]);
  const summary = runs.summary || {};
  const latest = (runs.items || []).slice(0, 6);
  const errorRate = summary.filtered
    ? `${Math.round((summary.error_events || 0) * 100 / summary.filtered)}%`
    : "--";
  setContent(`
    <div class="stat-grid">
      ${statCard("Agent 服务", deploy.agent?.status === "running" ? "运行中" : "异常", deploy.agent?.status === "running" ? "success-text" : "warn-text")}
      ${statCard("今日工单", summary.today_tickets || "--")}
      ${statCard("运行事件", summary.today_events || "--")}
      ${statCard("错误事件", summary.error_events || 0, summary.error_events ? "warn-text" : "success-text")}
      ${statCard("失败率", errorRate)}
    </div>
    <section class="card">
      <div class="card-header">
        <span>最近运行事件</span>
        <span class="chip">点击查看详情</span>
      </div>
      <div class="card-body mini-list">
        ${latest.length ? latest.map((item, index) => `
          <button class="mini-row" onclick="openLogDetail(${index})">
            <span>${escapeHtml(formatTime(item.timestamp || item.ts))}</span>
            <span class="event-name">${escapeHtml(item.event || "未知事件")}</span>
            <span>${escapeHtml(logSummary(item) || item.module || item._source_file || "查看完整日志")}</span>
          </button>
        `).join("") : emptyHtml(runRecordsEmptyText(runs))}
      </div>
    </section>
  `);
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
  return data;
}

async function renderRunRecords() {
  const data = await loadRunRecords(200);
  const items = data.items || [];
  const summary = data.summary || {};
  setContent(`
    <div class="stat-grid">
      ${statCard("匹配事件", summary.filtered ?? 0)}
      ${statCard("全部事件", summary.total ?? 0)}
      ${statCard("今日工单", summary.today_tickets ?? "--")}
      ${statCard("错误事件", summary.error_events ?? 0, summary.error_events ? "warn-text" : "success-text")}
      ${statCard("JSONL", data.jsonl_enabled ? "已开启" : "未开启", data.jsonl_enabled ? "success-text" : "warn-text")}
    </div>
    <section class="card">
      <div class="card-header">
        <span>筛选</span>
        <button class="btn secondary" onclick="resetRunFilters()">重置</button>
      </div>
      <div class="card-body">
        <div class="filter-grid">
          ${filterInput("run-ticket", "工单 ID", "ticket_id")}
          ${filterInput("run-event", "事件", "event")}
          ${filterInput("run-module", "模块", "module")}
          ${filterInput("run-level", "级别", "level")}
          <label>错误事件
            <select id="run-errors">
              <option value="">全部</option>
              <option value="true" ${state.runFilters.errors_only === "true" ? "selected" : ""}>只看错误</option>
            </select>
          </label>
        </div>
        <div class="button-row" style="margin-top:12px">
          <button class="btn primary" onclick="applyRunFilters()">查询</button>
        </div>
      </div>
    </section>
    <section class="card">
      <div class="card-header">
        <span>运行记录</span>
        <span class="chip">点击行查看完整日志</span>
      </div>
      <div class="card-body table">
        <div class="table-grid records-table">
          <div class="table-header records-table">
            <span>时间</span><span>工单</span><span>事件</span><span>模块</span><span>摘要</span>
          </div>
          ${items.map((item, index) => `
            <button class="table-row row-button records-table" onclick="openLogDetail(${index})">
              <span class="cell">${escapeHtml(formatTime(item.timestamp || item.ts))}</span>
              <span class="cell">${escapeHtml(item.ticket_id || item.ticket || "-")}</span>
              <span class="cell event-name">${escapeHtml(item.event || "未知事件")}</span>
              <span class="cell">${escapeHtml(item.module || "-")}</span>
              <span class="cell">${escapeHtml(logSummary(item) || "点击查看完整日志")}</span>
            </button>
          `).join("") || emptyHtml(runRecordsEmptyText(data))}
        </div>
      </div>
    </section>
  `);
}

function applyRunFilters() {
  state.runFilters = {
    ticket_id: document.getElementById("run-ticket").value.trim(),
    event: document.getElementById("run-event").value.trim(),
    module: document.getElementById("run-module").value.trim(),
    level: document.getElementById("run-level").value.trim(),
    errors_only: document.getElementById("run-errors").value
  };
  void renderRunRecords();
}

function resetRunFilters() {
  state.runFilters = {};
  void renderRunRecords();
}

function filterInput(id, label, key) {
  return `
    <label>${escapeHtml(label)}
      <input id="${id}" value="${escapeHtml(state.runFilters[key] || "")}">
    </label>
  `;
}

function renderConfigPage(menu) {
  const fields = fieldsFor(menu);
  if (!fields.length) {
    setContent(emptyHtml("当前模块没有可编辑配置"));
    return;
  }
  setContent(renderForm(menu, fields));
}

function renderIntegrations() {
  const fields = fieldsFor("工具与集成");
  setContent(`
    <section class="card">
      <div class="card-header">
        <span>工具连通性</span>
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
    <section class="card">
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
    <div class="stat-grid">
      ${statCard("Agent 服务", deploy.agent?.status || "unknown", deploy.agent?.status === "running" ? "success-text" : "warn-text")}
      ${statCard("镜像标签", deploy.image?.tag || "latest")}
      ${statCard("提交", shortSha(deploy.image?.commit_sha) || "--")}
      ${statCard("待重启", deploy.pending_restart ? "有" : "无", deploy.pending_restart ? "warn-text" : "success-text")}
      ${statCard("密码登录", deploy.admin_auth?.password_login?.enabled ? "开启" : "关闭")}
    </div>
    <section class="card">
      <div class="card-header">运行路径</div>
      <div class="card-body kv-grid">
        <strong>资源目录</strong><span>${escapeHtml(deploy.storage?.base_dir || "-")}</span>
        <strong>状态目录</strong><span>${escapeHtml(deploy.storage?.state_dir || "-")}</span>
        <strong>日志目录</strong><span>${escapeHtml(deploy.storage?.log_dir || "-")}</span>
        <strong>Google SSO</strong><span>${authStatus(deploy.admin_auth?.google_sso)}</span>
        <strong>飞书 SSO</strong><span>${authStatus(deploy.admin_auth?.feishu_sso)}</span>
      </div>
    </section>
    ${renderForm("部署状态", fieldsFor("部署状态"))}
  `);
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
    <div class="stat-grid">
      ${statCard("匹配记录", summary.filtered ?? 0)}
      ${statCard("全部记录", summary.total ?? 0)}
      ${statCard("最近操作", state.auditLogs[0]?.action || "--")}
      ${statCard("最近操作人", state.auditLogs[0]?.actor || "--")}
      ${statCard("结果", "success", "success-text")}
    </div>
    <section class="card">
      <div class="card-header">
        <span>筛选</span>
        <button class="btn secondary" onclick="resetAuditFilters()">重置</button>
      </div>
      <div class="card-body">
        <div class="filter-grid">
          <label>动作
            <input id="audit-action" value="${escapeHtml(state.auditFilters.action || "")}">
          </label>
          <label>操作人
            <input id="audit-actor" value="${escapeHtml(state.auditFilters.actor || "")}">
          </label>
        </div>
        <div class="button-row" style="margin-top:12px">
          <button class="btn primary" onclick="applyAuditFilters()">查询</button>
        </div>
      </div>
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

function openLogDetail(index) {
  const item = state.runRecords[index];
  if (!item) return;
  openDrawer(
    item.event || "日志详情",
    formatTime(item.timestamp || item.ts),
    `
      <div class="kv-grid">
        <strong>工单</strong><span>${escapeHtml(item.ticket_id || item.ticket || "-")}</span>
        <strong>模块</strong><span>${escapeHtml(item.module || "-")}</span>
        <strong>来源文件</strong><span>${escapeHtml(item._source_file || "-")}</span>
      </div>
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

function statCard(label, value, className = "") {
  return `
    <div class="stat">
      <div class="stat-label">${escapeHtml(label)}</div>
      <div class="stat-value ${className}">${escapeHtml(value)}</div>
    </div>
  `;
}

function loadingHtml(text) {
  return `<div class="empty-state">${escapeHtml(text)}</div>`;
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

function isErrorEvent(item) {
  const text = [
    item.event,
    item.status,
    item.level,
    item.error,
    item.exception,
    item.traceback
  ].filter(Boolean).join(" ").toLowerCase();
  return ["error", "failed", "failure", "exception", "traceback"].some(token => text.includes(token));
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleString();
  }
  return String(value).replace("T", " ");
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

function logSummary(item) {
  const parts = [];
  ["status", "reason", "error", "reply_type", "tool", "rounds", "_source_file"].forEach(key => {
    if (item[key] !== undefined && item[key] !== null && item[key] !== "") {
      parts.push(`${key}: ${item[key]}`);
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
