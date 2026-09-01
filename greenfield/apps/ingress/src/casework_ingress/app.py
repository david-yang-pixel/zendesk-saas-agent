"""FastAPI webhook boundary for deterministic ZendeskCasework ingress."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .fakes import InMemoryCancelSink, InMemoryIdempotency, InMemoryJobSink
from .ports import CancelSink, IdempotencyPort, JobSink
from .processing_rules import (
    NormalizedZendeskEvent,
    ProcessingDecision,
    ProcessingRuleConfig,
    evaluate_processing_rules,
)

WEBHOOK_PATH = "/webhook/zendesk"


class ZendeskWebhookPayload(BaseModel):
    """Small request projection used to build a normalized rule event.

    Zendesk may include additional delivery data.  It is intentionally ignored
    here so only fields needed by the pure evaluator cross this boundary.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    event_id: str = Field(min_length=1)
    ticket_id: StrictInt = Field(gt=0)
    tags: tuple[str, ...] | None = None
    assignee: str | int | None = None
    assignee_id: str | int | None = None
    status: str | None = None
    is_customer_message: StrictBool | None = None
    message_text: str | None = None
    message: object | None = None
    actor: object | None = None
    visibility: object | None = None
    message_visibility: object | None = None
    is_public: object | None = None

    @staticmethod
    def _normalise_origin_value(value: object | None) -> str | None:
        if not isinstance(value, str):
            return None
        return value.strip().casefold()

    def _has_customer_origin(self) -> bool:
        visibility_value = self.visibility
        if visibility_value is None:
            visibility_value = self.message_visibility
        visibility = self._normalise_origin_value(visibility_value)

        if self.actor is not None:
            actor = self._normalise_origin_value(self.actor)
            if actor not in {"customer", "end_user", "end-user", "requester"}:
                return False
            # An explicit actor must also carry an explicit public visibility.
            if visibility != "public":
                return False
        elif visibility is not None and visibility != "public":
            return False

        if self.is_public is None:
            return True
        if isinstance(self.is_public, bool):
            return self.is_public
        return self._normalise_origin_value(self.is_public) in {"true", "public"}

    def _has_all_rule_fields(self) -> bool:
        present = self.model_fields_set
        has_assignee = bool(
            "assignee" in present and self.assignee is not None
        ) or bool("assignee_id" in present and self.assignee_id is not None)
        has_message = bool(
            "message_text" in present and self.message_text is not None
        ) or bool(
            "message" in present and isinstance(self.message, str)
        )
        return (
            {"tags", "status", "is_customer_message"}.issubset(present)
            and has_assignee
            and has_message
        )

    def to_normalized_event(self) -> NormalizedZendeskEvent:
        customer_message = bool(
            self.is_customer_message
            and self._has_all_rule_fields()
            and self._has_customer_origin()
        )
        message_text = self.message_text
        if message_text is None:
            message_text = self.message if isinstance(self.message, str) else ""
        return NormalizedZendeskEvent(
            ticket_id=self.ticket_id,
            tags=self.tags or (),
            assignee=self.assignee if self.assignee is not None else self.assignee_id,
            status=self.status or "",
            is_customer_message=customer_message,
            message_text=message_text,
        )


Action = Literal["enqueue", "ignore", "cancel", "duplicate"]


class WebhookResponse(BaseModel):
    """Stable admission result returned to webhook delivery callers."""

    action: Action
    reason: str
    ticket_id: int


def _response(
    *, action: Action, reason: str, ticket_id: int
) -> WebhookResponse:
    return WebhookResponse(action=action, reason=reason, ticket_id=ticket_id)


def create_app(
    config: ProcessingRuleConfig | None = None,
    job_sink: JobSink | None = None,
    cancel_sink: CancelSink | None = None,
    idempotency: IdempotencyPort | None = None,
) -> FastAPI:
    """Build the ingress app with injected side-effect destinations.

    The defaults make the factory runnable for local development.  Production
    callers should inject durable sinks at process composition time.
    """

    processing_config = config or ProcessingRuleConfig(
        saas_tag="saas", saas_assignee="saas-support"
    )
    jobs = job_sink or InMemoryJobSink()
    cancellations = cancel_sink or InMemoryCancelSink()
    event_ids = idempotency or InMemoryIdempotency()

    app = FastAPI(title="casework-ingress")
    app.state.job_sink = jobs
    app.state.cancel_sink = cancellations
    app.state.idempotency = event_ids

    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def webhook(payload: ZendeskWebhookPayload) -> WebhookResponse:
        if not await event_ids.claim(payload.event_id):
            return _response(
                action="duplicate",
                reason="event_id_seen",
                ticket_id=payload.ticket_id,
            )
        decision: ProcessingDecision = evaluate_processing_rules(
            payload.to_normalized_event(), processing_config
        )
        if decision.action == "enqueue":
            accepted = await jobs.submit_ticket(
                ticket_id=decision.ticket_id,
                event_id=payload.event_id,
            )
            if accepted is False:
                return _response(
                    action="duplicate",
                    reason="event_id_seen",
                    ticket_id=decision.ticket_id,
                )
        elif decision.action == "cancel":
            accepted = await cancellations.cancel_ticket(
                ticket_id=decision.ticket_id,
                event_id=payload.event_id,
            )
            if accepted is False:
                return _response(
                    action="duplicate",
                    reason="event_id_seen",
                    ticket_id=decision.ticket_id,
                )

        return _response(
            action=decision.action,
            reason=decision.reason,
            ticket_id=decision.ticket_id,
        )

    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route(
        WEBHOOK_PATH,
        webhook,
        methods=["POST"],
        status_code=202,
        response_model=WebhookResponse,
    )

    return app
