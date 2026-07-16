"""TicketStateTracker: JSON-backed state machine for ticket lifecycle."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import time_ns
from typing import Optional

from zd_agent.log import get_logger
from zd_agent.services.atomic_write import atomic_write_text


logger = get_logger(module="trigger.state_store")


VALID_STATES = {
    "waiting_for_issue",    # LLM judged greeting/insufficient, waiting for customer
    "in_silence_window",    # LLM judged substantial issue, within silence period
    "processing",           # AgentSession running
    "processed",            # done (first pass or followup)
    "waiting_for_auth",     # Teleport down, suspended
    "abandoned",            # customer gone too long
    "escalated_to_human",   # 已转人工：agent 完全退出此工单，4 个 entry 入口 skip
}


class TicketStateCorruptedError(RuntimeError):
    pass


@dataclass
class TicketState:
    state: str
    first_seen: datetime
    last_customer_comment_id: Optional[str] = None
    last_comment_ts: Optional[datetime] = None
    last_eval_at: Optional[datetime] = None
    silence_window_end: Optional[datetime] = None
    processed_count: int = 0
    last_verdict: str = ""
    # 主动 public ask 时间，避免重复 nudge；客户新消息到达时由调用方重置为 None
    nudge_sent_at: Optional[datetime] = None
    # 30min greeting 飞书告警发送时间，去重
    alert_sent_at: Optional[datetime] = None
    # 24h abandoned 飞书告警发送时间，去重
    abandoned_alert_sent_at: Optional[datetime] = None
    # 上次 AgentSession 完成时刻；客户新消息到达时若距此 < cooldown，则跳过 dispatch
    # （防止客户连发两条触发 agent 跑两次同语义任务）。由 SessionManager / runner finally
    # 钩子在 agent_done 后写入。
    last_agent_finish_ts: Optional[datetime] = None
    # Durable owner for ``processing``. Completion callbacks from any older
    # session generation must not mutate the current ticket lifecycle.
    active_session_id: str = ""

    # ───── M3 (2026-05-13) auto-public-reply 新增字段 ─────
    # 同工单内 agent 累计自动 ask 客户的次数（每次发 public ask 模板 A/B/C/D 累加）。
    # 用于 M4 实施 max_ask_count（默认 10）上限：超过后强制转人工。
    # 客户 verdict 终于变 substantial 后保留计数不重置（说明 agent 花了几轮才拿到信息，
    # 用于事后复盘 + 调 prompt）。
    ask_count: int = 0
    # 转人工时刻（仅 state=escalated_to_human 时填）。供飞书卡片显示 + 事后审计。
    escalated_at: Optional[datetime] = None
    # 转人工原因。已支持的值：
    #   - customer_requested      客户主动说 "human" / 触发 fast-path
    #   - max_asks_reached        累计 ask >= max_ask_count 上限
    #   - high_risk_category      工单被判定为 refund / emotional / incident
    #   - low_confidence          agent 自评 confidence=low
    #   - agent_error             agent 自身出错（异常 / 工具失败 / token 超额等）
    #   - manual                  人工通过 mention 主动接手（兼容旧路径）
    escalated_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TicketState":
        data = dict(d)
        for k in (
            "first_seen", "last_comment_ts", "last_eval_at", "silence_window_end",
            "nudge_sent_at", "alert_sent_at", "abandoned_alert_sent_at",
            "last_agent_finish_ts", "escalated_at",
        ):
            if data.get(k):
                data[k] = datetime.fromisoformat(data[k])
            elif k in data and data[k] is None:
                # keep None
                pass
        # 旧 state.json 里没这几个字段，dataclass 会用 default — 但要剔除 dict 里多余的 key
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        data = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**data)


class TicketStateTracker:
    """JSON-backed state tracker; atomic write via tmp-rename."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict[str, TicketState] = {}
        self._serialized: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            self._quarantine(f"{type(error).__name__}: {error}")
            return
        if not isinstance(raw, dict):
            self._quarantine("expected a JSON object at the top level")
            return

        loaded: dict[str, TicketState] = {}
        serialized: dict[str, dict] = {}
        try:
            for key, value in raw.items():
                if not isinstance(value, dict):
                    raise TypeError(f"ticket {key} state must be a JSON object")
                ticket_state = TicketState.from_dict(value)
                if ticket_state.state not in VALID_STATES:
                    raise ValueError(
                        f"ticket {key} has invalid state: {ticket_state.state}",
                    )
                normalized_key = str(key)
                loaded[normalized_key] = ticket_state
                serialized[normalized_key] = ticket_state.to_dict()
        except (TypeError, ValueError) as error:
            self._quarantine(f"{type(error).__name__}: {error}")
            return
        self._data = loaded
        self._serialized = serialized

    def _quarantine(self, reason: str) -> None:
        quarantine_path = self.path.with_name(
            f"{self.path.name}.corrupt-{time_ns()}",
        )
        self.path.replace(quarantine_path)
        logger.error(
            "ticket_state_file_quarantined",
            path=str(self.path),
            quarantine_path=str(quarantine_path),
            reason=reason,
        )
        raise TicketStateCorruptedError(
            f"ticket state file is corrupted and was quarantined: {quarantine_path}",
        )

    def _save(self, serialized: dict[str, dict]) -> None:
        atomic_write_text(
            self.path,
            json.dumps(serialized, ensure_ascii=False, separators=(",", ":")),
        )

    def get(self, ticket_id: str | int) -> Optional[TicketState]:
        return self._data.get(str(ticket_id))

    def set(self, ticket_id: str | int, state: TicketState) -> None:
        if state.state not in VALID_STATES:
            raise ValueError(f"invalid state: {state.state}")
        key = str(ticket_id)
        serialized = state.to_dict()
        if self._serialized.get(key) == serialized:
            self._data[key] = state
            return
        next_serialized = dict(self._serialized)
        next_serialized[key] = serialized
        self._save(next_serialized)
        self._data[key] = state
        self._serialized = next_serialized

    def delete(self, ticket_id: str | int) -> None:
        key = str(ticket_id)
        if key not in self._data:
            return
        next_serialized = dict(self._serialized)
        next_serialized.pop(key, None)
        self._save(next_serialized)
        self._data.pop(key)
        self._serialized = next_serialized

    def list_by_state(self, state: str) -> list[str]:
        return [k for k, v in self._data.items() if v.state == state]

    def all(self) -> dict[str, TicketState]:
        return dict(self._data)

    def __len__(self) -> int:
        return len(self._data)
