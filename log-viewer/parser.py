"""读取 agent 落的 jsonl，聚合成工单列表 + 单工单时间线。纯函数，无 web 依赖。"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta, timezone


def read_events(log_dir: str, days: int = 7) -> list[dict]:
    """读最近 days 天的 events-*.jsonl，逐行 json.loads，坏行跳过。"""
    if not os.path.isdir(log_dir):
        return []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    events: list[dict] = []
    for path in sorted(glob.glob(os.path.join(log_dir, "events-*.jsonl"))):
        base = os.path.basename(path)
        datestr = base.removeprefix("events-").removesuffix(".jsonl")
        try:
            fdate = datetime.strptime(datestr, "%Y-%m-%d").date()
        except ValueError:
            continue
        if fdate < cutoff:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return events


def group_by_ticket(events: list[dict]) -> dict[int, list[dict]]:
    """按 ticket_id 分组；无 ticket_id 的行（startup/poller 等）丢弃。"""
    grouped: dict[int, list[dict]] = {}
    for e in events:
        tid = e.get("ticket_id")
        if tid is None:
            continue
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(tid, []).append(e)
    return grouped


def _last_time(events: list[dict]) -> str:
    times = [e.get("timestamp", "") for e in events if e.get("timestamp")]
    return max(times) if times else ""


def summarize_tickets(grouped: dict[int, list[dict]]) -> list[dict]:
    """每工单聚合一行摘要，按 last_time 倒序。"""
    out: list[dict] = []
    for tid, events in grouped.items():
        done = next((e for e in reversed(events) if e.get("event") == "agent_done"), {})
        verdict_ev = next(
            (e for e in reversed(events) if e.get("event") == "trigger_verdict"), {}
        )
        classify_ev = next(
            (e for e in reversed(events) if e.get("event") == "ticket_classified"), {}
        )
        out.append({
            "ticket_id": tid,
            "status": done.get("status", ""),
            "verdict": verdict_ev.get("verdict", ""),
            "ticket_type": classify_ev.get("ticket_type", ""),
            "rounds": done.get("rounds"),
            "total_tokens": done.get("total_tokens"),
            "elapsed": done.get("elapsed"),
            "reply_sent": done.get("reply_sent"),
            "last_time": _last_time(events),
            "event_count": len(events),
        })
    out.sort(key=lambda s: s["last_time"], reverse=True)
    return out


def ticket_timeline(grouped: dict[int, list[dict]], ticket_id: int) -> list[dict]:
    """单工单全事件，按 timestamp 升序。"""
    events = grouped.get(int(ticket_id), [])
    return sorted(events, key=lambda e: e.get("timestamp", ""))
