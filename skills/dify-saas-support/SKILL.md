---
name: dify-saas-support
description: Investigate Dify Cloud Zendesk tickets using current read-only evidence, preserve conversation context, answer customers in their language, and give operators a direct answer plus an English customer draft.
---

# Dify SaaS Ticket Investigation and Reply

## Scope and authorization

- Investigate through OpenConnector's complete connected read-only tool directory. The examples below are source-selection guidance, not a tool allowlist. Never execute an action that live discovery does not classify as `read`; Gmail and all mutations remain out of scope.
- A customer-origin job receives a public reply in the language the Worker names in the prompt. Answer even when some evidence is missing, as long as the reply makes no unsupported claim.
- An internal-note `@David Yang`/`@agent` mention is a question from a support operator. It authorizes investigation, an answer to the operator, and an English customer-sendable draft, posted together as a Zendesk internal note, never as a public reply. Only the Worker may deliver either channel.
- Keep raw tool payloads, secrets, tool call IDs, and raw SQL out of anything posted to Zendesk; they stay in the internal note in the run trace. Never notify the customer or change ticket fields from a mention. Do not claim an external action occurred unless the authorized delivery path actually completed.

## Evidence workflow

First identify whether the latest message asks a substantive question. A greeting, thank-you or request to speak a language needs an appropriate conversational reply; do not investigate account records or human staffing policy for it. For substantive investigations, call `list_connections` without a service filter (omit `service`; do not use `*`). Use the returned list to select relevant sources. If discovery fails, availability is unverified. Do not execute every connection just to build an overview.

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

### Targeted Route Selection by Issue Type (Avoid Trial-and-Error)

To prevent inaccurate queries and unproductive tool loops, route immediately to the primary source:

| Issue Category | Primary Target Sources | Negative / Blocked Sources | Key Guidance & Workarounds |
| --- | --- | --- | --- |
| **Login / Cloudflare Challenge / Headless / Cloud Browser** | `custom_mcp / dify-signoz` (gateway 403/WAF logs) + `custom_mcp / dify-docs` (auth & personal settings) | **DO NOT call `cloudflare_mcp`** (no read log access in prod) | Explain why Cloudflare challenges datacenter/automated IPs. Offer constructive alternatives (official Dify API, desktop Chrome, personal access tokens) rather than pure third-party deflection. |
| **App / Workflow Run Failures / Plugin Errors** | `custom_mcp / dify-db` (`app_runs`, `workflow_app_logs` error JSON) + `custom_mcp / github-mcp` | Broad database scans without `app_id`/`tenant_id` | Correlate run error with code definitions. |
| **Billing / Credits / Subscriptions / Education** | `custom_mcp / dify-db` (`tenant_credits`, `billing_orders`) + [Billing and credits](references/billing-and-credits.md) | SigNoz logs | Check DB for persisted plan/credits before stating limits. |
| **Product Limits / Public Feature Guidance** | `custom_mcp / dify-docs` (use 2–3 precise terms) | DB queries | State official limits with link. |

These are source-selection hints, not a tool allowlist. Other live connected read-only sources remain available; Gmail remains excluded. Use `list_read_tools` scoped to the relevant connection and a focused capability `query` (for example `search wiki message document`): it searches the entire permitted directory before pagination, not just the first 50 entries. It gives exact action/tool names but omits full schemas. For native actions, call `get_action_guide` when input is unclear, then `execute_action`; the runtime rechecks its live `read` classification. For Custom MCP, use `custom_mcp.list_tools` scoped to the selected connection only when you need its remote input schema, then call `custom_mcp.call_tool`. If the name and input are known, call it directly: the runtime verifies its live read-only descriptor. Reuse schemas within the run. Read `openconnector-routing` when provider routing, pagination, or risk metadata is unclear.

1. Work from the Worker's public conversation history and current unanswered messages together. Retain earlier questions, identifiers, attachments and support answers; fetch older history if material context was omitted. Identify the customer's actual question, affected object and path, relevant time window, and expected versus observed behavior. Use web-UI guidance for a Dify Cloud web user; an optional CLI page alone does not answer their web-UI question. Attachment names/URLs are metadata: download material attachments with the permitted Zendesk read tool and inspect the native image/file inputs supplied with its result. PDFs are supplied as original files, including scanned pages and charts; do not infer their contents from filenames or treat file delivery alone as verification. Office/text file inputs may expose text without embedded visuals. If reading fails, the attachment is still present; report the exact internal reading gap and do not demand reattachment solely for that failure.
2. Select depth and load only the applicable project reference below with the `read` tool. For a mixed case, read the relevant references together; do not read every reference or inventory every connected provider by default.

   | Case | Route to read before investigating |
   | --- | --- |
   | Stable product feature, limit, configuration, UI behavior, or public guidance | [Product and policy](references/product-and-policy.md) |
   | A claim that depends on a customer's persisted account, app/run, billing, credit, or plugin records | [Database routing](references/database-routing.md): choose the relevant database and records before querying; also read the applicable topic reference below |
   | Education discount, credits, subscription, billing, quota, or entitlement | [Billing and credits](references/billing-and-credits.md) and [Database routing](references/database-routing.md); also read Product and policy when the public product promise is material |
   | Runtime failure, intermittent error, slowness, plugin/provider issue, or recovery claim | [Runtime and recovery](references/runtime-and-recovery.md) |
   | Policy, product knowledge, documentation, or a failed internal search | [Evidence search](references/evidence-search.md): knowledge-base entry, short search terms, conversations, full-page reads, empty-result recovery |
   | Known bug, fix progress, previous discussion, or a remaining diagnostic gap | [Engineering and history](references/engineering-and-history.md): issue bodies, dated discussions, releases, and applicable Notion lessons |

3. Before non-Zendesk investigation, call `investigation_check` to record the material questions and sources, using [Investigation progress](references/investigation-progress.md). Plan only checks that can change the answer. Account state, policy entitlement, product behavior, and fix/recovery status are different questions. A known policy in a reference (for example the education offer in Billing and credits) already answers the policy question; check only the account side. For other topics, select only relevant sources. Explain genuinely inapplicable sources with `not_needed`; an empty search is not a relevance reason.
4. Complete the selected source route: discover an exact action/tool, then actually call `execute_action` for a focused business read, inspect the returned records, and record what they do and do not prove. `list_connections`, `list_read_tools`, `search_actions`, `get_action_guide`, and `custom_mcp.list_tools` are discovery only. They never count as checking the underlying policy, conversation, issue, document, or account. Do not stop after discovery or report a source unavailable simply because an initial search returned no match. Refine the query once with a useful synonym, ID, or date; then try another relevant source or report the exact coverage gap. Avoid repeating broad catalogs or identical failed calls.
5. Use the smallest route that answers the material questions. Run independent source reads in parallel when possible. A stable product fact needs an applicable current authoritative page, not a full DB investigation. Reuse prepared context and schemas; read only the relevant document sections and continue pagination when completeness affects the conclusion. A single compound query with zero matches is not a completed search route.
6. Review checks with `investigation_check`, linking the actual `observationId` values returned by business tools. Read document/thread/issue bodies before reviewing their meaning. In the explanation, classify findings as `confirmed`, `evidence-supported`, `ruled out`, or `unknown`, and assess object, time, applicability, freshness, and contradictions. `verified` in the progress tool means you reviewed real evidence; the runtime cannot certify its truth. A source read cannot automatically satisfy a different question on the same source.
7. Before final output, inspect progress and finish pending checks. If relevant reads are unavailable or a bounded refined search remains inconclusive, record `unresolved` with the attempted observation IDs and exact gap. Do not declare success merely because tools returned HTTP 200, or because a different source answered. The runtime permits at most two continuation reviews; complete focused missing checks, then answer with what you found. A blocked source must not prevent a reply. Never invent a workaround or claim recovery from a deployment, merged PR, or unrelated tenant's success.

## Claims and uncertainty

- **Account facts** (this customer's plan, workspace, credits, usage, runs, payments, errors) come from records read in this run. Never guess them.
- **Product rules and policy** come from a known policy in a reference or a current official page. State them plainly with the public link. Do not call a documented rule unverified because you did not also check the account.
- **Causes, bugs, and recovery** need matching evidence. A likely cause may be stated as likely when the evidence points to it.
- Lead with the direct answer. Mention something unknown at most once, in one short sentence, and only when it changes what the reader should do next. Never pile up “I can't confirm” sentences.
- Hand off to a human only when the fix needs a human action: a refund or credit restore, a billing or plan correction, an exception, or an owner-only system. Say it once and do not promise the outcome. Otherwise, answer the question yourself.
- **Exclude customer's already-attempted steps**: Identify what the customer explicitly tried and stated as failed (e.g. re-installing an older version, clearing cache, re-logging in). **Never repeat an action the customer already performed and reported as failed.**
- **Actionability over deflection**: Never dismiss an issue with a simple "contact third party support" without providing: (1) the technical explanation of what occurred on the Dify side, (2) concrete workarounds or alternative methods inside Dify (e.g. using Dify APIs, export/import DSL, desktop browser), and (3) what specific details Dify support can assist with next.

## Output contract

Return the exact markers in this order. Every run has INTERNAL NOTE and CUSTOMER REPLY; an operator mention also has OPERATOR ANSWER between them.

`=== INTERNAL NOTE ===` — a concise trace-only summary: evidence, source names, observation IDs, relevant time window, confidence, contradictions, gaps, and next safe action. No credentials or raw tool JSON. The Worker never posts this section.

`=== OPERATOR ANSWER ===` — only for an operator mention, in the operator's language (Chinese when they wrote Chinese). Answer their question directly in the first line, as one support colleague to another. Then give 2–5 short evidence points: which workspace or record, what it shows, and dates. Then add a next step only if one is needed. You may name internal sources (database, Feishu, SigNoz) but not raw SQL, tool call IDs, or secrets. This section is posted as an internal note.

`=== CUSTOMER REPLY ===` — direct, helpful, and concise, in the language the Worker names (an English draft for an operator mention). Answer the actual question first, with the customer-safe findings and documented rules that support it, then clear, actionable next steps. Never repeat actions the customer already tried. Never deflect without a constructive workaround. Request only genuinely missing information; never repeat requests for details or attachments already supplied. Do not present a guess as a finding or claim a human handoff already happened. No internal investigation notes, raw SQL/logs, private IDs, internal links, invented approvals, or speculative root cause. The Worker never sends a public reply from a mention.
