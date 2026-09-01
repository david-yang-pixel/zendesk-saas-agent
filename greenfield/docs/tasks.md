# Greenfield Implementation Tasks

## Slice 0: Contracts and repository foundation

- [ ] Add Python/TypeScript workspace manifests and CI commands.
  - Acceptance: clean installs, lint, type-check, unit-test and build commands
    exist without importing the legacy package.
  - Verify: `uv run pytest -q`, `uv run ruff check .`, `uv run pyright`, and
    `pnpm --dir apps/admin-web build`.
- [ ] Implement shared event, job, session, sandbox, Todo, Tool, evidence,
  Subagent, and outcome contracts.
  - Acceptance: contracts reject invalid transitions and serialize stable JSON.
  - Verify: contract tests and generated JSON schemas.
- [ ] Add PostgreSQL schema and repository ports.
  - Acceptance: a fresh database migrates up and down; job claims are atomic.
  - Verify: PostgreSQL integration tests.

## Slice 1: Webhook to durable ticket job

- [ ] Implement processing-rule evaluation with tests first.
- [ ] Implement webhook deduplication and minimal `ticket_id` enqueue.
- [ ] Implement deterministic `human` cancellation events.
- [ ] Add ingress health and metrics endpoints.

Checkpoint: duplicate and nonmatching webhooks cannot start Agent work.

## Slice 2: Sandbox lifecycle proof

- [ ] Implement Docker Engine abstraction and fake test engine.
- [ ] Create `0.5 CPU / 1 GiB` ephemeral sandbox for one claimed job.
- [ ] Destroy sandboxes on every terminal and exceptional path.
- [ ] Implement orphan sweeper and one `1 CPU / 1 GiB` resource retry.

Checkpoint: lifecycle integration tests prove no terminal sandbox survives.

## Slice 3: Minimal Harness vertical path

- [ ] Fetch current ticket from a fake Zendesk adapter using only `ticket_id`.
- [ ] Resolve active question separately from clarification answers.
- [ ] Generate a typed Todo plan.
- [ ] Execute one fake Tool through the Tool Loop and admit typed evidence.
- [ ] Return a structured answer outcome to the controller.

Checkpoint: webhook -> job -> sandbox -> Todo -> Tool -> outcome -> destroy.

## Slice 4: Context, Skills, real Tool SDK

- [ ] Implement Context Governor and checkpoints.
- [ ] Implement progressive Skill registry/loader.
- [ ] Implement Tool manifest, executor and result normalization SDK.
- [ ] Implement sandbox artifact storage for large outputs.

## Slice 5: Subagents and memory

- [ ] Implement explicit independent Subagent model configuration.
- [ ] Implement depth-one child sessions and task lifecycle.
- [ ] Aggregate child evidence and bounded reports.
- [ ] Implement working memory and reviewed long-term-memory proposals.

## Slice 6: Fresh, idempotent delivery

- [ ] Refresh latest ticket event before sending.
- [ ] Append the localized footer exactly once.
- [ ] Implement transactional reply outbox and receipt reconciliation.
- [ ] Cancel delivery when a concurrent `human` event arrives.

## Slice 7: Practical management console

- [ ] Overview page and Agent desired-state controls.
- [ ] Harness configuration and independent model editors.
- [ ] Processing-rules and localized-footer editor.
- [ ] Skill/tool registry page.
- [ ] End-to-end run detail page.

## Slice 8: Four images and release bundle

- [ ] Multi-stage Dockerfile with four image targets.
- [ ] Compose deployment and PostgreSQL migrations job.
- [ ] Per-image readiness and a combined release manifest.
- [ ] Immutable tag/digest deployment and rollback scripts.

## Slice 9: Replay, canary and cutover

- [ ] Sanitized replay suite for required Zendesk behaviors.
- [ ] No-write live dry-run.
- [ ] Explicit-ticket canary.
- [ ] Cut over ingress while preserving the legacy rollback image.
