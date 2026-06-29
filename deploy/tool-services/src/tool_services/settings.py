from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    github_host: str = Field(default="127.0.0.1", validation_alias="GITHUB_MCP_HOST")
    github_port: int = Field(default=8111, validation_alias="GITHUB_MCP_PORT")
    docs_host: str = Field(default="127.0.0.1", validation_alias="DOCS_TOOL_SERVICE_HOST")
    docs_port: int = Field(default=8112, validation_alias="DOCS_TOOL_SERVICE_PORT")
    notification_host: str = Field(default="127.0.0.1", validation_alias="NOTIFICATION_SERVICE_HOST")
    notification_port: int = Field(default=8113, validation_alias="NOTIFICATION_SERVICE_PORT")
    sandbox_host: str = Field(default="127.0.0.1", validation_alias="SANDBOX_WORKER_HOST")
    sandbox_port: int = Field(default=8114, validation_alias="SANDBOX_WORKER_PORT")

    github_api_key: str = Field(default="", validation_alias="GITHUB_MCP_API_KEY")
    github_token: str = Field(default="", validation_alias="GITHUB_MCP_GITHUB_TOKEN")
    github_timeout_seconds: float = Field(default=30.0, validation_alias="GITHUB_MCP_TIMEOUT_SECONDS")

    docs_api_key: str = Field(default="", validation_alias="DOCS_TOOL_SERVICE_API_KEY")
    docs_timeout_seconds: float = Field(default=20.0, validation_alias="DOCS_TOOL_SERVICE_TIMEOUT_SECONDS")
    docs_cache_ttl_seconds: float = Field(default=900.0, validation_alias="DOCS_TOOL_SERVICE_CACHE_TTL_SECONDS")

    notification_api_key: str = Field(default="", validation_alias="NOTIFICATION_SERVICE_API_KEY")
    notification_timeout_seconds: float = Field(default=20.0, validation_alias="NOTIFICATION_SERVICE_TIMEOUT_SECONDS")
    feishu_webhook_url: str = Field(default="", validation_alias="FEISHU_WEBHOOK_URL")
    feishu_webhook_secret: str = Field(default="", validation_alias="FEISHU_WEBHOOK_SECRET")

    sandbox_api_key: str = Field(default="", validation_alias="SANDBOX_WORKER_API_KEY")
    sandbox_base_dir: str = Field(default="/opt/zd-agent/resources/sandbox", validation_alias="SANDBOX_WORKER_BASE_DIR")
    sandbox_scripts_dir: str = Field(default="/opt/zd-agent/tool-scripts", validation_alias="SANDBOX_WORKER_SCRIPTS_DIR")
    sandbox_timeout_seconds: float = Field(default=60.0, validation_alias="SANDBOX_WORKER_TIMEOUT_SECONDS")
    sandbox_max_write_bytes: int = Field(default=1_048_576, validation_alias="SANDBOX_WORKER_MAX_WRITE_BYTES")


@lru_cache
def get_settings() -> Settings:
    return Settings()
