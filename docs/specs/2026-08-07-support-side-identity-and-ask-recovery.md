# Spec: Support-Side Workspace Identity and Ask Recovery

## Objective

Stop the production Zendesk agent from asking customers for a workspace name or
Workspace ID when the target can already be resolved from current Zendesk form
identity and successful support-side lookups. A failed reply gate must lead to a
specific support-side recovery action unless the evidence proves that only the
customer can disambiguate the target.

This closes the recurring pattern observed in the archived runs for tickets
3802, 3821, and 3846: form identity evidence was available to Support, but the
reply path either treated a display label as a system ID or failed to use a
display label that uniquely identified one of the email's candidate workspaces.
The resulting blocker was converted into a repeated public identity question.

## Approved Assumptions

1. Form Email is the primary contact identity. Requester email is a fallback
   only when the form does not provide a usable identity email.
2. An email-shaped form Workspace ID is an explicit target identity and must be
   resolved separately from the contact email.
3. A canonical tenant UUID may disambiguate multiple memberships. A non-UUID
   Workspace ID form value may also disambiguate them only when its conservatively
   normalized display name uniquely and exactly matches one candidate returned
   by the same email-membership lookup. It never overrides a unique email
   membership, never triggers a global name lookup, and never permits fuzzy
   matching.
4. A verified unique email membership is sufficient to continue support-side
   investigation. The customer must not be asked to repeat its workspace name
   or system ID.
5. A customer identity question is permitted only after a successful lookup
   proves one of: multiple candidate workspaces without a matching canonical ID
   or unique exact candidate name, no membership for the supplied identity, or
   a canonical form ID that conflicts with the resolved candidates.
6. A missing, failed, or unverified support lookup is not customer-missing
   information. The agent must retry or hand off the internal lookup without
   asking the customer for data already available to Support.
7. Greeting and no-request templates remain available, but they never request
   workspace identity fields. Refund, privacy, freshness, owner, evidence, and
   human-approval gates remain fail closed.
8. Historical tickets are not automatically reprocessed by this change.

## Audited Failure Classes

1. **Display-label selection drift**: a permissive identifier parser treated a
   value such as a workspace display label as a tenant ID. Fixed in `a5dbbfb`.
2. **Ask-path bypass**: identity checks protected solution/high-risk handoff
   paths but not `reply_type=ask`, so a blocked handoff could become a public
   request for the same identity.
3. **Ask-template drift**: both generated-ask prompt paths and their fallback
   bullets allowed a conditional Workspace ID request even before support-side
   resolution.
4. **Category-strategy drift**: several active category prompts said to
   “collect Workspace ID” without first requiring resolution from form email.
5. **Evidence-call opacity**: successful results were stored with exact call IDs
   internally, but the tool-result content did not explicitly tell the model
   which value to copy into `evidence_ledger.source_call_ids`. An archived 3821
   handoff consequently invented a call ID and failed closed.

## Tech Stack

- Python 3.12+
- Existing `AgentSession`, tool loop, Browser Relay, and read-only MCP services
- pytest / pytest-asyncio
- Ruff and `uv build`

## Runtime Contract

### Workspace Identity Decision

For a model-authored ask that requests a workspace name or Workspace ID:

- no identity phrase in the ask: continue through existing gates;
- usable form/requester email but no successful tenant lookup: block with a
  support-action-required recovery instruction;
- unverified tenant lookup: block and require a support-side retry;
- unique tenant resolution: block because the target is already resolved;
- ambiguous resolution plus matching canonical form tenant UUID: block because
  the target is already resolved;
- ambiguous resolution plus one unique exact candidate-name match after Unicode
  normalization, whitespace collapse, and case folding: block because the target
  is already resolved;
- ambiguous resolution without either matching mechanism, or with duplicate
  normalized candidate names: allow the minimum customer clarification;
- malformed or inconsistent candidate-name metadata: block and require a
  support-side lookup retry rather than turning an internal evidence failure
  into a customer question;
- verified no-membership or conflicting canonical tenant UUID: allow the
  minimum customer clarification;
- no usable identity email at all: allow a minimum identity question.

The decision is enforced in code before any Browser Relay delivery. Prompt
wording is guidance only and cannot bypass it.

The Inspector tenants result exposes a machine-readable candidate list at the
start of the model-visible tool output and in structured tool-call metadata used
by the live gates. The structured copy prevents the 500-character tool-call
result preview from truncating a larger candidate set. The gate validates that
candidate tenant IDs exactly match the verified membership-ID set before using
any name. Missing legacy candidate metadata preserves the UUID-only fallback;
present but malformed metadata fails closed.

### Evidence Call IDs

Every successful tool result returned to the model includes an explicit stable
marker containing the exact provider call ID. The archived session log remains
unchanged and authoritative. Failed calls do not receive a success marker and
remain ineligible evidence.

When a target tenant is derived after the investigation plan was frozen, a
material row cites both the successful identity-resolution call and the
tenant-scoped fact call. This binds the row to a frozen input identity and to
the resolved target without weakening plan, time-window, or high-risk gates.

## Project Structure

- `src/zd_agent/runtime/billing_plan_gate.py`: shared form identity resolution
  and customer-ask decision
- `src/zd_agent/tools/zendesk_public_reply.py`: hard ask gate and model-facing
  recovery contract
- `src/zd_agent/runtime/agent_loop.py`: exact successful call-ID marker
- `src/zd_agent/prompts/public_reply/`: deterministic and generated ask policy
- `src/zd_agent/prompts/skills/` and `strategies/`: active investigation policy
- `tests/runtime/`, `tests/tools/`, and `tests/prompts/`: regression coverage

## Testing Strategy

1. Pure identity-policy tests for unique, ambiguous, none, missing, unverified,
   explicit-email target, unique exact candidate-name match, duplicate names,
   unknown labels, malformed metadata, and conflicting canonical-ID cases.
2. Public-reply tests proving unique resolution blocks workspace name/ID asks,
   true ambiguity permits a minimal ask, and unrelated missing information is
   unaffected.
3. Ask-generation tests proving conditional and unconditional Workspace ID
   requests are filtered and deterministic fallbacks contain no identity field.
4. Tool-loop tests proving successful results expose the exact call ID and
   failed results do not advertise themselves as evidence.
5. Prompt contract tests covering the always-on skill and representative
   category strategies.
6. Targeted tests, full suite, Ruff, package build, production health/revision
   readback, and a controlled no-send policy probe.

## Commands

- Targeted tests: `uv run pytest -q tests/runtime/test_billing_plan_gate.py tests/tools/test_zendesk_public_reply.py tests/prompts/test_public_reply_templates.py tests/runtime/test_tool_execution_order.py tests/prompts/test_support_guardrail_prompts.py`
- Full tests: `uv run pytest -q`
- Lint: `uv run ruff check src tests`
- Build: `uv build`

## Boundaries

- Always: use current Zendesk form/conversation facts; continue support-side
  read-only lookup before asking; bind tenant facts to the resolved tenant; keep
  public reply freshness and privacy gates.
- Ask first: new external dependency, DB/schema mutation, globally disabling
  replies, or changing refund/subscription policy.
- Never: guess among multiple workspaces, use a display label as a tenant UUID,
  fuzzy-match or globally search a workspace name to bypass email membership,
  cite a failed tool as evidence, weaken human approval gates, send a real
  customer reply as a deployment test, or automatically replay old tickets.

## Success Criteria

1. All four archived 3821-style workspace-name/ID ask bodies are rejected when
   the form email has a verified unique membership.
2. A missing or unverified inspector lookup returns an explicit support-side
   recovery instruction rather than authorizing a customer ask.
3. Ticket 3846's two-membership shape resolves to the candidate whose name is
   exactly `Dify's Workspace`, blocks another Workspace ID ask, and binds
   tenant-scoped evidence to that candidate.
4. A verified ambiguous/no-membership/conflicting-ID/duplicate-name case can
   still ask the customer for the minimum identity detail.
5. Neither generated ask path nor fallback content requests Workspace ID,
   workspace name, email, plan, or owner by default.
6. Active category prompts consistently say to resolve workspace identity from
   the form before asking the customer.
7. The model sees the exact stable ID of every successful evidence call and can
   use it verbatim in the structured ledger.
8. Targeted and full tests, Ruff, and build pass; production reports the expected
   revision with healthy Agent, Browser Relay, queue, and MCP services.

## Open Questions

None blocking. The user explicitly requested all confirmed deficiencies in this
failure family to be fixed and deployed. This spec does not broaden the agent's
authority or permit historical ticket reprocessing.
