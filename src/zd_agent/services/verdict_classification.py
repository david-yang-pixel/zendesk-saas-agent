"""Verdict classification orchestration for customer ticket messages."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from zd_agent.log import get_logger
from zd_agent.trigger.decider import PreCheckVerdict, classify_messages

logger = get_logger(module="services.verdict_classification")

CollectImagesFn = Callable[[list[dict]], Awaitable[list[str]]]
ClassifyMessagesFn = Callable[..., Awaitable[PreCheckVerdict]]


@dataclass(frozen=True)
class VerdictClassificationResult:
    verdict: PreCheckVerdict
    images_used: int


def _count_message_images(customer_msgs: list[dict]) -> int:
    return sum(len(message.get("image_urls") or []) for message in customer_msgs)


async def classify_ticket_messages(
    *,
    ticket_id: int,
    customer_msgs: list[dict],
    model: str,
    api_key: str,
    base_url: str | None = None,
    enable_vision_input: bool = False,
    collect_recent_image_data_urls_fn: CollectImagesFn | None = None,
    classify_messages_fn: ClassifyMessagesFn | None = None,
) -> VerdictClassificationResult:
    """Collect image context and classify customer messages into a verdict."""
    if collect_recent_image_data_urls_fn is None:
        from zd_agent.services.ticket_messages import _collect_recent_image_data_urls

        collect_recent_image_data_urls_fn = _collect_recent_image_data_urls
    classify_messages_fn = classify_messages_fn or classify_messages

    issue_messages = [
        message for message in customer_msgs
        if message.get("content_type") != "formResponse"
    ]
    bodies = [message.get("body") or "" for message in issue_messages]
    image_count = _count_message_images(customer_msgs)
    image_data_urls = (
        await collect_recent_image_data_urls_fn(customer_msgs)
        if enable_vision_input
        else []
    )
    verdict = await classify_messages_fn(
        messages=bodies,
        model=model,
        api_key=api_key,
        base_url=base_url,
        image_data_urls=image_data_urls,
        image_count=image_count,
        enable_vision_input=enable_vision_input,
    )
    logger.info(
        "trigger_verdict",
        ticket_id=ticket_id,
        verdict=verdict.verdict,
        completeness=verdict.completeness,
        reason=verdict.reason[:100],
        images_used=len(image_data_urls),
        model=model,
    )
    return VerdictClassificationResult(
        verdict=verdict,
        images_used=len(image_data_urls),
    )
