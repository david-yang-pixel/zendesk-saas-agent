from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from deploy import configure_runtime, prepare_runtime

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
        state_dir / "credentials.env"
    )
    tools = json.loads(runtime["CASEWORK_TOOL_CONFIG"])
    assert runtime["CASEWORK_CONFIG_REVISION"] == "b" * 40
    assert runtime["ZENDESK_BASE_URL"] == "https://example.zendesk.com"
    assert runtime["CASEWORK_DELIVERY_MODE"] == "dry_run"
    assert runtime["DOCKER_GID"] == str(socket.stat().st_gid)
    assert len(tools) == len(prepare_runtime.READ_ONLY_SERVICES)
    parsed = SandboxRuntimeConfig.from_env(runtime)
    assert len(parsed.tool_configs) == len(prepare_runtime.READ_ONLY_SERVICES)
    assert credentials["CASEWORK_CREDENTIAL_OPENAI_PRIMARY"] == "openai-secret"
    assert "OPENAI_API_KEY" not in runtime
    assert os.stat(state_dir / "runtime.env").st_mode & 0o777 == 0o600
    assert os.stat(state_dir / "credentials.env").st_mode & 0o777 == 0o600


def test_configure_runtime_sets_rules_models_skills_and_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_names = tmp_path / "tool-names.json"
    tool_names.write_text('["get_ticket","source_search"]')
    calls: list[tuple[str, str, object | None]] = []

    def _request(
        url: str, *, method: str = "GET", payload: object | None = None
    ) -> dict[str, Any]:
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
    )

    assert revision == "revision-2"
    assert len(calls) == 2
    payload = calls[1][2]
    assert isinstance(payload, dict)
    config = cast(dict[str, Any], payload["config"])
    assert isinstance(config, dict)
    assert config["desired_state"] == "running"
    assert config["root_model"]["credential_ref"] == "openai-primary"
    assert config["subagent_slots"] == []
    assert config["rules"]["statuses"] == ["new", "open"]
    assert config["rules"]["reply_enabled"] is True
    assert config["enabled_skills"] == ["core-casework", "app-run-investigation"]
    assert config["enabled_tools"] == ["get_ticket", "source_search"]
