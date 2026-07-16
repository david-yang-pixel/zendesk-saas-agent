"""Test that config loads all new SaaS-related fields."""
import pytest
from pydantic import ValidationError

from zd_agent.config import LLMConfig, TriggerDeciderConfig, load_config


SAAS_CONFIG_YAML = """
agent:
  max_concurrent_sessions: 5
llm:
  provider: openai
  model: gpt-5.4
  reasoning_effort: high
  subagent_model: gpt-5.4-mini
  subagent_reasoning_effort: low
  enable_vision_input: true
resources:
  base_dir: /opt/zd-agent/resources
  saas_codebase_dir: saas源代码
  knowledge_base_dir: saas版知识库
  ticket_records_dir: zendesk-cli/工单记录
  state_dir: .state
zendesk:
  search_query: "status:new tags:saas"
  mention_trigger: "@saas-bot"
  mention_aliases:
    - "@yonggang"
trigger_decider:
  llm_model: gpt-5.4-mini
  reasoning_effort: medium
  enable_vision_input: true
  silence_minutes_default: 0
  silence_extend_to_minutes: 1
  rapid_burst_window_seconds: 30
db_gateway:
  request_timeout_seconds: 35
signoz:
  mcp_url: https://ops.example.com/signoz-mcp
  request_timeout_seconds: 35
source_mcp:
  mcp_url: https://ops.example.com/source-mcp
  request_timeout_seconds: 45
github_mcp:
  mcp_url: https://ops.example.com/github-mcp
  request_timeout_seconds: 35
docs_tool_service:
  mcp_url: https://ops.example.com/docs-tool-service
  request_timeout_seconds: 35
zendesk_mcp:
  mcp_url: https://ops.example.com/zendesk-mcp
  request_timeout_seconds: 35
notification_service:
  mcp_url: https://ops.example.com/notification-service
  request_timeout_seconds: 35
sandbox_worker:
  mcp_url: https://ops.example.com/sandbox-worker
  request_timeout_seconds: 65
feishu:
  daily_summary_time: "09:00"
repos:
  base_dir: ""
  repositories:
    - dify
    - dify-billing
    - dify-marketplace-backend
    - dify-marketplace-frontend
    - dify-plugin-daemon
    - saas-deploy
  aliases:
    d: dify
    main: dify
    bill: dify-billing
    billing: dify-billing
    mb: dify-marketplace-backend
    mf: dify-marketplace-frontend
    plugin: dify-plugin-daemon
    daemon: dify-plugin-daemon
    deploy: saas-deploy
"""


@pytest.fixture
def saas_env(monkeypatch):
    env_vars = {
        "ZENDESK_SUBDOMAIN": "dify",
        "ZENDESK_EMAIL": "saas-bot@dify.ai",
        "ZENDESK_API_TOKEN": "tok",
        "ZENDESK_WEBHOOK_SECRET": "wsec",
        "OPENAI_API_KEY": "sk-xxx",
        "SIGNOZ_MCP_URL": "https://ops.example.com/signoz-mcp/",
        "SIGNOZ_MCP_API_KEY": "signoz_mcp_xxx",
        "SIGNOZ_MCP_TIMEOUT_SECONDS": "45",
        "SOURCE_MCP_URL": "https://ops.example.com/source-mcp/",
        "SOURCE_MCP_API_KEY": "source_mcp_xxx",
        "SOURCE_MCP_TIMEOUT_SECONDS": "55",
        "GITHUB_MCP_URL": "https://ops.example.com/github-mcp/",
        "GITHUB_MCP_API_KEY": "github_mcp_xxx",
        "GITHUB_MCP_TIMEOUT_SECONDS": "45",
        "DOCS_TOOL_SERVICE_URL": "https://ops.example.com/docs-tool-service/",
        "DOCS_TOOL_SERVICE_API_KEY": "docs_mcp_xxx",
        "DOCS_TOOL_SERVICE_TIMEOUT_SECONDS": "45",
        "ZENDESK_MCP_URL": "https://ops.example.com/zendesk-mcp/",
        "ZENDESK_MCP_API_KEY": "zendesk_mcp_xxx",
        "ZENDESK_MCP_TIMEOUT_SECONDS": "45",
        "NOTIFICATION_SERVICE_URL": "https://ops.example.com/notification-service/",
        "NOTIFICATION_SERVICE_API_KEY": "notification_mcp_xxx",
        "NOTIFICATION_SERVICE_TIMEOUT_SECONDS": "45",
        "SANDBOX_WORKER_URL": "https://ops.example.com/sandbox-worker/",
        "SANDBOX_WORKER_API_KEY": "sandbox_worker_xxx",
        "SANDBOX_WORKER_TIMEOUT_SECONDS": "75",
        "GITHUB_TOKEN": "gh_xxx",
        "DB_GATEWAY_URL": "https://db-gateway.example.com/",
        "DB_GATEWAY_API_KEY": "dbgw_xxx",
        "ZENDESK_BROWSER_RELAY_URL": "https://zendesk.smlershou.top/zendesk-reply-mcp/",
        "ZENDESK_BROWSER_RELAY_API_KEY": "relay_xxx",
        "ZENDESK_BROWSER_RELAY_TIMEOUT_SECONDS": "55",
        "ZENDESK_FRESHNESS_CALLBACK_URL": (
            "https://agent.example.com/webhook/relay-pre-send-freshness/"
        ),
    }
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    yield env_vars


@pytest.fixture
def saas_config_file(tmp_path):
    """Write SAAS_CONFIG_YAML to a temp file; return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(SAAS_CONFIG_YAML)
    return str(p)


def test_config_loads_resources_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.resources.saas_codebase_dir == "saas源代码"
    assert cfg.resources.knowledge_base_dir == "saas版知识库"
    assert cfg.resources.ticket_records_dir == "zendesk-cli/工单记录"
    assert cfg.resources.state_dir == ".state"


def test_config_loads_trigger_decider_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.llm.enable_vision_input is True
    assert cfg.llm.reasoning_effort == "high"
    assert cfg.llm.subagent_model == "gpt-5.4-mini"
    assert cfg.llm.subagent_reasoning_effort == "low"
    assert cfg.trigger_decider.llm_model == "gpt-5.4-mini"
    assert cfg.trigger_decider.reasoning_effort == "medium"
    assert cfg.trigger_decider.enable_vision_input is True
    assert cfg.trigger_decider.silence_minutes_default == 0
    assert cfg.trigger_decider.silence_extend_to_minutes == 1
    assert cfg.trigger_decider.rapid_burst_window_seconds == 30


def test_llm_config_defaults_to_provider_managed_reasoning() -> None:
    cfg = LLMConfig()

    assert cfg.reasoning_effort == ""
    assert cfg.subagent_reasoning_effort == ""


def test_llm_config_rejects_reasoning_effort_unsupported_by_model() -> None:
    with pytest.raises(ValidationError, match="does not support reasoning effort 'max'"):
        LLMConfig(model="gpt-5.4-mini", reasoning_effort="max")


def test_llm_config_validates_subagent_effort_against_subagent_model() -> None:
    with pytest.raises(ValidationError, match="does not support reasoning effort 'max'"):
        LLMConfig(
            model="gpt-5.6-terra",
            reasoning_effort="max",
            subagent_model="gpt-5.4-mini",
            subagent_reasoning_effort="max",
        )


def test_trigger_decider_config_validates_effort_against_its_model() -> None:
    with pytest.raises(ValidationError, match="does not support reasoning effort 'max'"):
        TriggerDeciderConfig(llm_model="gpt-5.4-mini", reasoning_effort="max")


def test_model_ids_are_trimmed_before_reasoning_validation() -> None:
    llm = LLMConfig(
        model="  gpt-5.6-terra  ",
        reasoning_effort="max",
        subagent_model="  gpt-5.4-mini  ",
        subagent_reasoning_effort="low",
    )
    trigger = TriggerDeciderConfig(
        llm_model="  gpt-5.1  ",
        reasoning_effort="high",
    )

    assert llm.model == "gpt-5.6-terra"
    assert llm.subagent_model == "gpt-5.4-mini"
    assert trigger.llm_model == "gpt-5.1"


def test_zd_model_env_override_is_trimmed(
    saas_env,
    saas_config_file,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZD_MODEL", "  gpt-5.6-terra  ")

    cfg = load_config(saas_config_file)

    assert cfg.llm.model == "gpt-5.6-terra"


def test_zd_model_env_override_revalidates_reasoning_combination(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "llm:\n"
        "  model: gpt-5.6-terra\n"
        "  reasoning_effort: max\n"
        "  subagent_model: ''\n"
        "  subagent_reasoning_effort: max\n"
    )
    monkeypatch.setenv("ZD_MODEL", "gpt-5.4-mini")

    with pytest.raises(ValueError, match="does not support reasoning effort 'max'"):
        load_config(str(config_path))


def test_config_loads_signoz_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.signoz.mcp_url == "https://ops.example.com/signoz-mcp"
    assert cfg.signoz.mcp_api_key == "signoz_mcp_xxx"
    assert cfg.signoz.request_timeout_seconds == 45


def test_config_loads_source_mcp_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.source_mcp.mcp_url == "https://ops.example.com/source-mcp"
    assert cfg.source_mcp.mcp_api_key == "source_mcp_xxx"
    assert cfg.source_mcp.request_timeout_seconds == 55


def test_config_loads_next_batch_mcp_sections(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.github_mcp.mcp_url == "https://ops.example.com/github-mcp"
    assert cfg.github_mcp.mcp_api_key == "github_mcp_xxx"
    assert cfg.github_mcp.request_timeout_seconds == 45
    assert cfg.docs_tool_service.mcp_url == "https://ops.example.com/docs-tool-service"
    assert cfg.docs_tool_service.mcp_api_key == "docs_mcp_xxx"
    assert cfg.docs_tool_service.request_timeout_seconds == 45
    assert cfg.notification_service.mcp_url == "https://ops.example.com/notification-service"
    assert cfg.notification_service.mcp_api_key == "notification_mcp_xxx"
    assert cfg.notification_service.request_timeout_seconds == 45
    assert cfg.sandbox_worker.mcp_url == "https://ops.example.com/sandbox-worker"
    assert cfg.sandbox_worker.mcp_api_key == "sandbox_worker_xxx"
    assert cfg.sandbox_worker.request_timeout_seconds == 75


def test_config_loads_zendesk_mcp_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.zendesk_mcp.mcp_url == "https://ops.example.com/zendesk-mcp"
    assert cfg.zendesk_mcp.mcp_api_key == "zendesk_mcp_xxx"
    assert cfg.zendesk_mcp.request_timeout_seconds == 45


def test_config_loads_db_gateway_env(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.db_gateway.url == "https://db-gateway.example.com"
    assert cfg.db_gateway.api_key == "dbgw_xxx"
    assert cfg.db_gateway.request_timeout_seconds == 35


def test_config_loads_zendesk_browser_relay_env(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.public_reply.browser_relay_url == "https://zendesk.smlershou.top/zendesk-reply-mcp"
    assert cfg.public_reply.browser_relay_api_key == "relay_xxx"
    assert cfg.public_reply.browser_relay_timeout_seconds == 55
    assert cfg.public_reply.freshness_callback_url == (
        "https://agent.example.com/webhook/relay-pre-send-freshness"
    )


def test_config_loads_feishu_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.feishu.daily_summary_time == "09:00"
    assert cfg.feishu.webhook_url == ""
    assert cfg.feishu.webhook_secret == ""


def test_config_loads_repos_aliases(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.repos.aliases["d"] == "dify"
    assert cfg.repos.aliases["bill"] == "dify-billing"
    assert cfg.repos.aliases["plugin"] == "dify-plugin-daemon"
    assert cfg.repos.aliases["daemon"] == "dify-plugin-daemon"
    assert cfg.repos.aliases["deploy"] == "saas-deploy"
    assert len(cfg.repos.repositories) == 6
    assert cfg.repos.repositories[0].name == "dify"


def test_config_loads_zendesk_mention_aliases(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.zendesk.mention_trigger == "@saas-bot"
    assert cfg.zendesk.mention_aliases == ["@yonggang"]


def test_config_resolves_repos_base_dir_under_resources(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.repos.base_dir.endswith("saas源代码")
