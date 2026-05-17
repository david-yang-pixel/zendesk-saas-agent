#!/usr/bin/env python
"""CLI: 测量最近 N 个 SaaS 工单的 agent reply modify rate。

用法：
    python scripts/measure_modify_rate.py             # 默认最近 20 单
    python scripts/measure_modify_rate.py --limit 50

输出：每个工单的 classification + 总结统计。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 让脚本能找到 src
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zd_agent.config import get_config  # noqa: E402
from zd_agent.observability.modify_rate import (  # noqa: E402
    measure_recent_modify_rate,
    summarize_samples,
)
from zd_agent.tools.zendesk import _get_client  # noqa: E402


async def main(limit: int = 20) -> None:
    cfg = get_config()
    agent_uid = cfg.zendesk.agent_user_id
    if not agent_uid:
        print("ERROR: cfg.zendesk.agent_user_id 未配置")
        sys.exit(1)

    client = _get_client()
    # 拉最近 limit 个 SaaS 工单（任何状态）
    print(f"Fetching last {limit} SaaS tickets…", file=sys.stderr)
    resp = await asyncio.to_thread(
        client.search_tickets,
        "tags:saas",
        "updated_at", "desc", 1, limit,
    )
    tickets = (resp or {}).get("results") or []
    ticket_ids = [t["id"] for t in tickets if "id" in t]
    if not ticket_ids:
        print("No tickets found.", file=sys.stderr)
        return

    print(f"Measuring modify rate on {len(ticket_ids)} tickets…", file=sys.stderr)
    samples = await measure_recent_modify_rate(ticket_ids, agent_uid)

    # 详细每单
    print("\n=== Per-ticket detail ===")
    for s in samples:
        print(json.dumps(s.as_dict(), ensure_ascii=False))

    # 总结
    print("\n=== Summary ===")
    print(json.dumps(summarize_samples(samples), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(main(args.limit))
