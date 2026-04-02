"""ToolLoop 兼容包装器：消费 agent_loop 事件，保持现有 Rich 终端输出 + 日志上报。

runner.py 中 `await run_tool_loop(session, llm, registry, guardrails)` 无需改动。
核心循环逻辑已迁移到 agent_loop.py，本文件只负责事件消费 + 日志输出 + 远程上报。
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.events import LoopFinished, LoopProgress, ToolExecuted
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import ToolSchema
from zd_agent.runtime.llm_client import LLMClient
from zd_agent.runtime.log_reporter import LogReporter
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry

logger = get_logger(module="tool_loop")
_console = Console(highlight=False)


def _format_output_size(length: int) -> str:
    if length < 1024:
        return f"{length} chars"
    elif length < 1024 * 1024:
        return f"{length / 1024:.1f} KB"
    return f"{length / (1024 * 1024):.1f} MB"


async def run_tool_loop(
    session: AgentSession,
    llm: LLMClient,
    registry: ToolRegistry,
    guardrails: Guardrails,
    trigger_type: str = "cli",
) -> None:
    """向后兼容入口：构建 agent_loop 所需参数，消费事件并输出 Rich 日志。"""
    provider = llm._provider
    message_store = MessageStore(provider)
    message_store.raw = session.messages
    model_name = get_config().llm.model

    tool_schemas = [
        ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
        for t in registry.list_tools()
    ]

    reporter = LogReporter()
    run_id: int | None = None
    current_round_id: int | None = None

    if reporter.enabled:
        run_id = await reporter.report_run_start(
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            trigger_type=trigger_type,
            model=model_name,
        )

    logger.info(
        "tool_loop_start",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        tool_count=len(tool_schemas),
    )
    _console.print(
        f"\n[bold]🔄 ToolLoop 启动[/bold]  "
        f"ticket=[cyan]#{session.ticket_id}[/cyan]  "
        f"tools=[dim]{len(tool_schemas)}[/dim]"
    )

    try:
        async for event in agent_loop(
            session, provider, message_store, registry, guardrails, tool_schemas,
        ):
            match event:
                case LoopProgress():
                    _console.print(
                        f"\n[bold dim]{'─' * 50}[/bold dim]\n"
                        f"[bold]📍 Round {event.round}[/bold]  "
                        f"messages=[dim]{event.message_count}[/dim]  "
                        f"tokens=[dim]{event.token_count:,}[/dim]  "
                        f"LLM=[dim]{event.llm_elapsed:.1f}s[/dim]  "
                        f"prompt=[dim]{event.prompt_tokens:,}[/dim]  "
                        f"completion=[dim]{event.completion_tokens:,}[/dim]  "
                        f"tools=[dim]{event.tool_call_count}[/dim]"
                    )
                    if run_id is not None:
                        current_round_id = await reporter.report_round(run_id, event, model_name)

                case ToolExecuted() if event.success:
                    size_info = _format_output_size(event.output_length)
                    _console.print(
                        f"  [green]✓ {event.tool_name}[/green]  "
                        f"[dim]{event.elapsed:.1f}s  {size_info}[/dim]"
                    )
                    if run_id is not None and current_round_id is not None:
                        await reporter.report_tool_call(run_id, current_round_id, event)

                case ToolExecuted():
                    err_preview = (event.error or "unknown")[:120]
                    _console.print(
                        f"  [red]✗ {event.tool_name}[/red]  "
                        f"[dim]{event.elapsed:.1f}s[/dim]  [red]{err_preview}[/red]"
                    )
                    if run_id is not None and current_round_id is not None:
                        await reporter.report_tool_call(run_id, current_round_id, event)

                case LoopFinished(status="completed"):
                    if event.reason:
                        _console.print(
                            f"\n[bold yellow]⚠️  {event.reason}[/bold yellow]\n"
                            f"[bold green]✅ ToolLoop 完成（强制总结）[/bold green]  "
                            f"rounds=[cyan]{event.total_rounds}[/cyan]  "
                            f"tokens=[cyan]{event.total_tokens:,}[/cyan]  "
                            f"elapsed=[cyan]{event.elapsed_seconds:.1f}s[/cyan]"
                        )
                    else:
                        _console.print(
                            f"\n[bold green]✅ ToolLoop 完成[/bold green]  "
                            f"rounds=[cyan]{event.total_rounds}[/cyan]  "
                            f"tokens=[cyan]{event.total_tokens:,}[/cyan]  "
                            f"elapsed=[cyan]{event.elapsed_seconds:.1f}s[/cyan]"
                        )
                    logger.info(
                        "tool_loop_complete",
                        ticket_id=session.ticket_id,
                        rounds=event.total_rounds,
                        total_tokens=event.total_tokens,
                        elapsed=f"{event.elapsed_seconds:.1f}s",
                        forced_summary=bool(event.reason),
                    )
                    if run_id is not None:
                        await reporter.report_run_finish(
                            run_id, event,
                            total_input_tokens=session.token_usage.prompt_tokens,
                            total_output_tokens=session.token_usage.completion_tokens,
                        )

                case LoopFinished(status="guardrail"):
                    _console.print(f"\n[bold yellow]⚠️  护栏触发: {event.reason}[/bold yellow]")
                    if run_id is not None:
                        await reporter.report_run_finish(
                            run_id, event,
                            total_input_tokens=session.token_usage.prompt_tokens,
                            total_output_tokens=session.token_usage.completion_tokens,
                        )

                case LoopFinished(status="error"):
                    _console.print(f"\n[bold red]❌ 异常: {event.reason}[/bold red]")
                    if run_id is not None:
                        await reporter.report_run_finish(
                            run_id, event,
                            total_input_tokens=session.token_usage.prompt_tokens,
                            total_output_tokens=session.token_usage.completion_tokens,
                        )
    finally:
        await reporter.close()

    session.messages = message_store.raw
