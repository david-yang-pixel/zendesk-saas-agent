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
- In production, `cloudflare_mcp` does NOT expose read-only edge/firewall/challenge logs (`execute` is classified `write` and blocked). Never attempt to query Cloudflare security events or WAF logs via `cloudflare_mcp`.
- Never add a Zendesk internal note, update a ticket, create or comment on an issue, send a message, edit a document, refresh a repository, deploy, purge, or change external state.

## Discovery and execution

Start each investigation with one unfiltered `list_connections` call to establish the current connection-instance count, distinct services, and available connection names. Compare them with the known routes below; only the live result establishes what is configured now. A connection listing does not prove its business tools work. If the listing fails, report the discovery gap and use only sources that can be verified independently.

For a native provider:

1. Use `list_read_tools` to find relevant connected sources and read actions. Scope by `service` or `connectionName` and use its capability `query` filter (for example `search wiki message document`) to search across all permitted entries before pagination. Continue `nextOffset` with the same filters when needed; the first 50 actions are not the entire provider. All connected read actions remain available. Do not inventory unrelated providers on every ticket.
2. Use `search_actions` only when the scoped directory does not identify a suitable action; continue the action cursor when needed.
3. Call `get_action_guide` with the selected `actionId` and explicit `connectionName` when the live schema is unclear. The directory omits full schemas to keep model context small.
4. Call `execute_action` only for a live `read` action.

For Custom MCP:

1. Start from a scoped `list_read_tools` entry. If the remote input schema is unclear, call `execute_action` with `actionId=custom_mcp.list_tools`, the selected connection name, and empty input once; inspect only the selected tool.
2. Call `execute_action` with `actionId=custom_mcp.call_tool` and the exact selected remote tool name. The exact payload format is:
   ```json
   {
     "actionId": "custom_mcp.call_tool",
     "connectionName": "<connectionName>",
     "input": {
       "toolName": "<remote_tool_name>",
       "arguments": { "<param>": "<value>" }
     }
   }
   ```
   Notice: the key inside `input` is strictly `toolName` (never `tool` or `action`).

### High-frequency Custom MCP Tool Reference & Query Guidelines

To maximize accuracy and eliminate trial-and-error:

1. **`custom_mcp / dify-docs`**:
   - `official_docs_search`: `input: { "toolName": "official_docs_search", "arguments": { "query": "<focused technical terms>", "limit": 5 } }`
     - **Search formulation**: Use 2–3 precise technical keywords (e.g. `"Cloudflare human verification"`, `"OAuth callback"`, `"API trigger execution"`). Never submit long conversational sentences or entire ticket text.
   - `official_docs_read`: `input: { "toolName": "official_docs_read", "arguments": { "url": "https://docs.dify.ai/en/..." } }`

2. **`custom_mcp / dify-signoz`**:
   - `signoz_search_logs`: `input: { "toolName": "signoz_search_logs", "arguments": { "timeRange": "<focused window>", "searchText": "<query>", "limit": 20 } }`
     - **Time scoping**: When the customer reports an incident window (e.g. `2026-10-09 19:40-20:02 UTC`), target that specific window or narrow down to `1h`/`3h`. Never default to `1d` or `7d` for a specific incident time.
     - **Filter formulation**: Combine user identifier with technical error indicators (e.g. status code `403`, `429`, endpoint `/api/v1/auth`, `WAF`, `challenge`) instead of loose email-only searches that drown in routine notification logs.

3. **`custom_mcp / dify-db`**:
   - `db_gateway_query`: `input: { "toolName": "db_gateway_query", "arguments": { "database": "prod", "sql": "SELECT ... FROM ... WHERE tenant_id = '...' LIMIT 10;" } }`
     - Query indexed keys (`tenant_id`, `app_id`, `run_id`, `account_id`) directly. Never perform unindexed full-table scans.

4. **`custom_mcp / github-mcp`**:
   - `github_search_issues`: `input: { "toolName": "github_search_issues", "arguments": { "query": "repo:langgenius/dify <exact error message or keyword> is:issue" } }`
   - `github_code_search`: `input: { "toolName": "github_code_search", "arguments": { "query": "repo:langgenius/dify <symbol or error constant>" } }`

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
