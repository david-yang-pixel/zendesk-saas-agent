# Spec: Evidence-Grounded Support Investigation Controller

## Objective

Turn the locally proven Dify support workflow into deterministic runtime
contracts for the production Zendesk agent. The agent must resolve the current
customer question and identity, choose an investigation route, plan the real
execution chain, gather only relevant live evidence, reconcile contradictions,
and pass a fail-closed send gate before a customer-visible reply.

This change addresses the observed failure modes where the agent exposes every
tool on every round, dispatches subagents before intake is complete, repeats
evidence work, cites only a tool name instead of the exact successful call, or
reaches the reply gate without a coherent investigation plan.

## Approved Assumptions

1. Zendesk `conversation_log` remains the current-ticket source of truth.
2. The form email is the primary account-to-workspace identity key; requester
   email is a fallback only when the form email is absent.
3. Billing subscription facts come from tenant-scoped Billing evidence.
4. Existing remote MCP services and their read-only boundaries remain unchanged.
5. Public replies remain enabled, but `solution` replies fail closed unless the
   investigation and evidence contracts pass. Safe `ask` and `handoff` replies
   remain available when the cause is unknown.
6. Historical tickets and lessons may choose a next check but cannot prove a
   current claim.
7. No new third-party dependency or database schema is required.

## Tech Stack

- Python 3.12+
- Pydantic 2
- pytest / pytest-asyncio
- Existing `ToolRegistry`, `AgentSession`, provider-neutral tool loop, and
  Zendesk Browser Relay delivery boundary

## Commands

- Targeted tests: `uv run pytest -q tests/runtime/test_investigation_policy.py tests/runtime/test_reply_evidence.py tests/runtime/test_tool_execution_order.py`
- Full tests: `uv run pytest -q`
- Lint: `uv run ruff check src tests`
- Build: `uv build`
- Dry-run replay: `uv run zd-agent run <ticket-id> --dry-run`

## Project Structure

- `src/zd_agent/runtime/investigation.py`: investigation phases, structured plan,
  route/category tool policy, and execution enforcement
- `src/zd_agent/session/agent_session.py`: durable per-session investigation state
- `src/zd_agent/tools/investigation.py`: model-facing structured planning tool
- `src/zd_agent/runtime/agent_loop.py`: per-round schema filtering and execution gate
- `src/zd_agent/runtime/reply_evidence.py`: exact successful-call evidence binding
- `src/zd_agent/tools/zendesk_public_reply.py`: model schema and final send gate
- `tests/runtime/`: unit and loop-level contract tests
- `config.yaml`: persistent archive path under the mounted resources directory

## Runtime Contract

### Phases

1. `intake`: only current-ticket intake capabilities are exposed. A successful
   `zendesk_get_ticket` is required before classification.
2. `classify`: classification and attachment intake are available. A category is
   required before planning.
3. `plan`: the agent calls `plan_investigation` with the customer question,
   environment, route, identifiers, UTC window, execution chain, evidence lanes,
   and unresolved facts.
4. `investigate`: only the common safe tools plus the tools appropriate to the
   selected category and route are exposed. The execution boundary independently
   rejects a tool that was not allowed, even if a provider emits it.
5. `reply`: `zendesk_public_reply` remains the terminal external write. A
   `solution` must match the stored plan route and bind every material ledger row
   to exact successful evidence call IDs. A non-causal `ask` or `handoff` may
   proceed without pretending the root cause is known.

### Route Rules

- `fast`: current authoritative docs/product facts; no subagents.
- `standard`: one deterministic object/path; direct evidence tools first;
  subagents are permitted only for explicitly independent lanes.
- `deep`: intermittent, cross-component, regression, latency, or platform-scope
  investigation; requires explicit impact/baseline lanes before subagents.
- `high_risk`: billing, refund, entitlement, identity, permissions, security, or
  admin decisions; read-only evidence may be gathered, but business decisions and
  mutations remain outside the automated solution path.

### Tool Policy

- Always available after intake where appropriate: current Zendesk reads,
  attachment download, official docs, and the terminal reply tool.
- Category tools are allowlisted (for example, Billing gets identity/Billing DB
  tools; runtime incidents get SigNoz; suspected defects get aligned Source and
  GitHub).
- `run_subagent` is not exposed. `run_subagents_parallel` is exposed only after a
  `standard` or `deep` plan contains at least two genuinely independent evidence
  lanes; `fast` and `high_risk` never delegate live decisions.
- Sandbox write/exec, branch switching, Feishu push, historical mirrors, and
  unrelated broad tools are not part of the default investigation surface.

### Evidence Contract

Each successful tool call receives a stable `call_id` in the session log. Tool
metadata from parallel subagents is retained. Every material evidence row must
cite one or more exact `source_call_ids`; the cited calls must exist, have
succeeded, contain a non-empty result, and not be historical/control/write tools.
The legacy `source_tools` list remains for readable audit output but cannot by
itself satisfy a material claim.

For a model-authored `solution`:

- ledger route must equal the stored investigation route;
- standard/deep technical diagnoses require a confirmed failing boundary and a
  confirmed root cause;
- deep diagnoses also require a confirmed current-state row and plan lanes for
  impact scope and baseline comparison;
- high-risk replies may state confirmed technical facts but cannot make a
  business decision;
- current plan/subscription claims must additionally pass the existing form
  email -> workspace -> tenant-scoped Billing gate.

## Code Style

Use small pure policy functions and explicit string enums/constants. Return a
reason with every denial so the model can recover safely.

```python
decision = evaluate_tool_call(session=session, tool_name=tool_name)
if not decision.allowed:
    return ToolResult(success=False, error=decision.reason)
```

Avoid hidden mutations, provider-specific branching, and prompt-only security or
quality rules.

## Testing Strategy

1. Unit tests for phase inference, route validation, category allowlists, and
   subagent eligibility.
2. RED/GREEN regression tests proving an unplanned or disallowed call is rejected
   at execution time.
3. Evidence tests proving a tool name alone no longer satisfies a material row,
   exact successful call IDs do, failed/historical calls do not, and subagent
   metadata survives into the parent evidence log.
4. Send-gate tests for route mismatch and standard/deep causal requirements.
5. Full existing suite, Ruff, and package build.
6. Production deployment health/config readback followed by a controlled dry-run
   replay. No real customer reply is used as a deployment test.

## Boundaries

- Always: refresh current Zendesk state; preserve remote MCP read-only boundaries;
  fail closed for unsupported solution claims; retain an auditable run artifact.
- Ask first: new external dependency, database/schema mutation, disabling public
  replies globally, or changing customer-facing product policy.
- Never: use failed tools or historical lessons as current proof; infer a
  workspace when email mapping is ambiguous; let a subagent perform a write;
  expose internal paths/secrets in a public reply; test deployment by sending a
  real customer message.

## Success Criteria

1. Before current-ticket intake, the model sees only the intake tool surface.
2. Before a valid structured plan, evidence and subagent calls are blocked.
3. Each normal round exposes materially fewer tools than the current global set;
   tests assert representative category/route surfaces.
4. `run_subagents_parallel` is unavailable for fast/high-risk routes and before
   independent lanes exist.
5. A material ledger row citing only `source_tools` is rejected; exact successful
   `source_call_ids` are required.
6. Parent sessions retain nested subagent evidence metadata and can cite those
   exact calls without re-running the same source.
7. A standard/deep solution cannot send without the required confirmed causal
   rows and a plan-route match.
8. Billing plan claims still require form-email workspace resolution and
   tenant-scoped Billing evidence.
9. Run archives are written below `/opt/zd-agent/resources/agent-runs`, which is a
   persisted production mount.
10. Targeted tests, the full suite, Ruff, and package build pass; the deployed
    container reports healthy and the expected revision/config.

## Implementation Tasks

- [ ] Add the investigation state and policy module.
  - Acceptance: phases/routes/tool allowlists are pure and unit-tested.
  - Verify: `tests/runtime/test_investigation_policy.py`.
- [ ] Add and register `plan_investigation`; filter schemas and enforce calls.
  - Acceptance: provider-visible and execution-time policy agree.
  - Verify: policy and tool-loop tests.
- [ ] Bind ledger rows to exact successful tool calls and retain nested metadata.
  - Acceptance: legacy name-only evidence fails; valid call IDs pass.
  - Verify: reply-evidence and tool-execution tests.
- [ ] Tighten route-specific solution gates and prompt contract.
  - Acceptance: route mismatch and incomplete standard/deep diagnosis fail closed.
  - Verify: reply/prompt tests.
- [ ] Persist archives, run all verification, and deploy safely.
  - Acceptance: mounted archive path, clean tests/build, healthy expected revision.
  - Verify: archive test, production health/config readback, dry-run replay.

## Open Questions

None blocking. Full autonomous `solution` sending can later be tightened further
with an operator-review mode, but this change keeps the current behavior and adds
fail-closed evidence contracts first.
