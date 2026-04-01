"""配置加载模块：YAML 配置 + 环境变量覆盖"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
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
    mention_trigger: str = "@support-agent"
    mention_poll_interval_seconds: int = 60


class WebhookConfig(BaseModel):
    signing_secret: str | None = None


class RepoEntry(BaseModel):
    name: str
    url: str


class ReposConfig(BaseModel):
    base_dir: str = "./repos"
    auto_fetch: bool = True
    fetch_cron: str = "0 3 * * *"
    repositories: list[RepoEntry] = Field(default_factory=list)


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


class EnvSettings(BaseSettings):
    """环境变量，优先级高于 YAML 配置"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    openai_api_key: str = ""
    openai_base_url: str | None = None
    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_api_token: str = ""
    zd_max_concurrent: int | None = None
    zd_model: str | None = None
    zd_llm_provider: str | None = None  # 覆盖 llm.provider（openai / anthropic）
    zd_webhook_signing_secret: str | None = None
    github_token: str | None = None  # GitHub PAT，设置后仓库自动用 HTTPS+token 认证
    anthropic_api_key: str = ""
    zd_log_level: str = "INFO"  # DEBUG / INFO / WARNING / ERROR


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
    if env.zd_webhook_signing_secret is not None:
        cfg.webhook.signing_secret = env.zd_webhook_signing_secret

    return cfg


_config: AppConfig | None = None


def get_config() -> AppConfig:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = load_config()
    return _config
