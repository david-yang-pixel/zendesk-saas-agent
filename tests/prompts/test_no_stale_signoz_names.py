"""Ensure prompts expose the structured SigNoz MCP surface."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STRUCTURED = [
    "signoz_search_logs",
    "signoz_aggregate_logs",
    "signoz_list_services",
    "signoz_search_traces",
    "signoz_aggregate_traces",
    "signoz_get_trace_details",
    "signoz_list_metrics",
    "signoz_query_metrics",
    "signoz_get_field_keys",
    "signoz_get_field_values",
]

PROMPTS = [
    ROOT / "src/zd_agent/prompts/TOOLS.md",
    ROOT / "src/zd_agent/prompts/skills/DIFY_SUPPORT_TICKET.md",
    ROOT / "src/zd_agent/prompts/strategies/platform_issue.md",
]


@pytest.mark.parametrize("tool_name", STRUCTURED)
def test_structured_tool_names_are_documented(tool_name):
    text = "\n".join(path.read_text(encoding="utf-8") for path in PROMPTS)
    assert tool_name in text
