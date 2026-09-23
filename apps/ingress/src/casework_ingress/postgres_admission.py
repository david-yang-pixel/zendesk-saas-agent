"""Atomic PostgreSQL admission adapter for normalized Zendesk webhooks."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError

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
            if decision.action == "cancel":
                cancelled = await HumanCancelRepository(session).apply(
                    event_id=event.event_id,
                    ticket_id=event.normalized.ticket_id,
                    occurred_at=event.received_at,
                    reason="human",
                    payload=event.raw_payload,
                )
                if cancelled.duplicate_event:
                    return self._duplicate(event)
                return AdmissionResult(
                    action="cancel",
                    reason="human",
                    ticket_id=event.normalized.ticket_id,
                )

            # Check if ticket was previously handed over to human
            state = await TicketRepository(session).ensure_state(event.normalized.ticket_id)
            if decision.reason != "operator_mention" and state.human_cancelled_at is not None:
                return AdmissionResult(
                    action="ignore",
                    reason="human_takeover_active",
                    ticket_id=event.normalized.ticket_id,
                )

            inserted = await EventRepository(session).record(
                event_id=event.event_id,
                ticket_id=event.normalized.ticket_id,
                occurred_at=event.received_at,
                source="zendesk",
                customer_origin=(
                    "customer" if event.normalized.is_customer_message else "internal"
                ),
                payload=event.raw_payload,
            )
            if not inserted:
                return self._duplicate(event)
            if decision.reason == "operator_mention":
                # A replay must never undo a later human cancellation.
                state.human_cancelled_at = None
                state.updated_at = event.received_at
            if decision.action == "ignore":
                return AdmissionResult(
                    action="ignore",
                    reason=decision.reason,
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
                expected_event_id=self._expected_event_id(event),
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
        if isinstance(cursor, str) and cursor.strip():
            return cursor.strip()
        return event.event_id

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
