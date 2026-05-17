"""Tests for runtime.reply_audit (P2-9 lightweight).

零成本审计：scan agent's reply for "specific claims" + cross-reference
tool_call_log. 找出未经证实的 claim 作为 SE 复核 hint。
"""
from __future__ import annotations

from zd_agent.runtime.reply_audit import audit_reply_against_tools


def _tool_log(*previews: str) -> list[dict]:
    """构造 tool_call_log mock，每个 preview 是一条 tool 返回"""
    return [
        {"round": i, "tool": "x", "args": {}, "success": True,
         "error": None, "result_preview": p}
        for i, p in enumerate(previews)
    ]


# ---------------------------------------------------------------------------
# 正常路径：claim 都在 tool log 里 → 无 warning
# ---------------------------------------------------------------------------

class TestNoWarnings:
    def test_pr_in_tool_log_no_warning(self) -> None:
        body = "Per PR #33216 this is fixed."
        log = _tool_log("Found PR #33216 from github_search_prs")
        assert audit_reply_against_tools(body=body, tool_call_log=log) == []

    def test_file_path_in_tool_log_no_warning(self) -> None:
        body = "See api/services/workflow_service.py for details."
        log = _tool_log("git_grep result: api/services/workflow_service.py contains ...")
        assert audit_reply_against_tools(body=body, tool_call_log=log) == []

    def test_empty_body_empty_log_no_warnings(self) -> None:
        assert audit_reply_against_tools(body="", tool_call_log=[]) == []

    def test_no_specific_claims_no_warnings(self) -> None:
        body = "Hi, please contact support if the issue persists."
        log = _tool_log("nothing")
        assert audit_reply_against_tools(body=body, tool_call_log=log) == []


# ---------------------------------------------------------------------------
# Hallucination 检测
# ---------------------------------------------------------------------------

class TestPrHallucination:
    def test_pr_not_in_log_warns(self) -> None:
        body = "This was fixed in PR #99999."
        log = _tool_log("totally unrelated content")
        warnings = audit_reply_against_tools(body=body, tool_call_log=log)
        assert len(warnings) == 1
        assert "#99999" in warnings[0]
        assert "虚构" in warnings[0]

    def test_multiple_prs_some_real_some_fake(self) -> None:
        body = "Fixed in PR #33216 (released) and PR #99999 (queued)."
        log = _tool_log("PR #33216 is mentioned here")
        warnings = audit_reply_against_tools(body=body, tool_call_log=log)
        # 只有 #99999 是 hallucination
        assert len(warnings) == 1
        assert "#99999" in warnings[0]
        assert "#33216" not in warnings[0]


class TestFilePathHallucination:
    def test_made_up_path_warns(self) -> None:
        body = "See `api/services/totally_made_up.py` line 42."
        log = _tool_log("git_grep found nothing relevant")
        warnings = audit_reply_against_tools(body=body, tool_call_log=log)
        assert any("totally_made_up.py" in w for w in warnings)

    def test_fname_in_log_no_warning(self) -> None:
        """即使完整路径不一致，只要文件名出现过即认为见过"""
        body = "See api/services/workflow_service.py"
        # tool log 只提了文件名（如 SQL 错误信息）
        log = _tool_log("Error in file workflow_service.py at line 50")
        assert audit_reply_against_tools(body=body, tool_call_log=log) == []


class TestTicketRefHallucination:
    def test_referenced_ticket_not_in_log_warns(self) -> None:
        body = "Similar to historical ticket #1188."
        log = _tool_log("no historical reference here")
        warnings = audit_reply_against_tools(
            body=body, tool_call_log=log, current_ticket_id=5000,
        )
        assert len(warnings) == 1
        assert "#1188" in warnings[0]

    def test_current_ticket_id_not_flagged(self) -> None:
        """当前工单号在 body 里出现不算 hallucination"""
        body = "Processing ticket #5000."
        log = _tool_log("nothing")
        warnings = audit_reply_against_tools(
            body=body, tool_call_log=log, current_ticket_id=5000,
        )
        # 不该 flag 当前工单号
        assert all("#5000" not in w for w in warnings)


class TestVersionHallucination:
    def test_version_not_in_log_warns(self) -> None:
        body = "Fixed in v1.99.9."
        log = _tool_log("running v1.14.1 currently")
        warnings = audit_reply_against_tools(body=body, tool_call_log=log)
        assert any("1.99.9" in w for w in warnings)

    def test_version_in_log_no_warning(self) -> None:
        body = "You're on v1.14.1, which has this bug."
        log = _tool_log("classify returned version=v1.14.1")
        # tool dump 里有 1.14.1
        assert audit_reply_against_tools(body=body, tool_call_log=log) == []


class TestMultipleWarnings:
    def test_collects_all_types(self) -> None:
        body = (
            "Per PR #99999, see api/fake/file.py for details. "
            "This is similar to #8888 from last year. Fix in v9.9.9."
        )
        log = _tool_log("totally empty")
        warnings = audit_reply_against_tools(
            body=body, tool_call_log=log, current_ticket_id=1,
        )
        # PR + file + ticket ref + version = 4 warnings
        assert len(warnings) >= 4
