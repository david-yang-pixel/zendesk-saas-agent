from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from deploy import configure_runtime, migrate_legacy_model_config, prepare_runtime


def _source_environment(path: Path) -> None:
    values = {
        "OPENAI_API_KEY": "openai-secret",
        "OPENCONNECTOR_RUNTIME_TOKEN": "openconnector-secret",
        "ZENDESK_SUBDOMAIN": "example",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "zendesk-secret",
        "ZENDESK_MESSAGING_WEBHOOK_SECRET": "webhook-secret",
    }
    path.write_text("".join(f'{key}="{value}"\n' for key, value in values.items()))


def test_discover_tools_uses_cloudflare_safe_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: Any = None

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "tools": [
                            {
                                "name": "search_actions",
                                "description": "Search actions",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                }
            ).encode()

    def fake_urlopen(request: Any, *, timeout: int) -> Response:
        nonlocal captured_request
        captured_request = request
        assert timeout == 20
        return Response()

    monkeypatch.setattr(prepare_runtime.urllib.request, "urlopen", fake_urlopen)

    tools = prepare_runtime._discover_tools(  # pyright: ignore[reportPrivateUsage]
        prepare_runtime.READ_ONLY_SERVICES[0], "runtime-secret"
    )

    assert len(tools) == 1
    assert captured_request.get_header("User-agent") == "ZD-Agent-Deploy/1.0"


def test_ticket_events_runtime_settings_survive_redeploy(tmp_path, monkeypatch):
    source = tmp_path / "source.env"
    socket = tmp_path / "socket"
    socket.touch()
    _source_environment(source)
    for name in ("CASEWORK_INGRESS_IMAGE", "CASEWORK_PI_AGENT_IMAGE", "CASEWORK_ADMIN_IMAGE"):
        monkeypatch.setenv(name, "image@sha256:" + "a" * 64)
    monkeypatch.setattr(prepare_runtime, "_discover_tools", lambda *_: [])
    monkeypatch.setenv("ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET", "independent-secret")
    args: dict[str, Any] = dict(source_env=source, state_dir=tmp_path / "state", revision="a" * 40, docker_socket=socket)
    prepare_runtime.prepare(**args)
    monkeypatch.delenv("ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET")
    prepare_runtime.prepare(**args)
    runtime = prepare_runtime._parse_dotenv(tmp_path / "state/runtime.env")
    assert runtime["ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET"] == "independent-secret"
    assert "MENTION_POLLER_ENABLED" not in runtime
    assert runtime["ZENDESK_MESSAGING_WEBHOOK_SECRET"] == "webhook-secret"


def test_ticket_events_compose_and_ci_wiring():
    root = Path(__file__).resolve().parents[3]
    compose = (root / "deploy/compose.release.yaml").read_text()
    workflow = (root / ".github/workflows/docker-image.yml").read_text()
    assert "ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET: ${ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET:-}" in compose
    assert "MENTION_POLLER_ENABLED" not in compose
    assert "secrets.ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET" in workflow
    assert "vars.CASEWORK_REUSE_TOOL_CONFIG" in workflow
    assert "vars.CASEWORK_OPENCONNECTOR_MCP_URL" in workflow
    assert "CASEWORK_OPENCONNECTOR_MCP_URL: ${CASEWORK_OPENCONNECTOR_MCP_URL:-" in compose
    assert "ZENDESK_API_TOKEN: ${ZENDESK_API_TOKEN:?set ZENDESK_API_TOKEN}" in compose


def test_release_compose_runs_only_digest_pinned_image_code():
    root = Path(__file__).resolve().parents[3]
    compose = (root / "deploy/compose.release.yaml").read_text()
    workflow = (root / ".github/workflows/docker-image.yml").read_text()
    deploy = (root / "deploy/deploy-production.sh").read_text()
    rollback = (root / "deploy/rollback-production.sh").read_text()
    assert "image: ${CASEWORK_PI_AGENT_IMAGE:?" in compose
    assert compose.count("image: ${CASEWORK_ADMIN_IMAGE:?") == 3
    assert ":latest" not in compose
    # Admin must serve the code baked into its image, not host checkout files.
    assert "/opt/zd-agent/code/apps" not in compose
    for text in (compose, workflow, deploy, rollback):
        assert "HARNESS" not in text
        assert "SANDBOX" not in text
        assert "feishu-mcp" not in text
    assert "CASEWORK_PI_AGENT_IMAGE" in rollback


def test_openconnector_endpoint_switch_survives_redeploy(tmp_path, monkeypatch):
    source = tmp_path / "source.env"
    socket = tmp_path / "socket"
    socket.touch()
    _source_environment(source)
    for name in (
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_PI_AGENT_IMAGE",
        "CASEWORK_ADMIN_IMAGE",
    ):
        monkeypatch.setenv(name, "image@sha256:" + "a" * 64)

    endpoint = "https://openconnector.smlershou.top/mcp"
    monkeypatch.setenv("CASEWORK_OPENCONNECTOR_MCP_URL", endpoint)
    seen: list[str] = []

    def discover(service, _secret):
        seen.append(service.endpoint)
        return [
            {
                "name": "search_actions",
                "remote_tool_name": "search_actions",
                "description": "Search actions",
                "input_schema": {"type": "object"},
                "read_only": True,
                "endpoint_url": service.endpoint,
                "credential_ref": service.credential_ref,
            }
        ]

    monkeypatch.setattr(prepare_runtime, "_discover_tools", discover)
    args: dict[str, Any] = dict(
        source_env=source,
        state_dir=tmp_path / "state",
        revision="a" * 40,
        docker_socket=socket,
    )
    prepare_runtime.prepare(**args)
    monkeypatch.delenv("CASEWORK_OPENCONNECTOR_MCP_URL")
    prepare_runtime.prepare(**args)

    runtime = prepare_runtime._parse_dotenv(tmp_path / "state/runtime.env")
    assert runtime["CASEWORK_OPENCONNECTOR_MCP_URL"] == endpoint
    assert seen == [endpoint, endpoint]
    assert json.loads(runtime["CASEWORK_TOOL_CONFIG"])[0]["endpoint_url"] == endpoint


def test_openconnector_endpoint_rejects_credential_redirect(monkeypatch):
    monkeypatch.setenv("CASEWORK_OPENCONNECTOR_MCP_URL", "https://attacker.example/mcp")
    with pytest.raises(RuntimeError, match="approved MCP endpoint"):
        prepare_runtime._configured_services({})


def test_log_sync_is_opt_in_and_uses_private_credentials() -> None:
    root = Path(__file__).resolve().parents[3]
    compose = (root / "deploy/compose.release.yaml").read_text()
    deploy = (root / "deploy/deploy-production.sh").read_text()
    workflow = (root / ".github/workflows/docker-image.yml").read_text()
    assert "profiles: [log-sync]" in compose
    assert "/run/log-sync/credentials.json" in compose
    assert "CASEWORK_LOG_SYNC_ENABLED" in deploy
    assert "services+=(log-sync)" in deploy
    assert "vars.CASEWORK_LOG_SYNC_ENABLED" in workflow
    assert "vars.HC_CLIENT_URL" in workflow
    assert "secrets.HC_CLIENT_API_KEY" in workflow
    assert "Missing required log-sync input" in workflow


def test_saved_tools_validate_and_reuse_without_discovery(tmp_path, monkeypatch):
    source = tmp_path / "source.env"
    socket = tmp_path / "socket"
    socket.touch()
    _source_environment(source)
    for name in ("CASEWORK_INGRESS_IMAGE", "CASEWORK_PI_AGENT_IMAGE", "CASEWORK_ADMIN_IMAGE"):
        monkeypatch.setenv(name, "image@sha256:" + "a" * 64)
    service = prepare_runtime.READ_ONLY_SERVICES[0]
    tool = {"name": "search_actions", "remote_tool_name": "search_actions",
            "description": "Search", "input_schema": {}, "read_only": True,
            "endpoint_url": service.endpoint, "credential_ref": service.credential_ref}
    state = tmp_path / "state"
    state.mkdir()
    prepare_runtime._write_dotenv(state / "runtime.env", {"CASEWORK_TOOL_CONFIG": json.dumps([tool])})
    monkeypatch.setenv("CASEWORK_REUSE_TOOL_CONFIG", "true")
    def no_discovery(*_):
        raise AssertionError("must not discover")
    monkeypatch.setattr(prepare_runtime, "_discover_tools", no_discovery)
    prepare_runtime.prepare(source_env=source, state_dir=state, revision="a" * 40, docker_socket=socket)
    saved = prepare_runtime._parse_dotenv(state / "runtime.env")
    assert json.loads(saved["CASEWORK_TOOL_CONFIG"]) == [tool]
    for invalid in ([], [{**tool, "read_only": False}], [{**tool, "endpoint_url": "https://invalid"}]):
        with pytest.raises(RuntimeError):
            prepare_runtime._saved_tools({"CASEWORK_TOOL_CONFIG": json.dumps(invalid)})
    with pytest.raises(RuntimeError):
        prepare_runtime._saved_tools({})


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
        "CASEWORK_PI_AGENT_IMAGE",
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
    assert runtime["CASEWORK_OPENCONNECTOR_MCP_URL"].endswith("/mcp")
    assert runtime["ZENDESK_BASE_URL"] == "https://example.zendesk.com"
    assert runtime["CASEWORK_DELIVERY_MODE"] == "dry_run"
    assert "MENTION_POLLER_ENABLED" not in runtime
    assert runtime["ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET"] == ""
    assert runtime["DOCKER_GID"] == str(socket.stat().st_gid)
    assert runtime["CASEWORK_ADMIN_AUTH_DIR"] == str(state_dir / "admin-auth")
    assert runtime["CASEWORK_ADMIN_PUBLIC_ORIGIN"] == "https://zendesk.smlershou.top"
    assert runtime["CASEWORK_SAAS_GROUP_ID"] == "40685275477140"
    assert "CASEWORK_SAAS_ASSIGNEE" not in runtime
    assert len(tools) == len(prepare_runtime.READ_ONLY_SERVICES)
    for name in (
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_PI_AGENT_IMAGE",
        "CASEWORK_ADMIN_IMAGE",
    ):
        assert runtime[name] == f"ghcr.io/example/{name.lower()}@{digest}"
    assert "CASEWORK_HARNESS_IMAGE" not in runtime
    assert "CASEWORK_SANDBOX_IMAGE" not in runtime
    assert credentials["CASEWORK_CREDENTIAL_OPENAI_PRIMARY"] == "openai-secret"
    assert credentials["CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME"] == "openconnector-secret"
    assert "OPENAI_API_KEY" not in runtime
    assert os.stat(state_dir / "runtime.env").st_mode & 0o777 == 0o600
    assert (
        os.stat(state_dir / "credentials" / "credentials.env").st_mode & 0o777 == 0o600
    )
    assert os.stat(state_dir / "admin-auth" / "internal-token").st_mode & 0o777 == 0o600


def test_prepare_runtime_prefers_ci_openconnector_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_env = tmp_path / "source.env"
    state_dir = tmp_path / "state"
    socket = tmp_path / "docker.sock"
    socket.touch()
    _source_environment(source_env)
    monkeypatch.setenv("OPENCONNECTOR_RUNTIME_TOKEN", "ci-openconnector-secret")
    digest = "sha256:" + ("a" * 64)
    for name in (
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_PI_AGENT_IMAGE",
        "CASEWORK_ADMIN_IMAGE",
    ):
        monkeypatch.setenv(name, f"ghcr.io/example/{name.lower()}@{digest}")
    monkeypatch.setattr(prepare_runtime, "_discover_tools", lambda *_: [])

    prepare_runtime.prepare(
        source_env=source_env,
        state_dir=state_dir,
        revision="c" * 40,
        docker_socket=socket,
        legacy_auth_path=tmp_path / "missing-auth.json",
    )

    credentials = prepare_runtime._parse_dotenv(  # pyright: ignore[reportPrivateUsage]
        state_dir / "credentials" / "credentials.env"
    )
    assert (
        credentials["CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME"]
        == "ci-openconnector-secret"
    )


def test_prepare_runtime_isolates_log_sync_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_env = tmp_path / "source.env"
    state_dir = tmp_path / "state"
    socket = tmp_path / "docker.sock"
    socket.touch()
    _source_environment(source_env)
    source_env.write_text(
        source_env.read_text()
        + 'HC_CLIENT_URL="https://manager.example.com"\n'
        + 'HC_CLIENT_API_KEY="manager-secret"\n'
    )
    digest = "sha256:" + "a" * 64
    for name in (
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_PI_AGENT_IMAGE",
        "CASEWORK_ADMIN_IMAGE",
    ):
        monkeypatch.setenv(name, f"ghcr.io/example/{name.lower()}@{digest}")
    monkeypatch.setenv("CASEWORK_LOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("HC_CLIENT_URL", "https://manager.example.com")
    monkeypatch.setenv("HC_CLIENT_API_KEY", "manager-secret")
    monkeypatch.setattr(prepare_runtime, "_discover_tools", lambda *_: [])

    prepare_runtime.prepare(
        source_env=source_env,
        state_dir=state_dir,
        revision="d" * 40,
        docker_socket=socket,
        legacy_auth_path=tmp_path / "missing-auth.json",
    )

    runtime = prepare_runtime._parse_dotenv(state_dir / "runtime.env")  # pyright: ignore[reportPrivateUsage]
    credentials_file = state_dir / "log-sync" / "credentials.json"
    assert runtime["CASEWORK_LOG_SYNC_ENABLED"] == "true"
    assert "manager-secret" not in (state_dir / "runtime.env").read_text()
    assert "manager-secret" not in (state_dir / "credentials" / "credentials.env").read_text()
    assert json.loads(credentials_file.read_text()) == {
        "base_url": "https://manager.example.com",
        "api_key": "manager-secret",
    }
    assert os.stat(credentials_file).st_mode & 0o777 == 0o600


def test_prepare_runtime_does_not_resurrect_legacy_log_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_env = tmp_path / "source.env"
    state_dir = tmp_path / "state"
    socket = tmp_path / "docker.sock"
    socket.touch()
    _source_environment(source_env)
    source_env.write_text(
        source_env.read_text()
        + 'HC_CLIENT_URL="https://legacy.example.com"\n'
        + 'HC_CLIENT_API_KEY="legacy-key"\n'
    )
    digest = "sha256:" + "a" * 64
    for name in (
        "CASEWORK_INGRESS_IMAGE", "CASEWORK_PI_AGENT_IMAGE",
        "CASEWORK_ADMIN_IMAGE",
    ):
        monkeypatch.setenv(name, f"ghcr.io/example/{name.lower()}@{digest}")
    monkeypatch.setenv("CASEWORK_LOG_SYNC_ENABLED", "true")
    monkeypatch.delenv("HC_CLIENT_URL", raising=False)
    monkeypatch.delenv("HC_CLIENT_API_KEY", raising=False)
    monkeypatch.setattr(prepare_runtime, "_discover_tools", lambda *_: [])

    with pytest.raises(RuntimeError, match="Log sync requires"):
        prepare_runtime.prepare(
            source_env=source_env,
            state_dir=state_dir,
            revision="d" * 40,
            docker_socket=socket,
            legacy_auth_path=tmp_path / "missing-auth.json",
        )
    assert not (state_dir / "log-sync" / "credentials.json").exists()


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
        "CASEWORK_PI_AGENT_IMAGE",
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
        'CASEWORK_CREDENTIAL_ZENDESK_MCP="legacy-zendesk"\n'
        'CASEWORK_CREDENTIAL_FEISHU_MCP="legacy-feishu"\n'
    )
    digest = "sha256:" + ("a" * 64)
    for name in (
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_PI_AGENT_IMAGE",
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
    assert "CASEWORK_CREDENTIAL_ZENDESK_MCP" not in credentials
    assert "CASEWORK_CREDENTIAL_FEISHU_MCP" not in credentials


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
