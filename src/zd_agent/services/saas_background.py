"""Default background runner wiring for the SaaS entrypoint."""

from __future__ import annotations

from zd_agent.services.app_lifecycle import BackgroundRunners
from zd_agent.services.intake_poller import run_intake_poller
from zd_agent.services.mention_poller import run_mention_poller
from zd_agent.services.silence_waker import run_silence_waker
from zd_agent.services.waiting_poller import run_waiting_poller


def default_background_runners() -> BackgroundRunners:
    """Return the concrete background runners used by the SaaS app."""
    return BackgroundRunners(
        silence_waker=run_silence_waker,
        waiting_poller=run_waiting_poller,
        intake_poller=run_intake_poller,
        mention_poller=run_mention_poller,
    )
