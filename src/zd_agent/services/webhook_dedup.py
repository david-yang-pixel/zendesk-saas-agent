"""Idempotent webhook-event dedup cache backed by JSON on disk."""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path


class WebhookDedup:
    """In-memory LRU of webhook event keys, persisted to JSON."""

    def __init__(self, path: Path | str, max_size: int = 10000):
        self.path = Path(path)
        self.max_size = max_size
        self._seen: OrderedDict[str, bool] = OrderedDict()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        keys = raw.get("keys") if isinstance(raw, dict) else raw
        if isinstance(keys, list):
            for k in keys[-self.max_size:]:
                self._seen[str(k)] = True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"keys": list(self._seen.keys())}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def seen(self, key: str) -> bool:
        return key in self._seen

    def add(self, key: str) -> None:
        if key in self._seen:
            self._seen.move_to_end(key)
        else:
            self._seen[key] = True
            while len(self._seen) > self.max_size:
                self._seen.popitem(last=False)
        self._save()

    def __len__(self) -> int:
        return len(self._seen)
