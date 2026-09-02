"""Import legacy Admin model bindings into Greenfield exactly once.

The legacy YAML is intentionally decoded inside the legacy container, where its
existing PyYAML runtime is available. Secrets travel only through a captured
pipe and the authenticated loopback Admin API; they are never written to the
migration marker or printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

_BOOTSTRAP_ROOT = {
    "provider": "openai",
    "base_url": "https://api.openai.com",
    "model": "gpt-5.5",
    "credential_ref": "openai-primary",
}
_SAFE_SLOT_ID = re.compile(r"[^A-Za-z0-9_.-]+")

_LEGACY_EXPORT_SCRIPT = r"""
import json
import os
from pathlib import Path

import yaml

state = Path("/opt/zd-agent/resources/.state")

def load(name):
    path = state / name
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}

overrides = load("admin_overrides.yaml")
secrets = load("admin_secrets.yaml")
llm = overrides.get("llm") if isinstance(overrides.get("llm"), dict) else {}
openai = secrets.get("openai") if isinstance(secrets.get("openai"), dict) else {}
subagents = (
    overrides.get("subagents")
    if isinstance(overrides.get("subagents"), dict)
    else {}
)
subagent_secrets = (
    secrets.get("subagents")
    if isinstance(secrets.get("subagents"), dict)
    else {}
)
slot_keys = (
    subagent_secrets.get("slot_api_keys")
    if isinstance(subagent_secrets.get("slot_api_keys"), dict)
    else {}
)
slots = []
for raw in subagents.get("slots", []):
    if not isinstance(raw, dict):
        continue
    child_llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    slot_id = str(raw.get("id") or "").strip()
    if not slot_id:
        continue
    slots.append(
        {
            "slot_id": slot_id,
            "enabled": bool(
                subagents.get("enabled", True) and raw.get("enabled", True)
            ),
            "provider": str(child_llm.get("provider") or "openai"),
            "base_url": str(child_llm.get("base_url") or "https://api.openai.com"),
            "model": str(child_llm.get("model") or "").strip(),
            "api_key": str(slot_keys.get(slot_id) or child_llm.get("api_key") or ""),
        }
    )

print(
    json.dumps(
        {
            "root": {
                "provider": str(llm.get("provider") or "openai"),
                "base_url": str(
                    llm.get("base_url")
                    or os.environ.get("OPENAI_BASE_URL")
                    or "https://api.openai.com"
                ),
                "model": str(
                    llm.get("model") or os.environ.get("OPENAI_MODEL") or ""
                ).strip(),
                "api_key": str(
                    openai.get("api_key")
                    or os.environ.get("OPENAI_API_KEY")
                    or ""
                ),
            },
            "slots": slots,
        },
        separators=(",", ":"),
    )
)
"""


def _request(
    url: str,
    *,
    internal_token: str,
    method: str = "GET",
    payload: object | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {internal_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result: object = json.loads(response.read())
    if not isinstance(result, dict):
        raise RuntimeError("Admin API returned a non-object response")
    return cast(dict[str, Any], result)


def _export_legacy(container: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "/app/.venv/bin/python",
            "-c",
            _LEGACY_EXPORT_SCRIPT,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("Legacy model configuration export failed")
    try:
        payload: object = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Legacy model configuration export was invalid") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Legacy model configuration export was invalid")
    return cast(dict[str, Any], payload)


def _is_bootstrap_config(config: dict[str, Any]) -> bool:
    root = config.get("root_model")
    children = config.get("subagent_slots", [])
    if children not in (None, []):
        return False
    if not isinstance(root, dict):
        return True
    root_values = cast(dict[str, object], root)
    if any(
        root_values.get(field) in (None, "", "unconfigured")
        for field in ("provider", "model", "credential_ref")
    ):
        return True
    return all(root_values.get(key) == value for key, value in _BOOTSTRAP_ROOT.items())


def _credential_ref(slot_id: str) -> str:
    normalized = _SAFE_SLOT_ID.sub("-", slot_id).strip("-.")
    if not normalized:
        raise RuntimeError("Legacy subagent slot id cannot form a credential reference")
    return f"legacy-subagent-{normalized}"


def _migration_config(
    current: dict[str, Any], legacy: dict[str, Any]
) -> tuple[dict[str, Any], list[tuple[str, str, dict[str, str]]]]:
    root = legacy.get("root")
    raw_slots = legacy.get("slots", [])
    if not isinstance(root, dict) or not isinstance(raw_slots, list):
        raise RuntimeError("Legacy model configuration is incomplete")
    root_values = cast(dict[str, object], root)
    slots = cast(list[object], raw_slots)
    root_model = {
        "provider": str(root_values.get("provider") or "openai"),
        "base_url": str(root_values.get("base_url") or ""),
        "model": str(root_values.get("model") or ""),
        "credential_ref": "openai-primary",
    }
    root_key = str(root_values.get("api_key") or "")
    if not root_model["base_url"] or not root_model["model"] or not root_key:
        raise RuntimeError("Legacy root model, Base URL, or Admin API key is missing")

    credentials: list[tuple[str, str, dict[str, str]]] = [
        ("openai-primary", root_key, root_model)
    ]
    children: list[dict[str, object]] = []
    for raw_slot in slots:
        if not isinstance(raw_slot, dict):
            continue
        slot = cast(dict[str, object], raw_slot)
        slot_id = str(slot.get("slot_id") or "").strip()
        model = str(slot.get("model") or "").strip()
        base_url = str(slot.get("base_url") or "").strip()
        if not slot_id or not model or not base_url:
            continue
        api_key = str(slot.get("api_key") or "")
        reference = _credential_ref(slot_id)
        binding = {
            "provider": str(slot.get("provider") or "openai"),
            "base_url": base_url,
            "model": model,
            "credential_ref": reference,
        }
        children.append(
            {
                "slot_id": slot_id,
                # Legacy had no independent child secret in some deployments.
                # Keep its model visible, but do not silently inherit root access.
                "enabled": bool(slot.get("enabled", True) and api_key),
                **binding,
                "context_window_tokens": 32_000,
                "max_tool_rounds": 12,
                "max_parallel": 1,
            }
        )
        if api_key:
            credentials.append((reference, api_key, binding))

    migrated = dict(current)
    migrated["root_model"] = root_model
    migrated["subagent_slots"] = children
    return migrated, credentials


def _write_marker(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def migrate(
    *,
    base_url: str,
    internal_token: str,
    marker_path: Path,
    legacy_state_dir: Path,
    legacy_container: str,
) -> str:
    if marker_path.exists():
        return "already-applied"
    if not (legacy_state_dir / "admin_overrides.yaml").exists():
        return "no-legacy-config"

    legacy = _export_legacy(legacy_container)
    api = base_url.rstrip("/")
    snapshot = _request(f"{api}/api/config", internal_token=internal_token)
    revision = snapshot.get("revision")
    raw_current = snapshot.get("config")
    if not isinstance(revision, str) or not isinstance(raw_current, dict):
        raise RuntimeError("Admin API returned an invalid configuration snapshot")
    current = cast(dict[str, Any], raw_current)
    if not _is_bootstrap_config(current):
        _write_marker(
            marker_path,
            {
                "version": 1,
                "status": "skipped-admin-configured",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        return "skipped-admin-configured"

    migrated, credentials = _migration_config(current, legacy)
    for _reference, secret, binding in credentials:
        _request(
            f"{api}/api/model-catalog",
            internal_token=internal_token,
            method="POST",
            payload={
                "provider": binding["provider"],
                "base_url": binding["base_url"],
                "secret": secret,
            },
        )

    updated = _request(
        f"{api}/api/config",
        internal_token=internal_token,
        method="PUT",
        payload={"expected_revision": revision, "config": migrated},
    )
    updated_revision = updated.get("revision")
    if not isinstance(updated_revision, str):
        raise RuntimeError("Admin API did not return the migrated revision")

    try:
        for reference, secret, _binding in credentials:
            _request(
                f"{api}/api/model-credentials",
                internal_token=internal_token,
                method="PUT",
                payload={"credential_ref": reference, "secret": secret},
            )
    except Exception as error:
        latest = _request(f"{api}/api/config", internal_token=internal_token)
        latest_revision = latest.get("revision")
        if isinstance(latest_revision, str):
            _request(
                f"{api}/api/config",
                internal_token=internal_token,
                method="PUT",
                payload={"expected_revision": latest_revision, "config": current},
            )
        raise RuntimeError("Legacy model credential migration failed") from error

    children = cast(list[dict[str, object]], migrated["subagent_slots"])
    _write_marker(
        marker_path,
        {
            "version": 1,
            "status": "applied",
            "created_at": datetime.now(UTC).isoformat(),
            "root_model": cast(dict[str, object], migrated["root_model"])["model"],
            "subagent_slots": [
                {"slot_id": child["slot_id"], "enabled": child["enabled"]}
                for child in children
            ],
        },
    )
    return "applied"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18104")
    parser.add_argument("--internal-token-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--legacy-state-dir",
        type=Path,
        default=Path("/opt/zd-agent/resources/.state"),
    )
    parser.add_argument("--legacy-container", default="zd-agent")
    args = parser.parse_args()
    status = migrate(
        base_url=args.base_url,
        internal_token=args.internal_token_file.read_text(encoding="utf-8").strip(),
        marker_path=args.state_dir / "legacy-model-migration.json",
        legacy_state_dir=args.legacy_state_dir,
        legacy_container=args.legacy_container,
    )
    print(f"Legacy model migration: {status}.")


if __name__ == "__main__":
    main()
