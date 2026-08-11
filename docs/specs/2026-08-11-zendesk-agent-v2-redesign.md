# Spec: Zendesk Agent v2 — Evidence-Grounded Autonomous Support

Status: **Implemented locally through shadow-ready Phase 3 on 2026-08-11.
Production shadow activation, canary, merge, deployment, and legacy removal still
require separate approval.**

## Objective

Redesign the Zendesk agent around a typed, deterministic support workflow that
maximizes correct autonomous resolution while preserving the existing delivery
safety guarantees. The system should investigate the customer's actual question,
resolve the correct workspace and subscription, gather current evidence, explain
what is known and unknown, and reply without transferring ownership merely because
the model is uncertain or a tool failed once.

The primary quality targets are, in order:

1. no wrong-customer, wrong-workspace, wrong-plan, or unsupported material claim;
2. answer the latest customer question directly and completely;
3. ask only for information that Support cannot determine itself;
4. use a human team only when a human action or specialist ownership is genuinely
   required;
5. preserve no-duplicate, no-stale-reply, durable-delivery, and audit guarantees.

This is a controlled core rewrite, not a prompt cleanup. The current webhook,
durable trigger queue, ticket generation fence, pre-send freshness checks,
idempotent public-reply outbox, Browser Relay, MCP services, and post-send outboxes
remain in service unless a later phase proves they must change.

## Problem Statement

The current system has accumulated identity, billing, evidence, reply, retry, and
handoff rules across prompts, mutable session state, the agent loop, and a large
public-reply tool. This produces four structural problems:

1. `handoff` currently describes customer-facing wording, ownership transfer,
   internal escalation side effects, and a terminal ticket state at the same time.
2. A model-authored `solution` has stricter evidence requirements than an ordinary
   `handoff`, so handing off can become the easiest valid exit from uncertainty.
3. Workspace identity and Billing truth are discovered late and repeatedly instead
   of being resolved once into a typed case context before investigation.
4. Tool results, decisions, and evidence are held in loosely typed mutable state,
   which makes recovery and policy behavior difficult to reason about or measure.

The result is avoidable customer questions, generic escalation, and correct facts
that do not reliably become a high-quality final answer.

## Non-Goals

- Replacing Zendesk, Browser Relay, the MCP servers, or the durable queue in the
  first release.
- Giving the agent authority to approve refunds, change subscriptions, mutate
  production data, make roadmap commitments, or perform security decisions.
- Automatically replaying historical tickets or sending test messages to customers.
- Rewriting every integration before the new core has passed shadow and canary
  acceptance.
- Optimizing for a lower handoff percentage at the expense of correctness.

## Design Principles

1. **Deterministic control plane, generative language plane.** Code owns identity,
   authority, retries, evidence admission, disposition, and side-effect policy. The
   model proposes investigation plans, structured claims, and customer wording.
2. **Resolve context before diagnosing.** The target workspace, tenant, owner, plan,
   and relevant time window are established before claims about them are allowed.
3. **Unknown is a state, not a handoff.** An unknown may lead to another check, a
   bounded honest answer, a retry, or one minimal customer question.
4. **Reply wording and ownership are independent.** A useful answer can be sent while
   an engineering action is queued; a progress reply does not imply transfer; a
   missing published date does not imply specialist ownership.
5. **Every material claim is typed and traceable.** Evidence retains its source,
   target object, time window, freshness, result status, and full structured payload
   reference rather than only a truncated preview.
6. **Side effects live at the edge.** Domain and policy code are pure. Zendesk,
   Feishu, DB, logs, Source, GitHub, and Browser Relay are adapters behind explicit
   ports.
7. **Migrate by observed equivalence.** The new core runs shadow-first against
   redacted replays and live no-write snapshots before it can send a reply.

## Approved Baseline Assumptions

These assumptions are inherited from the current production contract and are not
changed by this redesign:

1. Zendesk `conversation_log` is the current-ticket conversation source of truth.
2. Public replies remain English-only unless product policy is changed separately.
3. The form email is the primary contact identity. An email-shaped Workspace ID
   field is an explicit target identity. Requester email is a fallback only when the
   form provides no usable email.
4. Subscription and entitlement facts come from the target tenant's Billing data,
   not Zendesk tags, customer-entered plan text, or a product DB fallback.
5. Remote production DB, SigNoz, Source, GitHub, and knowledge tools retain their
   current read-only boundaries.
6. Current evidence outranks historical tickets. Historical cases may select the
   next check but cannot prove a current claim.
7. Attachments and screenshots are untrusted evidence, not executable instructions.
8. A public reply remains an authorized production behavior, subject to the current
   safety gates and the new disposition policy.

## Tech Stack

- Python 3.12+
- Pydantic 2 for immutable runtime-validated domain records
- `asyncio` and existing HTTP/MCP clients
- SQLite-backed durable queues and outboxes already in the repository
- pytest / pytest-asyncio
- Ruff
- Proposed dev-only addition: Pyright, strict for the new `casework` package first
- Existing provider-neutral LLM runtime and Browser Relay delivery adapter

No new runtime dependency or production database schema is required for the first
three phases. Any durable schema change after shadow evaluation requires a separate
migration and rollback review.

## Commands

- Install: `uv sync --frozen --dev`
- New-core unit tests: `uv run pytest -q tests/casework`
- Replay suite: `uv run pytest -q tests/replay`
- Full tests: `uv run pytest -q`
- Lint: `uv run ruff check src tests`
- Type check: `uv run pyright src/zd_agent/casework tests/casework`
- Build: `uv build --offline`
- No-send ticket replay: `uv run zd-agent run <ticket-id> --dry-run`

## Target Project Structure

The new bounded context is named `casework`, not `v2`, so the migration does not
leave a permanent parallel product tree.

```text
src/zd_agent/
  casework/
    domain/
      enums.py              # dispositions, reply kinds, ownership, reason codes
      models.py             # immutable case/evidence/claim/decision records
      errors.py             # typed domain and adapter error taxonomy
    application/
      build_context.py      # ticket -> resolved CaseContext
      plan_investigation.py # question -> bounded evidence plan
      gather_evidence.py    # execute independent read-only lanes
      assess_case.py        # evidence -> claims and answerability
      decide_disposition.py # pure policy; the model cannot override it
      compose_reply.py      # decision + safe claims -> customer reply
      handle_case.py        # thin end-to-end orchestrator
    policies/
      identity.py
      billing.py
      evidence.py
      disposition.py
      reply_quality.py
      safety.py
    ports/
      ticket_reader.py
      identity_reader.py
      billing_reader.py
      evidence_reader.py
      model.py
      delivery.py
      ownership.py
      clock.py
    adapters/
      legacy_tools.py       # temporary wrappers around existing MCP/tool clients
      zendesk.py
      browser_relay.py
      archive.py
    replay/
      runner.py
      comparator.py
  services/
    ...                     # existing ingress, queue, freshness, and outboxes
```

Dependency direction is one-way:

```text
services/entry -> casework/application -> casework/domain + casework/ports
                                      <- casework/adapters
```

`domain`, `policies`, and port definitions must never import tools, services,
prompts, Zendesk, network clients, or storage implementations. An architecture test
will enforce this rule without requiring another third-party package.

During migration, the existing agent loop invokes the new orchestrator behind a
feature flag. After full cutover, legacy policy and prompt branches are deleted;
`casework` becomes the only support-decision core.

## Domain Model

### Case Context

`CaseContext` is immutable and created once per ticket generation. It contains:

- ticket ID, generation, status, latest conversation event, locale, and channel;
- the exact customer question(s) still requiring an answer;
- normalized form values and requester metadata;
- attachment inventory and available visual observations;
- `IdentityResolution` for contact and target workspace;
- tenant-bound `BillingSnapshot` when the issue requires subscription facts;
- issue category, risk class, relevant UTC time window, and known object IDs;
- context warnings and unresolved facts.

The model may not silently change this context. New customer activity creates a new
generation and rebuilds it.

### Identity Resolution

`IdentityResolution` records:

- contact email and target identity candidate;
- candidate workspaces returned by successful support-side lookups;
- selected tenant/workspace ID, display name, and role;
- resolution method: unique email membership, explicit canonical ID, unique exact
  normalized candidate name, or customer-confirmed selection;
- resolution status: resolved, ambiguous, no membership, conflict, lookup pending,
  or lookup failed;
- exact evidence IDs that support the selection.

Rules:

1. Resolve every usable form identity before asking the customer.
2. A unique email membership resolves the target without a Workspace ID question.
3. Multiple memberships may be disambiguated only by an explicit canonical ID or
   one unique exact normalized name among candidates returned for that same email.
4. No global name search, fuzzy match, or silent selection is allowed.
5. A timeout, auth failure, malformed result, or unavailable MCP is an internal
   recovery state, not missing customer information.
6. Ask one minimal identity question only when a successful lookup proves genuine
   ambiguity, no membership, or a conflict the customer must resolve.

### Billing Snapshot

`BillingSnapshot` is always bound to the selected tenant and records subscription,
Stripe, entitlement, quota, usage, cancellation, renewal, and observed-at fields as
separate facts. It never collapses them into a single guessed `plan` string.

Rules:

- Billing is authoritative for plan and subscription state.
- Zendesk plan tags and form values are routing hints only.
- `submitted`, `scheduled`, `active`, `canceled`, `paid`, `refunded`, and
  `entitlement restored` are distinct states.
- A material billing claim must cite both the identity resolution evidence and the
  tenant-scoped Billing evidence.
- If Billing data is unavailable, the agent retries according to adapter policy and
  states only what other evidence actually proves.

### Evidence and Claims

Every `EvidenceItem` contains:

- stable evidence ID and originating tool call ID;
- source adapter and authority class;
- target identifiers and query inputs;
- observation time and covered event time window;
- success, freshness, completeness, and redaction status;
- typed structured result or a durable payload reference;
- whether it is current evidence, a control/baseline, or historical guidance.

Every material `Claim` contains:

- a customer-safe statement;
- causal role: product fact, current state, failing boundary, root cause, impact,
  recovery, or limitation;
- status: confirmed, evidence-supported, unknown, or contradicted;
- supporting and contradicting evidence IDs;
- target object IDs and time window;
- whether the claim is allowed in a public reply.

The system rejects a material claim with no eligible current evidence. A root-cause
claim additionally needs evidence for both the failing boundary and the causal
mechanism. A confirmed boundary may still be reported honestly without pretending
the full root cause is known.

### Disposition, Reply, and Ownership

The old `reply_type=handoff` coupling is replaced by three independent decisions:

```python
class Disposition(str, Enum):
    RESOLVED = "resolved"
    AWAITING_CUSTOMER = "awaiting_customer"
    RETRY_PENDING = "retry_pending"
    HUMAN_ACTION_REQUIRED = "human_action_required"
    ENGINEERING_ESCALATION = "engineering_escalation"
    SRE_ESCALATION = "sre_escalation"
    SECURITY_REVIEW = "security_review"


class ReplyKind(str, Enum):
    ANSWER = "answer"
    ASK = "ask"
    PROGRESS = "progress"
    CLOSE = "close"
    NONE = "none"


class OwnershipAction(str, Enum):
    NONE = "none"
    SUPPORT = "support"
    BILLING = "billing"
    ENGINEERING = "engineering"
    SRE = "sre"
    SECURITY = "security"


class DispositionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    disposition: Disposition
    reply_kind: ReplyKind
    ownership_action: OwnershipAction = OwnershipAction.NONE
    reason_code: ReasonCode
    evidence_ids: tuple[str, ...]
    required_customer_fields: tuple[str, ...] = ()
    retry_directive: RetryDirective | None = None
```

This permits combinations that the current model cannot represent correctly:

- answer the known facts and queue engineering ownership;
- send a progress update while an internal retry remains pending;
- give a complete bounded answer with no ownership transfer;
- ask one customer-only question without labeling the case a human escalation;
- perform no public reply when a quick internal retry is safer.

## End-to-End Runtime Flow

1. **Ingress and fencing** — existing webhook/trigger intake, deduplication, lease,
   silence window, and generation fence produce a stable ticket snapshot.
2. **Question extraction** — extract the latest unresolved customer questions and
   required response language; never answer only an older issue.
3. **Context hydration** — normalize form fields, resolve identity, bind Billing
   state when relevant, enumerate attachments, and derive the time window.
4. **Classification and plan** — the model returns a typed investigation plan with
   explicit evidence lanes and stopping conditions. Code constrains tools by route,
   risk, and authority.
5. **Evidence gathering** — execute read-only lanes; parallelize only independent
   queries; classify errors and retry without changing customer-facing disposition.
6. **Assessment** — the model proposes structured claims and unanswered questions.
   Code verifies evidence IDs, target binding, freshness, and causal requirements.
7. **Disposition policy** — a pure policy decides resolved, customer input, retry,
   human action, or specialist escalation. The model cannot select ownership.
8. **Reply composition** — generate a concise customer-safe answer from admitted
   claims and the decision. A separate quality pass checks directness, completeness,
   unsupported promises, internal leakage, repeated asks, and tone.
9. **Admission and freshness** — existing privacy, identity, evidence, generation,
   and pre-send checks validate the exact final body and decision.
10. **Durable delivery** — atomically persist the public reply intent and any
    ownership command before Browser Relay delivery. Existing ambiguity and
    idempotency handling applies.
11. **Post-delivery effects** — internal analysis, tags, Feishu notification, and
    ownership outboxes execute from the persisted decision, not inferred wording.
12. **Outcome recording** — store disposition, reason code, evidence coverage,
    retry history, delivery state, and ownership outcome for replay and metrics.

## Tool and Error Contract

Each adapter capability declares:

- read/write class and allowed support routes;
- authoritative fact domains;
- required target identifiers;
- timeout and bounded retry policy;
- idempotency/ambiguity behavior;
- redaction rules and maximum customer-visible disclosure;
- typed result and typed error variants.

Minimum error taxonomy:

- `timeout`, `transport_unavailable`, `rate_limited`, and `auth_expired` are
  retryable internal failures;
- `not_found`, `ambiguous`, `permission_denied`, and `invalid_request` require
  domain-specific policy;
- `malformed_response` fails closed and is observable;
- `ambiguous_write` is never automatically retried;
- `permanent_failure` can become human ownership only after its impact is known.

A tool failure alone never selects human ownership. The system first applies the
adapter's retry budget, tries an authoritative alternative when one exists, and
then decides whether the customer can receive a bounded answer, must supply unique
information, or truly requires a human team.

## Disposition Policy

### Autonomous Resolution

Use `RESOLVED` when the customer's question can be answered safely from admitted
claims. A full technical root cause is not mandatory for product usage, published
documentation, account facts, recovery instructions, or a bounded statement about
what is and is not currently published.

Examples:

- no published Cloud release timeline was found in the authoritative sources:
  answer that limitation directly; do not promise a date and do not hand off;
- the target tenant's subscription ended after a scheduled cancellation:
  explain the verified state and supported recovery path;
- a unique workspace is resolved from the form email:
  continue the investigation without asking for Workspace ID.

### Customer Input

Use `AWAITING_CUSTOMER` only when the missing fact is uniquely controlled by the
customer and materially changes the answer. `required_customer_fields` must list
exactly what is needed. The reply may not ask for email, Workspace ID, plan, owner,
or screenshots already available in the current form or attachments.

### Internal Retry

Use `RETRY_PENDING` for transient support-tool or provider failures within policy.
The retry has an explicit attempt count, next action, deadline, and exhaustion
reason. A progress reply is sent only when the delay is meaningful to the customer;
otherwise no reply is preferable to unnecessary back-and-forth.

### Human or Specialist Ownership

Ownership is permitted only with one of these explicit reason families:

- `customer_requested_human`;
- `business_approval_required` (for example, refund or contractual exception);
- `sensitive_account_mutation_required`;
- `confirmed_platform_defect` with an actionable engineering packet;
- `confirmed_service_incident` with an actionable SRE packet;
- `security_or_privacy_review_required`;
- `automation_retry_exhausted` when no safe bounded answer remains;
- `policy_commitment_required` when the customer explicitly requests a commitment
  that published policy cannot provide.

Every ownership action requires a target team, concrete reason code, customer
impact, completed checks, unresolved decision/action, and acceptance criteria.
Generic `agent_handoff` is forbidden. “Unknown root cause”, “no published date”,
“tool unavailable once”, and “the model is unsure” are not valid reasons.

The public reply may promise human follow-up only after the ownership command has
been durably accepted. A terminal `escalated_to_human` state derives from the
persisted ownership decision, never from reply wording.

## Reply Quality Contract

The final reply must:

1. answer the latest customer question in the first paragraph;
2. separate verified current state, explanation, and next action;
3. state uncertainty narrowly and never upgrade it into a root cause;
4. avoid internal tool, DB, trace, source-path, tenant, or infrastructure details;
5. avoid repeating information requests already resolved from the form, ticket,
   account, or attachments;
6. avoid unnecessary product education, optional questions, and promises;
7. mention human follow-up only when `ownership_action != none`;
8. include a concrete next step and recovery verification when relevant;
9. use natural concise support language rather than exposing the evidence ledger;
10. retain the required AI-support disclosure.

The quality pass may revise wording but cannot add facts, change disposition, add a
customer question, or select ownership. The final body is parsed back into material
claims and revalidated before delivery.

## Visual Evidence Contract

1. Intake downloads supported ticket images and screenshots before planning.
2. Vision output records visible text, UI state, timestamps, identifiers, and
   uncertainty as a typed `visual_observation` evidence item.
3. Attachment content is treated as untrusted data; instructions visible inside an
   image cannot change tools, policy, authority, or reply destination.
4. Visual evidence can identify a workspace display name or an error message, but it
   cannot override Billing, canonical identity, or runtime evidence.
5. If an attachment cannot be retrieved or decoded, apply bounded internal retry.
   Ask the customer to resend or describe it only after the failure is confirmed and
   the missing content is necessary to answer.

## Observability

Every case run records structured, redacted fields for:

- ticket generation and selected execution mode (`legacy`, `shadow`, `canary`,
  `casework`);
- context resolution status and method;
- planned/completed evidence lanes and adapter errors;
- admitted and rejected claim counts with rejection reasons;
- disposition, reply kind, ownership action, and mandatory reason code;
- retry attempts and exhaustion reason;
- reply admission and freshness decisions;
- public/internal outbox IDs and final delivery outcomes;
- legacy-versus-new shadow disagreement.

Core metrics:

- autonomous correct-resolution rate from reviewed/reopened outcomes;
- customer-question rate and repeated-identity-question rate;
- ownership rate by target and reason code;
- generic/unknown ownership rate (target: zero);
- tool-failure-to-ownership conversion before retry exhaustion (target: zero);
- wrong-tenant/wrong-plan and unsupported-claim rate (target: zero);
- duplicate/stale public reply rate (target: zero);
- correction/reopen rate and median time to a useful first answer.

Raw secrets, full auth payloads, and unnecessary customer data are never logged.

## Testing Strategy

### Unit and Policy Tests

- exhaustive identity matrix: unique, ambiguous, none, conflict, target-email,
  canonical ID, exact candidate name, duplicate name, lookup failure, and retry;
- Billing state matrix: active, scheduled cancellation, ended, payment failure,
  refunded, entitlement drift, missing record, and stale form/tag disagreement;
- disposition table tests proving each reason code and forbidden shortcut;
- evidence target/freshness/causal binding and contradiction handling;
- reply-quality rules and customer-safe redaction;
- visual-input prompt-injection and unavailable-attachment behavior;
- property-style invariants for decision consistency.

### Contract and Integration Tests

- typed adapter contracts for Zendesk, Inspector/DB, Billing, SigNoz, Source,
  GitHub, knowledge, attachments, and Browser Relay;
- no-write integration tests against recorded sanitized responses;
- outbox atomicity, ambiguous delivery, retry, freshness, and generation fencing;
- architecture import-boundary test;
- strict type checking for the new package.

### Golden Replay Tests

Create redacted fixtures for at least these known failure families:

- ticket 3802: form target identity must not drift to a different workspace;
- ticket 3821: resolve workspace support-side and distinguish submitted work from a
  completed refund or subscription change;
- ticket 3846: do not ask Workspace ID again when email candidates and an exact
  form/visual name resolve it;
- ticket 3861: answer the absence of a published release timeline without generic
  handoff;
- plan/quota cases: tenant-bound Billing state overrides Zendesk plan tags;
- transient MCP failure: retry internally and never ask the customer for data that
  Support already has;
- confirmed platform defect: answer known impact and create an actionable ownership
  packet without overstating root cause.

Fixtures contain no live secrets and never send Zendesk writes.

### Quality Evaluation

For every golden case, assert:

- expected tenant and Billing state;
- expected material claims and prohibited claims;
- expected disposition, reply kind, ownership action, and reason code;
- required and forbidden customer questions;
- customer-safe final reply characteristics;
- evidence coverage and absence of internal leakage.

Model-output tests use fixed fixtures plus semantic assertions. Exact prose snapshots
are avoided except for required disclosures and prohibited phrases.

## Migration and Rollback Plan

### Phase 0 — Baseline and Replay Harness

- add outcome reason codes to the legacy path without changing behavior;
- create redacted golden fixtures and a legacy/new comparator;
- record current disposition and reviewed-quality baselines.

Exit: replay harness is deterministic, current 1,752-test suite remains green, and
no production behavior changes.

### Phase 1 — Domain, Ports, and Context Builder

- add immutable models, identity/Billing policies, typed adapter errors, and wrappers
  around existing read-only tools;
- build `CaseContext` in shadow mode; legacy agent still owns all replies.

Exit: target workspace and Billing snapshots match reviewed truth on the golden set
and a live no-write sample.

### Phase 2 — Evidence, Assessment, and Disposition

- implement typed evidence storage, claim admission, retry policy, and pure
  disposition decision;
- run new and legacy decisions side-by-side with no new-core writes.

Exit: zero wrong-tenant, wrong-plan, unsupported-claim, or forbidden-handoff results
on the golden set; every disagreement has a reason code.

### Phase 3 — Reply Composition and Quality Review

- compose new replies from admitted claims and run the bounded quality pass;
- compare human-reviewed answer completeness and unnecessary questions.

Exit: new replies meet or exceed the accepted legacy answer on all golden cases;
identity questions and ownership decisions match policy exactly.

### Phase 4 — Low-Risk Canary

- enable the new path only for selected fast/product-usage cases;
- retain the same ingress and durable delivery path;
- automatic rollback on safety, freshness, duplicate, or unsupported-claim signals.

Exit: reviewed canary cases meet quality and delivery thresholds with no safety
regression.

### Phase 5 — Standard and High-Risk Expansion

- expand to standard technical and read-only Billing investigations;
- refunds, account mutations, contractual exceptions, security, and production
  changes remain human-action decisions.

Exit: reviewed production sample meets acceptance criteria and reason-coded ownership
is actionable.

### Phase 6 — Cutover and Legacy Removal

- make `casework` the only decision path;
- delete superseded prompt gates, legacy disposition coupling, and duplicated
  identity/Billing rules;
- update README, operations guide, and architecture map.

Rollback at every canary phase is a configuration switch to the legacy decision
core. There is only one trigger consumer and one delivery outbox; shadow mode never
writes, so the migration cannot create duplicate Agent instances or replies.

## Code Style

- Keep domain and policy modules focused; target less than 400 lines per module and
  less than 50 lines per ordinary function. Exceptions require an explicit review.
- Use frozen Pydantic models, enums, protocols, and typed errors in the new core.
- Do not use `dict[str, Any]` inside domain or application layers. Convert untyped
  integration payloads at adapter boundaries.
- Prefer pure decision tables and explicit returned reasons over mutable session
  flags or prompt-only rules.
- Inject clock, model, readers, delivery, and ownership ports; do not read globals in
  domain/application code.
- One orchestrator coordinates the flow; it does not implement domain rules itself.
- Keep customer wording separate from evidence and internal handoff packets.

Example policy shape:

```python
def decide_disposition(assessment: CaseAssessment) -> DispositionDecision:
    if assessment.customer_only_fields:
        return DispositionDecision(
            disposition=Disposition.AWAITING_CUSTOMER,
            reply_kind=ReplyKind.ASK,
            reason_code=ReasonCode.CUSTOMER_INPUT_REQUIRED,
            evidence_ids=assessment.evidence_ids,
            required_customer_fields=assessment.customer_only_fields,
        )
    if assessment.retryable_blocker is not None:
        return retry_decision(assessment.retryable_blocker)
    if assessment.human_action is not None:
        return ownership_decision(assessment.human_action)
    return resolved_decision(assessment)
```

## Boundaries

### Always

- refresh the latest conversation and ticket generation before investigation and
  again before delivery;
- resolve workspace identity support-side before asking the customer;
- use target-bound Billing evidence for plan/subscription claims;
- keep the operational queue/outbox/freshness/idempotency safeguards;
- retain an auditable, redacted decision artifact;
- run shadow/replay/canary gates before increasing production scope.

### Ask First

- adding Pyright or any other dependency;
- production database/schema migration;
- changing public reply language policy;
- enabling a new mutation or business-decision capability;
- starting a production canary, increasing its scope, merging to `main`, or deploying;
- removing the legacy rollback path.

Approval of this specification authorizes the proposed dev-only Pyright addition and
implementation through local/shadow-ready code. Production canary, merge, and deploy
remain separate approval points.

### Never

- guess a workspace, subscription, refund, entitlement, recovery, or root cause;
- treat Zendesk tags/form plan values as Billing truth;
- ask for Workspace ID when a unique support-side resolution already exists;
- let the model directly choose ownership or bypass the reason-code allowlist;
- convert a first tool error or general uncertainty into human escalation;
- promise human follow-up before a durable ownership command exists;
- retry an ambiguous external write automatically;
- expose internal evidence sources or identifiers in a public reply;
- send a real customer message as a test or let shadow mode perform writes.

## Success Criteria

1. The new architecture has a single typed path from `CaseContext` through evidence,
   assessment, disposition, reply, and delivery.
2. Workspace identity is resolved from available form/email evidence; a customer
   Workspace ID question occurs only for proven ambiguity/conflict/no-membership.
3. All plan/subscription claims are bound to the selected tenant and Billing truth.
4. `handoff` no longer exists as a reply type. Reply kind, disposition, and ownership
   are independent and internally consistent.
5. Every non-none ownership action has a specific target and reason code; generic
   `agent_handoff` cannot be persisted.
6. A transient internal failure follows typed retry policy and cannot immediately
   become a customer identity question or human transfer.
7. Product facts and bounded “not currently published/confirmed” answers can resolve
   a ticket without a fabricated root cause or unnecessary handoff.
8. Visual attachments are ingested as untrusted typed evidence and used before
   asking the customer to repeat visible information.
9. Golden replays for 3802, 3821, 3846, 3861, plan/quota, retry, and confirmed-defect
   families produce the expected context, claims, disposition, and prohibited asks.
10. Unsupported material claims, wrong-tenant/plan results, duplicate replies, stale
    replies, and pre-exhaustion tool-failure handoffs are zero in the acceptance set.
11. New-core reply quality meets or exceeds the reviewed legacy/best-known answer for
    every golden case; no phase advances on an unresolved quality regression.
12. Existing tests plus new unit, contract, replay, architecture, lint, type, and build
    checks pass before canary.
13. Production canary can be disabled without data loss, duplicate processing, or a
    new Agent instance.
14. After cutover, superseded legacy rules are removed rather than left as permanent
    duplicate logic.

## Proposed Decisions Requiring Approval

1. Keep public replies English-only for this redesign.
2. Treat “no authoritative published timeline found” as a direct bounded answer by
   default, not a human handoff.
3. Allow the quality reviewer to revise wording only; it cannot alter facts,
   disposition, customer questions, or ownership.
4. Add Pyright as a dev-only dependency and require strict checking for new code.
5. Build locally through shadow-ready Phase 3 after spec approval; request separate
   approval before production canary, merge to `main`, deployment, or legacy removal.

## Implementation Tasks

Implementation starts only after this specification and the proposed decisions are
approved.

- [x] Phase 0: add reason-code baseline and redacted replay harness.
- [x] Phase 1: add domain models, ports, adapter error taxonomy, and context builder.
- [x] Phase 2: add evidence, assessment, retry, and disposition policies.
- [x] Phase 3: add reply composition, quality review, and shadow comparator.
- [x] Verify full existing/new tests, Ruff, Pyright, build, and no-write replay.
- [ ] Enable an approved live no-write shadow sample, review disagreements, and
      request production-canary approval.
- [ ] Canary, expand, cut over, and remove legacy code under separate approvals.

## Local Verification Evidence

- `uv sync --frozen --dev`: dependency lock audited successfully.
- `uv run pytest -q`: 1,875 tests passed; three pre-existing `structlog` warnings.
- `uv run pytest -q tests/casework tests/replay`: 120 focused and replay tests passed.
- `uv run ruff check src tests`: passed.
- `uv run pyright src/zd_agent/casework tests/casework`: zero errors and warnings.
- `uv build --offline`: source distribution and wheel built successfully.
- `git diff --check main`: passed.

The replay fixtures are sanitized and exercise the complete no-write casework
pipeline. Repository configuration remains `casework.mode: legacy`. The only
available opt-in is `ZD_CASEWORK_MODE=shadow`; delivery modes are rejected by
configuration validation, and shadow observations are written to a redacted JSONL
archive without sending Zendesk replies or changing ticket ownership.

## Open Questions

No technical question blocks local implementation. The five proposed decisions above
are the human approval gate. Production rollout scope and canary duration will be
set from replay/shadow evidence rather than an arbitrary calendar duration.
