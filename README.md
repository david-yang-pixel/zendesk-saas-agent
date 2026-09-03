# ZD Agent (Zendesk SaaS Agent)

Modern event-driven, sandbox-isolated Zendesk AI Agent system.
Provides automated ticket analysis, multi-source evidence investigation, and customer inquiry response generation.

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

docker build -f deploy/Dockerfile --target sandbox -t casework-sandbox:dev .
docker compose -f deploy/compose.yaml up --build
```

`casework-sandbox` is an image only, not a long-running Compose service. The
Harness creates a fresh container for each run and removes it on every terminal
path. Build that image before starting the long-running services.

On Linux, set `DOCKER_GID` to the numeric group owner of
`/var/run/docker.sock` before starting the Harness. The release Compose file
requires it explicitly so the non-root Harness process can create and destroy
ephemeral Sandbox containers. Release manifests render image, source and
configuration revisions; database credentials remain externally injected.

The Harness forwards only the explicit Sandbox adapter allowlist
(`ZENDESK_*`, `CASEWORK_MODEL_BASE_URL`, `CASEWORK_TOOL_CONFIG`, and
`CASEWORK_CREDENTIAL_*`) to each ephemeral container. Model configuration stores
only credential references. The Admin console validates and atomically replaces
model credentials in the dedicated directory selected by
`CASEWORK_CREDENTIAL_DIR`, while returning masked status only. Each new Sandbox
reloads that file. Do not put provider keys in versioned configuration or the
release manifest. The Harness never forwards `DATABASE_URL` or unrelated
process secrets.
