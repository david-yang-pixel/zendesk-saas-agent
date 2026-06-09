"""Tests for local knowledge-base tool metadata."""

from pathlib import Path

from zd_agent.tools.knowledge import KnowledgeSearch, _resolve_search_dir


def test_knowledge_search_schema_does_not_advertise_ticket_records() -> None:
    schema = KnowledgeSearch().to_openai_schema()
    text = str(schema)

    assert "tickets/" not in text
    assert "ticket_record_search" in text
    assert "历史工单" in text


def test_knowledge_search_rejects_ticket_record_path(tmp_path: Path) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()

    search_dir, error = _resolve_search_dir(docs_dir, "tickets//")

    assert search_dir is None
    assert error is not None
    assert "ticket_record_search" in error


def test_knowledge_search_rejects_path_traversal(tmp_path: Path) -> None:
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    search_dir, error = _resolve_search_dir(docs_dir, "../outside")

    assert search_dir is None
    assert error == "路径超出知识库范围"
