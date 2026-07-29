"""RunSubagentsParallel: dispatch N subagents concurrently via asyncio.gather."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, TYPE_CHECKING

from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


_MAX_SUBAGENT_OUTPUT_CHARS = 4000
_MAX_SUBAGENTS_PER_SESSION = 5

_SOURCE_TOOLS = {
    "git_grep",
    "git_show",
    "git_log",
    "git_diff",
    "github_code_search",
    "github_search_prs",
    "github_get_pr",
    "github_search_issues",
    "github_get_issue",
}
_LOG_TOOLS = {
    "signoz_errors",
    "signoz_traceback",
    "signoz_query",
    "signoz_services",
    "signoz_list_services",
    "signoz_get_trace_details",
    "signoz_list_metrics",
    "signoz_get_field_keys",
    "signoz_get_field_values",
    "signoz_search_logs",
    "signoz_aggregate_logs",
    "signoz_search_traces",
    "signoz_aggregate_traces",
    "signoz_query_metrics",
}
_KNOWLEDGE_TOOLS = {
    "knowledge_search",
    "knowledge_read",
    "ticket_record_search",
    "ticket_record_read",
    "zendesk_search_articles",
    "zendesk_get_article",
    "official_docs_search",
    "official_docs_read",
}
_DATA_TOOLS = {"sql_query", "inspector_exec"}


def _truncate_subagent_output_for_parent(text: str) -> str:
    """Limit each returned subagent block before appending to parent history."""
    if len(text) <= _MAX_SUBAGENT_OUTPUT_CHARS:
        return text
    keep = int(_MAX_SUBAGENT_OUTPUT_CHARS * 0.9)
    return (
        text[:keep]
        + f"\n\n[...subagent 输出已截断，原 {len(text)} 字符 → "
          f"{keep} 字符。若需细节请重新派一个更具体的 subagent 任务]"
    )


def _subagent_worker_role(tools: list[str]) -> str:
    tool_set = set(tools)
    if tool_set & _LOG_TOOLS:
        return "log_investigator"
    if tool_set & _SOURCE_TOOLS:
        return "source_investigator"
    if tool_set & _KNOWLEDGE_TOOLS:
        return "knowledge_investigator"
    if tool_set & _DATA_TOOLS:
        return "data_investigator"
    return "subagent"


class RunSubagentsParallel(Tool):
    name = "run_subagents_parallel"
    description = (
        "仅在 deep 调查中派 subagent 执行可独立并行的分析任务。"
        "不要因为需要两类证据就机械派发；只有源码、日志、文档、GitHub PR/Issue"
        "等任务互不依赖且并行能明显加快取证时使用。\n\n"
        "**Subagent 预算规则（每个工单累计）**：\n"
        "- 简单问题（usage-howto / product_usage / yes-no）：累计 **0-1 个** subagent\n"
        "- 中等多证据源问题（billing / refund / api / permissions / knowledge_base）：累计 **2-3 个**\n"
        "- 复杂问题（platform_issue / workflow / 多组件交互）：累计 **3-5 个**，硬上限 **5 个**\n\n"
        "**操作流程**：\n"
        "1. 先按证据源拆任务：源码、日志、文档、GitHub PR/Issue、专项数据核查\n"
        "2. 一次调用最多传 5 个 task；每个 task 只给 1-3 个真正需要的 tools\n"
        "3. 数据库查询若强依赖 workspace_id / 前序 SQL，可由主 agent 直接跑；"
        "独立统计或大结果摘要再派 subagent\n\n"
        "**反例**：客户问'能不能导出日志' yes/no 问题，1 个 KB 查询足够，不要派多个。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": (
                    "多证据源工单应按证据源拆成 2-5 个 task 并行跑"
                    "（如：源码 + 日志 + 文档/历史 + GitHub PR）。单次调用累计 ≤ 5 个。"
                ),
                "items": {
                    "type": "object",
                    "required": ["task", "tools"],
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "任务描述",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "**必填** 允许 subagent 使用的工具名 list。"
                                "**只列你这个 task 真需要的 1-3 个工具**（不要传全集）。"
                                "subagent 加载工具时会 register 这里指定的，"
                                "每个 tool schema ~150-800 tokens，多传一个就贵一份。"
                                "示例 task='搜 dify-billing 代码' → tools=['git_grep']。"
                                "task='查 SigNoz 错误' → tools=['signoz_errors', "
                                "'signoz_traceback']。**别图省事传 10 个**。"
                            ),
                            "minItems": 1,
                            "maxItems": 5,
                        },
                        "context": {
                            "type": "string",
                            "description": "传给 subagent 的背景信息",
                        },
                    },
                },
                "minItems": 1,
                # 多证据源工单按来源 fan-out；最多 5 个，聚合输出再截断，
                # 避免多个 subagent 同时返回大段内容把父 loop history 堆爆。
                "maxItems": 5,
            }
        },
        "required": ["tasks"],
    }
    group = "always"
    concurrency_safe = False

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        tasks = args.get("tasks") or []
        if not isinstance(tasks, list):
            return ToolResult(success=False, error="tasks must be a list")
        if not tasks:
            return ToolResult(success=False, error="tasks is empty")
        if len(tasks) > _MAX_SUBAGENTS_PER_SESSION:
            return ToolResult(success=False, error="tasks must contain at most 5 items")

        # 招法 3: tools 是 required，没传就报错让 LLM 重写。避免 fallback
        # 到全工具集（76 个 schema = 16K tokens 浪费）。
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                return ToolResult(success=False, error=f"task[{i}] must be an object")
            task = t.get("task")
            if not isinstance(task, str):
                return ToolResult(success=False, error=f"task[{i}].task must be a string")
            task = task.strip()
            if not task:
                return ToolResult(success=False, error=f"task[{i}].task is empty")
            context = t.get("context", "")
            if not isinstance(context, str):
                return ToolResult(success=False, error=f"task[{i}].context must be a string")
            t["task"] = task
            t["context"] = context
            tools = t.get("tools") or []
            if not tools:
                return ToolResult(
                    success=False,
                    error=(
                        f"task[{i}] missing required 'tools' field. "
                        "Specify 1-3 tools you actually need (e.g. ['git_grep'] for "
                        "源码搜，['signoz_errors'] for 日志). Don't dump the "
                        "full toolset — each tool schema is 150-800 tokens."
                    ),
                )
            if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
                return ToolResult(
                    success=False,
                    error=f"task[{i}].tools must be a list of tool name strings",
                )

        current_count = getattr(session, "subagent_dispatch_count", 0)
        if not isinstance(current_count, int):
            current_count = 0
        requested_count = len(tasks)
        if current_count + requested_count > _MAX_SUBAGENTS_PER_SESSION:
            return ToolResult(
                success=False,
                error=(
                    "cumulative subagent limit exceeded: "
                    f"{current_count} + {requested_count} > {_MAX_SUBAGENTS_PER_SESSION}"
                ),
            )

        # Late import to avoid circular dependency
        from zd_agent.runtime.subagent import run_subagent

        batch_id = f"subagents-{uuid.uuid4().hex[:12]}"

        coros = [
            run_subagent(
                t["task"],
                t.get("tools", []) or [],
                t["context"],
                session,
                log_metadata={
                    "batch_id": batch_id,
                    "sibling_index": i,
                    "worker_role": _subagent_worker_role(t.get("tools", []) or []),
                    "question_preview": t["task"][:500],
                    "task_preview": t["task"][:500],
                },
            )
            for i, t in enumerate(tasks)
        ]

        results = await asyncio.gather(*coros, return_exceptions=True)

        lines: list[str] = []
        any_success = False
        usable_count = 0
        for i, (spec, res) in enumerate(zip(tasks, results), start=1):
            title = str(spec.get("task", ""))[:80]
            lines.append(f"=== Subagent {i}: {title} ===")
            if isinstance(res, Exception):
                lines.append(f"[ERROR] {type(res).__name__}: {res}")
            elif isinstance(res, ToolResult):
                if res.success:
                    any_success = True
                    usable_count += 1
                    lines.append(
                        _truncate_subagent_output_for_parent(
                            res.output or "(empty output)"
                        )
                    )
                else:
                    lines.append(f"[FAILED] {res.error}")
            else:
                any_success = True
                usable_count += 1
                lines.append(str(res))
            lines.append("")

        session.subagent_dispatch_count = current_count + usable_count

        # Success if at least one subagent returned a successful ToolResult
        return ToolResult(
            success=any_success,
            output="\n".join(lines).rstrip(),
            error=None if any_success else "all subagents failed",
            metadata={
                "log_arguments": {
                    "batch_id": batch_id,
                    "child_count": len(tasks),
                }
            },
        )
