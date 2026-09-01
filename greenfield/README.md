# ZendeskCasework Greenfield

Independent Greenfield implementation of the three-layer ZendeskCasework
system described in [docs/spec.md](docs/spec.md).

This tree must not import from or depend on the legacy `src/zd_agent` package.

## Services

- `casework-ingress`: Zendesk webhook and deterministic processing rules.
- `casework-harness`: durable job consumer, sandbox controller and delivery.
- `casework-sandbox`: ephemeral per-run Agent Harness runtime.
- `casework-admin`: practical management API and Vue console.

## Development

```bash
uv sync --dev
uv run pytest -q
uv run ruff check .
uv run pyright

corepack enable
pnpm --dir apps/admin-web install
pnpm --dir apps/admin-web test
pnpm --dir apps/admin-web build
```
