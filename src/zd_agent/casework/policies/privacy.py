from __future__ import annotations

import re


_INTERNAL_DETAIL_RE = re.compile(
    r"\b(?:tenant_id|source_call_ids?|sql_query|inspector_exec|signoz|mcp__|"
    r"evidence ledger|database table|internal trace)\b|"
    r"/opt/zd-agent/|src/zd_agent/|\bSELECT\s+.+\s+FROM\b",
    re.IGNORECASE | re.DOTALL,
)


def claim_is_customer_safe(statement: str) -> bool:
    return not _INTERNAL_DETAIL_RE.search(statement)
