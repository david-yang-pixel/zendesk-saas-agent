"""Host-identity fence preventing two copied Agent states from writing Zendesk."""

from __future__ import annotations

import os
from pathlib import Path
import stat


_TRUE_VALUES = {"1", "true", "yes", "on"}


class InstanceFenceError(RuntimeError):
    pass


def instance_fence_enabled() -> bool:
    return os.getenv("ZD_AGENT_ENFORCE_INSTANCE_FENCE", "").strip().lower() in (
        _TRUE_VALUES
    )


def current_instance_id() -> str:
    configured = os.getenv("ZD_AGENT_INSTANCE_ID", "").strip()
    if configured:
        return configured
    for path in (Path("/etc/host-machine-id"), Path("/etc/machine-id")):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    raise InstanceFenceError("active instance identity is unavailable")


def verify_active_instance(state_dir: Path) -> str:
    """Fail before any write-capable runtime starts on a demoted host.

    A persisted marker is authoritative even outside systemd, so an operator
    cannot bypass the fence by running the CLI manually on the old host. The
    environment flag only changes the missing-marker case to fail closed.
    """
    marker = state_dir / "active_instance_id"
    try:
        metadata = marker.lstat()
    except FileNotFoundError:
        if not instance_fence_enabled():
            return ""
        raise InstanceFenceError(f"active instance marker is unavailable: {marker}")
    except OSError as error:
        raise InstanceFenceError(
            f"active instance marker is unavailable: {marker}",
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise InstanceFenceError("active instance marker is not a regular file")
    instance_id = current_instance_id()
    try:
        active_id = marker.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise InstanceFenceError(
            f"active instance marker is unavailable: {marker}",
        ) from error
    if not active_id:
        raise InstanceFenceError("active instance marker is empty")
    if active_id != instance_id:
        raise InstanceFenceError(
            "this host is fenced from Agent side effects "
            f"(active={active_id}, local={instance_id})",
        )
    return instance_id
