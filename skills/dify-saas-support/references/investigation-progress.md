# Questions, Evidence, and Completion

Use `investigation_check` to keep important unanswered questions visible throughout the run. This is a local planning tool; it does not send anything or modify a ticket. The source map in the main Skill remains a navigation aid, not a demand to query every provider.

## Plan before independent investigation

Usually identify 1–3 material questions. Plan only the checks that can change the answer. Example: for education credits, the policy is already known (see Billing and credits), so check which workspace is on the promotion and its refresh history. A product question answered by one official page needs one document check. A known-bug question instead needs matching engineering records and, when claiming recovery, deployed/customer-path evidence.

```json
{"checks":[
  {"id":"account","question":"Which workspace matches this customer, and is it on the education promotion?","source":"dify-db","kind":"record","status":"pending"},
  {"id":"refresh","question":"What do its credit grants, usage, and monthly reload records show?","source":"dify-db","kind":"record","status":"pending"}
]}
```

`source` is the Custom MCP connection name (`dify-docs`, `github-mcp`, etc.) or native provider for its default connection (`feishu`, `linear`, `notion`). Use `provider/connectionName` for a non-default native connection. `kind` is `record`, `document`, or `discussion`. Prefer document/discussion when a search title is insufficient. IDs are short stable labels, not customer identifiers.

## Execute and review

Business tool responses include `observationId`, `source`, `action`, and the real data. Inspect the data, then upsert the same check with those IDs:

```json
{"checks":[{"id":"account","status":"verified","callIds":["<actual observationId from a record read>"],"explanation":"The requester's workspace has an active education promotion until 2027-03-01, so the known education credit policy applies."}]}
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

Finish relevant pending checks within the bounded continuation budget, then answer with what the evidence and documented rules support. The internal note stays in the run trace. An operator mention delivers the operator answer and the English customer draft as an internal Zendesk note. A remaining gap goes in one sentence, only when it changes what the reader should do next. Do not replace a partial investigation with a generic “contact support” message. The Worker controls delivery.
