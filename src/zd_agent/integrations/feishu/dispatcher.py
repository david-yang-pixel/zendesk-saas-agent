"""Dispatch parsed Feishu commands to the right handlers."""
from __future__ import annotations

import asyncio
from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.feishu.client import FeishuClient
from zd_agent.integrations.feishu.commands import Command
from zd_agent.log import get_logger

logger = get_logger(module="integrations.feishu.dispatcher")


async def dispatch_command(cmd: Command, *, manager: Any) -> None:
    """Route a parsed Command to its handler."""
    logger.info("feishu_command_dispatch", name=cmd.name, args=cmd.args)

    if cmd.name == "branch":
        await _handle_branch(cmd)
    elif cmd.name == "fetch-all":
        await _handle_fetch_all()
    elif cmd.name in ("reprocess", "process"):
        await _handle_process(cmd, manager=manager)
    elif cmd.name == "status":
        await _handle_status(cmd, manager=manager)
    elif cmd.name == "pause":
        await _handle_pause(manager=manager)
    elif cmd.name == "resume":
        await _handle_resume(manager=manager)
    elif cmd.name == "relogin":
        await _handle_relogin()
    elif cmd.name == "help":
        await _handle_help()
    else:
        await _handle_unknown(cmd)


def _feishu() -> FeishuClient:
    cfg = get_config().feishu
    return FeishuClient(app_id=cfg.app_id, app_secret=cfg.app_secret)


def _chat_id() -> str:
    return get_config().feishu.default_chat_id


async def _handle_branch(cmd: Command) -> None:
    if len(cmd.args) < 2:
        await _feishu().send_alert(
            chat_id=_chat_id(),
            title="Branch command error",
            message=f"Usage: /branch <repo> <branch>",
        )
        return

    repo, branch = cmd.args[0], cmd.args[1]
    # Run via saas_branch_switch tool's underlying implementation
    from zd_agent.tools.saas_branch import SaasBranchSwitch
    from zd_agent.session.agent_session import AgentSession

    tool = SaasBranchSwitch()
    # Create a throwaway session so the tool interface works
    session = AgentSession(ticket_id=0, trigger_type="feishu-cmd")
    result = await tool.execute({"repo": repo, "branch": branch}, session)
    await _feishu().send_alert(
        chat_id=_chat_id(),
        title="Branch switch result",
        message=result.output if result.success else f"Failed: {result.error}",
    )


async def _handle_fetch_all() -> None:
    """Fetch all SaaS repos. Thin wrapper: calls saas_branch_switch per-repo with current branch."""
    import subprocess
    cfg = get_config()
    base = cfg.repos.base_dir
    names = cfg.repos.repositories
    results = []
    for entry in names:
        name = entry if isinstance(entry, str) else entry.name
        try:
            rc = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", f"{base}/{name}", "fetch", "--all"],
                capture_output=True, timeout=60, text=True,
            )
            results.append(f"- {name}: {'ok' if rc.returncode == 0 else 'failed'}")
        except Exception as e:
            results.append(f"- {name}: error {e}")
    await _feishu().send_alert(
        chat_id=_chat_id(),
        title="Fetch-all done",
        message="\n".join(results),
    )


async def _handle_process(cmd: Command, *, manager: Any) -> None:
    if not cmd.args:
        await _feishu().send_alert(
            chat_id=_chat_id(),
            title="Command error",
            message=f"Usage: /{cmd.name} <ticket_id>",
        )
        return
    try:
        tid = int(cmd.args[0])
    except ValueError:
        await _feishu().send_alert(
            chat_id=_chat_id(),
            title="Command error",
            message=f"Invalid ticket_id: {cmd.args[0]}",
        )
        return
    force = cmd.name == "reprocess"
    try:
        await manager.dispatch(
            tid, dry_run=False,
            trigger_type=f"feishu-{cmd.name}",
            force=force,
        )
    except TypeError:
        # Old SessionManager may not accept `force` kwarg — retry without
        await manager.dispatch(tid, dry_run=False, trigger_type=f"feishu-{cmd.name}")


async def _handle_status(cmd: Command, *, manager: Any) -> None:
    # Optional: status for a specific ticket or overall
    msg = "Status endpoint not fully implemented yet"
    if cmd.args:
        msg = f"Ticket #{cmd.args[0]}: <state query not implemented>"
    await _feishu().send_alert(chat_id=_chat_id(), title="Status", message=msg)


async def _handle_pause(*, manager: Any) -> None:
    # Minimal: set a flag on manager if it supports it
    if hasattr(manager, "pause"):
        manager.pause()
    await _feishu().send_alert(chat_id=_chat_id(), title="Paused", message="Scheduler/webhook paused")


async def _handle_resume(*, manager: Any) -> None:
    if hasattr(manager, "resume"):
        manager.resume()
    await _feishu().send_alert(chat_id=_chat_id(), title="Resumed", message="Processing resumed")


async def _handle_relogin() -> None:
    """Trigger headless login + push URL."""
    from zd_agent.integrations.teleport import trigger_headless_login
    cfg = get_config()
    url = await trigger_headless_login(
        proxy=cfg.teleport.proxy,
        user=cfg.teleport.user,
        auth_home=cfg.teleport.auth_home,
    )
    if url:
        await _feishu().send_auth_renew(
            chat_id=_chat_id(), url=url, user=cfg.teleport.user,
        )
    else:
        await _feishu().send_alert(
            chat_id=_chat_id(),
            title="Relogin failed",
            message="Could not obtain headless URL",
            urgent=True,
        )


async def _handle_help() -> None:
    msg = (
        "Available commands:\n"
        "/branch <repo> <branch> — switch SaaS repo branch\n"
        "/fetch-all — fetch all 5 SaaS repos\n"
        "/process <id> — process ticket now (skip silence)\n"
        "/reprocess <id> — force re-process (bypass cache)\n"
        "/status [id] — runtime/ticket status\n"
        "/pause /resume — toggle processing\n"
        "/relogin — trigger Teleport renew\n"
        "/help — this message"
    )
    await _feishu().send_alert(chat_id=_chat_id(), title="Help", message=msg)


async def _handle_unknown(cmd: Command) -> None:
    await _feishu().send_alert(
        chat_id=_chat_id(),
        title="Unknown command",
        message=f"Did not understand: {cmd.raw}. Try /help",
    )
