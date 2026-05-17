"""P2-9 轻量版：Internal/Public reply 发送前的工具使用审计。

**不调 LLM**，纯正则 + grep tool_call_log，零成本零延迟。

检测下列"具体声明"是否真的出现过在某个 tool 调用结果里：

- ``PR #NNNN`` / ``Issue #NNNN``（GitHub PR/Issue 编号）
- 源码文件路径（``api/...py`` / ``web/...ts(x)`` / ``app/...py``）
- 版本号 ``v1.14.1`` 这种 semver
- 历史工单引用 ``#1234``（但当前工单号不算 hallucination，从豁免列表里去）

如果 claim 在 reply 里出现但 tool_call_log 里没出现过 → 标记为 "potential
hallucination"。**不 block 发送**，只在 Internal Note 加 warning section +
emit metric（让 SE 决定是否复核）。

注意 result_preview 只有 500 字符，长 tool 结果里的引用可能被截断 → 这是
"soft warning"，不当作绝对判断。
"""
from __future__ import annotations

import re
from typing import Any

# 检测正则
_PR_PATTERN = re.compile(r"\bPR\s*#(\d{1,6})\b")
_ISSUE_PATTERN = re.compile(r"\bIssue\s*#(\d{1,6})\b")
_TICKET_REF_PATTERN = re.compile(r"#(\d{3,6})\b")
_FILE_PATH_PATTERN = re.compile(
    r"\b([a-zA-Z][\w/-]*?/[\w/-]+\.(?:py|ts|tsx|js|jsx|md|yaml|yml|sql))\b"
)
_VERSION_PATTERN = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")


def _join_tool_outputs(tool_call_log: list[dict[str, Any]]) -> str:
    """把所有 tool result_preview 拼起来，用于 grep。"""
    return "\n".join(
        (item.get("result_preview") or "")
        for item in (tool_call_log or [])
    )


def audit_reply_against_tools(
    *,
    body: str,
    tool_call_log: list[dict[str, Any]],
    current_ticket_id: int | None = None,
) -> list[str]:
    """扫 reply body 找具体声明，对照 tool_call_log 看是否实际查到过。

    Returns:
        warning 字符串列表（每条描述一个可疑 claim）。
        空列表 = 通过审计 / 没找到可疑 claim。
    """
    if not body or not tool_call_log:
        return []

    tool_dump = _join_tool_outputs(tool_call_log)
    warnings: list[str] = []

    # 1. PR 编号
    pr_nums = set(_PR_PATTERN.findall(body))
    for pr in pr_nums:
        # tool 输出里有提到这个 PR # 吗？
        if f"#{pr}" not in tool_dump and f"PR {pr}" not in tool_dump:
            warnings.append(
                f"Reply 引用 PR #{pr} 但 tool 调用结果里未见该编号 — 可能是模型虚构"
            )

    # 2. Issue 编号
    issue_nums = set(_ISSUE_PATTERN.findall(body))
    for issue in issue_nums:
        if f"#{issue}" not in tool_dump and f"Issue {issue}" not in tool_dump:
            warnings.append(
                f"Reply 引用 Issue #{issue} 但 tool 调用结果里未见该编号"
            )

    # 3. 历史工单 #NNNN 引用（排除当前工单）
    ticket_refs = set(_TICKET_REF_PATTERN.findall(body))
    if current_ticket_id is not None:
        ticket_refs.discard(str(current_ticket_id))
    # 也要剔除 PR/Issue（已单独 audit 过）
    ticket_refs -= pr_nums
    ticket_refs -= issue_nums
    for tref in ticket_refs:
        if f"#{tref}" not in tool_dump:
            warnings.append(
                f"Reply 引用历史工单 #{tref} 但 tool 调用结果里未见 — "
                f"可能 agent 凭印象编了工单号"
            )

    # 4. 文件路径
    file_paths = set(_FILE_PATH_PATTERN.findall(body))
    for fp in file_paths:
        # 路径整体或主要文件名出现过即认为见过
        fname = fp.rsplit("/", 1)[-1]
        if fp not in tool_dump and fname not in tool_dump:
            warnings.append(
                f"Reply 引用源码路径 `{fp}` 但 tool 调用结果里未见 — "
                f"可能是猜的路径，建议 git_grep 验证"
            )

    # 5. 版本号（语义版本）
    versions = set(_VERSION_PATTERN.findall(body))
    for v in versions:
        # 客户消息里可能也带版本号（如 "我用的 1.14.1"），那不算 hallucination
        # 所以这里宽松：只要 tool_dump 或 body 上下文里有"version"/"v"附近
        # 就放过。简化版：如果 tool_dump 完全没提这个版本号 → 警告
        if v not in tool_dump:
            warnings.append(
                f"Reply 提到版本 v{v} 但 tool 调用结果里未见 — "
                f"如果不是客户报的版本，建议核实"
            )

    return warnings
