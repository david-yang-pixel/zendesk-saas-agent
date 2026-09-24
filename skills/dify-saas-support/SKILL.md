---
name: dify-saas-support
description: Investigate Dify Cloud Zendesk tickets using current read-only evidence, separate operator analysis from customer delivery, and draft concise English replies that qualify unresolved evidence gaps.
---

# Dify SaaS Ticket Investigation and Reply

## Scope and authorization

- Investigate through OpenConnector's complete connected read-only tool directory. The examples below are source-selection guidance, not a tool allowlist. Never execute an action that live discovery does not classify as `read`; Gmail and all mutations remain out of scope.
- A customer-origin job should receive a public reply even when evidence is incomplete, as long as the reply makes no unsupported claim: identify what remains unverified and what the customer can provide or a human support engineer must check. An internal-note `@David Yang`/`@agent` mention authorizes investigation and a customer-sendable English reply draft posted as a Zendesk internal note, never a public reply. Only the Worker may deliver either channel.
- Keep raw tool payloads, secrets, and customer-sensitive data out of the draft. For operator mentions, provide a brief evidence summary and a separate sendable English draft in the internal note; never notify the customer or change ticket fields from a mention. Do not claim an external action occurred unless the authorized delivery path actually completed.

## Evidence workflow

First call `list_connections` without a service filter. Count the returned connection instances and distinct services, and use that live list to decide which sources actually exist in this run. The map below explains known routes; it is not a promise that every connection is still configured or healthy. If live discovery fails, say that availability is unverified rather than assuming the map is current. Do not execute every connection just to build the overview.

| Known type and connection | What it is useful for |
| --- | --- |
| Custom MCP `zendesk` | Ticket metadata, requester, conversation history, comments, and attachments; establish what the customer actually reported. |
| Custom MCP `dify-docs` | Current public Dify documentation and product guidance; check published feature behavior and promises. |
| Custom MCP `dify-db` | Read-only SaaS `prod`/`billing` records; check a specific workspace's persisted subscription, credits, usage, or configuration, not live runtime health by itself. |
| Custom MCP `dify-signoz` | Time-bounded logs, traces, and metrics; locate a runtime failure or verify the affected request path. |
| Custom MCP `dify-source` | Dify source and deployed reference information; inspect implementation or version-specific behavior, not proof that a customer request succeeded. |
| Custom MCP `github-mcp` | GitHub code, issues, and PRs; corroborate implementation, fixes, and known defects. |
| Native `feishu` | Internal SaaS knowledge, dated policies, and discussions; especially useful for education, promotion, and support policy questions. |
| Native `linear` | Engineering issue state and ownership; track a known defect or fix, not customer recovery by itself. |
| Native `notion` | Internal diagnostic notes and sanitized prior experience; use as a lead, not as current account or policy proof. |
| Native `cloudflare_mcp` | Cloudflare edge/Worker/API evidence when the customer path involves those services. |

These are source-selection hints, not a tool allowlist. Other live connected read-only sources remain available; Gmail remains excluded. Use `list_read_tools` scoped to the relevant connection: it gives the exact action/tool name but intentionally omits full schemas to keep discovery small. For native actions, call `get_action_guide` for the selected action when input is unclear, then `execute_action`; the runtime rechecks its live `read` classification. For Custom MCP, use `custom_mcp.list_tools` scoped to the selected connection only when you need its remote input schema, then call `custom_mcp.call_tool` with the selected name. If the exact name and input are already known, call `custom_mcp.call_tool` directly: the runtime fetches and verifies its live read-only descriptor before execution. Reuse discovered schemas within the run. Read the `openconnector-routing` skill when the provider route, pagination, or risk metadata is unclear.

1. Work from the latest customer conversation and attachments prepared by the Worker. Fetch older Zendesk history only when this question needs it. Identify the customer's actual question, affected object and path, relevant time window, and expected versus observed behavior.
2. Select depth and load only the applicable project reference below with the `read` tool. For a mixed case, read the relevant references together; do not read every reference or inventory every connected provider by default.

   | Case | Route to read before investigating |
   | --- | --- |
   | Stable product feature, limit, configuration, UI behavior, or public guidance | [Product and policy](references/product-and-policy.md) |
   | Education discount, credits, subscription, billing, quota, or entitlement | [Billing and credits](references/billing-and-credits.md); also read Product and policy when the public product promise is material |
   | Runtime failure, intermittent error, slowness, plugin/provider issue, or recovery claim | [Runtime and recovery](references/runtime-and-recovery.md) |

3. Before non-Zendesk investigation, make a short internal evidence plan: identify the 1–3 material claims in the question, choose the source capable of proving each, and mark it `required`, `conditional`, `unavailable`, or `not needed`. The table above is a routing hint, not a fixed set of required sources. A bug-status question may need Linear/GitHub/source; policy may need Feishu and public docs; a workspace-specific credit question may also need DB. Verify connection, current tool name/schema, read classification, and data coverage separately. Do not scan broad Feishu/Notion content without a ticket anchor.
4. Complete the selected source route: discover an exact action/tool, then actually call `execute_action` for a focused business read, inspect the returned records, and record what they do and do not prove. `list_connections`, `list_read_tools`, `search_actions`, `get_action_guide`, and `custom_mcp.list_tools` are discovery only. They never count as checking the underlying policy, conversation, issue, document, or account. Do not stop after discovery or report a source unavailable simply because an initial search returned no match. Refine the query once with a useful synonym, ID, or date; then try another relevant source or report the exact coverage gap. Avoid repeating broad catalogs or identical failed calls.
5. Use the smallest evidence route that can answer the question. A stable, non-account-specific product fact needs an applicable current authoritative page, not a full DB and telemetry investigation. Account-specific, causal, or recovery claims need matching object, path, version, and time evidence. Reuse already prepared context and discovered schemas; continue pagination only when completeness affects the conclusion.
6. Classify each material finding as `confirmed`, `evidence-supported`, `ruled out`, or `unknown`. Separate symptom, first failed hop, cause, and present state. Before writing a customer-facing claim, check its source, applicability, freshness, contradictions, and whether a real business read succeeded. Never turn an unavailable, stale, or contradictory source into a confident answer just to finish quickly.
7. Stop once the necessary claims and current state are supported, or name the precise missing customer/owner check. For a blocked customer, prefer a safe, customer-executable next step with its validation level; never invent a workaround or claim recovery from a deployment, HTTP 200, or unrelated tenant's success.

## Output contract

Return both exact markers, even for an internal-only mention:

`=== INTERNAL NOTE ===` — a concise operator summary of factual evidence, source names and relevant time window, confidence, contradictions, gaps, and next safe action. Do not paste raw SQL, tool JSON, credentials, or customer-sensitive identifiers. For operator mentions this summary is included in the private note above the customer-sendable draft; it is not itself a customer message.

`=== CUSTOMER REPLY ===` — direct, concise English addressed to `you`; include only verified customer-safe findings and useful next steps. If evidence is incomplete, still write a usable customer reply: specify what could not be verified and request only the missing customer detail or explain the human-support check needed. Do not present a guess as a finding or claim a human handoff already happened. No Chinese body, internal investigation notes, raw SQL/logs, private IDs, internal links, invented approvals, or speculative root cause. The Worker posts this as an internal customer-sendable draft for operator mentions and never sends it publicly from a mention.
