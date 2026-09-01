from __future__ import annotations

from typing import Protocol, cast

import httpx
from fastapi.testclient import TestClient

from casework_harness.service import create_app


class _Client(Protocol):
    def get(self, url: str) -> httpx.Response: ...


class _Readiness:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    async def check(self) -> bool:
        return self.ready


def test_harness_readiness_is_separate_from_liveness() -> None:
    client = cast(_Client, TestClient(create_app(readiness=_Readiness(False))))

    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503
