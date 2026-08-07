"""基于 LLMProvider 与 MessageStore 的 Agent 主循环（AsyncGenerator 事件驱动）。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator
from types import SimpleNamespace
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
from zd_agent.runtime.compaction import (
    count_tokens,
    has_responses_history,
    history_snip,
    multi_level_compact,
)
from zd_agent.runtime.events import AgentEvent, LoopFinished, LoopProgress, ToolExecuted
from zd_agent.runtime.guardrails import GuardrailTriggered, Guardrails
from zd_agent.runtime.investigation import (
    current_investigation_phase,
    evaluate_tool_call,
    filter_tool_schemas,
)
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.runtime.pre_send_check import (
    FreshnessRecovery,
    FreshnessRecoveryAction,
    FreshnessResult,
    pre_send_check_or_abort,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import ToolRegistry

logger = get_logger(module="agent_loop")

MAX_IMAGES = 10
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 30  # Anthropic 每分钟限额，等 30s 比较安全
_CONNECTION_MAX_RETRIES = 2
_CONNECTION_BASE_DELAY = 5  # 网络瞬时抖动通常 5s 内恢复
_PROACTIVE_CONTEXT_SNIP_TOKEN_THRESHOLD = 12_000
_TOOL_CALL_TIMEOUT_SECONDS = 180.0
_SLOW_SQL_FAILURE_SECONDS = 25.0
_MAX_SLOW_SQL_FAILURES = 3
_MAX_FRESHNESS_REBASES = 2
_SUBAGENT_BATCH_TOOL_NAME = "run_subagents_parallel"
_EVIDENCE_CALL_ID_EXCLUDED_TOOLS = frozenset(
    {
        "classify_ticket",
        "plan_investigation",
        "run_subagent",
        "run_subagents_parallel",
        "feishu_push",
        "zendesk_public_reply",
        "zendesk_internal_reply",
    }
)
_EVIDENCE_CALL_ID_EXCLUDED_PREFIXES = ("ticket_record_",)
_SUBAGENT_CONVERGENCE_REMINDER = (
    "[系统提醒] 并行证据收集已完成。请先基于当前已经拿到的 DB / 源码 / 日志 / "
    "历史工单 / 文档证据判断是否足够回复。若关键证据已经覆盖结论，请进入最终"
    "客户回复或内部分析，不要继续大范围 git_grep / github_code_search / SQL 扫描。"
    "只有缺少会改变结论的具体事实时，才追加少量、有明确目标的工具调用，并说明"
    "缺的是什么。"
)
_ERROR_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_ERROR_SECRET_RE = re.compile(
    r"\b(authorization|api[_-]?key|access[_-]?token|token|secret|password)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)


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


def _is_timeout_error(exc: Exception) -> bool:
    """判断是否为请求超时；超时通常适合降级整理上下文后重试。"""
    if isinstance(exc, APITimeoutError):
        return True
    if AnthropicTimeoutError is not None and isinstance(exc, AnthropicTimeoutError):
        return True
    name = type(exc).__name__
    if name in ("TimeoutError", "ReadTimeout", "ConnectTimeout"):
        return True
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg


def _safe_exception_detail(exc: Exception) -> str:
    """Return a useful one-line error detail without URLs or credentials."""
    detail = " ".join(str(exc).split())
    detail = _ERROR_URL_RE.sub("[URL]", detail)
    detail = _ERROR_SECRET_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        detail,
    )
    return (detail or type(exc).__name__)[:240]


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
    return any(
        marker in err_str
        for marker in (
            "invalid_image_url",
            "image input",
            "image_url",
            "images are not supported",
            "does not support images",
            "doesn't support images",
            "does not support vision",
            "multimodal",
            "multi-modal",
            "unsupported content type",
            "unsupported message content",
        )
    )


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
            "text": f"（⚠️ {img_count} 张截图无法被当前模型读取，请仅基于文字内容分析问题）",
        })
        msg["content"] = new_content
    return removed


def _apply_history_snip(message_store: MessageStore) -> tuple[int, int]:
    """对 message_store 做一次 cheap history_snip，返回前后 token 估算。"""
    messages = message_store.to_api_messages()
    before_tokens = count_tokens(messages)
    if has_responses_history(messages):
        return before_tokens, before_tokens
    message_store.raw = history_snip(messages)
    after_tokens = count_tokens(message_store.to_api_messages())
    return before_tokens, after_tokens


def _groom_context_proactively(
    session: AgentSession,
    message_store: MessageStore,
    *,
    message_store_tokens: int,
) -> int:
    """软阈值上下文整理：只裁剪长工具输出，不丢客户原文和截图。"""
    if message_store_tokens < _PROACTIVE_CONTEXT_SNIP_TOKEN_THRESHOLD:
        return message_store_tokens

    before_tokens, after_tokens = _apply_history_snip(message_store)
    if after_tokens < before_tokens:
        logger.info(
            "context_groomed_proactively",
            ticket_id=session.ticket_id,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            saved_tokens=before_tokens - after_tokens,
        )
    return after_tokens


def _groom_context_for_retry(
    session: AgentSession,
    message_store: MessageStore,
    *,
    reason: str,
) -> None:
    """timeout 后的重试整理：去掉图片块并裁剪长工具输出。

    首次请求仍保留截图；只有模型超时后才降级到纯文本上下文，避免
    因图片下载/视觉分析拖垮整单。
    """
    messages = message_store.to_api_messages()
    before_tokens = count_tokens(messages)
    if has_responses_history(messages):
        logger.warning(
            "context_grooming_skipped_responses_history",
            ticket_id=session.ticket_id,
            reason=reason,
            before_tokens=before_tokens,
        )
        return
    removed_images = _strip_images_from_messages(message_store.raw)
    message_store.raw = history_snip(message_store.to_api_messages())
    after_tokens = count_tokens(message_store.to_api_messages())
    logger.warning(
        "context_groomed_for_retry",
        ticket_id=session.ticket_id,
        reason=reason,
        removed_images=removed_images,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        saved_tokens=before_tokens - after_tokens,
    )


def _inject_subagent_convergence_reminder(
    session: AgentSession,
    message_store: MessageStore,
    *,
    executed_tool_names: list[str],
) -> None:
    """After a parallel evidence batch, bias the next round toward synthesis."""
    if _SUBAGENT_BATCH_TOOL_NAME not in executed_tool_names:
        return
    if getattr(session, "_subagent_convergence_reminder_injected", False):
        return
    message_store.append_user(_SUBAGENT_CONVERGENCE_REMINDER)
    session._subagent_convergence_reminder_injected = True  # type: ignore[attr-defined]
    logger.info(
        "subagent_convergence_reminder_injected",
        ticket_id=session.ticket_id,
        round=session.round_count,
    )


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


def _record_slow_sql_failure(
    session: AgentSession,
    *,
    tool_name: str,
    success: bool,
    elapsed: float,
) -> None:
    """Open a session-local circuit after repeated expensive SQL failures."""
    if (
        tool_name != "sql_query"
        or success
        or elapsed < _SLOW_SQL_FAILURE_SECONDS
    ):
        return

    session.slow_sql_failure_count += 1
    if session.slow_sql_failure_count >= _MAX_SLOW_SQL_FAILURES:
        session.slow_sql_circuit_open = True
    logger.warning(
        "slow_sql_failure_recorded",
        ticket_id=session.ticket_id,
        elapsed=elapsed,
        count=session.slow_sql_failure_count,
        circuit_open=session.slow_sql_circuit_open,
    )


async def _apply_freshness_recovery(
    *,
    result: Any,
    session: AgentSession,
    message_store: MessageStore,
    enable_vision_input: bool,
) -> bool:
    """Apply a typed freshness action before another public-reply attempt.

    Customer-message cursors are committed only after recovered text and every
    selected image are present in ``MessageStore``. Images skipped by the
    configured vision limit are called out explicitly, so a private Zendesk
    attachment URL can never masquerade as context the model has actually seen.
    """
    recovery = (getattr(result, "metadata", None) or {}).get(
        "freshness_recovery"
    )
    if not isinstance(recovery, FreshnessRecovery):
        return False

    if recovery.action is FreshnessRecoveryAction.DEFER:
        session.status = SessionStatus.DEFERRED
        session.error_message = recovery.reason or "freshness_check_deferred"
        logger.warning(
            "freshness_check_deferred",
            ticket_id=session.ticket_id,
            reason=session.error_message,
        )
        return True

    if recovery.action is FreshnessRecoveryAction.RETRY_FETCH:
        session.freshness_fetch_error_count += 1
        if session.freshness_fetch_error_count >= 2:
            session.status = SessionStatus.DEFERRED
            session.error_message = recovery.reason or "freshness_fetch_error"
            logger.warning(
                "freshness_fetch_retry_exhausted",
                ticket_id=session.ticket_id,
                attempts=session.freshness_fetch_error_count,
                reason=session.error_message,
            )
            return True
        message_store.append_user(
            "[系统] 发送前无法读取最新 Zendesk 客户消息。当前回复未发送；"
            "只允许再尝试读取一次，若仍失败，本次会话必须延期。"
        )
        logger.warning(
            "freshness_fetch_retry_scheduled",
            ticket_id=session.ticket_id,
            attempt=session.freshness_fetch_error_count,
            reason=recovery.reason,
        )
        return False

    if recovery.action is not FreshnessRecoveryAction.REFRESH_CONTEXT:
        return False
    if not recovery.new_messages or not recovery.next_snapshot_last_id:
        logger.warning(
            "freshness_recovery_invalid",
            ticket_id=session.ticket_id,
            next_snapshot_count=recovery.next_snapshot_count,
            next_snapshot_last_id=recovery.next_snapshot_last_id,
        )
        return False
    if session.freshness_rebase_count >= _MAX_FRESHNESS_REBASES:
        session.status = SessionStatus.DEFERRED
        session.error_message = "freshness_rebase_limit"
        logger.warning(
            "freshness_rebase_limit_reached",
            ticket_id=session.ticket_id,
            rebase_count=session.freshness_rebase_count,
            pending_messages_count=len(recovery.new_messages),
        )
        return True

    rendered_messages = []
    all_late_image_urls: list[str] = []
    seen_late_image_urls: set[str] = set()
    for message in recovery.new_messages:
        rendered_messages.append({
            "id": message.get("id"),
            "created_at": message.get("created_at"),
            "content_type": message.get("content_type") or "text",
            "body": message.get("body") or "",
        })
        for raw_url in message.get("image_urls") or []:
            url = str(raw_url or "").strip()
            if not url or url in seen_late_image_urls:
                continue
            seen_late_image_urls.add(url)
            all_late_image_urls.append(url)

    late_image_urls = all_late_image_urls[:MAX_IMAGES]
    late_images_truncated = len(all_late_image_urls) > MAX_IMAGES

    if late_image_urls and enable_vision_input:
        from zd_agent.services.ticket_messages import _download_image_data_url

        downloaded = await asyncio.gather(
            *(_download_image_data_url(url) for url in late_image_urls),
            return_exceptions=True,
        )
        if any(isinstance(item, Exception) or not item for item in downloaded):
            session.status = SessionStatus.DEFERRED
            session.error_message = "freshness_image_download_failed"
            logger.warning(
                "freshness_image_download_failed",
                ticket_id=session.ticket_id,
                attempted=len(late_image_urls),
            )
            return True

        text = (
            "[系统] 客户在本次分析期间补充了以下新消息。上一版回复已取消；"
            "请先吸收这些内容和截图，再重新生成客户回复。\n"
            + json.dumps(rendered_messages, ensure_ascii=False)
        )
        if late_images_truncated:
            text += (
                f"\n[系统提醒] 客户共补充 {len(all_late_image_urls)} 张图片；"
                f"本轮仅读取前 {MAX_IMAGES} 张，结论不得假设其余图片内容。"
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": str(data_url), "detail": "low"},
            }
            for data_url in downloaded
        )
        message_store.append_user(content)
    else:
        vision_warning = ""
        if late_image_urls and not enable_vision_input:
            vision_warning = (
                f"\n[系统提醒] 客户补充了 {len(all_late_image_urls)} 张图片，"
                "但当前主模型视觉输入已关闭，图片内容尚未读取。"
                "请仅依据文字内容分析；若图片可能改变结论，应向客户索取"
                "文字版报错或关键信息。"
            )
        message_store.append_user(
            "[系统] 客户在本次分析期间补充了以下新消息。上一版回复已取消；"
            "请先吸收这些内容，再重新生成客户回复。\n"
            + json.dumps(rendered_messages, ensure_ascii=False)
            + vision_warning
        )
    from zd_agent.services.ticket_messages import extract_form_data_from_msgs

    form_updates = extract_form_data_from_msgs(list(recovery.new_messages))
    if form_updates:
        session.customer_form_data = {
            **session.customer_form_data,
            **form_updates,
        }
        logger.info(
            "freshness_form_identity_rebased",
            ticket_id=session.ticket_id,
            updated_fields=sorted(form_updates),
            has_form_email=bool(session.customer_form_data.get("email")),
            has_form_workspace_id=bool(
                session.customer_form_data.get("workspace_id")
            ),
        )
    # Cursor advancement is deliberately last: the context must already be in
    # MessageStore before a subsequent send is allowed against the new cursor.
    session.customer_msgs_snapshot_count = recovery.next_snapshot_count
    session.customer_msgs_snapshot_last_id = recovery.next_snapshot_last_id
    session.customer_msgs_snapshot_captured = True
    session.freshness_rebase_count += 1
    session.freshness_fetch_error_count = 0
    logger.info(
        "freshness_context_rebased",
        ticket_id=session.ticket_id,
        new_messages_count=len(recovery.new_messages),
        snapshot_count=recovery.next_snapshot_count,
        snapshot_last_id=recovery.next_snapshot_last_id,
        rebase_count=session.freshness_rebase_count,
    )
    return False


async def _fetch_marked_customer_context(
    session: AgentSession,
) -> tuple[int, FreshnessResult] | None:
    """Resolve one webhook generation against the current Zendesk messages."""
    if not session.customer_context_dirty:
        return None
    if not session.customer_msgs_snapshot_captured:
        # Runner captures the initial cursor before entering agent_loop. Keep
        # the signal pending if another caller uses the loop prematurely.
        return None

    from zd_agent.services.ticket_messages import (
        fetch_customer_messages_for_freshness,
    )

    observed_generation = session.customer_context_generation
    result = await pre_send_check_or_abort(
        ticket_id=session.ticket_id,
        snapshot_count=session.customer_msgs_snapshot_count,
        snapshot_last_id=session.customer_msgs_snapshot_last_id,
        fetch_messages_fn=lambda: fetch_customer_messages_for_freshness(
            session.ticket_id,
        ),
        # A short, ambiguous customer message is still cheaper to re-analyze
        # than to spend several more full agent rounds against stale context.
        skip_llm=True,
    )
    logger.info(
        "active_session_customer_context_checked",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        source=session.customer_context_source,
        event_id=session.customer_context_event_id or None,
        generation=observed_generation,
        current_generation=session.customer_context_generation,
        continue_send=result.continue_send,
        reason=result.reason,
        new_messages_count=result.new_messages_count,
        round=session.round_count,
    )
    return observed_generation, result


async def _commit_marked_customer_context(
    *,
    check: tuple[int, FreshnessResult],
    session: AgentSession,
    message_store: MessageStore,
    enable_vision_input: bool,
) -> bool:
    """Commit an early freshness check; return True when the loop must restart."""
    observed_generation, result = check
    recovery = result.recovery

    if result.continue_send:
        # Non-substantive additions (thanks/OK) need no new LLM round, but
        # advancing the accepted cursor prevents the durable webhook retry
        # from dispatching the same customer activity after this run finishes.
        if (
            isinstance(recovery, FreshnessRecovery)
            and recovery.action is FreshnessRecoveryAction.REFRESH_CONTEXT
            and recovery.next_snapshot_last_id
        ):
            session.customer_msgs_snapshot_count = recovery.next_snapshot_count
            session.customer_msgs_snapshot_last_id = recovery.next_snapshot_last_id
            session.customer_msgs_snapshot_captured = True
        session.freshness_fetch_error_count = 0
        if session.customer_context_generation == observed_generation:
            session.customer_context_dirty = False
            return False
        # A newer webhook arrived during the fetch. Do not spend an LLM/tool
        # round until that generation has also been checked.
        return True

    if not isinstance(recovery, FreshnessRecovery):
        session.status = SessionStatus.DEFERRED
        session.error_message = "active_context_check_missing_recovery"
        logger.warning(
            "active_context_check_missing_recovery",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            reason=result.reason,
        )
        return True

    await _apply_freshness_recovery(
        result=SimpleNamespace(metadata={"freshness_recovery": recovery}),
        session=session,
        message_store=message_store,
        enable_vision_input=enable_vision_input,
    )
    if recovery.action is FreshnessRecoveryAction.REFRESH_CONTEXT:
        if session.customer_context_generation == observed_generation:
            session.customer_context_dirty = False
    elif recovery.action is FreshnessRecoveryAction.DEFER:
        session.customer_context_dirty = False
    # RETRY_FETCH deliberately leaves the signal dirty so the next safe
    # boundary consumes the bounded second attempt.
    return True


def _loop_finished_for_deferred(
    session: AgentSession,
    guardrails: Guardrails,
) -> LoopFinished:
    return LoopFinished(
        status="deferred",
        total_rounds=guardrails.current_round,
        total_tokens=guardrails.total_tokens,
        elapsed_seconds=guardrails.elapsed_seconds,
        reason=session.error_message or "freshness_check_deferred",
    )


def _tool_result_for_model(
    *,
    registry: ToolRegistry,
    tool_name: str,
    call_id: str,
    output: str,
    success: bool,
) -> str:
    """Expose the exact successful evidence call ID in model-visible content."""
    tool = registry.get(tool_name)
    if (
        not success
        or not call_id
        or not output.strip()
        or tool is None
        or tool.is_write_operation
        or tool_name in _EVIDENCE_CALL_ID_EXCLUDED_TOOLS
        or tool_name.startswith(_EVIDENCE_CALL_ID_EXCLUDED_PREFIXES)
    ):
        return output
    return (
        f"EVIDENCE_CALL_ID={call_id}\n"
        "If this successful result supports a material claim, copy this exact "
        "value into evidence_ledger.source_call_ids.\n\n"
        f"{output}"
    )


async def _run_tool_call(
    tc: ToolCallRequest,
    registry: ToolRegistry,
    session: AgentSession,
    message_store: MessageStore,
    current_round: int,
) -> ToolExecuted:
    """Execute one tool call and append its result to the message store."""
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(
            registry.execute(tc.name, tc.arguments, session),
            timeout=_TOOL_CALL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        error = f"工具执行超时: {tc.name} exceeded {_TOOL_CALL_TIMEOUT_SECONDS:.0f}s"
        output = f"❌ {error}"
        args_dict = _parse_tool_arguments(tc.arguments)
        session.log_tool_call(
            tc.name,
            args_dict,
            output,
            success=False,
            error=error,
            call_id=tc.id,
        )
        message_store.append_tool_result(tc.id, tc.name, output)
        logger.warning(
            "tool_execution_timeout",
            ticket_id=session.ticket_id,
            tool=tc.name,
            round=current_round,
            timeout_s=_TOOL_CALL_TIMEOUT_SECONDS,
            elapsed=elapsed,
        )
        _record_slow_sql_failure(
            session,
            tool_name=tc.name,
            success=False,
            elapsed=elapsed,
        )
        return ToolExecuted(
            round=current_round,
            tool_name=tc.name,
            success=False,
            output_length=len(output),
            elapsed=elapsed,
            error=error,
            arguments=args_dict,
            output=output[:4000],
        )
    elapsed = time.monotonic() - t0
    _record_slow_sql_failure(
        session,
        tool_name=tc.name,
        success=result.success,
        elapsed=elapsed,
    )
    output = result.output if result.success else f"❌ 错误: {result.error}"
    args_dict = _parse_tool_arguments(tc.arguments)
    session.log_tool_call(
        tc.name,
        args_dict,
        output,
        success=result.success,
        error=result.error,
        call_id=tc.id,
        metadata=result.metadata,
    )
    message_store.append_tool_result(
        tc.id,
        tc.name,
        _tool_result_for_model(
            registry=registry,
            tool_name=tc.name,
            call_id=tc.id,
            output=output,
            success=result.success,
        ),
    )
    return ToolExecuted(
        round=current_round,
        tool_name=tc.name,
        success=result.success,
        output_length=len(output),
        elapsed=elapsed,
        error=result.error,
        arguments=args_dict,
        output=output[:4000],
        metadata=result.metadata,
    )


def _parse_tool_arguments(arguments: str) -> dict[str, Any]:
    try:
        return json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return {"raw": arguments}


async def _execute_tool_calls(
    tool_calls: list[ToolCallRequest],
    registry: ToolRegistry,
    session: AgentSession,
    message_store: MessageStore,
    current_round: int,
    *,
    enable_vision_input: bool = False,
) -> AsyncGenerator[ToolExecuted, None]:
    """Run safe tools in parallel and unsafe tools serially.

    Some tools mutate Zendesk, local archives, sandbox state, or share a single
    subprocess. Their ``concurrency_safe=False`` flag used to be ignored by the
    main agent loop, so a model could trigger multiple writes in the same round.
    """
    policy_decisions = {
        tc.id: evaluate_tool_call(session=session, tool_name=tc.name)
        for tc in tool_calls
    }
    has_handoff_reply = any(
        tc.name == "zendesk_public_reply"
        and policy_decisions[tc.id].allowed
        and _parse_tool_arguments(tc.arguments).get("reply_type") == "handoff"
        for tc in tool_calls
    )
    suppressed_feishu_ids: set[str] = set()
    if has_handoff_reply:
        for tc in tool_calls:
            if tc.name != "feishu_push":
                continue
            suppressed_feishu_ids.add(tc.id)
            message_store.append_tool_result(
                tc.id,
                tc.name,
                (
                    "Cancelled: reply_type=handoff owns the escalation side "
                    "effects. The public-reply boundary notifies Feishu only "
                    "after customer delivery succeeds."
                ),
            )
        if suppressed_feishu_ids:
            logger.info(
                "feishu_push_cancelled_for_handoff",
                ticket_id=session.ticket_id,
                cancelled_count=len(suppressed_feishu_ids),
            )

    async def _run_safe_batch(
        safe_batch: list[ToolCallRequest],
    ) -> AsyncGenerator[ToolExecuted, None]:
        if not safe_batch:
            return
        tasks = [
            _run_tool_call(
                tc,
                registry,
                session,
                message_store,
                current_round,
            )
            for tc in safe_batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for tc, result in zip(safe_batch, results, strict=False):
            if isinstance(result, Exception):
                yield _tool_execution_error_event(
                    tc, session, message_store, current_round, result,
                )
            else:
                yield result

    safe_batch: list[ToolCallRequest] = []
    for index, tc in enumerate(tool_calls):
        if tc.id in suppressed_feishu_ids:
            continue
        policy_decision = policy_decisions[tc.id]
        if not policy_decision.allowed:
            async for event in _run_safe_batch(safe_batch):
                yield event
            safe_batch = []
            yield _tool_policy_blocked_event(
                tc,
                session,
                message_store,
                current_round,
                policy_decision.reason,
            )
            continue
        tool = registry.get(tc.name)
        # A public reply is a terminal external write even if a test double or
        # future implementation forgets to declare ``concurrency_safe=False``.
        # Keep it on the serial path so a freshness cancellation or successful
        # delivery can invalidate every later call from the same LLM response.
        if (
            tc.name != "zendesk_public_reply"
            and (tool is None or tool.concurrency_safe)
        ):
            safe_batch.append(tc)
            continue

        async for event in _run_safe_batch(safe_batch):
            yield event
        safe_batch = []

        try:
            event = await _run_tool_call(
                tc,
                registry,
                session,
                message_store,
                current_round,
            )
            if tc.name == "zendesk_public_reply" and session.reply_sent:
                # A confirmed customer-visible write is terminal even when
                # post-send internal-note/outbox bookkeeping reports failure.
                yield event
                return
            recovery = (event.metadata or {}).get("freshness_recovery")
            if (
                isinstance(recovery, FreshnessRecovery)
                and recovery.action
                in {
                    FreshnessRecoveryAction.REFRESH_CONTEXT,
                    FreshnessRecoveryAction.RETRY_FETCH,
                }
            ):
                # The assistant produced every remaining call against the old
                # customer snapshot.  Add explicit cancellation results so the
                # next OpenAI request still has a valid tool-call transcript,
                # but never execute those stale writes.
                for cancelled in tool_calls[index + 1:]:
                    if cancelled.id in suppressed_feishu_ids:
                        continue
                    message_store.append_tool_result(
                        cancelled.id,
                        cancelled.name,
                        (
                            "Cancelled: customer context changed before this "
                            "tool call could execute. Re-evaluate it against "
                            "the refreshed ticket context."
                        ),
                    )
                logger.info(
                    "tool_batch_cancelled_after_freshness_change",
                    ticket_id=session.ticket_id,
                    cancelled_count=len(tool_calls) - index - 1,
                    recovery_action=recovery.action.value,
                )
                # Every tool result for this assistant message is now present.
                # Only then may a user freshness message be appended; otherwise
                # the next provider request sees tool -> user -> tool and rejects
                # the transcript as malformed.
                await _apply_freshness_recovery(
                    result=event,
                    session=session,
                    message_store=message_store,
                    enable_vision_input=enable_vision_input,
                )
                yield event
                return

            await _apply_freshness_recovery(
                result=event,
                session=session,
                message_store=message_store,
                enable_vision_input=enable_vision_input,
            )
            yield event
            if session.status is SessionStatus.DEFERRED:
                return
        except Exception as e:
            yield _tool_execution_error_event(
                tc, session, message_store, current_round, e,
            )

    async for event in _run_safe_batch(safe_batch):
        yield event


def _tool_execution_error_event(
    tc: ToolCallRequest,
    session: AgentSession,
    message_store: MessageStore,
    current_round: int,
    error: Exception,
) -> ToolExecuted:
    err_text = f"❌ 工具执行异常: {error}"
    args = _parse_tool_arguments(tc.arguments)
    session.log_tool_call(
        tc.name,
        args,
        err_text,
        success=False,
        error=str(error),
        call_id=tc.id,
    )
    message_store.append_tool_result(tc.id, tc.name, err_text)
    logger.error(
        "tool_execution_error",
        ticket_id=session.ticket_id,
        tool=tc.name,
        error=str(error),
    )
    return ToolExecuted(
        round=current_round,
        tool_name=tc.name,
        success=False,
        output_length=len(err_text),
        elapsed=0.0,
        error=str(error),
        arguments=args,
        output=err_text[:4000],
    )


def _tool_policy_blocked_event(
    tc: ToolCallRequest,
    session: AgentSession,
    message_store: MessageStore,
    current_round: int,
    reason: str,
) -> ToolExecuted:
    error = f"Investigation policy blocked tool call: {reason}"
    output = f"❌ 错误: {error}"
    args = _parse_tool_arguments(tc.arguments)
    metadata = {"investigation_policy_blocked": True}
    session.log_tool_call(
        tc.name,
        args,
        output,
        success=False,
        error=error,
        call_id=tc.id,
        metadata=metadata,
    )
    message_store.append_tool_result(tc.id, tc.name, output)
    logger.warning(
        "investigation_tool_blocked",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        round=current_round,
        phase=current_investigation_phase(session),
        tool=tc.name,
        reason=reason,
    )
    return ToolExecuted(
        round=current_round,
        tool_name=tc.name,
        success=False,
        output_length=len(output),
        elapsed=0.0,
        error=error,
        arguments=args,
        output=output[:4000],
        metadata=metadata,
    )


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
    from zd_agent.services.ticket_messages import _download_image_data_url

    async def _resolve_image_url(raw_url: str) -> str | None:
        if raw_url.startswith("data:image/"):
            return raw_url
        return await _download_image_data_url(raw_url)

    # 已由 Zendesk MCP 下载的图片直接复用 data URL；其余 URL 并发下载。
    # 任一图片失败都不阻断 dispatch。
    results = await asyncio.gather(
        *(_resolve_image_url(img["url"]) for img in raw_images),
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
        f"该检测只用于理解客户原文；客户可见 public reply / Suggested Reply 必须使用 English，"
        f"不跟随客户语言。Internal Analysis、Review Guide 使用中文。"
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
    *,
    enable_vision_input: bool = False,
) -> AsyncGenerator[AgentEvent, None]:
    """LLM ↔ 工具循环，通过 yield 产出进度事件（不打印控制台）。"""
    session.status = SessionStatus.RUNNING

    try:
        while True:
            if session.slow_sql_circuit_open:
                raise GuardrailTriggered(
                    "SQL slow-query circuit opened after "
                    f"{session.slow_sql_failure_count} failed queries"
                )
            guardrails.check_all()

            # Webhooks mark the active session immediately, without trying to
            # cancel arbitrary in-flight network calls. Consume that signal at
            # this safe transcript boundary before paying for another LLM round.
            dirty_check = await _fetch_marked_customer_context(session)
            if dirty_check is not None:
                restart = await _commit_marked_customer_context(
                    check=dirty_check,
                    session=session,
                    message_store=message_store,
                    enable_vision_input=enable_vision_input,
                )
                if session.status is SessionStatus.DEFERRED:
                    yield _loop_finished_for_deferred(session, guardrails)
                    return
                if restart:
                    continue

            # 计算 message_store 当前 token 数（不算很贵，每轮一次）—
            # 用于"修法 2"基于 message_store 大小的提前 compaction 触发。
            ms_tokens = count_tokens(message_store.to_api_messages())
            ms_tokens = _groom_context_proactively(
                session,
                message_store,
                message_store_tokens=ms_tokens,
            )

            if guardrails.should_compact(message_store_tokens=ms_tokens):
                compaction_target = guardrails.compaction_target_tokens(
                    message_store_tokens=ms_tokens,
                )
                logger.info(
                    "compaction_triggered",
                    ticket_id=session.ticket_id,
                    message_store_tokens=ms_tokens,
                    cumulative_tokens=guardrails.total_tokens,
                    target_tokens=compaction_target,
                )
                # 用 multi_level_compact：先 Level 1 history_snip（cheap，截 tool
                # 输出到 200 字符）；如果还不够再 Level 2 LLM 摘要。之前直接调
                # Level 2 在已经短的内容上跑 LLM，反而 saved_tokens < 0（观察
                # 服务器日志: -18 / -12 / -23）。
                compacted = await multi_level_compact(
                    message_store.to_api_messages(),
                    provider,
                    target_tokens=compaction_target,
                )
                message_store.raw = compacted

            guardrails.increment_round()
            session.round_count = guardrails.current_round

            active_tool_schemas = filter_tool_schemas(session, tool_schemas)
            logger.info(
                "investigation_tool_surface",
                ticket_id=session.ticket_id,
                session_id=session.session_id,
                round=session.round_count,
                phase=current_investigation_phase(session),
                route=session.investigation_route or None,
                ticket_type=session.ticket_type or None,
                tool_count=len(active_tool_schemas),
                tool_names=[schema.name for schema in active_tool_schemas],
            )

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
                            tools=active_tool_schemas,
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
                            if _is_timeout_error(e):
                                _groom_context_for_retry(
                                    session,
                                    message_store,
                                    reason=type(e).__name__,
                                )
                            delay = _CONNECTION_BASE_DELAY * (2 ** (conn_attempts - 1))
                            logger.warning(
                                "connection_error_retry",
                                ticket_id=session.ticket_id,
                                session_id=session.session_id,
                                parent_session_id=session.parent_session_id,
                                subagent_batch_id=session.subagent_batch_id,
                                subagent_index=session.subagent_index,
                                round=session.round_count,
                                provider_type=type(provider).__name__,
                                attempt=conn_attempts,
                                delay=delay,
                                error_type=type(e).__name__,
                                error_cause_type=(
                                    type(e.__cause__ or e.__context__).__name__
                                    if (e.__cause__ or e.__context__) is not None
                                    else ""
                                ),
                                error_detail=_safe_exception_detail(e),
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
                if has_responses_history(message_store.to_api_messages()):
                    raise
                removed = _strip_images_from_messages(message_store.raw)
                logger.warning(
                    "image_url_fallback",
                    ticket_id=session.ticket_id,
                    removed_count=removed,
                    error_type=type(chat_err).__name__,
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

            # 透出每轮工具调用数 + 工具名 → 验证"批量发工具"指令是否生效。
            # 一轮一个工具 = 批量没生效（轮数被拉长）；一轮多个 = 在并行。
            logger.info(
                "round_tool_calls",
                ticket_id=session.ticket_id,
                round=guardrails.current_round,
                tool_call_count=len(response.tool_calls),
                tool_names=[tc.name for tc in response.tool_calls],
            )

            message_store.append_assistant(_assistant_message_dict(response))

            # A webhook may arrive while the LLM request is in flight. Re-fetch
            # now, before any tools from that stale assistant response execute.
            dirty_check = await _fetch_marked_customer_context(session)
            if dirty_check is not None:
                observed_generation, freshness_result = dirty_check
                newer_generation_pending = (
                    session.customer_context_generation != observed_generation
                )
                must_restart = (
                    not freshness_result.continue_send
                    or newer_generation_pending
                )
                if must_restart:
                    for stale_call in response.tool_calls:
                        message_store.append_tool_result(
                            stale_call.id,
                            stale_call.name,
                            (
                                "Cancelled: customer context changed while the LLM "
                                "response was in flight. Re-evaluate this tool call "
                                "against the refreshed ticket context."
                            ),
                        )
                restart = await _commit_marked_customer_context(
                    check=dirty_check,
                    session=session,
                    message_store=message_store,
                    enable_vision_input=enable_vision_input,
                )
                if must_restart:
                    logger.info(
                        "stale_assistant_cancelled_after_customer_webhook",
                        ticket_id=session.ticket_id,
                        session_id=session.session_id,
                        cancelled_tool_count=len(response.tool_calls),
                        reason=freshness_result.reason,
                        newer_generation_pending=newer_generation_pending,
                        round=guardrails.current_round,
                    )
                if session.status is SessionStatus.DEFERRED:
                    yield _loop_finished_for_deferred(session, guardrails)
                    return
                if restart:
                    continue

            if not response.has_tool_calls:
                if response.content:
                    session.analysis += f"\n{response.content}"
                if not session.dry_run and not session.reply_sent:
                    failure_reason = (
                        "Production agent stopped without delivering a "
                        "customer-visible reply"
                    )
                    session.status = SessionStatus.FAILED
                    session.error_message = failure_reason
                    yield LoopFinished(
                        status="error",
                        total_rounds=guardrails.current_round,
                        total_tokens=guardrails.total_tokens,
                        elapsed_seconds=guardrails.elapsed_seconds,
                        reason=failure_reason,
                    )
                    return
                session.status = SessionStatus.COMPLETED
                yield LoopFinished(
                    status="completed",
                    total_rounds=guardrails.current_round,
                    total_tokens=guardrails.total_tokens,
                    elapsed_seconds=guardrails.elapsed_seconds,
                    reason="",
                )
                return

            successful_tool_names: list[str] = []
            public_reply_delivered = False
            async for event in _execute_tool_calls(
                response.tool_calls,
                registry,
                session,
                message_store,
                guardrails.current_round,
                enable_vision_input=enable_vision_input,
            ):
                if isinstance(event, ToolExecuted) and event.success:
                    successful_tool_names.append(event.tool_name)
                if (
                    isinstance(event, ToolExecuted)
                    and event.tool_name == "zendesk_public_reply"
                    and session.reply_sent
                ):
                    public_reply_delivered = True
                yield event

            if session.status is SessionStatus.DEFERRED:
                yield LoopFinished(
                    status="deferred",
                    total_rounds=guardrails.current_round,
                    total_tokens=guardrails.total_tokens,
                    elapsed_seconds=guardrails.elapsed_seconds,
                    reason=session.error_message or "freshness_check_deferred",
                )
                return

            if public_reply_delivered:
                session.status = SessionStatus.COMPLETED
                yield LoopFinished(
                    status="completed",
                    total_rounds=guardrails.current_round,
                    total_tokens=guardrails.total_tokens,
                    elapsed_seconds=guardrails.elapsed_seconds,
                    reason="customer-visible reply delivered",
                )
                return

            if session.image_urls and not session.images_injected:
                if enable_vision_input:
                    await _inject_images(session, message_store)
                else:
                    message_store.append_user(
                        f"[系统提醒] 工单包含 {len(session.image_urls)} 张截图，"
                        "但当前主模型视觉输入已关闭。请仅依据文字内容分析；"
                        "如果截图内容会改变结论，向客户索取文字版报错或关键信息。"
                    )
                    logger.info(
                        "images_skipped_by_config",
                        ticket_id=session.ticket_id,
                        count=len(session.image_urls),
                    )
                session.images_injected = True

            _inject_subagent_convergence_reminder(
                session,
                message_store,
                executed_tool_names=successful_tool_names,
            )
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
                "**如需 SE/SRE/人工兜底 → 必须调 "
                'zendesk_public_reply(reply_type="handoff")，向客户说明已确认事实、'
                "升级原因和下一步，并把内部证据放入 internal_analysis**。"
                "如果冻结路线是 high_risk，handoff 也必须附 "
                "evidence_ledger，使用 technical_fact_only，并引用目标对象的精确"
                "成功调用 ID；不能用 handoff 跳过仍可执行的身份或计费核查。"
                "只有公开回复通道不可用或存在安全/隐私泄露风险时，才调 "
                "zendesk_internal_reply，并注明客户尚未收到通知。"
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
                    error_type=type(chat_err).__name__,
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
                        if (
                            tc.name == "zendesk_public_reply"
                            and not tc_result.success
                            and (tc_result.error or "").startswith("参数解析失败:")
                        ):
                            fallback_arguments = json.dumps(
                                {
                                    "ticket_id": session.ticket_id,
                                    "body": (
                                        "Thank you for the detailed report. We have "
                                        "escalated this case to our technical support "
                                        "team for further investigation. The automated "
                                        "investigation reached its processing limit "
                                        "before a verified diagnosis could be completed. "
                                        "A teammate will review the collected evidence "
                                        "and follow up with you."
                                    ),
                                    "reply_type": "handoff",
                                    "internal_analysis": (
                                        "The automated investigation reached its processing "
                                        "limit and produced malformed reply arguments. "
                                        "系统已发送确定性的 handoff 回复，需要人工审核已收集证据。"
                                    ),
                                },
                                ensure_ascii=False,
                            )
                            tc_result = await registry.execute(
                                tc.name,
                                fallback_arguments,
                                session,
                            )
                            logger.info(
                                "guardrail_summary_malformed_reply_recovered",
                                ticket_id=session.ticket_id,
                                tool=tc.name,
                                success=tc_result.success,
                            )
                        out = tc_result.output if tc_result.success else (
                            f"❌ {tc_result.error}"
                        )
                        message_store.append_tool_result(tc.id, tc.name, out)
                        logger.info(
                            "guardrail_summary_reply_result",
                            ticket_id=session.ticket_id,
                            tool=tc.name,
                            success=tc_result.success,
                        )
                        if not tc_result.success:
                            raise RuntimeError(
                                f"Forced summary reply tool {tc.name} failed: "
                                f"{tc_result.error or 'unknown error'}"
                            )
                        if (
                            tc.name == "zendesk_public_reply"
                            and session.reply_sent
                        ):
                            break
                    except Exception as exc:
                        logger.error(
                            "guardrail_summary_reply_failed",
                            ticket_id=session.ticket_id,
                            tool=tc.name, error=str(exc),
                        )
                        raise

            if not session.dry_run and not session.reply_sent:
                raise RuntimeError(
                    "Forced summary ended without delivering a "
                    "customer-visible reply"
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
            failure_reason = (
                f"{e.reason}; forced summary failed: {summary_err}"
            )
            session.error_message = failure_reason
            session.analysis += f"\n\n⚠️ 处理被护栏终止: {e.reason}"
            yield LoopFinished(
                status="guardrail",
                total_rounds=guardrails.current_round,
                total_tokens=guardrails.total_tokens,
                elapsed_seconds=guardrails.elapsed_seconds,
                reason=failure_reason,
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
