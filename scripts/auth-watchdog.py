#!/usr/bin/env python3
"""Teleport Auth Watchdog.

Periodically checks `tsh status`. When certificate is close to expiring,
requests a one-time auth URL from the main app (via internal API) and pushes
it to Feishu so the user can authenticate via phone web form.

Flow: Watchdog → POST /internal/teleport-auth/issue → URL → Feishu card.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

import httpx

# Ensure src is importable when running the script directly
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from zd_agent.config import load_config
from zd_agent.integrations.teleport import get_tsh_status
from zd_agent.log import get_logger

logger = get_logger(module="scripts.auth-watchdog")


async def _issue_auth_url(internal_secret: str) -> str | None:
    """Call main app to mint a one-time token; return the public URL."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://127.0.0.1:8080/internal/teleport-auth/issue",
                headers={"X-Internal-Secret": internal_secret},
            )
        if resp.status_code != 200:
            logger.error(
                "auth_url_issue_failed",
                status=resp.status_code, body=resp.text[:200],
            )
            return None
        return resp.json().get("url")
    except Exception as e:
        logger.error("auth_url_issue_exception", error=str(e))
        return None


async def check_and_notify(cfg, feishu, internal_secret: str) -> None:
    """One iteration: check status, push auth URL via Feishu if expiring."""
    warn_threshold = timedelta(minutes=cfg.teleport.warn_threshold_minutes)

    try:
        status = await get_tsh_status(
            proxy=cfg.teleport.proxy,
            user=cfg.teleport.user,
            auth_home=cfg.teleport.auth_home,
        )
    except Exception as e:
        logger.error("tsh_status_failed", error=str(e))
        return

    if status is None:
        logger.warning("tsh_not_logged_in_pushing_auth_url")
        await _push_auth_url(feishu, cfg, internal_secret,
                             reason="not logged in", remaining="?")
        return

    if status.remaining < warn_threshold:
        logger.warning(
            "tsh_expiring_soon",
            remaining=str(status.remaining),
            threshold=str(warn_threshold),
        )
        await _push_auth_url(
            feishu, cfg, internal_secret,
            reason="expiring soon", remaining=str(status.remaining),
        )
        return

    logger.info("tsh_ok", remaining=str(status.remaining))


async def _push_auth_url(feishu, cfg, internal_secret: str, *, reason: str, remaining: str) -> None:
    url = await _issue_auth_url(internal_secret)
    if url is None:
        logger.error("auth_url_could_not_be_issued")
        await feishu.send_alert(
            title="⚠️ Teleport Watchdog 异常",
            message=f"证书剩余 {remaining}，但无法从主 app 获取认证 URL。"
                    f"请检查 zd-agent 容器是否在线。",
            urgent=True,
        )
        return

    # Use the existing send_auth_renew card which already has a big button
    try:
        await feishu.send_auth_renew(url=url, user=cfg.teleport.user)
        logger.info("auth_url_pushed", reason=reason, remaining=remaining)
    except Exception as e:
        logger.error("feishu_push_failed", error=str(e))


async def main() -> None:
    cfg = load_config()
    internal_secret = os.environ.get("INTERNAL_SHARED_SECRET", "")
    if not internal_secret:
        logger.error("INTERNAL_SHARED_SECRET env not set; watchdog cannot mint tokens")
        # Still run — will push error alerts to Feishu

    from zd_agent.integrations.feishu.client import FeishuClient
    feishu = FeishuClient(
        webhook_url=cfg.feishu.webhook_url,
        webhook_secret=cfg.feishu.webhook_secret,
    )

    logger.info(
        "auth_watchdog_started",
        proxy=cfg.teleport.proxy,
        user=cfg.teleport.user,
        threshold_min=cfg.teleport.warn_threshold_minutes,
        interval_s=cfg.teleport.check_interval_seconds,
    )

    while True:
        try:
            await check_and_notify(cfg, feishu, internal_secret)
        except Exception as e:
            logger.error("watchdog_iteration_error", error=str(e))
        await asyncio.sleep(cfg.teleport.check_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
