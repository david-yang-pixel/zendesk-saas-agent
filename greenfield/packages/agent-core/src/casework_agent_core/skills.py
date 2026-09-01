"""Progressive Skill manifest/content loader for ZendeskCasework."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import Field

from contracts._base import ContractModel


class SkillManifest(ContractModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dependencies: tuple[str, ...] = ()
    compatible_tools: tuple[str, ...] = ()
    selection_predicates: tuple[str, ...] = ()
    max_context_tokens: int = Field(default=2_000, gt=0)
    content_digest: str | None = None


class LoadedSkill(ContractModel):
    manifest: SkillManifest
    content: str = Field(min_length=1)


class SkillLoader:
    """Read cheap manifests first; load Markdown only when a Skill is selected."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._manifest_cache: dict[str, SkillManifest] = {}
        self._content_cache: dict[str, LoadedSkill] = {}

    @property
    def loaded_content(self) -> tuple[str, ...]:
        return tuple(self._content_cache)

    def manifest(self, name: str) -> SkillManifest:
        if name not in self._manifest_cache:
            path = self._root / name / "manifest.json"
            try:
                data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as error:
                raise ValueError(f"skill manifest unavailable: {name}") from error
            manifest = SkillManifest.model_validate(data)
            if manifest.name != name:
                raise ValueError(f"skill manifest name mismatch: {name}")
            self._manifest_cache[name] = manifest
        return self._manifest_cache[name]

    def load(self, name: str) -> LoadedSkill:
        if name in self._content_cache:
            return self._content_cache[name]
        manifest = self.manifest(name)
        dependencies = tuple(
            self.load(dependency).manifest.name for dependency in manifest.dependencies
        )
        del dependencies  # Loading dependencies is the progressive dependency contract.
        path = self._root / name / "content.md"
        try:
            content = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as error:
            raise ValueError(f"skill content unavailable: {name}") from error
        if not content:
            raise ValueError(f"skill content is empty: {name}")
        if manifest.content_digest:
            digest = hashlib.sha256(content.encode()).hexdigest()
            if digest != manifest.content_digest:
                raise ValueError(f"skill content digest mismatch: {name}")
        loaded = LoadedSkill(manifest=manifest, content=content)
        self._content_cache[name] = loaded
        return loaded
