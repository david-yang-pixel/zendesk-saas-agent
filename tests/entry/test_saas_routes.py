"""Tests for SaaS aiohttp route registration."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.app_context import install_saas_app_context
from zd_agent.services.app_context import (
    BACKGROUND_TASK_KEYS,
    TRIGGER_TASK_KEY,
)
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.saas_routes import (
    PUBLIC_PRE_SEND_FRESHNESS_PATH,
    handle_agent_config,
    handle_agent_models,
    handle_healthz,
    handle_update_agent_config,
    handle_pre_send_freshness,
    register_saas_routes,
)
from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.runtime.pre_send_check import (
    FreshnessRecovery,
    FreshnessRecoveryAction,
    FreshnessResult,
)


def test_register_saas_routes_installs_expected_routes() -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.webhook.zendesk_path = "/webhook/zendesk"
    cfg.webhook.zendesk_messaging_path = "/webhook/zendesk-messaging"
    cfg.public_reply.freshness_callback_url = (
        "https://agent.example.test/webhook/relay-pre-send-freshness"
    )

    register_saas_routes(app=app, cfg=cfg)

    route_paths = {route.resource.canonical for route in app.router.routes()}
    assert "/webhook/zendesk" in route_paths
    assert "/webhook/zendesk-messaging" in route_paths
    assert "/internal/pre-send-freshness" in route_paths
    assert PUBLIC_PRE_SEND_FRESHNESS_PATH in route_paths
    assert "/agent/config" in route_paths
    assert "/agent/models" in route_paths
    assert "/agent/tools" in route_paths
    assert "/healthz" in route_paths
    assert "/teleport-auth" not in route_paths
    assert "/internal/teleport-auth/issue" not in route_paths
    assert "/webhook/feishu" not in route_paths


def test_public_freshness_route_is_registered_without_url_config() -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.webhook.zendesk_path = "/webhook/zendesk"
    cfg.webhook.zendesk_messaging_path = "/webhook/zendesk-messaging"
    cfg.public_reply.freshness_callback_url = ""

    register_saas_routes(app=app, cfg=cfg)

    route_paths = {route.resource.canonical for route in app.router.routes()}
    assert PUBLIC_PRE_SEND_FRESHNESS_PATH in route_paths


class _Request:
    def __init__(self, *, app, headers, payload, query=None):
        self.app = app
        self.headers = headers
        self._payload = payload
        self.query = query or {}

    async def json(self):
        return self._payload


class _RunningTask:
    def done(self):
        return False

    def cancelled(self):
        return False


class _FailedTask:
    def done(self):
        return True

    def cancelled(self):
        return False

    def exception(self):
        return RuntimeError("consumer stopped")


@pytest.mark.asyncio
async def test_healthz_reports_v2_runtime_and_durable_backlog(tmp_path) -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.casework.mode = ExecutionMode.CASEWORK
    queue = DurableTriggerQueue(tmp_path / "trigger_queue.sqlite3")
    await queue.put({"ticket_id": 1, "source": "test", "_queue_key": "one"})
    manager = MagicMock(active_count=2)
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=manager,
        trigger_queue=queue,
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    for key in BACKGROUND_TASK_KEYS:
        app[key] = _RunningTask()

    response = await handle_healthz(
        _Request(app=app, headers={}, payload={}),
    )
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["ok"] is True
    assert payload["ready"] is True
    assert payload["casework_mode"] == "casework"
    assert payload["queue"] == {"pending": 1, "dead_letter": 0}
    assert payload["background_tasks"]["trigger_task"] == "running"
    assert payload["active_sessions"] == 2
    assert payload["outbox"] == {
        "casework_ownership_pending": 0,
        "internal_note_pending": 0,
        "public_reply_ambiguous": 0,
        "historical_handoff_pending": 0,
        "manual_reconciliation_tickets": 0,
    }


@pytest.mark.asyncio
async def test_healthz_fails_closed_when_trigger_consumer_stops(tmp_path) -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.casework.mode = ExecutionMode.CASEWORK
    queue = DurableTriggerQueue(tmp_path / "trigger_queue.sqlite3")
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(active_count=0),
        trigger_queue=queue,
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    for key in BACKGROUND_TASK_KEYS:
        app[key] = _RunningTask()
    app[TRIGGER_TASK_KEY] = _FailedTask()

    response = await handle_healthz(
        _Request(app=app, headers={}, payload={}),
    )
    payload = json.loads(response.text)

    assert response.status == 503
    assert payload["ok"] is False
    assert payload["ready"] is False
    assert payload["background_tasks"]["trigger_task"] == "failed"


@pytest.mark.asyncio
async def test_pre_send_freshness_rejects_bad_api_key() -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer wrong"},
        payload={
            "ticket_id": 1,
            "snapshot_customer_message_count": 1,
        },
    )

    response = await handle_pre_send_freshness(request)

    assert response.status == 401


@pytest.mark.asyncio
async def test_pre_send_freshness_rejects_malformed_callback_payload() -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer relay-secret"},
        payload={"ticket_id": "not-an-integer"},
    )

    response = await handle_pre_send_freshness(request)

    assert response.status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"ticket_id": 0, "snapshot_customer_message_count": 1},
        {"ticket_id": True, "snapshot_customer_message_count": 1},
        {"ticket_id": 1, "snapshot_customer_message_count": -1},
        {"ticket_id": 1, "snapshot_customer_message_count": True},
        {
            "ticket_id": 1,
            "snapshot_customer_message_count": 1,
            "snapshot_customer_last_message_id": {"unexpected": "object"},
        },
    ],
)
async def test_pre_send_freshness_rejects_out_of_range_payload(payload) -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer relay-secret"},
        payload=payload,
    )

    response = await handle_pre_send_freshness(request)

    assert response.status == 400


@pytest.mark.asyncio
async def test_pre_send_freshness_blocks_when_ticket_fetch_returns_none(
    monkeypatch,
) -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        AsyncMock(return_value=([], None)),
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer relay-secret"},
        payload={
            "ticket_id": 1,
            "snapshot_customer_message_count": 0,
        },
    )

    response = await handle_pre_send_freshness(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["status"] == "stale_context"
    assert payload["continue_send"] is False
    assert payload["reason"] == "fetch_error: RuntimeError"


@pytest.mark.asyncio
async def test_pre_send_freshness_blocks_a_form_added_after_initial_tool_gate(
    monkeypatch,
) -> None:
    """The relay callback must re-run full readiness, including system forms."""
    app = web.Application()
    cfg = MagicMock()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    readiness = AsyncMock(
        return_value=FreshnessResult(
            continue_send=False,
            reason="pending_blocking_form",
            recovery=FreshnessRecovery(
                action=FreshnessRecoveryAction.DEFER,
                reason="pending_blocking_form",
            ),
        )
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_public_reply_readiness",
        readiness,
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer relay-secret"},
        payload={
            "ticket_id": 1,
            "snapshot_customer_message_count": 1,
            "snapshot_customer_last_message_id": "m1",
        },
    )

    response = await handle_pre_send_freshness(request)
    payload = json.loads(response.text)

    assert payload["status"] == "stale_context"
    assert payload["continue_send"] is False
    assert payload["reason"] == "pending_blocking_form"
    readiness.assert_awaited_once_with(
        ticket_id=1,
        snapshot_count=1,
        snapshot_last_id="m1",
        skip_llm=True,
        cfg=cfg,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ticket_updates", "reason"),
    [
        ({"status": "solved"}, "ticket_status_solved"),
        ({"status": "closed"}, "ticket_status_closed"),
        ({"assignee_id": 901}, "human_assignee"),
        (
            {
                "_zd_agent_comments": [
                    {
                        "id": "h2",
                        "author_id": 901,
                        "public": True,
                        "created_at": "2026-08-11T10:01:00Z",
                        "body": "A support engineer has taken this ticket.",
                    }
                ]
            },
            "newer_human_public_comment",
        ),
    ],
)
async def test_final_relay_readiness_blocks_lifecycle_races_without_new_customer(
    ticket_updates: dict,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = web.Application()
    cfg = AppConfig()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    cfg.zendesk.agent_user_id = 900
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    ticket = {
        "status": "open",
        "assignee_id": None,
        "requester_id": 100,
        "_zd_agent_comments": [],
        "_zd_agent_conversation_events": [],
        **ticket_updates,
    }
    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        AsyncMock(
            return_value=(
                [
                    {
                        "id": "m1",
                        "author_id": 100,
                        "created_at": "2026-08-11T10:00:00Z",
                        "body": "Original customer question",
                    }
                ],
                ticket,
            )
        ),
    )

    response = await handle_pre_send_freshness(
        _Request(
            app=app,
            headers={"Authorization": "Bearer relay-secret"},
            payload={
                "ticket_id": 1,
                "snapshot_customer_message_count": 1,
                "snapshot_customer_last_message_id": "m1",
            },
        )
    )
    payload = json.loads(response.text)

    assert payload["status"] == "stale_context"
    assert payload["continue_send"] is False
    assert payload["reason"] == reason


@pytest.mark.asyncio
async def test_pre_send_freshness_allows_form_only_handoff_callback(
    monkeypatch,
) -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    readiness = AsyncMock(
        return_value=FreshnessResult(
            continue_send=True,
            reason="no_snapshot_required",
        )
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_public_reply_readiness",
        readiness,
    )

    response = await handle_pre_send_freshness(
        _Request(
            app=app,
            headers={"Authorization": "Bearer relay-secret"},
            payload={"ticket_id": 1},
        )
    )

    assert json.loads(response.text)["continue_send"] is True
    readiness.assert_awaited_once_with(
        ticket_id=1,
        snapshot_count=None,
        snapshot_last_id=None,
        skip_llm=True,
        cfg=cfg,
    )


@pytest.mark.asyncio
async def test_pre_send_freshness_allows_late_non_substantive_message(
    monkeypatch,
) -> None:
    """A harmless acknowledgement does not invalidate the prepared answer."""
    app = web.Application()
    cfg = MagicMock()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    readiness = AsyncMock(
        return_value=FreshnessResult(
            continue_send=True,
            reason="all_new_msgs_non_substantive",
            new_messages_count=1,
            recovery=FreshnessRecovery(
                action=FreshnessRecoveryAction.REFRESH_CONTEXT,
                next_snapshot_count=2,
                next_snapshot_last_id="m2",
                reason="all_new_msgs_non_substantive",
            ),
        )
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_public_reply_readiness",
        readiness,
    )

    response = await handle_pre_send_freshness(
        _Request(
            app=app,
            headers={"Authorization": "Bearer relay-secret"},
            payload={
                "ticket_id": 1,
                "snapshot_customer_message_count": 1,
                "snapshot_customer_last_message_id": "m1",
            },
        )
    )
    payload = json.loads(response.text)

    assert payload["continue_send"] is True
    assert payload["status"] == "fresh"
    assert payload["accepted_snapshot_count"] == 2
    assert payload["accepted_snapshot_last_id"] == "m2"


@pytest.mark.asyncio
async def test_agent_config_rejects_bad_api_key(monkeypatch) -> None:
    class _Env:
        hc_client_api_key = "hc-secret"

    monkeypatch.setattr("zd_agent.services.saas_routes.EnvSettings", lambda: _Env())

    app = web.Application()
    cfg = MagicMock()
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer wrong"},
        payload={},
    )

    response = await handle_agent_config(request)

    assert response.status == 401


@pytest.mark.asyncio
async def test_agent_config_update_rejects_invalid_reasoning_combination(
    monkeypatch,
) -> None:
    class _Env:
        hc_client_api_key = "hc-secret"
        openai_api_key = ""
        anthropic_api_key = ""

    monkeypatch.setattr("zd_agent.services.saas_routes.EnvSettings", lambda: _Env())

    app = web.Application()
    from zd_agent.config import AppConfig

    cfg = AppConfig()
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer hc-secret"},
        payload={"llm": {"model": "gpt-4.1", "reasoning_effort": "low"}},
    )

    response = await handle_update_agent_config(request)
    payload = json.loads(response.text)

    assert response.status == 400
    assert "does not support reasoning effort" in payload["error"]
    assert cfg.llm.model == "gpt-4o"


@pytest.mark.asyncio
async def test_agent_config_update_rejects_string_false_for_enabled(
    monkeypatch,
) -> None:
    class _Env:
        hc_client_api_key = "hc-secret"
        openai_api_key = ""
        anthropic_api_key = ""

    monkeypatch.setattr("zd_agent.services.saas_routes.EnvSettings", lambda: _Env())
    app = web.Application()
    cfg = AppConfig()
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )

    response = await handle_update_agent_config(
        _Request(
            app=app,
            headers={"Authorization": "Bearer hc-secret"},
            payload={"public_reply": {"enabled": "false"}},
        )
    )

    assert response.status == 400
    assert json.loads(response.text) == {
        "ok": False,
        "error": "public_reply.enabled must be a boolean",
    }
    assert cfg.public_reply.enabled is False


@pytest.mark.asyncio
async def test_agent_models_rejects_request_level_base_url_override(
    monkeypatch,
) -> None:
    class _Env:
        hc_client_api_key = "hc-secret"
        openai_api_key = "env-model-key"
        openai_base_url = None

    monkeypatch.setattr("zd_agent.services.saas_routes.EnvSettings", lambda: _Env())
    list_models = AsyncMock(return_value={"count": 1, "models": [{"id": "gpt-5.5"}]})
    monkeypatch.setattr("zd_agent.services.saas_routes.list_agent_models", list_models)

    app = web.Application()
    cfg = MagicMock()
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    request = _Request(
        app=app,
        headers={
            "Authorization": "Bearer hc-secret",
            "X-Agent-Model-Api-Key": "user-model-key",
        },
        payload={},
        query={"target": "subagent", "base_url": "https://llm.example.test/v1"},
    )

    response = await handle_agent_models(request)

    assert response.status == 400
    list_models.assert_not_awaited()
