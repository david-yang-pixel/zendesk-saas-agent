"""Agent-core views over the shared immutable ticket contracts."""

from __future__ import annotations

from contracts.ticket import ActiveQuestion, TicketMessage, TicketSnapshot

# The core calls incoming messages comments; the cross-image contract uses the
# more general ``TicketMessage`` name. This is an alias, not a second schema.
TicketComment = TicketMessage


class TicketSnapshotNotReadyError(RuntimeError):
    """The read model has not exposed the webhook's triggering event yet."""

    def __init__(self, *, expected_event_id: str, observed_cursor: str) -> None:
        self.expected_event_id = expected_event_id
        self.observed_cursor = observed_cursor
        super().__init__(
            f"trigger event {expected_event_id} is not visible; "
            f"latest customer cursor is {observed_cursor}"
        )


def resolve_active_question(snapshot: TicketSnapshot) -> ActiveQuestion:
    latest_public_agent_index = max(
        (
            index
            for index, message in enumerate(snapshot.messages)
            if message.public and message.author_origin.casefold() == "agent"
        ),
        default=-1,
    )
    comments = tuple(
        message
        for message in snapshot.messages[latest_public_agent_index + 1 :]
        if message.public and message.author_origin.casefold() == "customer"
    )
    if not comments:
        raise ValueError("ticket has no unanswered public customer question")
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
