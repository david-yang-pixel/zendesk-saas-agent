from __future__ import annotations

import json
import os

import pytest

from zd_agent.config import AppConfig
from zd_agent.services.admin_config import AdminConfigStore


def test_schema_contains_editable_model_base_url_and_secret_field(tmp_path) -> None:
    store = AdminConfigStore(tmp_path)

    schema = store.schema()
    by_path = {field["path"]: field for field in schema["fields"]}

    assert by_path["llm.base_url"]["editable"] is True
    assert by_path["llm.model"]["input"] == "text"
    assert by_path["llm.reasoning_effort"]["input"] == "reasoning_effort"
    assert by_path["llm.reasoning_effort"]["model_path"] == "llm.model"
    assert by_path["llm.enable_vision_input"]["input"] == "boolean"
    assert by_path["llm.timeout"]["input"] == "number"
    assert by_path["openai.api_key"]["secret"] is True
    assert by_path["openai.api_key"]["editable"] is True
    assert by_path["retry.max_attempts"]["group"] == "模型与运行"
    assert by_path["casework.max_investigation_rounds"]["input"] == "number"
    assert by_path["casework.max_investigation_calls"]["input"] == "number"
    assert by_path["casework.max_investigation_tokens"]["input"] == "number"
    assert not any(path.startswith("agent.guardrails.") for path in by_path)
    assert "retry.llm.max_attempts" not in by_path
    assert "retry.zendesk.max_attempts" not in by_path
    assert "retry.sandbox.max_attempts" not in by_path
    assert by_path["intake.silence_minutes_default"]["group"] == "触发配置"
    assert by_path["zendesk.mention_trigger"]["label"] == "主 @mention 触发词"
    assert by_path["zendesk.mention_aliases"]["input"] == "textarea"
    assert by_path["zendesk.mention_aliases"]["group"] == "触发配置"
    assert by_path["mcp_services.custom"]["input"] == "json"
    assert not any(path.startswith("trigger_decider.") for path in by_path)
    assert not any(path.startswith("skills.") for path in by_path)
    assert not any(path.startswith("prompts.") for path in by_path)
    assert not any("subagent" in path for path in by_path)
    assert schema["reasoning_capabilities"][0]["model_prefix"] == "gpt-5.6"


def test_schema_covers_agent_control_plane_settings(tmp_path) -> None:
    store = AdminConfigStore(tmp_path)

    schema = store.schema()
    by_path = {field["path"]: field for field in schema["fields"]}

    for path in [
        "zendesk.subdomain",
        "zendesk.api_token",
        "webhook.zendesk_messaging_path",
        "intake.post_agent_cooldown_seconds",
        "waiting_poller.poll_new_messages",
        "public_reply.browser_relay_timeout_seconds",
        "public_reply.freshness_callback_url",
        "zendesk_mcp.request_timeout_seconds",
        "db_gateway.request_timeout_seconds",
        "signoz.request_timeout_seconds",
        "logging.jsonl_enabled",
        "logging.log_dir",
        "archive.archive_dir",
    ]:
        assert path in by_path
    assert by_path["zendesk.api_token"]["secret"] is True
    assert by_path["webhook.listen_port"]["restart_required"] is True
    assert by_path["logging.jsonl_enabled"]["input"] == "boolean"
    assert by_path["intake.post_agent_cooldown_seconds"]["group"] == "触发配置"


def test_v2_config_has_no_prompt_skill_or_trigger_classifier_surface() -> None:
    cfg = AppConfig()

    assert not hasattr(cfg, "skills")
    assert not hasattr(cfg, "prompts")
    assert not hasattr(cfg, "trigger_decider")
    assert not hasattr(cfg, "sandbox_worker")
    assert cfg.mcp_services.custom == []


def test_schema_groups_browser_relay_with_tool_integrations(tmp_path) -> None:
    store = AdminConfigStore(tmp_path)

    schema = store.schema()
    by_path = {field["path"]: field for field in schema["fields"]}

    assert by_path["public_reply.browser_relay_url"]["group"] == "工具与集成"
    assert by_path["public_reply.freshness_callback_url"]["group"] == "工具与集成"
    assert by_path["public_reply.browser_relay_api_key"]["group"] == "工具与集成"


def _active_remote_public_reply_config() -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply.enabled = True
    cfg.public_reply.browser_relay_url = "https://relay.example.test/zendesk-reply-mcp"
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    cfg.public_reply.freshness_callback_url = (
        "https://relay.example.test/webhook/relay-pre-send-freshness"
    )
    return cfg


def test_apply_rejects_remote_relay_without_https_callback(tmp_path) -> None:
    cfg = _active_remote_public_reply_config()
    store = AdminConfigStore(tmp_path)

    with pytest.raises(ValueError, match="HTTPS.*non-loopback"):
        store.apply(
            cfg,
            {
                "changes": {
                    "public_reply.freshness_callback_url": "",
                    "casework.max_investigation_rounds": 19,
                },
                "reason": "remove callback",
            },
            actor="admin",
            ip="127.0.0.1",
        )

    assert cfg.public_reply.freshness_callback_url.startswith("https://")
    assert cfg.casework.max_investigation_rounds != 19
    assert not store.overrides_path.exists()
    assert not store.secrets_path.exists()
    assert not store.audit_path.exists()


def test_apply_write_failure_leaves_runtime_and_files_unchanged(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _active_remote_public_reply_config()
    original_key = cfg.public_reply.browser_relay_api_key
    original_window = cfg.public_reply.silence_window_seconds
    store = AdminConfigStore(tmp_path)
    real_write_yaml = store._write_yaml
    calls = 0

    def fail_second_write(path, data, *, secret=False):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk write failed")
        return real_write_yaml(path, data, secret=secret)

    monkeypatch.delenv("ZENDESK_BROWSER_RELAY_API_KEY", raising=False)
    monkeypatch.setattr(store, "_write_yaml", fail_second_write)

    with pytest.raises(OSError, match="disk write failed"):
        store.apply(
            cfg,
            {
                "changes": {
                    "public_reply.browser_relay_api_key": "replacement-key",
                    "public_reply.silence_window_seconds": 12,
                },
                "reason": "test failed persistence",
            },
            actor="admin",
            ip="127.0.0.1",
        )

    assert cfg.public_reply.browser_relay_api_key == original_key
    assert cfg.public_reply.silence_window_seconds == original_window
    assert "ZENDESK_BROWSER_RELAY_API_KEY" not in os.environ
    assert not store.overrides_path.exists()
    assert not store.secrets_path.exists()
    assert not store.audit_path.exists()


def test_apply_rejects_nul_secret_before_persisting(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _active_remote_public_reply_config()
    original_key = cfg.public_reply.browser_relay_api_key
    store = AdminConfigStore(tmp_path)
    monkeypatch.delenv("ZENDESK_BROWSER_RELAY_API_KEY", raising=False)

    with pytest.raises(ValueError, match="must not contain NUL"):
        store.apply(
            cfg,
            {
                "changes": {
                    "public_reply.browser_relay_api_key": "bad\0secret",
                },
                "reason": "reject invalid secret",
            },
            actor="admin",
            ip="127.0.0.1",
        )

    assert cfg.public_reply.browser_relay_api_key == original_key
    assert "ZENDESK_BROWSER_RELAY_API_KEY" not in os.environ
    assert not store.overrides_path.exists()
    assert not store.secrets_path.exists()
    assert not store.audit_path.exists()


def test_apply_allows_disabling_public_reply_and_clearing_callback(
    tmp_path,
) -> None:
    cfg = _active_remote_public_reply_config()
    store = AdminConfigStore(tmp_path)

    result = store.apply(
        cfg,
        {
            "changes": {
                "public_reply.enabled": False,
                "public_reply.freshness_callback_url": "",
            },
            "reason": "disable public replies",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert result["ok"] is True
    assert cfg.public_reply.enabled is False
    assert cfg.public_reply.freshness_callback_url == ""


def test_apply_parses_custom_mcp_services(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    result = store.apply(
        cfg,
        {
            "changes": {
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
            "reason": "add custom mcp",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert result["ok"] is True
    assert cfg.mcp_services.custom[0].name == "linear_mcp"
    assert cfg.mcp_services.custom[0].mcp_url == "https://linear.example.test/mcp"
    assert cfg.mcp_services.custom[0].request_timeout_seconds == 3
    assert (
        store.effective_config(cfg)["values"]["mcp_services.custom"][0]["name"]
        == "linear_mcp"
    )


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
                "llm.model": "gpt-5.6-terra",
                "llm.reasoning_effort": "high",
                "intake.post_agent_cooldown_seconds": 90,
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
    assert cfg.llm.model == "gpt-5.6-terra"
    assert cfg.llm.reasoning_effort == "high"
    assert cfg.intake.post_agent_cooldown_seconds == 90
    assert cfg.db_gateway.api_key == "db-test-secret"
    assert store.overrides_path.exists()
    assert store.secrets_path.exists()
    audit_text = store.audit_path.read_text()
    assert "sk-test-secret" not in audit_text
    assert "db-test-secret" not in audit_text
    assert "llm.base_url" in store.audit_path.read_text()
    assert store.list_audit_logs()["items"][0]["actor"] == "admin"


def test_apply_trims_model_ids_before_persisting(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    store.apply(
        cfg,
        {
            "changes": {
                "llm.model": "  gpt-5.6-terra  ",
                "llm.reasoning_effort": "max",
            },
            "reason": "normalize model ids",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert cfg.llm.model == "gpt-5.6-terra"
    assert "  gpt-5.6-terra  " not in store.overrides_path.read_text()


def test_apply_rejects_reasoning_effort_not_supported_by_new_model(tmp_path) -> None:
    cfg = AppConfig()
    cfg.llm.model = "gpt-5.6-terra"
    cfg.llm.reasoning_effort = "max"
    store = AdminConfigStore(tmp_path)

    with pytest.raises(ValueError, match="does not support reasoning effort 'max'"):
        store.apply(
            cfg,
            {
                "changes": {"llm.model": "gpt-5.4-mini"},
                "reason": "switch model",
            },
            actor="admin",
            ip="127.0.0.1",
        )

    assert not store.overrides_path.exists()
    assert cfg.llm.model == "gpt-5.6-terra"


def test_apply_saved_rejects_invalid_reasoning_override_without_partial_mutation(
    tmp_path,
) -> None:
    cfg = AppConfig()
    cfg.llm.model = "gpt-5.6-terra"
    cfg.llm.reasoning_effort = "high"
    store = AdminConfigStore(tmp_path)
    store.overrides_path.write_text(
        "llm:\n  model: gpt-5.4-mini\n  reasoning_effort: max\n"
    )

    with pytest.raises(ValueError, match="does not support reasoning effort 'max'"):
        store.apply_saved(cfg)

    assert cfg.llm.model == "gpt-5.6-terra"
    assert cfg.llm.reasoning_effort == "high"


def test_apply_saved_migrates_legacy_admin_override_paths(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)
    store.overrides_path.write_text(
        "trigger_decider:\n"
        "  silence_minutes_default: 4\n"
        "  post_agent_cooldown_seconds: 17\n"
        "waiting_poller:\n"
        "  abandoned_hours: 6\n",
    )

    store.apply_saved(cfg)

    assert cfg.intake.silence_minutes_default == 4
    assert cfg.intake.post_agent_cooldown_seconds == 17
    assert cfg.waiting_poller.customer_wait_timeout_hours == 6
    persisted = store.overrides_path.read_text()
    assert "trigger_decider" not in persisted
    assert "abandoned_hours" not in persisted
    assert "post_agent_cooldown_seconds: 17" in persisted
    assert "silence_minutes_default: 4" in persisted
    assert "customer_wait_timeout_hours: 6" in persisted
    audit = json.loads(store.audit_path.read_text().splitlines()[-1])
    assert audit["action"] == "config_override_migration"
    assert {item["resolution"] for item in audit["diff"]} == {"migrated"}


def test_apply_saved_preserves_canonical_override_on_legacy_conflict(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)
    store.overrides_path.write_text(
        "intake:\n"
        "  post_agent_cooldown_seconds: 29\n"
        "trigger_decider:\n"
        "  post_agent_cooldown_seconds: 17\n",
    )

    store.apply_saved(cfg)

    assert cfg.intake.post_agent_cooldown_seconds == 29
    persisted = store.overrides_path.read_text()
    assert "trigger_decider" not in persisted
    audit = json.loads(store.audit_path.read_text().splitlines()[-1])
    assert audit["diff"] == [
        {
            "from": "trigger_decider.post_agent_cooldown_seconds",
            "resolution": "canonical_preserved",
            "to": "intake.post_agent_cooldown_seconds",
        },
    ]


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


@pytest.mark.parametrize(
    "path",
    [
        "agent.max_concurrent_sessions",
        "waiting_poller.customer_wait_timeout_hours",
    ],
)
def test_constructor_bound_runtime_fields_require_restart(path: str) -> None:
    from zd_agent.services.admin_config import FIELD_SPECS

    spec = next(field for field in FIELD_SPECS if field.path == path)

    assert spec.restart_required is True


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
