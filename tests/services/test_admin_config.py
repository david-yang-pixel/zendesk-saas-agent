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
    assert by_path["openai.api_key"]["secret"] is True
    assert by_path["openai.api_key"]["editable"] is True
    assert by_path["prompts.ask_extra_instructions"]["input"] == "textarea"
    assert by_path["prompts.agent_extra_instructions"]["input"] == "textarea"


def test_schema_groups_browser_relay_with_tool_integrations(tmp_path) -> None:
    store = AdminConfigStore(tmp_path)

    schema = store.schema()
    by_path = {field["path"]: field for field in schema["fields"]}

    assert by_path["public_reply.browser_relay_url"]["group"] == "工具与集成"
    assert by_path["public_reply.browser_relay_api_key"]["group"] == "工具与集成"


def test_effective_config_redacts_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = AppConfig()
    cfg.llm.base_url = "https://llm.example.test/v1"
    store = AdminConfigStore(tmp_path)

    effective = store.effective_config(cfg)

    assert effective["values"]["llm.base_url"] == "https://llm.example.test/v1"
    assert effective["values"]["openai.api_key"]["configured"] is False
    assert effective["values"]["admin_auth.password_login_enabled"] is True
    assert effective["values"]["admin_auth.google_sso_enabled"] is False
    assert effective["values"]["admin_auth.feishu_sso_enabled"] is False
    assert "sk-" not in json.dumps(effective["values"]).lower()


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
            },
            "reason": "test change",
        },
    )

    diff = {item["path"]: item for item in preview["diff"]}
    assert diff["llm.model"]["old"] == "old-model"
    assert diff["llm.model"]["new"] == "new-model"
    assert diff["openai.api_key"]["old"] == {"configured": False, "fingerprint": ""}
    assert diff["openai.api_key"]["new"]["configured"] is True
    assert "sk-test-secret" not in json.dumps(preview)


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
            },
            "reason": "switch model proxy",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert result["ok"] is True
    assert cfg.llm.base_url == "https://proxy.example.test/v1"
    assert cfg.llm.model == "gpt-custom"
    assert store.overrides_path.exists()
    assert store.secrets_path.exists()
    assert "sk-test-secret" not in store.audit_path.read_text()
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
