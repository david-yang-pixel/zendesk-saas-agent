from zd_agent.casework.adapters.evidence import evidence_contract
from zd_agent.casework.domain.enums import EvidenceAuthority


def test_subagent_and_memory_summaries_never_directly_support_current_claims() -> None:
    for tool_name in ("subagent_wait", "memory_search", "memory_get"):
        authority, roles = evidence_contract(tool_name)

        assert authority is EvidenceAuthority.HISTORICAL_GUIDANCE
        assert roles == ()
