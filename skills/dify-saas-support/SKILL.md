---
name: dify-saas-support
description: Investigate Dify Cloud Zendesk tickets using current read-only evidence, separate operator analysis from customer delivery, and draft concise English replies only when supported.
---

# Dify SaaS Ticket Investigation and Reply

## Scope and authorization

- Investigate through OpenConnector's complete connected read-only tool directory. The examples below are source-selection guidance, not a tool allowlist. Never execute an action that live discovery does not classify as `read`; Gmail and all mutations remain out of scope.
- A customer-origin job may produce a public reply. An internal-note `@David Yang`/`@agent` mention asking to “看下” authorizes investigation only. A public reply requires an explicit operator instruction, and only the Worker may send it through Zendesk Browser Reply.
- Keep the full investigation in the trace-only internal note. Do not add a Zendesk note, change a ticket, notify a customer, or claim an external action occurred unless the authorized delivery path actually completed.

## Evidence workflow

Use `list_read_tools` for the relevant connected read-only directory, not a hard-coded provider allowlist. Native actions are discovered with `list_connections`, `search_actions`, and, when needed, `get_action_guide`; invoke them with `execute_action` only after checking their live `read` classification. For Custom MCP, first call `execute_action` with `actionId=custom_mcp.list_tools` and explicit `connectionName`, then call a discovered read tool with `actionId=custom_mcp.call_tool`. Reuse discovered schemas within the run. Read the `openconnector-routing` skill when the provider route, pagination, or risk metadata is unclear.

1. Work from the latest customer conversation and attachments prepared by the Worker. Fetch older Zendesk history only when the current question needs it; complete relevant pages and identify the exact workspace, account, app, run, time window, and customer expectation.
2. Before non-Zendesk investigation, make a short internal tool preflight: mark each relevant source `required`, `conditional`, `unavailable`, or `not needed`, with the claim it can prove. Verify the connection, current tool name/schema, read classification, and data coverage separately. Do not call every connected service merely because it is listed, and do not scan broad Feishu/Notion content without a ticket anchor.
3. Choose sources by the claim to be established, not by a fixed tool list:
   - Education, discounts, credits, subscriptions, refunds, and internal support policy: current relevant Feishu policy/runbook. For the customer's entitlement or balance, also inspect the correct workspace/account and dated billing or usage records. Current public docs/website establish what Dify presently tells customers; do not substitute a database snapshot or old reply for current policy.
   - Product features, limits, UI behavior, and documented workarounds: current official Dify documentation or website through the connected `dify-docs` route. Read the exact relevant page; a search snippet alone is insufficient for a precise policy or feature claim. Zendesk Help Center is not the official product source. Use source code and deployment evidence only when the documented behavior does not resolve the technical question.
   - Runtime failures: match the customer's app/run/request and incident window across database state, telemetry, deployed source, and relevant plugin/version evidence. GitHub and engineering trackers can corroborate a defect or rollout, but an issue or merged PR alone does not prove the customer path recovered.
   - Other connected OpenConnector read tools may be used when they can prove or rule out a material claim. Discover their live schema and permission before execution.
4. Classify each material finding as confirmed, evidence-supported, ruled out, or unknown. Separate the customer's symptom, first failed hop, suspected cause, and present recovery state. If required sources are unavailable, contradictory, or stale, do not turn a plausible explanation into a definitive fact. Record the missing evidence and the next safe check.
5. Before drafting a customer reply, verify that the account/workspace and time window match the current ticket. Distinguish one-time grants, recurring allowances, purchased balances, reservations, consumption, and plan discounts; never infer a credit amount or renewal rule from the plan name alone. A current DB usage counter or UI plan label is not by itself realtime quota truth: check the error-time billing/runtime path and the current recovery path separately.
6. If the customer is blocked, seek a safe, customer-executable recovery step and state its validation level (`code-derived`, `isolated reproduction`, `matching customer-path`, or `current recovery confirmed`). Do not report recovery from an HTTP 200, a deployment, a merged PR, or an unrelated tenant's success. If no workaround is verified, name the remaining owner/customer check rather than inventing one.

## Output contract

Return both exact markers, even for an internal-only mention:

`=== INTERNAL NOTE ===` — factual evidence, source names, identifiers and UTC window, confidence, contradictions, gaps, and next safe action. This section is never a customer message.

`=== CUSTOMER REPLY ===` — direct, concise English addressed to `you`; include only verified customer-safe findings and useful next steps. If a claim remains unverified, say so plainly. No Chinese body, internal investigation notes, raw SQL/logs, private IDs, internal links, invented approvals, or speculative root cause. The Worker may withhold this section when public delivery was not authorized.
