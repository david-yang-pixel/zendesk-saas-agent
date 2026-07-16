"""AgentSession：单个工单处理的完整会话上下文"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SessionStatus(enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    DEFERRED = "deferred"


@dataclass
class TicketContext:
    """工单上下文信息"""

    ticket_id: int
    subject: str = ""
    description: str = ""
    customer_language: str = ""
    customer_version: str = ""
    deployment_method: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


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
    trigger_type: str = "cli"
    delivery_attempt_id: str = ""
    status: SessionStatus = SessionStatus.QUEUED

    # Sandbox
    sandbox_dir: Path | None = None

    # 工单上下文
    context: TicketContext | None = None

    # 运行时状态
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_call_log: list[dict[str, Any]] = field(default_factory=list)
    round_count: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    remote_log_run_id: int | None = None
    subagent_dispatch_count: int = 0
    slow_sql_failure_count: int = 0
    slow_sql_circuit_open: bool = False

    # 工单图片 URL（由 zendesk_get_ticket 收集，注入多模态消息供 LLM 查看）
    image_urls: list[dict[str, str]] = field(default_factory=list)
    images_injected: bool = False

    # 工单分类
    ticket_type: str = ""
    classification_reasoning: str = ""

    # 产出物
    analysis: str = ""
    reply_draft: str = ""
    customer_reply_draft: str = ""
    reviewer_checklist: str = ""
    fix_status: str = ""
    reply_sent: bool = False
    archived: bool = False
    error_message: str = ""

    # Teleport 认证挂起标志
    waiting_for_auth: bool = False

    # 跟进模式支持
    mode: str = "first_pass"           # "first_pass" | "followup"
    previous_record: str = ""          # 经 live Zendesk follow-up 判定后附加的历史参考
    new_comments_diff: str = ""        # 从最新 Zendesk 对话中确认的新增客户对话

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
    freshness_rebase_count: int = 0

    # ───── P2-8 (2026-05-17) 客户画像预拉 ─────
    # runner.py 在 build_system_prompt 之前调用 prefetch_customer_profile()，
    # 拿到 {requester_email, recent_ticket_ids, recent_ticket_count}。
    # 注入 system prompt 让 agent 知道"这是 repeat customer"。
    # 失败/超时时为空 dict，不影响主流程。
    customer_profile: dict[str, Any] = field(default_factory=dict)

    def log_tool_call(
        self,
        tool_name: str,
        args: dict,
        result: str,
        *,
        success: bool,
        error: str | None,
    ) -> None:
        self.tool_call_log.append({
            "round": self.round_count,
            "tool": tool_name,
            "args": args,
            "success": success,
            "error": error,
            "result_preview": result[:500],
        })

    def mark_waiting_for_auth(self) -> None:
        """Mark this session as waiting for Teleport auth renewal.

        SqlQuery sets this when psql connection fails with connection-refused /
        could-not-connect, indicating the tsh proxy-db is down or cert expired.
        Upstream scheduler can use this to suspend and retry later.
        """
        self.waiting_for_auth = True
