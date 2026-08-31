"""Test that config loads all new SaaS-related fields."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from zd_agent.config import (
    AppConfig,
    IntakeConfig,
    LLMConfig,
    PublicReplyConfig,
    SubagentConfig,
    SubagentLLMConfig,
    SubagentSlotConfig,
    ZendeskConfig,
    load_config,
)


SAAS_CONFIG_YAML = """
agent:
  max_concurrent_sessions: 5
llm:
  provider: openai
  model: gpt-5.4
  reasoning_effort: high
  enable_vision_input: true
resources:
  base_dir: /opt/zd-agent/resources
  saas_codebase_dir: saas源代码
  ticket_records_dir: zendesk-cli/工单记录
  state_dir: .state
zendesk:
  search_query: "status:new tags:saas"
  mention_trigger: "@saas-bot"
  mention_aliases:
    - "@yonggang"
intake:
  silence_minutes_default: 0
  post_agent_cooldown_seconds: 180
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
        "GITHUB_TOKEN": "gh_xxx",
        "DB_GATEWAY_URL": "https://db-gateway.example.com/",
        "DB_GATEWAY_API_KEY": "dbgw_xxx",
        "ZENDESK_BROWSER_RELAY_URL": "https://zendesk.smlershou.top/zendesk-reply-mcp/",
        "ZENDESK_BROWSER_RELAY_API_KEY": "relay_xxx",
        "ZENDESK_BROWSER_RELAY_TIMEOUT_SECONDS": "55",
        "ZENDESK_BROWSER_RELAY_READINESS_URL": "http://127.0.0.1:18097/ready/",
        "ZENDESK_BROWSER_RELAY_READINESS_INTERVAL_SECONDS": "45",
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
    assert cfg.resources.ticket_records_dir == "zendesk-cli/工单记录"
    assert cfg.resources.state_dir == ".state"


def test_config_loads_v2_intake_and_model_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.llm.enable_vision_input is True
    assert cfg.llm.reasoning_effort == "high"
    assert cfg.intake.silence_minutes_default == 0
    assert cfg.intake.post_agent_cooldown_seconds == 180


def test_llm_config_defaults_to_provider_managed_reasoning() -> None:
    cfg = LLMConfig()

    assert cfg.reasoning_effort == ""
    assert cfg.enable_vision_input is True
    assert IntakeConfig().post_agent_cooldown_seconds == 180


def test_subagent_slots_use_explicit_model_configuration_without_main_fallback() -> None:
    cfg = AppConfig(
        llm=LLMConfig(provider="openai", model="main-model"),
        subagents=SubagentConfig(
            enabled=True,
            slots=[
                SubagentSlotConfig(
                    id="evidence-reader",
                    label="Evidence reader",
                    llm=SubagentLLMConfig(
                        provider="anthropic",
                        model="child-model",
                        api_key="child-secret",
                        base_url="https://child.example.test/v1",
                    ),
                )
            ],
        ),
    )

    slot = cfg.subagents.slots[0]
    assert slot.llm.provider == "anthropic"
    assert slot.llm.model == "child-model"
    assert slot.llm.api_key == "child-secret"
    assert slot.llm.base_url == "https://child.example.test/v1"
    assert slot.llm.model != cfg.llm.model


def test_enabled_subagent_slot_rejects_missing_explicit_provider_or_model() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "subagents": {
                    "enabled": True,
                    "slots": [
                        {
                            "id": "missing-provider",
                            "llm": {"model": "child-model", "api_key": "secret"},
                        }
                    ],
                }
            }
        )


def test_subagent_config_rejects_duplicate_slot_ids() -> None:
    slot = {
        "id": "duplicate",
        "llm": {
            "provider": "openai",
            "model": "child-model",
            "api_key": "secret",
        },
    }

    with pytest.raises(ValidationError, match="duplicate subagent slot id"):
        AppConfig.model_validate(
            {"subagents": {"enabled": True, "slots": [slot, slot]}}
        )


def test_repository_config_enables_v2_visual_evidence() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert raw["llm"]["enable_vision_input"] is True
    assert "trigger_decider" not in raw
    assert raw["intake"]["post_agent_cooldown_seconds"] == 180
    assert raw["subagents"]["enabled"] is False
    assert raw["subagents"]["slots"] == []


def test_repository_config_persists_agent_archives_under_resources_mount() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert raw["archive"]["archive_dir"] == ("/opt/zd-agent/resources/agent-runs")


def test_llm_config_rejects_reasoning_effort_unsupported_by_model() -> None:
    with pytest.raises(
        ValidationError, match="does not support reasoning effort 'max'"
    ):
        LLMConfig(model="gpt-5.4-mini", reasoning_effort="max")


def test_model_ids_are_trimmed_before_reasoning_validation() -> None:
    llm = LLMConfig(
        model="  gpt-5.6-terra  ",
        reasoning_effort="max",
    )

    assert llm.model == "gpt-5.6-terra"


def test_legacy_trigger_decider_yaml_only_migrates_v2_intake_timings(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ZD_MODEL", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "trigger_decider:\n"
        "  llm_model: ignored-legacy-model\n"
        "  enable_public_nudge: true\n"
        "  silence_minutes_default: 2\n"
        "  post_agent_cooldown_seconds: 45\n"
    )

    cfg = load_config(str(config_path))

    assert cfg.intake == IntakeConfig(
        silence_minutes_default=2,
        post_agent_cooldown_seconds=45,
    )
    assert not hasattr(cfg, "trigger_decider")


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
    config_path.write_text("llm:\n  model: gpt-5.6-terra\n  reasoning_effort: max\n")
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
    assert (
        cfg.notification_service.mcp_url
        == "https://ops.example.com/notification-service"
    )
    assert cfg.notification_service.mcp_api_key == "notification_mcp_xxx"
    assert cfg.notification_service.request_timeout_seconds == 45


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
    assert (
        cfg.public_reply.browser_relay_url
        == "https://zendesk.smlershou.top/zendesk-reply-mcp"
    )
    assert cfg.public_reply.browser_relay_api_key == "relay_xxx"
    assert cfg.public_reply.browser_relay_timeout_seconds == 55
    assert (
        cfg.public_reply.browser_relay_readiness_url
        == "http://127.0.0.1:18097/ready"
    )
    assert cfg.public_reply.browser_relay_readiness_interval_seconds == 45
    assert cfg.public_reply.freshness_callback_url == (
        "https://agent.example.com/webhook/relay-pre-send-freshness"
    )


def test_browser_relay_readiness_url_rejects_remote_secret_exfiltration() -> None:
    with pytest.raises(ValidationError, match="must be a loopback URL"):
        PublicReplyConfig(
            browser_relay_readiness_url="https://attacker.example/collect"
        )


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


def test_default_mention_polling_meets_interactive_latency_budget() -> None:
    interval = ZendeskConfig().mention_poll_interval_seconds
    assert 0 < interval <= 5


def test_repository_config_meets_mention_latency_budget() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    interval = raw["zendesk"]["mention_poll_interval_seconds"]
    assert 0 < interval <= 5


def test_config_resolves_repos_base_dir_under_resources(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.repos.base_dir.endswith("saas源代码")
