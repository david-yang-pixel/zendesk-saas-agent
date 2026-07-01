"""Background worker for trigger queue messages."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.config import EnvSettings, get_config
from zd_agent.log import get_logger
from zd_agent.services.trigger_delay import wait_for_trigger_availability
from zd_agent.services.trigger_message_handler import handle_trigger_message

logger = get_logger(module="services.trigger_consumer")

HandleMessageFn = Callable[..., Awaitable[Any]]


def default_trigger_log_path(cfg: Any) -> Path:
    return (
        Path(cfg.resources.base_dir)
        / cfg.resources.state_dir
        / "logs"
        / "trigger_log.jsonl"
    )


async def run_trigger_consumer(
    queue: Any,
    manager: Any,
    state: Any,
    *,
    cfg: Any | None = None,
    env: Any | None = None,
    log_path: Path | None = None,
    handle_message_fn: HandleMessageFn = handle_trigger_message,
    silence_expired_ask_fn: HandleMessageFn | None = None,
    post_verdict_dispatch_fn: HandleMessageFn | None = None,
    escalate_fn: HandleMessageFn | None = None,
) -> None:
    """Consume trigger queue messages and delegate one-message handling."""
    cfg = cfg or get_config()
    env = env or EnvSettings()
    log_path = log_path or default_trigger_log_path(cfg)

    while True:
        msg = await queue.get()
        try:
            await wait_for_trigger_availability(msg)
            await handle_message_fn(
                msg=msg,
                manager=manager,
                state=state,
                cfg=cfg,
                env=env,
                log_path=log_path,
                silence_expired_ask_fn=silence_expired_ask_fn,
                post_verdict_dispatch_fn=post_verdict_dispatch_fn,
                escalate_fn=escalate_fn,
            )
        except Exception as e:
            logger.error(
                "trigger_consumer_error",
                error=str(e), msg=msg,
            )


async def run_saas_trigger_consumer(
    queue: Any,
    manager: Any,
    state: Any,
    *,
    cfg: Any | None = None,
    env: Any | None = None,
    log_path: Path | None = None,
    handle_message_fn: HandleMessageFn = handle_trigger_message,
) -> None:
    """Run the SaaS trigger consumer with default action adapters."""
    from zd_agent.services.trigger_actions import (
        handle_silence_expired_ask_action,
        post_verdict_dispatch_action,
        set_escalated_to_human_action,
    )

    await run_trigger_consumer(
        queue,
        manager,
        state,
        cfg=cfg,
        env=env,
        log_path=log_path,
        handle_message_fn=handle_message_fn,
        silence_expired_ask_fn=handle_silence_expired_ask_action,
        post_verdict_dispatch_fn=post_verdict_dispatch_action,
        escalate_fn=set_escalated_to_human_action,
    )
