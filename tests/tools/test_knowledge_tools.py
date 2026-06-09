"""Tests for local knowledge-base tool metadata."""

from zd_agent.tools.knowledge import KnowledgeSearch


def test_knowledge_search_schema_does_not_advertise_ticket_records() -> None:
    schema = KnowledgeSearch().to_openai_schema()
    text = str(schema)

    assert "tickets/" not in text
    assert "ticket_record_search" in text
    assert "历史工单" in text
