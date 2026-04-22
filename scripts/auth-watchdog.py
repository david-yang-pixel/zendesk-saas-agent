#!/usr/bin/env python3
"""Teleport Auth Watchdog.

Periodically checks `tsh status`. When the certificate is close to expiring
(< warn_threshold_minutes remaining), triggers `tsh login --headless` and
pushes the approval URL to Feishu so the user can click it from a phone/laptop
to complete SSO + MFA.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

# Ensure src is importable when running the script directly
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from zd_agent.config import load_config
from zd_agent.integrations.teleport import get_tsh_status, trigger_headless_login
from zd_agent.log import get_logger

logger = get_logger(module="scripts.auth-watchdog")


async def check_and_renew(cfg, feishu) -> None:
    """One iteration: check status, trigger renew if needed."""
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
        logger.warning("tsh_not_logged_in_triggering_headless")
        await _renew(cfg, feishu, reason="not logged in")
        return

    if status.remaining < warn_threshold:
        logger.warning(
            "tsh_expiring_soon",
            remaining=str(status.remaining),
            threshold=str(warn_threshold),
        )
        await _renew(cfg, feishu, reason=f"remaining {status.remaining}")
        return

    logger.info("tsh_ok", remaining=str(status.remaining))


async def _renew(cfg, feishu, *, reason: str) -> None:
    """Trigger headless login and push URL to Feishu."""
    try:
        url = await trigger_headless_login(
            proxy=cfg.teleport.proxy,
            user=cfg.teleport.user,
            auth_home=cfg.teleport.auth_home,
        )
    except Exception as e:
        logger.error("tsh_headless_launch_failed", error=str(e))
        return
    if not url:
        logger.error("tsh_headless_no_url_captured", reason=reason)
        return

    try:
        await feishu.send_auth_renew(
            chat_id=cfg.feishu.default_chat_id,
            url=url,
            user=cfg.teleport.user,
        )
        logger.info("auth_renew_pushed", url=url, reason=reason)
    except Exception as e:
        logger.error("feishu_push_failed", error=str(e), url=url)


async def main() -> None:
    cfg = load_config()
    from zd_agent.integrations.feishu.client import FeishuClient
    feishu = FeishuClient(
        app_id=cfg.feishu.app_id,
        app_secret=cfg.feishu.app_secret,
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
            await check_and_renew(cfg, feishu)
        except Exception as e:
            logger.error("watchdog_iteration_error", error=str(e))
        await asyncio.sleep(cfg.teleport.check_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
