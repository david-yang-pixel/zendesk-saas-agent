"""基于 LLMProvider 与 MessageStore 的 Agent 主循环（AsyncGenerator 事件驱动）。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from openai import APIConnectionError, APITimeoutError, BadRequestError

try:
    from anthropic import RateLimitError as AnthropicRateLimitError
    from anthropic import APIConnectionError as AnthropicConnectionError
    from anthropic import APITimeoutError as AnthropicTimeoutError
except ImportError:
    AnthropicRateLimitError = None  # type: ignore[assignment,misc]
    AnthropicConnectionError = None  # type: ignore[assignment,misc]
    AnthropicTimeoutError = None  # type: ignore[assignment,misc]

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
_CONNECTION_MAX_RETRIES = 2
_CONNECTION_BASE_DELAY = 5  # 网络瞬时抖动通常 5s 内恢复


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, OpenAIRateLimitError):
        return True
    if AnthropicRateLimitError is not None and isinstance(exc, AnthropicRateLimitError):
        return True
    return False


def _is_connection_error(exc: Exception) -> bool:
    """OpenAI / Anthropic 的网络瞬时错误（连接 reset / TLS / timeout）。"""
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if AnthropicConnectionError is not None and isinstance(exc, AnthropicConnectionError):
        return True
    if AnthropicTimeoutError is not None and isinstance(exc, AnthropicTimeoutError):
        return True
    # generic Connection error / TimeoutError 字符串兜底
    name = type(exc).__name__
    if name in ("ConnectionError", "TimeoutError", "ReadTimeout", "ConnectTimeout"):
        return True
    msg = str(exc).lower()
    return "connection error" in msg or "connection reset" in msg


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

            # 计算 message_store 当前 token 数（不算很贵，每轮一次）—
            # 用于"修法 2"基于 message_store 大小的提前 compaction 触发。
            from zd_agent.runtime.compaction import count_tokens as _count_msg_tokens
            ms_tokens = _count_msg_tokens(message_store.to_api_messages())

            if guardrails.should_compact(message_store_tokens=ms_tokens):
                logger.info(
                    "compaction_triggered",
                    ticket_id=session.ticket_id,
                    message_store_tokens=ms_tokens,
                    cumulative_tokens=guardrails.total_tokens,
                )
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
                """LLM 调用，含 rate-limit 和 connection 错误重试。

                之前只 retry rate_limit，OpenAI Connection error 一次就挂导致
                整个 session reply_sent=False（#2664 case）。现在两类瞬时错误
                都重试：
                - rate_limit: 最多 3 次，指数 base=30s（Anthropic 配额需更长等待）
                - connection: 最多 2 次，指数 base=5s（网络瞬时抖动通常 < 5s 恢复）
                """
                last_exc: Exception | None = None
                rate_attempts = 0
                conn_attempts = 0
                max_total = (
                    _RATE_LIMIT_MAX_RETRIES + _CONNECTION_MAX_RETRIES + 1
                )
                for _ in range(max_total):
                    try:
                        return await provider.chat(
                            message_store.to_api_messages(),
                            tools=tool_schemas,
                            temperature=0.2,
                        )
                    except Exception as e:
                        last_exc = e
                        if _is_rate_limit_error(e) and rate_attempts < _RATE_LIMIT_MAX_RETRIES:
                            rate_attempts += 1
                            delay = _get_retry_after(e)
                            logger.warning(
                                "rate_limit_retry",
                                ticket_id=session.ticket_id,
                                attempt=rate_attempts,
                                delay=delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        if _is_connection_error(e) and conn_attempts < _CONNECTION_MAX_RETRIES:
                            conn_attempts += 1
                            delay = _CONNECTION_BASE_DELAY * (2 ** (conn_attempts - 1))
                            logger.warning(
                                "connection_error_retry",
                                ticket_id=session.ticket_id,
                                attempt=conn_attempts,
                                delay=delay,
                                error=str(e)[:200],
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

        # 修法 4：强制总结时**仍允许调用 reply 工具**（内部 + 公开），
        # 让 agent 即使 guardrail 触发也能至少写出一条 reply 给客户 / SE
        # 看到结论，不再丢回复。其它工具（subagent / SigNoz / SQL / etc）
        # 仍禁用，避免循环。
        _REPLY_TOOL_NAMES = {"zendesk_internal_reply", "zendesk_public_reply"}
        reply_only_schemas = [
            s for s in (tool_schemas or [])
            if s.name in _REPLY_TOOL_NAMES
        ] or None  # 万一 registry 没注册（如 dry test）则用 None

        try:
            message_store.append_user(
                f"[系统] {e.reason}。请基于目前收集到的所有信息，"
                "立即输出你的最终分析。"
                "**如有具体方案 → 必须调 zendesk_public_reply 把 reply 发给客户**；"
                "**如还需 SE 兜底 → 调 zendesk_internal_reply 写 Internal Note 让 SE 接手**。"
                "不要再调其它工具（SigNoz / SQL / subagent 等都已禁用）。"
            )
            summary_start = time.monotonic()
            try:
                summary_response = await provider.chat(
                    message_store.to_api_messages(),
                    tools=reply_only_schemas,
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
                    tools=reply_only_schemas,
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
                tool_call_count=(
                    len(summary_response.tool_calls)
                    if summary_response.tool_calls else 0
                ),
                assistant_content=summary_response.content,
                stop_reason="guardrail_summary",
            )

            if summary_response.content:
                session.analysis += f"\n{summary_response.content}"
                # 修法 e1: subagent 通过 run_subagent 从 message_store.raw 尾部
                # extract assistant content 作为 final_content。之前 forced_summary
                # 路径只把 content 加到 session.analysis 没 append_assistant →
                # subagent 返回 output_chars=0，父 agent 拿不到结论。
                # 现在也 append_assistant，让 subagent extract 拿到正确内容。
                # （注意如果有 tool_calls 下面会再 append 一次包含 tool_calls
                # 的 dict — 那种情况优先 tool_calls 版本，所以这里只在没 tool
                # call 时 append。）
                if not summary_response.tool_calls:
                    message_store.append_assistant(_assistant_message_dict(summary_response))

            # 修法 4: 如果 LLM 在 forced_summary 时调了 reply 工具（we 允许
            # 了 zendesk_internal_reply / zendesk_public_reply），**执行它**。
            # 否则之前 tools=None agent 没法 reply → 客户/SE 啥都看不到。
            if summary_response.tool_calls:
                message_store.append_assistant(_assistant_message_dict(summary_response))
                for tc in summary_response.tool_calls:
                    if tc.name not in _REPLY_TOOL_NAMES:
                        # 防御：万一 LLM 调了非 reply 工具（schema 应该不允许）
                        logger.warning(
                            "guardrail_summary_unexpected_tool",
                            ticket_id=session.ticket_id, tool=tc.name,
                        )
                        message_store.append_tool_result(
                            tc.id, tc.name,
                            "Tool unavailable in forced_summary mode.",
                        )
                        continue
                    try:
                        tc_result = await registry.execute(
                            tc.name, tc.arguments, session,
                        )
                        out = tc_result.output if tc_result.success else (
                            f"❌ {tc_result.error}"
                        )
                        message_store.append_tool_result(tc.id, tc.name, out)
                        logger.info(
                            "guardrail_summary_reply_sent",
                            ticket_id=session.ticket_id,
                            tool=tc.name,
                            success=tc_result.success,
                        )
                    except Exception as exc:
                        logger.error(
                            "guardrail_summary_reply_failed",
                            ticket_id=session.ticket_id,
                            tool=tc.name, error=str(exc),
                        )

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

        # 兜底救援：即使 generic Exception 也试一次写 Internal Note 让 SE
        # 看到失败 + 已收集到的信息，不要让客户工单完全没痕迹。
        # 失败本身也吞掉，不让兜底救援的 error 覆盖原始 error 信息。
        try:
            _REPLY_TOOL_NAMES_LOCAL = {"zendesk_internal_reply"}
            reply_only_schemas = [
                s for s in (tool_schemas or [])
                if s.name in _REPLY_TOOL_NAMES_LOCAL
            ] or None
            if reply_only_schemas:
                message_store.append_user(
                    f"[系统] 处理过程出现异常({type(e).__name__}: {str(e)[:200]})。"
                    "请基于已收集到的信息立即写一条 zendesk_internal_reply 让 SE "
                    "看到当前结论 + 失败原因，body 简洁不超过 200 字。"
                )
                fallback_resp = await provider.chat(
                    message_store.to_api_messages(),
                    tools=reply_only_schemas,
                    temperature=0.2,
                )
                if fallback_resp.tool_calls:
                    for tc in fallback_resp.tool_calls:
                        if tc.name in _REPLY_TOOL_NAMES_LOCAL:
                            try:
                                await registry.execute(
                                    tc.name, tc.arguments, session,
                                )
                                logger.info(
                                    "agent_loop_error_fallback_reply_sent",
                                    ticket_id=session.ticket_id, tool=tc.name,
                                )
                            except Exception:
                                pass
        except Exception as fallback_err:
            logger.warning(
                "agent_loop_error_fallback_failed",
                ticket_id=session.ticket_id,
                fallback_error=str(fallback_err)[:200],
            )

        yield LoopFinished(
            status="error",
            total_rounds=guardrails.current_round,
            total_tokens=guardrails.total_tokens,
            elapsed_seconds=guardrails.elapsed_seconds,
            reason=str(e),
        )
