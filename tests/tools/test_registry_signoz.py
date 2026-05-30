"""registry：注册 sz 工具 + 不再注册 MCP signoz 工具。"""
import pytest

from zd_agent.tools.registry import create_tool_registry, augment_registry_async


def test_sz_tools_registered():
    reg = create_tool_registry()
    for name in ["saas_deployed_version", "signoz_errors", "signoz_traceback",
                 "signoz_services", "signoz_query"]:
        assert name in reg, f"{name} 未注册"


@pytest.mark.asyncio
async def test_augment_does_not_spawn_mcp():
    reg = create_tool_registry()
    before = len(reg)
    await augment_registry_async(reg)  # 应为 no-op（MCP 已停用）
    assert len(reg) == before
    # 确认没有 MCP 的旧工具名
    for stale in ["signoz_search_logs", "signoz_aggregate_logs", "signoz_list_services"]:
        assert stale not in reg
