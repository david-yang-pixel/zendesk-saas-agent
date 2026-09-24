# Questions, Evidence, and Completion

Use `investigation_check` to keep important unanswered questions visible throughout the run. This is a local planning tool; it does not send anything or modify a ticket. The source map in the main Skill remains a navigation aid, not a demand to query every provider.

## Plan before independent investigation

Usually identify 1–3 material questions. Each question may require multiple source checks. Example: education eligibility and credit refresh require separate policy, public-guidance, account, and refresh-history checks. A known-bug question instead needs matching engineering records and, when claiming recovery, deployed/customer-path evidence.

```json
{"checks":[
  {"id":"current-policy","question":"What does the current education offer grant?","source":"feishu","kind":"document","status":"pending"},
  {"id":"public-guidance","question":"What does current public guidance promise?","source":"dify-docs","kind":"document","status":"pending"},
  {"id":"account","question":"Which workspace and persisted entitlement match this customer?","source":"dify-db","kind":"record","status":"pending"}
]}
```

`source` is the Custom MCP connection name (`dify-docs`, `github-mcp`, etc.) or native provider for its default connection (`feishu`, `linear`, `notion`). Use `provider/connectionName` for a non-default native connection. `kind` is `record`, `document`, or `discussion`. Prefer document/discussion when a search title is insufficient. IDs are short stable labels, not customer identifiers.

## Execute and review

Business tool responses include `observationId`, `source`, `action`, and the real data. Inspect the data, then upsert the same check with those IDs:

```json
{"checks":[{"id":"current-policy","status":"verified","callIds":["<actual observationId from a body read>"],"explanation":"Confirmed from the dated policy body for this offer. The older promotion described in ticket history does not establish the current benefit."}]}
```

Omitted checks remain in the plan. Update existing check IDs rather than creating replacement checks and leaving the original questions pending. The equivalent source forms `feishu/default` and `custom_mcp/dify-docs` are accepted and normalized to `feishu` and `dify-docs`; non-default native connections remain distinct. `verified` means a matching real read was reviewed, not that the runtime proved the claim. Explain applicability and any limitations. Keep facts, inference, missing evidence, and contradictions separate. A DB schema lookup or another customer's record does not establish this customer's entitlement.

`investigation_check` with `checks: []` shows progress:

- `not_queried`: execute a business read; catalogs do not count.
- `search_empty`: shorten/refine the query or try a relevant search surface/known entry. Repeating the same query is not refinement.
- `candidate_unread`: read the candidate document, issue, or conversation body and required continuation.
- `failed`: diagnose permissions/input/availability or name the exact inaccessible hop.
- `unreviewed`: connect actual results to this question, object, time, and version.
- `verified`: reviewed evidence; qualify inference and contradictions in the answer.
- `unresolved`: attempts completed but the needed fact is unverified. Name the next customer/owner check.
- `not_needed`: an explicit relevance decision, never a substitute for a failed search.

For `unresolved`, link an actual access/availability failure, a body read that does not establish the claim, or at least two distinct inconclusive attempts. Correct an invalid-input/schema error before giving up; one malformed query does not make the source unavailable. Do not fabricate IDs or use a result from another source. After finding unrelated candidates, inspect them and refine the query; do not label a promising unread result unavailable.

## Final output

Finish relevant pending checks within the bounded continuation budget. When evidence remains missing, still provide the required internal summary and customer-sendable English draft. Include verified useful findings, the precise unresolved point, and the minimum next step. Do not replace a partial investigation with a generic “contact support” message. The Worker controls delivery.
