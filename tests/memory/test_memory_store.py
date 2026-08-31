from __future__ import annotations

from zd_agent.memory.models import MemoryKind, MemoryStatus
from zd_agent.memory.store import MemoryStore


def test_long_term_memory_requires_approval_before_recall(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    proposed = store.propose(
        kind=MemoryKind.EPISODIC,
        title="Ticket 4001 timeout investigation",
        content="A prior timeout was associated with provider latency.",
        tags=("timeout", "provider"),
        source_ticket_id=4001,
        evidence_references=("ticket:4001", "signoz:trace-abc"),
        proposed_by="session-child-1",
    )

    assert proposed.status is MemoryStatus.PROPOSED
    assert store.search("provider") == ()

    approved = store.approve(proposed.id, reviewer="admin")
    hits = store.search("provider")

    assert approved.status is MemoryStatus.ACTIVE
    assert len(hits) == 1
    assert hits[0].entry.id == proposed.id
    assert hits[0].authority == "historical_guidance"
    assert "not current-ticket evidence" in hits[0].disclaimer


def test_working_memory_is_shared_by_root_session_not_child_session(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    store.put_working(
        root_session_id="root-1",
        key="investigation.scope",
        value={"tenant_id": "tenant-7"},
        writer_session_id="child-a",
    )

    shared = store.get_working("root-1", "investigation.scope")
    isolated = store.get_working("root-2", "investigation.scope")

    assert shared is not None
    assert shared.value == {"tenant_id": "tenant-7"}
    assert shared.writer_session_id == "child-a"
    assert isolated is None


def test_memory_feedback_is_audited_without_mutating_entry_content(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    entry = store.propose(
        kind=MemoryKind.SEMANTIC,
        title="HTTP 200 boundary",
        content="An async HTTP 200 proves admission only.",
        proposed_by="root-1",
    )
    store.approve(entry.id, reviewer="admin")

    feedback = store.add_feedback(
        entry.id,
        useful=False,
        note="Too broad for synchronous endpoints.",
        actor="agent-session-2",
    )

    assert feedback.entry_id == entry.id
    assert feedback.useful is False
    assert store.get(entry.id).content == entry.content
    assert store.list_feedback(entry.id) == (feedback,)
