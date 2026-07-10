from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from collections.abc import Iterable
from typing import Any, Protocol
from urllib.parse import SplitResult, urlsplit

import httpcore
import httpx
from anyio import CapacityLimiter
from anyio.to_thread import run_sync
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.base import SOCKET_OPTION

_STRICT_ZENDESK_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_KNOWN_METADATA_HOSTS = frozenset({
    "instance-data.ec2.internal",
    "metadata.google.internal",
    "metadata.google",
})
_DNS_RESOLVER_LIMITER = CapacityLimiter(8)


class ImageUrlRejected(ValueError):
    pass


class _AsyncNetworkBackend(Protocol):
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> Any: ...

    async def sleep(self, seconds: float) -> None: ...


def _parsed_image_url(url: str) -> SplitResult:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ImageUrlRejected("invalid URL") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ImageUrlRejected("unsupported URL scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ImageUrlRejected("URL credentials are not allowed")
    if not parsed.hostname:
        raise ImageUrlRejected("URL host is required")
    try:
        parsed.port
    except ValueError as exc:
        raise ImageUrlRejected("invalid URL port") from exc
    return parsed


def _normalized_hostname(parsed: SplitResult) -> str:
    hostname = (parsed.hostname or "").rstrip(".").lower()
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ImageUrlRejected("invalid URL hostname") from exc


def _address_is_blocked(raw_address: str) -> bool:
    address = ipaddress.ip_address(raw_address.split("%", 1)[0])
    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_and_validate_host(hostname: str, port: int) -> tuple[str, ...]:
    if hostname in _KNOWN_METADATA_HOSTS:
        raise ImageUrlRejected("metadata host is not allowed")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            answers = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ImageUrlRejected("URL host could not be resolved") from exc
        addresses = tuple({str(answer[4][0]) for answer in answers})
    else:
        addresses = (hostname,)
    if not addresses:
        raise ImageUrlRejected("URL host has no addresses")
    try:
        blocked = any(_address_is_blocked(address) for address in addresses)
    except ValueError as exc:
        raise ImageUrlRejected("URL host returned an invalid address") from exc
    if blocked:
        raise ImageUrlRejected("URL host resolves to a non-public address")
    return addresses


class PublicAddressNetworkBackend:
    def __init__(self, *, delegate: _AsyncNetworkBackend | None = None) -> None:
        self._delegate = delegate or AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> Any:
        addresses = await run_sync(
            _resolve_and_validate_host,
            host,
            port,
            abandon_on_cancel=True,
            limiter=_DNS_RESOLVER_LIMITER,
        )
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._delegate.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (OSError, httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ImageUrlRejected("URL host has no connectable public address")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


def image_http_transport() -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    pool = getattr(transport, "_pool")
    setattr(pool, "_network_backend", PublicAddressNetworkBackend())
    return transport


async def validate_image_download_url(url: str) -> str:
    parsed = _parsed_image_url(url)
    hostname = _normalized_hostname(parsed)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    await run_sync(
        _resolve_and_validate_host,
        hostname,
        port,
        abandon_on_cancel=True,
        limiter=_DNS_RESOLVER_LIMITER,
    )
    return url


def is_strict_zendesk_url(url: str) -> bool:
    try:
        parsed = _parsed_image_url(url)
        hostname = _normalized_hostname(parsed)
    except ImageUrlRejected:
        return False
    if parsed.scheme.lower() != "https":
        return False
    suffix = ".zendesk.com"
    if not hostname.endswith(suffix):
        return False
    label = hostname[: -len(suffix)]
    return bool(_STRICT_ZENDESK_LABEL_RE.fullmatch(label))


def is_zendesk_tenant_url(url: str, subdomain: str) -> bool:
    candidate = subdomain.strip().lower()
    if not _STRICT_ZENDESK_LABEL_RE.fullmatch(candidate):
        return False
    try:
        parsed = _parsed_image_url(url)
        hostname = _normalized_hostname(parsed)
    except ImageUrlRejected:
        return False
    return (
        parsed.scheme.lower() == "https"
        and hostname == f"{candidate}.zendesk.com"
    )


def image_url_log_ref(url: str) -> str:
    try:
        hostname = _normalized_hostname(_parsed_image_url(url))
    except ImageUrlRejected:
        hostname = "invalid-host"
    digest = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{hostname}#{digest}"
