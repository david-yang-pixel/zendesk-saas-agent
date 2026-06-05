"""Compatibility exports for the Teleport auth web handlers."""
from __future__ import annotations

from zd_agent.services.teleport_auth_web import (
    _run_tsh_login,
    handle_teleport_auth_get,
    handle_teleport_auth_issue,
    handle_teleport_auth_post,
    render_form,
    render_result,
)

__all__ = [
    "_run_tsh_login",
    "handle_teleport_auth_get",
    "handle_teleport_auth_issue",
    "handle_teleport_auth_post",
    "render_form",
    "render_result",
]
