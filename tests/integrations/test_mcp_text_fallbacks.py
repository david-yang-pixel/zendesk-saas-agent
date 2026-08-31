from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from zd_agent.integrations.source_mcp import _parse_text_payload as parse_source
from zd_agent.integrations.tool_service_mcp import _parse_text_payload as parse_service
from zd_agent.integrations.zendesk_mcp import _parse_text_payload as parse_zendesk


Parser = Callable[[dict[str, object]], dict[str, object]]


def _parse_service(raw: dict[str, object]) -> dict[str, object]:
    return parse_service(raw, prefix="DOCS", service="docs")


@pytest.mark.parametrize("parser", [_parse_service, parse_source, parse_zendesk])
def test_mcp_text_fallback_finds_only_json_object_after_other_blocks(
    parser: Parser,
) -> None:
    expected = {"success": True, "data": {"id": 42}}
    result = parser(
        {
            "content": [
                {"type": "image", "data": "ignored"},
                {"type": "text", "text": "human-readable preface"},
                {"type": "text", "text": json.dumps(expected)},
            ]
        }
    )

    assert result == expected


@pytest.mark.parametrize("parser", [_parse_service, parse_source, parse_zendesk])
def test_mcp_text_fallback_rejects_multiple_json_objects(parser: Parser) -> None:
    result = parser(
        {
            "content": [
                {"type": "text", "text": '{"success": true}'},
                {"type": "text", "text": '{"success": false}'},
            ]
        }
    )

    assert result["success"] is False
    assert "AMBIGUOUS" in str(result["code"])
