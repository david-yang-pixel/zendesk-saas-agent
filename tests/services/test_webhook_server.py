import pytest


def test_create_app_registers_classic_webhook_routes():
    from zd_agent.services.webhook_server import create_app

    app = create_app(manager=object(), tracker=object())
    paths = {route.resource.canonical for route in app.router.routes()}

    assert "/health" in paths
    assert "/status" in paths
    assert "/webhook/zendesk" in paths


@pytest.mark.asyncio
async def test_handle_health_returns_ok():
    from zd_agent.services.webhook_server import handle_health

    response = await handle_health(None)

    assert response.status == 200
    assert response.text == '{"status": "ok"}'
