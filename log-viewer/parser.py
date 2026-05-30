"""读取 agent 落的 jsonl，聚合成工单列表 + 单工单时间线。纯函数，无 web 依赖。"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta, timezone


def read_events(log_dir: str, days: int = 7) -> list[dict]:
    """读 events-*.jsonl，逐行 json.loads，坏行跳过。

    days <= 0 表示读取**全部历史**（不按日期过滤）；磁盘上的 jsonl
    始终不删，这里只是控制网页往回读多少天。
    """
    if not os.path.isdir(log_dir):
        return []
    cutoff = None
    if days > 0:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    events: list[dict] = []
    for path in sorted(glob.glob(os.path.join(log_dir, "events-*.jsonl"))):
        base = os.path.basename(path)
        datestr = base.removeprefix("events-").removesuffix(".jsonl")
        try:
            fdate = datetime.strptime(datestr, "%Y-%m-%d").date()
        except ValueError:
            continue
        if cutoff is not None and fdate < cutoff:
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


# agent_done(主 agent) 排最前，再 subagent，最后 trigger_verdict(判定)；
# 其余带 model 的事件兜底排末尾。同模型只显示一次。
_MODEL_EVENT_ORDER = {
    "agent_done": 0,
    "subagent_using_cheap_model": 1,
    "trigger_verdict": 2,
}


def _collect_models(events: list[dict]) -> list[str]:
    """聚合该工单全部阶段用过的模型，去重，按阶段优先级排序。

    主 agent(agent_done) → subagent → 判定(trigger_verdict) → 其它。
    """
    seen: dict[str, int] = {}
    for e in events:
        m = e.get("model")
        if not m:
            continue
        m = str(m)
        rank = _MODEL_EVENT_ORDER.get(e.get("event", ""), 9)
        # 同一模型保留最小 rank（最靠前的阶段）
        if m not in seen or rank < seen[m]:
            seen[m] = rank
    return [m for m, _ in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))]


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
        # 触发方式：source（zendesk_webhook/silence_waker 等）优先，
        # 退回 via（native_messaging 渠道）；按时间升序取最早一次
        trigger = next(
            (e.get("source") or e.get("via") for e in events
             if e.get("source") or e.get("via")),
            None,
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
            # 模型：聚合该工单全阶段用过的模型并去重（主 agent / subagent /
            # 判定）。主 agent 优先排前。旧日志无 model → 空列表 → 前端显示 —
            "models": _collect_models(events),
            # 兼容字段：主 agent 模型（agent_done），单值，给其它消费方用
            "model": done.get("model"),
            "trigger": trigger,
            "last_time": _last_time(events),
            "event_count": len(events),
        })
    out.sort(key=lambda s: s["last_time"], reverse=True)
    return out


def ticket_timeline(grouped: dict[int, list[dict]], ticket_id: int) -> list[dict]:
    """单工单全事件，按 timestamp 升序。"""
    events = grouped.get(int(ticket_id), [])
    return sorted(events, key=lambda e: e.get("timestamp", ""))
