from __future__ import annotations

import asyncio
import socket
import time
from urllib.parse import urljoin
from unittest.mock import MagicMock

import httpx
import pytest

from zd_agent.services.ticket_messages import _download_image_data_url
from zd_agent.services.ticket_images import (
    ImageUrlRejected,
    PublicAddressNetworkBackend,
    validate_image_download_url,
)

_PNG = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 16)


class _Response:
    def __init__(
        self,
        status_code: int,
        *,
        content_type: str = "image/png",
        content: bytes = _PNG,
        location: str = "",
        content_length: int | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self._chunks = chunks or [content]
        self.content = b"".join(self._chunks)
        self.iter_count = 0
        self.closed = False
        self.headers = {"Content-Type": content_type}
        if location:
            self.headers["Location"] = location
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    async def aiter_bytes(self):
        for chunk in self._chunks:
            self.iter_count += 1
            yield chunk


class _StubStream:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self) -> _Response:
        return self.response

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.response.closed = True


class _StubAsyncClient:
    responses: list[_Response] = []
    calls: list[tuple[str, dict]] = []
    init_kwargs: dict = {}
    stream_calls: list[tuple[str, str, dict]] = []

    def __init__(self, **kwargs) -> None:
        type(self).init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def get(self, url: str, **kwargs):
        type(self).calls.append((url, kwargs))
        response = type(self).responses.pop(0)
        follow_redirects = kwargs.get(
            "follow_redirects",
            type(self).init_kwargs.get("follow_redirects", False),
        )
        if response.status_code in {301, 302, 303, 307, 308} and follow_redirects:
            target = urljoin(url, response.headers["Location"])
            return await self.get(target, **kwargs)
        return response

    def stream(self, method: str, url: str, **kwargs) -> _StubStream:
        type(self).stream_calls.append((method, url, kwargs))
        type(self).calls.append((url, kwargs))
        return _StubStream(type(self).responses.pop(0))


@pytest.fixture(autouse=True)
def _stub_http_and_public_dns(monkeypatch) -> None:
    _StubAsyncClient.responses = []
    _StubAsyncClient.calls = []
    _StubAsyncClient.init_kwargs = {}
    _StubAsyncClient.stream_calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _StubAsyncClient)

    def public_dns(host: str, port: int, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", public_dns)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/image.png",
        "http://127.0.0.1/image.png",
        "http://10.0.0.8/image.png",
        "http://169.254.169.254/latest/meta-data",
        "http://224.0.0.1/image.png",
        "http://240.0.0.1/image.png",
    ],
)
async def test_image_download_rejects_unsafe_url_before_request(url: str) -> None:
    _StubAsyncClient.responses = [_Response(200)]

    result = await _download_image_data_url(url)

    assert result is None
    assert _StubAsyncClient.calls == []


@pytest.mark.asyncio
async def test_image_download_rejects_private_dns_answer(monkeypatch) -> None:
    def private_dns(host: str, port: int, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("192.168.1.20", port),
            ),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", private_dns)
    _StubAsyncClient.responses = [_Response(200)]

    result = await _download_image_data_url("https://images.example.com/a.png")

    assert result is None
    assert _StubAsyncClient.calls == []


@pytest.mark.asyncio
async def test_image_download_revalidates_redirect_dns(monkeypatch) -> None:
    def dns_by_host(host: str, port: int, **_kwargs):
        address = "10.0.0.9" if host == "internal.example.com" else "93.184.216.34"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port),
            ),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", dns_by_host)
    _StubAsyncClient.responses = [
        _Response(302, location="https://internal.example.com/secret"),
        _Response(200),
    ]

    result = await _download_image_data_url("https://images.example.com/a.png")

    assert result is None
    assert [url for url, _kwargs in _StubAsyncClient.calls] == [
        "https://images.example.com/a.png",
    ]


@pytest.mark.asyncio
async def test_image_download_disables_environment_and_automatic_redirects() -> None:
    _StubAsyncClient.responses = [_Response(200)]

    result = await _download_image_data_url("https://images.example.com/a.png")

    assert result is not None
    assert _StubAsyncClient.init_kwargs["trust_env"] is False
    assert _StubAsyncClient.init_kwargs["follow_redirects"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "content"),
    [
        ("text/html", _PNG),
        ("image/png", b"<html>not an image</html>"),
    ],
)
async def test_image_download_requires_image_content_type_and_magic_bytes(
    content_type: str,
    content: bytes,
) -> None:
    _StubAsyncClient.responses = [
        _Response(200, content_type=content_type, content=content),
    ]

    result = await _download_image_data_url("https://images.example.com/a.png")

    assert result is None


@pytest.mark.asyncio
async def test_image_download_stops_stream_after_size_limit() -> None:
    response = _Response(
        200,
        chunks=[b"abc", b"def", b"ghi"],
    )
    _StubAsyncClient.responses = [response]

    result = await _download_image_data_url(
        "https://images.example.com/a.png",
        max_bytes=4,
    )

    assert result is None
    assert len(_StubAsyncClient.stream_calls) == 1
    assert response.iter_count == 2
    assert response.closed is True


@pytest.mark.asyncio
async def test_image_download_rejects_oversized_content_length_before_read() -> None:
    response = _Response(
        200,
        content_length=100,
        chunks=[_PNG],
    )
    _StubAsyncClient.responses = [response]

    result = await _download_image_data_url(
        "https://images.example.com/a.png",
        max_bytes=16,
    )

    assert result is None
    assert len(_StubAsyncClient.stream_calls) == 1
    assert response.iter_count == 0
    assert response.closed is True


@pytest.mark.asyncio
async def test_image_download_enforces_total_wall_clock_deadline(monkeypatch) -> None:
    from zd_agent.services import ticket_image_download

    warning = MagicMock()
    monkeypatch.setattr(ticket_image_download.logger, "warning", warning)
    async def validate_immediately(_url: str) -> None:
        return None

    class SlowResponse(_Response):
        async def aiter_bytes(self):
            await asyncio.sleep(1)
            yield _PNG

    monkeypatch.setattr(
        "zd_agent.services.ticket_image_download.validate_image_download_url",
        validate_immediately,
    )
    response = SlowResponse(200)
    _StubAsyncClient.responses = [response]

    started = asyncio.get_running_loop().time()
    result = await _download_image_data_url(
        "https://images.example.com/a.png",
        timeout=0.01,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert result is None
    assert elapsed < 0.2
    assert response.closed is True
    timeout_log = next(
        call for call in warning.call_args_list
        if call.args and call.args[0] == "vision_image_download_failed"
    )
    assert timeout_log.kwargs["stage"] == "response_body"
    assert timeout_log.kwargs["timeout_s"] == 0.01


@pytest.mark.asyncio
async def test_image_download_waits_for_async_stream_cleanup_after_timeout(
    monkeypatch,
) -> None:
    async def validate_immediately(_url: str) -> None:
        return None

    class SlowResponse(_Response):
        async def aiter_bytes(self):
            await asyncio.sleep(1)
            yield _PNG

    class AsyncClosingStream(_StubStream):
        async def __aexit__(self, exc_type, exc, traceback) -> None:
            await asyncio.sleep(0)
            self.response.closed = True

    def stream_with_async_close(self, method: str, url: str, **kwargs):
        type(self).stream_calls.append((method, url, kwargs))
        type(self).calls.append((url, kwargs))
        return AsyncClosingStream(type(self).responses.pop(0))

    monkeypatch.setattr(
        "zd_agent.services.ticket_image_download.validate_image_download_url",
        validate_immediately,
    )
    monkeypatch.setattr(_StubAsyncClient, "stream", stream_with_async_close)
    response = SlowResponse(200)
    _StubAsyncClient.responses = [response]

    result = await _download_image_data_url(
        "https://images.example.com/a.png",
        timeout=0.01,
    )

    assert result is None
    assert response.closed is True


@pytest.mark.asyncio
async def test_image_download_deadline_does_not_wait_for_blocked_dns(
    monkeypatch,
) -> None:
    def blocked_dns(host: str, port: int, **_kwargs):
        time.sleep(0.25)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", blocked_dns)
    started = asyncio.get_running_loop().time()

    result = await _download_image_data_url(
        "https://images.example.com/a.png",
        timeout=0.02,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert result is None
    assert elapsed < 0.15
    assert _StubAsyncClient.calls == []


@pytest.mark.asyncio
async def test_image_download_sends_auth_only_to_strict_zendesk_subdomain(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZENDESK_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "secret-token")
    _StubAsyncClient.responses = [_Response(401), _Response(200)]

    result = await _download_image_data_url(
        "https://acme.zendesk.com.evil.example/image.png",
    )

    assert result is None
    assert len(_StubAsyncClient.calls) == 1
    assert "auth" not in _StubAsyncClient.calls[0][1]


@pytest.mark.asyncio
async def test_image_download_preserves_zendesk_auth_retry(monkeypatch) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "secret-token")
    _StubAsyncClient.responses = [_Response(401), _Response(200)]

    result = await _download_image_data_url(
        "https://acme.zendesk.com/attachments/image.png",
    )

    assert result is not None
    assert "auth" not in _StubAsyncClient.calls[0][1]
    assert _StubAsyncClient.calls[1][1]["auth"] == (
        "agent@example.com/token",
        "secret-token",
    )


@pytest.mark.asyncio
async def test_image_download_never_sends_zendesk_auth_over_http(monkeypatch) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "secret-token")
    _StubAsyncClient.responses = [_Response(401), _Response(200)]

    result = await _download_image_data_url(
        "http://acme.zendesk.com/attachments/image.png",
    )

    assert result is None
    assert len(_StubAsyncClient.calls) == 1
    assert "auth" not in _StubAsyncClient.calls[0][1]


@pytest.mark.asyncio
async def test_image_download_drops_auth_on_cross_origin_redirect(monkeypatch) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "secret-token")
    _StubAsyncClient.responses = [
        _Response(401),
        _Response(302, location="https://cdn.example.com/image.png"),
        _Response(200),
    ]

    result = await _download_image_data_url(
        "https://acme.zendesk.com/attachments/image.png",
    )

    assert result is not None
    assert "auth" not in _StubAsyncClient.calls[0][1]
    assert "auth" in _StubAsyncClient.calls[1][1]
    assert "auth" not in _StubAsyncClient.calls[2][1]


@pytest.mark.asyncio
async def test_image_download_preserves_sc_attachment_fallback(monkeypatch) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "secret-token")
    _StubAsyncClient.responses = [
        _Response(401),
        _Response(403),
        _Response(401),
        _Response(302, location="https://cdn.example.com/image.png"),
        _Response(200),
    ]

    result = await _download_image_data_url(
        "https://acme.zendesk.com/sc/attachments/v2/123/image.png",
    )

    assert result is not None
    assert ["auth" in kwargs for _url, kwargs in _StubAsyncClient.calls] == [
        False,
        True,
        False,
        True,
        False,
    ]
    assert _StubAsyncClient.calls[2][0] == (
        "https://acme.zendesk.com/api/v2/ticket_attachments/123/content"
    )


@pytest.mark.asyncio
async def test_network_backend_connects_to_revalidated_public_address(
    monkeypatch,
) -> None:
    class Delegate:
        calls: list[tuple[str, int]] = []

        async def connect_tcp(self, host, port, **_kwargs):
            self.calls.append((host, port))
            return object()

        async def sleep(self, _seconds):
            return None

    delegate = Delegate()
    backend = PublicAddressNetworkBackend(delegate=delegate)

    stream = await backend.connect_tcp("images.example.com", 443)

    assert stream is not None
    assert delegate.calls == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_network_backend_blocks_dns_rebinding_before_connect(
    monkeypatch,
) -> None:
    answers = iter(["93.184.216.34", "10.0.0.8"])

    def changing_dns(host: str, port: int, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (next(answers), port),
            ),
        ]

    class Delegate:
        calls: list[tuple[str, int]] = []

        async def connect_tcp(self, host, port, **_kwargs):
            self.calls.append((host, port))
            return object()

        async def sleep(self, _seconds):
            return None

    monkeypatch.setattr(socket, "getaddrinfo", changing_dns)
    delegate = Delegate()
    backend = PublicAddressNetworkBackend(delegate=delegate)
    await validate_image_download_url("https://images.example.com/a.png")

    with pytest.raises(ImageUrlRejected, match="non-public"):
        await backend.connect_tcp("images.example.com", 443)

    assert delegate.calls == []


@pytest.mark.asyncio
async def test_image_download_never_sends_auth_to_other_zendesk_tenant(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "secret-token")
    _StubAsyncClient.responses = [_Response(401), _Response(200)]

    result = await _download_image_data_url(
        "https://other.zendesk.com/attachments/image.png",
    )

    assert result is None
    assert len(_StubAsyncClient.calls) == 1
    assert "auth" not in _StubAsyncClient.calls[0][1]


@pytest.mark.asyncio
async def test_image_failure_log_does_not_include_url_secret(monkeypatch) -> None:
    from zd_agent.services import ticket_image_download

    warning = MagicMock()
    monkeypatch.setattr(ticket_image_download.logger, "warning", warning)
    _StubAsyncClient.responses = [
        _Response(200, content_type="text/html", content=b"not an image"),
    ]

    await _download_image_data_url(
        "https://images.example.com/private/path.png?token=super-secret",
    )

    rendered = repr(warning.call_args_list)
    assert "super-secret" not in rendered
    assert "/private/path.png" not in rendered
    assert "images.example.com" in rendered
