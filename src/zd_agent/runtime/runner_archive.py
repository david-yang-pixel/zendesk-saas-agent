from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.archive_meta import build_archive_meta
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.image_evidence import guard_against_stale_no_image_claim
from zd_agent.session.agent_session import AgentSession

logger = get_logger(module="runner.archive")


def ensure_archive(session: AgentSession, guardrails: Guardrails) -> None:
    cfg = get_config()
    archive_dir = Path(cfg.knowledge.archive_dir) / str(session.ticket_id)
    meta_path = archive_dir / "meta.json"
    if meta_path.exists():
        return

    archive_dir.mkdir(parents=True, exist_ok=True)
    meta = build_archive_meta(
        session=session,
        archived_at=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=guardrails.elapsed_seconds,
    )
    try:
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if session.analysis:
            (archive_dir / "analysis.md").write_text(
                guard_against_stale_no_image_claim(session.analysis, session),
                encoding="utf-8",
            )
        if session.reply_draft:
            (archive_dir / "reply.md").write_text(
                guard_against_stale_no_image_claim(session.reply_draft, session),
                encoding="utf-8",
            )
        logger.info(
            "auto_archive_created",
            ticket_id=session.ticket_id,
            path=str(archive_dir),
        )
    except Exception as exc:
        logger.warning(
            "auto_archive_failed",
            ticket_id=session.ticket_id,
            error=str(exc),
        )
