# Spec: Zendesk SaaS Agent (ZD Agent)

This document describes the system as it runs in production. The original
Greenfield design (a Python Harness that launched one Docker sandbox per run)
was replaced by `apps/pi-agent` and has been removed from the repository; see
Git history before this revision for that design.

## Objective

Answer Dify Cloud (SaaS) Zendesk tickets automatically and safely:

1. Ingress receives signed Zendesk webhooks and applies deterministic admission
   rules. A matching event queues only the `ticket_id`.
2. Pi Agent claims the job, rereads the ticket, investigates through read-only
   OpenConnector tools guided by on-demand Skills, and drafts a reply.
3. Deterministic guards decide whether the draft may be delivered. A customer
   job is answered publicly through the Zendesk Browser Relay; an operator
   mention produces an internal-note draft that is never sent to the customer.
4. Every run is recorded for the Admin console and optional log export.

## Architecture

```text
Zendesk webhooks
  │ /webhook/zendesk, /webhook/zendesk-messaging, /webhook/zendesk-ticket-events
  ▼
ingress (FastAPI) ── admission rules ──► PostgreSQL: events, case_jobs, ticket_states
                                              │ lease + heartbeat
                                              ▼
                                         pi-agent (Node/TypeScript worker)
                                           ├─ tools: OpenConnector MCP (read-only)
                                           ├─ skills: skills/*/SKILL.md (on demand)
                                           ├─ public reply  ─► Zendesk Browser Relay
                                           ├─ mention draft ─► Zendesk internal note
                                           └─ alerts        ─► Feishu bot (optional)
                                              │
                                              ▼
                                         run_records ◄── admin-api (FastAPI) + admin-web (Vue 3)
                                              └────────► log-sync (optional export)
```

| Component | Source | Image | Role |
| --- | --- | --- | --- |
| Ingress | `apps/ingress` | `casework-ingress` | Webhook verification, normalization, admission, `human` cancellation |
| Pi Agent | `apps/pi-agent` | `casework-pi-agent` | Queue consumer, investigation, guards, delivery, alerts |
| Admin | `apps/admin-api`, `apps/admin-web` | `casework-admin` | Console, model settings, run traces, migrations job, log-sync job |
| Shared Python | `packages/contracts`, `packages/persistence` | (in Python images) | Pydantic contracts, SQLAlchemy models and repositories |
| Skills | `skills/` | (copied into Pi Agent) | Investigation procedures and provider routing |
| Migrations | `migrations/` | (run from Admin image) | Alembic schema history |

## Ingress admission

- `/webhook/zendesk` and `/webhook/zendesk-messaging` accept customer events,
  verified with the Zendesk webhook signature and
  `ZENDESK_MESSAGING_WEBHOOK_SECRET`.
- `/webhook/zendesk-ticket-events` accepts `ticket.comment_added` events signed
  with its own `ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET`. Only a private staff
  comment that mentions the operator trigger (for example `@agent`) is
  admitted, as a mention job. Comments that start with the Agent's internal
  draft marker are ignored. See `deploy/TICKET_EVENTS_WEBHOOK.md`.
- Processing rules (`processing_rules.py`) are pure: the configured SaaS tag
  and assignee, `new`/`open` status, and a public customer-origin message are
  required. A whole-message `human` (NFKC-normalized, punctuation-trimmed)
  records a takeover in `ticket_states` and cancels active work instead of
  queueing a job.
- Admission is atomic in PostgreSQL. It deduplicates by `event_id`, keeps at
  most one queued job per ticket, and rejects new work with `agent_paused` when
  the Admin desired state is `paused`.

## Job processing (Pi Agent)

The worker polls `case_jobs` with `CASEWORK_WORKER_CONCURRENCY` slots
(default 2). A claim takes a 180-second lease renewed every 60 seconds. Expired
running jobs are reclaimed for up to 3 attempts.

Per job:

1. **Pre-run guards** (customer jobs only; mention jobs bypass them): skip if
   the ticket was already handed to a human, if the latest message has already
   been answered, or if the conversation contains a request for a human. A
   takeover is persisted and announced to the Feishu bot.
2. **Context**: read the full ticket conversation and attachments through
   OpenConnector's Zendesk connection. Attachments are passed to the model as
   native image/file inputs. For a mention job the operator's note is injected
   as the instruction and the draft must be in English.
3. **Model**: the model, base URL and API key come only from
   `system_settings.model_config`, saved on the Admin 模型配置 page. There is
   no built-in default: if any of them is missing the job fails with an alert
   instead of running a different model.
4. **Investigation**: the agent loop (`@earendil-works/pi-coding-agent`) uses
   only tools discovered live from OpenConnector whose operation type is
   `read`. Gmail and every mutation are excluded. Skills are loaded on demand
   through a read tool. `investigation_check` records the material questions,
   sources and observation IDs, and the runtime allows at most two continuation
   reviews before returning qualified partial findings.
5. **Reply review gate**: the model must return `=== INTERNAL NOTE ===` and
   `=== CUSTOMER REPLY ===` with a JSON review decision.
   `validateReplyReview` blocks delivery when the review fails, or when a
   `verified` claim has no successful business evidence. The internal note
   stays in run telemetry and is never written to Zendesk.
6. **Footer**: a localized footer (en/ja/zh, New York support hours) is
   appended in code, never by the model.
7. **In-flight monitor** (every 5 seconds): a human takeover or a new customer
   message aborts the run. A new message requeues the ticket with a 10-second
   debounce.
8. **Pre-delivery freshness check**: reread the ticket. If anything changed,
   discard the draft and requeue (or hand over to a human).
9. **Delivery**:
   - Customer job: POST to the Browser Relay `/send`, the only public-reply
     write path. A relay failure fails the job and sends the draft to the
     Feishu bot. On success, redundant queued jobs for the ticket are
     cancelled.
   - Mention job: an idempotent internal note through the Zendesk API, marked
     `[DIFY_AI_AGENT_DRAFT:<job_id>]` and recorded in
     `internal_draft_deliveries`.
10. **Outcome**: upsert `run_records` with the trace, rounds, tokens, tool calls
    and delivery result, and mark the job `completed`, `cancelled` or `failed`.

`FEISHU_WEBHOOK_URL` is optional. When it is unset, alerts are disabled and
the worker logs a warning at startup.

## Persistence

PostgreSQL is the queue and the source of truth. Alembic owns these tables:
`events`, `ticket_states`, `config_revisions`, `config_head`, `case_jobs`,
`outbox_messages`, `run_records`, `run_events`, `run_log_exports` and
`internal_draft_deliveries`.

`system_settings` (model, mode and safety settings written by Admin) is read
and written by `admin-api` and `pi-agent`, but no migration creates it.

## Admin console

`admin-api` serves the built `admin-web` from its own image together with
`/api/v2`: run traces (retry, skip, escalate), model settings and connection
test, operating mode, safety and Feishu test, the MCP mesh and tool probes,
Skills, and live logs. Authentication uses the mounted admin auth file and an
internal token used by deployment scripts.

## Deployment

- CI (`.github/workflows/docker-image.yml`) runs Ruff, Pyright, pytest, and the
  Admin and Pi Agent test/build. On `main` it pushes the `ingress`, `admin` and
  `pi-agent` images and deploys them by digest.
- `deploy/deploy-production.sh` runs on the server. It requires a
  digest-pinned image for each service. `prepare_runtime.py` writes
  `runtime.env` and the mounted `credentials.env` (both mode 0600), including
  the image references. The script then runs migrations, restarts the
  services, waits for `/readyz`, and records `release.env` /
  `previous-release.env`.
- `deploy/rollback-production.sh` redeploys the images recorded in
  `previous-release.env`. It never downgrades the database.
- After a successful deploy, CI tags the deployed commit
  `deploy-YYYY-MM-DD-<short sha>` (Beijing date). The tag job is the only
  job with repository write access.
- `deploy/compose.release.yaml` refuses to start without the digest-pinned
  images. Every container runs the code baked into its image; no source files
  are mounted from the host.
- Secrets are never committed. Settings have exactly two homes:
  - **GitHub `production` environment**: every deployment setting and secret
    (OpenConnector token, Zendesk account and webhook secrets, Browser Relay
    key, Feishu URL, log export). The workflow exports them to
    `prepare_runtime.py`. A rollback runs without GitHub and reuses the values
    the previous deployment saved in `runtime.env` / `credentials.env`.
  - **Admin console**: everything changed at run time, including the model,
    its endpoint and API key, operating mode and safety rules.
  `runtime.env` and `credentials.env` are generated; never edit them by hand.

## Boundaries

### Always

- Keep investigation read-only through OpenConnector. Recheck each tool's
  live `read` classification before calling it.
- Keep `human` detection, guards, footers and delivery in code, not in prompts.
- Refresh the ticket before any public reply. Drop stale drafts.
- Deploy only digest-pinned images.

### Ask first

- Add a new write path to Zendesk or any other system.
- Add a durable dependency (Redis, Kubernetes, another database).
- Change admission rules, delivery guards or the production deployment flow.

### Never

- Send a public reply from an operator mention.
- Write the internal investigation note to a Zendesk comment.
- Commit credentials, webhook URLs or tokens.
- Persist hidden model reasoning.

## Testing

- Python: `uv run pytest -q` covers ingress rules and webhooks, persistence
  (integration tests need Docker for Testcontainers), admin API, contracts and
  deployment scripts. `uv run ruff check .` and `uv run pyright` must pass.
- Pi Agent: `pnpm --filter pi-agent test` (Vitest) and `pnpm --filter pi-agent build`.
- Admin web: `pnpm --dir apps/admin-web test` and `build`.
- Read-only investigation evaluation: `pnpm --dir apps/pi-agent eval:investigation`
  (uses the real model and OpenConnector and consumes quota; see README).
