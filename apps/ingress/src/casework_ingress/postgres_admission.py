"""Atomic PostgreSQL admission adapter for normalized Zendesk webhooks."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from casework_ingress.intake_form import is_contact_form_only
from casework_ingress.ports import (
    AdmissionEvent,
    AdmissionResult,
    AdmissionUnavailable,
)
from casework_ingress.processing_rules import (
    ProcessingRuleConfig,
    evaluate_processing_rules,
)
from casework_persistence.repositories import (
    ConfigRevisionMissing,
    ConfigRevisionRepository,
    ConfigSnapshot,
    EventRepository,
    HumanCancelRepository,
    HumanHandoffRepository,
    JobRepository,
    SessionPort,
    TicketRepository,
)
from contracts.runtime import (
    ChildModelBinding,
    ModelBinding,
    registry_digest,
    runtime_model_digest,
)


class _Projection(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _ModelConfig(_Projection):
    provider: str = Field(min_length=1)
    base_url: str = Field(default="https://api.openai.com", min_length=1)
    model: str = Field(min_length=1)
    credential_ref: str = Field(min_length=1)


class _SubagentConfig(_ModelConfig):
    slot_id: str = Field(min_length=1)
    enabled: bool = True
    context_window_tokens: int = Field(default=32_000, gt=0, le=1_000_000)
    max_tool_rounds: int = Field(default=12, gt=0, le=100)
    max_parallel: int = Field(default=1, ge=1, le=8)


class _RulesConfig(_Projection):
    saas_tag: str = Field(min_length=1)
    saas_assignee: str = Field(min_length=1)
    statuses: tuple[Literal["new", "open"], ...] = ("new", "open")
    human_aliases: tuple[str, ...] = ()


class _RuntimeConfig(_Projection):
    desired_state: Literal["running", "paused"]
    root_model: _ModelConfig
    subagent_slots: tuple[_SubagentConfig, ...] = ()
    rules: _RulesConfig
    enabled_skills: tuple[str, ...] = ()
    enabled_tools: tuple[str, ...] = ()


TransactionScope = Callable[[], AbstractAsyncContextManager[SessionPort]]


def postgres_transaction_scope(
    sessions: async_sessionmaker[AsyncSession],
) -> TransactionScope:
    @asynccontextmanager
    async def scope():
        async with sessions() as session, session.begin():
            yield cast(SessionPort, session)

    return scope


class PostgresAdmission:
    """Load config, evaluate rules and mutate durable state in one transaction."""

    def __init__(self, *, transaction_scope: TransactionScope) -> None:
        self._transaction_scope = transaction_scope

    async def admit(self, event: AdmissionEvent) -> AdmissionResult:
        try:
            return await self._admit(event)
        except ConfigRevisionMissing as error:
            raise AdmissionUnavailable("configuration_not_ready") from error
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise AdmissionUnavailable("postgres_unavailable") from error

    async def _admit(self, event: AdmissionEvent) -> AdmissionResult:
        async with self._transaction_scope() as session:
            revision = await ConfigRevisionRepository(session).current()
            runtime = _RuntimeConfig.model_validate(revision.config)
            decision = evaluate_processing_rules(
                event.normalized,
                ProcessingRuleConfig(
                    saas_tag=runtime.rules.saas_tag,
                    saas_assignee=runtime.rules.saas_assignee,
                    statuses=runtime.rules.statuses,
                    human_aliases=runtime.rules.human_aliases,
                ),
            )
            provider_time = self._provider_message_time(event)
            occurred_at = provider_time or event.received_at
            if decision.action == "cancel":
                cancelled = await HumanCancelRepository(session).apply(
                    event_id=event.event_id,
                    ticket_id=event.normalized.ticket_id,
                    occurred_at=occurred_at,
                    received_at=event.received_at,
                    message_id=self._message_cursor(event),
                    reason="human",
                    payload=event.raw_payload,
                )
                if cancelled.duplicate_event:
                    return self._duplicate(event)
                if cancelled.stale_event:
                    return AdmissionResult(
                        action="ignore",
                        reason="stale_human_request",
                        ticket_id=event.normalized.ticket_id,
                    )
                # The worker alerts Feishu and tags the ticket for every
                # accepted command, and acknowledges the customer only for the
                # first command of a takeover so repeats are not answered.
                await HumanHandoffRepository(session).record(
                    event_id=event.event_id,
                    ticket_id=event.normalized.ticket_id,
                    acknowledge=not cancelled.takeover_already_active,
                    customer_message=event.normalized.message_text,
                    occurred_at=event.received_at,
                )
                return AdmissionResult(
                    action="cancel",
                    reason="human",
                    ticket_id=event.normalized.ticket_id,
                )

            # Admission, requeues and delivery all serialize on this row.
            state = await TicketRepository(session).ensure_state(event.normalized.ticket_id)
            if decision.reason != "operator_mention" and state.human_cancelled_at is not None:
                return AdmissionResult(
                    action="ignore",
                    reason="human_takeover_active",
                    ticket_id=event.normalized.ticket_id,
                )

            is_mention = decision.reason == "operator_mention"
            payload = event.raw_payload
            if is_mention:
                payload = {**payload, "body": event.normalized.message_text}
            form_only = (
                event.normalized.is_customer_message
                and payload.get("has_attachments") is not True
                and (payload.get("form_only") is True or is_contact_form_only(event.normalized.message_text))
            )
            needs_reply = (
                decision.action == "enqueue" and event.normalized.is_customer_message
                and (form_only or bool(event.normalized.message_text.strip()) or payload.get("has_attachments") is True)
            )
            events = EventRepository(session)
            mention_cursor = self._mention_cursor(event) if is_mention else None
            if mention_cursor and await events.message_event_exists(
                event.normalized.ticket_id, mention_cursor, "operator.mention"
            ):
                return self._duplicate(event)
            message_id = self._message_cursor(event) if needs_reply else None
            tracked = (
                await events.tracked_customer_message(event.normalized.ticket_id, message_id)
                if message_id is not None else None
            )
            next_seq = state.latest_customer_seq + 1 if needs_reply and tracked is None else None
            event_type = "operator.mention" if is_mention else "ticket.updated"
            inserted = await events.record(
                event_id=event.event_id,
                ticket_id=event.normalized.ticket_id,
                occurred_at=occurred_at,
                source="zendesk",
                customer_origin=(
                    "customer" if event.normalized.is_customer_message else "internal"
                ),
                event_type="customer.form_only" if form_only else event_type,
                message_id=mention_cursor or message_id,
                customer_seq=next_seq,
                payload=payload,
            )
            if not inserted:
                return self._duplicate(event)
            if next_seq is not None:
                state.latest_customer_seq = next_seq
                latest_at = state.latest_customer_at
                if latest_at is None or occurred_at > latest_at.replace(tzinfo=latest_at.tzinfo or UTC):
                    state.latest_customer_at = occurred_at
                state.updated_at = event.received_at
            if is_mention:
                cancelled_at = state.human_cancelled_at
                resumed_at = state.human_resumed_at
                blocked_by_takeover = cancelled_at is not None and (
                    provider_time is None
                    or provider_time <= cancelled_at.replace(tzinfo=cancelled_at.tzinfo or UTC)
                )
                superseded = provider_time is not None and resumed_at is not None and (
                    provider_time < resumed_at.replace(tzinfo=resumed_at.tzinfo or UTC)
                )
                if blocked_by_takeover or superseded:
                    return AdmissionResult(
                        action="ignore",
                        reason="stale_operator_mention",
                        ticket_id=event.normalized.ticket_id,
                    )
                # Legacy notes without provider time can request a draft on an unlocked ticket.
                if provider_time is not None and (
                    resumed_at is None or provider_time > resumed_at.replace(tzinfo=resumed_at.tzinfo or UTC)
                ):
                    state.human_cancelled_at = None
                    state.human_resumed_at = provider_time
                    state.human_resume_cursor = mention_cursor
                    state.updated_at = event.received_at
            if decision.action == "ignore":
                return AdmissionResult(
                    action="ignore",
                    reason=decision.reason,
                    ticket_id=event.normalized.ticket_id,
                )
            if tracked is not None and (tracked.answered_by_job_id is not None or tracked.answered_by_staff):
                return AdmissionResult(
                    action="ignore",
                    reason="customer_message_covered",
                    ticket_id=event.normalized.ticket_id,
                )
            if runtime.desired_state != "running":
                return AdmissionResult(
                    action="ignore",
                    reason="agent_paused",
                    ticket_id=event.normalized.ticket_id,
                )

            queued = await JobRepository(session).enqueue(
                ticket_id=event.normalized.ticket_id,
                event_id=event.event_id,
                expected_event_id=mention_cursor or message_id or self._expected_event_id(event),
                is_operator_mention=is_mention,
                config=self.frozen_config(revision.revision, runtime),
                job_id=str(uuid4()),
                # Operator mentions are explicit requests; the customer-message
                # debounce is unnecessary and delays every internal-note run.
                available_at=(
                    event.received_at
                    if decision.reason == "operator_mention"
                    else event.received_at + timedelta(seconds=20)
                ),
            )
            if not queued.accepted:
                return AdmissionResult(
                    action="ignore",
                    reason=queued.reason or "not_enqueued",
                    ticket_id=event.normalized.ticket_id,
                )
            return AdmissionResult(
                action="enqueue",
                reason="eligible",
                ticket_id=event.normalized.ticket_id,
            )

    @staticmethod
    def _expected_event_id(event: AdmissionEvent) -> str:
        cursor = event.raw_payload.get("snapshot_cursor")
        if isinstance(cursor, str) and 0 < len(cursor.strip()) <= 255:
            return cursor.strip()
        return event.event_id

    @staticmethod
    def _message_cursor(event: AdmissionEvent) -> str | None:
        cursor = event.raw_payload.get("snapshot_cursor")
        if not isinstance(cursor, str):
            return None
        cursor = cursor.strip()
        for provider_prefix, namespace in (
            ("zen:sunco:conversation_message:", "messaging:"),
            ("zen:ticket:comment:", "zendesk:"),
            ("messaging:", "messaging:"),
            ("zendesk:", "zendesk:"),
        ):
            if cursor.startswith(provider_prefix):
                message_id = cursor[len(provider_prefix):].strip()
                normalized = f"{namespace}{message_id}"
                if message_id and ":" not in message_id and len(normalized) <= 255:
                    return normalized
        return None

    @classmethod
    def _mention_cursor(cls, event: AdmissionEvent) -> str:
        cursor = cls._expected_event_id(event)
        return cursor if cursor.startswith("mention:") else event.event_id

    @staticmethod
    def _provider_message_time(event: AdmissionEvent) -> datetime | None:
        value = event.raw_payload.get("message_created_at")
        if isinstance(value, str):
            try:
                created_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if created_at.tzinfo is not None:
                    return created_at
            except ValueError:
                pass
        return None


    @staticmethod
    def _duplicate(event: AdmissionEvent) -> AdmissionResult:
        return AdmissionResult(
            action="duplicate",
            reason="event_id_seen",
            ticket_id=event.normalized.ticket_id,
        )

    @classmethod
    def frozen_config(
        cls, revision: str, runtime: _RuntimeConfig
    ) -> ConfigSnapshot:
        enabled_children = tuple(
            slot for slot in runtime.subagent_slots if slot.enabled
        )
        root_binding = ModelBinding.model_validate(runtime.root_model.model_dump())
        child_bindings = tuple(
            ChildModelBinding.model_validate(slot.model_dump())
            for slot in enabled_children
        )
        return ConfigSnapshot(
            config_revision=revision,
            root_model_config_id="root",
            child_model_config_ids=tuple(slot.slot_id for slot in enabled_children),
            model_digest=runtime_model_digest(root_binding, child_bindings),
            skill_digest=registry_digest(runtime.enabled_skills),
            tool_digest=registry_digest(runtime.enabled_tools),
        )


def frozen_config_snapshot(
    *, revision: str, config: dict[str, object]
) -> ConfigSnapshot:
    """Build the canonical queue snapshot used by cross-service contract tests."""

    return PostgresAdmission.frozen_config(
        revision, _RuntimeConfig.model_validate(config)
    )


__all__ = [
    "PostgresAdmission",
    "frozen_config_snapshot",
    "postgres_transaction_scope",
]
