"""Ingress gate shared by Zendesk webhook handlers."""

from __future__ import annotations

from aiohttp import web

from zd_agent.log import get_logger
from zd_agent.services.app_context import agent_lifecycle
from zd_agent.services.event_watermark import timestamp_unix

logger = get_logger(module="services.webhook_lifecycle")


def drop_when_agent_offline(
    request: web.Request,
    *,
    source: str,
    ticket_id: str,
    dedup: object,
    dedup_key: str,
) -> web.Response | None:
    """ACK a valid event without queueing when no worker may own it."""
    lifecycle = agent_lifecycle(request.app)
    ingress_state = getattr(lifecycle, "ingress_state", None)
    if callable(ingress_state) and ingress_state() == "unavailable":
        logger.error("zendesk_event_rejected_lifecycle_unavailable", source=source)
        return web.Response(status=503, text="lifecycle state unavailable")
    if lifecycle is None or lifecycle.accepting_events():
        return None
    add = getattr(dedup, "add", None)
    if callable(add):
        add(dedup_key)
    record = getattr(lifecycle, "record_dropped_event", None)
    if callable(record):
        record(source=source, ticket_id=ticket_id)
    logger.warning(
        "zendesk_event_discarded_agent_offline",
        source=source,
        ticket_id=ticket_id,
        lifecycle_status=lifecycle.status().get("status"),
    )
    return web.Response(status=200, text="agent offline; event discarded")


def drop_when_before_online_watermark(
    request: web.Request,
    *,
    source: str,
    ticket_id: str,
    event_time: object,
    dedup: object,
    dedup_key: str,
) -> web.Response | None:
    """Discard delayed delivery of an event created in the offline epoch."""
    lifecycle = agent_lifecycle(request.app)
    get_boundary = getattr(lifecycle, "accept_after", None)
    if not callable(get_boundary):
        return None
    boundary = float(get_boundary() or 0.0)
    observed = timestamp_unix(event_time)
    if boundary <= 0 or (observed is not None and observed > boundary):
        return None
    add = getattr(dedup, "add", None)
    if callable(add):
        add(dedup_key)
    record = getattr(lifecycle, "record_dropped_event", None)
    if callable(record):
        record(source=f"{source}:offline_watermark", ticket_id=ticket_id)
    logger.warning(
        "zendesk_event_discarded_before_online_watermark",
        source=source,
        ticket_id=ticket_id,
        event_time=observed,
        event_time_missing=observed is None,
        accept_after=boundary,
    )
    return web.Response(status=200, text="offline epoch event discarded")
