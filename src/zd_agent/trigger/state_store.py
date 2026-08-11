"""TicketStateTracker: JSON-backed state machine for ticket lifecycle."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import time_ns
from typing import Callable, Iterator, Optional

from zd_agent.log import get_logger
from zd_agent.services.atomic_write import atomic_write_text


logger = get_logger(module="trigger.state_store")


VALID_STATES = {
    # Historical values remain loadable until all persisted state has passed
    # through v2_state_migration.  V2 production code never writes them.
    "waiting_for_issue",  # legacy: generic classifier wait
    "waiting_for_form",  # Answer Bot blocking form is not submitted yet
    "awaiting_customer",  # v2 sent a confirmed ask and awaits new evidence
    "in_silence_window",  # LLM judged substantial issue, within silence period
    "processing",  # AgentSession running
    "ownership_pending",  # typed v2 owner command is being durably completed
    "processed",  # done (first pass or followup)
    "waiting_for_auth",  # Teleport down, suspended
    "abandoned",  # legacy: customer timeout + implicit escalation
    "escalated_to_human",  # legacy: untyped human handoff
    "owned",  # v2 typed owner command is durable; agent exits
    "externally_owned",  # a real human owns the ticket; automation exits
    "out_of_scope",  # Zendesk lifecycle/status is no longer automatable
    "manual_reconciliation",  # ambiguous external effect requires an operator
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
    # Start of the current Answer Bot blocking-form wait.  ``first_seen`` is
    # ticket-lifetime metadata and may predate this wait by days or weeks.
    form_wait_started_at: Optional[datetime] = None
    silence_window_end: Optional[datetime] = None
    processed_count: int = 0
    last_verdict: str = ""
    # Legacy classifier/notification markers retained only for state-file
    # deserialization. V2 does not create nudges or escalation alerts.
    nudge_sent_at: Optional[datetime] = None
    alert_sent_at: Optional[datetime] = None
    abandoned_alert_sent_at: Optional[datetime] = None
    # 上次 AgentSession 完成时刻；客户新消息到达时若距此 < cooldown，则跳过 dispatch
    # （防止客户连发两条触发 agent 跑两次同语义任务）。由 SessionManager / runner finally
    # 钩子在 agent_done 后写入。
    last_agent_finish_ts: Optional[datetime] = None
    # Durable owner for ``processing``. Completion callbacks from any older
    # session generation must not mutate the current ticket lifecycle.
    active_session_id: str = ""
    # Stable logical id for customer-visible delivery side effects.  Queue
    # scheduling keys may change across silence-window and startup recovery,
    # but retries must keep this id so the public-reply outbox can reconcile
    # an already delivered message instead of sending regenerated prose.
    delivery_attempt_id: str = ""
    # Consecutive AgentSession failures that ended before a public reply was
    # delivered.  This must live in the durable ticket state rather than the
    # queue message: a dispatched session is ACKed before its runner finishes,
    # so queue attempts cannot bound this recovery loop.
    consecutive_agent_failures: int = 0
    # Monotonic lifecycle generation. Delayed queue messages carry the
    # generation they were created from and must no-op after any newer state
    # transition, preventing stale asks or dispatches from taking effect.
    state_revision: int = 0

    # Historical ask/escalation audit fields remain readable; v2 writes typed
    # reply dispositions and ownership effects instead.
    ask_count: int = 0
    escalated_at: Optional[datetime] = None
    escalated_reason: str = ""
    # v2 ownership is independent from customer reply wording.  These fields
    # are populated only after a typed durable command exists.
    ownership_action: str = ""
    ownership_reason: str = ""
    ownership_effect_reference: str = ""

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
            "first_seen",
            "last_comment_ts",
            "last_eval_at",
            "form_wait_started_at",
            "silence_window_end",
            "nudge_sent_at",
            "alert_sent_at",
            "abandoned_alert_sent_at",
            "last_agent_finish_ts",
            "escalated_at",
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


@dataclass(frozen=True)
class TicketStateCommit:
    """One successful generation-fenced ticket-state transition."""

    before: Optional[TicketState]
    after: TicketState
    previous_revision: Optional[int]
    state_revision: int


class TicketStateTracker:
    """JSON-backed state tracker; atomic write via tmp-rename."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock_path = self.path.with_name(f"{self.path.name}.lock")
        self._data: dict[str, TicketState] = {}
        self._serialized: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        with self._file_lock():
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        self._data = {}
        self._serialized = {}
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

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Serialize read-modify-write cycles across agent processes."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(self._lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

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
        with self._file_lock():
            self._load_unlocked()
            return self._data.get(str(ticket_id))

    def set(self, ticket_id: str | int, state: TicketState) -> None:
        if state.state not in VALID_STATES:
            raise ValueError(f"invalid state: {state.state}")
        with self._file_lock():
            self._load_unlocked()
            key = str(ticket_id)
            previous = self._serialized.get(key)
            previous_revision = int((previous or {}).get("state_revision") or 0)
            candidate = state.to_dict()
            # Callers often mutate the tracker-owned dataclass before set().  Use
            # the last serialized snapshot as the source of truth for comparison
            # and generation assignment instead of trusting the mutable object.
            candidate["state_revision"] = previous_revision
            if previous == candidate:
                state.state_revision = previous_revision
                self._data[key] = state
                return
            state.state_revision = previous_revision + 1
            serialized = state.to_dict()
            next_serialized = dict(self._serialized)
            next_serialized[key] = serialized
            self._save(next_serialized)
            self._data[key] = state
            self._serialized = next_serialized

    def update_if_revision(
        self,
        ticket_id: str | int,
        *,
        expected_revision: int | None,
        mutate: Callable[[Optional[TicketState]], TicketState],
        expected_state: str | None = None,
        expected_active_session_id: str | None = None,
    ) -> TicketStateCommit | None:
        """Atomically update one lifecycle generation in this tracker.

        The comparison and write are synchronous, so they cannot interleave
        with another asyncio task using this tracker.  Both the comparison and
        mutation input come from the last serialized snapshot, not from the
        mutable object returned by :meth:`get`.

        ``expected_revision=None`` means that the record must not exist.
        Optional state and owner predicates further fence lifecycle ownership.
        A stale predicate returns ``None`` without invoking ``mutate`` or
        changing memory/disk.  Every successful update creates exactly one new
        revision, including a logically identical mutation used as a claim.
        """
        with self._file_lock():
            self._load_unlocked()
            key = str(ticket_id)
            serialized_before = self._serialized.get(key)
            if serialized_before is None:
                if expected_revision is not None:
                    return None
                current_revision: int | None = None
                current_state: TicketState | None = None
            else:
                current_revision = int(serialized_before.get("state_revision") or 0)
                if current_revision != expected_revision:
                    return None
                current_state = TicketState.from_dict(dict(serialized_before))

            if expected_state is not None and (
                current_state is None or current_state.state != expected_state
            ):
                return None
            if expected_active_session_id is not None and (
                current_state is None
                or current_state.active_session_id != expected_active_session_id
            ):
                return None

            before = (
                TicketState.from_dict(dict(serialized_before))
                if serialized_before is not None
                else None
            )
            mutation_input = (
                TicketState.from_dict(dict(serialized_before))
                if serialized_before is not None
                else None
            )
            candidate = mutate(mutation_input)
            if not isinstance(candidate, TicketState):
                raise TypeError("ticket state mutation must return TicketState")
            if candidate.state not in VALID_STATES:
                raise ValueError(f"invalid state: {candidate.state}")

            next_revision = (current_revision or 0) + 1
            candidate.state_revision = next_revision
            serialized_after = candidate.to_dict()
            next_serialized = dict(self._serialized)
            next_serialized[key] = serialized_after
            self._save(next_serialized)

            stored_state = TicketState.from_dict(dict(serialized_after))
            self._data[key] = stored_state
            self._serialized = next_serialized
            return TicketStateCommit(
                before=before,
                after=TicketState.from_dict(dict(serialized_after)),
                previous_revision=current_revision,
                state_revision=next_revision,
            )

    def delete(self, ticket_id: str | int) -> None:
        with self._file_lock():
            self._load_unlocked()
            key = str(ticket_id)
            if key not in self._data:
                return
            next_serialized = dict(self._serialized)
            next_serialized.pop(key, None)
            self._save(next_serialized)
            self._data.pop(key)
            self._serialized = next_serialized

    def delete_if_revision(
        self,
        ticket_id: str | int,
        *,
        expected_revision: int,
        expected_state: str | None = None,
        expected_active_session_id: str | None = None,
    ) -> bool:
        """Delete only the generation still owned by the caller."""
        with self._file_lock():
            self._load_unlocked()
            key = str(ticket_id)
            serialized = self._serialized.get(key)
            if serialized is None:
                return False
            current = TicketState.from_dict(dict(serialized))
            if current.state_revision != expected_revision:
                return False
            if expected_state is not None and current.state != expected_state:
                return False
            if (
                expected_active_session_id is not None
                and current.active_session_id != expected_active_session_id
            ):
                return False
            next_serialized = dict(self._serialized)
            next_serialized.pop(key)
            self._save(next_serialized)
            self._data.pop(key, None)
            self._serialized = next_serialized
            return True

    def list_by_state(self, state: str) -> list[str]:
        with self._file_lock():
            self._load_unlocked()
            return [k for k, v in self._data.items() if v.state == state]

    def all(self) -> dict[str, TicketState]:
        with self._file_lock():
            self._load_unlocked()
            return dict(self._data)

    def __len__(self) -> int:
        with self._file_lock():
            self._load_unlocked()
            return len(self._data)
