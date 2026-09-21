---
name: openconnector-routing
description: Route Zendesk Agent investigations through OpenConnector with live connection and action discovery, dynamic Custom MCP risk checks, complete reads, and a hard no-write boundary; excludes Gmail and delivery.
---

# OpenConnector Investigation Routing

OpenConnector is the only investigation gateway. The Agent receives four controls: `list_connections`, `search_actions`, `get_action_guide`, and `execute_action`. `list_apps` is intentionally not exposed.

## Hard boundaries

- Investigation is read-only. Never send `confirmOperation`, and never call a tool or action whose live `operationType` is not `read`.
- Gmail is out of scope. Do not list its connection, search its actions, read mail, or mention it as an investigation option.
- Public replies are not OpenConnector actions. The Worker sends the final customer reply through Zendesk Browser Reply.
- Never add a Zendesk internal note, update a ticket, create or comment on an issue, send a message, edit a document, refresh a repository, deploy, purge, or change external state.

## Discovery and execution

For a native provider:

1. Call `list_connections` with an explicit `service` filter.
2. Call `search_actions` with the same explicit service and continue its cursor when needed.
3. Call `get_action_guide` with the selected `actionId` and explicit `connectionName` when the live schema is unclear.
4. Call `execute_action` only for a live `read` action.

For Custom MCP:

1. Call `execute_action` with `actionId=custom_mcp.list_tools`, an explicit connection name, and empty input.
2. Read the returned tool name, schema, `operationType`, confirmation requirement, and pagination contract.
3. Call `execute_action` with `actionId=custom_mcp.call_tool` only when the selected remote tool is `read` and does not require confirmation.

## Evidence routes

| Evidence | Provider / connection |
| --- | --- |
| Zendesk ticket, latest conversation, requester, attachments | `custom_mcp / zendesk` |
| Dify official docs and public website | `custom_mcp / dify-docs` |
| Dify SaaS persisted state | `custom_mcp / dify-db` |
| Logs, traces, metrics, queues | `custom_mcp / dify-signoz` |
| Deployed source and runtime refs | `custom_mcp / dify-source` |
| GitHub Issue, PR, and code evidence | `custom_mcp / github-mcp` |
| Internal knowledge and discussions | `feishu / default` |
| Engineering issue status | `linear / default` |
| Sanitized diagnostic experience | `notion / default` |
| Edge/API evidence when relevant | `cloudflare_mcp / default` |

The verified GitHub semantic reads are `github_search_prs`, `github_get_pr`, `github_search_issues`, `github_get_issue`, and `github_code_search`. They must be configured as `read` through the GitHub Custom MCP connection's trusted `toolOperationTypes`. If live discovery reports `write`, stop that route and report the configuration gap; do not bypass it with confirmation.

## Complete reads

Follow returned pagination until `hasMore` is false when completeness matters. For Custom MCP inspect `structuredContent["io.oomol.open-connector/pagination"]`; continue from `nextCursor` after `subrequest_limit`. Preserve previous unique records. Record any deliberate bound or interruption in the trace-only internal section.
