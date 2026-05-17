"""基于 LLMProvider 与 MessageStore 的 Agent 主循环（AsyncGenerator 事件驱动）。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from openai import BadRequestError

try:
    from anthropic import RateLimitError as AnthropicRateLimitError
except ImportError:
    AnthropicRateLimitError = None  # type: ignore[assignment,misc]

from openai import RateLimitError as OpenAIRateLimitError

from zd_agent.log import get_logger
from zd_agent.runtime.compaction import multi_level_compact
from zd_agent.runtime.events import AgentEvent, LoopFinished, LoopProgress, ToolExecuted
from zd_agent.runtime.guardrails import GuardrailTriggered, Guardrails
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import ToolRegistry

logger = get_logger(module="agent_loop")

MAX_IMAGES = 10
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 30  # Anthropic 每分钟限额，等 30s 比较安全


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, OpenAIRateLimitError):
        return True
    if AnthropicRateLimitError is not None and isinstance(exc, AnthropicRateLimitError):
        return True
    return False


def _get_retry_after(exc: Exception) -> float:
    """从 response header 中提取 retry-after 秒数，取不到则用默认值。"""
    resp = getattr(exc, "response", None)
    if resp is not None:
        header = getattr(resp, "headers", {}).get("retry-after")
        if header:
            try:
                return max(float(header), 1.0)
            except (ValueError, TypeError):
                pass
    return _RATE_LIMIT_BASE_DELAY


def _is_image_url_error(exc: Exception) -> bool:
    """判断是否为 LLM 下载图片 URL 超时/失败的错误（OpenAI code=invalid_image_url）。"""
    if isinstance(exc, BadRequestError) and getattr(exc, "code", None) == "invalid_image_url":
        return True
    err_str = str(exc).lower()
    return "invalid_image_url" in err_str


def _strip_images_from_messages(messages: list[dict[str, Any]]) -> int:
    """移除消息中所有 image_url 块，返回移除的图片数量。"""
    removed = 0
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        new_content: list[dict[str, Any]] = []
        img_count = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                img_count += 1
            else:
                new_content.append(part)
        if img_count == 0:
            continue
        removed += img_count
        new_content.append({
            "type": "text",
            "text": f"（⚠️ {img_count} 张截图因网络超时无法加载，请仅基于文字内容分析问题）",
        })
        msg["content"] = new_content
    return removed


def _assistant_message_dict(response: LLMResponse) -> dict[str, Any]:
    """将 assistant 轮次转为可存入 MessageStore 的 dict（优先使用 provider 的 raw）。"""
    if isinstance(response.raw, dict):
        return response.raw
    msg: dict[str, Any] = {"role": "assistant", "content": response.content}
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in response.tool_calls
        ]
    return msg


async def _inject_images(session: AgentSession, message_store: MessageStore) -> None:
    """将工单截图以多模态 user 消息注入上下文。

    必须把图**预下载**成 data URL 注入。原始 URL（特别是
    Zendesk Sunshine ``/sc/attachments/v2/...``）OpenAI 那边
    fetch 会 401 —— 客户截图本质看不到。
    """
    raw_images = session.image_urls[:MAX_IMAGES]
    if not raw_images:
        return

    # Lazy import to avoid circular saas_app ↔ runtime
    from zd_agent.entry.saas_app import _download_image_data_url

    # 并发下载，跳过失败的（不阻断 dispatch）
    results = await asyncio.gather(
        *(_download_image_data_url(img["url"]) for img in raw_images),
        return_exceptions=False,
    )
    data_urls = [(img, dl) for img, dl in zip(raw_images, results) if dl]

    if not data_urls:
        logger.warning(
            "images_inject_all_failed",
            ticket_id=session.ticket_id,
            attempted=len(raw_images),
        )
        return

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"以下是工单中客户提交的 {len(data_urls)} 张截图（已预下载并以 base64 内嵌），"
                "请仔细查看并结合对话内容分析问题："
            ),
        },
    ]
    for img, durl in data_urls:
        content.append({
            "type": "image_url",
            "image_url": {"url": durl, "detail": "low"},
        })
    message_store.append_user(content)
    logger.info(
        "images_injected",
        ticket_id=session.ticket_id,
        count=len(data_urls),
        attempted=len(raw_images),
        truncated=len(session.image_urls) > MAX_IMAGES,
    )


_LANG_NAMES = {"zh": "中文", "en": "English", "ja": "日本語"}


def _inject_language_reminder(session: AgentSession, message_store: MessageStore) -> None:
    """检测到客户语言后，注入一次性语言提醒到对话中（仅作为参考建议）。"""
    if getattr(session, "_language_reminder_injected", False):
        return
    ctx = getattr(session, "context", None)
    if ctx is None or not ctx.customer_language:
        return
    lang = ctx.customer_language
    label = _LANG_NAMES.get(lang, lang)
    message_store.append_user(
        f"[系统提醒] 基于字符统计的初步检测，客户可能使用 {label} 沟通。"
        f"请你结合对话全文独立判断客户实际使用的语言，"
        f"并使用客户的语言撰写 Internal Note 正文内容"
        f"（Suggested Reply、Internal Analysis、Review Guide 各段内容）。"
        f"section 标题保持英文不变，代码块和命令行内容不受此限制。"
    )
    session._language_reminder_injected = True  # type: ignore[attr-defined]
    logger.info(
        "language_reminder_injected",
        ticket_id=session.ticket_id,
        language=lang,
    )


async def agent_loop(
    session: AgentSession,
    provider: LLMProvider,
    message_store: MessageStore,
    registry: ToolRegistry,
    guardrails: Guardrails,
    tool_schemas: list[ToolSchema],
) -> AsyncGenerator[AgentEvent, None]:
    """LLM ↔ 工具循环，通过 yield 产出进度事件（不打印控制台）。"""
    session.status = SessionStatus.RUNNING

    try:
        while True:
            guardrails.check_all()

            if guardrails.should_compact():
                logger.info("compaction_triggered", ticket_id=session.ticket_id)
                # 用 multi_level_compact：先 Level 1 history_snip（cheap，截 tool
                # 输出到 200 字符）；如果还不够再 Level 2 LLM 摘要。之前直接调
                # Level 2 在已经短的内容上跑 LLM，反而 saved_tokens < 0（观察
                # 服务器日志: -18 / -12 / -23）。
                compacted = await multi_level_compact(
                    message_store.to_api_messages(),
                    provider,
                )
                message_store.raw = compacted

            guardrails.increment_round()
            session.round_count = guardrails.current_round

            llm_start = time.monotonic()

            async def _chat_with_retry() -> LLMResponse:
                """带 rate-limit 重试的 LLM 调用。"""
                last_exc: Exception | None = None
                for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
                    try:
                        return await provider.chat(
                            message_store.to_api_messages(),
                            tools=tool_schemas,
                            temperature=0.2,
                        )
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            last_exc = e
                            if attempt < _RATE_LIMIT_MAX_RETRIES:
                                delay = _get_retry_after(e)
                                logger.warning(
                                    "rate_limit_retry",
                                    ticket_id=session.ticket_id,
                                    attempt=attempt + 1,
                                    delay=delay,
                                )
                                await asyncio.sleep(delay)
                                continue
                        raise
                raise last_exc  # type: ignore[misc]

            try:
                response = await _chat_with_retry()
            except Exception as chat_err:
                if not _is_image_url_error(chat_err):
                    raise
                removed = _strip_images_from_messages(message_store.raw)
                logger.warning(
                    "image_url_fallback",
                    ticket_id=session.ticket_id,
                    removed_count=removed,
                    error=str(chat_err),
                )
                response = await _chat_with_retry()
            llm_elapsed = time.monotonic() - llm_start

            guardrails.add_tokens(response.prompt_tokens, response.completion_tokens)
            session.token_usage.prompt_tokens += response.prompt_tokens
            session.token_usage.completion_tokens += response.completion_tokens

            yield LoopProgress(
                round=guardrails.current_round,
                message_count=len(message_store),
                token_count=guardrails.total_tokens,
                llm_elapsed=llm_elapsed,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                tool_call_count=len(response.tool_calls),
                assistant_content=response.content,
                stop_reason=response.finish_reason,
            )

            message_store.append_assistant(_assistant_message_dict(response))

            if not response.has_tool_calls:
                if response.content:
                    session.analysis += f"\n{response.content}"
                session.status = SessionStatus.COMPLETED
                yield LoopFinished(
                    status="completed",
                    total_rounds=guardrails.current_round,
                    total_tokens=guardrails.total_tokens,
                    elapsed_seconds=guardrails.elapsed_seconds,
                    reason="",
                )
                return

            async def _run_tool(tc: ToolCallRequest) -> ToolExecuted:
                t0 = time.monotonic()
                result = await registry.execute(tc.name, tc.arguments, session)
                elapsed = time.monotonic() - t0
                output = result.output if result.success else f"❌ 错误: {result.error}"
                try:
                    args_dict = json.loads(tc.arguments) if tc.arguments else {}
                except json.JSONDecodeError:
                    args_dict = {"raw": tc.arguments}
                session.log_tool_call(
                    tc.name,
                    args_dict,
                    output,
                    success=result.success,
                    error=result.error,
                )
                message_store.append_tool_result(tc.id, tc.name, output)
                return ToolExecuted(
                    round=guardrails.current_round,
                    tool_name=tc.name,
                    success=result.success,
                    output_length=len(output),
                    elapsed=elapsed,
                    error=result.error,
                    arguments=args_dict,
                    output=output[:4000],
                )

            tasks = [_run_tool(tc) for tc in response.tool_calls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, r in enumerate(results):
                tc = response.tool_calls[i]
                if isinstance(r, Exception):
                    err_text = f"❌ 工具执行异常: {r}"
                    message_store.append_tool_result(tc.id, tc.name, err_text)
                    logger.error(
                        "tool_execution_error",
                        ticket_id=session.ticket_id,
                        tool=tc.name,
                        error=str(r),
                    )
                    yield ToolExecuted(
                        round=guardrails.current_round,
                        tool_name=tc.name,
                        success=False,
                        output_length=len(err_text),
                        elapsed=0.0,
                        error=str(r),
                    )
                else:
                    yield r

            if session.image_urls and not session.images_injected:
                await _inject_images(session, message_store)
                session.images_injected = True

            _inject_language_reminder(session, message_store)

    except GuardrailTriggered as e:
        logger.warning(
            "guardrail_triggered",
            ticket_id=session.ticket_id,
            reason=e.reason,
            rounds=guardrails.current_round,
            total_tokens=guardrails.total_tokens,
        )

        # 强制总结：不传工具，让 LLM 基于已有上下文输出最终分析
        try:
            message_store.append_user(
                f"[系统] {e.reason}。请基于目前收集到的所有信息，"
                "立即输出你的最终分析和回复建议，不要再调用任何工具。"
            )
            summary_start = time.monotonic()
            try:
                summary_response = await provider.chat(
                    message_store.to_api_messages(),
                    tools=None,
                    temperature=0.2,
                )
            except Exception as chat_err:
                if not _is_image_url_error(chat_err):
                    raise
                _strip_images_from_messages(message_store.raw)
                logger.warning(
                    "image_url_fallback_summary",
                    ticket_id=session.ticket_id,
                    error=str(chat_err),
                )
                summary_response = await provider.chat(
                    message_store.to_api_messages(),
                    tools=None,
                    temperature=0.2,
                )
            summary_elapsed = time.monotonic() - summary_start

            guardrails.add_tokens(summary_response.prompt_tokens, summary_response.completion_tokens)
            session.token_usage.prompt_tokens += summary_response.prompt_tokens
            session.token_usage.completion_tokens += summary_response.completion_tokens

            yield LoopProgress(
                round=guardrails.current_round + 1,
                message_count=len(message_store),
                token_count=guardrails.total_tokens,
                llm_elapsed=summary_elapsed,
                prompt_tokens=summary_response.prompt_tokens,
                completion_tokens=summary_response.completion_tokens,
                tool_call_count=0,
                assistant_content=summary_response.content,
                stop_reason="guardrail_summary",
            )

            if summary_response.content:
                session.analysis += f"\n{summary_response.content}"

            session.status = SessionStatus.COMPLETED
            yield LoopFinished(
                status="completed",
                total_rounds=guardrails.current_round + 1,
                total_tokens=guardrails.total_tokens,
                elapsed_seconds=guardrails.elapsed_seconds,
                reason=f"护栏触发后强制总结: {e.reason}",
            )
            logger.info(
                "guardrail_summary_completed",
                ticket_id=session.ticket_id,
                original_reason=e.reason,
                summary_tokens=summary_response.prompt_tokens + summary_response.completion_tokens,
            )
        except Exception as summary_err:
            logger.error(
                "guardrail_summary_failed",
                ticket_id=session.ticket_id,
                original_reason=e.reason,
                summary_error=str(summary_err),
            )
            if "超时" in e.reason:
                session.status = SessionStatus.TIMEOUT
            else:
                session.status = SessionStatus.FAILED
            session.error_message = e.reason
            session.analysis += f"\n\n⚠️ 处理被护栏终止: {e.reason}"
            yield LoopFinished(
                status="guardrail",
                total_rounds=guardrails.current_round,
                total_tokens=guardrails.total_tokens,
                elapsed_seconds=guardrails.elapsed_seconds,
                reason=e.reason,
            )

    except Exception as e:
        logger.error(
            "agent_loop_error",
            ticket_id=session.ticket_id,
            error=str(e),
            exc_info=True,
        )
        session.status = SessionStatus.FAILED
        session.error_message = str(e)
        session.analysis += f"\n\n❌ 处理异常: {e}"
        yield LoopFinished(
            status="error",
            total_rounds=guardrails.current_round,
            total_tokens=guardrails.total_tokens,
            elapsed_seconds=guardrails.elapsed_seconds,
            reason=str(e),
        )
