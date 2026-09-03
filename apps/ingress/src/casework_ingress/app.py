"""FastAPI webhook boundary for deterministic ZendeskCasework ingress."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.types import Lifespan

from .fakes import InMemoryCancelSink, InMemoryIdempotency, InMemoryJobSink
from .messaging import (
    MessagingPayloadError,
    decode_json_body,
    parse_messaging_payload,
    verify_zendesk_signature,
)
from .metrics import IngressMetricsPort, InMemoryIngressMetrics
from .ports import (
    AdmissionEvent,
    AdmissionPort,
    AdmissionUnavailable,
    CancelSink,
    IdempotencyPort,
    JobSink,
)
from .processing_rules import (
    NormalizedZendeskEvent,
    ProcessingDecision,
    ProcessingRuleConfig,
    evaluate_processing_rules,
)
from .zendesk import ZendeskTicketError, ZendeskTicketReader

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
    group_id: str | int | None = None
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
    snapshot_cursor: str | None = Field(default=None, min_length=1)

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
        has_assignment_group = bool(
            "group_id" in present and self.group_id is not None
        ) or bool(
            "assignee" in present and self.assignee is not None
        ) or bool("assignee_id" in present and self.assignee_id is not None)
        has_message = bool(
            "message_text" in present and self.message_text is not None
        ) or bool(
            "message" in present and isinstance(self.message, str)
        )
        return (
            {"tags", "status", "is_customer_message"}.issubset(present)
            and has_assignment_group
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
        assignment_group = self.group_id
        individual_assignee = None
        if assignment_group is None:
            # Compatibility for an older custom webhook template. New
            # templates and Messaging ticket lookups must supply group_id.
            assignment_group = (
                self.assignee if self.assignee is not None else self.assignee_id
            )
        else:
            individual_assignee = (
                self.assignee_id if self.assignee_id is not None else self.assignee
            )
        return NormalizedZendeskEvent(
            ticket_id=self.ticket_id,
            tags=self.tags or (),
            assignee=assignment_group,
            assignee_id=individual_assignee,
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


class ReadinessPort(Protocol):
    async def check(self) -> bool: ...


def _response(
    *, action: Action, reason: str, ticket_id: int
) -> WebhookResponse:
    return WebhookResponse(action=action, reason=reason, ticket_id=ticket_id)


def create_app(
    config: ProcessingRuleConfig | None = None,
    job_sink: JobSink | None = None,
    cancel_sink: CancelSink | None = None,
    idempotency: IdempotencyPort | None = None,
    metrics: IngressMetricsPort | None = None,
    admission: AdmissionPort | None = None,
    readiness: ReadinessPort | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
    ticket_reader: ZendeskTicketReader | None = None,
    messaging_signing_secret: str | None = None,
    messaging_secret: str | None = None,
    messaging_signature_max_age_seconds: float = 300.0,
    messaging_clock: Callable[[], float] | None = None,
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
    ingress_metrics = metrics or InMemoryIngressMetrics()
    signing_secret = (
        messaging_signing_secret
        if messaging_signing_secret is not None
        else messaging_secret or ""
    )

    app = FastAPI(title="casework-ingress", lifespan=lifespan)
    app.state.job_sink = jobs
    app.state.cancel_sink = cancellations
    app.state.idempotency = event_ids
    app.state.metrics = ingress_metrics
    app.state.ticket_reader = ticket_reader

    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def readyz() -> dict[str, str]:
        if readiness is not None and not await readiness.check():
            raise HTTPException(status_code=503, detail="postgres_not_ready")
        return {"status": "ready"}

    async def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(
            ingress_metrics.render(),
            media_type="text/plain; version=0.0.4",
        )

    def respond(*, action: Action, reason: str, ticket_id: int) -> WebhookResponse:
        ingress_metrics.record_webhook(action=action, reason=reason)
        return _response(action=action, reason=reason, ticket_id=ticket_id)

    async def webhook(payload: ZendeskWebhookPayload) -> WebhookResponse:
        if admission is not None:
            try:
                admitted = await admission.admit(
                    AdmissionEvent(
                        event_id=payload.event_id,
                        received_at=datetime.now(UTC),
                        normalized=payload.to_normalized_event(),
                        raw_payload=payload.model_dump(mode="json"),
                    )
                )
            except AdmissionUnavailable as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            return respond(
                action=admitted.action,
                reason=admitted.reason,
                ticket_id=admitted.ticket_id,
            )
        if not await event_ids.claim(payload.event_id):
            return respond(
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
                return respond(
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
                return respond(
                    action="duplicate",
                    reason="event_id_seen",
                    ticket_id=decision.ticket_id,
                )

        return respond(
            action=decision.action,
            reason=decision.reason,
            ticket_id=decision.ticket_id,
        )

    async def messaging_webhook(request: Request) -> WebhookResponse:
        body = await request.body()
        if not verify_zendesk_signature(
            secret=signing_secret,
            timestamp=request.headers.get(
                "X-Zendesk-Webhook-Signature-Timestamp", ""
            ),
            body=body,
            signature=request.headers.get("X-Zendesk-Webhook-Signature", ""),
            now=messaging_clock() if messaging_clock is not None else None,
            max_age_seconds=messaging_signature_max_age_seconds,
        ):
            raise HTTPException(status_code=401, detail="bad_signature")
        try:
            event = parse_messaging_payload(decode_json_body(body))
        except MessagingPayloadError as error:
            raise HTTPException(status_code=400, detail="bad_payload") from error

        mention_triggers = (
            "@david yang",
            "@davidyang",
            "**david yang**",
            "@agent",
            "@ai agent",
            "@ai",
            "@dify-agent",
            "@saas-agent",
        )
        is_operator_mention = any(
            tr in (event.message_text or "").lower() for tr in mention_triggers
        )
        if not event.is_customer_message and not is_operator_mention:
            return respond(
                action="ignore",
                reason="not_customer_message",
                ticket_id=event.ticket_id,
            )
        if ticket_reader is None:
            raise HTTPException(
                status_code=503, detail="zendesk_ticket_reader_not_configured"
            )
        try:
            ticket = await ticket_reader.fetch_ticket(event.ticket_id)
        except (ZendeskTicketError, OSError) as error:
            raise HTTPException(
                status_code=503, detail="zendesk_unavailable"
            ) from error

        try:
            normalized = event.to_normalized_event(ticket)
        except MessagingPayloadError as error:
            raise HTTPException(
                status_code=503, detail="zendesk_unavailable"
            ) from error
        if admission is not None:
            try:
                admitted = await admission.admit(
                    AdmissionEvent(
                        event_id=event.event_id,
                        received_at=datetime.now(UTC),
                        normalized=normalized,
                        raw_payload=event.safe_payload(),
                    )
                )
            except AdmissionUnavailable as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            return respond(
                action=admitted.action,
                reason=admitted.reason,
                ticket_id=admitted.ticket_id,
            )

        if not await event_ids.claim(event.event_id):
            return respond(
                action="duplicate",
                reason="event_id_seen",
                ticket_id=event.ticket_id,
            )
        decision = evaluate_processing_rules(normalized, processing_config)
        if decision.action == "enqueue":
            accepted = await jobs.submit_ticket(
                ticket_id=decision.ticket_id,
                event_id=event.event_id,
            )
            if accepted is False:
                return respond(
                    action="duplicate",
                    reason="event_id_seen",
                    ticket_id=decision.ticket_id,
                )
        elif decision.action == "cancel":
            accepted = await cancellations.cancel_ticket(
                ticket_id=decision.ticket_id,
                event_id=event.event_id,
            )
            if accepted is False:
                return respond(
                    action="duplicate",
                    reason="event_id_seen",
                    ticket_id=decision.ticket_id,
                )
        return respond(
            action=decision.action,
            reason=decision.reason,
            ticket_id=decision.ticket_id,
        )

    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route("/readyz", readyz, methods=["GET"])
    app.add_api_route("/metrics", metrics_endpoint, methods=["GET"])
    app.add_api_route(
        WEBHOOK_PATH,
        webhook,
        methods=["POST"],
        status_code=202,
        response_model=WebhookResponse,
    )
    app.add_api_route(
        "/webhook/zendesk-messaging",
        messaging_webhook,
        methods=["POST"],
        status_code=202,
        response_model=WebhookResponse,
    )

    return app
