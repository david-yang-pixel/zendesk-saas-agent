const MENU_GROUPS = [
  { label: "工作台", items: ["总览", "运行记录"] },
  { label: "Agent 配置", items: ["触发配置", "回复策略", "模型与运行", "工具与集成", "存储配置"] },
  { label: "系统", items: ["部署状态", "审计日志"] }
];

const MENU_ICONS = {
  "总览": '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
  "运行记录": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 19h16M5 15l4-4 3 3 6-7"/><path d="M18 7h-4M18 7v4"/></svg>',
  "触发配置": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 4v6h6M20 20v-6h-6"/><path d="M5.1 15a8 8 0 0 0 13.4 2M18.9 9A8 8 0 0 0 5.5 7"/></svg>',
  "回复策略": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="M8 9h8M8 13h5"/></svg>',
  "模型与运行": '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/></svg>',
  "工具与集成": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 2v6M12 16v6M4.9 4.9l4.2 4.2M14.9 14.9l4.2 4.2M2 12h6M16 12h6M4.9 19.1l4.2-4.2M14.9 9.1l4.2-4.2"/><circle cx="12" cy="12" r="4"/></svg>',
  "存储配置": '<svg aria-hidden="true" viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></svg>',
  "部署状态": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3 4 7v10l8 4 8-4V7Z"/><path d="m4 7 8 4 8-4M12 11v10"/></svg>',
  "审计日志": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h12v18H6zM9 7h6M9 11h6M9 15h4"/></svg>'
};

const PASSWORD_ICONS = {
  visible: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M2.1 12s3.6-7 9.9-7 9.9 7 9.9 7-3.6 7-9.9 7-9.9-7-9.9-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
  hidden: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 4.2A10 10 0 0 1 12 4c6.3 0 9.9 8 9.9 8a16 16 0 0 1-2.1 3.1M6.6 6.6C3.8 8.5 2.1 12 2.1 12S5.7 20 12 20a10 10 0 0 0 4.1-.9"/></svg>'
};

const SIDEBAR_FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
let sidebarReturnFocus = null;
let drawerReturnFocus = null;
let modalReturnFocus = null;

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

const GROUP_DESCRIPTIONS = {
  "触发配置": [
    "控制 agent 什么时候开始处理、什么时候等待，以及什么时候主动追问。",
    "判定模型放在「模型与运行」里；这里聚焦入口、窗口和扫描策略。"
  ],
  "回复策略": [
    "控制 agent 什么时候发送 public reply、什么时候 ask、什么时候交给人工。",
    "Browser Relay 的 URL/API Key 已移到「工具与集成」，这里只保留回复行为策略。"
  ],
  "模型与运行": [
    "配置主回复模型、Subagent 模型、触发判定模型、API Key、并发、轮次、超时和系统提示词。",
    "模型名是可编辑输入框，不限制为固定下拉选项。"
  ],
  "工具与集成": [
    "配置 agent 使用的 Skills、远程 MCP/Tool Service 和 Browser Relay。",
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
      description: "Tickets、Messaging 和飞书 webhook 的监听地址、路径与签名密钥。",
      paths: [
        "webhook.signing_secret",
        "webhook.messaging_signing_secret",
        "webhook.listen_host",
        "webhook.listen_port",
        "webhook.zendesk_path",
        "webhook.zendesk_messaging_path",
        "webhook.feishu_path"
      ]
    },
    {
      title: "触发判定与等待策略",
      description: "客户消息合并、信息不足等待、仅问候告警和主动公开询问开关。",
      paths: [
        "trigger_decider.silence_minutes_default",
        "trigger_decider.silence_extend_to_minutes",
        "trigger_decider.rapid_burst_window_seconds",
        "trigger_decider.rapid_burst_threshold",
        "trigger_decider.insufficient_timeout_minutes",
        "trigger_decider.abandoned_hours",
        "trigger_decider.greeting_only_alert_minutes",
        "trigger_decider.enable_public_nudge",
        "trigger_decider.post_agent_cooldown_seconds"
      ]
    },
    {
      title: "后台扫描器",
      description: "静默唤醒和等待工单兜底扫描，偏运行时调度。",
      paths: [
        "silence_waker.scan_interval_seconds",
        "waiting_poller.scan_interval_seconds",
        "waiting_poller.abandoned_hours",
        "waiting_poller.greeting_alert_minutes",
        "waiting_poller.poll_new_messages"
      ]
    }
  ],
  "回复策略": [
    {
      title: "Public reply 策略",
      description: "是否允许自动公开回复、ask 次数上限和 Browser Relay 调用超时。",
      paths: [
        "public_reply.enabled",
        "public_reply.max_ask_count",
        "public_reply.browser_relay_timeout_seconds"
      ]
    },
    {
      title: "等待与追问窗口",
      description: "客户消息到达后的等待、合并、ask 告警和放弃等待时间。",
      paths: [
        "public_reply.silent_initial_wait_seconds",
        "public_reply.silence_window_seconds",
        "public_reply.ask_followup_alert_minutes",
        "public_reply.abandoned_hours"
      ]
    },
    {
      title: "Ask 提示词",
      description: "信息不足时交给大模型生成 ask 的补充规则。",
      paths: ["prompts.ask_extra_instructions"]
    }
  ],
  "模型与运行": [
    {
      title: "主模型与判定模型",
      description: "主回复、Subagent 和触发判定各自使用的模型与接入地址。",
      paths: [
        "llm.provider",
        "llm.base_url",
        "llm.model",
        "llm.reasoning_effort",
        "llm.subagent_model",
        "llm.subagent_reasoning_effort",
        "llm.enable_vision_input",
        "trigger_decider.llm_model",
        "trigger_decider.reasoning_effort",
        "trigger_decider.enable_vision_input"
      ]
    },
    {
      title: "模型参数与密钥",
      description: "请求温度、超时、输出上限和供应商 API Key。",
      paths: [
        "llm.temperature",
        "llm.timeout",
        "llm.max_tokens",
        "openai.api_key",
        "anthropic.api_key"
      ]
    },
    {
      title: "运行护栏",
      description: "并发、最大轮次、会话超时和上下文压缩阈值。",
      paths: [
        "agent.max_concurrent_sessions",
        "agent.guardrails.max_rounds",
        "agent.guardrails.timeout_seconds",
        "agent.guardrails.max_tokens_per_session",
        "agent.guardrails.context_compaction_trigger_tokens",
        "agent.guardrails.context_compaction_target_tokens"
      ]
    },
    {
      title: "重试策略",
      description: "全局和下游服务的 retry 次数、退避与 jitter。",
      paths: [
        "retry.max_attempts",
        "retry.base_delay_seconds",
        "retry.max_delay_seconds",
        "retry.jitter",
        "retry.llm.max_attempts",
        "retry.zendesk.max_attempts",
        "retry.git.max_attempts",
        "retry.sandbox.max_attempts"
      ]
    },
    {
      title: "系统提示词",
      description: "追加到 system prompt，用于规范处理工单时的 Skill 调度、语气和排查约束。",
      paths: ["prompts.agent_extra_instructions"]
    }
  ],
  "工具与集成": [
    {
      title: "Skills",
      description: "配置 agent 可启用的处理流程能力，和 MCP 一起组成排查能力面。",
      paths: ["skills.enabled", "skills.custom"]
    },
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
        "sandbox_worker.mcp_url",
        "sandbox_worker.mcp_api_key",
        "sandbox_worker.request_timeout_seconds",
        "mcp_services.custom"
      ]
    },
    {
      title: "通知服务",
      description: "飞书通知服务、fallback webhook 和日报时间。",
      paths: [
        "notification_service.mcp_url",
        "notification_service.mcp_api_key",
        "notification_service.request_timeout_seconds",
        "feishu.webhook_url",
        "feishu.webhook_secret",
        "feishu.daily_summary_time",
        "feishu.daily_summary_timezone"
      ]
    },
    {
      title: "Legacy Inspector",
      description: "旧 inspector 兼容入口，后续可逐步迁移到远程服务。",
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
      description: "资源根目录、代码库、知识库和工单记录目录。",
      paths: [
        "resources.base_dir",
        "resources.saas_codebase_dir",
        "resources.knowledge_base_dir",
        "resources.ticket_records_dir",
        "resources.attachments_dir",
        "resources.state_dir"
      ]
    },
    {
      title: "知识库与日志",
      description: "知识库路径、归档路径和 JSONL 运行日志存储。",
      paths: [
        "knowledge.docs_dir",
        "knowledge.archive_dir",
        "logging.jsonl_enabled",
        "logging.log_dir"
      ]
    },
    {
      title: "Sandbox 存储",
      description: "隔离执行目录和成功/失败后的清理策略。",
      paths: [
        "sandbox.base_dir",
        "sandbox.cleanup_on_success",
        "sandbox.cleanup_on_failure"
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
  ["notification_service", "Notification Service", "飞书通知和重试"],
  ["sandbox_worker", "Sandbox Worker", "隔离文件读写、搜索和执行"]
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
  ],
  sandbox_worker: [
    "sandbox_worker.mcp_url",
    "sandbox_worker.mcp_api_key",
    "sandbox_worker.request_timeout_seconds"
  ]
};

const BUILT_IN_SKILLS = [
  {
    name: "dify_support_ticket",
    label: "Dify Support Ticket",
    description: "服务器 agent 默认处理 SaaS 工单的核心 Skill，始终启用。"
  }
];

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
  MENU_GROUPS.forEach(group => {
    const section = document.createElement("section");
    section.className = "nav-group";

    const label = document.createElement("div");
    label.className = "nav-group-label";
    label.textContent = group.label;
    section.appendChild(label);

    group.items.forEach(menu => {
      const btn = document.createElement("button");
      const isActive = menu === state.activeMenu;
      btn.type = "button";
      btn.className = isActive ? "active" : "";
      btn.setAttribute("aria-current", isActive ? "page" : "false");
      btn.innerHTML = `<span class="nav-icon">${MENU_ICONS[menu]}</span><span>${menu}</span>`;
      btn.onclick = () => {
        const wasMobileDrawerOpen = document.getElementById("sidebar").classList.contains("open");
        state.activeMenu = menu;
        closeSidebar();
        renderNav();
        void render().then(() => {
          if (wasMobileDrawerOpen) document.getElementById("page-title").focus();
        });
      };
      section.appendChild(btn);
    });
    nav.appendChild(section);
  });
}

async function render() {
  document.title = `${state.activeMenu} · zd-agent`;
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
  if (state.activeMenu === "工具与集成") {
    actions.innerHTML = `
      <button class="btn secondary" onclick="refreshActivePage()">刷新</button>
      <button class="btn secondary" onclick="openSkillManager()">管理 Skill</button>
      <button class="btn primary" onclick="openMcpManager()">新增 MCP</button>
    `;
    return;
  }
  const editable = !["总览", "运行记录", "审计日志"].includes(state.activeMenu);
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
  setContent(renderOverviewShell());
  void loadDeployStatus()
    .then(renderOverviewDeploy)
    .catch(error => renderOverviewDeployError(error));
  void loadRunRecords(5, {}, { quick: true, detail: false, updateState: false })
    .then(renderRecentRunsIntoOverview)
    .catch(error => renderRecentRunsError(error));
}

function renderOverviewShell() {
  return `
    <div class="metric-grid">
      ${metricCard("Agent 服务", "加载中", "读取部署状态", "", "overview-agent-card")}
      ${metricCard("今日处理", "--", "读取运行记录", "", "overview-today-card")}
      ${metricCard("成功率", "--", "读取错误事件", "", "overview-success-card")}
      ${metricCard("配置状态", "加载中", "读取配置状态", "", "overview-config-card")}
    </div>
    <div class="overview-grid">
      <section class="card">
        <div class="card-header">
          <span>最近运行</span>
          <button class="btn secondary" onclick="goMenu('运行记录')">查看全部</button>
        </div>
        <div id="overview-recent-list" class="card-body mini-list">
          ${inlineLoadingHtml("正在读取最近运行")}
        </div>
      </section>
      <section class="card">
        <div class="card-header">
          <span>关键配置</span>
          <span id="overview-config-chip" class="chip">加载中</span>
        </div>
        <div id="overview-config-grid" class="card-body kv-grid">
          <strong>镜像标签</strong><span>--</span>
          <strong>提交</strong><span>--</span>
          <strong>账号密码登录</strong><span>--</span>
          <strong>Google SSO</strong><span>--</span>
          <strong>飞书 SSO</strong><span>--</span>
        </div>
      </section>
    </div>
  `;
}

function renderOverviewDeploy(deploy) {
  replaceHtml(
    "overview-agent-card",
    metricCard(
      "Agent 服务",
      deploy.agent?.status === "running" ? "运行中" : "异常",
      "当前进程",
      deploy.agent?.status === "running" ? "success-text" : "warn-text",
      "overview-agent-card"
    )
  );
  replaceHtml(
    "overview-config-card",
    metricCard(
      "配置状态",
      deploy.pending_restart ? "待重启" : "已同步",
      deploy.pending_restart ? "存在需重启配置" : "当前配置已生效",
      deploy.pending_restart ? "warn-text" : "success-text",
      "overview-config-card"
    )
  );
  setInnerHtml("overview-config-chip", deploy.pending_restart ? "待重启" : "已同步");
  setInnerHtml("overview-config-grid", `
    <strong>镜像标签</strong><span>${escapeHtml(deploy.image?.tag || "latest")}</span>
    <strong>提交</strong><span>${escapeHtml(shortSha(deploy.image?.commit_sha) || "--")}</span>
    <strong>账号密码登录</strong><span>${deploy.admin_auth?.password_login?.enabled ? "开启" : "关闭"}</span>
    <strong>Google SSO</strong><span>${authStatus(deploy.admin_auth?.google_sso)}</span>
    <strong>飞书 SSO</strong><span>${authStatus(deploy.admin_auth?.feishu_sso)}</span>
  `);
}

function renderOverviewDeployError(error) {
  replaceHtml(
    "overview-agent-card",
    metricCard("Agent 服务", "读取失败", error.message || "部署状态不可用", "warn-text", "overview-agent-card")
  );
  replaceHtml(
    "overview-config-card",
    metricCard("配置状态", "未知", "部署状态读取失败", "warn-text", "overview-config-card")
  );
}

function renderRecentRunsIntoOverview(runs) {
  const summary = runs.summary || {};
  const latest = buildRunDisplayRecords(runs.items || []).slice(0, 5);
  const total = summary.today_tickets || latest.length || summary.filtered || 0;
  const errors = summary.error_events || 0;
  const successRate = total ? `${Math.max(0, Math.round((1 - errors / total) * 100))}%` : "--";
  replaceHtml(
    "overview-today-card",
    metricCard("今日处理", total || "--", `${summary.today_events || 0} 个事件`, "", "overview-today-card")
  );
  replaceHtml(
    "overview-success-card",
    metricCard("成功率", successRate, `${errors} 个错误事件`, errors ? "warn-text" : "success-text", "overview-success-card")
  );
  setInnerHtml("overview-recent-list", `
    ${latest.length ? latest.map((record, index) => `
      <button class="mini-row" onclick="goRunDetail(${index})">
        <span class="event-name">${escapeHtml(record.ticketLabel)}</span>
        <span>${renderStatusBadge(record.status)}</span>
        <span class="truncate">${escapeHtml(record.summary || "查看完整日志")}</span>
      </button>
    `).join("") : emptyHtml(runRecordsEmptyText(runs))}
  `);
  state.runDisplayRecords = latest;
}

function renderRecentRunsError(error) {
  setInnerHtml("overview-recent-list", errorHtml(error.message || "运行记录读取失败"));
  replaceHtml(
    "overview-today-card",
    metricCard("今日处理", "--", "运行记录读取失败", "warn-text", "overview-today-card")
  );
  replaceHtml(
    "overview-success-card",
    metricCard("成功率", "--", "运行记录读取失败", "warn-text", "overview-success-card")
  );
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
          <span class="chip">${customSkills().length + BUILT_IN_SKILLS.length} 个 Skill</span>
          <span class="chip">${INTEGRATIONS.length + customMcpServices().length} 个服务</span>
          <span class="chip">检测会写入审计日志</span>
        </div>
      </div>
      <div class="card-body integration-grid">
        ${renderSkillSummaryCard()}
        ${INTEGRATIONS.map(([name, label, description]) => renderIntegrationCard(name, label, description)).join("")}
        ${customMcpServices().map(service => renderCustomMcpCard(service)).join("")}
      </div>
    </section>
    <section class="card">
      <div class="card-header">
        <span>管理说明</span>
        <span class="chip">Skill 与 MCP 统一管理</span>
      </div>
      <div class="card-body integration-note-grid">
        <div>
          <strong>Skill</strong>
          <p>Skill 用来定义处理工单的 SOP 和工具使用约束。内置 Dify Support Ticket 始终启用，自定义 Skill 可以新增、禁用或删除。</p>
        </div>
        <div>
          <strong>MCP</strong>
          <p>内置 MCP 点击卡片编辑连接配置；自定义 MCP 保存后会通过 <code>custom_mcp_call</code> 按服务名和工具名调用。</p>
        </div>
      </div>
    </section>
  `);
}

function renderSkillSummaryCard() {
  const skills = customSkills();
  const enabledCustom = skills.filter(skill => skill.enabled !== false).length;
  return `
    <div class="integration-card action-card skill-card" role="button" tabindex="0"
      onclick="openSkillManager()"
      onkeydown="if(event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')){event.preventDefault(); openSkillManager();}">
      <div class="integration-card-head">
        <div>
          <strong>Skills</strong>
          <div class="muted">处理工单 SOP、系统提示词配套能力</div>
        </div>
        <span class="chip enabled">内置必开</span>
      </div>
      <div class="integration-status success-text">
        ${BUILT_IN_SKILLS.length} 个内置 · ${enabledCustom} 个自定义启用
      </div>
      <div class="integration-card-actions">
        <button class="btn secondary" onclick="event.stopPropagation(); openSkillManager()">管理 Skill</button>
      </div>
    </div>
  `;
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

function openSkillManager() {
  showModal("Skill 管理", `
    <div class="manager-intro">
      <strong>内置 Skill</strong>
      ${BUILT_IN_SKILLS.map(skill => `
        <div class="locked-skill-row">
          <div>
            <span>${escapeHtml(skill.label)}</span>
            <p>${escapeHtml(skill.description)}</p>
          </div>
          <span class="chip enabled">始终启用</span>
        </div>
      `).join("")}
    </div>
    <div class="manager-toolbar">
      <strong>自定义 Skill</strong>
      <button class="btn secondary compact" onclick="addSkillManagerItem()">添加 Skill</button>
    </div>
    <div id="skill-manager-list" class="manager-list">
      ${customSkills().map(renderSkillManagerItem).join("") || emptyHtml("还没有自定义 Skill")}
    </div>
    ${renderReasonBox("调整 Skill 配置")}
  `, [
    { label: "取消", className: "secondary", onClick: closeModal },
    { label: "保存", className: "primary", onClick: saveSkillManager }
  ]);
}

function renderSkillManagerItem(skill = {}) {
  return `
    <div class="manager-item" data-skill-item>
      <div class="manager-item-head">
        <strong>${escapeHtml(skill.name || "新 Skill")}</strong>
        <button class="btn secondary compact" onclick="removeManagerItem(this)">删除</button>
      </div>
      <div class="manager-field-grid">
        <label>名称</label>
        <input data-skill-field="name" value="${escapeHtml(skill.name || "")}" placeholder="例如 billing_refund_sop">
        <span class="muted">字母开头，可用数字、-、_</span>
        <label>说明</label>
        <input data-skill-field="description" value="${escapeHtml(skill.description || "")}" placeholder="这个 Skill 解决什么问题">
        <span class="muted">展示给管理员和 prompt</span>
        <label>启用</label>
        <select data-skill-field="enabled">
          <option value="true" ${skill.enabled !== false ? "selected" : ""}>开启</option>
          <option value="false" ${skill.enabled === false ? "selected" : ""}>关闭</option>
        </select>
        <span class="muted">关闭后不会注入上下文</span>
      </div>
      <label class="manager-wide-label">Skill 内容</label>
      <textarea data-skill-field="content" class="prompt-textarea" rows="8" placeholder="写入 SOP、证据要求和工具使用规则">${escapeHtml(skill.content || "")}</textarea>
    </div>
  `;
}

function addSkillManagerItem() {
  addManagerItem("skill-manager-list", renderSkillManagerItem({ enabled: true }));
}

async function saveSkillManager() {
  const error = document.getElementById("modal-save-error");
  const skills = Array.from(document.querySelectorAll("[data-skill-item]")).map(item => ({
    name: item.querySelector("[data-skill-field='name']").value.trim(),
    description: item.querySelector("[data-skill-field='description']").value.trim(),
    enabled: item.querySelector("[data-skill-field='enabled']").value === "true",
    content: item.querySelector("[data-skill-field='content']").value.trim()
  })).filter(skill => skill.name || skill.description || skill.content);
  const enabled = [
    "dify_support_ticket",
    ...skills.filter(skill => skill.enabled).map(skill => skill.name).filter(Boolean)
  ];
  await applyConfigPayload({
    "skills.custom": skills,
    "skills.enabled": enabled.join("\n")
  }, reasonFromModal("调整 Skill 配置"), error);
}

function openMcpManager() {
  showModal("自定义 MCP 管理", `
    <div class="manager-toolbar">
      <div>
        <strong>自定义 MCP 服务</strong>
        <p>用于补充内置 MCP 之外的远程能力。保存后 Skill 或系统提示词可通过 service 名称引用。</p>
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
  const item = button.closest("[data-skill-item], [data-mcp-item]");
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

function customSkills() {
  const value = state.values["skills.custom"];
  return Array.isArray(value) ? value : [];
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
  return `
    <label for="field-${field.path}" title="${escapeHtml(field.path)}">${escapeHtml(field.label)}</label>
    ${renderInput(field)}
    <span class="field-help ${field.restart_required ? "warn-text" : "muted"}">
      ${fieldHelpText(field)}
    </span>
  `;
}

function fieldHelpText(field) {
  if (field.secret) return "可替换密钥";
  if (field.input === "reasoning_effort") return "按所选模型限制档位；新会话立即生效";
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
  const reasoningRefresh = [
    "llm.model",
    "llm.subagent_model",
    "trigger_decider.llm_model"
  ].includes(field.path)
    ? ' oninput="refreshReasoningEffortOptions()"'
    : "";
  return `<input id="${id}" data-path="${field.path}" type="${inputType}" value="${escapeHtml(displayValue)}"${reasoningRefresh}>`;
}

function normalizedModelId(model) {
  return String(model || "").trim().toLowerCase().split("/").pop();
}

function reasoningEffortsForModel(model) {
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

function reasoningUnsupportedReason(modelPath, model) {
  const rule = reasoningCapabilityForModel(model);
  if (modelPath === "trigger_decider.llm_model" && rule?.responses_only) {
    return "触发判定暂不支持仅限 Responses API 的模型";
  }
  return "";
}

function reasoningModelValue(modelPath) {
  const input = document.getElementById(`field-${modelPath}`);
  let model = input ? input.value : state.values[modelPath];
  if (modelPath === "llm.subagent_model" && !String(model || "").trim()) {
    const mainInput = document.getElementById("field-llm.model");
    model = mainInput ? mainInput.value : state.values["llm.model"];
  }
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
      ${result.restart_required ? "<p class=\"warn-text\">本次变更包含需重启生效的配置。</p>" : ""}
    `, [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
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
      ${result.restart_required ? "<p class=\"warn-text\">本次变更包含需重启生效的配置。</p>" : ""}
    `, [
      { label: "关闭", className: "secondary", onClick: closeModal }
    ]);
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
  setContent(`
    ${renderModuleGuide("部署状态")}
    <div class="metric-grid">
      ${metricCard("Agent 服务", deploy.agent?.status || "unknown", "当前进程", deploy.agent?.status === "running" ? "success-text" : "warn-text")}
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

window.addEventListener("resize", syncSidebarAccessibility);

init();
