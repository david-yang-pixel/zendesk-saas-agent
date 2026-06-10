"""Tests for local knowledge-base tool metadata."""

from pathlib import Path

import pytest

from zd_agent.tools.knowledge import KnowledgeRead, KnowledgeSearch, _resolve_search_dir


def test_knowledge_search_schema_does_not_advertise_ticket_records() -> None:
    schema = KnowledgeSearch().to_openai_schema()
    text = str(schema)

    assert "tickets/" not in text
    assert "ticket_record_search" in text
    assert "历史工单" in text


def test_knowledge_search_rejects_ticket_record_path(tmp_path: Path) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()

    search_dir, error = _resolve_search_dir(docs_dir, "工单记录//")

    assert search_dir is None
    assert error is not None
    assert "ticket_record_search" in error


@pytest.mark.asyncio
async def test_knowledge_search_falls_back_to_root_for_missing_subdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()
    (docs_dir / "index.md").write_text(
        "Queuing indexing documents stuck\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "zd_agent.tools.knowledge._resolve_docs_dir",
        lambda: docs_dir,
    )

    result = await KnowledgeSearch().execute(
        {"query": "Queuing indexing documents stuck", "path": "tickets/"},
        session=None,  # type: ignore[arg-type]
    )

    assert result.success is True
    assert "已忽略不存在的知识库子目录: tickets" in result.output
    assert "Queuing indexing documents stuck" in result.output


def test_knowledge_search_rejects_path_traversal(tmp_path: Path) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    search_dir, error = _resolve_search_dir(docs_dir, "../outside")

    assert search_dir is None
    assert error == "路径超出知识库范围"


@pytest.mark.asyncio
async def test_knowledge_search_rejects_non_string_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()
    monkeypatch.setattr(
        "zd_agent.tools.knowledge._resolve_docs_dir",
        lambda: docs_dir,
    )

    result = await KnowledgeSearch().execute(
        {"query": {"text": "workflow stuck"}},
        session=None,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.error == "query must be a string"


@pytest.mark.asyncio
async def test_knowledge_search_rejects_missing_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()
    monkeypatch.setattr(
        "zd_agent.tools.knowledge._resolve_docs_dir",
        lambda: docs_dir,
    )

    result = await KnowledgeSearch().execute(
        {},
        session=None,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.error == "query is required"


@pytest.mark.asyncio
async def test_knowledge_search_rejects_blank_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()
    monkeypatch.setattr(
        "zd_agent.tools.knowledge._resolve_docs_dir",
        lambda: docs_dir,
    )

    result = await KnowledgeSearch().execute(
        {"query": "   "},
        session=None,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.error == "query is empty"


@pytest.mark.asyncio
async def test_knowledge_read_rejects_non_string_filepath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()
    monkeypatch.setattr(
        "zd_agent.tools.knowledge._resolve_docs_dir",
        lambda: docs_dir,
    )

    result = await KnowledgeRead().execute(
        {"filepath": {"path": "index.md"}},
        session=None,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.error == "filepath must be a string"
