"""Create or update the immutable production configuration revision."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, cast

FOOTERS = {
    "en": (
        "This reply was sent by Dify AI Agent. If this does not resolve your "
        'issue, reply "human". A human support engineer will respond as soon '
        "as possible during support hours, 9:00 AM-6:00 PM Beijing Time (UTC+8)."
    ),
    "ja": (
        "この返信は Dify AI Agent によって送信されました。問題が解決しない場合は、"
        "「human」と返信してください。サポート営業時間(北京時間 UTC+8, 9:00〜18:00)"
        "に担当者ができるだけ早く対応します。"
    ),
    "zh": (
        "此回复由 Dify AI Agent 发送。如果问题仍未解决,请回复“human”。"
        "人工支持工程师将在北京时间(UTC+8)9:00-18:00 的服务时间内尽快回复。"
    ),
}


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
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise RuntimeError("Admin API returned a non-object response")
    return cast(dict[str, Any], result)


def configure(
    *, base_url: str, activate: bool, tool_names_path: Path, internal_token: str
) -> str:
    current = _request(
        f"{base_url.rstrip('/')}/api/config", internal_token=internal_token
    )
    revision = current.get("revision")
    raw_config = current.get("config")
    if not isinstance(revision, str) or not isinstance(raw_config, dict):
        raise RuntimeError("Admin API returned an invalid configuration snapshot")
    config = cast(dict[str, Any], raw_config)
    raw_tool_names: object = json.loads(tool_names_path.read_text())
    if not isinstance(raw_tool_names, list):
        raise RuntimeError("tool-names.json is invalid")
    tool_name_values = cast(list[object], raw_tool_names)
    if not all(isinstance(name, str) for name in tool_name_values):
        raise RuntimeError("tool-names.json is invalid")
    tool_names = [cast(str, name) for name in tool_name_values]

    config.update(
        {
            "desired_state": "running" if activate else "paused",
            "root_model": {
                "provider": "openai",
                "base_url": os.environ.get(
                    "CASEWORK_ROOT_MODEL_BASE_URL", "https://api.openai.com"
                ),
                "model": os.environ.get("CASEWORK_ROOT_MODEL", "gpt-5.5"),
                "credential_ref": "openai-primary",
            },
            # Child slots must be configured independently in Admin.  Never
            # synthesize an inherited child binding during bootstrap.
            "subagent_slots": config.get("subagent_slots", []),
            "rules": {
                "saas_tag": "saas",
                "saas_assignee": os.environ.get(
                    "CASEWORK_SAAS_ASSIGNEE", "46493567999892"
                ),
                "statuses": ["new", "open"],
                "human_aliases": ["human", "人工", "真人"],
                "reply_enabled": activate,
                "default_locale": "en",
                "footers": FOOTERS,
            },
            "enabled_skills": ["core-casework", "app-run-investigation"],
            "enabled_tools": tool_names,
            "context_window_tokens": 272000,
            "soft_compaction_percent": 60,
            "hard_checkpoint_percent": 80,
            "max_tool_rounds": 24,
        }
    )
    updated = _request(
        f"{base_url.rstrip('/')}/api/config",
        internal_token=internal_token,
        method="PUT",
        payload={"expected_revision": revision, "config": config},
    )
    updated_revision = updated.get("revision")
    if not isinstance(updated_revision, str):
        raise RuntimeError("Admin API did not return the new revision")
    print(
        "Activated Greenfield configuration."
        if activate
        else "Prepared paused Greenfield configuration."
    )
    return updated_revision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18104")
    parser.add_argument("--tool-names", type=Path, required=True)
    parser.add_argument("--internal-token-file", type=Path, required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    configure(
        base_url=args.base_url,
        activate=args.activate,
        tool_names_path=args.tool_names,
        internal_token=args.internal_token_file.read_text(encoding="utf-8").strip(),
    )


if __name__ == "__main__":
    main()
