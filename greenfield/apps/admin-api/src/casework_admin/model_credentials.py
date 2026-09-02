"""Secret-file backed model credential management for the Admin API.

Only masked metadata crosses the API boundary. Candidate secrets are verified
against the same Responses endpoint used by Sandbox before an atomic replace.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import socket
import stat
import tempfile
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from casework_admin.config import AdminConfig, ModelConfig, normalize_model_base_url

_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

EndpointGuard = Callable[[str], Awaitable[None]]


async def validate_public_model_endpoint(base_url: str) -> None:
    """Resolve immediately before use and reject non-public destinations."""

    normalized = normalize_model_base_url(base_url)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("model endpoint has no hostname")
    port = parsed.port or 443
    try:
        records = cast(
            list[
                tuple[
                    int,
                    int,
                    int,
                    str,
                    tuple[str, int] | tuple[str, int, int, int],
                ]
            ],
            await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ),
        )
    except socket.gaierror as error:
        raise ValueError("model endpoint DNS lookup failed") from error
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    if not addresses or any(not ip_address(address).is_global for address in addresses):
        raise ValueError("model endpoint must resolve only to public addresses")


def credential_environment_key(credential_ref: str) -> str:
    if not _SAFE_REFERENCE.fullmatch(credential_ref):
        raise ValueError("credential reference is invalid")
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", credential_ref).strip("_").upper()
    if not normalized:
        raise ValueError("credential reference is invalid")
    return f"CASEWORK_CREDENTIAL_{normalized}"


def parse_credential_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name.startswith("CASEWORK_CREDENTIAL_"):
            continue
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                continue
            if not isinstance(decoded, str):
                continue
            value = decoded.replace("$$", "$")
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        if value and "\n" not in value and "\r" not in value:
            values[name] = value
    return values


def _dotenv_line(name: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
    return f'{name}="{escaped}"\n'


class CredentialStore(Protocol):
    async def metadata(
        self, credential_ref: str
    ) -> tuple[bool, str | None, datetime | None]: ...

    async def replace(self, credential_ref: str, secret: str) -> None: ...


class FileCredentialStore:
    """Atomically update one namespaced credential in a dedicated file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def metadata(
        self, credential_ref: str
    ) -> tuple[bool, str | None, datetime | None]:
        return await asyncio.to_thread(self._metadata_sync, credential_ref)

    def _metadata_sync(
        self, credential_ref: str
    ) -> tuple[bool, str | None, datetime | None]:
        key = credential_environment_key(credential_ref)
        values = parse_credential_file(self._path)
        secret = values.get(key)
        if not secret:
            return False, None, None
        changed = datetime.fromtimestamp(self._path.stat().st_mtime, tz=UTC)
        suffix = secret[-4:] if len(secret) > 4 else ""
        masked = f"••••••••{suffix}" if suffix else "••••••••"
        return True, masked, changed

    async def replace(self, credential_ref: str, secret: str) -> None:
        await asyncio.to_thread(self._replace_sync, credential_ref, secret)

    def _replace_sync(self, credential_ref: str, secret: str) -> None:
        key = credential_environment_key(credential_ref)
        if not secret.strip() or secret != secret.strip():
            raise ValueError(
                "credential must be non-empty without surrounding whitespace"
            )
        if "\n" in secret or "\r" in secret or len(secret) > 8192:
            raise ValueError("credential must be a bounded single-line value")
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self._path.parent / ".credentials.lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            lock_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self._replace_locked(key, secret)

    def _replace_locked(self, key: str, secret: str) -> None:
        values = parse_credential_file(self._path)
        values[key] = secret
        descriptor, temporary = tempfile.mkstemp(
            prefix=".credentials-", suffix=".tmp", dir=self._path.parent
        )
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                for name, value in sorted(values.items()):
                    stream.write(_dotenv_line(name, value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._path)
            directory = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ModelBinding:
    provider: str
    base_url: str
    model: str
    credential_ref: str


class ModelProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    latency_ms: int | None = None
    error_code: str | None = None


class ModelCredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credential_ref: str
    configured: bool
    masked_value: str | None = None
    updated_at: datetime | None = None
    bindings: tuple[ModelBinding, ...]


class ModelCredentialList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelCredentialStatus, ...]


class ModelCredentialReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    credential_ref: str = Field(min_length=1, max_length=255)
    secret: str = Field(min_length=8, max_length=8192)


class ModelCatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    provider: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=8, max_length=2048)
    secret: str = Field(min_length=8, max_length=8192)


class ModelCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    base_url: str
    models: tuple[str, ...]
    latency_ms: int


class ModelCredentialReplaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credential: ModelCredentialStatus
    probe: ModelProbeResult


class ModelCredentialValidationError(RuntimeError):
    def __init__(self, result: ModelProbeResult) -> None:
        self.result = result
        detail = result.error_code or "unavailable"
        super().__init__(f"model credential validation failed: {detail}")


class ModelCredentialNotConfigured(ValueError):
    pass


class ModelCredentialProbe(Protocol):
    async def validate(
        self, *, provider: str, base_url: str, model: str, secret: str
    ) -> ModelProbeResult: ...

    async def discover(
        self, *, provider: str, base_url: str, secret: str
    ) -> ModelCatalogResponse: ...


class OpenAIResponsesCredentialProbe:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        endpoint_guard: EndpointGuard = validate_public_model_endpoint,
    ) -> None:
        self._base_url = (
            normalize_model_base_url(base_url) if base_url is not None else None
        )
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._endpoint_guard = endpoint_guard

    async def validate(
        self,
        *,
        provider: str,
        base_url: str | None = None,
        model: str,
        secret: str,
    ) -> ModelProbeResult:
        if provider != "openai":
            return ModelProbeResult(
                status="degraded", error_code="unsupported_provider"
            )
        normalized = normalize_model_base_url(base_url or self._base_url or "")
        try:
            await self._endpoint_guard(normalized)
        except ValueError:
            return ModelProbeResult(status="degraded", error_code="unsafe_endpoint")
        started = time.monotonic()
        try:
            response = await self._client.post(
                f"{normalized}/v1/responses",
                headers={"authorization": f"Bearer {secret}"},
                json={
                    "model": model,
                    "input": "Reply with OK.",
                    "max_output_tokens": 16,
                    "metadata": {
                        "casework_provider": provider,
                        "purpose": "credential_test",
                    },
                    "store": False,
                },
                timeout=30.0,
            )
        except httpx.TimeoutException:
            return ModelProbeResult(status="degraded", error_code="timeout")
        except httpx.HTTPError:
            return ModelProbeResult(status="degraded", error_code="unavailable")
        latency = max(0, round((time.monotonic() - started) * 1000))
        if response.is_success:
            return ModelProbeResult(status="connected", latency_ms=latency)
        return ModelProbeResult(
            status="degraded",
            latency_ms=latency,
            error_code=f"HTTP_{response.status_code}_{_provider_error_code(response)}",
        )

    async def discover(
        self, *, provider: str, base_url: str, secret: str
    ) -> ModelCatalogResponse:
        if provider != "openai":
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="unsupported_provider")
            )
        normalized = normalize_model_base_url(base_url)
        try:
            await self._endpoint_guard(normalized)
        except ValueError as error:
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="unsafe_endpoint")
            ) from error
        started = time.monotonic()
        try:
            response = await self._client.get(
                f"{normalized}/v1/models",
                headers={"authorization": f"Bearer {secret}"},
                timeout=15.0,
            )
        except httpx.TimeoutException as error:
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="timeout")
            ) from error
        except httpx.HTTPError as error:
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="unavailable")
            ) from error
        latency = max(0, round((time.monotonic() - started) * 1000))
        if not response.is_success:
            raise ModelCredentialValidationError(
                ModelProbeResult(
                    status="degraded",
                    latency_ms=latency,
                    error_code=(
                        f"HTTP_{response.status_code}_{_provider_error_code(response)}"
                    ),
                )
            )
        if len(response.content) > 1_000_000:
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="response_too_large")
            )
        try:
            raw_payload: object = response.json()
        except ValueError as error:
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="invalid_response")
            ) from error
        if not isinstance(raw_payload, dict):
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="invalid_response")
            )
        payload = cast(dict[str, object], raw_payload)
        raw_data = payload.get("data")
        if not isinstance(raw_data, list):
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="invalid_response")
            )
        data = cast(list[object], raw_data)
        discovered: set[str] = set()
        for raw_item in data[:500]:
            if not isinstance(raw_item, dict):
                continue
            item = cast(dict[str, object], raw_item)
            identifier = item.get("id")
            if not isinstance(identifier, str):
                continue
            normalized_identifier = identifier.strip()
            if 0 < len(normalized_identifier) <= 255:
                discovered.add(normalized_identifier)
        model_ids = tuple(sorted(discovered))
        if not model_ids:
            raise ModelCredentialValidationError(
                ModelProbeResult(status="degraded", error_code="no_models")
            )
        return ModelCatalogResponse(
            provider=provider,
            base_url=normalized,
            models=model_ids,
            latency_ms=latency,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _provider_error_code(response: httpx.Response) -> str:
    code = "provider_error"
    try:
        payload = response.json()
    except ValueError:
        return code
    if not isinstance(payload, dict):
        return code
    error = cast(dict[str, Any], payload).get("error")
    if not isinstance(error, dict):
        return code
    error_mapping = cast(dict[str, Any], error)
    candidate = error_mapping.get("code") or error_mapping.get("type")
    if isinstance(candidate, str) and _SAFE_ERROR_CODE.fullmatch(candidate.strip()):
        return candidate.strip()
    return code


def model_bindings(config: AdminConfig) -> tuple[ModelBinding, ...]:
    models: Iterable[ModelConfig] = (config.root_model, *config.subagent_slots)
    return tuple(
        ModelBinding(
            provider=binding.provider,
            base_url=binding.base_url,
            model=binding.model,
            credential_ref=binding.credential_ref,
        )
        for binding in models
    )


class ModelCredentialService:
    def __init__(self, *, store: CredentialStore, probe: ModelCredentialProbe) -> None:
        self._store = store
        self._probe = probe

    async def list(self, config: AdminConfig) -> ModelCredentialList:
        grouped = _group_bindings(model_bindings(config))
        items: list[ModelCredentialStatus] = []
        for credential_ref, bindings in grouped.items():
            configured, masked, updated_at = await self._store.metadata(credential_ref)
            items.append(
                ModelCredentialStatus(
                    credential_ref=credential_ref,
                    configured=configured,
                    masked_value=masked,
                    updated_at=updated_at,
                    bindings=bindings,
                )
            )
        return ModelCredentialList(items=tuple(items))

    async def replace(
        self, *, config: AdminConfig, credential_ref: str, secret: str
    ) -> ModelCredentialReplaceResponse:
        grouped = _group_bindings(model_bindings(config))
        bindings = grouped.get(credential_ref)
        if not bindings:
            raise ModelCredentialNotConfigured(
                "credential reference is not used by model configuration"
            )
        last_probe: ModelProbeResult | None = None
        for binding in bindings:
            last_probe = await self._probe.validate(
                provider=binding.provider,
                base_url=binding.base_url,
                model=binding.model,
                secret=secret,
            )
            if last_probe.status != "connected":
                raise ModelCredentialValidationError(last_probe)
        await self._store.replace(credential_ref, secret)
        configured, masked, updated_at = await self._store.metadata(credential_ref)
        assert last_probe is not None
        return ModelCredentialReplaceResponse(
            credential=ModelCredentialStatus(
                credential_ref=credential_ref,
                configured=configured,
                masked_value=masked,
                updated_at=updated_at,
                bindings=bindings,
            ),
            probe=last_probe,
        )

    async def discover(
        self, *, provider: str, base_url: str, secret: str
    ) -> ModelCatalogResponse:
        if secret != secret.strip() or "\n" in secret or "\r" in secret:
            raise ValueError("credential must be a bounded single-line value")
        return await self._probe.discover(
            provider=provider.strip(), base_url=base_url, secret=secret
        )


def _group_bindings(
    bindings: Iterable[ModelBinding],
) -> dict[str, tuple[ModelBinding, ...]]:
    grouped: defaultdict[str, list[ModelBinding]] = defaultdict(list)
    environment_keys: dict[str, str] = {}
    for binding in bindings:
        key = credential_environment_key(binding.credential_ref)
        previous = environment_keys.get(key)
        if previous is not None and previous != binding.credential_ref:
            raise ValueError("model credential references collide after normalization")
        environment_keys[key] = binding.credential_ref
        if binding not in grouped[binding.credential_ref]:
            grouped[binding.credential_ref].append(binding)
    return {key: tuple(value) for key, value in grouped.items()}


__all__ = [
    "FileCredentialStore",
    "ModelCredentialList",
    "ModelCredentialNotConfigured",
    "ModelCredentialProbe",
    "ModelCredentialReplaceRequest",
    "ModelCredentialReplaceResponse",
    "ModelCredentialService",
    "ModelCredentialValidationError",
    "ModelProbeResult",
    "OpenAIResponsesCredentialProbe",
    "credential_environment_key",
    "model_bindings",
    "parse_credential_file",
]
