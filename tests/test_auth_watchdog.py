"""Test auth-watchdog check_and_renew iteration logic.

We test the single-iteration function, not the infinite loop.
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Load the script as a module (it's not a package, so use spec_from_file_location)
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "auth-watchdog.py"


def _load_watchdog_module():
    spec = importlib.util.spec_from_file_location("auth_watchdog", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wd_module():
    return _load_watchdog_module()


@pytest.fixture
def fake_cfg():
    cfg = MagicMock()
    cfg.teleport.proxy = "teleport.example.com"
    cfg.teleport.user = "me"
    cfg.teleport.auth_home = "/tmp/.tsh"
    cfg.teleport.warn_threshold_minutes = 30
    cfg.teleport.check_interval_seconds = 300
    return cfg


@pytest.fixture
def fake_feishu():
    c = MagicMock()
    c.send_auth_renew = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_check_skips_when_cert_still_valid(wd_module, fake_cfg, fake_feishu):
    status = MagicMock()
    status.remaining = timedelta(hours=8)  # plenty of time
    with patch.object(wd_module, "get_tsh_status", AsyncMock(return_value=status)):
        with patch.object(wd_module, "trigger_headless_login", AsyncMock(return_value="should-not-be-called")):
            await wd_module.check_and_renew(fake_cfg, fake_feishu)
    fake_feishu.send_auth_renew.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_renews_when_cert_expiring_soon(wd_module, fake_cfg, fake_feishu):
    status = MagicMock()
    status.remaining = timedelta(minutes=15)  # below 30-min threshold
    fake_url = "https://teleport.example.com/headless/abc123"
    with patch.object(wd_module, "get_tsh_status", AsyncMock(return_value=status)):
        with patch.object(wd_module, "trigger_headless_login", AsyncMock(return_value=fake_url)):
            await wd_module.check_and_renew(fake_cfg, fake_feishu)
    fake_feishu.send_auth_renew.assert_awaited_once()
    kwargs = fake_feishu.send_auth_renew.await_args.kwargs
    assert kwargs["url"] == fake_url
    assert kwargs["user"] == "me"


@pytest.mark.asyncio
async def test_check_renews_when_not_logged_in(wd_module, fake_cfg, fake_feishu):
    with patch.object(wd_module, "get_tsh_status", AsyncMock(return_value=None)):
        with patch.object(wd_module, "trigger_headless_login", AsyncMock(return_value="https://x/headless/y")):
            await wd_module.check_and_renew(fake_cfg, fake_feishu)
    fake_feishu.send_auth_renew.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_handles_no_url_from_headless(wd_module, fake_cfg, fake_feishu):
    status = MagicMock()
    status.remaining = timedelta(minutes=10)
    with patch.object(wd_module, "get_tsh_status", AsyncMock(return_value=status)):
        with patch.object(wd_module, "trigger_headless_login", AsyncMock(return_value="")):
            await wd_module.check_and_renew(fake_cfg, fake_feishu)
    # No URL captured → no feishu push
    fake_feishu.send_auth_renew.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_handles_tsh_status_exception(wd_module, fake_cfg, fake_feishu):
    with patch.object(wd_module, "get_tsh_status", AsyncMock(side_effect=RuntimeError("x"))):
        # Should not raise
        await wd_module.check_and_renew(fake_cfg, fake_feishu)
    fake_feishu.send_auth_renew.assert_not_awaited()
