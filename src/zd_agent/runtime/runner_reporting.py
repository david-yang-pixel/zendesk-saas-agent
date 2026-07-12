from __future__ import annotations

from zd_agent.runtime.events import LoopFinished
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.log_reporter import LogReporter
from zd_agent.session.agent_session import AgentSession


async def report_preflight_failure(
    session: AgentSession,
    guardrails: Guardrails,
    *,
    model: str,
    reason: str,
) -> None:
    reporter = LogReporter()
    try:
        if not reporter.enabled:
            return
        run_id = await reporter.report_run_start(
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            trigger_type=session.trigger_type,
            model=model,
        )
        session.remote_log_run_id = run_id
        if run_id is None:
            return
        await reporter.report_run_finish(
            run_id,
            LoopFinished(
                status="error",
                total_rounds=session.round_count,
                total_tokens=session.token_usage.total_tokens,
                elapsed_seconds=guardrails.elapsed_seconds,
                reason=reason,
            ),
            total_input_tokens=session.token_usage.prompt_tokens,
            total_output_tokens=session.token_usage.completion_tokens,
        )
    finally:
        await reporter.close()
