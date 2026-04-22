"""Test that config loads all new SaaS-related fields."""
import pytest

from zd_agent.config import load_config


@pytest.fixture
def saas_env(monkeypatch):
    env_vars = {
        "ZENDESK_SUBDOMAIN": "dify",
        "ZENDESK_EMAIL": "saas-bot@dify.ai",
        "ZENDESK_API_TOKEN": "tok",
        "ZENDESK_WEBHOOK_SECRET": "wsec",
        "OPENAI_API_KEY": "sk-xxx",
        "FEISHU_APP_ID": "cli_xxx",
        "FEISHU_APP_SECRET": "fs_xxx",
        "FEISHU_VERIFY_TOKEN": "vt_xxx",
        "FEISHU_ENCRYPT_KEY": "ek_xxx",
        "FEISHU_BOT_CHAT_ID": "oc_xxx",
        "GRAFANA_API_KEY": "glsa_xxx",
        "SIGNOZ_API_KEY": "sn_xxx",
        "PG_PASSWORD": "pg_xxx",
        "GITHUB_TOKEN": "gh_xxx",
        "TELEPORT_USER": "me",
    }
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    yield env_vars


def test_config_loads_resources_section(saas_env):
    cfg = load_config()
    assert cfg.resources.saas_codebase_dir == "saas源代码"
    assert cfg.resources.knowledge_base_dir == "saas版知识库"
    assert cfg.resources.ticket_records_dir == "zendesk-cli/工单记录"
    assert cfg.resources.state_dir == ".state"


def test_config_loads_trigger_decider_section(saas_env):
    cfg = load_config()
    assert cfg.trigger_decider.llm_model == "gpt-5.4-mini"
    assert cfg.trigger_decider.silence_minutes_default == 3
    assert cfg.trigger_decider.silence_extend_to_minutes == 5
    assert cfg.trigger_decider.rapid_burst_window_seconds == 30


def test_config_loads_postgres_section(saas_env):
    cfg = load_config()
    assert cfg.postgres.host == "localhost"
    assert cfg.postgres.port == 56061
    assert cfg.postgres.db_prod == "langgenius"
    assert cfg.postgres.db_billing == "billing"
    assert cfg.postgres.db_plugin == "dify_plugin_daemon"
    assert cfg.postgres.password == "pg_xxx"  # from env


def test_config_loads_grafana_signoz_sections(saas_env):
    cfg = load_config()
    assert cfg.grafana.url == "https://grafana.ops.dify.dev"
    assert cfg.grafana.api_key == "glsa_xxx"  # from env
    assert cfg.signoz.url == "https://dify.us2.signoz.cloud"
    assert cfg.signoz.api_key == "sn_xxx"


def test_config_loads_feishu_section(saas_env):
    cfg = load_config()
    assert cfg.feishu.daily_summary_time == "09:00"
    assert cfg.feishu.app_id == "cli_xxx"
    assert cfg.feishu.default_chat_id == "oc_xxx"


def test_config_loads_repos_aliases(saas_env):
    cfg = load_config()
    assert cfg.repos.aliases["d"] == "dify"
    assert cfg.repos.aliases["bill"] == "dify-billing"
    assert cfg.repos.aliases["deploy"] == "saas-deploy"
    # repositories coerced from list[str] to list[RepoEntry] via validator
    assert len(cfg.repos.repositories) == 5
    assert cfg.repos.repositories[0].name == "dify"


def test_config_resolves_repos_base_dir_under_resources(saas_env):
    cfg = load_config()
    # repos.base_dir should end with saas源代码 (under resources.base_dir)
    assert cfg.repos.base_dir.endswith("saas源代码")
