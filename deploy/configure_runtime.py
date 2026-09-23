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
        "as possible during support hours (New York time): Sunday-Thursday nights, "
        "9:00 PM-6:00 AM EDT (daylight saving time) or "
        "8:00 PM-5:00 AM EST (standard time)."
    ),
    "ja": (
        "この返信は Dify AI Agent によって送信されました。問題が解決しない場合は、"
        "「human」と返信してください。担当者はニューヨーク時間の日曜〜木曜の夜、"
        "夏時間は21:00〜翌6:00、標準時間は20:00〜翌5:00の営業時間内に"
        "できるだけ早く対応します。"
    ),
    "zh": (
        "此回复由 Dify AI Agent 发送。如果问题仍未解决,请回复“human”。"
        "人工支持工程师将在纽约时间周日至周四夜间的服务时间内尽快回复："
        "夏令时 21:00–次日 06:00，冬令时 20:00–次日 05:00。"
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
    *,
    base_url: str,
    activate: bool | None,
    tool_names_path: Path,
    internal_token: str,
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

    root_model = config.get("root_model")
    if isinstance(root_model, dict):
        root_values = cast(dict[str, object], root_model)
        root_is_unconfigured = any(
            root_values.get(field) in (None, "", "unconfigured")
            for field in ("provider", "model", "credential_ref")
        )
    else:
        root_is_unconfigured = True
    if root_is_unconfigured:
        root_model = {
            "provider": "openai",
            "base_url": os.environ.get(
                "CASEWORK_ROOT_MODEL_BASE_URL", "https://api.openai.com"
            ),
            "model": os.environ.get("CASEWORK_ROOT_MODEL", "gpt-5.5"),
            "credential_ref": "openai-primary",
        }

    previous_state = config.get("desired_state")
    desired_state = (
        previous_state
        if activate is None and previous_state in {"running", "paused"}
        else "running" if activate else "paused"
    )
    raw_rules = config.get("rules")
    existing_rules = (
        cast(dict[str, object], raw_rules) if isinstance(raw_rules, dict) else {}
    )
    if activate is None:
        previous_reply_enabled = existing_rules.get("reply_enabled")
        reply_enabled = (
            previous_reply_enabled
            if isinstance(previous_reply_enabled, bool)
            else desired_state == "running"
        )
    else:
        reply_enabled = activate
    configured_group_id = (
        os.environ.get("CASEWORK_SAAS_GROUP_ID", "").strip()
        or os.environ.get("CASEWORK_SAAS_ASSIGNEE", "").strip()
        or "40685275477140"
    )
    previous_assignment = existing_rules.get("saas_assignee")
    saas_group_id = (
        previous_assignment
        if isinstance(previous_assignment, (str, int))
        and str(previous_assignment).strip() not in {"", "46493567999892"}
        else configured_group_id
    )

    config.update(
        {
            "desired_state": desired_state,
            # The Admin revision is authoritative after the initial bootstrap.
            # Deployments may pause/start the Agent, but must never reset a model,
            # endpoint, credential reference, or independently configured child.
            "root_model": root_model,
            # Child slots must be configured independently in Admin.  Never
            # synthesize an inherited child binding during bootstrap.
            "subagent_slots": config.get("subagent_slots", []),
            "rules": {
                "saas_tag": "saas",
                # The persisted key is retained for API compatibility. Its
                # value is the Zendesk SaaS group ID, never a user assignee ID.
                "saas_assignee": saas_group_id,
                "statuses": ["new", "open"],
                "human_aliases": ["human", "人工", "真人"],
                "reply_enabled": reply_enabled,
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
    print(f"Configured Greenfield runtime in {desired_state} state.")
    return updated_revision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18104")
    parser.add_argument("--tool-names", type=Path, required=True)
    parser.add_argument("--internal-token-file", type=Path, required=True)
    state = parser.add_mutually_exclusive_group()
    state.add_argument(
        "--activate", dest="activate", action="store_const", const=True
    )
    state.add_argument(
        "--pause", dest="activate", action="store_const", const=False
    )
    parser.set_defaults(activate=None)
    args = parser.parse_args()
    configure(
        base_url=args.base_url,
        activate=args.activate,
        tool_names_path=args.tool_names,
        internal_token=args.internal_token_file.read_text(encoding="utf-8").strip(),
    )


if __name__ == "__main__":
    main()
