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
        "派 subagent 执行独立分析任务。**默认每次只派 1 个**（串行模式 = 看完上一个再派下一个）"
        "—— 这样 message_store 不会被多个 subagent 同时返回堆积，省 token 且每次决策更精准。\n\n"
        "**Subagent 预算规则（每个工单累计）**：\n"
        "- 简单问题（usage-howto / product_usage / yes-no）：累计 **≤ 2 个** subagent\n"
        "- 中等问题（billing / refund / api / permissions / knowledge_base）：累计 **≤ 4 个**\n"
        "- 复杂问题（platform_issue / workflow / 多组件交互）：累计 **≤ 6 个**\n\n"
        "**操作流程（串行）**：\n"
        "1. 第一次调用：派 1 个最关键的 subagent（如根因源码搜索）\n"
        "2. 看返回结论 → 如果已够答客户，**不要**再派；如果不够，决定下一个派啥\n"
        "3. 重复，直到能写 reply\n\n"
        "**反例**：客户问'能不能导出日志' yes/no 问题，1 个 KB 查询足够，不要派 4 个。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": (
                    "子任务列表。**默认串行模式：每次只传 1 个 task**，"
                    "看完结论再决定是否派下一个（不传则当前轮结束 → 主 agent 评估）。\n"
                    "**只在多个 task 真完全互不依赖且必须同时跑时**才传 ≥2 个"
                    "（如：源码搜 + GitHub PR 查 同时进行）。但单次调用累计 ≤ 3 个。"
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
                # 招法 1: 默认 1，串行派发省 token：
                # - 一次只派 1 个 subagent，看完结果决定下个干啥
                # - 主 loop message_store 不会一下被 4 个 subagent 返回堆爆
                # - 复杂工单要 fan-out 仍可一次传 2-3 个（视情况）
                # - 主 agent 通过多次调本 tool 实现串行（每次 1-2 个 task）
                "maxItems": 3,
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

        # 招法 3: tools 是 required，没传就报错让 LLM 重写。避免 fallback
        # 到全工具集（76 个 schema = 16K tokens 浪费）。
        for i, t in enumerate(tasks):
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
