"""保证 prompt / 代码里无残留的已失效 SigNoz MCP 工具名。"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STALE = [
    "signoz_search_logs",
    "signoz_aggregate_logs",
    "signoz_list_services",
    "signoz_search_traces",
    "signoz_get_field_keys",
    "signoz_get_field_values",
]

SCAN = [
    ROOT / "src/zd_agent/prompts",
    ROOT / "src/zd_agent/tools/subagent_parallel.py",
]


def _iter_files():
    for p in SCAN:
        if p.is_file():
            yield p
        else:
            yield from p.rglob("*.md")
            yield from p.rglob("*.py")


@pytest.mark.parametrize("stale", STALE)
def test_no_stale_tool_names(stale):
    hits = []
    for f in _iter_files():
        if "__pycache__" in str(f):
            continue
        text = f.read_text(encoding="utf-8")
        if stale in text:
            hits.append(str(f.relative_to(ROOT)))
    assert not hits, f"残留旧工具名 {stale}: {hits}"
