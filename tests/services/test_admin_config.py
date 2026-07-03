from __future__ import annotations

import json

from zd_agent.config import AppConfig
from zd_agent.services.admin_config import AdminConfigStore


def test_schema_contains_editable_model_base_url_and_secret_field(tmp_path) -> None:
    store = AdminConfigStore(tmp_path)

    schema = store.schema()
    by_path = {field["path"]: field for field in schema["fields"]}

    assert by_path["llm.base_url"]["editable"] is True
    assert by_path["llm.model"]["input"] == "text"
    assert by_path["llm.timeout"]["input"] == "number"
    assert by_path["openai.api_key"]["secret"] is True
    assert by_path["openai.api_key"]["editable"] is True
    assert by_path["prompts.ask_extra_instructions"]["input"] == "textarea"
    assert by_path["prompts.agent_extra_instructions"]["input"] == "textarea"
    assert by_path["prompts.agent_extra_instructions"]["label"] == "系统提示词"
    assert by_path["retry.max_attempts"]["group"] == "模型与运行"
    assert by_path["trigger_decider.llm_model"]["group"] == "模型与运行"
    assert by_path["zendesk.mention_trigger"]["label"] == "主 @mention 触发词"
    assert by_path["zendesk.mention_aliases"]["input"] == "textarea"
    assert by_path["zendesk.mention_aliases"]["group"] == "触发配置"
    assert by_path["skills.custom"]["input"] == "json"
    assert by_path["mcp_services.custom"]["input"] == "json"


def test_schema_covers_agent_control_plane_settings(tmp_path) -> None:
    store = AdminConfigStore(tmp_path)

    schema = store.schema()
    by_path = {field["path"]: field for field in schema["fields"]}

    for path in [
        "zendesk.subdomain",
        "zendesk.api_token",
        "webhook.zendesk_messaging_path",
        "trigger_decider.post_agent_cooldown_seconds",
        "waiting_poller.poll_new_messages",
        "public_reply.browser_relay_timeout_seconds",
        "zendesk_mcp.request_timeout_seconds",
        "db_gateway.request_timeout_seconds",
        "signoz.request_timeout_seconds",
        "skills.enabled",
        "logging.jsonl_enabled",
        "logging.log_dir",
        "knowledge.archive_dir",
    ]:
        assert path in by_path
    assert by_path["zendesk.api_token"]["secret"] is True
    assert by_path["webhook.listen_port"]["restart_required"] is True
    assert by_path["logging.jsonl_enabled"]["input"] == "boolean"
    assert by_path["trigger_decider.enable_public_nudge"]["group"] == "触发配置"
    assert by_path["skills.enabled"]["group"] == "工具与集成"
    assert by_path["skills.enabled"]["input"] == "textarea"


def test_default_enabled_skill_is_server_support_skill() -> None:
    cfg = AppConfig()

    assert cfg.skills.enabled == ["dify_support_ticket"]
    assert cfg.skills.custom == []
    assert cfg.mcp_services.custom == []


def test_schema_groups_browser_relay_with_tool_integrations(tmp_path) -> None:
    store = AdminConfigStore(tmp_path)

    schema = store.schema()
    by_path = {field["path"]: field for field in schema["fields"]}

    assert by_path["public_reply.browser_relay_url"]["group"] == "工具与集成"
    assert by_path["public_reply.browser_relay_api_key"]["group"] == "工具与集成"


def test_apply_parses_enabled_skills(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    result = store.apply(
        cfg,
        {
            "changes": {
                "skills.enabled": "dify_support_ticket\n dify_support_ticket",
            },
            "reason": "limit active skills",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert result["ok"] is True
    assert cfg.skills.enabled == ["dify_support_ticket"]
    assert store.effective_config(cfg)["values"]["skills.enabled"] == ["dify_support_ticket"]


def test_apply_parses_custom_skills_and_mcp_services(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    result = store.apply(
        cfg,
        {
            "changes": {
                "skills.custom": json.dumps([
                    {
                        "name": "billing_refund_skill",
                        "description": "Refund handling SOP",
                        "content": "Always verify workspace owner before refund checks.",
                        "enabled": True,
                    },
                ]),
                "skills.enabled": "dify_support_ticket\nbilling_refund_skill",
                "mcp_services.custom": [
                    {
                        "name": "linear_mcp",
                        "label": "Linear MCP",
                        "description": "Issue tracking",
                        "mcp_url": "https://linear.example.test/mcp/",
                        "mcp_api_key": "linear-secret",
                        "request_timeout_seconds": 1,
                        "enabled": True,
                    },
                ],
            },
            "reason": "add custom skill and mcp",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert result["ok"] is True
    assert cfg.skills.enabled == ["dify_support_ticket", "billing_refund_skill"]
    assert cfg.skills.custom[0].name == "billing_refund_skill"
    assert cfg.skills.custom[0].content == "Always verify workspace owner before refund checks."
    assert cfg.mcp_services.custom[0].name == "linear_mcp"
    assert cfg.mcp_services.custom[0].mcp_url == "https://linear.example.test/mcp"
    assert cfg.mcp_services.custom[0].request_timeout_seconds == 3
    assert store.effective_config(cfg)["values"]["mcp_services.custom"][0]["name"] == "linear_mcp"


def test_apply_rejects_unknown_enabled_skill(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    try:
        store.apply(
            cfg,
            {
                "changes": {
                    "skills.enabled": "missing_skill",
                },
                "reason": "bad skill",
            },
            actor="admin",
            ip="127.0.0.1",
        )
    except ValueError as exc:
        assert "unknown Skills: missing_skill" in str(exc)
    else:
        raise AssertionError("unknown skill should fail")


def test_effective_config_redacts_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "zendesk-token-secret")
    cfg = AppConfig()
    cfg.llm.base_url = "https://llm.example.test/v1"
    store = AdminConfigStore(tmp_path)

    effective = store.effective_config(cfg)
    dumped = json.dumps(effective["values"])

    assert effective["values"]["llm.base_url"] == "https://llm.example.test/v1"
    assert effective["values"]["openai.api_key"]["configured"] is True
    assert effective["values"]["zendesk.api_token"]["configured"] is True
    assert effective["values"]["admin_auth.password_login_enabled"] is True
    assert effective["values"]["admin_auth.google_sso_enabled"] is False
    assert effective["values"]["admin_auth.feishu_sso_enabled"] is False
    assert "sk-test-secret" not in dumped
    assert "zendesk-token-secret" not in dumped


def test_preview_reports_diff_and_redacts_secret_values(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = AppConfig()
    cfg.llm.model = "old-model"
    store = AdminConfigStore(tmp_path)

    preview = store.preview(
        cfg,
        {
            "changes": {
                "llm.model": "new-model",
                "openai.api_key": "sk-test-secret",
                "zendesk.api_token": "zendesk-token-secret",
            },
            "reason": "test change",
        },
    )

    diff = {item["path"]: item for item in preview["diff"]}
    assert diff["llm.model"]["old"] == "old-model"
    assert diff["llm.model"]["new"] == "new-model"
    assert diff["openai.api_key"]["old"] == {"configured": False, "fingerprint": ""}
    assert diff["openai.api_key"]["new"]["configured"] is True
    assert diff["zendesk.api_token"]["new"]["configured"] is True
    assert "sk-test-secret" not in json.dumps(preview)
    assert "zendesk-token-secret" not in json.dumps(preview)


def test_apply_persists_overrides_secrets_and_audit(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    result = store.apply(
        cfg,
        {
            "changes": {
                "llm.base_url": "https://proxy.example.test/v1/",
                "llm.model": "gpt-custom",
                "openai.api_key": "sk-test-secret",
                "db_gateway.api_key": "db-test-secret",
            },
            "reason": "switch model proxy",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert result["ok"] is True
    assert cfg.llm.base_url == "https://proxy.example.test/v1"
    assert cfg.llm.model == "gpt-custom"
    assert cfg.db_gateway.api_key == "db-test-secret"
    assert store.overrides_path.exists()
    assert store.secrets_path.exists()
    audit_text = store.audit_path.read_text()
    assert "sk-test-secret" not in audit_text
    assert "db-test-secret" not in audit_text
    assert "llm.base_url" in store.audit_path.read_text()
    assert store.list_audit_logs()["items"][0]["actor"] == "admin"


def test_restart_required_overrides_wait_for_restart(tmp_path) -> None:
    cfg = AppConfig()
    cfg.zendesk.poll_interval_seconds = 60
    store = AdminConfigStore(tmp_path)

    result = store.apply(
        cfg,
        {
            "changes": {
                "zendesk.poll_interval_seconds": 120,
            },
            "reason": "slow polling",
        },
        actor="admin",
        ip="127.0.0.1",
    )
    restarted_cfg = AppConfig()
    store.apply_saved(restarted_cfg)

    assert result["restart_required"] is True
    assert cfg.zendesk.poll_interval_seconds == 60
    assert store.effective_config(cfg)["values"]["zendesk.poll_interval_seconds"] == 120
    assert restarted_cfg.zendesk.poll_interval_seconds == 120


def test_apply_parses_zendesk_mention_aliases(tmp_path) -> None:
    cfg = AppConfig()
    cfg.zendesk.mention_aliases = ["@old"]
    store = AdminConfigStore(tmp_path)

    result = store.apply(
        cfg,
        {
            "changes": {
                "zendesk.mention_aliases": "@yonggang\n@saas-agent, @YONGGANG\n\n",
            },
            "reason": "show mention aliases",
        },
        actor="admin",
        ip="127.0.0.1",
    )
    restarted_cfg = AppConfig()
    store.apply_saved(restarted_cfg)

    assert result["ok"] is True
    assert cfg.zendesk.mention_aliases == ["@yonggang", "@saas-agent"]
    assert store.effective_config(cfg)["values"]["zendesk.mention_aliases"] == [
        "@yonggang",
        "@saas-agent",
    ]
    assert restarted_cfg.zendesk.mention_aliases == ["@yonggang", "@saas-agent"]


def test_apply_saved_skips_admin_auth_secrets_without_leaking_audit(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    result = store.apply(
        cfg,
        {
            "changes": {
                "admin_auth.google_sso_enabled": True,
                "admin_auth.google_client_id": "google-client",
                "admin_auth.google_client_secret": "google-secret",
                "admin_auth.feishu_sso_enabled": True,
                "admin_auth.feishu_app_id": "feishu-app",
                "admin_auth.feishu_app_secret": "feishu-secret",
            },
            "reason": "configure admin sso",
        },
        actor="admin",
        ip="127.0.0.1",
    )
    restarted_cfg = AppConfig()

    store.apply_saved(restarted_cfg)
    effective = store.effective_config(restarted_cfg)

    assert result["ok"] is True
    assert effective["values"]["admin_auth.google_sso_enabled"] is True
    assert effective["values"]["admin_auth.google_client_secret"]["configured"] is True
    assert effective["values"]["admin_auth.feishu_sso_enabled"] is True
    assert effective["values"]["admin_auth.feishu_app_secret"]["configured"] is True
    audit_text = store.audit_path.read_text()
    assert "google-secret" not in audit_text
    assert "feishu-secret" not in audit_text
