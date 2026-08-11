"""配置加载模块：YAML 配置 + 环境变量覆盖"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.runtime.openai_reasoning import validate_reasoning_effort


class AgentConfig(BaseModel):
    max_concurrent_sessions: int = 5


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o"
    # 空字符串 = 由模型使用默认档位。后台会按具体模型限制可选值。
    reasoning_effort: str = ""
    enable_vision_input: bool = True
    temperature: float = 0.2
    base_url: str | None = None  # 自定义 OpenAI API 地址（代理 / Azure）
    timeout: int = 120  # API 请求超时（秒）
    max_tokens: int = 8192  # Anthropic 必传，OpenAI 可选（0 表示不设）

    @field_validator("model", mode="before")
    @classmethod
    def _trim_model_id(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("reasoning_effort")
    @classmethod
    def _validate_main_reasoning_effort(cls, value: str, info) -> str:
        return validate_reasoning_effort(str(info.data.get("model") or ""), value)


class RetryPolicyOverrideConfig(BaseModel):
    """模块级重试覆盖配置；为空时继承全局默认值"""

    max_attempts: int | None = None
    base_delay_seconds: float | None = None
    max_delay_seconds: float | None = None


class RetryConfig(BaseModel):
    """全局重试配置 + 模块级覆盖"""

    max_attempts: int = 3
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 20.0
    jitter: bool = True
    git: RetryPolicyOverrideConfig = Field(default_factory=RetryPolicyOverrideConfig)


class ZendeskConfig(BaseModel):
    subdomain: str = ""
    email: str = ""
    api_token: str = ""
    poll_interval_seconds: int = 60
    poll_query: str = "status:open status:new"
    search_query: str = "status:new tags:saas"
    mention_trigger: str = "@saas-bot"
    mention_aliases: list[str] = Field(default_factory=list)
    mention_poll_interval_seconds: int = 5
    # 我（agent）在 Zendesk 上的 user_id；用于区分评论作者是否为自动回复作者。
    agent_user_id: int = 0
    # Zendesk Messaging / 队列自动化身份，不算人工受托人。
    non_human_assignee_ids: list[int] = Field(default_factory=list)
    # 新分支上线时的灰度边界：trigger_consumer 顶部 skip 一切 ticket_id < min_ticket_id
    # 的工单。0 = 不过滤（处理全部 SaaS 工单）。设 2624 = 只处理 ID ≥ 2624 的新工单，
    # 历史工单不受新逻辑影响（避免上线初期把已有 in-flight 工单搞乱）。
    min_ticket_id: int = 0


class WebhookConfig(BaseModel):
    signing_secret: str | None = (
        None  # 经典 Tickets webhook（conditional_ticket_events）
    )
    # 新增 messaging webhook（messaging_ticket.message_added）独立 secret。
    # 两个 webhook 走两个 endpoint、两套 signing secret，避免一个泄露牵连另一个。
    messaging_signing_secret: str | None = None
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    zendesk_path: str = "/webhook/zendesk"
    zendesk_messaging_path: str = "/webhook/zendesk-messaging"


class RepoEntry(BaseModel):
    name: str
    url: str


class ReposConfig(BaseModel):
    base_dir: str = "./repos"
    auto_fetch: bool = True
    fetch_cron: str = "0 3 * * *"
    repositories: list[RepoEntry] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("repositories", mode="before")
    @classmethod
    def _coerce_str_to_repo_entry(cls, v):
        if isinstance(v, list):
            return [
                {"name": item, "url": ""} if isinstance(item, str) else item
                for item in v
            ]
        return v


class SandboxConfig(BaseModel):
    """Read-only location for inspecting or cleaning pre-v2 forensic files."""

    base_dir: str = "/tmp/zd-agent"


class KnowledgeConfig(BaseModel):
    docs_dir: str = "./ai-docs"
    archive_dir: str = "./ai-docs/tickets"


class ResourcesConfig(BaseModel):
    base_dir: str = "/opt/zd-agent/resources"
    saas_codebase_dir: str = "saas源代码"
    knowledge_base_dir: str = "saas版知识库"
    ticket_records_dir: str = "zendesk-cli/工单记录"
    attachments_dir: str = "工单附件"
    state_dir: str = ".state"


class CustomMcpServiceConfig(BaseModel):
    name: str = ""
    label: str = ""
    description: str = ""
    mcp_url: str = ""
    mcp_api_key: str = ""
    request_timeout_seconds: int = 35
    enabled: bool = True


class McpServicesConfig(BaseModel):
    custom: list[CustomMcpServiceConfig] = Field(default_factory=list)


class IntakeConfig(BaseModel):
    """V2 intake debounce and post-run cooldown settings.

    The old trigger classifier no longer exists.  These two timing controls
    remain because they protect the durable queue from bursty customer events.
    """

    silence_minutes_default: int = 0
    post_agent_cooldown_seconds: int = 180


class SilenceWakerConfig(BaseModel):
    scan_interval_seconds: int = 1


class WaitingPollerConfig(BaseModel):
    """Recover missed messaging events and bound customer-owned waits.

    2026-05 后客户新消息检测**主要走 messaging webhook**，poller 退化为低频兜底：
    - scan_interval_seconds 默认从 60s 拉长到 300s
    - poll_new_messages flag 控制是否做"消息 diff 兜底"——webhook 丢消息时才有用
    """

    # 60s → 300s：messaging webhook 接管后，poller 不再是消息检测主力
    scan_interval_seconds: int = 300
    # The old YAML key remains an input alias for rolling upgrades. V2 stores
    # ``out_of_scope`` with a typed timeout reason and creates no handoff.
    customer_wait_timeout_hours: int = Field(
        default=24,
        validation_alias=AliasChoices(
            "customer_wait_timeout_hours",
            "abandoned_hours",
        ),
    )
    # webhook 兜底开关：True=poller 也 diff 客户消息（防 webhook 漏发）
    # False=纯靠 webhook（更省 API 配额，但 webhook 故障时会漏消息）
    poll_new_messages: bool = True


class DbGatewayConfig(BaseModel):
    """外部 DB Gateway 客户端配置。"""

    url: str = ""
    api_key: str = ""
    request_timeout_seconds: int = 35


class SignozConfig(BaseModel):
    """Remote SigNoz MCP client config.

    The agent must not hold the raw SigNoz API key. That key belongs to the
    sibling signoz-mcp service, which exposes a narrow remote MCP tool.
    """

    mcp_url: str = ""
    mcp_api_key: str = ""
    request_timeout_seconds: int = 35


class SourceMcpConfig(BaseModel):
    """Remote source-code MCP client config.

    The source MCP resolves ``ref=runtime`` to the code currently running
    online, then reads/searches cloned SaaS repositories from that exact ref.
    """

    mcp_url: str = ""
    mcp_api_key: str = ""
    request_timeout_seconds: int = 45


class GithubMcpConfig(BaseModel):
    """Remote GitHub MCP client config.

    The GitHub PAT and rate-limit handling belong to github-mcp. The agent
    keeps this URL/API-key pair only for the narrow tool-service boundary.
    """

    mcp_url: str = ""
    mcp_api_key: str = ""
    request_timeout_seconds: int = 35


class DocsToolServiceConfig(BaseModel):
    """Remote official-docs MCP client config."""

    mcp_url: str = ""
    mcp_api_key: str = ""
    request_timeout_seconds: int = 35


class ZendeskMcpConfig(BaseModel):
    """Remote Zendesk MCP client config.

    Public customer replies intentionally stay on the browser relay. This MCP
    handles ticket reads, internal notes, metadata writes, and guarded support
    operations only.
    """

    mcp_url: str = ""
    mcp_api_key: str = ""
    request_timeout_seconds: int = 35


class NotificationServiceConfig(BaseModel):
    """Remote notification-service MCP client config.

    Feishu webhook credentials and retry policy live only in the sibling
    notification-service; the Agent keeps only this authenticated MCP client.
    """

    mcp_url: str = ""
    mcp_api_key: str = ""
    request_timeout_seconds: int = 35


class InspectorConfig(BaseModel):
    """Inspector CLI（langgenius/infra-toolkit）配置。

    Inspector 是私有 CLI，把 SaaS 主库 + billing 库的高频查询打包成命令，
    支持跨库聚合（如 quota / workflow_run / account drill-down），手写 SQL
    很容易漏字段（例如 `is_universal=false` / TiDB 知识库存储统计）。
    """

    binary: str = "/usr/local/bin/inspector"
    config_file: str = "/opt/zd-agent/inspector-config/prod.yaml"
    # 单次最多 45s，失败后只重试一次。两次尝试总预算 90s，给 agent_loop 的
    # 180s 工具硬超时留足取消、日志和 sql_query 降级时间，避免 Inspector 自己的
    # 90s × 2 刚好撞上外层 deadline，最终只留下笼统的 tool timeout。
    timeout_seconds: int = 45


class PublicReplyConfig(BaseModel):
    """Crash-safe v2 public-delivery transport and intake windows."""

    # Customer-write kill switch.  False keeps the v2 analysis/draft path but
    # prevents Browser Relay delivery; it does not invoke a legacy fallback.
    enabled: bool = False

    # SaaS 工单创建后立即读取完整表单/会话；缺少问题描述时立刻询问。
    silent_initial_wait_seconds: int = 0

    # 客户补充消息立即进入新一轮分析。发送边界仍会重新拉取完整工单，
    # 若客户在分析期间继续补充，则废弃旧草稿并基于最新上下文重跑。
    silence_window_seconds: int = 0

    # SaaS native messaging 工单的客户可见回复不再走 Conversations API。
    # browser relay 使用已登录的 Zendesk Agent 前台会话提交消息，确保客户 widget 可见。
    browser_relay_url: str = ""
    browser_relay_api_key: str = ""
    browser_relay_timeout_seconds: int = 90
    # Public HTTPS endpoint the remote browser relay calls immediately before
    # clicking Send. Required when browser_relay_url is not loopback.
    freshness_callback_url: str = ""


class LoggingConfig(BaseModel):
    """日志配置。stdout 始终走带色 ConsoleRenderer（不可配）；
    jsonl_enabled 控制是否额外写 JSON 行到 log_dir/events-<date>.jsonl 供排查。
    """

    jsonl_enabled: bool = True
    log_dir: str = "/opt/zd-agent/logs"


def _reject_removed_casework_mode(value: object) -> object:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"legacy", "shadow"}:
            raise ValueError(
                f"casework mode '{normalized}' is no longer supported; "
                "use 'canary' or 'casework'",
            )
    return value


class CaseworkConfig(BaseModel):
    """v2 decision-core rollout and bounded-investigation controls."""

    model_config = ConfigDict(validate_assignment=True)

    mode: ExecutionMode = ExecutionMode.CASEWORK
    canary_ticket_ids: tuple[int, ...] = ()
    max_investigation_rounds: int = Field(default=10, ge=1, le=20)
    max_investigation_calls: int = Field(default=16, ge=1, le=40)
    max_investigation_tokens: int = Field(
        default=1_500_000,
        ge=1_000,
        le=10_000_000,
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _reject_removed_mode(cls, value: object) -> object:
        return _reject_removed_casework_mode(value)


class AppConfig(BaseModel):
    """应用完整配置"""

    agent: AgentConfig = Field(default_factory=AgentConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    zendesk: ZendeskConfig = Field(default_factory=ZendeskConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    repos: ReposConfig = Field(default_factory=ReposConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    intake: IntakeConfig = Field(
        default_factory=IntakeConfig,
        validation_alias=AliasChoices("intake", "trigger_decider"),
    )
    silence_waker: SilenceWakerConfig = Field(default_factory=SilenceWakerConfig)
    waiting_poller: WaitingPollerConfig = Field(default_factory=WaitingPollerConfig)
    db_gateway: DbGatewayConfig = Field(default_factory=DbGatewayConfig)
    signoz: SignozConfig = Field(default_factory=SignozConfig)
    source_mcp: SourceMcpConfig = Field(default_factory=SourceMcpConfig)
    github_mcp: GithubMcpConfig = Field(default_factory=GithubMcpConfig)
    docs_tool_service: DocsToolServiceConfig = Field(
        default_factory=DocsToolServiceConfig
    )
    zendesk_mcp: ZendeskMcpConfig = Field(default_factory=ZendeskMcpConfig)
    notification_service: NotificationServiceConfig = Field(
        default_factory=NotificationServiceConfig,
    )
    mcp_services: McpServicesConfig = Field(default_factory=McpServicesConfig)
    inspector: InspectorConfig = Field(default_factory=InspectorConfig)
    public_reply: PublicReplyConfig = Field(default_factory=PublicReplyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    casework: CaseworkConfig = Field(default_factory=CaseworkConfig)


class EnvSettings(BaseSettings):
    """环境变量，优先级高于 YAML 配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 允许 .env 里有 EnvSettings 没定义的变量（如 INTERNAL_SHARED_SECRET 由 os.environ 消费）
    )

    openai_api_key: str = ""
    openai_base_url: str | None = None
    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_api_token: str = ""
    zendesk_admin_email: str = ""
    zendesk_admin_api_token: str = ""
    zd_max_concurrent: int | None = None
    zd_model: str | None = None
    zd_llm_provider: str | None = None  # 覆盖 llm.provider（openai / anthropic）
    zd_webhook_signing_secret: str | None = None
    zendesk_browser_relay_url: str = ""
    zendesk_browser_relay_api_key: str = ""
    zendesk_browser_relay_timeout_seconds: int | None = None
    zendesk_freshness_callback_url: str = ""
    github_token: str | None = None  # GitHub PAT，设置后仓库自动用 HTTPS+token 认证
    anthropic_api_key: str = ""
    zd_log_level: str = "INFO"  # DEBUG / INFO / WARNING / ERROR
    zd_casework_mode: ExecutionMode | None = None
    zd_casework_canary_ticket_ids: str = ""
    zd_admin_initial_password: str = ""
    hc_client_url: str = ""  # zendesk-hc-client API 地址（如 http://localhost:8000）
    hc_client_api_key: str = ""  # 对应 hc-client 的 AGENT_INNER_API_KEY
    # SaaS-specific env vars
    db_gateway_url: str = ""
    db_gateway_api_key: str = ""
    signoz_mcp_url: str = ""
    signoz_mcp_api_key: str = ""
    signoz_mcp_timeout_seconds: int | None = None
    source_mcp_url: str = ""
    source_mcp_api_key: str = ""
    source_mcp_timeout_seconds: int | None = None
    github_mcp_url: str = ""
    github_mcp_api_key: str = ""
    github_mcp_timeout_seconds: int | None = None
    docs_tool_service_url: str = ""
    docs_tool_service_api_key: str = ""
    docs_tool_service_timeout_seconds: int | None = None
    zendesk_mcp_url: str = ""
    zendesk_mcp_api_key: str = ""
    zendesk_mcp_timeout_seconds: int | None = None
    notification_service_url: str = ""
    notification_service_api_key: str = ""
    notification_service_timeout_seconds: int | None = None
    zendesk_webhook_secret: str = ""
    zendesk_messaging_webhook_secret: str = ""

    @field_validator("zd_casework_mode", mode="before")
    @classmethod
    def _reject_removed_mode(cls, value: object) -> object:
        return _reject_removed_casework_mode(value)


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """加载配置：先读 YAML，再用环境变量覆盖

    配置中的相对路径统一解析为相对于 config.yaml 所在目录。
    """
    path = Path(config_path).resolve()
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        config_dir = path.parent
    else:
        raw = {}
        config_dir = Path.cwd()

    cfg = AppConfig(**raw)

    # 将相对路径解析为基于 config.yaml 所在目录的绝对路径
    def _resolve(p: str) -> str:
        pp = Path(p)
        if not pp.is_absolute():
            pp = (config_dir / pp).resolve()
        return str(pp)

    # Resolve resources.base_dir and derive repos.base_dir from resources
    cfg.resources.base_dir = _resolve(cfg.resources.base_dir)
    # Only derive from resources when not explicitly set
    if not cfg.repos.base_dir:
        cfg.repos.base_dir = str(
            Path(cfg.resources.base_dir) / cfg.resources.saas_codebase_dir
        )
    else:
        cfg.repos.base_dir = _resolve(cfg.repos.base_dir)
    cfg.knowledge.docs_dir = _resolve(cfg.knowledge.docs_dir)
    cfg.knowledge.archive_dir = _resolve(cfg.knowledge.archive_dir)

    env = EnvSettings()

    # 环境变量覆盖
    if env.zendesk_subdomain:
        cfg.zendesk.subdomain = env.zendesk_subdomain
    if env.zendesk_email:
        cfg.zendesk.email = env.zendesk_email
    if env.zendesk_api_token:
        cfg.zendesk.api_token = env.zendesk_api_token
    if env.zd_max_concurrent is not None:
        cfg.agent.max_concurrent_sessions = env.zd_max_concurrent
    if env.zd_llm_provider is not None:
        cfg.llm.provider = env.zd_llm_provider
    if env.zd_casework_mode is not None:
        cfg.casework.mode = env.zd_casework_mode
    if env.zd_casework_canary_ticket_ids.strip():
        ticket_ids = tuple(
            dict.fromkeys(
                int(value.strip())
                for value in env.zd_casework_canary_ticket_ids.split(",")
                if value.strip()
            ),
        )
        if any(ticket_id <= 0 for ticket_id in ticket_ids):
            raise ValueError("casework canary ticket IDs must be positive")
        cfg.casework.canary_ticket_ids = ticket_ids
    if env.zd_model is not None:
        model_override = env.zd_model.strip()
        if model_override:
            validate_reasoning_effort(
                model_override,
                cfg.llm.reasoning_effort,
            )
            cfg.llm.model = model_override
    if env.openai_base_url is not None:
        cfg.llm.base_url = env.openai_base_url
    # DB Gateway
    if env.db_gateway_url:
        cfg.db_gateway.url = env.db_gateway_url.rstrip("/")
    if env.db_gateway_api_key:
        cfg.db_gateway.api_key = env.db_gateway_api_key
    # SigNoz remote MCP
    if env.signoz_mcp_url:
        cfg.signoz.mcp_url = env.signoz_mcp_url.rstrip("/")
    if env.signoz_mcp_api_key:
        cfg.signoz.mcp_api_key = env.signoz_mcp_api_key
    if env.signoz_mcp_timeout_seconds is not None:
        cfg.signoz.request_timeout_seconds = env.signoz_mcp_timeout_seconds
    # Source-code remote MCP
    if env.source_mcp_url:
        cfg.source_mcp.mcp_url = env.source_mcp_url.rstrip("/")
    if env.source_mcp_api_key:
        cfg.source_mcp.mcp_api_key = env.source_mcp_api_key
    if env.source_mcp_timeout_seconds is not None:
        cfg.source_mcp.request_timeout_seconds = env.source_mcp_timeout_seconds
    # GitHub remote MCP
    if env.github_mcp_url:
        cfg.github_mcp.mcp_url = env.github_mcp_url.rstrip("/")
    if env.github_mcp_api_key:
        cfg.github_mcp.mcp_api_key = env.github_mcp_api_key
    if env.github_mcp_timeout_seconds is not None:
        cfg.github_mcp.request_timeout_seconds = env.github_mcp_timeout_seconds
    # Official docs tool service
    if env.docs_tool_service_url:
        cfg.docs_tool_service.mcp_url = env.docs_tool_service_url.rstrip("/")
    if env.docs_tool_service_api_key:
        cfg.docs_tool_service.mcp_api_key = env.docs_tool_service_api_key
    if env.docs_tool_service_timeout_seconds is not None:
        cfg.docs_tool_service.request_timeout_seconds = (
            env.docs_tool_service_timeout_seconds
        )
    # Zendesk remote MCP
    if env.zendesk_mcp_url:
        cfg.zendesk_mcp.mcp_url = env.zendesk_mcp_url.rstrip("/")
    if env.zendesk_mcp_api_key:
        cfg.zendesk_mcp.mcp_api_key = env.zendesk_mcp_api_key
    if env.zendesk_mcp_timeout_seconds is not None:
        cfg.zendesk_mcp.request_timeout_seconds = env.zendesk_mcp_timeout_seconds
    # Notification service
    if env.notification_service_url:
        cfg.notification_service.mcp_url = env.notification_service_url.rstrip("/")
    if env.notification_service_api_key:
        cfg.notification_service.mcp_api_key = env.notification_service_api_key
    if env.notification_service_timeout_seconds is not None:
        cfg.notification_service.request_timeout_seconds = (
            env.notification_service_timeout_seconds
        )
    # Zendesk webhook secret (ZENDESK_WEBHOOK_SECRET is primary; ZD_WEBHOOK_SIGNING_SECRET
    # kept as legacy fallback for pre-SaaS deployments)
    if env.zendesk_webhook_secret:
        cfg.webhook.signing_secret = env.zendesk_webhook_secret
    elif env.zd_webhook_signing_secret is not None:
        cfg.webhook.signing_secret = env.zd_webhook_signing_secret
    # Messaging webhook secret (独立于 Tickets webhook secret，两套 signing 用不同 key)
    if env.zendesk_messaging_webhook_secret:
        cfg.webhook.messaging_signing_secret = env.zendesk_messaging_webhook_secret
    # Zendesk browser relay (SaaS native messaging public reply path)
    if env.zendesk_browser_relay_url:
        cfg.public_reply.browser_relay_url = env.zendesk_browser_relay_url.rstrip("/")
    if env.zendesk_browser_relay_api_key:
        cfg.public_reply.browser_relay_api_key = env.zendesk_browser_relay_api_key
    if env.zendesk_browser_relay_timeout_seconds is not None:
        cfg.public_reply.browser_relay_timeout_seconds = (
            env.zendesk_browser_relay_timeout_seconds
        )
    if env.zendesk_freshness_callback_url:
        cfg.public_reply.freshness_callback_url = (
            env.zendesk_freshness_callback_url.rstrip("/")
        )
    return cfg


_config: AppConfig | None = None


def get_config() -> AppConfig:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = load_config()
    return _config
