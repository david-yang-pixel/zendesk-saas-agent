# Spec: ZendeskCasework Greenfield Harness

## Objective

Build a new Zendesk support-agent system without importing or depending on the
legacy `zd_agent` implementation. The system has three business layers:

1. Webhook ingress receives Zendesk events.
2. Processing-rule filtering checks the configured SaaS tag, SaaS assignee,
   `new`/`open` status, customer-message origin, and the `human` keyword. A
   matching event submits only `ticket_id` to the durable job queue.
3. ZendeskCasework Harness creates one ephemeral sandbox per processing run.
   The sandbox fetches the complete current ticket, resolves the active
   question, creates and executes a dynamic Todo plan through Tool Loops,
   optionally runs independently configured Subagents, governs and compacts
   context, loads Skills progressively, records evidence, and returns a
   structured outcome. The controller refreshes the ticket, appends the
   configured language footer once, delivers idempotently, persists the
   compact result, and destroys the sandbox.

The management console must expose only practical operational controls. It is
not a deployment or filesystem editor.

## Fixed Decisions

- This is a Greenfield implementation under `greenfield/`; it must not import
  from `src/zd_agent` or migrate legacy live sessions/state.
- Application images: `casework-ingress`, `casework-harness`,
  `casework-sandbox`, and `casework-admin`.
- PostgreSQL is the durable source of truth and queue. Redis is not required.
- Every processing run gets a new sandbox. Every terminal path destroys it.
- Default sandbox limit: `0.5` CPU and `1 GiB` memory, 30-minute timeout.
- One resource retry may use `1` CPU and `1 GiB` memory.
- Root Agent and Subagents share the ticket sandbox but use separate working
  directories, sessions, model loops, context windows, and explicit model
  configurations.
- The system has one business Agent type: `ZendeskCasework`. All registered
  investigation tools are available to root and child Agents. Harness
  orchestration and final delivery are controller operations, not model tools.
- System instructions are minimal. Investigation procedures live in
  progressively loaded Skills.
- `human` is deterministic ingress behavior, not a model decision.
- The final customer footer is deterministic delivery behavior, not model text.

## Tech Stack

- Python 3.12
- FastAPI and Pydantic v2
- SQLAlchemy 2 async + Alembic
- PostgreSQL 16
- pytest, pytest-asyncio, Ruff, Pyright
- Vue 3, TypeScript, Vite, Vitest, Playwright
- Docker/BuildKit with four independently tagged image targets
- Docker Engine API for ephemeral sandbox lifecycle in the initial single-host
  deployment

## Commands

From `greenfield/`:

```bash
uv sync --frozen --dev
uv run pytest -q
uv run ruff check .
uv run pyright
uv run alembic upgrade head
uv run uvicorn casework_ingress.app:create_app --factory --reload --port 8101
uv run uvicorn casework_admin.app:create_app --factory --reload --port 8102
uv run python -m casework_harness.worker

corepack enable
pnpm --dir apps/admin-web install --frozen-lockfile
pnpm --dir apps/admin-web test
pnpm --dir apps/admin-web build
pnpm --dir apps/admin-web e2e

docker build -f deploy/Dockerfile --target ingress -t casework-ingress:dev .
docker build -f deploy/Dockerfile --target harness -t casework-harness:dev .
docker build -f deploy/Dockerfile --target sandbox -t casework-sandbox:dev .
docker build -f deploy/Dockerfile --target admin -t casework-admin:dev .
docker compose -f deploy/compose.yaml up --build
```

## Project Structure

```text
greenfield/
  apps/
    ingress/          Webhook API and deterministic processing rules
    harness/          Job consumer, sandbox controller, delivery/outbox
    sandbox/          Ephemeral ZendeskCasework runtime
    admin-api/        Configuration and run-query API
    admin-web/        Vue management console
  packages/
    contracts/        Pydantic and JSON contracts shared across processes
    persistence/      PostgreSQL models, repositories, migrations
    agent-core/       Todo, Tool Loop, Skills, context, evidence, memory
    tool-sdk/         Tool definition/execution/result contract
  skills/             Versioned built-in ZendeskCasework Skills
  migrations/         Alembic revisions
  tests/
    unit/
    integration/
    contract/
    e2e/
    fixtures/
  deploy/
    compose.yaml
    Dockerfile
  docs/
    spec.md
    tasks.md
```

## Core Contracts

### Processing job

The ingress-to-harness business payload is intentionally minimal:

```json
{"ticket_id": 3726}
```

Queue metadata such as `job_id`, `event_id`, timestamps, attempts, and config
revision is infrastructure metadata and does not expand the Agent input.

Every job freezes a required configuration snapshot: `config_revision`, root
and child model configuration identifiers, plus model, Skill, and tool registry
digests. Missing snapshot metadata fails closed before sandbox creation.

### Normalized webhook event

The webhook adapter supplies `event_id`, `ticket_id`, status, assignee, tags,
message visibility, actor origin, and message text to the pure rule layer. If a
required field is missing or actor origin is unknown, the event is ignored with
a typed reason; the rule layer never fetches Zendesk. Repeated `event_id` values
are idempotent.

Only a public, customer-origin message can trigger processing or `human`.
Human aliases use Unicode NFKC normalization, trim surrounding whitespace,
casefold, and exact comparison after removing surrounding Unicode punctuation.
Agent replies and deterministic footers can never trigger it. `human` creates a
cancellation command and never a CaseJob. Applying the command atomically
advances the ticket generation, cancels queued/running work, and fences unsent
outbox entries for older generations.

### Ticket snapshot and active question

The sandbox fetches an immutable `TicketSnapshot` containing ticket metadata,
requester, assignee, tags, form fields, all paginated public/internal comments,
Messaging events, attachment metadata, locale, and latest customer-event
cursor. An incomplete page, attachment listing, or cursor is a typed fetch
failure rather than a partial answer.

`ActiveQuestion` binds the original unresolved customer request to later
clarifications and their event IDs. A clarification changes facts and
constraints without replacing the original question.

### Todo

```json
{
  "task_id": "task-01",
  "goal": "Locate the affected app",
  "status": "ready",
  "assigned_to": "root",
  "dependencies": [],
  "skill": "app-run-investigation",
  "suggested_tools": ["sql_query"],
  "required_evidence": ["app_id", "workspace_id"],
  "attempts": 0,
  "completion_condition": "One exact app is found or ambiguity is proven"
}
```

### Sandbox protocol and outcome

Harness and sandbox communicate only through versioned JSON contracts for
`StartRun`, `Heartbeat`, `CancelRun`, and `RunResult`; neither application
imports the other's runtime modules. `StartRun` includes job/session/generation,
ticket ID, attempt number, frozen config/digests, and deadline.

`RunResult` includes terminal disposition (`answer`, `ask_customer`,
`human_takeover`, `failed`, or `cancelled`), reply locale/body when applicable,
snapshot cursor, Todo summary, evidence and artifact references, checkpoint
reference, retry classification, and delivery eligibility. Hidden model
reasoning is never included or persisted.

### Tool result

```json
{
  "status": "success",
  "summary": "Found one matching app",
  "data": {},
  "evidence_id": "ev-01",
  "observed_at": "2026-09-01T00:00:00Z",
  "targets": [],
  "artifacts": [],
  "truncated": false,
  "retryable": false
}
```

## Runtime State Machines

Case job:

```text
queued -> claimed -> sandbox_creating -> running -> finalizing
       -> completed | awaiting_customer | human_takeover | failed | cancelled
```

The PostgreSQL queue enforces one active generation per ticket and uniqueness
by `event_id`. Claims use a lease and heartbeat; expired claims are reclaimable.
Retry count, bounded backoff, terminal dead-letter reason, ticket generation,
and cancellation generation are durable. A worker acknowledges a job only
after its terminal record and eligible outbox row commit atomically.

Sandbox:

```text
creating -> ready -> running -> destroying -> destroyed
```

Attempt `1` always uses `0.5 CPU / 1 GiB`, no extra swap, and a 30-minute
deadline. Only a typed CPU/memory resource-exhaustion result may create attempt
`2`, with a new sandbox ID, `1 CPU / 1 GiB`, no extra swap, and a second
30-minute deadline. All other failures are not resource retries.

The controller creates the container, waits for readiness, monitors heartbeat,
propagates cancellation, and requests destruction on success, ask, takeover,
failure, timeout, cancellation, worker crash, and retry. A label-scoped orphan
sweeper removes only expired casework sandboxes carrying this installation's
controller/session/job/attempt labels. Destruction failure is durable and
alertable; it never makes a failed run look successful.

Todo:

```text
pending -> ready -> running -> completed | failed | blocked | cancelled
```

Subagent:

```text
queued -> running -> completed | failed | timed_out | cancelled
```

## Agent Runtime

The sandbox runtime follows this loop:

```text
fetch ticket -> resolve active question -> load core skill -> build Todo plan
-> choose ready Todo -> call tool or Subagent -> record evidence
-> update Todo and context checkpoint -> repeat until completion gate
-> produce structured outcome
```

It must not treat a clarification answer as a replacement for the original
active question. It must not ask the customer while an internally executable
Todo remains.

Each Tool Loop iteration has a versioned request schema, remaining wall-clock
and token budgets, maximum tool rounds, and explicit termination reason. Tool
results are validated before entering context; retryable tool failures use a
bounded per-tool policy. An answer is allowed only when the completion gate
finds no executable Todo and all claims are evidence-bound.

## Context Governance

- Soft compaction at 60% of the configured model context window.
- Hard checkpoint/rebuild at 80%.
- Retain the four most recent tool rounds inline.
- Keep exact identifiers, timestamps, error codes, evidence IDs, open Todos,
  and current customer messages verbatim.
- Store large raw tool outputs and attachments as sandbox artifacts; place only
  typed summaries and artifact references in model context.
- Root and child contexts are isolated. Child results return as bounded,
  structured reports.
- Before delivery, refetch the latest Zendesk event. A changed event invalidates
  the draft and starts a fresh processing run.
- A context checkpoint contains active question, exact identifiers and cursors,
  Todo graph/state, evidence/claim bindings, unresolved contradictions, loaded
  Skill versions, model/tool budgets, and artifact references. Rebuild validates
  them before the next model call; an irreducibly over-budget checkpoint fails
  rather than dropping required evidence.

## Skills

The core Skill is always loaded. Specialized Skills are selected and loaded
only when needed:

- `zendesk-casework-core`
- `app-run-investigation`
- `workflow-investigation`
- `saas-database-investigation`
- `signoz-investigation`
- `billing-investigation`
- `source-release-investigation`
- `customer-reply`

Every Skill has a versioned manifest with content digest, selection predicates,
dependencies, compatible tool contracts, and bounded context contribution.
Load failure is typed; a required Skill blocks the dependent Todo instead of
being silently omitted. A Subagent receives an explicitly configured slot,
independent model credentials/name, scoped Todo, separate workdir/session and
context budget, and returns a bounded evidence-linked report. Child
cancellation follows the parent run. Child Agents cannot create children.

## Delivery and outbox

Only the Harness controller may create or send a customer reply. An outbox row
is keyed by ticket generation and snapshot cursor and progresses through
`pending`, `attempted`, `sent`, `confirmed`, `ambiguous`, or `cancelled`.
Immediately before send, the controller verifies the latest customer cursor and
human cancellation fence. A mismatch cancels the draft and schedules a fresh
generation.

The localized footer is selected from the frozen config revision, uses a
canonical invisible marker, and is appended exactly once to customer-facing
`answer` and `ask_customer` bodies. Existing marked footers are replaced, not
duplicated. Unsupported locales use the configured default locale. Failed,
cancelled, and takeover outcomes create no customer outbox entry.

## Management Console

Five top-level pages only:

1. Overview: desired Agent state, jobs, failures, queue, active sandboxes.
2. Harness: root model, explicit Subagent models, context, sandbox, memory,
   Todo/Tool Loop settings.
3. Processing rules: tags, assignee, statuses, `human` aliases, reply enable,
   localized footers.
4. Skills and tools: versions, enablement, assignments, tool contracts and
   connection tests.
5. Runs: ticket/session, active question, Todos, Tool Calls, Subagents,
   evidence, checkpoints, sandbox lifecycle, and outcome.

Host deployment, filesystem paths, and raw system-prompt editing are excluded.

The editable allowlist is limited to desired Agent state, root model,
independent Subagent slots/models, processing rules and human aliases, localized
footers/reply enablement, Skill/tool enablement and assignment, bounded Tool
Loop/context settings, and connection tests. Fixed sandbox resources, run
snapshots, lifecycle history, evidence, digests, and audit records are read-only.
Secrets are write-only and masked after save. Every save uses optimistic
revision checks and creates an auditable configuration revision; rollback
creates another revision rather than mutating history.

## Image dependency graph

```text
casework-ingress -> contracts + persistence -> PostgreSQL
casework-harness -> contracts + persistence -> PostgreSQL + Docker Engine API
casework-sandbox -> contracts + agent-core + tool-sdk + built-in Skills
casework-admin -> contracts + persistence -> PostgreSQL
```

Reverse application imports are forbidden. The sandbox does not access the
queue or outbox database. Harness never imports sandbox runtime code. A release
manifest records all four immutable image digests plus the migration revision;
readiness requires migrations, queue connectivity, and a sandbox launch probe.

## Code Style

Use small typed functions, explicit domain names, immutable contracts, and
dependency injection at process boundaries:

```python
async def submit_ticket(
    *, ticket_id: int, jobs: JobRepository, event_id: str
) -> CaseJob:
    existing = await jobs.find_by_event_id(event_id)
    if existing is not None:
        return existing
    return await jobs.enqueue(ticket_id=ticket_id, event_id=event_id)
```

- Python modules and functions use `snake_case`; types use `PascalCase`.
- TypeScript uses strict mode and named exports.
- Domain contracts contain validation but no infrastructure calls.
- Tests assert outcomes, not private method call sequences.

## Testing Strategy

- Unit tests: processing rules, state transitions, Todo readiness, compaction,
  Skill selection, Tool result validation, footer idempotency.
- Contract tests: all cross-image JSON payloads and database enums.
- Integration tests: PostgreSQL queue claim/retry, outbox idempotency, config
  snapshots, Docker sandbox lifecycle through a fake engine first.
- E2E tests: webhook -> filter -> sandbox -> structured outcome -> refreshed
  delivery -> sandbox deletion, including `human`, timeout, OOM retry, Japanese
  response, duplicate webhook, and new-message invalidation.
- Browser tests: all five admin pages and the common operator workflows.
- CI guard: run from `greenfield/`, fail on imports from `zd_agent`, verify the
  dependency graph, and build each image target independently.

Every behavior change follows RED -> GREEN -> REFACTOR.

## Boundaries

### Always

- Create failing behavior tests before implementation.
- Pin every run to an immutable configuration revision.
- Destroy the sandbox on every terminal path.
- Keep customer delivery idempotent and freshness-checked.
- Keep root and child model settings explicit.
- Keep large raw artifacts outside model context.

### Ask first

- Change the four-image boundary.
- Add Redis, Kubernetes, or another durable dependency.
- Change fixed sandbox CPU/memory policy.
- Migrate legacy sessions, memory, or reply outbox data.
- Push, deploy, or enable production Zendesk writes.

### Never

- Import or copy legacy `src/zd_agent` runtime modules into Greenfield code.
- Reuse a sandbox between processing runs.
- Let a Subagent deliver directly to Zendesk.
- Put `human` detection or the fixed footer in model prompts.
- Persist hidden model reasoning.
- Commit credentials.

## Success Criteria

- Four application images build independently from the same release revision.
- A matching webhook creates exactly one job containing `ticket_id` as its
  business input; a nonmatching event creates none.
- `human` cancels a live run and prevents its draft from being delivered.
- Every run creates a `0.5 CPU / 1 GiB` sandbox and destroys it after success,
  ask, human takeover, failure, timeout, or cancellation.
- One resource-exhaustion retry may use `1 CPU / 1 GiB`.
- The sandbox fetches the full current ticket and creates a visible Todo plan.
- Tool Loop continues while an executable Todo remains.
- Subagent models are independently configured and do not inherit root model
  credentials or model names.
- Compaction preserves all exact evidence bindings and active work.
- The final footer is localized and appended exactly once.
- The console implements the five-page information architecture.
- Greenfield unit, contract, integration, browser, and E2E suites pass without
  invoking production Zendesk writes.

## Open Questions

None block the initial contract and foundation slices. Production credentials,
exact Zendesk field identifiers, and localized footer text are deployment-time
configuration and are not required to build the runtime.
