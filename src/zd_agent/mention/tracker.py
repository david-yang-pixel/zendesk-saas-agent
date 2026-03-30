"""MentionTracker: 追踪已处理的 @mention 评论 ID，防止重复派发"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MentionTracker:
    def __init__(self, state_file: str | Path = ".state/processed_mentions.json") -> None:
        self._path = Path(state_file)
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def is_processed(self, comment_id: str | int) -> bool:
        return str(comment_id) in self._data

    def mark_processed(self, comment_id: str | int) -> None:
        self._data[str(comment_id)] = datetime.now(timezone.utc).isoformat()
        self._save()

    def cleanup(self, days: int = 30) -> None:
        """删除超过 days 天的记录。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        to_delete = [
            cid for cid, ts in self._data.items()
            if datetime.fromisoformat(ts) < cutoff
        ]
        for cid in to_delete:
            del self._data[cid]
        if to_delete:
            self._save()
