from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.archive_meta import build_archive_meta
from zd_agent.session.agent_session import AgentSession

logger = get_logger(module="runner.archive")

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _archive_session_dir(session: AgentSession) -> Path:
    cfg = get_config()
    raw_session_id = str(session.session_id or "").strip()
    if raw_session_id not in {"", ".", ".."} and _SAFE_SESSION_ID_RE.fullmatch(
        raw_session_id
    ):
        session_dir_name = raw_session_id
    else:
        digest = hashlib.sha256(raw_session_id.encode("utf-8")).hexdigest()[:16]
        session_dir_name = f"session-{digest}"
    return (
        Path(cfg.archive.archive_dir)
        / str(session.ticket_id)
        / "runs"
        / session_dir_name
    )


def ensure_archive(
    session: AgentSession,
    *,
    elapsed_seconds: float,
) -> None:
    archive_dir: Path | None = None
    try:
        archive_dir = _archive_session_dir(session)
        meta_path = archive_dir / "meta.json"
        if meta_path.exists():
            session.archived = True
            return

        archive_dir.mkdir(parents=True, exist_ok=True)
        meta = build_archive_meta(
            session=session,
            archived_at=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=elapsed_seconds,
        )
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if session.analysis:
            (archive_dir / "analysis.md").write_text(
                session.analysis,
                encoding="utf-8",
            )
        if session.reply_draft:
            (archive_dir / "reply.md").write_text(
                session.reply_draft,
                encoding="utf-8",
            )
        logger.info(
            "auto_archive_created",
            ticket_id=session.ticket_id,
            path=str(archive_dir),
        )
        session.archived = True
    except Exception as exc:
        logger.warning(
            "auto_archive_failed",
            ticket_id=session.ticket_id,
            path=str(archive_dir or ""),
            error=str(exc),
        )
