from __future__ import annotations

import base64
import re
import sys
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from anyio import CancelScope, move_on_after

from zd_agent.config import EnvSettings, get_config
from zd_agent.log import get_logger
from zd_agent.services.ticket_images import (
    ImageUrlRejected,
    detected_image_content_type,
    image_http_transport,
    image_url_log_ref,
    is_zendesk_tenant_url,
    validate_image_download_url,
)

logger = get_logger(module="services.ticket_image_download")

_IMG_MAX_BYTES = 4 * 1024 * 1024
_SC_ATTACHMENT_RE = re.compile(r"/sc/attachments/v2/([^/]+)/")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_IMAGE_REDIRECTS = 5
_ZENDESK_SUBDOMAIN_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)


@dataclass(frozen=True, slots=True)
class StreamedImageResponse:
    status_code: int
    content_type: str
    location: str
    data: bytes | None


@dataclass(slots=True)
class ImageDownloadProgress:
    stage: str = "setup"


def _content_length(headers: httpx.Headers | dict[str, str]) -> int | None:
    raw_value = headers.get("Content-Length", "").strip()
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    return value if value >= 0 else None


def _snapshot_response(
    response: httpx.Response,
    data: bytes | None,
) -> StreamedImageResponse:
    return StreamedImageResponse(
        status_code=response.status_code,
        content_type=response.headers.get("Content-Type", ""),
        location=response.headers.get("Location", "").strip(),
        data=data,
    )


async def _stream_image_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    auth: tuple[str, str] | None,
    max_bytes: int,
    url_ref: str,
    progress: ImageDownloadProgress,
) -> StreamedImageResponse:
    progress.stage = "url_validation"
    await validate_image_download_url(url)
    progress.stage = "request_headers"
    stream_context = (
        client.stream("GET", url, follow_redirects=False)
        if auth is None
        else client.stream("GET", url, auth=auth, follow_redirects=False)
    )
    response = await stream_context.__aenter__()
    try:
        progress.stage = "response_headers"
        if response.status_code != 200:
            return _snapshot_response(response, b"")

        declared_size = _content_length(response.headers)
        if declared_size is not None and declared_size > max_bytes:
            logger.warning(
                "vision_image_too_large",
                url_ref=url_ref,
                size=declared_size,
                limit=max_bytes,
            )
            return _snapshot_response(response, None)

        progress.stage = "response_body"
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > max_bytes:
                logger.warning(
                    "vision_image_too_large",
                    url_ref=url_ref,
                    size=len(body) + len(chunk),
                    limit=max_bytes,
                )
                return _snapshot_response(response, None)
            body.extend(chunk)
        return _snapshot_response(response, bytes(body))
    finally:
        # The total wall-clock deadline may cancel this task while streaming.
        # Shield the context exit so httpx can release the response/connection
        # before the timeout result is returned to the caller.
        with CancelScope(shield=True):
            await stream_context.__aexit__(*sys.exc_info())


async def _request_image_with_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    zendesk_auth: tuple[str, str] | None,
    zendesk_subdomain: str,
    max_bytes: int,
    url_ref: str,
    progress: ImageDownloadProgress,
) -> StreamedImageResponse | None:
    current_url = url
    for redirect_count in range(_MAX_IMAGE_REDIRECTS + 1):
        response = await _stream_image_response(
            client,
            current_url,
            auth=None,
            max_bytes=max_bytes,
            url_ref=url_ref,
            progress=progress,
        )
        if (
            response.status_code in {401, 403}
            and zendesk_auth is not None
            and is_zendesk_tenant_url(current_url, zendesk_subdomain)
        ):
            response = await _stream_image_response(
                client,
                current_url,
                auth=zendesk_auth,
                max_bytes=max_bytes,
                url_ref=url_ref,
                progress=progress,
            )
        if response.status_code not in _REDIRECT_STATUSES:
            return response
        if not response.location or redirect_count >= _MAX_IMAGE_REDIRECTS:
            return response
        current_url = urljoin(current_url, response.location)
    return None


def _zendesk_subdomain_for_attachment(url: str, configured: str) -> str:
    candidate = configured.strip().lower()
    if not _ZENDESK_SUBDOMAIN_RE.fullmatch(candidate):
        return ""
    return candidate if is_zendesk_tenant_url(url, candidate) else ""


def _image_data_url(response: StreamedImageResponse, url_ref: str) -> str | None:
    if response.status_code != 200 or response.data is None:
        return None
    content_type = detected_image_content_type(
        response.content_type,
        response.data,
    )
    if content_type is None:
        logger.warning(
            "vision_image_invalid_content",
            url_ref=url_ref,
            stage="content_validation",
        )
        return None
    encoded = base64.b64encode(response.data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


async def _download_image_data_url_unbounded(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = _IMG_MAX_BYTES,
    progress: ImageDownloadProgress | None = None,
) -> str | None:
    progress = progress or ImageDownloadProgress()
    progress.stage = "configuration"
    cfg = get_config()
    env = EnvSettings()
    auth = None
    if env.zendesk_email and env.zendesk_api_token:
        auth = (f"{env.zendesk_email}/token", env.zendesk_api_token)
    configured_subdomain = env.zendesk_subdomain or cfg.zendesk.subdomain or ""
    zendesk_subdomain = _zendesk_subdomain_for_attachment(
        url,
        configured_subdomain,
    )
    url_ref = image_url_log_ref(url)

    try:
        progress.stage = "client_setup"
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=image_http_transport(),
        ) as client:
            response = await _request_image_with_redirects(
                client,
                url,
                zendesk_auth=auth,
                zendesk_subdomain=zendesk_subdomain,
                max_bytes=max_bytes,
                url_ref=url_ref,
                progress=progress,
            )
            if response is None:
                return None
            if response.status_code == 200:
                return _image_data_url(response, url_ref)

            sc_match = _SC_ATTACHMENT_RE.search(url)
            if (
                response.status_code in {401, 403}
                and sc_match
                and zendesk_subdomain
                and auth is not None
            ):
                attachment_id = sc_match.group(1)
                redirect_url = (
                    f"https://{zendesk_subdomain}.zendesk.com/api/v2/"
                    f"ticket_attachments/{attachment_id}/content"
                )
                redirected = await _request_image_with_redirects(
                    client,
                    redirect_url,
                    zendesk_auth=auth,
                    zendesk_subdomain=zendesk_subdomain,
                    max_bytes=max_bytes,
                    url_ref=url_ref,
                    progress=progress,
                )
                if redirected is None:
                    return None
                if redirected.status_code == 200:
                    return _image_data_url(redirected, url_ref)
                logger.warning(
                    "vision_sc_redirect_unexpected",
                    url_ref=url_ref,
                    attempted_ref=image_url_log_ref(redirect_url),
                    status=redirected.status_code,
                )

            logger.warning(
                "vision_image_download_http_error",
                url_ref=url_ref,
                status=response.status_code,
                stage="http_status",
            )
            return None
    except (ImageUrlRejected, httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning(
            "vision_image_download_failed",
            url_ref=url_ref,
            error_type=type(exc).__name__,
            error_cause_type=(type(exc.__cause__).__name__ if exc.__cause__ else ""),
            stage=progress.stage,
        )
        return None


async def download_image_data_url(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = _IMG_MAX_BYTES,
) -> str | None:
    result: str | None = None
    progress = ImageDownloadProgress()
    with move_on_after(max(0.001, timeout)) as cancel_scope:
        result = await _download_image_data_url_unbounded(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            progress=progress,
        )
    if not cancel_scope.cancel_called:
        return result
    logger.warning(
        "vision_image_download_failed",
        url_ref=image_url_log_ref(url),
        error_type="TimeoutError",
        stage=progress.stage,
        timeout_s=timeout,
    )
    return None
