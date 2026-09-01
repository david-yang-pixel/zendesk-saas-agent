from casework_harness.sandbox_policy import (
    SandboxFailure,
    SandboxResourcePolicy,
)
from contracts.sandbox import SandboxResources


def test_first_attempt_uses_fixed_default_resources() -> None:
    policy = SandboxResourcePolicy()

    allocation = policy.allocation_for(attempt=1)

    assert allocation.cpus == 0.5
    assert allocation.memory_bytes == 1024**3
    assert allocation.memory_swap_bytes == 1024**3
    assert allocation.timeout_seconds == 30 * 60
    assert allocation.docker_limits() == {
        "nano_cpus": 500_000_000,
        "mem_limit": 1024**3,
        "memswap_limit": 1024**3,
    }


def test_resource_exhaustion_allows_one_larger_retry() -> None:
    policy = SandboxResourcePolicy()

    allocation = policy.allocation_for(
        attempt=2,
        previous_failure=SandboxFailure.RESOURCE_EXHAUSTED,
    )

    assert allocation.cpus == 1.0
    assert allocation.memory_bytes == 1024**3


def test_non_resource_failure_never_receives_larger_retry() -> None:
    policy = SandboxResourcePolicy()

    assert (
        policy.retry_allocation(
            attempt=1,
            failure=SandboxFailure.EXECUTION_FAILED,
        )
        is None
    )


def test_resource_retry_is_available_only_once() -> None:
    policy = SandboxResourcePolicy()

    assert (
        policy.retry_allocation(
            attempt=2,
            failure=SandboxFailure.RESOURCE_EXHAUSTED,
        )
        is None
    )


def test_harness_policy_matches_cross_image_resource_contract() -> None:
    policy = SandboxResourcePolicy()

    for attempt, previous_failure in (
        (1, None),
        (2, SandboxFailure.RESOURCE_EXHAUSTED),
    ):
        allocation = policy.allocation_for(
            attempt=attempt, previous_failure=previous_failure
        )
        contract = SandboxResources(
            cpu=allocation.cpus,
            memory_mb=allocation.memory_bytes // 1024**2,
            timeout_seconds=allocation.timeout_seconds,
        )

        assert contract.cpu == allocation.cpus
        assert contract.memory_mb == 1024
