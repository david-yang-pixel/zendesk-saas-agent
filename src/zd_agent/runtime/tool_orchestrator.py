"""工具编排器：按并发安全性分批执行工具调用。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from zd_agent.log import get_logger
from zd_agent.runtime.events import ToolExecuted
from zd_agent.runtime.llm.types import ToolCallRequest
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry

logger = get_logger(module="tool_orchestrator")


async def orchestrate_tool_calls(
    tool_calls: list[ToolCallRequest],
    registry: ToolRegistry,
    session: AgentSession,
    message_store: MessageStore,
    current_round: int,
) -> AsyncGenerator[ToolExecuted, None]:
    """编排工具执行：并发安全的并行执行，非并发安全的串行执行。"""
    safe_batch: list[ToolCallRequest] = []
    serial_queue: list[ToolCallRequest] = []

    for tc in tool_calls:
        tool = registry.get(tc.name)
        if tool and not tool.concurrency_safe:
            serial_queue.append(tc)
        else:
            safe_batch.append(tc)

    if safe_batch:
        tasks = [
            _run_one(tc, registry, session, message_store, current_round)
            for tc in safe_batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            tc = safe_batch[i]
            if isinstance(r, Exception):
                err_text = f"❌ 工具执行异常: {r}"
                message_store.append_tool_result(tc.id, tc.name, err_text)
                logger.error("tool_execution_error", tool=tc.name, error=str(r))
                yield ToolExecuted(
                    round=current_round,
                    tool_name=tc.name,
                    success=False,
                    output_length=len(err_text),
                    elapsed=0.0,
                    error=str(r),
                )
            else:
                yield r

    for tc in serial_queue:
        try:
            result = await _run_one(
                tc, registry, session, message_store, current_round
            )
            yield result
        except Exception as e:
            err_text = f"❌ 工具执行异常: {e}"
            message_store.append_tool_result(tc.id, tc.name, err_text)
            logger.error("tool_execution_error", tool=tc.name, error=str(e))
            yield ToolExecuted(
                round=current_round,
                tool_name=tc.name,
                success=False,
                output_length=len(err_text),
                elapsed=0.0,
                error=str(e),
            )


async def _run_one(
    tc: ToolCallRequest,
    registry: ToolRegistry,
    session: AgentSession,
    message_store: MessageStore,
    current_round: int,
) -> ToolExecuted:
    """执行单个工具调用。"""
    t0 = time.monotonic()
    result = await registry.execute(tc.name, tc.arguments, session)
    elapsed = time.monotonic() - t0

    output = result.output if result.success else f"❌ 错误: {result.error}"

    try:
        args_dict: dict[str, Any] = (
            json.loads(tc.arguments) if tc.arguments else {}
        )
    except json.JSONDecodeError:
        args_dict = {"raw": tc.arguments}
    session.log_tool_call(
        tc.name, args_dict, output, success=result.success, error=result.error
    )

    message_store.append_tool_result(tc.id, tc.name, output)

    return ToolExecuted(
        round=current_round,
        tool_name=tc.name,
        success=result.success,
        output_length=len(output),
        elapsed=elapsed,
        error=result.error,
    )
