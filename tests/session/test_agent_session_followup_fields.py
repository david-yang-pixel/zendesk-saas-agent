"""Verify AgentSession has the new followup-mode fields."""
from zd_agent.session.agent_session import AgentSession


def test_agent_session_defaults_to_first_pass():
    s = AgentSession(ticket_id=1)
    assert s.mode == "first_pass"
    assert s.previous_record == ""
    assert s.new_comments_diff == ""


def test_agent_session_accepts_followup():
    s = AgentSession(
        ticket_id=1,
        mode="followup",
        previous_record="prev",
        new_comments_diff="new",
    )
    assert s.mode == "followup"
    assert s.previous_record == "prev"
    assert s.new_comments_diff == "new"
