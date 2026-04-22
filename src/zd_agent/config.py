"""配置加载模块：YAML 配置 + 环境变量覆盖"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GuardrailsConfig(BaseModel):
    max_rounds: int = 30
    timeout_seconds: int = 600
    max_tokens_per_session: int = 200_000
    compaction_threshold: float = 0.75


class AgentConfig(BaseModel):
    max_concurrent_sessions: int = 5
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o"
    temperature: float = 0.2
    base_url: str | None = None  # 自定义 OpenAI API 地址（代理 / Azure）
    timeout: int = 120  # API 请求超时（秒）
    max_tokens: int = 8192  # Anthropic 必传，OpenAI 可选（0 表示不设）


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
    llm: RetryPolicyOverrideConfig = Field(default_factory=RetryPolicyOverrideConfig)
    zendesk: RetryPolicyOverrideConfig = Field(default_factory=RetryPolicyOverrideConfig)
    git: RetryPolicyOverrideConfig = Field(default_factory=RetryPolicyOverrideConfig)
    sandbox: RetryPolicyOverrideConfig = Field(default_factory=RetryPolicyOverrideConfig)


class ZendeskConfig(BaseModel):
    subdomain: str = ""
    email: str = ""
    api_token: str = ""
    poll_interval_seconds: int = 60
    poll_query: str = "status:open status:new"
    search_query: str = "status:new tags:saas"
    mention_trigger: str = "@saas-bot"
    mention_poll_interval_seconds: int = 60


class WebhookConfig(BaseModel):
    signing_secret: str | None = None  # keep for backward compat
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    zendesk_path: str = "/webhook/zendesk"
    feishu_path: str = "/webhook/feishu"


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
    type: Literal["local", "docker"] = "local"
    base_dir: str = "/tmp/zd-agent"
    cleanup_on_success: bool = True
    cleanup_on_failure: bool = False


class HelmWatchdogConfig(BaseModel):
    url: str = "https://helm-watchdog.dify.ai"
    timeout: int = 30


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


class TriggerDeciderConfig(BaseModel):
    llm_model: str = "gpt-5.4-mini"
    silence_minutes_default: int = 3
    silence_extend_to_minutes: int = 5
    rapid_burst_window_seconds: int = 30
    rapid_burst_threshold: int = 2
    insufficient_timeout_minutes: int = 20
    abandoned_hours: int = 6
    greeting_only_alert_minutes: int = 30


class SilenceWakerConfig(BaseModel):
    scan_interval_seconds: int = 30


class PostgresConfig(BaseModel):
    host: str = "localhost"
    port: int = 56061
    user: str = "pro_ops_teleport_read"
    password: str = ""  # from env PG_PASSWORD
    db_prod: str = "langgenius"
    db_billing: str = "billing"
    db_plugin: str = "dify_plugin_daemon"
    query_timeout_seconds: int = 30
    max_rows: int = 1000


class GrafanaConfig(BaseModel):
    url: str = ""
    api_key: str = ""  # from env GRAFANA_API_KEY
    prom_datasource_id: int = 1


class SignozConfig(BaseModel):
    url: str = ""
    api_key: str = ""  # from env SIGNOZ_API_KEY


class FeishuConfig(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    verify_token: str = ""
    encrypt_key: str = ""
    default_chat_id: str = ""
    daily_summary_time: str = "09:00"
    daily_summary_timezone: str = "Asia/Shanghai"


class TeleportConfig(BaseModel):
    proxy: str = ""
    user: str = ""  # from env TELEPORT_USER
    auth_home: str = "/opt/zd-agent/.tsh"
    warn_threshold_minutes: int = 30
    check_interval_seconds: int = 300


class AppConfig(BaseModel):
    """应用完整配置"""

    agent: AgentConfig = Field(default_factory=AgentConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    zendesk: ZendeskConfig = Field(default_factory=ZendeskConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    repos: ReposConfig = Field(default_factory=ReposConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    helm_watchdog: HelmWatchdogConfig = Field(default_factory=HelmWatchdogConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    trigger_decider: TriggerDeciderConfig = Field(default_factory=TriggerDeciderConfig)
    silence_waker: SilenceWakerConfig = Field(default_factory=SilenceWakerConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    grafana: GrafanaConfig = Field(default_factory=GrafanaConfig)
    signoz: SignozConfig = Field(default_factory=SignozConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    teleport: TeleportConfig = Field(default_factory=TeleportConfig)


class EnvSettings(BaseSettings):
    """环境变量，优先级高于 YAML 配置"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

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
    github_token: str | None = None  # GitHub PAT，设置后仓库自动用 HTTPS+token 认证
    anthropic_api_key: str = ""
    zd_log_level: str = "INFO"  # DEBUG / INFO / WARNING / ERROR
    hc_client_url: str = ""  # zendesk-hc-client API 地址（如 http://localhost:8000）
    hc_client_api_key: str = ""  # 对应 hc-client 的 AGENT_INNER_API_KEY
    # SaaS-specific env vars
    pg_password: str = ""
    grafana_api_key: str = ""
    signoz_api_key: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verify_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_bot_chat_id: str = ""
    zendesk_webhook_secret: str = ""
    teleport_user: str = ""


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
    if env.zd_model is not None:
        cfg.llm.model = env.zd_model
    if env.openai_base_url is not None:
        cfg.llm.base_url = env.openai_base_url
    # Postgres password
    if env.pg_password:
        cfg.postgres.password = env.pg_password
    # Grafana
    if env.grafana_api_key:
        cfg.grafana.api_key = env.grafana_api_key
    # SigNoz
    if env.signoz_api_key:
        cfg.signoz.api_key = env.signoz_api_key
    # Feishu
    if env.feishu_app_id:
        cfg.feishu.app_id = env.feishu_app_id
    if env.feishu_app_secret:
        cfg.feishu.app_secret = env.feishu_app_secret
    if env.feishu_verify_token:
        cfg.feishu.verify_token = env.feishu_verify_token
    if env.feishu_encrypt_key:
        cfg.feishu.encrypt_key = env.feishu_encrypt_key
    if env.feishu_bot_chat_id:
        cfg.feishu.default_chat_id = env.feishu_bot_chat_id
    # Zendesk webhook secret (ZENDESK_WEBHOOK_SECRET is primary; ZD_WEBHOOK_SIGNING_SECRET
    # kept as legacy fallback for pre-SaaS deployments)
    if env.zendesk_webhook_secret:
        cfg.webhook.signing_secret = env.zendesk_webhook_secret
    elif env.zd_webhook_signing_secret is not None:
        cfg.webhook.signing_secret = env.zd_webhook_signing_secret
    # Teleport
    if env.teleport_user:
        cfg.teleport.user = env.teleport_user

    return cfg


_config: AppConfig | None = None


def get_config() -> AppConfig:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = load_config()
    return _config
