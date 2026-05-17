"""RunSubagentsParallel: dispatch N subagents concurrently via asyncio.gather."""
from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


class RunSubagentsParallel(Tool):
    name = "run_subagents_parallel"
    description = (
        "一次并行派发多个独立 subagent。每个 task 拥有独立上下文，互不污染。\n\n"
        "**Subagent 预算规则（每次工单处理累计）**：\n"
        "- 简单问题（usage-howto / product_usage / 文档引用 / 单一已知 bug）：\n"
        "  全工单累计 **≤ 2 个** subagent。直接搜 KB 即可，不要 fan-out。\n"
        "- 中等问题（billing / refund / api / permissions / knowledge_base）：\n"
        "  分两阶段，每阶段 **≤ 3 个**（根因 3 个 + 知识匹配 3 个 = 累计 6 个）。\n"
        "- 复杂问题（platform_issue / workflow / 多组件交互）：\n"
        "  分两阶段，每阶段 **≤ 4 个**（根因 4 个 + 知识匹配 4 个 = 累计 8 个）。\n\n"
        "**反例**：客户问'能不能导出日志' 这种 yes/no 问题不要 fan-out 6 个 subagent —"
        "用 1 个 subagent 查 KB 即可。预算用完后再补充派发会让 token 爆。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": (
                    "要并行派发的子任务列表。**按工单复杂度选数量**（见 description）：\n"
                    "simple ≤2 / medium ≤3 / complex ≤4，单次调用最多 4 个 task。"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "任务描述",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "允许该 subagent 使用的工具名；留空用默认子集",
                        },
                        "context": {
                            "type": "string",
                            "description": "传给 subagent 的背景信息",
                        },
                    },
                    "required": ["task"],
                },
                "minItems": 1,
                "maxItems": 4,
            }
        },
        "required": ["tasks"],
    }
    group = "always"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        tasks = args.get("tasks") or []
        if not tasks:
            return ToolResult(success=False, error="tasks is empty")

        # Late import to avoid circular dependency
        from zd_agent.runtime.subagent import run_subagent

        coros = [
            run_subagent(
                t["task"],
                t.get("tools", []) or [],
                t.get("context", "") or "",
                session,
            )
            for t in tasks
        ]

        results = await asyncio.gather(*coros, return_exceptions=True)

        lines: list[str] = []
        any_success = False
        for i, (spec, res) in enumerate(zip(tasks, results), start=1):
            title = str(spec.get("task", ""))[:80]
            lines.append(f"=== Subagent {i}: {title} ===")
            if isinstance(res, Exception):
                lines.append(f"[ERROR] {type(res).__name__}: {res}")
            elif isinstance(res, ToolResult):
                if res.success:
                    any_success = True
                    lines.append(res.output or "(empty output)")
                else:
                    lines.append(f"[FAILED] {res.error}")
            else:
                any_success = True
                lines.append(str(res))
            lines.append("")

        # Success if at least one subagent returned a successful ToolResult
        return ToolResult(
            success=any_success,
            output="\n".join(lines).rstrip(),
            error=None if any_success else "all subagents failed",
        )
