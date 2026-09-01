"""Docker Engine adapter for ephemeral Casework sandboxes.

The rest of the Harness talks to :class:`SandboxEngine`, an asynchronous port.
This module is the only place that knows about Docker's synchronous SDK and
the published sandbox HTTP port.  Both the Docker client and HTTP transport
are injected, so lifecycle tests never need a Docker daemon or a listening
socket.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Protocol, cast

from casework_harness.sandbox_controller import (
    SandboxEngine,
    SandboxExecution,
    SandboxExecutionStatus,
    SandboxResourceExhaustedError,
)
from casework_harness.sandbox_policy import SandboxResourcePolicy

_SANDBOX_PORT = 8103
_SANDBOX_TIMEOUT_SECONDS = 30 * 60
_NANO_CPU_DEFAULT = 500_000_000
_NANO_CPU_RETRY = 1_000_000_000
_GIB = 1024**3
_DATABASE_ENV_KEYS = frozenset(
    {"DATABASE_URL", "PGHOST", "PGPASSWORD", "POSTGRES_PASSWORD"}
)


class DockerContainerPort(Protocol):
    """Small synchronous subset of a Docker SDK container."""

    id: str
    attrs: Mapping[str, Any]

    def start(self) -> object: ...

    def reload(self) -> object: ...

    def remove(self, *, force: bool = False) -> object: ...


class DockerContainerCollectionPort(Protocol):
    def create(self, **kwargs: Any) -> DockerContainerPort: ...

    def get(self, container_id: str) -> DockerContainerPort: ...

    def list(self, **kwargs: Any) -> Sequence[DockerContainerPort]: ...


class DockerImageCollectionPort(Protocol):
    def get(self, image: str) -> object: ...


class DockerClientPort(Protocol):
    @property
    def containers(self) -> DockerContainerCollectionPort: ...

    @property
    def images(self) -> DockerImageCollectionPort: ...

    def ping(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class SandboxHttpResponse:
    status_code: int
    body: dict[str, Any]


class SandboxHttpPort(Protocol):
    async def get(
        self, url: str, *, timeout_seconds: int
    ) -> SandboxHttpResponse: ...

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout_seconds: int,
    ) -> SandboxHttpResponse: ...


class HttpxSandboxHttp:
    """Production HTTP transport, kept behind the injectable port."""

    def __init__(self, client: Any | None = None) -> None:
        # ``Any`` keeps this adapter independent from httpx's client protocol;
        # callers can inject a shared AsyncClient or let us create one lazily.
        self._client = client

    async def get(
        self, url: str, *, timeout_seconds: int
    ) -> SandboxHttpResponse:
        client = await self._get_client()
        response = await client.get(url, timeout=timeout_seconds)
        return _http_response(response)

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout_seconds: int,
    ) -> SandboxHttpResponse:
        client = await self._get_client()
        response = await client.post(url, json=json, timeout=timeout_seconds)
        return _http_response(response)

    async def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient()
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class SandboxReadinessError(RuntimeError):
    """The sandbox did not expose ``/readyz`` before its deadline."""


class SandboxReadinessResourceExhausted(
    SandboxReadinessError, SandboxResourceExhaustedError
):
    """The container was OOM-killed while it was becoming ready."""

    def __init__(self, error_code: str) -> None:
        SandboxReadinessError.__init__(self, error_code)
        SandboxResourceExhaustedError.__init__(self, error_code)


@dataclass(frozen=True, slots=True)
class DockerOrphanSweepReport:
    scanned: int
    destroyed: tuple[str, ...]
    skipped: int = 0
    failed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SandboxProbeResult:
    """Safe Harness readiness result for one launch probe."""

    ready: bool
    reason: str
    cached: bool = False


class SandboxLaunchProbe:
    """Check the sandbox image and protocol without running any casework.

    A launch probe creates a short-lived default-resource sandbox, waits only
    for ``/readyz``, and destroys it.  It deliberately never calls
    ``/v1/runs`` and carries no ticket identifier.  Results are cached and
    concurrent checks are coalesced so an operator polling ``/readyz`` does
    not continuously rebuild containers.
    """

    def __init__(
        self,
        *,
        client: DockerClientPort,
        engine: SandboxEngine,
        image: str,
        cache_ttl_seconds: float = 30.0,
        failure_retry_seconds: float = 5.0,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        if not image.strip():
            raise ValueError("sandbox image is required")
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        if failure_retry_seconds < 0:
            raise ValueError("failure_retry_seconds must not be negative")
        self._client = client
        self._engine = engine
        self._image = image
        self._cache_ttl_seconds = cache_ttl_seconds
        self._failure_retry_seconds = failure_retry_seconds
        self._monotonic = monotonic_clock or time.monotonic
        self._lock = asyncio.Lock()
        self._cached: tuple[float, SandboxProbeResult] | None = None
        self._probe_number = 0

    async def check(self) -> bool:
        """Return whether the last launch probe or a fresh probe is healthy."""
        return (await self.probe()).ready

    async def probe(self) -> SandboxProbeResult:
        cached = self._usable_cached(self._monotonic())
        if cached is not None:
            return replace(cached, cached=True)
        async with self._lock:
            cached = self._usable_cached(self._monotonic())
            if cached is not None:
                return replace(cached, cached=True)
            result = await self._run_probe()
            self._cached = (self._monotonic(), result)
            return result

    def _usable_cached(self, now: float) -> SandboxProbeResult | None:
        if self._cached is None:
            return None
        checked_at, result = self._cached
        ttl = self._cache_ttl_seconds if result.ready else self._failure_retry_seconds
        if now - checked_at < ttl:
            return result
        return None

    async def _run_probe(self) -> SandboxProbeResult:
        try:
            if not await _call_sync(self._client.ping):
                return SandboxProbeResult(ready=False, reason="docker_unavailable")
        except Exception as error:
            if _is_permission_denied(error):
                return SandboxProbeResult(
                    ready=False, reason="docker_permission_denied"
                )
            return SandboxProbeResult(ready=False, reason="docker_unavailable")
        try:
            await _call_sync(self._client.images.get, self._image)
        except Exception as error:
            if _is_permission_denied(error):
                return SandboxProbeResult(
                    ready=False, reason="docker_permission_denied"
                )
            return SandboxProbeResult(
                ready=False, reason="sandbox_image_unavailable"
            )

        self._probe_number += 1
        probe_id = f"launch-probe-{self._probe_number}"
        sandbox_id: str | None = None
        result = SandboxProbeResult(ready=False, reason="launch_failed")
        try:
            try:
                sandbox_id = await self._engine.create(
                    image=self._image,
                    limits=SandboxResourcePolicy()
                    .allocation_for(attempt=1)
                    .docker_limits(),
                    labels={
                        "casework.managed": "true",
                        "casework.probe": "true",
                        "casework.run_id": probe_id,
                        "casework.job_id": probe_id,
                        "casework.session_id": f"{probe_id}-session",
                        "casework.attempt": "1",
                    },
                )
                await self._engine.wait_ready(
                    sandbox_id, timeout_seconds=30 * 60
                )
                result = SandboxProbeResult(ready=True, reason="ready")
            except SandboxResourceExhaustedError:
                result = SandboxProbeResult(
                    ready=False, reason="resource_exhausted"
                )
            except Exception:
                result = SandboxProbeResult(ready=False, reason="readiness_failed")
        finally:
            if sandbox_id is not None:
                try:
                    await self._engine.destroy(sandbox_id)
                except Exception:
                    result = SandboxProbeResult(ready=False, reason="cleanup_failed")
        return result


def _http_response(response: Any) -> SandboxHttpResponse:
    body: object
    try:
        body = response.json()
    except (AttributeError, ValueError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    return SandboxHttpResponse(
        status_code=int(response.status_code), body=cast(dict[str, Any], body)
    )


async def _call_sync(
    function: Callable[..., Any], *args: object, **kwargs: object
) -> Any:
    return await asyncio.to_thread(partial(function, *args, **kwargs))


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return cast(Mapping[str, Any], value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("sandbox clock must return an aware datetime")
    return value.astimezone(UTC)


def _labels(container: DockerContainerPort) -> Mapping[str, str]:
    attrs = _mapping(container.attrs)
    config = _mapping(attrs.get("Config"))
    configured = config.get("Labels")
    if isinstance(configured, Mapping):
        configured_map = cast(Mapping[str, Any], configured)
        return {
            str(key): str(value)
            for key, value in configured_map.items()
            if value is not None
        }
    return {}


def _state(container: DockerContainerPort) -> Mapping[str, Any]:
    return _mapping(_mapping(container.attrs).get("State"))


def _resource_error_code(container: DockerContainerPort) -> str | None:
    state = _state(container)
    if bool(state.get("OOMKilled")):
        return "container_oom_killed"
    try:
        if int(state.get("ExitCode", 0)) == 137:
            return "container_oom_exit_137"
    except (TypeError, ValueError):
        pass
    return None


def _payload_status(body: Mapping[str, Any]) -> str | None:
    values: list[object] = [
        body.get("status"),
        body.get("retry_classification"),
        body.get("error_code"),
        body.get("reason"),
    ]
    outcome = _mapping(body.get("outcome"))
    values.extend(
        (
            outcome.get("status"),
            outcome.get("retry_classification"),
            outcome.get("error_code"),
            outcome.get("reason"),
        )
    )
    for value in values:
        if isinstance(value, str):
            normalized = value.casefold().replace("-", "_").replace(" ", "_")
            if "resource_exhaust" in normalized or normalized in {"oom", "oom_killed"}:
                return "resource_exhausted"
    return None


def _is_not_found(error: BaseException) -> bool:
    if error.__class__.__name__.casefold() == "notfound":
        return True
    status_code = getattr(error, "status_code", None)
    return status_code == 404


def _is_permission_denied(error: BaseException) -> bool:
    if isinstance(error, PermissionError):
        return True
    if error.__class__.__name__.casefold() in {"forbidden", "permissionerror"}:
        return True
    return getattr(error, "status_code", None) == 403


class DockerSandboxEngine:
    """Run the versioned sandbox protocol through one Docker Engine host."""

    def __init__(
        self,
        *,
        client: DockerClientPort,
        http: SandboxHttpPort,
        installation_id: str,
        controller_id: str,
        container_port: int = _SANDBOX_PORT,
        host: str = "127.0.0.1",
        environment: Mapping[str, str] | None = None,
        poll_interval_seconds: float = 0.25,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not installation_id.strip() or not controller_id.strip():
            raise ValueError("installation_id and controller_id are required")
        if not 1 <= container_port <= 65535:
            raise ValueError("container_port must be a valid TCP port")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must not be negative")
        sandbox_environment = dict(environment or {})
        if _DATABASE_ENV_KEYS.intersection(sandbox_environment):
            raise ValueError(
                "sandbox environment must not include database credentials"
            )
        self._client = client
        self._http = http
        self._installation_id = installation_id
        self._controller_id = controller_id
        self._container_port = container_port
        self._host = host
        self._environment = sandbox_environment
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._endpoints: dict[str, str] = {}

    async def create(
        self, *, image: str, limits: dict[str, int], labels: dict[str, str]
    ) -> str:
        if not image.strip():
            raise ValueError("sandbox image is required")
        _validate_limits(limits)
        now = _utc(self._clock())
        run_id = labels.get("casework.run_id") or labels.get("casework.job_id")
        if not run_id:
            raise ValueError(
                "sandbox labels require casework.run_id or casework.job_id"
            )
        merged_labels = dict(labels)
        merged_labels.update(
            {
                "casework.managed": "true",
                "casework.installation_id": self._installation_id,
                "casework.controller_id": self._controller_id,
                "casework.run_id": run_id,
                "casework.expires_at": _format_expiry(
                    now + timedelta(seconds=_SANDBOX_TIMEOUT_SECONDS)
                ),
            }
        )
        container = await _call_sync(
            self._client.containers.create,
            image=image,
            detach=True,
            labels=merged_labels,
            environment=self._environment,
            ports={f"{self._container_port}/tcp": None},
            **limits,
        )
        try:
            await _call_sync(container.start)
            await _call_sync(container.reload)
            endpoint = self._resolve_endpoint(container)
        except Exception:
            try:
                await _call_sync(container.remove, force=True)
            except Exception:
                pass
            raise
        self._endpoints[container.id] = endpoint
        return container.id

    async def endpoint(self, sandbox_id: str) -> str:
        endpoint = self._endpoints.get(sandbox_id)
        if endpoint is not None:
            return endpoint
        container = await _call_sync(self._client.containers.get, sandbox_id)
        await _call_sync(container.reload)
        endpoint = self._resolve_endpoint(container)
        self._endpoints[sandbox_id] = endpoint
        return endpoint

    async def wait_ready(self, sandbox_id: str, *, timeout_seconds: int) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        url = f"{await self.endpoint(sandbox_id)}/readyz"
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            try:
                response = await self._http.get(
                    url, timeout_seconds=max(1, timeout_seconds)
                )
            except Exception:
                response = None
            if response is not None and response.status_code // 100 == 2:
                status = response.body.get("status")
                if status in {None, "ready"}:
                    return
            container = await self._inspect(sandbox_id)
            if container is not None:
                error_code = _resource_error_code(container)
                if error_code is not None:
                    raise SandboxReadinessResourceExhausted(error_code)
            if asyncio.get_running_loop().time() >= deadline:
                raise SandboxReadinessError(
                    f"sandbox {sandbox_id} did not become ready before timeout"
                )
            await asyncio.sleep(self._poll_interval_seconds)

    async def execute(
        self,
        sandbox_id: str,
        *,
        request: dict[str, Any],
        timeout_seconds: int,
    ) -> SandboxExecution:
        url = f"{await self.endpoint(sandbox_id)}/v1/runs"
        try:
            response = await self._http.post(
                url, json=request, timeout_seconds=max(1, timeout_seconds)
            )
        except Exception:
            container = await self._inspect(sandbox_id)
            resource_error = (
                None
                if container is None
                else _resource_error_code(container)
            )
            if resource_error is not None:
                return SandboxExecution(
                    status=SandboxExecutionStatus.RESOURCE_EXHAUSTED,
                    error_code=resource_error,
                )
            return SandboxExecution(
                status=SandboxExecutionStatus.FAILED,
                error_code="sandbox_transport_error",
            )

        if _payload_status(response.body) == "resource_exhausted":
            return SandboxExecution(
                status=SandboxExecutionStatus.RESOURCE_EXHAUSTED,
                result=response.body,
                error_code="sandbox_reported_resource_exhaustion",
            )
        container = await self._inspect(sandbox_id)
        resource_error = None if container is None else _resource_error_code(container)
        if resource_error is not None:
            return SandboxExecution(
                status=SandboxExecutionStatus.RESOURCE_EXHAUSTED,
                result=response.body,
                error_code=resource_error,
            )
        if response.status_code // 100 != 2:
            return SandboxExecution(
                status=SandboxExecutionStatus.FAILED,
                result=response.body,
                error_code=f"sandbox_http_{response.status_code}",
            )
        status = _execution_status(response.body)
        return SandboxExecution(
            status=status,
            result=response.body,
            error_code=(
                None
                if status is SandboxExecutionStatus.SUCCEEDED
                else "sandbox_reported_failure"
            ),
        )

    async def destroy(self, sandbox_id: str) -> None:
        try:
            container = await _call_sync(self._client.containers.get, sandbox_id)
        except Exception as error:
            if _is_not_found(error):
                self._endpoints.pop(sandbox_id, None)
                return
            raise
        try:
            await _call_sync(container.remove, force=True)
        finally:
            self._endpoints.pop(sandbox_id, None)

    async def _inspect(self, sandbox_id: str) -> DockerContainerPort | None:
        try:
            container = await _call_sync(self._client.containers.get, sandbox_id)
            await _call_sync(container.reload)
            return container
        except Exception as error:
            if _is_not_found(error):
                return None
            return None

    def _resolve_endpoint(self, container: DockerContainerPort) -> str:
        attrs = _mapping(container.attrs)
        network = _mapping(attrs.get("NetworkSettings"))
        ports = _mapping(network.get("Ports"))
        bindings = ports.get(f"{self._container_port}/tcp")
        if not isinstance(bindings, Sequence) or isinstance(
            bindings, (str, bytes)
        ):
            raise RuntimeError("sandbox container did not publish its HTTP port")
        for binding in cast(Sequence[object], bindings):
            item = _mapping(binding)
            host_port = item.get("HostPort")
            if host_port:
                host_ip = str(item.get("HostIp") or self._host)
                if host_ip in {"0.0.0.0", "::", "[::]"}:
                    host_ip = self._host
                return f"http://{host_ip}:{int(host_port)}"
        raise RuntimeError("sandbox container HTTP port has no host binding")


class DockerSandboxOrphanSweeper:
    """Delete only expired sandboxes owned by this installation/controller."""

    def __init__(
        self,
        *,
        client: DockerClientPort,
        installation_id: str,
        controller_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not installation_id.strip() or not controller_id.strip():
            raise ValueError("installation_id and controller_id are required")
        self._client = client
        self._installation_id = installation_id
        self._controller_id = controller_id
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sweep(self) -> DockerOrphanSweepReport:
        containers = await _call_sync(
            self._client.containers.list,
            all=True,
            filters={
                "label": [
                    "casework.managed=true",
                    f"casework.installation_id={self._installation_id}",
                    f"casework.controller_id={self._controller_id}",
                ]
            },
        )
        now = _utc(self._clock())
        destroyed: list[str] = []
        failed: list[str] = []
        skipped = 0
        for container in containers:
            labels = _labels(container)
            if not self._owned(labels):
                skipped += 1
                continue
            expires_at = _parse_expiry(labels.get("casework.expires_at"))
            if expires_at is None or expires_at > now:
                skipped += 1
                continue
            try:
                await _call_sync(container.remove, force=True)
            except Exception as error:
                if _is_not_found(error):
                    destroyed.append(container.id)
                else:
                    failed.append(container.id)
            else:
                destroyed.append(container.id)
        return DockerOrphanSweepReport(
            scanned=len(containers),
            destroyed=tuple(destroyed),
            skipped=skipped,
            failed=tuple(failed),
        )

    def _owned(self, labels: Mapping[str, str]) -> bool:
        return (
            labels.get("casework.managed") == "true"
            and labels.get("casework.installation_id") == self._installation_id
            and labels.get("casework.controller_id") == self._controller_id
        )


def _format_expiry(value: datetime) -> str:
    return _utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _utc(parsed)
    except ValueError:
        return None


def _validate_limits(limits: Mapping[str, int]) -> None:
    expected_keys = {"nano_cpus", "mem_limit", "memswap_limit"}
    if set(limits) != expected_keys:
        raise ValueError("sandbox Docker limits must contain only fixed resource keys")
    if limits["nano_cpus"] not in {_NANO_CPU_DEFAULT, _NANO_CPU_RETRY}:
        raise ValueError("sandbox CPU must be 0.5 or 1.0")
    if limits["mem_limit"] != _GIB or limits["memswap_limit"] != _GIB:
        raise ValueError("sandbox memory and swap must each be 1 GiB")


def _execution_status(body: Mapping[str, Any]) -> SandboxExecutionStatus:
    status = body.get("status")
    if isinstance(status, str):
        normalized = status.casefold().replace("-", "_").replace(" ", "_")
        for value in SandboxExecutionStatus:
            if normalized == value.value:
                return value
    outcome = _mapping(body.get("outcome"))
    kind = outcome.get("kind")
    if isinstance(kind, str) and kind.casefold() in {"failed", "cancelled"}:
        return (
            SandboxExecutionStatus.FAILED
            if kind.casefold() == "failed"
            else SandboxExecutionStatus.CANCELLED
        )
    return SandboxExecutionStatus.SUCCEEDED
