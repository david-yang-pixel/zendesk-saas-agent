from zd_agent.services.assignee import is_human_assignee


def test_empty_assignee_is_not_human() -> None:
    assert not is_human_assignee(None, non_human_assignee_ids=())
    assert not is_human_assignee("", non_human_assignee_ids=())


def test_non_human_assignee_ids_are_excluded() -> None:
    assert not is_human_assignee(
        "123",
        non_human_assignee_ids=[123],
    )


def test_other_assignee_is_human() -> None:
    assert is_human_assignee(
        "456",
        non_human_assignee_ids=[123],
    )
