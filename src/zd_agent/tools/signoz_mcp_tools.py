"""SigNoz tools backed by the official SigNoz MCP server subprocess.

We don't maintain a hand-written SigNoz client anymore — instead we ship the
SigNoz-published Go binary and proxy tool calls through stdio JSON-RPC. This
gives us the always-up-to-date Query Builder v5 schema without owning that
complexity.

At registration time we spawn the subprocess, fetch tools/list, and create
one zd-agent ``Tool`` instance per MCP tool, with the JSON schema copied
verbatim. LLM sees them with their original names (signoz_search_logs etc.).
"""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from zd_agent.config import EnvSettings, get_config
from zd_agent.integrations.mcp_subprocess import MCPClientError, MCPSubprocessClient
from zd_agent.log import get_logger
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession

logger = get_logger(module="tools.signoz_mcp")


# Module-level singleton — the subprocess stays alive for the whole zd-agent run.
_client: MCPSubprocessClient | None = None


def _binary_path() -> str:
    cfg = get_config().signoz
    return getattr(cfg, "mcp_binary", "/opt/zd-agent/bin/signoz-mcp-server") or \
        "/opt/zd-agent/bin/signoz-mcp-server"


def _make_client() -> MCPSubprocessClient | None:
    """Build the singleton MCP client, or return None if SigNoz isn't configured."""
    global _client
    if _client is not None:
        return _client

    cfg = get_config().signoz
    env = EnvSettings()
    url = cfg.url or env.signoz_url if hasattr(env, "signoz_url") else cfg.url
    api_key = cfg.api_key or (env.signoz_api_key if hasattr(env, "signoz_api_key") else "")
    if not url or not api_key:
        logger.info("signoz_mcp_skipped", reason="not configured (url or api_key)")
        return None

    binary = _binary_path()
    import os
    if not os.path.exists(binary):
        logger.warning("signoz_mcp_binary_missing", path=binary)
        return None

    _client = MCPSubprocessClient(
        binary=binary,
        env={
            "SIGNOZ_URL": url,
            "SIGNOZ_API_KEY": api_key,
            "LOG_LEVEL": "warn",
        },
        client_name="zd-agent",
    )
    return _client


class _SignozMcpTool(Tool):
    """Generic Tool wrapping a single MCP tool."""

    group = "platform_issue"
    concurrency_safe = False  # subprocess single-flight via internal lock
    max_output_tokens = 5000

    def __init__(self, *, mcp_name: str, description: str, parameters: dict):
        # Cannot set class attrs at instance time; rely on __dict__ override
        self.name = mcp_name
        self.description = description
        self.parameters = parameters
        self._mcp_name = mcp_name

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        client = _make_client()
        if client is None:
            return ToolResult(
                success=False,
                error="SigNoz MCP unavailable (binary missing or not configured)",
            )
        # Structured logging: see exactly what we send to MCP. Helps diagnose
        # "rowsScanned=0" cases — distinguishes bad args from API-side empty response.
        try:
            args_preview = json.dumps(args, ensure_ascii=False)[:500]
        except Exception:
            args_preview = repr(args)[:500]
        logger.info("signoz_mcp_call", tool=self._mcp_name, args=args_preview)
        try:
            result = await client.call_tool(self._mcp_name, args)
        except MCPClientError as e:
            logger.warning("signoz_mcp_error", tool=self._mcp_name, error=str(e))
            return ToolResult(success=False, error=f"MCP error: {e}")
        # MCP returns {content: [{type:'text', text:'...'}], isError: bool}
        if result.get("isError"):
            content = result.get("content", [])
            err = content[0].get("text", "") if content else "unknown MCP error"
            logger.warning("signoz_mcp_tool_error", tool=self._mcp_name, error=err[:200])
            return ToolResult(success=False, error=err[:500])
        # Unwrap text content (most SigNoz tools return single text item with JSON)
        content = result.get("content", [])
        if content and isinstance(content[0], dict) and content[0].get("type") == "text":
            text = content[0]["text"]
            logger.info(
                "signoz_mcp_ok",
                tool=self._mcp_name,
                bytes=len(text),
                preview=text[:200].replace("\n", " "),
            )
            return ToolResult(output=text)
        # Fallback — dump the raw result
        dumped = json.dumps(result, ensure_ascii=False, indent=2)[:5000]
        logger.info("signoz_mcp_ok_raw", tool=self._mcp_name, bytes=len(dumped))
        return ToolResult(output=dumped)


# SigNoz Cloud 歧义字段提示。MCP 上游 tool description 给的示例
# (`body CONTAINS 'x'`, `service.name = 'x'`) 在 SigNoz Cloud 上会触发字段歧义
# warning ——`body` 同时存在于 attribute / log context，`service.name` 同时存在
# 于 attribute / resource。SigNoz 不报错而是默认选一个解释跑，结果命中 0 行。
# 在 description 里追加这段，让 LLM 改用 searchText 或带 context 前缀的字段。
_LOG_QUERY_HINT = (
    "\n\n[SigNoz Cloud 用法须知 — 重要]\n"
    "上面示例里的 `body CONTAINS 'x'` / `service.name = 'x'` 在 SigNoz Cloud 上**会触发字段歧义**\n"
    "（`body` 和 `service.name` 同时存在 attribute/log/resource 上下文），导致命中 0 行但不报错。\n"
    "正确写法（任选其一）：\n"
    "  1. 文本匹配 body：**用 `searchText` 参数**（不要把 `body CONTAINS '...'` 写进 query）。\n"
    "  2. 服务过滤：**用 `service` 参数**（不要在 query 里写 `service.name = '...'`）。\n"
    "  3. 自定义 query 里引用字段时，加上 context 前缀：\n"
    "     - `attributes.workflow_run_id = '...'`\n"
    "     - `resources.workspace_id = '...'`\n"
    "     - `log.body LIKE '%xxx%'`（注意是 LIKE 不是 CONTAINS，且要 % 通配）\n"
    "  4. 不要用多个 `body CONTAINS` AND 起来 — 拆成 searchText（一段文本）+ "
    "其它结构化条件用 query 字段。\n"
    "结果若有 `meta.warning.warnings` 含 'is ambiguous' 或 `rowsScanned: 0`，"
    "说明 query 条件没生效，需按上面规则重写。"
)


async def discover_signoz_mcp_tools() -> list[_SignozMcpTool]:
    """Bootstrap once: spawn MCP subprocess, list its tools, wrap each."""
    client = _make_client()
    if client is None:
        return []
    try:
        tools_meta = await client.list_tools()
    except Exception as e:
        logger.warning("signoz_mcp_list_tools_failed", error=str(e))
        return []
    out: list[_SignozMcpTool] = []
    for meta in tools_meta:
        try:
            description = meta.get("description", "")
            # 给 logs 类工具追加 Cloud 歧义字段提示
            name = meta["name"]
            if name in ("signoz_search_logs", "signoz_aggregate_logs"):
                description = description + _LOG_QUERY_HINT
            t = _SignozMcpTool(
                mcp_name=name,
                description=description,
                parameters=meta.get("inputSchema") or {
                    "type": "object", "properties": {}, "required": [],
                },
            )
            out.append(t)
        except Exception as e:
            logger.warning("signoz_mcp_tool_load_failed", name=meta.get("name"), error=str(e))
    logger.info("signoz_mcp_tools_loaded", count=len(out))
    return out


async def shutdown_signoz_mcp() -> None:
    """Cleanup hook for app shutdown."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
