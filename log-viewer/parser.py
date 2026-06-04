"""读取 agent 落的 jsonl，聚合成工单列表 + 单工单时间线。纯函数，无 web 依赖。"""
from __future__ import annotations

import glob
import json
import os
import re
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

_SKIP_ONLY_EVENTS = {
    "zendesk_messaging_webhook_skip_non_user",
    "zendesk_messaging_webhook_dup",
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
        status = done.get("status", "")
        if not status and events and all(
            e.get("event") in _SKIP_ONLY_EVENTS for e in events
        ):
            status = "skipped"
        out.append({
            "ticket_id": tid,
            "status": status,
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


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _archive_empty(status: str = "missing", filename: str = "") -> dict:
    return {
        "available": False,
        "status": status,
        "filename": filename,
        "frontmatter": {},
        "sections": [],
        "grouped": {},
    }


def _parse_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    text = markdown.lstrip("\ufeff")
    if not text.startswith("---\n"):
        return {}, markdown
    end = text.find("\n---", 4)
    if end < 0:
        return {}, markdown
    raw = text[4:end]
    rest = text[end + 4 :]
    if rest.startswith("\n"):
        rest = rest[1:]
    data: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        data[key] = value.strip().strip('"').strip("'")
    return data, rest


def _section_category(title: str) -> str:
    t = re.sub(r"\s+", " ", title).strip().lower()
    if "internal analysis" in t or "内部分析" in t or "新增分析" in t:
        return "internal_analysis"
    if "解决方案" in t or "新增建议" in t or re.search(r"(^|\s)建议($|\s|/)", t):
        return "solution"
    if "客户回复草稿" in t:
        return "customer_reply_draft"
    if t.startswith("跟进"):
        return "follow_up"
    return "other"


def _group_sections(sections: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for section in sections:
        grouped.setdefault(section["category"], []).append(section)
    return grouped


def parse_archive_markdown(markdown: str) -> dict:
    """解析工单归档 Markdown：frontmatter + 按 heading 切块的章节。

    不依赖固定中文标题，未知章节保留为 other，前端决定如何展示。
    """
    frontmatter, body = _parse_frontmatter(markdown)
    sections: list[dict] = []
    current: dict | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal current, buf
        if current is None:
            content = "\n".join(buf).strip()
            if content:
                sections.append({
                    "level": 0,
                    "title": "归档说明",
                    "category": "other",
                    "content": content,
                })
            buf = []
            return
        current["content"] = "\n".join(buf).strip()
        sections.append(current)
        current = None
        buf = []

    for line in body.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            flush()
            title = m.group(2).strip()
            current = {
                "level": len(m.group(1)),
                "title": title,
                "category": _section_category(title),
                "content": "",
            }
        else:
            buf.append(line)
    flush()

    return {
        "available": True,
        "status": "loaded",
        "filename": "",
        "frontmatter": frontmatter,
        "sections": sections,
        "grouped": _group_sections(sections),
    }


def read_ticket_archive(records_dir: str, ticket_id: int) -> dict:
    """只读读取 <ticket_id>.md；不存在或不可读时返回 available=false。"""
    filename = f"{int(ticket_id)}.md"
    path = os.path.join(records_dir, filename)
    if not os.path.isfile(path):
        return _archive_empty(filename=filename)
    try:
        with open(path, encoding="utf-8") as f:
            archive = parse_archive_markdown(f.read())
    except OSError:
        return _archive_empty(status="unreadable", filename=filename)
    archive["filename"] = filename
    return archive


def _last_event(events: list[dict], name: str) -> dict:
    return next((e for e in reversed(events) if e.get("event") == name), {})


def _first_value(event: dict, keys: tuple[str, ...]) -> object:
    for key in keys:
        value = event.get(key)
        if value not in (None, ""):
            return value
    return None


def _event_pick(event: dict, keys: tuple[str, ...]) -> dict:
    out: dict = {}
    for key in keys:
        if key in event:
            out[key] = event[key]
    return out


def build_ticket_highlights(events: list[dict]) -> dict:
    """从事件时间线提取复盘页的关键证据。"""
    timeline = sorted(events, key=lambda e: e.get("timestamp", ""))
    verdict = _last_event(timeline, "trigger_verdict")
    classify = _last_event(timeline, "ticket_classified")
    done = _last_event(timeline, "agent_done")
    reply_events = [
        _event_pick(e, ("event", "timestamp", "comment_id", "status", "reply_sent"))
        for e in timeline
        if "reply" in str(e.get("event", ""))
    ]
    return {
        "trigger_verdict": {
            **_event_pick(verdict, ("event", "timestamp", "verdict", "completeness")),
            "reason": _first_value(
                verdict,
                ("reason", "verdict_reason", "decision_reason", "analysis", "message"),
            ),
        } if verdict else {},
        "classification": {
            **_event_pick(classify, ("event", "timestamp", "ticket_type")),
            "reason": _first_value(
                classify,
                ("reason", "classification_reason", "analysis", "message"),
            ),
        } if classify else {},
        "tool_rounds": [
            _event_pick(
                e,
                ("event", "timestamp", "round", "tool_call_count", "tool_names"),
            )
            for e in timeline
            if e.get("event") == "round_tool_calls"
        ],
        "reply_events": reply_events,
        "completion": _event_pick(
            done,
            (
                "event",
                "timestamp",
                "status",
                "rounds",
                "total_tokens",
                "elapsed",
                "reply_sent",
                "model",
            ),
        ) if done else {},
    }


def build_ticket_review(events: list[dict], records_dir: str, ticket_id: int) -> dict:
    """组装单工单复盘页 payload：summary + archive + highlights。"""
    timeline = sorted(events, key=lambda e: e.get("timestamp", ""))
    summary_list = summarize_tickets({int(ticket_id): timeline})
    summary = summary_list[0] if summary_list else {"ticket_id": int(ticket_id)}
    return {
        "summary": summary,
        "archive": read_ticket_archive(records_dir, int(ticket_id)),
        "highlights": build_ticket_highlights(timeline),
    }
