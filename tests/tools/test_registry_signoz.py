"""registry：注册 sz 工具 + 不再注册 MCP signoz 工具。"""

from zd_agent.tools.registry import create_tool_registry


def test_sz_tools_registered():
    reg = create_tool_registry()
    for name in ["saas_deployed_version", "signoz_errors", "signoz_traceback",
                 "signoz_services", "signoz_query"]:
        assert name in reg, f"{name} 未注册"


def test_mcp_signoz_tools_are_not_registered():
    reg = create_tool_registry()
    for stale in ["signoz_search_logs", "signoz_aggregate_logs", "signoz_list_services"]:
        assert stale not in reg
