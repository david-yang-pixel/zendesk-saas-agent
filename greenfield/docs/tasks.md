# Greenfield Implementation Tasks

## Slice 0: Contracts and repository foundation

- [x] Add Python/TypeScript workspace manifests and CI commands.
  - Acceptance: clean installs, lint, type-check, unit-test and build commands
    exist without importing the legacy package.
  - Verify: `uv run pytest -q`, `uv run ruff check .`, `uv run pyright`, and
    `pnpm --dir apps/admin-web build`.
- [x] Implement shared event, job, session, sandbox, Todo, Tool, evidence,
  Subagent, and outcome contracts.
  - Acceptance: contracts reject invalid transitions and serialize stable JSON.
  - Verify: contract tests and generated JSON schemas.
- [x] Add PostgreSQL schema and repository ports.
  - Acceptance: a fresh database migrates up and down; job claims are atomic.
  - Verify: PostgreSQL integration tests.

## Slice 1: Webhook to durable ticket job

- [x] Implement processing-rule evaluation with tests first.
- [x] Implement webhook deduplication and minimal `ticket_id` enqueue.
- [x] Implement deterministic `human` cancellation events.
- [x] Add ingress health and metrics endpoints.

Checkpoint: duplicate and nonmatching webhooks cannot start Agent work.

## Slice 2: Sandbox lifecycle proof

- [x] Implement Docker Engine abstraction and fake test engine.
- [x] Create `0.5 CPU / 1 GiB` ephemeral sandbox for one claimed job.
- [x] Destroy sandboxes on every terminal and exceptional path.
- [x] Implement orphan sweeper and one `1 CPU / 1 GiB` resource retry.

Checkpoint: lifecycle integration tests prove no terminal sandbox survives.

## Slice 3: Minimal Harness vertical path

- [x] Fetch current ticket from a fake Zendesk adapter using only `ticket_id`.
- [x] Resolve active question separately from clarification answers.
- [x] Generate a typed Todo plan.
- [x] Execute one fake Tool through the Tool Loop and admit typed evidence.
- [x] Return a structured answer outcome to the controller.

Checkpoint: webhook -> job -> sandbox -> Todo -> Tool -> outcome -> destroy.

## Slice 4: Context, Skills, real Tool SDK

- [x] Implement Context Governor and checkpoints.
- [x] Implement progressive Skill registry/loader.
- [x] Implement Tool manifest, executor and result normalization SDK.
- [x] Implement sandbox artifact storage for large outputs.

## Slice 5: Subagents and memory

- [x] Implement explicit independent Subagent model configuration.
- [x] Implement depth-one child sessions and task lifecycle.
- [x] Aggregate child evidence and bounded reports.
- [x] Implement working memory and reviewed long-term-memory proposals.

## Slice 6: Fresh, idempotent delivery

- [x] Refresh latest ticket event before sending.
- [x] Append the localized footer exactly once.
- [x] Implement transactional reply outbox and receipt reconciliation.
- [x] Cancel delivery when a concurrent `human` event arrives.

## Slice 7: Practical management console

- [x] Overview page and Agent desired-state controls.
- [x] Harness configuration and independent model editors.
- [x] Processing-rules and localized-footer editor.
- [x] Skill/tool registry page.
- [x] End-to-end run detail page.

## Slice 8: Four images and release bundle

- [x] Multi-stage Dockerfile with four image targets.
- [x] Compose deployment and PostgreSQL migrations job.
- [x] Per-image readiness and a combined release manifest.
- [x] Immutable tag/digest deployment and rollback inputs.

## Slice 9: Replay, canary and cutover

- [x] Sanitized replay suite for required Zendesk behaviors.
- [x] No-write live dry-run with an explicit `DELIVERY_MODE=dry_run` gate.
- [x] Explicit-ticket canary with a required allowlist and no-write path.
- [ ] Cut over ingress while preserving the legacy rollback image.

Implemented but not yet exercised against production: the read-only Zendesk
snapshot adapter, delivery mode gates, digest manifest renderer, and release
Compose bundle. Production execution remains unchecked because it requires
separate deployment/write authorization and live credentials.

## Slice 10: Production composition

- [x] Add PostgreSQL-backed configuration revisions, atomic ingress admission,
  durable root session IDs, queue leases, and worker heartbeats.
- [x] Add runnable Ingress, Harness, and Admin composition roots with database
  schema readiness and owned startup/shutdown lifecycles.
- [x] Make the deployment migration gate execute `alembic upgrade head` and
  point the long-running images at their production composition roots.
- [x] Compose the fail-closed Sandbox HTTP adapters and pass only an explicit
  non-database runtime environment into each ephemeral container.
- [x] Connect durable PostgreSQL delivery fencing and outbox claims while
  keeping production delivery in explicit dry-run by default.
- [x] Add a mockable Zendesk reply sender with provider receipts; keep live and
  canary fail-closed when complete credentials are absent.

- [x] Resolve frozen config and credential references inside a sandbox without
  persisting secret values in job/session records.
- [x] Add production root-model and depth-one child-model runners using each
  slot's explicit provider/model/credential reference.
- [x] Register the production investigation Tool adapters and connection
  probes; keep delivery and orchestration outside the Tool registry.
- [x] Compose PostgreSQL ingress, worker, outbox, Admin read models, Docker
  engine, and orphan sweeper in runnable service factories.
- [x] Build all four images with a live Docker daemon and record their digests.
  - Local verification (2026-09-01): ingress
    `sha256:903449963ac42655f39e74e0dcd4459c775877e37146ec258472a0ccdca86e8b`,
    harness
    `sha256:60b1b3362bcf073ca77bb5385d86b5a965faac65b477aa0eabedaf43cced5c83`,
    sandbox
    `sha256:225a63392f4efa2960b70bee35c58a83d36f37057e4b2b039394953092b04825`,
    admin
    `sha256:ab08e9149b4ec443f838731da51bf7bdaa5d87a0597e7b182138ad472d0c4ade`.
  - These are local BuildKit image IDs, not registry digests and not a
    production deployment record.
