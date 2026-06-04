import json
from datetime import datetime, timezone

import parser as P


def _write_jsonl(tmp_path, rows):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


def test_read_events_basic(tmp_path):
    _write_jsonl(tmp_path, [
        {"event": "a", "ticket_id": 1},
        {"event": "b", "ticket_id": 2},
    ])
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) == 2
    assert {e["event"] for e in events} == {"a", "b"}


def test_read_events_skips_bad_lines(tmp_path):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    f.write_text('{"event":"ok","ticket_id":1}\nNOT JSON GARBAGE\n{"event":"ok2","ticket_id":2}\n')
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) == 2


def test_read_events_empty_dir(tmp_path):
    assert P.read_events(str(tmp_path), days=1) == []


def test_group_by_ticket():
    events = [
        {"event": "a", "ticket_id": 1},
        {"event": "b", "ticket_id": 2},
        {"event": "c", "ticket_id": 1},
        {"event": "startup"},
    ]
    grouped = P.group_by_ticket(events)
    assert set(grouped.keys()) == {1, 2}
    assert len(grouped[1]) == 2


def test_summarize_tickets():
    events = [
        {"event": "trigger_verdict", "ticket_id": 5, "verdict": "substantial_issue",
         "timestamp": "2026-05-29T04:00:00Z"},
        {"event": "ticket_classified", "ticket_id": 5, "ticket_type": "knowledge_base",
         "timestamp": "2026-05-29T04:00:05Z"},
        {"event": "agent_done", "ticket_id": 5, "rounds": 7, "total_tokens": 370297,
         "reply_sent": True, "status": "completed", "elapsed": 150.6,
         "timestamp": "2026-05-29T04:02:30Z"},
    ]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert len(summaries) == 1
    s = summaries[0]
    assert s["ticket_id"] == 5
    assert s["status"] == "completed"
    assert s["verdict"] == "substantial_issue"
    assert s["rounds"] == 7
    assert s["total_tokens"] == 370297
    assert s["reply_sent"] is True
    assert s["last_time"] == "2026-05-29T04:02:30Z"


def test_summarize_sorts_desc_by_last_time():
    events = [
        {"event": "agent_done", "ticket_id": 1, "timestamp": "2026-05-29T01:00:00Z"},
        {"event": "agent_done", "ticket_id": 2, "timestamp": "2026-05-29T03:00:00Z"},
        {"event": "agent_done", "ticket_id": 3, "timestamp": "2026-05-29T02:00:00Z"},
    ]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert [s["ticket_id"] for s in summaries] == [2, 3, 1]


def test_ticket_timeline_sorted_asc():
    events = [
        {"event": "agent_done", "ticket_id": 9, "timestamp": "2026-05-29T04:02:00Z"},
        {"event": "trigger_verdict", "ticket_id": 9, "timestamp": "2026-05-29T04:00:00Z"},
    ]
    tl = P.ticket_timeline(P.group_by_ticket(events), 9)
    assert [e["event"] for e in tl] == ["trigger_verdict", "agent_done"]


def test_summarize_missing_fields_no_crash():
    events = [{"event": "trigger_verdict", "ticket_id": 7, "timestamp": "2026-05-29T04:00:00Z"}]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert summaries[0]["ticket_id"] == 7
    assert summaries[0]["status"] == ""
    assert summaries[0]["rounds"] is None


def test_summarize_skip_only_ticket_marked_skipped():
    events = [
        {
            "event": "zendesk_messaging_webhook_skip_non_user",
            "ticket_id": 2756,
            "actor_type": "system",
            "timestamp": "2026-06-02T05:37:35Z",
        },
        {
            "event": "zendesk_messaging_webhook_skip_non_user",
            "ticket_id": 2756,
            "actor_type": "agent",
            "timestamp": "2026-06-02T05:38:02Z",
        },
    ]

    summaries = P.summarize_tickets(P.group_by_ticket(events))

    assert summaries[0]["status"] == "skipped"


import os as _os


def test_real_sample_parses(tmp_path):
    fixture = _os.path.join(_os.path.dirname(__file__), "fixtures", "sample.jsonl")
    from datetime import datetime as _d, timezone as _t
    date = _d.now(_t.utc).strftime("%Y-%m-%d")
    (tmp_path / f"events-{date}.jsonl").write_text(open(fixture, encoding="utf-8").read())
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) > 0
    grouped = P.group_by_ticket(events)
    assert len(grouped) >= 1
    summaries = P.summarize_tickets(grouped)
    assert all("ticket_id" in s for s in summaries)
    # startup 行（无 ticket_id）必须被丢弃，不出现在任何工单分组里
    assert 2709 in grouped


def test_read_events_days_zero_reads_all_history(tmp_path):
    """days<=0 读取全部历史日志（不按日期过滤）；老文件不被截断。"""
    # 今天的文件
    _write_jsonl(tmp_path, [{"ticket_id": 1, "event": "x"}])
    # 一个很久以前的文件（14 天窗口会丢，全部历史应保留）
    (tmp_path / "events-2000-01-01.jsonl").write_text(
        json.dumps({"ticket_id": 2, "event": "y"}) + "\n"
    )
    ids_all = sorted(e["ticket_id"] for e in P.read_events(str(tmp_path), days=0))
    assert ids_all == [1, 2]
    # 对照：14 天窗口只读到今天的
    ids_win = sorted(e["ticket_id"] for e in P.read_events(str(tmp_path), days=14))
    assert ids_win == [1]


def test_parse_archive_markdown_frontmatter_and_sections():
    md = """---
ticket_id: 2774
status: solved
assignee: dify.ai
---

# 工单 #2774

## 内部分析

用户反馈工作流运行失败，需要确认报错链路。

## 解决方案 / 建议

建议客户重新授权并重试。

## 客户回复草稿（Internal Note）

您好，我们已经定位到授权失效。

## 跟进 2026-06-03

客户补充了截图。
"""

    parsed = P.parse_archive_markdown(md)

    assert parsed["frontmatter"] == {
        "ticket_id": "2774",
        "status": "solved",
        "assignee": "dify.ai",
    }
    assert parsed["available"] is True
    assert parsed["sections"][0]["title"] == "工单 #2774"
    assert parsed["sections"][1]["category"] == "internal_analysis"
    assert parsed["sections"][2]["category"] == "solution"
    assert parsed["sections"][3]["category"] == "customer_reply_draft"
    assert parsed["sections"][4]["category"] == "follow_up"


def test_parse_archive_markdown_english_and_incremental_titles():
    md = """## Internal Analysis
Need verify billing state.

## 新增分析
客户第二次补充了 invoice id。

## 新增建议
同步给 billing team。

## 客户回复草稿
We are checking this with the billing team.
"""

    parsed = P.parse_archive_markdown(md)

    categories = [s["category"] for s in parsed["sections"]]
    assert categories == [
        "internal_analysis",
        "internal_analysis",
        "solution",
        "customer_reply_draft",
    ]


def test_read_ticket_archive_missing_file(tmp_path):
    archive = P.read_ticket_archive(str(tmp_path), 123)

    assert archive["available"] is False
    assert archive["frontmatter"] == {}
    assert archive["sections"] == []


def test_build_ticket_review_returns_summary_archive_and_highlights(tmp_path):
    rows = [
        {
            "event": "trigger_verdict",
            "ticket_id": 42,
            "verdict": "substantial_issue",
            "reason": "客户提供了明确报错。",
            "timestamp": "2026-05-29T04:00:00Z",
        },
        {
            "event": "ticket_classified",
            "ticket_id": 42,
            "ticket_type": "api",
            "reason": "涉及 API 调用失败。",
            "timestamp": "2026-05-29T04:00:05Z",
        },
        {
            "event": "round_tool_calls",
            "ticket_id": 42,
            "round": 1,
            "tool_call_count": 2,
            "tool_names": ["zendesk_search", "kb_search"],
            "timestamp": "2026-05-29T04:00:30Z",
        },
        {
            "event": "public_reply_sent",
            "ticket_id": 42,
            "comment_id": 777,
            "timestamp": "2026-05-29T04:01:00Z",
        },
        {
            "event": "agent_done",
            "ticket_id": 42,
            "rounds": 3,
            "total_tokens": 12000,
            "elapsed": 95.4,
            "reply_sent": True,
            "status": "completed",
            "timestamp": "2026-05-29T04:02:00Z",
        },
    ]
    (tmp_path / "42.md").write_text(
        "---\nstatus: solved\n---\n\n## 内部分析\n\n已定位到 API 参数问题。\n",
        encoding="utf-8",
    )

    review = P.build_ticket_review(rows, str(tmp_path), 42)

    assert review["summary"]["ticket_id"] == 42
    assert review["summary"]["status"] == "completed"
    assert review["summary"]["ticket_type"] == "api"
    assert review["archive"]["available"] is True
    assert review["archive"]["sections"][0]["category"] == "internal_analysis"
    assert review["highlights"]["trigger_verdict"]["reason"] == "客户提供了明确报错。"
    assert review["highlights"]["classification"]["reason"] == "涉及 API 调用失败。"
    assert review["highlights"]["tool_rounds"][0]["tool_names"] == ["zendesk_search", "kb_search"]
    assert review["highlights"]["reply_events"][0]["event"] == "public_reply_sent"
    assert review["highlights"]["completion"]["rounds"] == 3
