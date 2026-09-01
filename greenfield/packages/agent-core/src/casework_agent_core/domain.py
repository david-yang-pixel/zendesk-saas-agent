"""Agent-core views over the shared immutable ticket contracts."""

from __future__ import annotations

from contracts.ticket import ActiveQuestion, TicketMessage, TicketSnapshot

# The core calls incoming messages comments; the cross-image contract uses the
# more general ``TicketMessage`` name. This is an alias, not a second schema.
TicketComment = TicketMessage


def resolve_active_question(snapshot: TicketSnapshot) -> ActiveQuestion:
    comments = tuple(
        message
        for message in snapshot.messages
        if message.public and message.author_origin.casefold() == "customer"
    )
    if not comments:
        raise ValueError("ticket has no public customer question")
    original = comments[0]
    clarification_comments = comments[1:]
    return ActiveQuestion(
        original_event_id=original.event_id,
        original_request=original.body,
        clarification_event_ids=tuple(
            comment.event_id for comment in clarification_comments
        ),
        current_facts=tuple(comment.body for comment in clarification_comments),
    )
