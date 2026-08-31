"""AgentSession：单个工单处理的完整会话上下文"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class SessionStatus(enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    DEFERRED = "deferred"


@dataclass
class TokenUsage:
    """Token 消耗统计"""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class AgentSession:
    """单个工单的完整处理会话"""

    ticket_id: int
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    root_session_id: str = ""
    parent_session_id: str = ""
    session_kind: str = "root"
    subagent_task_id: str = ""
    subagent_slot_id: str = ""
    runtime_config_digest: str = ""
    trigger_type: str = "cli"
    delivery_attempt_id: str = ""
    status: SessionStatus = SessionStatus.QUEUED

    # v2 运行时状态
    tool_call_log: list[dict[str, Any]] = field(default_factory=list)
    # Object IDs discovered from code-scoped current-ticket evidence. Arbitrary
    # queries may pivot only to identifiers already proven in this set.
    investigation_scope_ids: set[str] = field(default_factory=set)
    round_count: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    active_context_tokens: int = 0
    context_compaction_count: int = 0
    context_compaction_log: list[dict[str, Any]] = field(default_factory=list)
    context_checkpoints: list[dict[str, Any]] = field(default_factory=list)

    # v2 产出物与不可逆 effect 回执
    analysis: str = ""
    reply_draft: str = ""
    reply_sent: bool = False
    casework_disposition: str = ""
    casework_reply_kind: str = ""
    casework_ownership_action: str = ""
    casework_reason_code: str = ""
    casework_delivery_state: str = ""
    casework_ownership_state: str = ""
    casework_effect_reference: str = ""
    casework_required_customer_fields: tuple[str, ...] = ()
    casework_question: str = ""
    casework_attempt: int = 1
    archived: bool = False
    error_message: str = ""

    # Durable clarification provenance inherited from the prior customer ask.
    # The latest customer message may be a workspace answer, not a replacement
    # for the original support question.
    prior_customer_required_fields: tuple[str, ...] = ()
    prior_issue_question: str = ""

    # 额外提示
    hint: str = ""
    dry_run: bool = False

    # ───── M5 (2026-05-13) pre-send freshness check 用 ─────
    # agent dispatch 起跑时的客户消息 snapshot。public reply 发送前会再 fetch
    # 一次客户消息，对比 snapshot 决定是否 abort（客户在 agent 分析期间发了
    # 实质性新消息时 abort）。
    # 仅 public reply 路径使用；internal reply 不需要（同事看见草稿后会自己决定）。
    customer_msgs_snapshot_count: int = 0
    customer_msgs_snapshot_last_id: str = ""
    customer_msgs_snapshot_captured: bool = False
    # Webhook arrives while this session is running. The manager increments a
    # generation instead of cancelling arbitrary in-flight I/O. The durable
    # pre-send freshness boundary rejects a stale delivery and schedules retry.
    customer_context_dirty: bool = False
    customer_context_generation: int = 0
    customer_context_source: str = ""
    customer_context_event_id: str = ""

    def log_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        *,
        success: bool,
        error: str | None,
        call_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.tool_call_log.append(
            {
                "call_id": call_id,
                "round": self.round_count,
                "tool": tool_name,
                "args": args,
                "success": success,
                "error": error,
                "result_preview": result[:500],
                "metadata": dict(metadata or {}),
            }
        )

    def log_context_compaction(
        self,
        *,
        trigger: str,
        tokens_before: int,
        tokens_after: int,
        checkpoint: dict[str, Any],
    ) -> None:
        self.context_compaction_count += 1
        self.active_context_tokens = tokens_after
        reclaimed = max(tokens_before - tokens_after, 0)
        self.context_compaction_log.append(
            {
                "sequence": self.context_compaction_count,
                "trigger": trigger,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "reclaimed_tokens": reclaimed,
                "reclaimed_ratio": round(
                    reclaimed / max(tokens_before, 1),
                    4,
                ),
            }
        )
        self.context_checkpoints.append(dict(checkpoint))
