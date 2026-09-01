"""Atomic PostgreSQL admission adapter for normalized Zendesk webhooks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
)


class _Projection(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _ModelConfig(_Projection):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    credential_ref: str = Field(min_length=1)


class _SubagentConfig(_ModelConfig):
    slot_id: str = Field(min_length=1)
    enabled: bool = True


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
                config=self._frozen_config(revision.revision, runtime),
                job_id=str(uuid4()),
                available_at=event.received_at,
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
    def _duplicate(event: AdmissionEvent) -> AdmissionResult:
        return AdmissionResult(
            action="duplicate",
            reason="event_id_seen",
            ticket_id=event.normalized.ticket_id,
        )

    @classmethod
    def _frozen_config(
        cls, revision: str, runtime: _RuntimeConfig
    ) -> ConfigSnapshot:
        enabled_children = tuple(
            slot for slot in runtime.subagent_slots if slot.enabled
        )
        model_payload = {
            "root": runtime.root_model.model_dump(mode="json"),
            "children": [slot.model_dump(mode="json") for slot in enabled_children],
        }
        return ConfigSnapshot(
            config_revision=revision,
            root_model_config_id="root",
            child_model_config_ids=tuple(slot.slot_id for slot in enabled_children),
            model_digest=cls._digest(model_payload),
            skill_digest=cls._digest(runtime.enabled_skills),
            tool_digest=cls._digest(runtime.enabled_tools),
        )

    @staticmethod
    def _digest(value: object) -> str:
        encoded = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = ["PostgresAdmission", "postgres_transaction_scope"]
