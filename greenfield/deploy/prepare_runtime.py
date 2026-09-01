"""Prepare a least-privilege production environment from the legacy secret file.

The script never prints secret values.  It writes a dedicated runtime file and
credential file with mode 0600, and discovers the current read-only MCP tool
schemas so the Sandbox receives typed definitions rather than a hard-coded
copy that can drift from the deployed services.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import stat
import tempfile
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class McpService:
    name: str
    endpoint: str
    credential_ref: str
    secret_name: str
    excluded_tools: frozenset[str] = frozenset()


READ_ONLY_SERVICES = (
    McpService(
        "source",
        "https://zendesk.smlershou.top/source-mcp/mcp",
        "source-mcp",
        "SOURCE_MCP_API_KEY",
        frozenset({"refresh_repos"}),
    ),
    McpService(
        "github",
        "https://zendesk.smlershou.top/github-mcp/mcp",
        "github-mcp",
        "GITHUB_MCP_API_KEY",
    ),
    McpService(
        "signoz",
        "https://zendesk.smlershou.top/signoz-mcp/mcp",
        "signoz-mcp",
        "SIGNOZ_MCP_API_KEY",
    ),
    McpService(
        "docs",
        "https://zendesk.smlershou.top/docs-tool-service/mcp",
        "docs-mcp",
        "DOCS_TOOL_SERVICE_API_KEY",
    ),
    McpService(
        "zendesk",
        "https://zendesk.smlershou.top/zendesk-mcp/mcp",
        "zendesk-mcp",
        "ZENDESK_MCP_API_KEY",
        frozenset(
            {"add_internal_note", "update_ticket_status", "update_ticket_metadata"}
        ),
    ),
    McpService(
        "db-gateway",
        "https://zendesk.smlershou.top/db-gateway/mcp",
        "db-gateway",
        "DB_GATEWAY_API_KEY",
    ),
)


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
            else:
                if isinstance(decoded, str):
                    value = decoded.replace("$$", "$")
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[name] = value
    return values


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise RuntimeError(f"legacy environment is missing required {name}")
    return value


def _dotenv_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def _write_dotenv(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(
                "".join(
                    f"{name}={_dotenv_quote(value)}\n" for name, value in values.items()
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _json_response(body: str) -> dict[str, Any]:
    if body.lstrip().startswith("data:") or "\ndata:" in body:
        frames = [
            line[5:].strip() for line in body.splitlines() if line.startswith("data:")
        ]
        if not frames:
            raise RuntimeError("MCP returned an empty event stream")
        body = frames[-1]
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise RuntimeError("MCP returned a non-object response")
    return cast(dict[str, Any], payload)


def _discover_tools(service: McpService, secret: str) -> list[dict[str, object]]:
    request = urllib.request.Request(
        service.endpoint,
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        ).encode(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = _json_response(response.read().decode())
    raw_result = payload.get("result")
    if not isinstance(raw_result, dict):
        raise RuntimeError(f"{service.name} MCP did not return a tool list")
    result = cast(dict[str, object], raw_result)
    if not isinstance(result.get("tools"), list):
        raise RuntimeError(f"{service.name} MCP did not return a tool list")

    tools: list[dict[str, object]] = []
    for raw_tool in cast(list[object], result["tools"]):
        if not isinstance(raw_tool, dict):
            continue
        tool = cast(dict[str, object], raw_tool)
        name = tool.get("name")
        if not isinstance(name, str) or name in service.excluded_tools:
            continue
        description = tool.get("description")
        schema = tool.get("inputSchema")
        if not isinstance(description, str) or not isinstance(schema, dict):
            raise RuntimeError(f"{service.name} MCP returned an invalid tool contract")
        tools.append(
            {
                "name": name,
                "description": description,
                "input_schema": schema,
                "endpoint_url": service.endpoint,
                "timeout_seconds": 30,
                "read_only": True,
                "remote_tool_name": name,
                "credential_ref": service.credential_ref,
            }
        )
    if not tools:
        raise RuntimeError(f"{service.name} MCP exposed no allowed tools")
    return tools


def prepare(
    *,
    source_env: Path,
    state_dir: Path,
    revision: str,
    docker_socket: Path = Path("/var/run/docker.sock"),
) -> None:
    legacy = _parse_dotenv(source_env)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)

    existing_runtime_path = state_dir / "runtime.env"
    existing = (
        _parse_dotenv(existing_runtime_path) if existing_runtime_path.exists() else {}
    )
    postgres_password = existing.get("POSTGRES_PASSWORD") or secrets.token_hex(24)
    delivery_mode = existing.get("CASEWORK_DELIVERY_MODE", "dry_run")
    canary_ids = existing.get("CASEWORK_CANARY_TICKET_IDS", "")

    credential_dir = state_dir / "credentials"
    credential_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    credential_dir.chmod(0o700)
    credentials_path = credential_dir / "credentials.env"
    legacy_credentials_path = state_dir / "credentials.env"
    if os.geteuid() == 0:
        os.chown(credential_dir, 10_001, 10_001)
    all_tools: list[dict[str, object]] = []
    service_credentials: dict[str, str] = {}
    for service in READ_ONLY_SERVICES:
        secret = _required(legacy, service.secret_name)
        service_credentials[
            "CASEWORK_CREDENTIAL_" + service.credential_ref.replace("-", "_").upper()
        ] = secret
        all_tools.extend(_discover_tools(service, secret))

    lock_path = credential_dir / ".credentials.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        lock_path.chmod(0o600)
        if os.geteuid() == 0:
            os.chown(lock_path, 10_001, 10_001)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if credentials_path.exists():
            credentials = _parse_dotenv(credentials_path)
        elif legacy_credentials_path.exists():
            credentials = _parse_dotenv(legacy_credentials_path)
        else:
            credentials = {}
        # Admin replacements are authoritative and must survive later deploys.
        credentials.setdefault(
            "CASEWORK_CREDENTIAL_OPENAI_PRIMARY",
            _required(legacy, "OPENAI_API_KEY"),
        )
        credentials.update(service_credentials)
        _write_dotenv(credentials_path, credentials)
    if os.geteuid() == 0:
        os.chown(credentials_path, 10_001, 10_001)

    images = {
        name: _required(os.environ, name)
        for name in (
            "CASEWORK_INGRESS_IMAGE",
            "CASEWORK_HARNESS_IMAGE",
            "CASEWORK_SANDBOX_IMAGE",
            "CASEWORK_ADMIN_IMAGE",
        )
    }
    subdomain = _required(legacy, "ZENDESK_SUBDOMAIN")
    runtime = {
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": (
            f"postgresql+asyncpg://casework:{postgres_password}@postgres:5432/casework"
        ),
        "CASEWORK_CONFIG_REVISION": revision,
        **images,
        "ZENDESK_BASE_URL": f"https://{subdomain}.zendesk.com",
        "ZENDESK_EMAIL": _required(legacy, "ZENDESK_EMAIL"),
        "ZENDESK_API_TOKEN": _required(legacy, "ZENDESK_API_TOKEN"),
        "ZENDESK_MESSAGING_WEBHOOK_SECRET": _required(
            legacy, "ZENDESK_MESSAGING_WEBHOOK_SECRET"
        ),
        "CASEWORK_MODEL_BASE_URL": "https://api.openai.com",
        "CASEWORK_TOOL_CONFIG": json.dumps(
            all_tools, ensure_ascii=False, separators=(",", ":")
        ),
        "CASEWORK_CREDENTIAL_ENV_FILE": str(credentials_path),
        "CASEWORK_CREDENTIAL_DIR": str(credential_dir),
        "CASEWORK_DELIVERY_MODE": delivery_mode,
        "CASEWORK_CANARY_TICKET_IDS": canary_ids,
        "DOCKER_GID": str(docker_socket.stat().st_gid),
        "CASEWORK_INGRESS_PORT": "18101",
        "CASEWORK_HARNESS_PORT": "18102",
        "CASEWORK_ADMIN_PORT": "18104",
        "CASEWORK_SAAS_ASSIGNEE": "46493567999892",
        "CASEWORK_ROOT_MODEL": "gpt-5.5",
    }
    _write_dotenv(existing_runtime_path, runtime)
    (state_dir / "tool-names.json").write_text(
        json.dumps([tool["name"] for tool in all_tools], separators=(",", ":"))
    )
    (state_dir / "tool-names.json").chmod(0o600)
    print(f"Prepared Greenfield runtime with {len(all_tools)} read-only tools.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    prepare(
        source_env=args.source_env,
        state_dir=args.state_dir,
        revision=args.revision,
    )


if __name__ == "__main__":
    main()
