"""Test Feishu webhook handler (URL verify + event parsing + command dispatch)."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zd_agent.entry.feishu_webhook import (
    extract_message_text,
    handle_feishu_webhook,
)
from zd_agent.integrations.feishu.commands import Command


@pytest.fixture
def app_cfg():
    cfg = MagicMock()
    cfg.feishu.verify_token = "tok"
    cfg.feishu.encrypt_key = ""   # plaintext for most tests
    return cfg


async def _make_client(cfg):
    app = web.Application()
    app["config"] = cfg
    app["feishu_command_queue"] = asyncio.Queue()
    app.router.add_post("/feishu", handle_feishu_webhook)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client, app


@pytest.mark.asyncio
async def test_url_verification_ok(app_cfg):
    client, app = await _make_client(app_cfg)
    try:
        r = await client.post("/feishu", json={
            "type": "url_verification",
            "token": "tok",
            "challenge": "c123",
        })
        assert r.status == 200
        data = await r.json()
        assert data == {"challenge": "c123"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_url_verification_bad_token(app_cfg):
    client, app = await _make_client(app_cfg)
    try:
        r = await client.post("/feishu", json={
            "type": "url_verification",
            "token": "wrong",
            "challenge": "c",
        })
        assert r.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_text_message_with_command_enqueues(app_cfg):
    client, app = await _make_client(app_cfg)
    try:
        r = await client.post("/feishu", json={
            "schema": "2.0",
            "header": {"token": "tok", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_type": "text",
                    "content": json.dumps({"text": "/reprocess 12345"}),
                },
            },
        })
        assert r.status == 200
        q = app["feishu_command_queue"]
        cmd = await asyncio.wait_for(q.get(), timeout=1.0)
        assert isinstance(cmd, Command)
        assert cmd.name == "reprocess"
        assert cmd.args == ["12345"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_event_token_mismatch_rejected(app_cfg):
    client, app = await _make_client(app_cfg)
    try:
        r = await client.post("/feishu", json={
            "schema": "2.0",
            "header": {"token": "wrong"},
            "event": {
                "message": {
                    "message_type": "text",
                    "content": json.dumps({"text": "/status"}),
                },
            },
        })
        assert r.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_non_text_message_ignored(app_cfg):
    client, app = await _make_client(app_cfg)
    try:
        r = await client.post("/feishu", json={
            "schema": "2.0",
            "header": {"token": "tok"},
            "event": {"message": {"message_type": "image"}},
        })
        assert r.status == 200
        assert app["feishu_command_queue"].empty()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_plain_chat_not_a_command(app_cfg):
    client, app = await _make_client(app_cfg)
    try:
        r = await client.post("/feishu", json={
            "schema": "2.0",
            "header": {"token": "tok"},
            "event": {
                "message": {
                    "message_type": "text",
                    "content": json.dumps({"text": "hello there"}),
                },
            },
        })
        assert r.status == 200
        assert app["feishu_command_queue"].empty()
    finally:
        await client.close()


def test_extract_message_text_from_v2_event():
    payload = {
        "schema": "2.0",
        "event": {
            "message": {
                "message_type": "text",
                "content": json.dumps({"text": "/status 123"}),
            },
        },
    }
    assert extract_message_text(payload) == "/status 123"


def test_extract_message_text_non_text_returns_empty():
    payload = {"event": {"message": {"message_type": "image"}}}
    assert extract_message_text(payload) == ""


def test_extract_message_text_bad_content_returns_empty():
    payload = {"event": {"message": {"message_type": "text", "content": "not json"}}}
    assert extract_message_text(payload) == ""


def test_extract_message_text_no_event():
    assert extract_message_text({}) == ""
