from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from deploy import configure_runtime, migrate_legacy_model_config, prepare_runtime

from casework_sandbox.composition import SandboxRuntimeConfig


def _source_environment(path: Path) -> None:
    values = {
        "OPENAI_API_KEY": "openai-secret",
        "SOURCE_MCP_API_KEY": "source-secret",
        "GITHUB_MCP_API_KEY": "github-secret",
        "SIGNOZ_MCP_API_KEY": "signoz-secret",
        "DOCS_TOOL_SERVICE_API_KEY": "docs-secret",
        "ZENDESK_MCP_API_KEY": "zendesk-mcp-secret",
        "DB_GATEWAY_API_KEY": "db-secret",
        "ZENDESK_SUBDOMAIN": "example",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "zendesk-secret",
        "ZENDESK_MESSAGING_WEBHOOK_SECRET": "webhook-secret",
    }
    path.write_text("".join(f'{key}="{value}"\n' for key, value in values.items()))


def test_prepare_runtime_writes_digest_pinned_isolated_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_env = tmp_path / "legacy.env"
    state_dir = tmp_path / "state"
    socket = tmp_path / "docker.sock"
    socket.touch()
    _source_environment(source_env)
    digest = "sha256:" + ("a" * 64)
    for name in (
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_HARNESS_IMAGE",
        "CASEWORK_SANDBOX_IMAGE",
        "CASEWORK_ADMIN_IMAGE",
    ):
        monkeypatch.setenv(name, f"ghcr.io/example/{name.lower()}@{digest}")

    def _discover(
        service: prepare_runtime.McpService, secret: str
    ) -> list[dict[str, object]]:
        assert secret.endswith("secret")
        return [
            {
                "name": f"{service.name}-read",
                "description": "Read evidence",
                "input_schema": {"type": "object"},
                "endpoint_url": service.endpoint,
                "read_only": True,
                "remote_tool_name": f"{service.name}-read",
                "credential_ref": service.credential_ref,
            }
        ]

    monkeypatch.setattr(prepare_runtime, "_discover_tools", _discover)
    prepare_runtime.prepare(
        source_env=source_env,
        state_dir=state_dir,
        revision="b" * 40,
        docker_socket=socket,
    )

    runtime = prepare_runtime._parse_dotenv(  # pyright: ignore[reportPrivateUsage]
        state_dir / "runtime.env"
    )
    credentials = prepare_runtime._parse_dotenv(  # pyright: ignore[reportPrivateUsage]
        state_dir / "credentials" / "credentials.env"
    )
    tools = json.loads(runtime["CASEWORK_TOOL_CONFIG"])
    assert runtime["CASEWORK_CONFIG_REVISION"] == "b" * 40
    assert runtime["ZENDESK_BASE_URL"] == "https://example.zendesk.com"
    assert runtime["CASEWORK_DELIVERY_MODE"] == "dry_run"
    assert runtime["DOCKER_GID"] == str(socket.stat().st_gid)
    assert runtime["CASEWORK_ADMIN_AUTH_DIR"] == str(state_dir / "admin-auth")
    assert runtime["CASEWORK_ADMIN_PUBLIC_ORIGIN"] == "https://zendesk.smlershou.top"
    assert runtime["CASEWORK_SAAS_GROUP_ID"] == "40685275477140"
    assert "CASEWORK_SAAS_ASSIGNEE" not in runtime
    assert len(tools) == len(prepare_runtime.READ_ONLY_SERVICES)
    parsed = SandboxRuntimeConfig.from_env(runtime)
    assert len(parsed.tool_configs) == len(prepare_runtime.READ_ONLY_SERVICES)
    assert credentials["CASEWORK_CREDENTIAL_OPENAI_PRIMARY"] == "openai-secret"
    assert "OPENAI_API_KEY" not in runtime
    assert os.stat(state_dir / "runtime.env").st_mode & 0o777 == 0o600
    assert (
        os.stat(state_dir / "credentials" / "credentials.env").st_mode & 0o777 == 0o600
    )
    assert os.stat(state_dir / "admin-auth" / "internal-token").st_mode & 0o777 == 0o600


def test_prepare_runtime_migrates_legacy_admin_password_hash_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_env = tmp_path / "legacy.env"
    state_dir = tmp_path / "state"
    socket = tmp_path / "docker.sock"
    legacy_auth = tmp_path / "admin_auth.json"
    socket.touch()
    _source_environment(source_env)
    legacy_auth.write_text(
        json.dumps(
            {
                "users": {
                    "admin": {
                        "password": {"algorithm": "pbkdf2_sha256"},
                        "force_password_change": False,
                    }
                }
            }
        )
    )
    digest = "sha256:" + ("a" * 64)
    for name in (
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_HARNESS_IMAGE",
        "CASEWORK_SANDBOX_IMAGE",
        "CASEWORK_ADMIN_IMAGE",
    ):
        monkeypatch.setenv(name, f"ghcr.io/example/{name.lower()}@{digest}")

    def _discover_empty(
        _service: prepare_runtime.McpService, _secret: str
    ) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(prepare_runtime, "_discover_tools", _discover_empty)

    prepare_runtime.prepare(
        source_env=source_env,
        state_dir=state_dir,
        revision="b" * 40,
        docker_socket=socket,
        legacy_auth_path=legacy_auth,
    )

    migrated = json.loads((state_dir / "admin-auth" / "admin_auth.json").read_text())
    assert migrated == json.loads(legacy_auth.read_text())


def test_prepare_runtime_preserves_admin_replaced_model_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_env = tmp_path / "legacy.env"
    state_dir = tmp_path / "state"
    credential_dir = state_dir / "credentials"
    credential_dir.mkdir(parents=True)
    socket = tmp_path / "docker.sock"
    socket.touch()
    _source_environment(source_env)
    (credential_dir / "credentials.env").write_text(
        'CASEWORK_CREDENTIAL_OPENAI_PRIMARY="admin-replaced"\n'
    )
    digest = "sha256:" + ("a" * 64)
    for name in (
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_HARNESS_IMAGE",
        "CASEWORK_SANDBOX_IMAGE",
        "CASEWORK_ADMIN_IMAGE",
    ):
        monkeypatch.setenv(name, f"ghcr.io/example/{name.lower()}@{digest}")

    def _discover(
        _service: prepare_runtime.McpService, _secret: str
    ) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(prepare_runtime, "_discover_tools", _discover)

    prepare_runtime.prepare(
        source_env=source_env,
        state_dir=state_dir,
        revision="b" * 40,
        docker_socket=socket,
    )

    credentials = prepare_runtime._parse_dotenv(  # pyright: ignore[reportPrivateUsage]
        credential_dir / "credentials.env"
    )
    assert credentials["CASEWORK_CREDENTIAL_OPENAI_PRIMARY"] == "admin-replaced"


def test_configure_runtime_sets_rules_models_skills_and_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_names = tmp_path / "tool-names.json"
    tool_names.write_text('["get_ticket","source_search"]')
    calls: list[tuple[str, str, object | None]] = []

    def _request(
        url: str,
        *,
        internal_token: str,
        method: str = "GET",
        payload: object | None = None,
    ) -> dict[str, Any]:
        assert internal_token == "internal-token"
        calls.append((url, method, payload))
        if method == "GET":
            return {
                "revision": "revision-1",
                "config": {"subagent_slots": []},
            }
        return {"revision": "revision-2"}

    monkeypatch.setattr(configure_runtime, "_request", _request)
    revision = configure_runtime.configure(
        base_url="http://127.0.0.1:18104",
        activate=True,
        tool_names_path=tool_names,
        internal_token="internal-token",
    )

    assert revision == "revision-2"
    assert len(calls) == 2
    payload = calls[1][2]
    assert isinstance(payload, dict)
    config = cast(dict[str, Any], payload["config"])
    assert isinstance(config, dict)
    assert config["desired_state"] == "running"
    assert config["root_model"]["credential_ref"] == "openai-primary"
    assert config["root_model"]["base_url"] == "https://api.openai.com"
    assert config["subagent_slots"] == []
    assert config["rules"]["statuses"] == ["new", "open"]
    assert config["rules"]["saas_assignee"] == "40685275477140"
    assert config["rules"]["reply_enabled"] is True
    assert config["enabled_skills"] == ["core-casework", "app-run-investigation"]
    assert config["enabled_tools"] == ["get_ticket", "source_search"]


def test_configure_runtime_preserves_admin_model_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_names = tmp_path / "tool-names.json"
    tool_names.write_text("[]")
    configured_root = {
        "provider": "openai",
        "base_url": "https://api.tokener.dev",
        "model": "gpt-5.6-terra",
        "credential_ref": "legacy-root",
    }
    configured_children = [
        {
            "slot_id": "subagent-1",
            "enabled": True,
            "provider": "openai",
            "base_url": "https://api.tokener.dev",
            "model": "gpt-5.6-luna",
            "credential_ref": "legacy-child",
            "context_window_tokens": 32_000,
            "max_tool_rounds": 12,
            "max_parallel": 1,
        }
    ]
    saved: dict[str, object] = {}

    def _request(
        _url: str,
        *,
        internal_token: str,
        method: str = "GET",
        payload: object | None = None,
    ) -> dict[str, Any]:
        assert internal_token == "internal-token"
        if method == "GET":
            return {
                "revision": "revision-1",
                "config": {
                    "root_model": configured_root,
                    "subagent_slots": configured_children,
                },
            }
        assert isinstance(payload, dict)
        saved.update(cast(dict[str, object], payload))
        return {"revision": "revision-2"}

    monkeypatch.setattr(configure_runtime, "_request", _request)

    configure_runtime.configure(
        base_url="http://127.0.0.1:18104",
        activate=False,
        tool_names_path=tool_names,
        internal_token="internal-token",
    )

    config = cast(dict[str, Any], saved["config"])
    assert config["root_model"] == configured_root
    assert config["subagent_slots"] == configured_children
    assert config["desired_state"] == "paused"


def test_configure_runtime_preserves_running_state_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_names = tmp_path / "tool-names.json"
    tool_names.write_text("[]")
    saved: dict[str, object] = {}

    def _request(
        _url: str,
        *,
        internal_token: str,
        method: str = "GET",
        payload: object | None = None,
    ) -> dict[str, Any]:
        assert internal_token == "internal-token"
        if method == "GET":
            return {
                "revision": "revision-1",
                "config": {
                    "desired_state": "running",
                    "rules": {
                        # Existing production revisions used the AI Agent user
                        # ID here. Deployment must migrate it to the SaaS group.
                        "saas_assignee": "46493567999892",
                        "reply_enabled": True,
                    },
                    "subagent_slots": [],
                },
            }
        assert isinstance(payload, dict)
        saved.update(cast(dict[str, object], payload))
        return {"revision": "revision-2"}

    monkeypatch.setattr(configure_runtime, "_request", _request)

    configure_runtime.configure(
        base_url="http://127.0.0.1:18104",
        activate=None,
        tool_names_path=tool_names,
        internal_token="internal-token",
    )

    config = cast(dict[str, Any], saved["config"])
    assert config["desired_state"] == "running"
    assert config["rules"]["reply_enabled"] is True
    assert config["rules"]["saas_assignee"] == "40685275477140"


def test_legacy_model_migration_imports_root_and_disables_keyless_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_state = tmp_path / "legacy"
    legacy_state.mkdir()
    (legacy_state / "admin_overrides.yaml").write_text("llm: {}\n")
    marker = tmp_path / "state" / "legacy-model-migration.json"
    legacy_secret = "legacy-admin-secret"
    legacy = {
        "root": {
            "provider": "openai",
            "base_url": "https://api.tokener.dev/v1",
            "model": "gpt-5.6-terra",
            "api_key": legacy_secret,
        },
        "slots": [
            {
                "slot_id": "subagent-1",
                "enabled": True,
                "provider": "openai",
                "base_url": "https://api.tokener.dev/v1/",
                "model": "gpt-5.6-luna",
                "api_key": "",
            }
        ],
    }
    saved_configs: list[dict[str, Any]] = []
    credential_updates: list[dict[str, Any]] = []

    def _export_legacy(_container: str) -> dict[str, Any]:
        return legacy

    monkeypatch.setattr(migrate_legacy_model_config, "_export_legacy", _export_legacy)

    def _request(
        url: str,
        *,
        internal_token: str,
        method: str = "GET",
        payload: object | None = None,
    ) -> dict[str, Any]:
        assert internal_token == "internal-token"
        if url.endswith("/api/config") and method == "GET":
            return {
                "revision": "revision-1",
                "config": {
                    "root_model": {
                        "provider": "openai",
                        "base_url": "https://api.openai.com",
                        "model": "gpt-5.5",
                        "credential_ref": "openai-primary",
                    },
                    "subagent_slots": [],
                },
            }
        assert isinstance(payload, dict)
        typed_payload = cast(dict[str, Any], payload)
        if url.endswith("/api/model-catalog"):
            assert typed_payload["secret"] == legacy_secret
            return {"models": ["gpt-5.6-terra"]}
        if url.endswith("/api/config"):
            saved_configs.append(cast(dict[str, Any], typed_payload["config"]))
            return {"revision": "revision-2"}
        if url.endswith("/api/model-credentials"):
            credential_updates.append(typed_payload)
            return {"credential": {"configured": True}}
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(migrate_legacy_model_config, "_request", _request)

    status = migrate_legacy_model_config.migrate(
        base_url="http://127.0.0.1:18104",
        internal_token="internal-token",
        marker_path=marker,
        legacy_state_dir=legacy_state,
        legacy_container="legacy-agent",
    )

    assert status == "applied"
    assert saved_configs[0]["root_model"] == {
        "provider": "openai",
        "base_url": "https://api.tokener.dev/v1",
        "model": "gpt-5.6-terra",
        "credential_ref": "openai-primary",
    }
    assert saved_configs[0]["subagent_slots"][0] == {
        "slot_id": "subagent-1",
        "enabled": False,
        "provider": "openai",
        "base_url": "https://api.tokener.dev/v1/",
        "model": "gpt-5.6-luna",
        "credential_ref": "legacy-subagent-subagent-1",
        "context_window_tokens": 32_000,
        "max_tool_rounds": 12,
        "max_parallel": 1,
    }
    assert credential_updates == [
        {"credential_ref": "openai-primary", "secret": legacy_secret}
    ]
    marker_text = marker.read_text()
    assert legacy_secret not in marker_text
    assert json.loads(marker_text)["subagent_slots"] == [
        {"slot_id": "subagent-1", "enabled": False}
    ]


def test_legacy_model_migration_does_not_overwrite_new_admin_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_state = tmp_path / "legacy"
    legacy_state.mkdir()
    (legacy_state / "admin_overrides.yaml").write_text("llm: {}\n")
    marker = tmp_path / "state" / "legacy-model-migration.json"

    def _export_legacy(_container: str) -> dict[str, Any]:
        return {"root": {}, "slots": []}

    monkeypatch.setattr(migrate_legacy_model_config, "_export_legacy", _export_legacy)

    def _request(
        _url: str,
        *,
        internal_token: str,
        method: str = "GET",
        payload: object | None = None,
    ) -> dict[str, Any]:
        assert internal_token == "internal-token"
        assert method == "GET"
        assert payload is None
        return {
            "revision": "revision-1",
            "config": {
                "root_model": {
                    "provider": "openai",
                    "base_url": "https://models.example.com",
                    "model": "operator-selected",
                    "credential_ref": "operator-secret",
                },
                "subagent_slots": [],
            },
        }

    monkeypatch.setattr(migrate_legacy_model_config, "_request", _request)

    status = migrate_legacy_model_config.migrate(
        base_url="http://127.0.0.1:18104",
        internal_token="internal-token",
        marker_path=marker,
        legacy_state_dir=legacy_state,
        legacy_container="legacy-agent",
    )

    assert status == "skipped-admin-configured"
    assert json.loads(marker.read_text())["status"] == "skipped-admin-configured"
