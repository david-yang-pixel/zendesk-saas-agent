from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

_GIB = 1024**3


class SandboxFailure(StrEnum):
    RESOURCE_EXHAUSTED = "resource_exhausted"
    EXECUTION_FAILED = "execution_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SandboxAllocation:
    cpus: float
    memory_bytes: int
    memory_swap_bytes: int
    timeout_seconds: int

    def docker_limits(self) -> dict[str, int]:
        return {
            "nano_cpus": int(self.cpus * 1_000_000_000),
            "mem_limit": self.memory_bytes,
            "memswap_limit": self.memory_swap_bytes,
        }


class SandboxResourcePolicy:
    """Fixed two-attempt allocation policy approved for case sandboxes."""

    _default = SandboxAllocation(
        cpus=0.5,
        memory_bytes=_GIB,
        memory_swap_bytes=_GIB,
        timeout_seconds=30 * 60,
    )
    _resource_retry = SandboxAllocation(
        cpus=1.0,
        memory_bytes=_GIB,
        memory_swap_bytes=_GIB,
        timeout_seconds=30 * 60,
    )

    def allocation_for(
        self,
        *,
        attempt: int,
        previous_failure: SandboxFailure | None = None,
    ) -> SandboxAllocation:
        if attempt == 1 and previous_failure is None:
            return self._default
        if attempt == 2 and previous_failure is SandboxFailure.RESOURCE_EXHAUSTED:
            return self._resource_retry
        raise ValueError("sandbox allocation is not permitted for this attempt")

    def retry_allocation(
        self,
        *,
        attempt: int,
        failure: SandboxFailure,
    ) -> SandboxAllocation | None:
        if attempt == 1 and failure is SandboxFailure.RESOURCE_EXHAUSTED:
            return self._resource_retry
        return None
