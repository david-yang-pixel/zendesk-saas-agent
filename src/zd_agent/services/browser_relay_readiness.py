"""Fail-closed Zendesk Browser Relay readiness monitoring."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from aiohttp import web

from zd_agent.log import get_logger

logger = get_logger(module="services.browser_relay_readiness")

_DEPENDENCY = "zendesk_browser_relay"
BROWSER_RELAY_READINESS_KEY = web.AppKey(
    "zd_agent.browser_relay_readiness",
    object,
)


@dataclass(frozen=True, slots=True)
class RelayReadiness:
    ok: bool
    status: str


ProbeFn = Callable[[], Awaitable[RelayReadiness]]


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class BrowserRelayReadinessMonitor:
    """Poll Relay readiness and operate the Agent dependency interlock."""

    def __init__(
        self,
        *,
        lifecycle: Any,
        readiness_url: str,
        api_key: str,
        interval_seconds: float = 30.0,
        timeout_seconds: float = 15.0,
        probe_fn: ProbeFn | None = None,
        enabled: bool = True,
    ) -> None:
        self._lifecycle = lifecycle
        self._readiness_url = str(readiness_url).strip()
        self._api_key = str(api_key)
        self._interval_seconds = max(5.0, float(interval_seconds))
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._probe_fn = probe_fn
        self._enabled = bool(enabled)
        self._last = RelayReadiness(
            ok=not self._enabled,
            status="disabled" if not self._enabled else "not_checked",
        )
        self._last_checked_at = ""
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def check_once(self) -> RelayReadiness:
        if not self._enabled:
            return self._last
        try:
            result = (
                await self._probe_fn()
                if self._probe_fn is not None
                else await self._probe()
            )
        except Exception as exc:  # noqa: BLE001 - readiness always fails closed.
            logger.warning(
                "browser_relay_readiness_probe_failed",
                error_type=type(exc).__name__,
            )
            result = RelayReadiness(ok=False, status="unreachable")

        previous = self._last
        self._last = result
        self._last_checked_at = _utc_now()
        if result.ok and result.status == "busy":
            return result
        interlock = self._lifecycle.status().get("dependency_interlock") or {}
        interlock_active = bool(interlock.get("active"))
        interlock_dependency = str(interlock.get("dependency") or "")

        if result.ok and result.status == "logged_in":
            if interlock_active and interlock_dependency in {"", _DEPENDENCY}:
                await self._lifecycle.clear_dependency_interlock(
                    dependency=_DEPENDENCY
                )
        elif (
            not interlock_active
            or interlock_dependency != _DEPENDENCY
            or previous.status != result.status
        ):
            await self._lifecycle.engage_dependency_interlock(
                dependency=_DEPENDENCY,
                reason=result.status,
            )
        return result

    async def _probe(self) -> RelayReadiness:
        if not self._readiness_url or not self._api_key:
            return RelayReadiness(ok=False, status="unconfigured")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = await client.get(self._readiness_url, headers=headers)
        try:
            payload = response.json()
        except ValueError:
            return RelayReadiness(ok=False, status="invalid_response")
        status = (
            str(payload.get("status") or "unknown")
            if isinstance(payload, dict)
            else "invalid_response"
        )
        ok = bool(
            response.status_code == 200
            and isinstance(payload, dict)
            and payload.get("ok") is True
            and status in {"logged_in", "busy"}
        )
        if response.status_code in {401, 403}:
            status = "unauthorized"
        elif response.status_code >= 500 and status == "unknown":
            status = "unavailable"
        return RelayReadiness(ok=ok, status=status)

    def start(self) -> None:
        if not self._enabled or (self._task is not None and not self._task.done()):
            return
        self._closed = False
        self._task = asyncio.create_task(
            self._run(),
            name="browser-relay-readiness",
        )

    async def _run(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(self._interval_seconds)
                if not self._closed:
                    await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the safety monitor alive.
                logger.warning(
                    "browser_relay_readiness_loop_failed",
                    error_type=type(exc).__name__,
                )

    async def close(self) -> None:
        self._closed = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "ok": self._last.ok,
            "status": self._last.status,
            "last_checked_at": self._last_checked_at,
            "interval_seconds": self._interval_seconds,
        }
