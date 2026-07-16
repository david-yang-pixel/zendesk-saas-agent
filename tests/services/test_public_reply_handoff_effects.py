from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_unconfirmed_public_delivery_never_runs_handoff_effects(tmp_path) -> None:
    from zd_agent.services.public_reply_handoff_effects import (
        PostPublicHandoffStore,
        ensure_post_public_handoff_effects,
    )

    store = PostPublicHandoffStore(tmp_path / "handoff.sqlite3")
    feishu = AsyncMock(return_value=True)
    tag = AsyncMock(return_value=True)

    completed = await ensure_post_public_handoff_effects(
        ticket_id=3461,
        delivery_attempt_id="attempt-3461",
        body_with_footer="A human will follow up.",
        store=store,
        public_delivery_confirmed=False,
        feishu_sender=feishu,
        tag_sender=tag,
    )

    assert completed is False
    feishu.assert_not_awaited()
    tag.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_handoff_effect_failure_retries_only_missing_effect(
    tmp_path,
) -> None:
    from zd_agent.services.public_reply_handoff_effects import (
        PostPublicHandoffStore,
        ensure_post_public_handoff_effects,
    )

    path = tmp_path / "handoff.sqlite3"
    feishu = AsyncMock(side_effect=[False, True])
    tag = AsyncMock(return_value=True)

    first = await ensure_post_public_handoff_effects(
        ticket_id=3461,
        delivery_attempt_id="attempt-3461",
        body_with_footer="A human will follow up.",
        store=PostPublicHandoffStore(path),
        public_delivery_confirmed=True,
        feishu_sender=feishu,
        tag_sender=tag,
    )
    second = await ensure_post_public_handoff_effects(
        ticket_id=3461,
        delivery_attempt_id="attempt-3461",
        body_with_footer="Regenerated text must not replace the frozen handoff.",
        store=PostPublicHandoffStore(path),
        public_delivery_confirmed=True,
        feishu_sender=feishu,
        tag_sender=tag,
    )

    assert first is False
    assert second is True
    assert feishu.await_count == 2
    tag.assert_awaited_once()
    assert feishu.await_args_list[1].kwargs["agent_last_reply"] == (
        "A human will follow up."
    )
    reference = feishu.await_args_list[1].kwargs["idempotency_reference"]
    assert reference.startswith("public-handoff:")


@pytest.mark.asyncio
async def test_ambiguous_feishu_result_is_not_reissued_after_restart(tmp_path) -> None:
    from zd_agent.services.public_reply_handoff_effects import (
        PostPublicHandoffStore,
        ensure_post_public_handoff_effects,
    )

    path = tmp_path / "handoff.sqlite3"
    first = AsyncMock(side_effect=TimeoutError("response lost"))
    tag = AsyncMock(return_value=True)
    assert not await ensure_post_public_handoff_effects(
        ticket_id=3461,
        delivery_attempt_id="attempt-ambiguous",
        body_with_footer="A human will follow up.",
        store=PostPublicHandoffStore(path),
        public_delivery_confirmed=True,
        feishu_sender=first,
        tag_sender=tag,
    )

    retry = AsyncMock(return_value=True)
    assert not await ensure_post_public_handoff_effects(
        ticket_id=3461,
        delivery_attempt_id="attempt-ambiguous",
        body_with_footer="A human will follow up.",
        store=PostPublicHandoffStore(path),
        public_delivery_confirmed=True,
        feishu_sender=retry,
        tag_sender=tag,
    )
    retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_feishu_success_with_local_mark_failure_is_not_reissued(tmp_path) -> None:
    from zd_agent.services.public_reply_handoff_effects import (
        PostPublicHandoffStore,
        ensure_post_public_handoff_effects,
    )

    path = tmp_path / "handoff.sqlite3"
    first_store = PostPublicHandoffStore(path)
    real_mark = first_store.mark_effect

    def fail_only_feishu(reference: str, token: str, column: str) -> bool:
        if column == "feishu_sent":
            return False
        return real_mark(reference, token, column)

    first_store.mark_effect = fail_only_feishu  # type: ignore[method-assign]
    first = AsyncMock(return_value=True)
    tag = AsyncMock(return_value=True)
    assert not await ensure_post_public_handoff_effects(
        ticket_id=3461,
        delivery_attempt_id="attempt-mark-failure",
        body_with_footer="A human will follow up.",
        store=first_store,
        public_delivery_confirmed=True,
        feishu_sender=first,
        tag_sender=tag,
    )

    retry = AsyncMock(return_value=True)
    assert not await ensure_post_public_handoff_effects(
        ticket_id=3461,
        delivery_attempt_id="attempt-mark-failure",
        body_with_footer="A human will follow up.",
        store=PostPublicHandoffStore(path),
        public_delivery_confirmed=True,
        feishu_sender=retry,
        tag_sender=tag,
    )
    retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_activates_handoff_effects_only_after_public_outbox_delivery(
    tmp_path,
) -> None:
    from zd_agent.config import AppConfig
    from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
    from zd_agent.services.public_reply_handoff_effects import (
        PostPublicHandoffStore,
        reconcile_pending_post_public_handoff_effects,
    )
    from zd_agent.tools.public_reply_delivery import send_public_reply_safely

    delivery_path = tmp_path / "public.sqlite3"
    handoff_path = tmp_path / "handoff.sqlite3"
    delivery_store = PublicReplyDeliveryStore(delivery_path)
    handoff_store = PostPublicHandoffStore(handoff_path)
    handoff_store.prepare(
        ticket_id=3461,
        delivery_attempt_id="attempt-3461",
        body_with_footer="A human will follow up.",
        public_delivery_confirmed=False,
    )

    cfg = AppConfig()
    cfg.public_reply.browser_relay_url = "https://relay.example.test/mcp"
    cfg.public_reply.browser_relay_api_key = "secret"
    cfg.public_reply.freshness_callback_url = "https://agent.example.test/internal/pre-send-freshness"
    await send_public_reply_safely(
        cfg=cfg,
        ticket_id=3461,
        body_with_footer="A human will follow up.",
        reply_type="handoff",
        conversation_id="conversation-3461",
        snapshot_customer_message_count=None,
        snapshot_customer_last_message_id=None,
        delivery_attempt_id="attempt-3461",
        delivery_store=delivery_store,
        delivery_store_factory=lambda _cfg: delivery_store,
        zendesk_tool_caller=AsyncMock(
            return_value={"success": True, "data": {"comments": []}},
        ),
        relay_sender=AsyncMock(),
    )

    feishu = AsyncMock(return_value=True)
    tag = AsyncMock(return_value=True)
    reconciled = await reconcile_pending_post_public_handoff_effects(
        store=PostPublicHandoffStore(handoff_path),
        delivery_store=PublicReplyDeliveryStore(delivery_path),
        feishu_sender=feishu,
        tag_sender=tag,
    )
    repeated = await reconcile_pending_post_public_handoff_effects(
        store=PostPublicHandoffStore(handoff_path),
        delivery_store=PublicReplyDeliveryStore(delivery_path),
        feishu_sender=feishu,
        tag_sender=tag,
    )

    assert reconciled == 1
    assert repeated == 0
    feishu.assert_awaited_once()
    tag.assert_awaited_once()
