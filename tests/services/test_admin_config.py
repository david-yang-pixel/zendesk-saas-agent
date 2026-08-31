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
    assert by_path["llm.model"]["input"] == "model_picker"
    assert by_path["llm.reasoning_effort"]["input"] == "reasoning_effort"
    assert by_path["llm.reasoning_effort"]["model_path"] == "llm.model"
    assert by_path["llm.enable_vision_input"]["input"] == "vision_capability"
    assert by_path["llm.enable_vision_input"]["label"] == "视觉能力"
    assert by_path["llm.timeout"]["input"] == "number"
    assert by_path["llm.context_window_tokens"]["input"] == "number"
    assert by_path["openai.api_key"]["secret"] is True
    assert by_path["openai.api_key"]["editable"] is True
    assert by_path["retry.max_attempts"]["group"] == "模型与运行"
    assert by_path["agent.session_timeout_seconds"]["input"] == "number"
    assert by_path["agent.session_timeout_seconds"]["label"] == "单工单最长运行秒数"
    assert "casework.max_investigation_rounds" not in by_path
    assert "casework.max_investigation_calls" not in by_path
    assert "casework.max_investigation_tokens" not in by_path
    assert by_path["casework.context_compaction_soft_ratio"]["input"] == "number"
    assert by_path["casework.context_compaction_hard_ratio"]["input"] == "number"
    assert by_path["casework.context_checkpoint_max_tokens"]["input"] == "number"
    assert by_path["casework.context_retain_recent_rounds"]["input"] == "number"
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
    assert by_path["subagents.enabled"]["input"] == "boolean"
    assert by_path["subagents.max_parallel"]["input"] == "number"
    assert by_path["subagents.max_tasks_per_run"]["input"] == "number"
    assert by_path["subagents.task_timeout_seconds"]["input"] == "number"
    assert by_path["subagents.slots"]["input"] == "subagent_models"
    assert by_path["subagents.slots"]["group"] == "模型与运行"
    assert schema["reasoning_capabilities"][0]["model_prefix"] == "gpt-5.6-sol"


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


def test_apply_subagent_slots_persists_api_keys_only_in_secret_storage(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)
    slots = [
        {
            "id": "evidence-reader",
            "label": "Evidence reader",
            "enabled": True,
            "llm": {
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "low",
                "api_key": "child-secret",
                "base_url": "https://child.example.test/v1",
            },
        }
    ]

    store.apply(
        cfg,
        {
            "changes": {
                "subagents.enabled": True,
                "subagents.slots": slots,
            },
            "reason": "configure independent child model",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    overrides = store.overrides_path.read_text(encoding="utf-8")
    secrets = store.secrets_path.read_text(encoding="utf-8")
    effective = store.effective_config(cfg)["values"]["subagents.slots"]

    assert "child-secret" not in overrides
    assert "child-secret" in secrets
    assert "child-secret" not in store.audit_path.read_text(encoding="utf-8")
    assert "child-secret" not in json.dumps(effective)
    assert effective[0]["llm"]["api_key_configured"] is True
    assert cfg.subagents.enabled is True
    assert cfg.subagents.slots[0].llm.model == "gpt-5.6-luna"
    assert cfg.subagents.slots[0].llm.api_key == "child-secret"

    restarted = AppConfig()
    store.apply_saved(restarted)
    assert restarted.subagents.enabled is True
    assert restarted.subagents.slots[0].llm.model == "gpt-5.6-luna"
    assert restarted.subagents.slots[0].llm.api_key == "child-secret"


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
                    "casework.context_checkpoint_max_tokens": 19_000,
                },
                "reason": "remove callback",
            },
            actor="admin",
            ip="127.0.0.1",
        )

    assert cfg.public_reply.freshness_callback_url.startswith("https://")
    assert cfg.casework.context_checkpoint_max_tokens != 19_000
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


def test_apply_validates_the_complete_candidate_before_persisting(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    with pytest.raises(ValueError, match="上下文压缩软阈值比例必须小于硬限制比例"):
        store.apply(
            cfg,
            {
                "changes": {
                    "casework.context_compaction_soft_ratio": 0.95,
                    "agent.session_timeout_seconds": 1_440,
                },
                "reason": "reject invalid aggregate config",
            },
            actor="admin",
            ip="127.0.0.1",
        )

    assert cfg.casework.context_compaction_soft_ratio == 0.70
    assert not store.overrides_path.exists()
    assert not store.secrets_path.exists()
    assert not store.audit_path.exists()


def test_apply_saved_removes_legacy_budgets_and_keeps_valid_overrides(
    tmp_path,
) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)
    store.overrides_path.write_text(
        "agent:\n"
        "  session_timeout_seconds: 9999\n"
        "casework:\n"
        "  max_investigation_calls: 100\n"
        "  max_investigation_rounds: 40\n"
        "  max_investigation_tokens: 99999999\n"
        "  context_checkpoint_max_tokens: 16000\n",
        encoding="utf-8",
    )

    store.apply_saved(cfg)

    assert cfg.casework.context_checkpoint_max_tokens == 16_000
    assert cfg.agent.session_timeout_seconds == 1_440
    assert not hasattr(cfg.casework, "max_investigation_calls")
    assert not hasattr(cfg.casework, "max_investigation_rounds")
    assert not hasattr(cfg.casework, "max_investigation_tokens")
    saved = store._read_yaml(store.overrides_path)
    assert saved["agent"]["session_timeout_seconds"] == 1_440
    assert "max_investigation_calls" not in saved["casework"]
    assert "max_investigation_rounds" not in saved["casework"]
    assert "max_investigation_tokens" not in saved["casework"]
    audit = store.list_audit_logs()["items"]
    assert audit[0]["action"] == "config_override_migration"


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


def test_custom_mcp_api_keys_are_secret_and_can_be_preserved_on_edit(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)
    payload = {
        "changes": {
            "mcp_services.custom": [
                {
                    "name": "linear_mcp",
                    "label": "Linear MCP",
                    "mcp_url": "https://linear.example.test/mcp",
                    "mcp_api_key": "linear-secret",
                    "enabled": True,
                }
            ]
        },
        "reason": "add custom mcp",
    }

    result = store.apply(cfg, payload, actor="admin", ip="127.0.0.1")

    assert "linear-secret" not in json.dumps(result)
    assert "mcp_api_key" not in json.dumps(result)
    custom_effective = store.effective_config(cfg)["values"]["mcp_services.custom"]
    assert "linear-secret" not in json.dumps(custom_effective)
    assert "mcp_api_key" not in json.dumps(custom_effective)
    assert "linear-secret" not in store.overrides_path.read_text()
    assert "linear-secret" in store.secrets_path.read_text()
    assert "linear-secret" not in store.audit_path.read_text()
    service = store.effective_config(cfg)["values"]["mcp_services.custom"][0]
    assert service["api_key_configured"] is True

    store.apply(
        cfg,
        {
            "changes": {
                "mcp_services.custom": [
                    {
                        **service,
                        "label": "Linear Issues",
                        "mcp_api_key": "",
                    }
                ]
            },
            "reason": "rename custom mcp",
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert cfg.mcp_services.custom[0].label == "Linear Issues"
    assert cfg.mcp_services.custom[0].mcp_api_key == "linear-secret"
    restarted_cfg = AppConfig()
    store.apply_saved(restarted_cfg)
    assert restarted_cfg.mcp_services.custom[0].mcp_api_key == "linear-secret"


def test_apply_saved_migrates_legacy_custom_mcp_key_out_of_overrides(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)
    store.overrides_path.write_text(
        "mcp_services:\n"
        "  custom:\n"
        "    - name: linear_mcp\n"
        "      label: Linear MCP\n"
        "      mcp_url: https://linear.example.test/mcp\n"
        "      mcp_api_key: legacy-linear-secret\n"
        "      enabled: true\n",
        encoding="utf-8",
    )
    store.audit_path.write_text(
        json.dumps(
            {
                "id": "audit_legacy",
                "ts": 1,
                "actor": "admin",
                "ip": "127.0.0.1",
                "action": "config_apply",
                "reason": "legacy custom MCP save",
                "diff": [
                    {
                        "path": "mcp_services.custom",
                        "old": [],
                        "new": [
                            {
                                "name": "linear_mcp",
                                "mcp_api_key": "legacy-linear-secret",
                            }
                        ],
                    }
                ],
                "status": "success",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    store.apply_saved(cfg)

    assert cfg.mcp_services.custom[0].mcp_api_key == "legacy-linear-secret"
    assert "legacy-linear-secret" not in store.overrides_path.read_text()
    assert "mcp_api_key" not in store.overrides_path.read_text()
    assert "legacy-linear-secret" in store.secrets_path.read_text()
    assert store.secrets_path.stat().st_mode & 0o777 == 0o600
    assert "legacy-linear-secret" not in store.audit_path.read_text()
    assert "legacy custom MCP save" in store.audit_path.read_text()
    assert "api_key_configured" in store.audit_path.read_text()
    audit = store.list_audit_logs(action="custom_mcp_secret_migration")
    assert audit["summary"]["filtered"] == 1
    assert "legacy-linear-secret" not in store.audit_path.read_text()


def test_apply_saved_redacts_deleted_custom_mcp_key_from_legacy_audit(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)
    store.overrides_path.write_text("mcp_services:\n  custom: []\n", encoding="utf-8")
    store.audit_path.write_text(
        json.dumps(
            {
                "id": "audit_deleted_mcp",
                "action": "config_apply",
                "diff": [
                    {
                        "path": "mcp_services.custom",
                        "old": [{"mcp_api_key": "deleted-service-secret"}],
                        "new": [],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    store.apply_saved(cfg)

    audit_text = store.audit_path.read_text()
    assert "deleted-service-secret" not in audit_text
    assert "mcp_api_key" not in audit_text
    assert "api_key_configured" in audit_text


def test_password_login_cannot_be_disabled_until_sso_is_implemented(tmp_path) -> None:
    cfg = AppConfig()
    store = AdminConfigStore(tmp_path)

    with pytest.raises(ValueError, match="password_login_required"):
        store.apply(
            cfg,
            {
                "changes": {"admin_auth.password_login_enabled": False},
                "reason": "disable password login",
            },
            actor="admin",
            ip="127.0.0.1",
        )

    assert (
        store.effective_config(cfg)["values"]["admin_auth.password_login_enabled"]
        is True
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
