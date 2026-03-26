"""ToolLoop：LLM 驱动的工具循环核心引擎

借鉴 OpenClaw 的 runEmbeddedPiAgent 设计：
LLM 作为决策主体，在循环中自主调用工具，直到任务完成或护栏触发。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from rich.console import Console
from rich.text import Text

from zd_agent.log import get_logger
from zd_agent.runtime.compaction import compact_messages, count_tokens
from zd_agent.runtime.guardrails import GuardrailTriggered, Guardrails
from zd_agent.runtime.llm_client import LLMClient, LLMResponse
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import ToolRegistry

logger = get_logger(module="tool_loop")
_console = Console(highlight=False)


def _format_args_summary(raw_args: str, max_len: int = 80) -> str:
    """将 tool call 的 JSON 参数格式化为简短摘要"""
    if not raw_args:
        return ""
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        return raw_args[:max_len]
    parts: list[str] = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:37] + "..."
        parts.append(f"{k}={sv}")
    summary = ", ".join(parts)
    if len(summary) > max_len:
        summary = summary[: max_len - 3] + "..."
    return summary


async def run_tool_loop(
    session: AgentSession,
    llm: LLMClient,
    registry: ToolRegistry,
    guardrails: Guardrails,
) -> None:
    """执行工具循环：LLM ↔ Tools 交互直到任务完成

    循环流程：
    1. 调用 LLM（带 messages + tools 定义）
    2. LLM 返回 tool_calls → 并行执行工具 → 结果回填 messages
    3. LLM 返回纯文本（无 tool_calls）→ 任务完成，退出循环
    4. 每轮检查护栏（轮次/超时/token）
    5. 需要时触发 compaction 压缩上下文
    """
    tools_schema = registry.to_openai_tools()

    session.status = SessionStatus.RUNNING
    logger.info(
        "tool_loop_start",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        tool_count=len(tools_schema),
    )

    _console.print(
        f"\n[bold]🔄 ToolLoop 启动[/bold]  "
        f"ticket=[cyan]#{session.ticket_id}[/cyan]  "
        f"tools=[dim]{len(tools_schema)}[/dim]"
    )

    try:
        while True:
            # 护栏检查
            guardrails.check_all()

            if guardrails.is_approaching_limit():
                logger.warning(
                    "approaching_round_limit",
                    ticket_id=session.ticket_id,
                    round=f"{guardrails.current_round}/{guardrails.config.max_rounds}",
                )

            # Compaction 检查
            if guardrails.should_compact():
                logger.info("compaction_triggered", ticket_id=session.ticket_id)
                _console.print("  [yellow]⚡ Compaction 触发 — 压缩上下文[/yellow]")
                session.messages = await compact_messages(session.messages, llm)

            # 调用 LLM
            guardrails.increment_round()
            session.round_count = guardrails.current_round

            _console.print(
                f"\n[bold dim]{'─' * 50}[/bold dim]\n"
                f"[bold]📍 Round {guardrails.current_round}[/bold]  "
                f"messages=[dim]{len(session.messages)}[/dim]  "
                f"tokens=[dim]{guardrails.total_tokens:,}[/dim]"
            )

            logger.info(
                "llm_call",
                ticket_id=session.ticket_id,
                round=guardrails.current_round,
                message_count=len(session.messages),
                tokens_so_far=guardrails.total_tokens,
            )

            llm_start = time.monotonic()
            response: LLMResponse = await llm.chat(session.messages, tools_schema)
            llm_elapsed = time.monotonic() - llm_start

            # 更新 token 统计
            guardrails.add_tokens(response.prompt_tokens, response.completion_tokens)
            session.token_usage.prompt_tokens += response.prompt_tokens
            session.token_usage.completion_tokens += response.completion_tokens

            _console.print(
                f"  [dim]LLM 响应[/dim]  "
                f"{llm_elapsed:.1f}s  "
                f"prompt=[dim]{response.prompt_tokens:,}[/dim]  "
                f"completion=[dim]{response.completion_tokens:,}[/dim]  "
                f"finish=[dim]{response.finish_reason}[/dim]"
            )

            # LLM 的思考/内容（非 tool_calls 部分）
            if response.content and response.has_tool_calls:
                preview = response.content[:200]
                if len(response.content) > 200:
                    preview += "..."
                _console.print(f"  [italic dim]💭 {preview}[/italic dim]")

            # 将 assistant 消息追加到历史
            session.messages.append(response.raw_message)

            # 无 tool_calls → LLM 认为任务完成
            if not response.has_tool_calls:
                if response.content:
                    session.analysis += f"\n{response.content}"
                _console.print(
                    f"\n[bold green]✅ ToolLoop 完成[/bold green]  "
                    f"rounds=[cyan]{guardrails.current_round}[/cyan]  "
                    f"tokens=[cyan]{guardrails.total_tokens:,}[/cyan]  "
                    f"elapsed=[cyan]{guardrails.elapsed_seconds:.1f}s[/cyan]"
                )
                logger.info(
                    "tool_loop_complete",
                    ticket_id=session.ticket_id,
                    rounds=guardrails.current_round,
                    total_tokens=guardrails.total_tokens,
                    elapsed=f"{guardrails.elapsed_seconds:.1f}s",
                )
                session.status = SessionStatus.COMPLETED
                return

            # 有 tool_calls → 显示即将调用的工具
            tc_names = [tc.function.name for tc in response.tool_calls]
            if len(tc_names) > 1:
                _console.print(
                    f"  [bold]🔧 并行调用 {len(tc_names)} 个工具:[/bold] "
                    + ", ".join(f"[cyan]{n}[/cyan]" for n in tc_names)
                )
            await _execute_tool_calls(
                response.tool_calls, session, registry, guardrails
            )

            # 图片注入：工具执行后如果有新收集到的图片 URL，追加多模态 user 消息
            if session.image_urls and not session.images_injected:
                _inject_images(session)
                _console.print(
                    f"  [bold magenta]🖼  注入 {len(session.image_urls[:MAX_IMAGES])} 张截图到上下文[/bold magenta]"
                )
                session.images_injected = True

    except GuardrailTriggered as e:
        _console.print(f"\n[bold yellow]⚠️  护栏触发: {e.reason}[/bold yellow]")
        logger.warning(
            "guardrail_triggered",
            ticket_id=session.ticket_id,
            reason=e.reason,
            rounds=guardrails.current_round,
            total_tokens=guardrails.total_tokens,
        )
        if "超时" in e.reason:
            session.status = SessionStatus.TIMEOUT
        else:
            session.status = SessionStatus.FAILED
        session.error_message = e.reason
        session.analysis += f"\n\n⚠️ 处理被护栏终止: {e.reason}"

    except Exception as e:
        _console.print(f"\n[bold red]❌ 异常: {e}[/bold red]")
        logger.error(
            "tool_loop_error",
            ticket_id=session.ticket_id,
            error=str(e),
            exc_info=True,
        )
        session.status = SessionStatus.FAILED
        session.error_message = str(e)
        session.analysis += f"\n\n❌ 处理异常: {e}"


async def _execute_tool_calls(
    tool_calls: list[Any],
    session: AgentSession,
    registry: ToolRegistry,
    guardrails: Guardrails,
) -> None:
    """并行执行多个工具调用，将结果追加到 messages"""

    async def _run_one(tc: Any) -> dict[str, Any]:
        fn = tc.function
        tool_name = fn.name
        tool_id = tc.id

        args_summary = _format_args_summary(fn.arguments)
        _console.print(
            f"  [cyan]▶ {tool_name}[/cyan]"
            + (f"  [dim]({args_summary})[/dim]" if args_summary else "")
        )

        logger.info(
            "tool_call",
            ticket_id=session.ticket_id,
            tool=tool_name,
            round=guardrails.current_round,
        )

        t0 = time.monotonic()
        result = await registry.execute(tool_name, fn.arguments, session)
        elapsed = time.monotonic() - t0

        output = result.output if result.success else f"❌ 错误: {result.error}"

        # 记录到 session 日志
        try:
            args_dict = json.loads(fn.arguments) if fn.arguments else {}
        except json.JSONDecodeError:
            args_dict = {"raw": fn.arguments}
        session.log_tool_call(
            tool_name,
            args_dict,
            output,
            success=result.success,
            error=result.error,
        )

        # Rich 输出：结果状态
        if result.success:
            size_info = _format_output_size(len(output))
            _console.print(
                f"  [green]✓ {tool_name}[/green]  "
                f"[dim]{elapsed:.1f}s  {size_info}[/dim]"
            )
        else:
            err_preview = (result.error or "unknown")[:120]
            _console.print(
                f"  [red]✗ {tool_name}[/red]  "
                f"[dim]{elapsed:.1f}s[/dim]  [red]{err_preview}[/red]"
            )

        logger.info(
            "tool_result",
            ticket_id=session.ticket_id,
            tool=tool_name,
            success=result.success,
            output_length=len(output),
        )

        return {
            "role": "tool",
            "tool_call_id": tool_id,
            "name": tool_name,
            "content": output,
        }

    # 并行执行所有工具调用
    tasks = [_run_one(tc) for tc in tool_calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, r in enumerate(results):
        if isinstance(r, Exception):
            tc_id = tool_calls[i].id if i < len(tool_calls) else "unknown"
            tc_name = tool_calls[i].function.name if i < len(tool_calls) else "unknown"
            logger.error("tool_execution_error", tool=tc_name, error=str(r))
            _console.print(f"  [bold red]💥 {tc_name} 异常: {r}[/bold red]")
            session.messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "name": tc_name,
                "content": f"❌ 工具执行异常: {r}",
            })
        else:
            session.messages.append(r)  # type: ignore[arg-type]


def _format_output_size(length: int) -> str:
    """将输出长度格式化为人类可读"""
    if length < 1024:
        return f"{length} chars"
    elif length < 1024 * 1024:
        return f"{length / 1024:.1f} KB"
    return f"{length / (1024 * 1024):.1f} MB"


MAX_IMAGES = 10


def _inject_images(session: AgentSession) -> None:
    """将收集到的图片 URL 作为多模态 user 消息注入到对话历史

    使用 image_url 方式传递，OpenAI 服务端直接拉取图片。
    detail 设为 low 以节省 token（每张固定 85 token）。
    """
    images = session.image_urls[:MAX_IMAGES]
    if not images:
        return

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"以下是工单中客户提交的 {len(images)} 张截图，请仔细查看并结合对话内容分析问题：",
        },
    ]

    for img in images:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": img["url"],
                "detail": "low",
            },
        })

    session.messages.append({
        "role": "user",
        "content": content,
    })

    logger.info(
        "images_injected",
        ticket_id=session.ticket_id,
        count=len(images),
        truncated=len(session.image_urls) > MAX_IMAGES,
    )
