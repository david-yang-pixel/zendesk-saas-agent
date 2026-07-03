from __future__ import annotations

import json
from typing import Any

from zd_agent.integrations.custom_mcp import call_custom_mcp_tool
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult


class CustomMcpCall(Tool):
    name = "custom_mcp_call"
    description = (
        "Call an admin-configured custom MCP service by service name and tool name. "
        "Use this only when the active Skill or system prompt names that custom MCP."
    )
    group = "custom_mcp"
    max_output_tokens = 6000
    parameters = {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "Custom MCP service name configured in admin.",
            },
            "tool": {
                "type": "string",
                "description": "Remote MCP tool name to call.",
            },
            "arguments": {
                "type": "object",
                "description": "JSON object passed as MCP tool arguments.",
            },
        },
        "required": ["service", "tool", "arguments"],
    }

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        service = _required_string(args, "service")
        if isinstance(service, ToolResult):
            return service
        tool = _required_string(args, "tool")
        if isinstance(tool, ToolResult):
            return tool
        arguments = args.get("arguments")
        if not isinstance(arguments, dict):
            return ToolResult(success=False, error="arguments must be a JSON object")

        payload = await call_custom_mcp_tool(
            service_name=service,
            tool_name=tool,
            arguments=arguments,
        )
        if payload.get("success") is False:
            code = payload.get("code") or "UNKNOWN"
            error = payload.get("error") or "unknown error"
            return ToolResult(success=False, error=f"Custom MCP failed [{code}]: {error}")

        output = payload.get("output")
        if isinstance(output, str) and output.strip():
            return ToolResult(output=output)
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return ToolResult(output=text)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            metadata={"service": service, "tool": tool},
        )


def _required_string(args: dict[str, Any], name: str) -> str | ToolResult:
    value = args.get(name)
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    value = value.strip()
    if not value:
        return ToolResult(success=False, error=f"{name} is required")
    return value
