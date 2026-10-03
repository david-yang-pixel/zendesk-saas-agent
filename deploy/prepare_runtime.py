"""Prepare a least-privilege production environment from the legacy secret file.

The script never prints secret values.  It writes a dedicated runtime file and
credential file with mode 0600, and discovers the current read-only MCP tool
schemas so the Admin configuration lists the deployed tools rather than a
hard-coded copy that can drift from the deployed services.
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
from dataclasses import dataclass, replace
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
        "openconnector",
        "https://open-connector.yyg066340.workers.dev/mcp",
        "openconnector-runtime",
        "OPENCONNECTOR_RUNTIME_TOKEN",
        frozenset({"list_apps"}),
    ),
)

ALLOWED_OPENCONNECTOR_MCP_URLS = frozenset(
    {
        READ_ONLY_SERVICES[0].endpoint,
        "https://openconnector.smlershou.top/mcp",
    }
)


def _configured_services(existing: Mapping[str, str]) -> tuple[McpService, ...]:
    endpoint = (
        os.environ.get("CASEWORK_OPENCONNECTOR_MCP_URL", "").strip()
        or existing.get("CASEWORK_OPENCONNECTOR_MCP_URL", "").strip()
        or READ_ONLY_SERVICES[0].endpoint
    )
    # Tool discovery sends the runtime token to this URL. Do not allow an
    # arbitrary deployment variable to redirect that credential elsewhere.
    if endpoint not in ALLOWED_OPENCONNECTOR_MCP_URLS:
        raise RuntimeError("CASEWORK_OPENCONNECTOR_MCP_URL is not an approved MCP endpoint")
    return (replace(READ_ONLY_SERVICES[0], endpoint=endpoint),)


LEGACY_INVESTIGATION_CREDENTIAL_KEYS = frozenset(
    {
        "CASEWORK_CREDENTIAL_SOURCE_MCP",
        "CASEWORK_CREDENTIAL_GITHUB_MCP",
        "CASEWORK_CREDENTIAL_SIGNOZ_MCP",
        "CASEWORK_CREDENTIAL_DOCS_MCP",
        "CASEWORK_CREDENTIAL_ZENDESK_MCP",
        "CASEWORK_CREDENTIAL_DB_GATEWAY",
        "CASEWORK_CREDENTIAL_FEISHU_MCP",
    }
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


def _write_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _prepare_admin_auth(*, state_dir: Path, legacy_auth_path: Path) -> Path:
    auth_dir = state_dir / "admin-auth"
    auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth_dir.chmod(0o700)
    auth_path = auth_dir / "admin_auth.json"
    if not auth_path.exists() and legacy_auth_path.is_file():
        raw = legacy_auth_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("users"), dict):
            raise RuntimeError("legacy admin authentication file is invalid")
        _write_private_text(auth_path, raw)

    token_path = auth_dir / "internal-token"
    if not token_path.exists():
        _write_private_text(token_path, secrets.token_urlsafe(48) + "\n")
    if len(token_path.read_text(encoding="utf-8").strip()) < 32:
        raise RuntimeError("admin internal token is invalid")

    for path in (auth_dir, auth_path, token_path):
        if path.exists():
            path.chmod(0o700 if path.is_dir() else 0o600)
            if os.geteuid() == 0:
                os.chown(path, 10_001, 10_001)
    return auth_dir


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
            # Cloudflare rejects Python urllib's default browser signature.
            "User-Agent": "ZD-Agent-Deploy/1.0",
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


def _saved_tools(
    existing: Mapping[str, str], services: tuple[McpService, ...] = READ_ONLY_SERVICES
) -> list[dict[str, object]]:
    """Explicit offline deploy using only previously saved, allowed contracts."""
    try:
        tools = json.loads(existing.get("CASEWORK_TOOL_CONFIG", ""))
    except (ValueError, TypeError) as error:
        raise RuntimeError("No valid saved tool configuration") from error
    if not isinstance(tools, list) or not tools:
        raise RuntimeError("No valid saved tool configuration")
    allowed = {(s.endpoint, s.credential_ref): s for s in services}
    for tool in tools:
        if not isinstance(tool, dict):
            raise RuntimeError("Invalid saved tool contract")
        endpoint_url = tool.get("endpoint_url")
        credential_ref = tool.get("credential_ref")
        service = (
            allowed.get((endpoint_url, credential_ref))
            if isinstance(endpoint_url, str) and isinstance(credential_ref, str)
            else None
        )
        if (
            service is None or tool.get("read_only") is not True
            or not isinstance(tool.get("name"), str) or not tool["name"]
            or not isinstance(tool.get("input_schema"), dict)
            or not isinstance(tool.get("description"), str)
            or not isinstance(tool.get("remote_tool_name"), str)
            or tool["remote_tool_name"] in service.excluded_tools
        ):
            raise RuntimeError("Invalid saved tool contract")
    return tools


def prepare(
    *,
    source_env: Path,
    state_dir: Path,
    revision: str,
    legacy_auth_path: Path = Path("/opt/zd-agent/resources/.state/admin_auth.json"),
) -> None:
    legacy = _parse_dotenv(source_env)
    # GitHub Actions injects this production secret into the remote deploy
    # process. Prefer it over the host dotenv so rotations have one authority.
    openconnector_runtime_token = os.environ.get(
        "OPENCONNECTOR_RUNTIME_TOKEN", ""
    ).strip()
    if openconnector_runtime_token:
        legacy["OPENCONNECTOR_RUNTIME_TOKEN"] = openconnector_runtime_token
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)

    existing_runtime_path = state_dir / "runtime.env"
    existing = (
        _parse_dotenv(existing_runtime_path) if existing_runtime_path.exists() else {}
    )
    services = _configured_services(existing)
    postgres_password = existing.get("POSTGRES_PASSWORD") or secrets.token_hex(24)
    # Optional during staged rollout. Never reuse the Messaging signing key.
    ticket_events_secret = (
        os.environ.get("ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET", "").strip()
        or legacy.get("ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET", "")
        or existing.get("ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET", "")
    )
    reuse_tools = os.environ.get("CASEWORK_REUSE_TOOL_CONFIG", "false").strip().lower()
    if reuse_tools not in {"true", "false"}:
        raise RuntimeError("CASEWORK_REUSE_TOOL_CONFIG must be true or false")

    credential_dir = state_dir / "credentials"
    credential_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    credential_dir.chmod(0o700)
    credentials_path = credential_dir / "credentials.env"
    legacy_credentials_path = state_dir / "credentials.env"
    if os.geteuid() == 0:
        os.chown(credential_dir, 10_001, 10_001)
    all_tools: list[dict[str, object]] = []
    if reuse_tools == "true":
        all_tools = _saved_tools(existing, services)
    service_credentials: dict[str, str] = {}
    for service in services:
        secret = _required(legacy, service.secret_name)
        service_credentials[
            "CASEWORK_CREDENTIAL_" + service.credential_ref.replace("-", "_").upper()
        ] = secret
        if reuse_tools != "true":
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
        for legacy_key in LEGACY_INVESTIGATION_CREDENTIAL_KEYS:
            credentials.pop(legacy_key, None)
        credentials.update(service_credentials)
        _write_dotenv(credentials_path, credentials)
    if os.geteuid() == 0:
        os.chown(credentials_path, 10_001, 10_001)

    # GitHub production environment is the sole authority for log export.
    # Never resurrect an old server-side setting during a redeploy.
    log_sync_enabled = os.environ.get("CASEWORK_LOG_SYNC_ENABLED", "false").strip().lower()
    if log_sync_enabled not in {"true", "false"}:
        raise RuntimeError("CASEWORK_LOG_SYNC_ENABLED must be true or false")
    log_sync_dir = state_dir / "log-sync"
    log_sync_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_sync_dir.chmod(0o700)
    log_sync_url = os.environ.get("HC_CLIENT_URL", "").strip()
    log_sync_key = os.environ.get("HC_CLIENT_API_KEY", "").strip()
    if log_sync_enabled == "true" and (not log_sync_url or not log_sync_key):
        raise RuntimeError("Log sync requires HC_CLIENT_URL and HC_CLIENT_API_KEY")
    if log_sync_enabled == "true":
        _write_private_text(
            log_sync_dir / "credentials.json",
            json.dumps({"base_url": log_sync_url, "api_key": log_sync_key}),
        )
    if os.geteuid() == 0:
        os.chown(log_sync_dir, 10_001, 10_001)
        if (log_sync_dir / "credentials.json").exists():
            os.chown(log_sync_dir / "credentials.json", 10_001, 10_001)

    admin_auth_dir = _prepare_admin_auth(
        state_dir=state_dir, legacy_auth_path=legacy_auth_path
    )

    images = {
        name: _required(os.environ, name)
        for name in (
            "CASEWORK_INGRESS_IMAGE",
            "CASEWORK_PI_AGENT_IMAGE",
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
        "CASEWORK_LOG_SYNC_ENABLED": log_sync_enabled,
        "CASEWORK_LOG_SYNC_CREDENTIAL_DIR": str(log_sync_dir),
        **images,
        "ZENDESK_BASE_URL": f"https://{subdomain}.zendesk.com",
        "ZENDESK_EMAIL": _required(legacy, "ZENDESK_EMAIL"),
        "ZENDESK_API_TOKEN": _required(legacy, "ZENDESK_API_TOKEN"),
        "ZENDESK_MESSAGING_WEBHOOK_SECRET": _required(
            legacy, "ZENDESK_MESSAGING_WEBHOOK_SECRET"
        ),
        "ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET": ticket_events_secret,
        "CASEWORK_MODEL_BASE_URL": "https://api.openai.com",
        "CASEWORK_OPENCONNECTOR_MCP_URL": services[0].endpoint,
        "CASEWORK_TOOL_CONFIG": json.dumps(
            all_tools, ensure_ascii=False, separators=(",", ":")
        ),
        "CASEWORK_CREDENTIAL_DIR": str(credential_dir),
        "CASEWORK_ADMIN_AUTH_DIR": str(admin_auth_dir),
        "CASEWORK_ADMIN_AUTH_FILE": "/run/casework-admin-auth/admin_auth.json",
        "CASEWORK_ADMIN_INTERNAL_TOKEN_FILE": (
            "/run/casework-admin-auth/internal-token"
        ),
        "CASEWORK_ADMIN_PUBLIC_ORIGIN": "https://zendesk.smlershou.top",
        "CASEWORK_BROWSER_RELAY_URL": (
            existing.get("CASEWORK_BROWSER_RELAY_URL")
            or "https://zendesk.smlershou.top/zendesk-browser-relay"
        ),
        "CASEWORK_BROWSER_RELAY_API_KEY": (
            existing.get("CASEWORK_BROWSER_RELAY_API_KEY")
            or legacy.get("ZENDESK_BROWSER_RELAY_API_KEY")
            or legacy.get("RELAY_API_KEY")
            or ""
        ),
        # The bot URL is a bearer credential. A CI secret rotates it; otherwise
        # keep the value already deployed. Alerts are disabled when it is unset.
        "FEISHU_WEBHOOK_URL": (
            os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
            or existing.get("FEISHU_WEBHOOK_URL")
            or legacy.get("FEISHU_WEBHOOK_URL")
            or ""
        ),
        "CASEWORK_INGRESS_PORT": "18101",
        "CASEWORK_PI_AGENT_PORT": "18102",
        "CASEWORK_ADMIN_PORT": "18104",
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
    parser.add_argument(
        "--legacy-auth-path",
        type=Path,
        default=Path("/opt/zd-agent/resources/.state/admin_auth.json"),
    )
    args = parser.parse_args()
    prepare(
        source_env=args.source_env,
        state_dir=args.state_dir,
        revision=args.revision,
        legacy_auth_path=args.legacy_auth_path,
    )


if __name__ == "__main__":
    main()
