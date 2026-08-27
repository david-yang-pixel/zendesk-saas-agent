const PRIMARY_MENU = [
  { key: "总览", label: "工单指挥台" },
  { key: "工单中心", label: "工单中心" },
  { key: "运行记录", label: "日志与观测" },
  { key: "模型与运行", label: "模型管理" },
  { key: "Agent 指令", label: "Agent 指令" },
  { key: "Agent 策略", label: "Agent 策略" },
  { key: "工具与集成", label: "工具与集成" },
  { key: "系统与审计", label: "系统与审计" }
];

const NAV_PARENT = {
  "触发配置": "Agent 策略",
  "回复策略": "Agent 策略",
  "存储配置": "系统与审计",
  "部署状态": "系统与审计",
  "审计日志": "系统与审计"
};

const PAGE_LABELS = Object.fromEntries(PRIMARY_MENU.map(item => [item.key, item.label]));

const MENU_ICONS = {
  "总览": '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
  "运行记录": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 19h16M5 15l4-4 3 3 6-7"/><path d="M18 7h-4M18 7v4"/></svg>',
  "触发配置": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 4v6h6M20 20v-6h-6"/><path d="M5.1 15a8 8 0 0 0 13.4 2M18.9 9A8 8 0 0 0 5.5 7"/></svg>',
  "回复策略": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="M8 9h8M8 13h5"/></svg>',
  "模型与运行": '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/></svg>',
  "Agent 指令": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>',
  "工具与集成": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 2v6M12 16v6M4.9 4.9l4.2 4.2M14.9 14.9l4.2 4.2M2 12h6M16 12h6M4.9 19.1l4.2-4.2M14.9 9.1l4.2-4.2"/><circle cx="12" cy="12" r="4"/></svg>',
  "存储配置": '<svg aria-hidden="true" viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></svg>',
  "部署状态": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3 4 7v10l8 4 8-4V7Z"/><path d="m4 7 8 4 8-4M12 11v10"/></svg>',
  "审计日志": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h12v18H6zM9 7h6M9 11h6M9 15h4"/></svg>'
};
MENU_ICONS["工单中心"] = MENU_ICONS["回复策略"];
MENU_ICONS["Agent 策略"] = MENU_ICONS["触发配置"];
MENU_ICONS["系统与审计"] = MENU_ICONS["审计日志"];

const PASSWORD_ICONS = {
  visible: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M2.1 12s3.6-7 9.9-7 9.9 7 9.9 7-3.6 7-9.9 7-9.9-7-9.9-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
  hidden: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 4.2A10 10 0 0 1 12 4c6.3 0 9.9 8 9.9 8a16 16 0 0 1-2.1 3.1M6.6 6.6C3.8 8.5 2.1 12 2.1 12S5.7 20 12 20a10 10 0 0 0 4.1-.9"/></svg>'
};

const SIDEBAR_FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
let sidebarReturnFocus = null;
let drawerReturnFocus = null;
let modalReturnFocus = null;
let lifecyclePollTimer = null;

const SUBTITLES = {
  "总览": "实时掌握每一张工单、每一个 Agent 槽位与每一次发送",
  "工单中心": "统一查看正在处理、等待客户和已经完成的工单",
  "运行记录": "每次 agent 处理工单的完整日志中心",
  "Agent 策略": "集中管理触发、等待、人工接管与自动回复策略",
  "系统与审计": "部署、持久化、访问控制与关键变更记录",
  "触发配置": "webhook、轮询、@mention 和 intake 规则",
  "回复策略": "自动 public reply、可靠送达和等待策略",
  "模型与运行": "模型、Base URL、API key、并发和运行护栏",
  "Agent 指令": "查看安全核心、Skill 与场景参考，按版本安全调整附加指令",
  "工具与集成": "Zendesk、DB、SigNoz、GitHub、Docs、Sandbox 等远程服务",
  "存储配置": "配置运行记录、审计日志、附件缓存和配置持久化路径",
  "部署状态": "镜像、服务状态、重载状态和后台访问方式",
  "审计日志": "所有配置、密钥、登录方式和部署变更记录"
};

const GROUP_DESCRIPTIONS = {
  "触发配置": [
    "控制 Agent 什么时候开始处理、什么时候等待，以及如何合并客户消息。",
    "这里聚焦 durable queue、intake 窗口和漏消息恢复。"
  ],
  "回复策略": [
    "控制自动 public reply 的写入总开关、合并窗口和可靠送达参数。",
    "人工所有权由类型化策略决定，不再由追问次数或旧回复模板触发。"
  ],
  "模型与运行": [
    "配置调查与回复模型、API Key、并发、轮次和超时。",
    "展开主模型下拉框时，会按当前 Base URL 实时读取可用模型与能力。"
  ],
  "工具与集成": [
    "配置 Agent 使用的远程 MCP/Tool Service 和 Browser Relay。",
    "这里可以测试连通性；检测结果会写入审计日志。"
  ],
  "存储配置": [
    "配置运行记录、审计日志、附件缓存、sandbox 和配置持久化路径。",
    "路径类配置通常需要重启生效。"
  ],
  "部署状态": [
    "查看镜像、提交、重启状态，并管理后台登录方式。",
    "默认账号密码登录；Google/飞书 SSO 需要配置后再开启。"
  ]
};

const FIELD_SECTIONS = {
  "触发配置": [
    {
      title: "Zendesk 接入与轮询",
      description: "工单来源、搜索条件、轮询间隔、@mention 和灰度范围。",
      paths: [
        "zendesk.subdomain",
        "zendesk.email",
        "zendesk.api_token",
        "zendesk.search_query",
        "zendesk.poll_query",
        "zendesk.poll_interval_seconds",
        "zendesk.mention_trigger",
        "zendesk.mention_aliases",
        "zendesk.mention_poll_interval_seconds",
        "zendesk.agent_user_id",
        "zendesk.min_ticket_id"
      ]
    },
    {
      title: "Webhook 入口",
      description: "Tickets 与 Messaging webhook 的监听地址、路径与签名密钥。",
      paths: [
        "webhook.signing_secret",
        "webhook.messaging_signing_secret",
        "webhook.listen_host",
        "webhook.listen_port",
        "webhook.zendesk_path",
        "webhook.zendesk_messaging_path",
      ]
    },
    {
      title: "Intake 与等待策略",
      description: "durable queue 入队前的静默窗口与处理后冷却。",
      paths: [
        "intake.silence_minutes_default",
        "intake.post_agent_cooldown_seconds"
      ]
    },
    {
      title: "后台扫描器",
      description: "静默唤醒和等待工单兜底扫描，偏运行时调度。",
      paths: [
        "silence_waker.scan_interval_seconds",
        "waiting_poller.scan_interval_seconds",
        "waiting_poller.customer_wait_timeout_hours",
        "waiting_poller.poll_new_messages"
      ]
    }
  ],
  "回复策略": [
    {
      title: "Public reply 策略",
      description: "是否允许 Agent 自动公开回复以及 Browser Relay 调用超时。",
      paths: [
        "public_reply.enabled",
        "public_reply.browser_relay_timeout_seconds"
      ]
    },
    {
      title: "消息合并窗口",
      description: "工单创建和客户消息到达后的 durable queue 延迟窗口。",
      paths: [
        "public_reply.silent_initial_wait_seconds",
        "public_reply.silence_window_seconds"
      ]
    }
  ],
  "模型与运行": [
    {
      title: "模型连接与能力",
      description: "在一处配置供应商、Base URL、密钥、模型及模型能力。",
      paths: [
        "llm.provider",
        "llm.base_url",
        "openai.api_key",
        "anthropic.api_key",
        "llm.model",
        "llm.reasoning_effort",
        "llm.enable_vision_input"
      ]
    },
    {
      title: "模型运行参数",
      description: "请求温度、超时和单次最大输出 token。",
      paths: [
        "llm.temperature",
        "llm.timeout",
        "llm.max_tokens",
        "llm.context_window_tokens"
      ]
    },
    {
      title: "运行边界",
      description: "调查轮次和工具调用不设上限，仅限制单工单运行时长。",
      paths: [
        "agent.max_concurrent_sessions",
        "agent.session_timeout_seconds"
      ]
    },
    {
      title: "上下文压缩",
      description: "接近模型窗口时生成证据受约束的检查点，并保留最近工具轮次。",
      paths: [
        "casework.context_compaction_soft_ratio",
        "casework.context_compaction_hard_ratio",
        "casework.context_checkpoint_max_tokens",
        "casework.context_retain_recent_rounds"
      ]
    },
    {
      title: "重试策略",
      description: "全局重试退避，以及源码/Git 的专用次数覆盖。",
      paths: [
        "retry.max_attempts",
        "retry.base_delay_seconds",
        "retry.max_delay_seconds",
        "retry.jitter",
        "retry.git.max_attempts"
      ]
    }
  ],
  "工具与集成": [
    {
      title: "Browser Relay",
      description: "客户可见 public reply 的浏览器中继服务。",
      paths: [
        "public_reply.browser_relay_url",
        "public_reply.browser_relay_api_key"
      ]
    },
    {
      title: "Zendesk MCP",
      description: "工单读取、内部评论、标签、关注和附件读取。",
      paths: [
        "zendesk_mcp.mcp_url",
        "zendesk_mcp.mcp_api_key",
        "zendesk_mcp.request_timeout_seconds"
      ]
    },
    {
      title: "排查工具服务",
      description: "DB、SigNoz、Source、GitHub、官方文档和 Sandbox 等远程能力。",
      paths: [
        "db_gateway.url",
        "db_gateway.api_key",
        "db_gateway.request_timeout_seconds",
        "signoz.mcp_url",
        "signoz.mcp_api_key",
        "signoz.request_timeout_seconds",
        "source_mcp.mcp_url",
        "source_mcp.mcp_api_key",
        "source_mcp.request_timeout_seconds",
        "github_mcp.mcp_url",
        "github_mcp.mcp_api_key",
        "github_mcp.request_timeout_seconds",
        "docs_tool_service.mcp_url",
        "docs_tool_service.mcp_api_key",
        "docs_tool_service.request_timeout_seconds",
        "mcp_services.custom"
      ]
    },
    {
      title: "通知服务",
      description: "飞书凭据、幂等与传输重试全部由独立 notification-service 管理。",
      paths: [
        "notification_service.mcp_url",
        "notification_service.mcp_api_key",
        "notification_service.request_timeout_seconds"
      ]
    },
    {
      title: "Inspector（只读）",
      description: "现有只读生产检查入口；Agent 会按证据需要调用。",
      paths: [
        "inspector.binary",
        "inspector.config_file",
        "inspector.timeout_seconds"
      ]
    }
  ],
  "存储配置": [
    {
      title: "资源目录",
      description: "资源根目录、代码库、工单记录和附件目录。",
      paths: [
        "resources.base_dir",
        "resources.saas_codebase_dir",
        "resources.ticket_records_dir",
        "resources.attachments_dir",
        "resources.state_dir"
      ]
    },
    {
      title: "归档与日志",
      description: "Agent 运行归档和 JSONL 日志存储。",
      paths: [
        "archive.archive_dir",
        "logging.jsonl_enabled",
        "logging.log_dir"
      ]
    },
    {
      title: "历史 Sandbox 取证",
      description: "只供 CLI 查看或显式清理历史遗留文件；Agent 不在这里执行代码。",
      paths: [
        "sandbox.base_dir"
      ]
    }
  ],
  "部署状态": [
    {
      title: "账号密码登录",
      description: "后台默认登录方式，支持开启或关闭。",
      paths: ["admin_auth.password_login_enabled"]
    },
    {
      title: "Google SSO",
      description: "Google OAuth 客户端和允许登录的域名。",
      paths: [
        "admin_auth.google_sso_enabled",
        "admin_auth.google_client_id",
        "admin_auth.google_client_secret",
        "admin_auth.google_allowed_domains"
      ]
    },
    {
      title: "飞书 SSO",
      description: "飞书应用凭据和 SSO 开关。",
      paths: [
        "admin_auth.feishu_sso_enabled",
        "admin_auth.feishu_app_id",
        "admin_auth.feishu_app_secret"
      ]
    }
  ]
};

const INTEGRATIONS = [
  ["public_reply_browser_relay", "Browser Relay", "客户可见 public reply"],
  ["zendesk_mcp", "Zendesk MCP", "工单读取、内部评论、标签和关注"],
  ["db_gateway", "DB Gateway", "生产库只读查询"],
  ["signoz", "SigNoz MCP", "日志、指标、trace 和 ClickHouse 查询"],
  ["source_mcp", "Source MCP", "线上源码搜索和读取"],
  ["github_mcp", "GitHub MCP", "PR、Issue、revert 和 follow-up 校验"],
  ["docs_tool_service", "Docs Service", "官方文档缓存和限域读取"],
  ["notification_service", "Notification Service", "飞书通知和重试"]
];

const INTEGRATION_CONFIG_PATHS = {
  public_reply_browser_relay: [
    "public_reply.browser_relay_url",
    "public_reply.browser_relay_api_key",
    "public_reply.browser_relay_timeout_seconds"
  ],
  zendesk_mcp: [
    "zendesk_mcp.mcp_url",
    "zendesk_mcp.mcp_api_key",
    "zendesk_mcp.request_timeout_seconds"
  ],
  db_gateway: [
    "db_gateway.url",
    "db_gateway.api_key",
    "db_gateway.request_timeout_seconds"
  ],
  signoz: [
    "signoz.mcp_url",
    "signoz.mcp_api_key",
    "signoz.request_timeout_seconds"
  ],
  source_mcp: [
    "source_mcp.mcp_url",
    "source_mcp.mcp_api_key",
    "source_mcp.request_timeout_seconds"
  ],
  github_mcp: [
    "github_mcp.mcp_url",
    "github_mcp.mcp_api_key",
    "github_mcp.request_timeout_seconds"
  ],
  docs_tool_service: [
    "docs_tool_service.mcp_url",
    "docs_tool_service.mcp_api_key",
    "docs_tool_service.request_timeout_seconds"
  ],
  notification_service: [
    "notification_service.mcp_url",
    "notification_service.mcp_api_key",
    "notification_service.request_timeout_seconds"
  ]
};

const state = {
  activeMenu: "总览",
  schema: { fields: [], groups: [] },
  values: {},
  runRecords: [],
  runRecordsPayload: null,
  runDisplayRecords: [],
  runLimit: 50,
  runQueryKey: "",
  runRefreshing: false,
  expandedRuns: new Set(),
  loadingRunDetails: new Set(),
  auditLogs: [],
  deployStatus: null,
  runFilters: {},
  auditFilters: {},
  activeConfigSections: {},
  integrationChecks: {},
  modelDiscovery: {
    provider: "",
    baseUrl: "",
    models: [],
    latencyMs: 0,
    loading: false,
    error: ""
  },
  instructions: null,
  instructionDraft: null,
  instructionScenario: "general",
  instructionError: "",
  ticketCenter: {
    items: [],
    summary: {},
    status: "",
    loading: false,
    error: ""
  }
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
  closeSidebar();
  resetLoginPassword();
  resetPasswordChangeFields();
  document.getElementById("login").classList.remove("hidden");
  document.getElementById("password-change").classList.add("hidden");
  document.getElementById("app").classList.add("hidden");
}

function showPasswordChange() {
  closeSidebar();
  document.getElementById("login").classList.add("hidden");
  document.getElementById("password-change").classList.remove("hidden");
  document.getElementById("app").classList.add("hidden");
}

function showApp() {
  closeSidebar();
  document.getElementById("login").classList.add("hidden");
  document.getElementById("password-change").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
}

function showSsoHint(provider) {
  const msg = document.getElementById("login-msg");
  msg.textContent = `${provider} SSO 需要先在「部署状态」里配置并启用。`;
}

function togglePassword(inputId, button) {
  const input = document.getElementById(inputId);
  const willShow = input.type === "password";
  input.type = willShow ? "text" : "password";
  button.setAttribute("aria-label", willShow ? "隐藏密码" : "显示密码");
  button.innerHTML = willShow ? PASSWORD_ICONS.hidden : PASSWORD_ICONS.visible;
}

function resetLoginPassword() {
  const input = document.getElementById("login-pass");
  const button = document.querySelector(".password-toggle");
  if (!input || !button) return;
  input.value = "";
  input.type = "password";
  button.setAttribute("aria-label", "显示密码");
  button.innerHTML = PASSWORD_ICONS.visible;
}

function resetPasswordChangeFields() {
  ["change-current", "change-new", "change-confirm"].forEach(id => {
    const input = document.getElementById(id);
    if (!input) return;
    input.value = "";
    input.type = "password";
  });
}

function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");
  const toggle = document.getElementById("mobile-nav-toggle");
  if (sidebar.classList.contains("open")) {
    closeSidebar(true);
    return;
  }
  sidebarReturnFocus = document.activeElement;
  const isOpen = sidebar.classList.toggle("open");
  backdrop.classList.toggle("visible", isOpen);
  toggle.setAttribute("aria-expanded", String(isOpen));
  syncSidebarAccessibility();
  requestAnimationFrame(() => sidebar.querySelector(".sidebar-close")?.focus());
}

function closeSidebar(restoreFocus = false) {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");
  const toggle = document.getElementById("mobile-nav-toggle");
  sidebar?.classList.remove("open");
  backdrop?.classList.remove("visible");
  toggle?.setAttribute("aria-expanded", "false");
  syncSidebarAccessibility();
  const returnFocus = sidebarReturnFocus;
  sidebarReturnFocus = null;
  if (restoreFocus && returnFocus instanceof HTMLElement) {
    requestAnimationFrame(() => returnFocus.focus());
  }
}

function syncSidebarAccessibility() {
  const sidebar = document.getElementById("sidebar");
  const main = document.getElementById("main-content");
  if (!sidebar || !main) return;
  const isMobile = window.matchMedia("(max-width: 900px)").matches;
  const backdrop = document.getElementById("sidebar-backdrop");
  const toggle = document.getElementById("mobile-nav-toggle");
  if (!isMobile) {
    sidebar.classList.remove("open");
    backdrop?.classList.remove("visible");
    toggle?.setAttribute("aria-expanded", "false");
    sidebarReturnFocus = null;
  }
  const isOpen = isMobile && sidebar.classList.contains("open");
  sidebar.inert = isMobile && !isOpen;
  sidebar.setAttribute("aria-hidden", String(isMobile && !isOpen));
  main.inert = isMobile && isOpen;
  document.body.classList.toggle("sidebar-open", isMobile && isOpen);
}

function handleSidebarKeydown(event) {
  const sidebar = document.getElementById("sidebar");
  if (!sidebar?.classList.contains("open") || !window.matchMedia("(max-width: 900px)").matches) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeSidebar(true);
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = Array.from(sidebar.querySelectorAll(SIDEBAR_FOCUSABLE)).filter(element => !element.inert && element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

async function login() {
  const msg = document.getElementById("login-msg");
  const password = document.getElementById("login-pass").value;
  msg.textContent = "";
  try {
    const result = await api("/admin/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: document.getElementById("login-user").value,
        password
      })
    });
    if (result.force_password_change) {
      document.getElementById("change-current").value = password;
      resetLoginPassword();
      showPasswordChange();
      return;
    }
    resetLoginPassword();
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
  const section = document.createElement("section");
  section.className = "nav-group nav-primary";
  PRIMARY_MENU.forEach(item => {
      const menu = item.key;
      const btn = document.createElement("button");
      const isActive = menu === state.activeMenu || menu === NAV_PARENT[state.activeMenu];
      btn.type = "button";
      btn.className = isActive ? "active" : "";
      btn.setAttribute("aria-current", isActive ? "page" : "false");
      btn.innerHTML = `<span class="nav-icon">${MENU_ICONS[menu]}</span><span>${item.label}</span>`;
      btn.onclick = () => {
        const wasMobileDrawerOpen = document.getElementById("sidebar").classList.contains("open");
        state.activeMenu = menu;
        window.scrollTo({ top: 0, behavior: "auto" });
        closeSidebar();
        renderNav();
        void render().then(() => {
          if (wasMobileDrawerOpen) document.getElementById("page-title").focus();
        });
      };
      section.appendChild(btn);
  });
  nav.appendChild(section);
}

async function render() {
  const pageLabel = PAGE_LABELS[state.activeMenu] || state.activeMenu;
  document.title = `${pageLabel} · zd-agent`;
  document.getElementById("page-title").textContent = pageLabel;
  document.getElementById("page-subtitle").textContent = SUBTITLES[state.activeMenu] || "";
  setPageActions();
  setContent(loadingHtml("加载中"));
  try {
    if (state.activeMenu === "总览") {
      await renderOverview();
    } else if (state.activeMenu === "工单中心") {
      await renderTicketCenter();
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
    } else if (state.activeMenu === "模型与运行") {
      renderModelManagement();
    } else if (state.activeMenu === "Agent 指令") {
      await renderAgentInstructions();
    } else if (state.activeMenu === "Agent 策略") {
      renderStrategyHub();
    } else if (state.activeMenu === "系统与审计") {
      renderSystemHub();
    } else {
      renderConfigPage(state.activeMenu);
    }
  } catch (err) {
    setContent(errorHtml(err.message || "加载失败"));
  }
}

function setPageActions() {
  const actions = document.getElementById("page-actions");
  if (state.activeMenu === "工具与集成") {
    actions.innerHTML = `
      <button class="btn secondary" onclick="refreshActivePage()">刷新</button>
      <button class="btn primary" onclick="openMcpManager()">新增 MCP</button>
    `;
    return;
  }
  const editable = ![
    "总览", "工单中心", "运行记录", "审计日志", "Agent 指令", "Agent 策略", "系统与审计"
  ].includes(state.activeMenu);
  if (!editable) {
    actions.innerHTML = `<button class="btn secondary" onclick="refreshActivePage()">刷新</button>`;
    return;
  }
  actions.innerHTML = `
    <button class="btn secondary" onclick="previewChanges()">预览变更</button>
    <button class="btn primary" onclick="saveChanges()">保存配置</button>
  `;
}

function refreshActivePage() {
  if (state.activeMenu === "运行记录") {
    void renderRunRecords({ reload: true });
    return;
  }
  void render();
}

function setContent(html) {
  document.getElementById("content").innerHTML = html;
}

function setInnerHtml(id, html) {
  const node = document.getElementById(id);
  if (node) {
    node.innerHTML = html;
  }
}

function replaceHtml(id, html) {
  const node = document.getElementById(id);
  if (node) {
    node.outerHTML = html;
  }
}

async function renderOverview() {
  state.runDisplayRecords = [];
  setContent(loadingHtml("正在同步 Agent、队列与发送状态"));
  const [operationsResult, deployResult, runsResult] = await Promise.allSettled([
    api("/admin/api/operations/status"),
    loadDeployStatus(),
    loadRunRecords(14, {}, { quick: true, detail: false, updateState: false })
  ]);
  const operations = operationsResult.status === "fulfilled" ? operationsResult.value : null;
  const deploy = deployResult.status === "fulfilled" ? deployResult.value : null;
  const runs = runsResult.status === "fulfilled" ? runsResult.value : { items: [], summary: {} };
  const healthLine = operations?.ready
    ? `系统运行正常 · Intake 正常 · Codex ${operations.active || 0} / ${operations.max_concurrent || 3} 在线 · 队列 ${operations.queue?.pending || 0}`
    : "系统状态未完全就绪，请查看右侧运行概览";
  document.getElementById("page-subtitle").textContent = healthLine;
  document.getElementById("header-live-state").textContent = new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }).format(new Date());
  const errors = [operationsResult, deployResult, runsResult]
    .filter(result => result.status === "rejected")
    .map(result => result.reason?.message || "数据源不可用");
  setContent(renderCommandCenter(operations, deploy, runs, errors));
}

function renderCommandCenter(operations, deploy, runs, errors = []) {
  const activeSessions = operations?.active_sessions || [];
  const maxConcurrent = Math.max(1, Number(operations?.max_concurrent || 3));
  const visibleSlots = Array.from({ length: Math.min(maxConcurrent, 3) }, (_, index) => activeSessions[index] || null);
  const records = buildRunDisplayRecords(runs.items || []);
  state.runDisplayRecords = records;
  const tickets = mergeCommandTickets(activeSessions, records).slice(0, 8);
  const queuePending = Number(operations?.queue?.pending || 0);
  const outbox = operations?.outbox || {};
  const outboxPending = Object.values(outbox).reduce((total, value) => total + Number(value || 0), 0);
  const backgroundReady = operations?.ready === true;
  const deliveryFailures = records.filter(record => ["failed", "timeout"].includes(record.status)).length;
  const durations = records.map(record => record.durationMs).filter(Boolean);
  const averageDuration = durations.length
    ? formatDuration(durations.reduce((sum, value) => sum + value, 0) / durations.length)
    : "--";
  return `
    ${errors.length ? `<div class="command-alert">部分数据暂不可用：${escapeHtml(errors.join("；"))}</div>` : ""}
    <div class="command-layout">
      <div class="command-main">
        <section class="live-session-panel">
          <div class="section-heading">
            <div><span class="section-kicker">LIVE CAPACITY</span><h2>Codex 实时槽位</h2></div>
            <span class="capacity-number">${activeSessions.length}<small> / ${maxConcurrent}</small></span>
          </div>
          <div class="session-slots">
            ${visibleSlots.map((session, index) => renderSessionSlot(session, index)).join("")}
          </div>
        </section>

        <section class="command-section ticket-board">
          <div class="section-heading">
            <div><span class="section-kicker">TICKET FLOW</span><h2>正在处理的工单</h2></div>
            <button class="text-action" onclick="goMenu('工单中心')">查看工单中心 <span>→</span></button>
          </div>
          <div class="command-ticket-table">
            <div class="command-ticket-head"><span>工单</span><span>当前状态</span><span>处理摘要</span><span>耗时</span><span>更新时间</span></div>
            ${tickets.length ? tickets.map(renderCommandTicketRow).join("") : `<div class="command-empty">当前没有正在处理或最近完成的工单</div>`}
          </div>
        </section>

        <section class="command-section activity-stream">
          <div class="section-heading">
            <div><span class="section-kicker">ACTIVITY</span><h2>系统动态</h2></div>
            <button class="text-action" onclick="goMenu('运行记录')">查看全部日志 <span>→</span></button>
          </div>
          <div class="activity-list">
            ${records.length ? records.slice(0, 5).map(renderActivityRow).join("") : `<div class="command-empty">暂无运行动态</div>`}
          </div>
        </section>
      </div>

      <aside class="operations-rail">
        <div class="rail-heading"><span class="status-dot ${backgroundReady ? "" : "warning"}"></span><strong>${backgroundReady ? "系统运行正常" : "系统需要关注"}</strong></div>
        ${renderOperationalStat("接入服务", backgroundReady ? "已连接" : "检查中", backgroundReady ? "正常接收 Zendesk 事件" : "后台任务未全部运行")}
        ${renderOperationalStat("队列等待", String(queuePending), queuePending ? "工单等待处理" : "当前无排队工单")}
        ${renderOperationalStat("并发占用", `${activeSessions.length} / ${maxConcurrent}`, `${Math.max(0, maxConcurrent - activeSessions.length)} 个槽位可用`)}
        ${renderOperationalStat("平均处理", averageDuration, "基于最近运行记录")}
        ${renderOperationalStat("发送异常", String(deliveryFailures), deliveryFailures ? "最近记录中需要复核" : "最近发送未见异常", deliveryFailures > 0)}
        ${renderOperationalStat("待补偿任务", String(outboxPending), outboxPending ? "可靠送达队列仍有任务" : "Outbox 已清空", outboxPending > 0)}
        <div class="rail-footer">
          <span>版本</span><strong>${escapeHtml(deploy?.image?.tag || "latest")}</strong>
          <span>配置</span><strong>${deploy?.pending_restart ? "待重启" : "已同步"}</strong>
        </div>
      </aside>
    </div>
  `;
}

function renderSessionSlot(session, index) {
  if (!session) {
    return `<div class="session-slot empty"><span>槽位 ${index + 1}</span><strong>空闲</strong><small>等待下一张工单</small></div>`;
  }
  const elapsedMs = Number(session.elapsed || session.elapsed_seconds || 0) * 1000;
  return `
    <div class="session-slot active">
      <span class="slot-pulse"></span>
      <span>槽位 ${index + 1} · #${escapeHtml(session.ticket_id || "-")}</span>
      <strong>${escapeHtml(sessionStatusLabel(session.status))}</strong>
      <small>${escapeHtml(session.round_count || 0)} 轮 · ${escapeHtml(formatTokenCount(session.total_tokens || 0))} tokens · ${escapeHtml(elapsedMs ? formatDuration(elapsedMs) : "刚刚开始")}</small>
    </div>`;
}

function mergeCommandTickets(activeSessions, records) {
  const seen = new Set();
  const active = activeSessions.map(session => {
    const ticket = String(session.ticket_id || "-");
    seen.add(ticket);
    return {
      ticketLabel: ticket === "-" ? "-" : `#${ticket}`,
      status: "running",
      summary: session.hint || sessionStatusLabel(session.status),
      durationMs: Number(session.elapsed || session.elapsed_seconds || 0) * 1000,
      time: null,
      active: true
    };
  });
  return active.concat(records.filter(record => {
    const ticket = record.ticketLabel.replace(/^#/, "");
    if (seen.has(ticket)) return false;
    seen.add(ticket);
    return true;
  }));
}

function renderCommandTicketRow(record) {
  return `<button class="command-ticket-row" onclick="goMenu('运行记录')">
    <strong>${escapeHtml(record.ticketLabel)}</strong>
    <span>${renderStatusBadge(record.status)}</span>
    <span class="ticket-summary">${escapeHtml(record.summary || "查看处理链路")}</span>
    <span>${escapeHtml(record.durationMs ? formatDuration(record.durationMs) : "--")}</span>
    <span>${escapeHtml(record.active ? "进行中" : relativeTime(record.time))}</span>
  </button>`;
}

function renderActivityRow(record) {
  return `<button class="activity-row" onclick="goMenu('运行记录')">
    <span class="activity-mark ${record.status}"></span>
    <div><strong>${escapeHtml(record.ticketLabel)} · ${escapeHtml(record.summary || "Agent 运行记录")}</strong><small>${escapeHtml(record.model)} · ${escapeHtml(relativeTime(record.time))}</small></div>
    <span>${renderStatusBadge(record.status)}</span>
  </button>`;
}

function renderOperationalStat(label, value, note, warning = false) {
  return `<div class="operational-stat ${warning ? "warning" : ""}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></div>`;
}

function sessionStatusLabel(status) {
  const value = String(status || "processing").toLowerCase();
  if (value.includes("tool")) return "调用工具";
  if (value.includes("reply")) return "生成回复";
  if (value.includes("send")) return "发送前复核";
  if (value.includes("wait")) return "等待外部结果";
  return "分析工单";
}

function goMenu(menu) {
  state.activeMenu = menu;
  window.scrollTo({ top: 0, behavior: "auto" });
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

async function loadRunRecords(limit = 100, filters = state.runFilters, options = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  Object.entries(filters || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  if (options.quick) {
    params.set("quick", "true");
  }
  if (typeof options.detail === "boolean") {
    params.set("detail", options.detail ? "true" : "false");
  }
  const data = await api(`/admin/api/run-records?${params.toString()}`);
  if (options.updateState !== false) {
    state.runRecords = data.items || [];
    state.runRecordsPayload = data;
    state.runQueryKey = runQueryKey(limit, filters);
  }
  return data;
}

async function renderRunRecords(options = {}) {
  const queryKey = runQueryKey(state.runLimit, state.runFilters);
  const data = options.reload !== true && state.runRecordsPayload && state.runQueryKey === queryKey
    ? { ...state.runRecordsPayload, items: state.runRecords }
    : null;
  if (data) {
    renderRunRecordsContent(data, { refreshing: state.runRefreshing });
    if (!state.runRefreshing && state.loadingRunDetails.size === 0 && state.expandedRuns.size === 0) {
      refreshRunRecordsInBackground();
    }
    return;
  }
  setContent(renderRunRecordsShell());
  try {
    const payload = await loadRunRecords(state.runLimit, state.runFilters, {
      quick: true,
      detail: false
    });
    renderRunRecordsContent(payload);
    refreshRunRecordsInBackground();
  } catch (err) {
    setContent(errorHtml(err.message || "运行记录加载失败"));
  }
}

function refreshRunRecordsInBackground() {
  if (state.loadingRunDetails.size > 0 || state.expandedRuns.size > 0) {
    return;
  }
  state.runRefreshing = true;
  renderRunRecordsContent(
    { ...state.runRecordsPayload, items: state.runRecords },
    { refreshing: true }
  );
  void loadRunRecords(state.runLimit, state.runFilters, {
    quick: false,
    detail: false
  })
    .then(payload => {
      if (state.activeMenu === "运行记录") {
        state.runRefreshing = false;
        renderRunRecordsContent(payload);
      }
    })
    .catch(error => {
      if (state.activeMenu === "运行记录") {
        showToast(`运行记录刷新失败：${error.message || "未知错误"}`, "danger");
        renderRunRecordsContent(
          { ...state.runRecordsPayload, items: state.runRecords },
          { refreshError: error.message || "刷新失败" }
        );
      }
    })
    .finally(() => {
      state.runRefreshing = false;
    });
}

function runQueryKey(limit, filters = {}) {
  return JSON.stringify({ limit, filters });
}

function renderRunRecordsShell() {
  return `
    <div class="metric-grid run-metrics">
      ${metricCard("总处理数", "--", "读取最近运行")}
      ${metricCard("成功率", "--", "读取错误事件")}
      ${metricCard("平均耗时", "--", "读取耗时")}
      ${metricCard("Token 消耗", "--", "读取用量")}
    </div>
    <section class="filter-panel">
      <input placeholder="工单号筛选" disabled>
      <select disabled><option>全部状态</option></select>
      <input placeholder="事件" disabled>
      <input placeholder="模块" disabled>
      <button class="btn primary" disabled>查询</button>
      <button class="btn secondary" disabled>重置</button>
    </section>
    <section class="card run-record-card">
      <div class="card-header">
        <span>运行记录</span>
        <span class="chip">快速读取中</span>
      </div>
      <div class="card-body">
        ${inlineLoadingHtml("正在读取最近运行记录")}
      </div>
    </section>
  `;
}

function renderRunRecordsContent(data, meta = {}) {
  const items = data.items || [];
  let records = buildRunDisplayRecords(items);
  if (state.runFilters.status === "success") {
    records = records.filter(record => record.status === "success");
  } else if (state.runFilters.status === "failed") {
    records = records.filter(record => ["failed", "timeout"].includes(record.status));
  } else if (state.runFilters.status === "deferred") {
    records = records.filter(record => record.status === "deferred");
  }
  state.runDisplayRecords = records;
  const summary = buildRunSummary(data, records);
  setContent(`
    <div class="metric-grid run-metrics">
      ${metricCard("总处理数", summary.totalRuns, `运行中 ${summary.running}`)}
      ${metricCard("成功率", summary.successRate, `失败 ${summary.failed} / 超时 ${summary.timeout} / 延期 ${summary.deferred}`, (summary.failed || summary.timeout || summary.deferred) ? "warn-text" : "success-text")}
      ${metricCard("平均耗时", summary.avgDuration, "最近匹配记录")}
      ${metricCard("Token 消耗", summary.totalTokens, `入 ${summary.inputTokens} / 出 ${summary.outputTokens}`)}
    </div>
    <section class="filter-panel">
      <input id="run-ticket" placeholder="工单号筛选" value="${escapeHtml(state.runFilters.ticket_id || "")}">
      <select id="run-status">
        <option value="">全部状态</option>
        <option value="success" ${state.runFilters.status === "success" ? "selected" : ""}>成功</option>
        <option value="failed" ${state.runFilters.status === "failed" ? "selected" : ""}>失败</option>
        <option value="deferred" ${state.runFilters.status === "deferred" ? "selected" : ""}>已延期</option>
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
          <span class="chip">完整会话 ${data.summary?.total ?? records.length}</span>
          <span class="chip">原始事件 ${data.raw_events_total ?? 0}</span>
          <span class="chip">扫描文件 ${data.scanned_files ?? 0}</span>
          ${meta.refreshing ? `<span class="chip">后台刷新中</span>` : ""}
          ${meta.refreshError ? `<span class="chip danger-chip">刷新失败</span>` : ""}
          ${data.cache_hit ? `<span class="chip">缓存命中</span>` : ""}
          ${data.partial ? `<span class="chip">快速模式</span>` : ""}
          <span class="chip">JSONL ${data.jsonl_enabled ? "已开启" : "未开启"}</span>
          ${data.has_more || (data.summary?.filtered || 0) > state.runLimit
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
    const key = item.record_key || [
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
      hasDetailedRounds: rounds.some(round => !round.synthetic),
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
  const deferred = records.filter(record => record.status === "deferred").length;
  const completed = Math.max(0, totalRuns - failed - timeout - running - deferred);
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
    deferred,
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
  if (state.loadingRunDetails.has(record.key)) {
    return `
      <div class="run-record-detail">
        ${inlineLoadingHtml("正在读取完整日志链路")}
      </div>
    `;
  }
  return `
    <div class="run-record-detail">
      <div class="detail-summary">
        <span class="chip">${escapeHtml(record.model)}</span>
        <span>轮次 ${escapeHtml(record.roundsCount)}</span>
        <span>${escapeHtml(formatTokenPair(record))}</span>
        <span>${escapeHtml(record.durationMs ? formatDuration(record.durationMs) : "耗时未知")}</span>
        <span>${escapeHtml(record.eventCount ? `${record.eventCount} 条事件` : "无事件链路")}</span>
        ${record.hasDetailedRounds ? "" : `<span class="chip warn-chip">仅聚合轮次</span>`}
        <button class="btn secondary compact" onclick="openRunRecordDetail(${index})">查看完整日志</button>
      </div>
      ${record.hasDetailedRounds ? "" : `
        <div class="notice-card">
          这条记录只有总轮次和事件链路，JSONL 中没有逐轮 LLM 明细；后台已按聚合轮次展示，完整原始事件可在日志抽屉查看。
        </div>
      `}
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
  const badge = round.synthetic ? "聚合" : `R${roundNumber}`;
  return `
    <div class="round-block">
      <div class="round-meta">
        <span class="round-badge">${escapeHtml(badge)}</span>
        <span>${escapeHtml(round.model || "-")}</span>
        <span>${escapeHtml(formatRoundTokens(round))}</span>
        <span>${escapeHtml(round.durationMs ? formatDuration(round.durationMs) : "--")}</span>
        <span class="chip">${escapeHtml(round.type || "tool_calls")}</span>
        ${round.synthetic && round.totalRounds ? `<span class="chip">${escapeHtml(round.totalRounds)} 轮</span>` : ""}
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
  const round = eventRoundLabel(event);
  return `
    <div class="event-trace-row">
      <span class="event-time">${escapeHtml(formatTime(event.timestamp || event.ts || event.time || event.created_at))}</span>
      <span class="event-dot ${status}"></span>
      <span class="round-chip">${escapeHtml(round)}</span>
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
  const traceRounds = extractTraceRounds(item.events || [], item);
  if (traceRounds.length) {
    return traceRounds.map(round => normalizeRound(round, item));
  }
  const totalRounds = numberFromKeys(item, ["rounds", "round_count", "total_rounds"]);
  const traceTools = extractTraceTools(item.events || []);
  if (totalRounds) {
    return [normalizeRound({
      synthetic: true,
      round: 1,
      model: item.model || item.llm_model || "-",
      input_tokens: item.input_tokens || item.prompt_tokens || 0,
      output_tokens: item.output_tokens || item.completion_tokens || 0,
      total_tokens: item.total_tokens || item.tokens || 0,
      duration_ms: item.duration_ms || item.elapsed_ms || 0,
      type: "聚合轮次",
      total_rounds: totalRounds,
      tools: traceTools
    }, item)];
  }
  return [normalizeRound({ ...item, synthetic: true, type: item.event || "event" }, item)];
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
    round: numberFromKeys(round, ["round", "round_number"]) || numberFromKeys(parent, ["round", "rounds", "round_count"]),
    model: round.model || round.llm_model || parent.model || parent.llm_model || "-",
    inputTokens: numberFromKeys(round, ["input_tokens", "prompt_tokens", "tokens_in"]),
    outputTokens: numberFromKeys(round, ["output_tokens", "completion_tokens", "tokens_out"]),
    totalTokens: numberFromKeys(round, ["total_tokens", "tokens", "token_count"]),
    durationMs: durationMsFromItem(round),
    type: round.type || round.finish_reason || (tools.length ? "tool_calls" : parent.event || "event"),
    synthetic: Boolean(round.synthetic),
    totalRounds: numberFromKeys(round, ["total_rounds", "rounds", "round_count"]),
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
    message: tool.error || tool.message || tool.summary || "",
    round: numberFromKeys(tool, ["round", "round_number"])
  }));
}

function extractTraceRounds(events, parent) {
  if (!Array.isArray(events)) return [];
  const groups = new Map();
  events.forEach(event => {
    if (!event || typeof event !== "object") return;
    const round = numberFromKeys(event, ["round", "round_number"]);
    if (!round) return;
    const eventName = String(event.event || "").toLowerCase();
    const isRoundEvent = eventName === "llm_round" || eventName === "round_tool_calls";
    const isToolEvent = eventName === "tool_call" || event.tool || event.tool_name;
    if (!isRoundEvent && !isToolEvent) return;
    if (!groups.has(round)) {
      groups.set(round, {
        round,
        model: event.model || parent.model || parent.llm_model || "-",
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
        duration_ms: 0,
        type: event.stop_reason || (isToolEvent ? "tool_calls" : eventName),
        tools: []
      });
    }
    const group = groups.get(round);
    group.input_tokens = Math.max(group.input_tokens, numberFromKeys(event, ["input_tokens", "prompt_tokens", "tokens_in"]));
    group.output_tokens = Math.max(group.output_tokens, numberFromKeys(event, ["output_tokens", "completion_tokens", "tokens_out"]));
    group.total_tokens = Math.max(group.total_tokens, numberFromKeys(event, ["total_tokens", "tokens", "token_count"]));
    group.duration_ms = Math.max(group.duration_ms, durationMsFromItem(event));
    if (event.stop_reason) group.type = event.stop_reason;
    if (isToolEvent) {
      group.tools.push(traceToolFromEvent(event));
    }
  });
  return Array.from(groups.values()).sort((a, b) => a.round - b.round);
}

function extractTraceTools(events) {
  if (!Array.isArray(events)) return [];
  return events
    .filter(event => event && typeof event === "object" && (event.event === "tool_call" || event.tool || event.tool_name))
    .map(event => traceToolFromEvent(event));
}

function traceToolFromEvent(event) {
  return {
    name: event.tool || event.tool_name || event.event || "unknown_tool",
    status: runStatus(event),
    duration: event.duration || event.elapsed || formatDuration(durationMsFromItem(event)),
    message: event.error || event.status || event.module || "",
    round: numberFromKeys(event, ["round", "round_number"])
  };
}

function toggleRunRecord(index) {
  const record = state.runDisplayRecords[index];
  if (!record) return;
  if (state.expandedRuns.has(record.key)) {
    state.expandedRuns.delete(record.key);
    state.loadingRunDetails.delete(record.key);
    void renderRunRecords({ reload: false });
    return;
  } else {
    state.expandedRuns.add(record.key);
  }
  if (record.raw?.detail_loaded) {
    void renderRunRecords({ reload: false });
    return;
  }
  state.loadingRunDetails.add(record.key);
  void renderRunRecords({ reload: false });
  void ensureRunRecordDetail(index)
    .catch(error => showModal("运行日志读取失败", errorHtml(error.message || "详情加载失败"), [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]))
    .finally(() => {
      state.loadingRunDetails.delete(record.key);
      void renderRunRecords({ reload: false });
    });
}

function applyRunFilters() {
  const status = document.getElementById("run-status").value;
  state.runLimit = 50;
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
  state.runLimit = 50;
  state.expandedRuns.clear();
  void renderRunRecords();
}

function loadMoreRuns() {
  state.runLimit += 80;
  void renderRunRecords({ reload: true });
}

async function renderTicketCenter() {
  setContent(loadingHtml("正在读取工单生命周期"));
  state.ticketCenter.loading = true;
  state.ticketCenter.error = "";
  try {
    const params = new URLSearchParams({ limit: "200" });
    if (state.ticketCenter.status) params.set("status", state.ticketCenter.status);
    const data = await api(`/admin/api/tickets?${params.toString()}`);
    state.ticketCenter.items = data.items || [];
    state.ticketCenter.summary = data.summary || {};
    renderTicketCenterContent(data);
  } catch (err) {
    state.ticketCenter.error = err.message || "工单生命周期加载失败";
    setContent(errorHtml(state.ticketCenter.error));
  } finally {
    state.ticketCenter.loading = false;
  }
}

function renderTicketCenterContent(data) {
  const items = data.items || [];
  const summary = data.summary || {};
  const groups = summary.by_status || {};
  const groupHtml = Object.entries(groups).map(([status, count]) => `
    <button type="button" class="ticket-status-tab ${state.ticketCenter.status === status ? "active" : ""}"
      aria-pressed="${state.ticketCenter.status === status ? "true" : "false"}"
      onclick="setTicketCenterStatus(${jsArg(status)})">
      <span>${escapeHtml(ticketBusinessStatusLabel(status))}</span><strong>${escapeHtml(count)}</strong>
    </button>
  `).join("");
  setContent(`
    <section class="ticket-center-intro">
      <div><span class="section-kicker">BUSINESS TICKET LIFECYCLE</span><h2>工单处理全景</h2><p>这里展示工单业务状态与所有权，不复制运行轮次、工具调用或原始日志。</p></div>
      <div class="ticket-center-count"><strong>${escapeHtml(summary.visible ?? items.length)}</strong><span>张当前视图工单</span></div>
    </section>
    <div class="metric-grid ticket-center-metrics">
      ${metricCard("活跃工单", summary.active ?? items.filter(item => item.active).length, "当前有 Agent Session")}
      ${metricCard("等待客户", summary.awaiting_customer ?? 0, "等待必要字段或新消息")}
      ${metricCard("已归属", summary.owned ?? 0, "存在类型化所有权动作")}
      ${metricCard("失败计数", summary.failures ?? 0, "连续 Agent 失败次数")}
    </div>
    <section class="ticket-center-filters card">
      <div class="ticket-status-tabs" role="group" aria-label="按业务状态筛选">
        <button type="button" class="ticket-status-tab ${!state.ticketCenter.status ? "active" : ""}" aria-pressed="${!state.ticketCenter.status ? "true" : "false"}" onclick="setTicketCenterStatus('')">全部 <strong>${escapeHtml(summary.total ?? items.length)}</strong></button>
        ${groupHtml}
      </div>
      <button type="button" class="btn secondary" onclick="refreshActivePage()">刷新</button>
    </section>
    <section class="command-section ticket-board ticket-center-board">
      <div class="command-ticket-table ticket-lifecycle-table">
        <div class="command-ticket-head"><span>工单</span><span>业务状态</span><span>等待客户</span><span>所有权</span><span>更新时间</span><span>日志</span></div>
        ${items.length ? items.map(renderTicketLifecycleRow).join("") : `<div class="command-empty">当前没有可展示的工单生命周期</div>`}
      </div>
    </section>
  `);
}

function ticketBusinessStatusLabel(status) {
  const labels = {
    processing: "处理中",
    awaiting_customer: "等待客户",
    processed: "已处理",
    owned: "已归属",
    externally_owned: "人工接管",
    ownership_pending: "归属处理中",
    out_of_scope: "超出范围",
    waiting_for_auth: "等待授权",
    manual_reconciliation: "需人工核对",
    unknown: "未知"
  };
  return labels[status] || status || "未知";
}

function renderTicketLifecycleRow(item) {
  const ticketId = String(item.ticket_id || "-");
  const waiting = item.waiting_for_customer
    ? (item.pending_customer_fields || []).join("、") || "等待客户补充"
    : "-";
  const ownership = item.ownership_action
    ? `${item.ownership_action}${item.ownership_reason ? ` · ${item.ownership_reason}` : ""}`
    : "自动处理";
  return `
    <div class="ticket-lifecycle-row">
      <strong class="ticket-cell">#${escapeHtml(ticketId)}</strong>
      <span><span class="status-badge business-${escapeHtml(item.status || "unknown")}">${escapeHtml(ticketBusinessStatusLabel(item.status))}</span>${item.active ? `<small class="ticket-active-note">活跃</small>` : ""}</span>
      <span class="ticket-summary" title="${escapeHtml(waiting)}">${escapeHtml(waiting)}</span>
      <span class="ticket-summary" title="${escapeHtml(ownership)}">${escapeHtml(ownership)}</span>
      <span class="ticket-summary">${escapeHtml(relativeTime(item.updated_at) || "-")}</span>
      <button type="button" class="text-action ticket-log-link" onclick="viewTicketRunLogs(${jsArg(ticketId)})">查看运行日志 →</button>
    </div>
  `;
}

function setTicketCenterStatus(status) {
  state.ticketCenter.status = status || "";
  void renderTicketCenter();
}

function viewTicketRunLogs(ticketId) {
  state.runFilters = { ticket_id: String(ticketId) };
  state.runLimit = 50;
  state.expandedRuns.clear();
  state.activeMenu = "运行记录";
  renderNav();
  void render();
}

async function renderAgentInstructions() {
  if (!state.instructions) {
    setContent(loadingHtml("正在读取 Agent 指令"));
  }
  try {
    const data = await api("/admin/api/instructions");
    state.instructions = data;
    state.instructionDraft = {
      operator_extra_instructions: data.operator_extra_instructions || "",
      skill_core: data.skill_core || "",
      scenario_references: { ...(data.scenario_references || {}) }
    };
    renderAgentInstructionsContent();
  } catch (err) {
    state.instructionError = err.message || "Agent 指令加载失败";
    setContent(errorHtml(state.instructionError));
  }
}

function renderAgentInstructionsContent() {
  const data = state.instructions || {};
  const draft = state.instructionDraft || {
    operator_extra_instructions: "",
    skill_core: "",
    scenario_references: {}
  };
  const activeScenario = state.instructionScenario || "general";
  const refs = draft.scenario_references || {};
  const scenarioLabels = data.scenario_labels || {
    general: "通用产品问题",
    async_processing: "异步处理",
    billing_entitlement: "Billing / Entitlement",
    runtime_failure: "运行时失败"
  };
  const model = state.values["llm.model"] || "-";
  const provider = state.values["llm.provider"] || "-";
  const effort = state.values["llm.reasoning_effort"] || "默认";
  const contextWindow = state.values["llm.context_window_tokens"] || "-";
  const changed = instructionDraftChanged();
  const activeReference = refs[activeScenario] || "";
  const preview = [
    data.system_security_core || "",
    draft.operator_extra_instructions ? `## Operator extra instructions\n\n${draft.operator_extra_instructions}` : "",
    `ACTIVE_SERVER_SKILL=dify-ticket-investigation\nACTIVE_SCENARIO=${activeScenario}`,
    draft.skill_core || "",
    "## Active scenario",
    activeReference
  ].filter(Boolean).join("\n\n");
  setContent(`
    <section class="instruction-hero card">
      <div><span class="section-kicker">AGENT CONTROL SURFACE</span><h2>Agent 指令</h2><p>查看有效指令分层并安全调整下一次新调查。代码安全核心、只读工具权限、确定性 disposition 和发送边界始终由代码控制。</p></div>
      <div class="instruction-meta"><span id="instruction-sync-chip" class="chip ${changed ? "warning" : "enabled"}">${changed ? "有未保存修改" : "已同步"}</span><span>revision ${escapeHtml(data.revision ?? 0)}</span></div>
    </section>
    <section class="instruction-safety-grid">
      <article class="card instruction-safety-card"><span class="section-kicker">ARCHITECTURE</span><h3>单 Agent · 受控调查层</h3><p>系统核心之后才会附加 operator instructions；Skill 和场景参考只影响调查指导，不获得写权限。</p><ul><li>只读 MCP 与租户范围由代码校验</li><li>每个新调查冻结一份 revision</li><li>保存后正在运行的 Session 不改变</li></ul></article>
      <article class="card instruction-safety-card instruction-hidden-reasoning"><span class="section-kicker">MODEL VISIBILITY</span><h3>隐藏推理不可查看</h3><p>这里只展示模型配置、推理强度、上下文容量，以及日志中的工具调用、证据和检查点摘要。</p><strong>模型隐藏推理不可查看</strong></article>
    </section>
    <section class="card instruction-model-summary">
      <div class="card-header"><span>模型与推理摘要</span><span class="chip">下一次新调查生效</span></div>
      <div class="instruction-model-grid"><div><span>供应商</span><strong>${escapeHtml(provider)}</strong></div><div><span>模型</span><strong>${escapeHtml(model)}</strong></div><div><span>推理强度</span><strong>${escapeHtml(effort)}</strong></div><div><span>上下文容量</span><strong>${escapeHtml(contextWindow)} tokens</strong></div></div>
    </section>
    <section class="card instruction-editor-card">
      <div class="card-header"><div><span>只读系统安全核心</span><p>此层不可由后台覆盖，作为所有调查的代码安全边界。</p></div><span class="chip">只读</span></div>
      <div class="card-body"><pre class="instruction-code readonly-code">${escapeHtml(data.system_security_core || "")}</pre></div>
    </section>
    <section class="card instruction-editor-card">
      <div class="card-header"><div><span>Operator 附加系统指令</span><p>受控附加层；不能修改工具 ACL、租户绑定、发送或 disposition 核心规则。</p></div><span class="chip ${draft.operator_extra_instructions ? "warning" : ""}">${draft.operator_extra_instructions ? "已覆盖" : "默认空"}</span></div>
      <div class="card-body"><label for="operator-extra-instructions">附加指令</label><textarea id="operator-extra-instructions" class="prompt-textarea instruction-textarea" maxlength="8000" placeholder="输入面向运营的补充指导，不要放入密钥或隐藏推理要求。" oninput="handleInstructionDraftInput()">${escapeHtml(draft.operator_extra_instructions)}</textarea><small class="field-help">最多 8,000 个字符；保存时必须填写变更原因。</small></div>
    </section>
    <section class="card instruction-editor-card">
      <div class="card-header"><div><span>Skill 核心</span><p>默认来自镜像内 dify-ticket-investigation/SKILL.md，可通过持久化覆盖调整。</p></div><span class="chip ${data.overridden?.skill_core ? "warning" : ""}">${data.overridden?.skill_core ? "已覆盖" : "代码默认"}</span></div>
      <div class="card-body"><label for="instruction-skill-core">Skill 正文</label><textarea id="instruction-skill-core" class="prompt-textarea instruction-textarea" maxlength="60000" oninput="handleInstructionDraftInput()">${escapeHtml(draft.skill_core)}</textarea></div>
    </section>
    <section class="card instruction-editor-card">
      <div class="card-header"><div><span>场景参考</span><p>选择场景查看并修改对应参考；默认文件不会被后台直接改写。</p></div><span class="chip">4 个场景</span></div>
      <div class="card-body"><div class="instruction-tabs" role="tablist" aria-label="调查场景参考">
        ${Object.entries(scenarioLabels).map(([key, label]) => `<button type="button" role="tab" aria-selected="${key === activeScenario ? "true" : "false"}" class="instruction-tab ${key === activeScenario ? "active" : ""}" onclick="selectInstructionScenario(${jsArg(key)})">${escapeHtml(label)}</button>`).join("")}
      </div><label for="instruction-scenario-reference">${escapeHtml(scenarioLabels[activeScenario] || activeScenario)}参考</label><textarea id="instruction-scenario-reference" class="prompt-textarea instruction-textarea" maxlength="30000" oninput="handleInstructionDraftInput()">${escapeHtml(activeReference)}</textarea><small class="field-help">运行时失败参考覆盖 Workflow、Retrieval、Provider 三类场景。</small></div>
    </section>
    <section class="card instruction-preview-card">
      <div class="card-header"><div><span>有效内容预览</span><p>仅展示将注入下一次调查的指令层，不包含模型隐藏推理。</p></div><span class="chip">${changed ? "草稿" : "当前 revision"}</span></div>
      <div class="card-body"><pre id="instruction-effective-preview" class="instruction-code">${escapeHtml(preview)}</pre></div>
    </section>
    <section class="instruction-save-panel card">
      <div><span class="section-kicker">CHANGE CONTROL</span><strong id="instruction-revision-state">revision ${escapeHtml(data.revision ?? 0)} · ${changed ? "待保存" : "无未保存修改"}</strong><p>保存/重置必须填写原因；新配置只对下一张新工单或下一次新调查生效。</p></div>
      <div class="instruction-save-controls"><label for="instruction-change-reason">变更原因</label><textarea id="instruction-change-reason" rows="2" maxlength="500" placeholder="例如：补充 Billing 证据检查要求"></textarea><div id="instruction-save-error" class="danger-text" role="alert"></div><div class="button-row"><button type="button" class="btn secondary" onclick="previewAgentInstructions()">预览变更</button><button type="button" class="btn danger" onclick="resetAgentInstructions()">重置为代码默认</button><button id="instruction-save-button" type="button" class="btn primary" onclick="saveAgentInstructions()" ${changed ? "" : "disabled"}>保存指令</button></div></div>
    </section>
  `);
}

function syncInstructionDraftFromDom() {
  if (!state.instructionDraft) return;
  const extra = document.getElementById("operator-extra-instructions");
  const skill = document.getElementById("instruction-skill-core");
  const reference = document.getElementById("instruction-scenario-reference");
  if (extra) state.instructionDraft.operator_extra_instructions = extra.value;
  if (skill) state.instructionDraft.skill_core = skill.value;
  if (reference) state.instructionDraft.scenario_references[state.instructionScenario] = reference.value;
}

function handleInstructionDraftInput() {
  syncInstructionDraftFromDom();
  const changed = instructionDraftChanged();
  const saveButton = document.getElementById("instruction-save-button");
  const syncChip = document.getElementById("instruction-sync-chip");
  const revisionState = document.getElementById("instruction-revision-state");
  const preview = document.getElementById("instruction-effective-preview");
  if (saveButton) saveButton.disabled = !changed;
  if (syncChip) {
    syncChip.className = `chip ${changed ? "warning" : "enabled"}`;
    syncChip.textContent = changed ? "有未保存修改" : "已同步";
  }
  if (revisionState) {
    revisionState.textContent = `revision ${state.instructions?.revision ?? 0} · ${changed ? "待保存" : "无未保存修改"}`;
  }
  if (preview) {
    const data = state.instructions || {};
    const draft = state.instructionDraft || {};
    preview.textContent = [
      data.system_security_core || "",
      draft.operator_extra_instructions ? `## Operator extra instructions\n\n${draft.operator_extra_instructions}` : "",
      `ACTIVE_SERVER_SKILL=dify-ticket-investigation\nACTIVE_SCENARIO=${state.instructionScenario}`,
      draft.skill_core || "",
      "## Active scenario",
      draft.scenario_references?.[state.instructionScenario] || ""
    ].filter(Boolean).join("\n\n");
  }
}

function instructionDraftChanged() {
  if (!state.instructions || !state.instructionDraft) return false;
  const current = state.instructions;
  const draft = state.instructionDraft;
  if ((current.operator_extra_instructions || "") !== (draft.operator_extra_instructions || "")) return true;
  if ((current.skill_core || "") !== (draft.skill_core || "")) return true;
  return Object.keys({ ...(current.scenario_references || {}), ...(draft.scenario_references || {}) }).some(key =>
    (current.scenario_references?.[key] || "") !== (draft.scenario_references?.[key] || "")
  );
}

function selectInstructionScenario(key) {
  syncInstructionDraftFromDom();
  state.instructionScenario = key;
  renderAgentInstructionsContent();
}

function collectInstructionChanges() {
  syncInstructionDraftFromDom();
  return {
    operator_extra_instructions: state.instructionDraft?.operator_extra_instructions || "",
    skill_core: state.instructionDraft?.skill_core || "",
    scenario_references: { ...(state.instructionDraft?.scenario_references || {}) }
  };
}

async function previewAgentInstructions() {
  const error = document.getElementById("instruction-save-error");
  const changes = collectInstructionChanges();
  try {
    const result = await api("/admin/api/instructions/preview", {
      method: "POST",
      body: JSON.stringify({ changes, reason: document.getElementById("instruction-change-reason")?.value?.trim() || "" })
    });
    showModal("Agent 指令变更预览", `<p>当前 revision ${escapeHtml(result.revision)}，${result.changed ? "检测到待保存修改。" : "没有内容变化。"}</p><pre class="instruction-code">${escapeHtml(result.skill_core || "")}</pre>`, [{ label: "关闭", className: "secondary", onClick: closeModal }]);
  } catch (err) {
    if (error) error.textContent = err.message || "预览失败";
  }
}

async function saveAgentInstructions() {
  const error = document.getElementById("instruction-save-error");
  const reason = document.getElementById("instruction-change-reason")?.value?.trim() || "";
  if (reason.length < 3) {
    if (error) error.textContent = "请填写至少 3 个字符的变更原因";
    return;
  }
  try {
    const data = await api("/admin/api/instructions/apply", {
      method: "POST",
      body: JSON.stringify({ expected_revision: state.instructions?.revision ?? 0, reason, changes: collectInstructionChanges() })
    });
    state.instructions = data;
    state.instructionDraft = { operator_extra_instructions: data.operator_extra_instructions || "", skill_core: data.skill_core || "", scenario_references: { ...(data.scenario_references || {}) } };
    renderAgentInstructionsContent();
    showToast(`Agent 指令已保存（revision ${data.revision}）`, "success");
  } catch (err) {
    if (error) error.textContent = err.message || "保存失败，请刷新后重试";
  }
}

function resetAgentInstructions() {
  showModal("重置 Agent 指令", `<p>将删除持久化覆盖并回到镜像内代码默认。正在执行的 Session 不受影响。</p><label class="field-stack" for="instruction-reset-reason">重置原因<textarea id="instruction-reset-reason" rows="3" maxlength="500" placeholder="例如：撤销临时运营指导"></textarea></label><div id="instruction-reset-error" class="danger-text" role="alert"></div>`, [
    { label: "取消", className: "secondary", onClick: closeModal },
    { label: "确认重置", className: "danger", onClick: submitInstructionReset }
  ]);
  document.getElementById("instruction-reset-reason")?.focus();
}

async function submitInstructionReset() {
  const reason = document.getElementById("instruction-reset-reason")?.value?.trim() || "";
  const error = document.getElementById("instruction-reset-error");
  if (reason.length < 3) {
    if (error) error.textContent = "请填写至少 3 个字符的重置原因";
    return;
  }
  try {
    const data = await api("/admin/api/instructions/reset", { method: "POST", body: JSON.stringify({ expected_revision: state.instructions?.revision ?? 0, reason }) });
    closeModal();
    state.instructions = data;
    state.instructionDraft = { operator_extra_instructions: "", skill_core: data.skill_core || "", scenario_references: { ...(data.scenario_references || {}) } };
    renderAgentInstructionsContent();
    showToast("Agent 指令已恢复代码默认", "success");
  } catch (err) {
    if (error) error.textContent = err.message || "重置失败，请刷新后重试";
  }
}

function renderModelManagement() {
  const fields = fieldsFor("模型与运行");
  setContent(`
    <section class="model-registry-note">
      <div>
        <span class="section-kicker">MODEL REGISTRY</span>
        <h2>模型能力跟随所选模型</h2>
        <p>供应商、Base URL 与 API Key 在同一组完成配置。展开“主模型”选择器后读取模型；推理强度与视觉能力由所选模型自动决定。</p>
      </div>
      <span class="secure-note"><span class="status-dot"></span>密钥仅在服务端使用</span>
      <div class="capability-legend">
        <span data-status="supported">支持</span>
        <span data-status="unsupported">不支持</span>
        <span data-status="unknown">未知，需按供应商文档确认</span>
      </div>
    </section>
    ${renderModuleOverview("模型与运行", fields)}
    ${renderForm("模型与运行", fields)}
  `);
  syncProviderSecretVisibility();
  syncSelectedModelCapabilities();
}

function modelDiscoveryContext() {
  const provider = document.getElementById("field-llm.provider")?.value.trim().toLowerCase()
    || String(state.values["llm.provider"] || "openai").trim().toLowerCase();
  const baseUrl = document.getElementById("field-llm.base_url")?.value.trim()
    || String(fieldValue("llm.base_url") || "").trim();
  return { provider, baseUrl };
}

function modelCatalogMatchesCurrentInputs() {
  const { provider, baseUrl } = modelDiscoveryContext();
  return state.modelDiscovery.provider === provider && state.modelDiscovery.baseUrl === baseUrl;
}

function invalidateModelCatalog() {
  state.modelDiscovery = {
    provider: "",
    baseUrl: "",
    models: [],
    latencyMs: 0,
    loading: false,
    error: ""
  };
  const visionValue = document.querySelector("input[data-vision-model-path]");
  if (visionValue) visionValue.value = "false";
  refreshReasoningEffortOptions();
  syncSelectedModelCapabilities();
}

function handleModelProviderChange() {
  invalidateModelCatalog();
  syncProviderSecretVisibility();
}

function syncProviderSecretVisibility() {
  const { provider } = modelDiscoveryContext();
  document.querySelectorAll("[data-provider-secret]").forEach(element => {
    element.classList.toggle(
      "provider-secret-hidden",
      element.getAttribute("data-provider-secret") !== provider
    );
  });
}

function pendingModelApiKey(provider) {
  return document.querySelector(`input[data-path="${provider}.api_key"]`)?.value.trim() || "";
}

function positionModelPicker() {
  const picker = document.getElementById("model-picker");
  const trigger = document.getElementById("model-picker-trigger");
  if (!picker || !trigger || picker.hidden) return;

  const viewportPadding = 16;
  const triggerGap = 8;
  const minimumComfortableHeight = 320;
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
  const triggerRect = trigger.getBoundingClientRect();
  const spaceBelow = Math.max(
    0,
    viewportHeight - triggerRect.bottom - triggerGap - viewportPadding
  );
  const spaceAbove = Math.max(
    0,
    triggerRect.top - triggerGap - viewportPadding
  );
  const openAbove = spaceBelow < minimumComfortableHeight && spaceAbove > spaceBelow;
  const availableHeight = openAbove ? spaceAbove : spaceBelow;
  const width = Math.min(
    Math.max(triggerRect.width, 320),
    Math.max(0, viewportWidth - viewportPadding * 2)
  );
  const left = Math.min(
    Math.max(viewportPadding, triggerRect.left),
    Math.max(viewportPadding, viewportWidth - width - viewportPadding)
  );

  picker.style.left = `${Math.round(left)}px`;
  picker.style.right = "auto";
  picker.style.width = `${Math.round(width)}px`;
  picker.style.maxHeight = `${Math.floor(Math.min(520, availableHeight))}px`;
  picker.style.top = openAbove ? "auto" : `${Math.round(triggerRect.bottom + triggerGap)}px`;
  picker.style.bottom = openAbove
    ? `${Math.round(viewportHeight - triggerRect.top + triggerGap)}px`
    : "auto";
  picker.dataset.placement = openAbove ? "top" : "bottom";
}

function toggleModelPicker() {
  const picker = document.getElementById("model-picker");
  const trigger = document.getElementById("model-picker-trigger");
  if (!picker || !trigger) return;
  const willOpen = picker.hidden;
  picker.hidden = !willOpen;
  trigger.setAttribute("aria-expanded", willOpen ? "true" : "false");
  if (!willOpen) return;
  positionModelPicker();
  if (!modelCatalogMatchesCurrentInputs() || !state.modelDiscovery.models.length) {
    void discoverModels();
  } else {
    renderModelPickerOptions();
    document.getElementById("model-picker-search")?.focus();
  }
}

async function discoverModels(force = false) {
  const { provider, baseUrl } = modelDiscoveryContext();
  const apiKey = pendingModelApiKey(provider);
  if (!force && state.modelDiscovery.loading) return;
  state.modelDiscovery = {
    provider,
    baseUrl,
    models: [],
    latencyMs: 0,
    loading: true,
    error: ""
  };
  renderModelPickerOptions();
  try {
    const result = await api("/admin/api/models/discover", {
      method: "POST",
      body: JSON.stringify({ provider, base_url: baseUrl, api_key: apiKey })
    });
    state.modelDiscovery = {
      provider,
      baseUrl,
      models: Array.isArray(result.models) ? result.models : [],
      latencyMs: Number(result.latency_ms || 0),
      loading: false,
      error: ""
    };
  } catch (err) {
    state.modelDiscovery = {
      provider,
      baseUrl,
      models: [],
      latencyMs: 0,
      loading: false,
      error: modelDiscoveryErrorText(err.message)
    };
  }
  renderModelPickerOptions();
  syncSelectedModelCapabilities();
  positionModelPicker();
  document.getElementById("model-picker-search")?.focus();
}

function modelDiscoveryErrorText(message) {
  const value = String(message || "");
  if (value.includes("http_401") || value.includes("http_403")) return "鉴权失败，请检查 API Key 与 Base URL。";
  if (value.includes("api_key_is_required")) return "请在当前模型配置中输入 API Key，再重新获取模型。";
  if (value.includes("unreachable")) return "无法连接模型服务，请检查 Base URL、网络和证书配置。";
  if (value.includes("invalid_json")) return "模型服务返回了无法识别的响应格式。";
  return value || "模型列表读取失败";
}

function selectDiscoveredModel(model) {
  const input = document.getElementById("field-llm.model");
  if (!input) return;
  input.value = model;
  const label = document.getElementById("model-picker-current");
  if (label) label.textContent = model;
  const picker = document.getElementById("model-picker");
  const trigger = document.getElementById("model-picker-trigger");
  if (picker) picker.hidden = true;
  if (trigger) {
    trigger.setAttribute("aria-expanded", "false");
    trigger.focus();
  }
  refreshReasoningEffortOptions();
  syncSelectedModelCapabilities();
  showToast(`已选择 ${model}`);
}

function filterModelPicker(value) {
  const query = String(value || "").trim().toLowerCase();
  document.querySelectorAll("#model-picker-list .model-option").forEach(option => {
    const searchable = String(option.getAttribute("data-search") || "").toLowerCase();
    option.hidden = Boolean(query) && !searchable.includes(query);
  });
}

function renderModelPickerOptions() {
  const target = document.getElementById("model-picker-content");
  if (!target) return;
  if (state.modelDiscovery.loading) {
    target.innerHTML = inlineLoadingHtml("正在按当前 Base URL 读取模型");
    return;
  }
  if (state.modelDiscovery.error) {
    target.innerHTML = `
      <div class="model-picker-error">${escapeHtml(state.modelDiscovery.error)}</div>
      <button type="button" class="btn secondary compact" onclick="discoverModels(true)">重新获取</button>
    `;
    return;
  }
  const models = state.modelDiscovery.models || [];
  if (!models.length) {
    target.innerHTML = `<div class="model-picker-empty">展开后会按当前 Base URL 获取模型。</div>`;
    return;
  }
  target.innerHTML = `
    <div class="model-picker-toolbar">
      <input id="model-picker-search" type="search" placeholder="搜索 ${models.length} 个模型" oninput="filterModelPicker(this.value)">
      <button type="button" class="btn secondary compact" onclick="discoverModels(true)">刷新</button>
    </div>
    <div class="model-picker-meta"><span>${models.length} 个模型</span><span>${escapeHtml(state.modelDiscovery.latencyMs)} ms</span></div>
    <div id="model-picker-list" class="model-picker-list" role="listbox">
      ${models.map(renderModelOption).join("")}
    </div>
  `;
}

function renderModelOption(model) {
  const capabilities = model.capabilities || {};
  const reasoning = capabilities.reasoning || "unknown";
  const vision = capabilities.vision || "unknown";
  const efforts = Array.isArray(capabilities.reasoning_efforts)
    ? capabilities.reasoning_efforts.length
    : 0;
  const reasoningLabel = reasoning === "supported"
    ? `推理 ${efforts} 档`
    : `推理 ${capabilityStatusLabel(reasoning)}`;
  return `
    <button
      type="button"
      class="model-option"
      role="option"
      data-model-id="${escapeHtml(model.id)}"
      data-search="${escapeHtml(`${model.id} ${model.owned_by || ""}`)}"
      onclick="selectDiscoveredModel(this.dataset.modelId)"
    >
      <span class="model-option-name"><strong>${escapeHtml(model.id)}</strong>${model.owned_by ? `<small>${escapeHtml(model.owned_by)}</small>` : ""}</span>
      <span class="model-option-capabilities">
        <small data-status="${escapeHtml(reasoning)}">${escapeHtml(reasoningLabel)}</small>
        <small data-status="${escapeHtml(vision)}">视觉 ${escapeHtml(capabilityStatusLabel(vision))}</small>
      </span>
    </button>
  `;
}

function renderStrategyHub() {
  setContent(`
    <section class="hub-intro"><span class="section-kicker">POLICY CONTROL</span><h2>Agent 策略</h2><p>把触发、等待、自动回复与人工接管拆成清晰边界。每次保存都会预览变更并写入审计日志。</p></section>
    <div class="hub-grid">
      ${renderHubLink("触发与等待", "Webhook、轮询、静默窗口和消息合并策略。", "触发配置", "接入")}
      ${renderHubLink("回复与送达", "公开回复开关、发送前复核、可靠送达和人工接管。", "回复策略", "回复")}
    </div>
  `);
}

function renderSystemHub() {
  setContent(`
    <section class="hub-intro"><span class="section-kicker">SYSTEM CONTROL</span><h2>系统与审计</h2><p>运行环境、持久化和所有关键变更都可以在这里追踪。</p></section>
    <div class="hub-grid three">
      ${renderHubLink("部署状态", "镜像、提交、进程状态、SSO 与待重启配置。", "部署状态", "部署")}
      ${renderHubLink("存储配置", "日志、附件、状态文件和配置覆盖的持久化路径。", "存储配置", "存储")}
      ${renderHubLink("审计日志", "登录、配置、密钥、连通性检测和模型同步记录。", "审计日志", "审计")}
    </div>
  `);
}

function renderHubLink(title, description, menu, tag) {
  return `<button class="hub-link" onclick="goMenu('${menu}')"><span>${escapeHtml(tag)}</span><strong>${escapeHtml(title)}</strong><p>${escapeHtml(description)}</p><small>进入配置 →</small></button>`;
}

function renderConfigPage(menu) {
  const fields = fieldsFor(menu);
  if (!fields.length) {
    setContent(emptyHtml("当前模块没有可编辑配置"));
    return;
  }
  setContent(`${renderModuleGuide(menu)}${renderModuleOverview(menu, fields)}${renderForm(menu, fields)}`);
}

async function renderStorageConfig() {
  const deploy = await loadDeployStatus().catch(() => null);
  const fields = fieldsFor("存储配置");
  const baseDir = fieldValue("resources.base_dir") || deploy?.storage?.base_dir || "-";
  const attachmentsDir = fieldValue("resources.attachments_dir") || "-";
  const stateDir = fieldValue("resources.state_dir") || deploy?.storage?.state_dir || "-";
  const logDir = deploy?.storage?.log_dir || "-";
  setContent(`
    ${renderModuleGuide("存储配置")}
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
  setContent(`
    ${renderModuleGuide("工具与集成")}
    <section class="card">
      <div class="card-header">
        <span>工具与集成</span>
        <div class="header-actions">
          <span class="chip">${INTEGRATIONS.length + customMcpServices().length} 个服务</span>
          <span class="chip">检测会写入审计日志</span>
        </div>
      </div>
      <div class="card-body integration-grid">
        ${INTEGRATIONS.map(([name, label, description]) => renderIntegrationCard(name, label, description)).join("")}
        ${customMcpServices().map(service => renderCustomMcpCard(service)).join("")}
      </div>
    </section>
    <section class="card">
      <div class="card-header">
        <span>管理说明</span>
        <span class="chip">只读能力面</span>
      </div>
      <div class="card-body integration-note-grid">
        <div>
          <strong>MCP</strong>
          <p>生产调查只注册经过审核的只读工具。内置 MCP 点击卡片编辑连接配置；自定义 MCP 当前仅做连通性管理，不会自动加入生产工具注册表。</p>
        </div>
      </div>
    </section>
  `);
}

function renderIntegrationCard(name, label, description) {
  const result = state.integrationChecks[name];
  const status = result
    ? `${result.status} · ${result.detail || ""}`
    : "尚未检测";
  const cls = result?.reachable ? "success-text" : result ? "warn-text" : "muted";
  const paths = INTEGRATION_CONFIG_PATHS[name] || [];
  const configured = integrationConfigured(paths);
  return `
    <div class="integration-card action-card" role="button" tabindex="0"
      onclick="openIntegrationConfig(${jsArg(name)})"
      onkeydown="if(event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')){event.preventDefault(); openIntegrationConfig(${jsArg(name)});}">
      <div class="integration-card-head">
        <div>
          <strong>${escapeHtml(label)}</strong>
          <div class="muted">${escapeHtml(description)}</div>
        </div>
        <span class="chip ${configured ? "enabled" : ""}">${configured ? "已配置" : "未配置"}</span>
      </div>
      <div class="integration-status ${cls}">${escapeHtml(status)}</div>
      <div class="integration-card-actions">
        <button class="btn secondary" onclick="event.stopPropagation(); checkIntegration(${jsArg(name)})">测试连接</button>
        <button class="btn secondary" onclick="event.stopPropagation(); openIntegrationConfig(${jsArg(name)})">配置</button>
      </div>
    </div>
  `;
}

function renderCustomMcpCard(service) {
  const name = String(service.name || "");
  const label = service.label || name || "未命名 MCP";
  const description = service.description || "自定义远程 MCP 服务";
  const result = state.integrationChecks[name];
  const status = result
    ? `${result.status} · ${result.detail || ""}`
    : "尚未检测";
  const cls = result?.reachable ? "success-text" : result ? "warn-text" : "muted";
  const enabled = service.enabled !== false;
  const configured = enabled && Boolean(service.mcp_url);
  return `
    <div class="integration-card action-card custom-mcp-card" role="button" tabindex="0"
      onclick="openCustomMcpConfig(${jsArg(name)})"
      onkeydown="if(event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')){event.preventDefault(); openCustomMcpConfig(${jsArg(name)});}">
      <div class="integration-card-head">
        <div>
          <strong>${escapeHtml(label)}</strong>
          <div class="muted">${escapeHtml(description)}</div>
        </div>
        <span class="chip ${configured ? "enabled" : ""}">${enabled ? (configured ? "已配置" : "缺 URL") : "已禁用"}</span>
      </div>
      <div class="integration-status ${cls}">${escapeHtml(status)}</div>
      <div class="integration-card-actions">
        <button class="btn secondary" onclick="event.stopPropagation(); checkIntegration(${jsArg(name)})">测试连接</button>
        <button class="btn secondary" onclick="event.stopPropagation(); openCustomMcpConfig(${jsArg(name)})">配置</button>
      </div>
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

async function testIntegrationFromModal(name) {
  const node = document.getElementById("integration-modal-status");
  if (node) {
    node.className = "integration-modal-status muted";
    node.textContent = "检测中";
  }
  try {
    const result = await api("/admin/api/integrations/check", {
      method: "POST",
      body: JSON.stringify({ integration: name })
    });
    state.integrationChecks[name] = result;
    if (node) {
      node.className = `integration-modal-status ${result.reachable ? "success-text" : "warn-text"}`;
      node.textContent = `${result.status} · ${result.detail || ""}`;
    }
  } catch (err) {
    state.integrationChecks[name] = {
      status: "failed",
      detail: err.message || "检测失败",
      reachable: false
    };
    if (node) {
      node.className = "integration-modal-status danger-text";
      node.textContent = err.message || "检测失败";
    }
  }
}

function openIntegrationConfig(name) {
  const integration = INTEGRATIONS.find(item => item[0] === name);
  if (!integration) return;
  const [, label, description] = integration;
  const fields = fieldsByPaths(INTEGRATION_CONFIG_PATHS[name] || []);
  showModal(`${label} 配置`, `
    <div class="modal-config-summary">
      <div>
        <strong>${escapeHtml(label)}</strong>
        <p>${escapeHtml(description)}</p>
      </div>
      <span id="integration-modal-status" class="integration-modal-status muted">${escapeHtml(integrationStatusText(name))}</span>
    </div>
    <div class="modal-form-grid">
      ${fields.map(renderFieldRow).join("")}
    </div>
    ${renderReasonBox(`${label} 连接配置调整`)}
  `, [
    { label: "测试连接", className: "secondary", onClick: () => testIntegrationFromModal(name) },
    { label: "取消", className: "secondary", onClick: closeModal },
    { label: "保存", className: "primary", onClick: () => saveModalConfig(`${label} 连接配置调整`) }
  ]);
}

function openMcpManager() {
  showModal("自定义 MCP 管理", `
    <div class="manager-toolbar">
      <div>
        <strong>自定义 MCP 服务</strong>
        <p>用于登记内置 MCP 之外的远程服务并执行连通性检查；它不会自动加入生产工具注册表。</p>
      </div>
      <button class="btn secondary compact" onclick="addMcpManagerItem()">添加 MCP</button>
    </div>
    <div id="mcp-manager-list" class="manager-list">
      ${customMcpServices().map(service => renderMcpManagerItem(service)).join("") || emptyHtml("还没有自定义 MCP")}
    </div>
    ${renderReasonBox("调整自定义 MCP 配置")}
  `, [
    { label: "取消", className: "secondary", onClick: closeModal },
    { label: "保存", className: "primary", onClick: saveMcpManager }
  ]);
}

function renderMcpManagerItem(service = {}) {
  const name = service.name || "";
  return `
    <div class="manager-item" data-mcp-item data-original-name="${escapeHtml(name)}">
      <div class="manager-item-head">
        <strong>${escapeHtml(service.label || name || "新 MCP")}</strong>
        <button class="btn secondary compact" onclick="removeManagerItem(this)">删除</button>
      </div>
      ${renderMcpFieldGrid(service)}
    </div>
  `;
}

function renderMcpFieldGrid(service = {}) {
  return `
    <div class="manager-field-grid">
      <label>服务名</label>
      <input data-mcp-field="name" value="${escapeHtml(service.name || "")}" placeholder="例如 linear_mcp">
      <span class="muted">调用时使用的 service</span>
      <label>展示名</label>
      <input data-mcp-field="label" value="${escapeHtml(service.label || "")}" placeholder="例如 Linear MCP">
      <span class="muted">卡片标题</span>
      <label>说明</label>
      <input data-mcp-field="description" value="${escapeHtml(service.description || "")}" placeholder="这个 MCP 提供什么能力">
      <span class="muted">卡片说明</span>
      <label>MCP URL</label>
      <input data-mcp-field="mcp_url" value="${escapeHtml(service.mcp_url || "")}" placeholder="https://example.com/mcp">
      <span class="muted">支持 http(s)</span>
      <label>API Key</label>
      <input data-mcp-field="mcp_api_key" type="password" placeholder="${service.mcp_api_key ? "已配置，留空保持不变" : "可选"}">
      <span class="muted">保存后用于 Authorization</span>
      <label>超时秒数</label>
      <input data-mcp-field="request_timeout_seconds" type="number" value="${escapeHtml(service.request_timeout_seconds || 35)}">
      <span class="muted">最小 3 秒</span>
      <label>启用</label>
      <select data-mcp-field="enabled">
        <option value="true" ${service.enabled !== false ? "selected" : ""}>开启</option>
        <option value="false" ${service.enabled === false ? "selected" : ""}>关闭</option>
      </select>
      <span class="muted">关闭后不可检测和调用</span>
    </div>
  `;
}

function addMcpManagerItem() {
  addManagerItem("mcp-manager-list", renderMcpManagerItem({ enabled: true, request_timeout_seconds: 35 }));
}

function openCustomMcpConfig(name) {
  const service = customMcpServices().find(item => item.name === name);
  if (!service) return;
  showModal(`${service.label || service.name} 配置`, `
    <div class="modal-config-summary">
      <div>
        <strong>${escapeHtml(service.label || service.name)}</strong>
        <p>${escapeHtml(service.description || "自定义远程 MCP 服务")}</p>
      </div>
      <span id="integration-modal-status" class="integration-modal-status muted">${escapeHtml(integrationStatusText(service.name))}</span>
    </div>
    <div class="manager-item single-manager-item" data-mcp-item data-original-name="${escapeHtml(service.name)}">
      ${renderMcpFieldGrid(service)}
    </div>
    ${renderReasonBox(`${service.label || service.name} 配置调整`)}
  `, [
    { label: "删除", className: "danger", onClick: () => deleteCustomMcp(service.name) },
    { label: "测试连接", className: "secondary", onClick: () => testIntegrationFromModal(service.name) },
    { label: "取消", className: "secondary", onClick: closeModal },
    { label: "保存", className: "primary", onClick: () => saveSingleCustomMcp(service.name) }
  ]);
}

async function saveMcpManager() {
  const error = document.getElementById("modal-save-error");
  const services = collectMcpManagerServices(document);
  await applyConfigPayload({ "mcp_services.custom": services }, reasonFromModal("调整自定义 MCP 配置"), error);
}

async function saveSingleCustomMcp(originalName) {
  const error = document.getElementById("modal-save-error");
  const [updated] = collectMcpManagerServices(document);
  if (!updated) {
    if (error) error.textContent = "MCP 配置不能为空";
    return;
  }
  const services = customMcpServices().map(service => (
    service.name === originalName ? updated : service
  ));
  await applyConfigPayload({ "mcp_services.custom": services }, reasonFromModal(`${originalName} 配置调整`), error);
}

async function deleteCustomMcp(name) {
  const error = document.getElementById("modal-save-error");
  const services = customMcpServices().filter(service => service.name !== name);
  await applyConfigPayload({ "mcp_services.custom": services }, reasonFromModal(`删除自定义 MCP ${name}`), error);
}

function addManagerItem(listId, html) {
  const list = document.getElementById(listId);
  if (!list) return;
  if (list.querySelector(".empty-state")) {
    list.innerHTML = "";
  }
  list.insertAdjacentHTML("beforeend", html);
}

function removeManagerItem(button) {
  const item = button.closest("[data-mcp-item]");
  if (item) item.remove();
}

function collectMcpManagerServices(root) {
  const existingByName = new Map(customMcpServices().map(service => [service.name, service]));
  return Array.from(root.querySelectorAll("[data-mcp-item]")).map(item => {
    const field = name => item.querySelector(`[data-mcp-field='${name}']`);
    const originalName = item.getAttribute("data-original-name") || "";
    const existing = existingByName.get(originalName) || {};
    const newKey = field("mcp_api_key").value.trim();
    return {
      name: field("name").value.trim(),
      label: field("label").value.trim(),
      description: field("description").value.trim(),
      mcp_url: field("mcp_url").value.trim(),
      mcp_api_key: newKey || existing.mcp_api_key || "",
      request_timeout_seconds: Number(field("request_timeout_seconds").value || 35),
      enabled: field("enabled").value === "true"
    };
  }).filter(service => service.name || service.label || service.mcp_url);
}

function fieldsFor(menu) {
  return (state.schema.fields || []).filter(field => field.group === menu);
}

function fieldByPath(path) {
  return (state.schema.fields || []).find(field => field.path === path);
}

function fieldsByPaths(paths) {
  return paths.map(fieldByPath).filter(Boolean);
}

function jsArg(value) {
  return escapeHtml(JSON.stringify(value));
}

function integrationConfigured(paths) {
  const firstUrlPath = paths.find(path => path.endsWith("url") || path.endsWith("mcp_url"));
  return Boolean(firstUrlPath && fieldValue(firstUrlPath));
}

function integrationStatusText(name) {
  const result = state.integrationChecks[name];
  return result ? `${result.status} · ${result.detail || ""}` : "尚未检测";
}

function customMcpServices() {
  const value = state.values["mcp_services.custom"];
  return Array.isArray(value) ? value : [];
}

function renderReasonBox(placeholder) {
  return `
    <div class="modal-reason-box">
      <label for="modal-change-reason">变更原因</label>
      <textarea id="modal-change-reason" rows="3" placeholder="${escapeHtml(placeholder)}"></textarea>
      <div id="modal-save-error" class="danger-text"></div>
    </div>
  `;
}

function renderForm(title, fields) {
  const sections = fieldSectionsFor(title, fields);
  if (!sections.length) return "";
  const activeIndex = activeConfigSectionIndex(title, sections);
  return `
    <div class="config-section-stack">
      ${renderConfigSectionPicker(title, sections, activeIndex)}
      ${renderConfigSectionDetails(sections[activeIndex])}
    </div>
  `;
}

function fieldSectionsFor(menu, fields) {
  const fieldByPath = new Map(fields.map(field => [field.path, field]));
  const used = new Set();
  const configuredSections = FIELD_SECTIONS[menu] || [];
  const sections = configuredSections
    .map(section => {
      const sectionFields = section.paths
        .map(path => fieldByPath.get(path))
        .filter(Boolean);
      sectionFields.forEach(field => used.add(field.path));
      return { ...section, fields: sectionFields };
    })
    .filter(section => section.fields.length);
  const remainingFields = fields.filter(field => !used.has(field.path));
  if (remainingFields.length) {
    sections.push({
      title: "其他配置",
      description: "尚未归入专门小节，但仍可在后台编辑。",
      fields: remainingFields
    });
  }
  return sections;
}

function activeConfigSectionIndex(menu, sections) {
  const activeTitle = state.activeConfigSections[menu];
  const index = sections.findIndex(section => section.title === activeTitle);
  return index >= 0 ? index : 0;
}

function selectConfigSection(menu, index) {
  const sections = fieldSectionsFor(menu, fieldsFor(menu));
  const section = sections[index];
  if (!section) return;
  state.activeConfigSections[menu] = section.title;
  void render();
}

function renderConfigSectionPicker(menu, sections, activeIndex) {
  return `
    <section class="card config-section-picker-card">
      <div class="config-section-picker-head">
        <div>
          <span>配置分组</span>
          <p>点击卡片查看配置，只展开当前需要调整的部分。</p>
        </div>
        <span class="chip">${sections.length} 个分组</span>
      </div>
      <div class="config-section-picker">
        ${sections.map((section, index) => renderConfigSectionTile(menu, section, index, activeIndex)).join("")}
      </div>
    </section>
  `;
}

function renderConfigSectionTile(menu, section, index, activeIndex) {
  const isActive = index === activeIndex;
  return `
    <button
      type="button"
      class="config-section-tile ${isActive ? "active" : ""}"
      aria-pressed="${isActive ? "true" : "false"}"
      onclick="selectConfigSection(${escapeHtml(JSON.stringify(menu))}, ${index})"
    >
      <div class="config-section-tile-head">
        <strong>${escapeHtml(section.title)}</strong>
        <span>${isActive ? "已选中" : "打开"}</span>
      </div>
      <p>${escapeHtml(section.description || "查看并编辑这一组配置。")}</p>
      <div class="config-section-tile-foot">
        <span>${section.fields.length} 项配置</span>
        <span class="config-section-arrow">›</span>
      </div>
    </button>
  `;
}

function renderConfigSectionDetails(section) {
  return `
    <section class="card config-section-card config-section-detail">
      <div class="card-header config-section-header">
        <div>
          <span>${escapeHtml(section.title)}</span>
          <p>${escapeHtml(section.description || "")}</p>
        </div>
        <span class="chip">${section.fields.length} 项配置</span>
      </div>
      <div class="card-body form-grid">
        ${section.fields.map(renderFieldRow).join("")}
      </div>
    </section>
  `;
}

function renderFieldRow(field) {
  const labelTarget = field.input === "model_picker"
    ? "model-picker-trigger"
    : field.input === "vision_capability"
      ? "model-vision-capability"
      : `field-${field.path}`;
  const secretProvider = field.path === "openai.api_key"
    ? "openai"
    : field.path === "anthropic.api_key"
      ? "anthropic"
      : "";
  const providerAttribute = secretProvider
    ? ` data-provider-secret="${secretProvider}"`
    : "";
  const renderedInput = secretProvider
    ? `<div${providerAttribute}>${renderInput(field)}</div>`
    : renderInput(field);
  return `
    <label for="${labelTarget}" title="${escapeHtml(field.path)}"${providerAttribute}>${escapeHtml(field.label)}</label>
    ${renderedInput}
    <span id="field-help-${field.path}" class="field-help ${field.restart_required ? "warn-text" : "muted"}"${providerAttribute}>
      ${fieldHelpText(field)}
    </span>
  `;
}

function fieldHelpText(field) {
  if (field.secret) return "可替换密钥";
  if (field.input === "model_picker") return "展开下拉框，按当前 Base URL 获取模型";
  if (field.input === "reasoning_effort") return "按所选模型限制档位；新会话立即生效";
  if (field.input === "vision_capability") return "由所选模型自动决定，不提供人工开关";
  if (field.restart_required) return "需重启生效";
  if (field.protected) return "受保护";
  return "立即生效";
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

function renderModuleGuide(menu) {
  const lines = GROUP_DESCRIPTIONS[menu] || [];
  if (!lines.length) return "";
  return `
    <section class="module-guide">
      <div>
        <strong>${escapeHtml(menu)}管理</strong>
        <p>${escapeHtml(lines[0])}</p>
      </div>
      <span>${escapeHtml(lines[1] || "保存前可预览变更，保存后写入审计日志。")}</span>
    </section>
  `;
}

function renderInput(field) {
  const value = state.values[field.path];
  const displayValue = fieldValue(field.path);
  const id = `field-${field.path}`;
  if (field.secret) {
    return `
      <div class="secret-input">
        <input id="${id}" disabled value="${escapeHtml(displayValue)}">
        <input data-path="${field.path}" type="password" placeholder="输入新密钥后保存">
      </div>
    `;
  }
  if (field.input === "model_picker") {
    return renderModelPickerInput(field);
  }
  if (field.input === "vision_capability") {
    return renderVisionCapabilityInput(field);
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
  if (field.input === "reasoning_effort") {
    return renderReasoningEffortInput(field);
  }
  if (field.input === "textarea") {
    return `<textarea id="${id}" data-path="${field.path}" class="prompt-textarea" rows="7">${escapeHtml(displayValue)}</textarea>`;
  }
  if (field.input === "json") {
    return `<textarea id="${id}" data-path="${field.path}" class="json-textarea" rows="10">${escapeHtml(formatJsonFieldValue(value))}</textarea>`;
  }
  const inputType = field.input === "number" ? "number" : "text";
  if (field.path === "llm.provider") {
    const provider = String(displayValue || "openai").toLowerCase();
    return `
      <select id="${id}" data-path="${field.path}" onchange="handleModelProviderChange()">
        <option value="openai" ${provider === "openai" ? "selected" : ""}>OpenAI Compatible</option>
        <option value="anthropic" ${provider === "anthropic" ? "selected" : ""}>Anthropic</option>
      </select>
    `;
  }
  const modelCatalogRefresh = ["llm.provider", "llm.base_url"].includes(field.path)
    ? ' oninput="invalidateModelCatalog()"'
    : "";
  return `<input id="${id}" data-path="${field.path}" type="${inputType}" value="${escapeHtml(displayValue)}"${modelCatalogRefresh}>`;
}

function renderModelPickerInput(field) {
  const model = String(fieldValue(field.path) || "");
  return `
    <div class="model-picker-control">
      <input id="field-${field.path}" data-path="${field.path}" type="hidden" value="${escapeHtml(model)}">
      <button
        id="model-picker-trigger"
        type="button"
        class="model-picker-trigger"
        aria-haspopup="listbox"
        aria-expanded="false"
        onclick="toggleModelPicker()"
      >
        <span id="model-picker-current">${escapeHtml(model || "请选择模型")}</span>
        <small>选择模型</small>
      </button>
      <div id="model-picker" class="model-picker-popover" hidden>
        <div class="model-picker-heading">
          <div><strong>可用模型</strong><span>来自当前 Base URL</span></div>
          <button type="button" class="model-picker-close" onclick="toggleModelPicker()">关闭</button>
        </div>
        <div id="model-picker-content"><div class="model-picker-empty">展开后会按当前 Base URL 获取模型。</div></div>
      </div>
      <div id="model-capability-summary" class="model-capability-summary">
        ${selectedModelCapabilitySummaryHtml(model)}
      </div>
    </div>
  `;
}

function renderVisionCapabilityInput(field) {
  const configured = state.values[field.path] === true || state.values[field.path] === "true";
  const capabilities = selectedModelCapabilities(reasoningModelValue("llm.model"));
  const discovered = discoveredModelCapabilities(reasoningModelValue("llm.model"));
  const effective = capabilities.vision === "supported"
    ? true
    : capabilities.vision === "unsupported" || discovered
      ? false
      : configured;
  return `
    <div class="vision-capability-control">
      <input
      id="field-${field.path}"
      type="hidden"
      data-path="${field.path}"
      data-vision-model-path="llm.model"
      value="${effective ? "true" : "false"}"
      >
      <div id="model-vision-capability" class="vision-capability-readonly" data-status="${escapeHtml(capabilities.vision)}" tabindex="0">
        ${visionCapabilityReadonlyHtml(capabilities)}
      </div>
    </div>
  `;
}

function visionCapabilityReadonlyHtml(capabilities) {
  const status = capabilities.vision || "unknown";
  const title = status === "supported"
    ? "支持视觉输入"
    : status === "unsupported"
      ? "不支持视觉输入"
      : "视觉能力未知";
  const detail = status === "unknown"
    ? "供应商未返回能力信息；保存时按不支持处理"
    : capabilitySourceLabel(capabilities.vision_source);
  return `<strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small>`;
}

function normalizedModelId(model) {
  return String(model || "").trim().toLowerCase().split("/").pop();
}

function reasoningEffortsForModel(model) {
  const discovered = discoveredModelCapabilities(model);
  if (discovered && Array.isArray(discovered.reasoning_efforts)) {
    return discovered.reasoning_efforts;
  }
  const rule = reasoningCapabilityForModel(model);
  return rule && Array.isArray(rule.efforts) ? rule.efforts : [];
}

function reasoningCapabilityForModel(model) {
  const normalized = normalizedModelId(model);
  for (const rule of state.schema?.reasoning_capabilities || []) {
    try {
      if (new RegExp(rule.model_pattern, "i").test(normalized)) {
        return rule;
      }
    } catch {
      continue;
    }
  }
  return null;
}

function discoveredModelCapabilities(model) {
  if (!modelCatalogMatchesCurrentInputs()) return null;
  const selected = (state.modelDiscovery.models || []).find(item => item.id === model);
  return selected?.capabilities || null;
}

function selectedModelCapabilities(model) {
  if (modelDiscoveryContext().provider !== "openai") {
    const discovered = discoveredModelCapabilities(model);
    return {
      reasoning: "unsupported",
      reasoning_efforts: [],
      reasoning_source: "agent_adapter",
      vision: discovered?.vision || "unknown",
      vision_source: discovered?.vision_source || "unreported"
    };
  }
  const discovered = discoveredModelCapabilities(model);
  if (discovered) {
    return {
      reasoning: discovered.reasoning || "unknown",
      reasoning_efforts: Array.isArray(discovered.reasoning_efforts)
        ? discovered.reasoning_efforts
        : [],
      reasoning_source: discovered.reasoning_source || "unreported",
      vision: discovered.vision || "unknown",
      vision_source: discovered.vision_source || "unreported"
    };
  }
  const reasoningRule = reasoningCapabilityForModel(model);
  const efforts = reasoningRule && Array.isArray(reasoningRule.efforts)
    ? reasoningRule.efforts
    : [];
  return {
    reasoning: reasoningRule ? (efforts.length ? "supported" : "unsupported") : "unknown",
    reasoning_efforts: efforts,
    reasoning_source: reasoningRule ? "agent_registry" : "unreported",
    vision: "unknown",
    vision_source: "unreported"
  };
}

function capabilityStatusLabel(status) {
  if (status === "supported") return "支持";
  if (status === "unsupported") return "不支持";
  return "未知";
}

function capabilitySourceLabel(source) {
  if (source === "provider_metadata") return "供应商返回";
  if (source === "agent_registry") return "Agent 已验证";
  if (source === "agent_adapter") return "当前适配器限制";
  return "供应商未返回";
}

function selectedModelCapabilitySummaryHtml(model) {
  const capabilities = selectedModelCapabilities(model);
  const reasoningCount = capabilities.reasoning_efforts.length;
  const reasoningLabel = capabilities.reasoning === "supported"
    ? `推理强度 ${reasoningCount} 档`
    : `推理强度 ${capabilityStatusLabel(capabilities.reasoning)}`;
  return `
    <span data-status="${escapeHtml(capabilities.reasoning)}" title="${escapeHtml(capabilitySourceLabel(capabilities.reasoning_source))}">${escapeHtml(reasoningLabel)}</span>
    <span data-status="${escapeHtml(capabilities.vision)}" title="${escapeHtml(capabilitySourceLabel(capabilities.vision_source))}">视觉 ${escapeHtml(capabilityStatusLabel(capabilities.vision))}</span>
  `;
}

function reasoningUnsupportedReason(_modelPath, model) {
  const capabilities = selectedModelCapabilities(model);
  if (capabilities.reasoning === "unsupported") return "该模型不支持可配置推理强度";
  if (capabilities.reasoning === "unknown") return "推理强度未知，暂不可配置";
  return "";
}

function reasoningModelValue(modelPath) {
  const input = document.getElementById(`field-${modelPath}`);
  const model = input ? input.value : state.values[modelPath];
  return model || "";
}

function reasoningDefaultLabel(model) {
  return normalizedModelId(model).startsWith("gpt-5.6")
    ? "自动（Chat 工具轮使用 none）"
    : "模型默认";
}

function reasoningOptionsHtml(model, selected, modelPath = "llm.model") {
  const unsupported = reasoningUnsupportedReason(modelPath, model);
  if (unsupported) {
    return `<option value="" selected>${escapeHtml(unsupported)}</option>`;
  }
  const efforts = reasoningEffortsForModel(model);
  const normalizedSelected = efforts.includes(selected) ? selected : "";
  const options = [
    `<option value="" ${normalizedSelected === "" ? "selected" : ""}>${escapeHtml(reasoningDefaultLabel(model))}</option>`,
    ...efforts.map(effort => (
      `<option value="${escapeHtml(effort)}" ${normalizedSelected === effort ? "selected" : ""}>${escapeHtml(effort)}</option>`
    ))
  ];
  return options.join("");
}

function renderReasoningEffortInput(field) {
  const modelPath = field.model_path || "llm.model";
  const model = reasoningModelValue(modelPath);
  const selected = String(state.values[field.path] || "").trim().toLowerCase();
  const disabled = (
    reasoningEffortsForModel(model).length === 0
    || Boolean(reasoningUnsupportedReason(modelPath, model))
  ) ? " disabled" : "";
  return `
    <select
      id="field-${field.path}"
      data-path="${field.path}"
      data-reasoning-model-path="${modelPath}"
      ${disabled}
    >
      ${reasoningOptionsHtml(model, selected, modelPath)}
    </select>
  `;
}

function refreshReasoningEffortOptions() {
  document.querySelectorAll("select[data-reasoning-model-path]").forEach(select => {
    const modelPath = select.getAttribute("data-reasoning-model-path") || "llm.model";
    const model = reasoningModelValue(modelPath);
    const selected = select.value;
    select.innerHTML = reasoningOptionsHtml(model, selected, modelPath);
    select.disabled = (
      reasoningEffortsForModel(model).length === 0
      || Boolean(reasoningUnsupportedReason(modelPath, model))
    );
  });
}

function syncSelectedModelCapabilities() {
  const model = reasoningModelValue("llm.model");
  const capabilities = selectedModelCapabilities(model);
  const summary = document.getElementById("model-capability-summary");
  if (summary) summary.innerHTML = selectedModelCapabilitySummaryHtml(model);

  const reasoningHelp = document.getElementById("field-help-llm.reasoning_effort");
  if (reasoningHelp) {
    reasoningHelp.textContent = capabilities.reasoning === "supported"
      ? `支持 ${capabilities.reasoning_efforts.join(" / ")}；${capabilitySourceLabel(capabilities.reasoning_source)}`
      : `${capabilityStatusLabel(capabilities.reasoning)}；${capabilitySourceLabel(capabilities.reasoning_source)}`;
  }

  const vision = document.querySelector("input[data-vision-model-path]");
  const discoveredVision = discoveredModelCapabilities(model);
  if (vision && capabilities.vision === "supported") {
    vision.value = "true";
  } else if (
    vision
    && (capabilities.vision === "unsupported" || discoveredVision)
  ) {
    vision.value = "false";
  }
  const visionDisplay = document.getElementById("model-vision-capability");
  if (visionDisplay) {
    visionDisplay.setAttribute("data-status", capabilities.vision);
    visionDisplay.innerHTML = visionCapabilityReadonlyHtml(capabilities);
  }
  const visionHelp = document.getElementById("field-help-llm.enable_vision_input");
  if (visionHelp) {
    visionHelp.textContent = capabilities.vision === "unknown"
      ? "能力未知时按不支持处理；不可人工覆盖"
      : `由模型自动设为${capabilities.vision === "supported" ? "开启" : "关闭"}；${capabilitySourceLabel(capabilities.vision_source)}`;
  }
}

function fieldValue(path) {
  const value = state.values[path];
  if (value && typeof value === "object" && "configured" in value) {
    return value.configured ? `已配置 · ${value.fingerprint || ""}` : "未配置";
  }
  if (Array.isArray(value)) return value.join("\n");
  if (value === null || value === undefined) return "";
  return value;
}

function collectChanges() {
  return collectChangesFrom(document);
}

function collectChangesFrom(root) {
  const changes = {};
  root.querySelectorAll("[data-path]").forEach(input => {
    const path = input.getAttribute("data-path");
    if (!path) return;
    if (input.type === "password" && !input.value) return;
    const oldValue = state.values[path];
    const nextValue = input.value;
    if (typeof oldValue === "object" && oldValue && "configured" in oldValue) {
      if (nextValue) changes[path] = nextValue;
    } else if (fieldComparableValue(oldValue) !== String(nextValue)) {
      changes[path] = nextValue;
    }
  });
  return changes;
}

function fieldComparableValue(value) {
  if (Array.isArray(value)) return value.join("\n");
  if (value && typeof value === "object" && !("configured" in value)) {
    return formatJsonFieldValue(value);
  }
  return String(value ?? "");
}

function formatJsonFieldValue(value) {
  if (value === undefined || value === null || value === "") return "[]";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
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
      ${result.worker_reload_started ? "<p>Agent Worker 正在安全重载，新配置将在替换进程启动后生效。</p>" : ""}
      ${result.restart_required ? "<p class=\"warn-text\">本次变更包含需重启生效的配置。</p>" : ""}
    `, [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
    if (result.worker_reload_started) scheduleLifecyclePoll();
  } catch (err) {
    error.textContent = err.message || "保存失败";
  }
}

async function saveModalConfig(defaultReason) {
  const body = document.getElementById("modal-body");
  const error = document.getElementById("modal-save-error");
  const changes = collectChangesFrom(body);
  if (!Object.keys(changes).length) {
    if (error) error.textContent = "没有检测到配置变更";
    return;
  }
  await applyConfigPayload(changes, reasonFromModal(defaultReason), error);
}

function reasonFromModal(defaultReason) {
  const reason = document.getElementById("modal-change-reason")?.value.trim() || "";
  return reason || defaultReason;
}

async function applyConfigPayload(changes, reason, errorNode) {
  if (!reason) {
    if (errorNode) errorNode.textContent = "变更原因不能为空";
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
      ${result.worker_reload_started ? "<p>Agent Worker 正在安全重载，新配置将在替换进程启动后生效。</p>" : ""}
      ${result.restart_required ? "<p class=\"warn-text\">本次变更包含需重启生效的配置。</p>" : ""}
    `, [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
    if (result.worker_reload_started) scheduleLifecyclePoll();
  } catch (err) {
    if (errorNode) {
      errorNode.textContent = err.message || "保存失败";
    }
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
  const agent = deploy.agent || { status: "unknown" };
  setContent(`
    ${renderModuleGuide("部署状态")}
    ${renderAgentLifecycleCard(agent)}
    <div class="metric-grid">
      ${metricCard("Agent Worker", agentStatusLabel(agent.status), agent.process_running ? `PID ${agent.pid || "-"}` : "当前没有 Agent 进程", agent.status === "running" ? "success-text" : "warn-text")}
      ${metricCard("镜像标签", deploy.image?.tag || "latest", deploy.image?.name || "zd-agent")}
      ${metricCard("提交", shortSha(deploy.image?.commit_sha) || "--", "当前部署版本")}
      ${metricCard("待重启", deploy.pending_restart ? "有" : "无", deploy.pending_restart ? "存在需重启配置" : "配置已生效", deploy.pending_restart ? "warn-text" : "success-text")}
    </div>
    <div class="auth-method-grid">
      ${authMethodCard("账号密码登录", deploy.admin_auth?.password_login?.enabled, "默认账号 admin，首次启动使用服务器生成的一次性密码")}
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

function agentStatusLabel(status) {
  return ({
    running: "运行中",
    stopped: "已关闭",
    starting: "正在开启",
    stopping: "正在安全关闭",
    restarting: "正在重启",
    failed: "启动失败"
  })[status] || "未知";
}

function renderAgentLifecycleCard(agent) {
  const status = agent.status || "unknown";
  const busy = ["stopping", "restarting"].includes(status);
  const active = Number(agent.active || 0);
  const desiredRunning = agent.desired_state === "running";
  const canStart = agent.desired_state === "stopped";
  const canStop = desiredRunning && status !== "stopping";
  const canRestart = desiredRunning && active === 0 && !["stopping", "restarting"].includes(status);
  const details = status === "stopped"
    ? "控制面仍保持在线；Zendesk 事件会在验签后丢弃，不会进入处理队列。"
    : status === "stopping"
      ? "已禁止创建新会话；正在等待现有处理和发送副作用安全完成，然后彻底退出 Worker。"
    : active
      ? `当前还有 ${active} 个会话运行；关闭会等待它们安全结束。`
      : "Worker 独立运行；关闭后服务器中不会保留 Agent 处理进程。";
  return `
    <section class="card lifecycle-card ${status === "stopped" ? "is-stopped" : ""}">
      <div class="lifecycle-copy">
        <span class="section-kicker">AGENT LIFECYCLE</span>
        <div class="lifecycle-title-row">
          <h2>Agent 运行控制</h2>
          <span class="chip ${status === "running" ? "enabled" : status === "failed" ? "danger-chip" : ""}">${escapeHtml(agentStatusLabel(status))}</span>
        </div>
        <p>${escapeHtml(details)}</p>
        <div class="lifecycle-meta">
          <span>代际 <strong>${escapeHtml(agent.generation ?? 0)}</strong></span>
          <span>活跃会话 <strong>${active}</strong></span>
          <span>停机丢弃 <strong>${escapeHtml(agent.dropped_events ?? 0)}</strong></span>
          <span>积压策略 <strong>不补跑</strong></span>
        </div>
      </div>
      <div class="lifecycle-actions">
        <button class="btn primary" ${canStart && !busy ? "" : "disabled"} onclick="openAgentAction('start')">开启 Agent</button>
        <button class="btn secondary" ${canRestart && !busy ? "" : "disabled"} onclick="openAgentAction('restart')">重启 Agent</button>
        <button class="btn danger" ${canStop ? "" : "disabled"} onclick="openAgentAction('stop')">关闭 Agent</button>
      </div>
    </section>
  `;
}

function openAgentAction(action) {
  const config = {
    start: {
      title: "开启 Agent",
      warning: "将创建新的 Agent Worker 进程。开启时刻以前的队列、停机消息和轮询结果不会补跑。",
      button: "确认开启"
    },
    restart: {
      title: "重启 Agent",
      warning: "只重启 Agent Worker，控制面保持在线。已有活跃会话或待确认副作用时会拒绝操作。",
      button: "确认重启"
    },
    stop: {
      title: "关闭 Agent",
      warning: "Agent Worker 将彻底退出。控制面继续在线，但停机期间收到的工单事件会被永久丢弃，重新开启后不会补跑。",
      button: "确认关闭"
    }
  }[action];
  showModal(config.title, `
    <div class="lifecycle-warning">${escapeHtml(config.warning)}</div>
    <label class="field-stack" for="lifecycle-reason">
      <span>操作原因</span>
      <textarea id="lifecycle-reason" rows="3" maxlength="500" placeholder="请填写本次操作原因"></textarea>
    </label>
    <div id="lifecycle-action-error" class="form-message danger-text" role="alert"></div>
  `, [
    { label: "取消", className: "secondary", onClick: closeModal },
    { label: config.button, className: action === "stop" ? "danger" : "primary", onClick: () => submitAgentAction(action) }
  ]);
  document.getElementById("lifecycle-reason")?.focus();
}

async function submitAgentAction(action) {
  const reason = document.getElementById("lifecycle-reason")?.value?.trim() || "";
  const errorNode = document.getElementById("lifecycle-action-error");
  if (reason.length < 3) {
    errorNode.textContent = "请填写至少 3 个字符的操作原因";
    return;
  }
  const confirmation = {
    start: "START_AGENT",
    stop: "STOP_AGENT",
    restart: "RESTART_AGENT"
  }[action];
  const operationId = globalThis.crypto?.randomUUID?.() || `op-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    await api("/admin/api/agent/actions", {
      method: "POST",
      headers: { "X-ZD-Agent-Action": "lifecycle" },
      body: JSON.stringify({ action, confirmation, reason, operation_id: operationId })
    });
    closeModal();
    await renderDeployStatus();
    scheduleLifecyclePoll();
  } catch (err) {
    const message = err.message === "active_sessions"
      ? "仍有工单正在处理，完成前不能关闭或重启"
      : err.message === "unsafe_outbox"
        ? "仍有发送或所有权副作用待确认，为避免重复回复，当前拒绝关闭或重启"
        : err.message === "lifecycle_operation_in_progress"
          ? "另一个 Agent 生命周期操作仍在进行，请等待当前操作完成"
        : err.message || "操作失败";
    errorNode.textContent = message;
  }
}

function scheduleLifecyclePoll() {
  if (lifecyclePollTimer) clearTimeout(lifecyclePollTimer);
  lifecyclePollTimer = setTimeout(async () => {
    try {
      const result = await api("/admin/api/agent/lifecycle");
      const status = result.agent?.status || "unknown";
      if (state.activeMenu === "部署状态") await renderDeployStatus();
      if (["starting", "stopping", "restarting"].includes(status)) {
        scheduleLifecyclePoll();
      }
    } catch (_err) {
      scheduleLifecyclePoll();
    }
  }, 1000);
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

async function ensureRunRecordDetail(index) {
  const record = state.runDisplayRecords[index];
  if (!record) return null;
  if (record.raw?.detail_loaded) return record;
  const recordKey = record.raw?.record_key || record.key;
  if (!recordKey) return record;
  const data = await loadRunRecords(1, { record_key: recordKey }, {
    detail: true,
    updateState: false
  });
  const item = data.items?.[0];
  if (!item) {
    throw new Error("没有找到这条运行记录的完整日志");
  }
  const rawIndex = state.runRecords.findIndex(raw => (raw.record_key || "") === recordKey);
  if (rawIndex >= 0) {
    state.runRecords[rawIndex] = item;
  }
  if (state.runRecordsPayload?.items && rawIndex >= 0) {
    state.runRecordsPayload.items[rawIndex] = item;
  }
  const [detailed] = buildRunDisplayRecords([item]);
  state.runDisplayRecords[index] = detailed;
  return detailed;
}

async function openRunRecordDetail(index) {
  const current = state.runDisplayRecords[index];
  if (!current) return;
  openDrawer(
    `${current.ticketLabel} 运行日志`,
    "正在读取完整日志链路",
    inlineLoadingHtml("正在读取完整日志链路")
  );
  try {
    const record = await ensureRunRecordDetail(index);
    if (!record) return;
    openRunRecordDrawer(record);
  } catch (err) {
    openDrawer(
      `${current.ticketLabel} 运行日志`,
      "读取失败",
      errorHtml(err.message || "详情加载失败")
    );
  }
}

function openRunRecordDrawer(record) {
  const item = record.raw;
  openDrawer(
    `${record.ticketLabel} 运行日志`,
    `${formatTime(record.time)} · ${record.eventCount || 0} 条事件`,
    `
      ${renderRunDrawerSummary(record)}
      <section class="drawer-section">
        <div class="drawer-section-title">轮次与工具</div>
        <div class="round-timeline drawer-rounds">
          ${record.rounds.map((round, roundIndex) => renderRound(round, roundIndex)).join("")}
        </div>
      </section>
      <section class="drawer-section">
        <div class="drawer-section-title">事件链路</div>
        ${record.rawEvents.length ? renderEventTimeline(record) : emptyHtml("没有可展示的事件链路")}
      </section>
      <details class="raw-json" open>
        <summary>原始 JSON</summary>
        <pre>${escapeHtml(JSON.stringify(redactLogValue(item), null, 2))}</pre>
      </details>
    `
  );
}

function renderRunDrawerSummary(record) {
  const item = record.raw;
  return `
    <section class="drawer-section">
      <div class="drawer-section-title">运行摘要</div>
      <div class="summary-strip">
        <div><span>工单</span><strong>${escapeHtml(item.ticket_id || item.ticket || "-")}</strong></div>
        <div><span>状态</span><strong>${renderStatusBadge(record.status)}</strong></div>
        <div><span>轮次</span><strong>${escapeHtml(record.roundsCount)}</strong></div>
        <div><span>Token</span><strong>${escapeHtml(formatTokenPair(record))}</strong></div>
        <div><span>耗时</span><strong>${escapeHtml(record.durationMs ? formatDuration(record.durationMs) : "-")}</strong></div>
      </div>
      <div class="kv-grid detail-kv">
        <strong>Session</strong><span>${escapeHtml(item.session_id || "-")}</span>
        <strong>模块</strong><span>${escapeHtml(item.module || "-")}</span>
        <strong>触发方式</strong><span>${escapeHtml(formatTrigger(record.trigger))}</span>
        <strong>来源文件</strong><span>${escapeHtml(item._source_file || "-")}</span>
        <strong>明细状态</strong><span>${record.hasDetailedRounds ? "逐轮明细可用" : "仅聚合轮次"}</span>
      </div>
    </section>
  `;
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
  const drawer = document.getElementById("detail-drawer");
  const wasHidden = drawer.classList.contains("hidden");
  if (wasHidden) drawerReturnFocus = document.activeElement;
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("drawer-subtitle").textContent = subtitle || "";
  document.getElementById("drawer-body").innerHTML = body;
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  syncOverlayAccessibility();
  if (wasHidden) drawer.querySelector('[aria-label="关闭详情"]')?.focus();
}

function closeDrawer() {
  const drawer = document.getElementById("detail-drawer");
  if (drawer.classList.contains("hidden")) return;
  drawer.classList.add("hidden");
  drawer.setAttribute("aria-hidden", "true");
  syncOverlayAccessibility();
  if (document.getElementById("modal-backdrop").classList.contains("hidden")) {
    drawerReturnFocus?.focus?.();
  }
  drawerReturnFocus = null;
}

function showModal(title, body, actions) {
  const modal = document.getElementById("modal-backdrop");
  const wasHidden = modal.classList.contains("hidden");
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
  if (wasHidden) modalReturnFocus = document.activeElement;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  syncOverlayAccessibility();
  if (wasHidden) modal.querySelector('[aria-label="关闭"]')?.focus();
}

function closeModal() {
  const modal = document.getElementById("modal-backdrop");
  if (modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  syncOverlayAccessibility();
  modalReturnFocus?.focus?.();
  modalReturnFocus = null;
}

function syncOverlayAccessibility() {
  const drawerOpen = !document.getElementById("detail-drawer").classList.contains("hidden");
  const modalOpen = !document.getElementById("modal-backdrop").classList.contains("hidden");
  document.getElementById("app").inert = drawerOpen || modalOpen;
}

function handleOverlayKeydown(event) {
  const modelPicker = document.getElementById("model-picker");
  if (event.key === "Escape" && modelPicker && !modelPicker.hidden) {
    event.preventDefault();
    toggleModelPicker();
    document.getElementById("model-picker-trigger")?.focus();
    return;
  }
  const modal = document.getElementById("modal-backdrop");
  const drawer = document.getElementById("detail-drawer");
  const overlay = !modal.classList.contains("hidden")
    ? modal
    : !drawer.classList.contains("hidden")
      ? drawer
      : null;
  if (!overlay) return;
  if (event.key === "Escape") {
    event.preventDefault();
    if (overlay === modal) closeModal();
    else closeDrawer();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = Array.from(overlay.querySelectorAll(SIDEBAR_FOCUSABLE)).filter(element => !element.inert && element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function showToast(message, tone = "info") {
  const existing = document.querySelector(".toast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.className = `toast ${tone}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function metricCard(label, value, description = "", className = "", id = "") {
  return `
    <div ${id ? `id="${escapeHtml(id)}"` : ""} class="metric-card">
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

function inlineLoadingHtml(text) {
  return `
    <div class="inline-loading" role="status" aria-live="polite">
      <span></span>
      <strong>${escapeHtml(text)}</strong>
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
  if (text.includes("deferred")) return "deferred";
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
    running: "运行中",
    deferred: "已延期"
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

function eventRoundLabel(event) {
  const round = numberFromKeys(event, ["round", "round_number"]);
  return round ? `R${round}` : "-";
}

function logSummary(item) {
  const direct = item.summary || item.message || item.hint;
  if (direct) return String(direct);
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
  handleSidebarKeydown(event);
  handleOverlayKeydown(event);
});

window.addEventListener("resize", () => {
  syncSidebarAccessibility();
  positionModelPicker();
});
document.addEventListener("scroll", positionModelPicker, true);

init();
