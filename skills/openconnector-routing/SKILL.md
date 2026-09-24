---
name: openconnector-routing
description: Route Zendesk Agent investigations through OpenConnector with live connection and action discovery, dynamic Custom MCP risk checks, complete reads, and a hard no-write boundary; excludes Gmail and delivery.
---

# OpenConnector Investigation Routing

OpenConnector is the only investigation gateway. The Agent receives `list_read_tools`, `list_connections`, `search_actions`, `get_action_guide`, and `execute_action`. `list_read_tools` is a paginated directory of currently connected read-only actions; its entries are discovery candidates, not proof that execution succeeded. `list_apps` is intentionally not exposed because it includes unconnected catalog providers.

## Hard boundaries

- Investigation is read-only. Never send `confirmOperation`, and never call a tool or action whose live `operationType` is not `read`.
- Gmail is out of scope. Do not list its connection, search its actions, read mail, or mention it as an investigation option.
- Public replies are not OpenConnector actions. The Worker sends the final customer reply through Zendesk Browser Reply.
- Never add a Zendesk internal note, update a ticket, create or comment on an issue, send a message, edit a document, refresh a repository, deploy, purge, or change external state.

## Discovery and execution

Start each investigation with one unfiltered `list_connections` call to establish the current connection-instance count, distinct services, and available connection names. Compare them with the known routes below; only the live result establishes what is configured now. A connection listing does not prove its business tools work. If the listing fails, report the discovery gap and use only sources that can be verified independently.

For a native provider:

1. Use `list_read_tools` to find relevant connected sources and read actions. Scope by `service` or `connectionName` when known; use an unscoped tool inventory only if the evidence source is genuinely unknown. Continue `nextOffset` when needed. All connected read actions may be used, not just the examples below; do not inventory every provider's tools on every ticket.
2. Use `search_actions` only when the scoped directory does not identify a suitable action; continue the action cursor when needed.
3. Call `get_action_guide` with the selected `actionId` and explicit `connectionName` when the live schema is unclear. The directory omits full schemas to keep model context small.
4. Call `execute_action` only for a live `read` action.

For Custom MCP:

1. Start from a scoped `list_read_tools` entry. If the remote input schema is unclear, call `execute_action` with `actionId=custom_mcp.list_tools`, the selected connection name, and empty input once; inspect only the selected tool.
2. Call `execute_action` with `actionId=custom_mcp.call_tool` and the exact selected remote tool name. The runtime re-fetches and verifies its live `read` classification and confirmation policy before allowing the call, even if the directory looked safe.

Discovery is not execution. After choosing a source, use its actual read action, inspect the business result, and record the exact coverage or error. A successful `list_connections`, `search_actions`, `get_action_guide`, or `custom_mcp.list_tools` call proves only tool availability. If a focused business search has no match, refine its query once or check a second relevant source before declaring evidence unavailable. Do not run every connected tool merely to satisfy this step.

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

GitHub tools, including `github_search_prs`, `github_get_pr`, `github_search_issues`, `github_get_issue`, and `github_code_search`, are examples rather than a fixed allowlist. Every selected remote tool must be classified as `read` by live discovery. If live discovery reports `write` or unclassified, stop that route and report the configuration gap; do not bypass it with confirmation.

## Complete reads

Follow returned pagination until `hasMore` is false when completeness matters. For Custom MCP inspect `structuredContent["io.oomol.open-connector/pagination"]`; continue from `nextCursor` after `subrequest_limit`. Preserve previous unique records. Record any deliberate bound or interruption in the trace-only internal section.
