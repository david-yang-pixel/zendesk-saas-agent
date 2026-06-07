"""Test that config loads all new SaaS-related fields."""
import pytest

from zd_agent.config import load_config


SAAS_CONFIG_YAML = """
agent:
  max_concurrent_sessions: 5
llm:
  provider: openai
  model: gpt-5.4
resources:
  base_dir: /opt/zd-agent/resources
  saas_codebase_dir: saas源代码
  knowledge_base_dir: saas版知识库
  ticket_records_dir: zendesk-cli/工单记录
  state_dir: .state
zendesk:
  search_query: "status:new tags:saas"
  mention_trigger: "@saas-bot"
trigger_decider:
  llm_model: gpt-5.4-mini
  silence_minutes_default: 1
  silence_extend_to_minutes: 5
  rapid_burst_window_seconds: 30
db_gateway:
  mcp_enabled: true
  request_timeout_seconds: 35
signoz:
  url: https://dify.us2.signoz.cloud
feishu:
  daily_summary_time: "09:00"
repos:
  base_dir: ""
  repositories:
    - dify
    - dify-billing
    - dify-marketplace-backend
    - dify-marketplace-frontend
    - saas-deploy
  aliases:
    d: dify
    main: dify
    bill: dify-billing
    billing: dify-billing
    mb: dify-marketplace-backend
    mf: dify-marketplace-frontend
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
        "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
        "FEISHU_WEBHOOK_SECRET": "fs_sign_xxx",
        "SIGNOZ_API_KEY": "sn_xxx",
        "GITHUB_TOKEN": "gh_xxx",
        "DB_GATEWAY_URL": "https://db-gateway.example.com/",
        "DB_GATEWAY_API_KEY": "dbgw_xxx",
        "DB_GATEWAY_MCP_BINARY": "/opt/db-gateway/code/scripts/db_gateway_mcp.py",
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
    assert cfg.trigger_decider.llm_model == "gpt-5.4-mini"
    assert cfg.trigger_decider.silence_minutes_default == 1
    assert cfg.trigger_decider.silence_extend_to_minutes == 5
    assert cfg.trigger_decider.rapid_burst_window_seconds == 30


def test_config_loads_signoz_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.signoz.url == "https://dify.us2.signoz.cloud"
    assert cfg.signoz.api_key == "sn_xxx"


def test_config_loads_db_gateway_env(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.db_gateway.url == "https://db-gateway.example.com"
    assert cfg.db_gateway.api_key == "dbgw_xxx"
    assert cfg.db_gateway.mcp_binary == "/opt/db-gateway/code/scripts/db_gateway_mcp.py"
    assert cfg.db_gateway.request_timeout_seconds == 35


def test_config_loads_feishu_section(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.feishu.daily_summary_time == "09:00"
    assert cfg.feishu.webhook_url == "https://open.feishu.cn/open-apis/bot/v2/hook/test"
    assert cfg.feishu.webhook_secret == "fs_sign_xxx"


def test_config_loads_repos_aliases(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.repos.aliases["d"] == "dify"
    assert cfg.repos.aliases["bill"] == "dify-billing"
    assert cfg.repos.aliases["deploy"] == "saas-deploy"
    assert len(cfg.repos.repositories) == 5
    assert cfg.repos.repositories[0].name == "dify"


def test_config_resolves_repos_base_dir_under_resources(saas_env, saas_config_file):
    cfg = load_config(saas_config_file)
    assert cfg.repos.base_dir.endswith("saas源代码")
