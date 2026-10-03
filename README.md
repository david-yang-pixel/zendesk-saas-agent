# ZD Agent (Zendesk SaaS Agent)

Event-driven Zendesk AI Agent for Dify Cloud support.
Provides automated ticket analysis, read-only multi-source evidence investigation, and customer reply generation.
See [docs/spec.md](docs/spec.md) for the end-to-end design.

## Architecture & Services

The project is structured as a modern Polyglot Monorepo:

### Applications (`apps/`)
- `apps/ingress`: Zendesk webhook ingestion and deterministic triage / admission rules (FastAPI / Python).
- `apps/pi-agent`: AI casework reasoning engine worker (TypeScript / Node.js).
- `apps/admin-api`: Management & observability API, MCP service mesh probe (FastAPI / Python).
- `apps/admin-web`: Management console UI with live trace player & MCP mesh monitor (Vue 3 / Vite).

### Shared Packages (`packages/`)
- `packages/contracts`: Versioned schemas and JSON message contracts across all services.
- `packages/persistence`: PostgreSQL SQLAlchemy models and repository layer.

### Skills (`skills/`)
- Agent persona definitions, OpenConnector routing, and specialized investigation instructions (`core-casework`, `openconnector-routing`, `dify-saas-support`, `dify-db-investigation`, `app-run-investigation`).

## Development

```bash
# Python services & test suite
uv sync --dev
uv run pytest -q
uv run ruff check .

# Frontend & Node services (pnpm workspace)
corepack enable
pnpm install
pnpm --dir apps/admin-web test
pnpm --dir apps/admin-web build
pnpm --filter pi-agent test
pnpm --dir apps/pi-agent build

# Local stack: export OPENCONNECTOR_RUNTIME_TOKEN and OPENAI_API_KEY first.
docker compose -f deploy/compose.yaml up --build
```

### Read-only investigation evaluation

After building Pi Agent, run `pnpm --dir apps/pi-agent eval:investigation education`
(or `product` / `bug`) with the runtime's existing mounted credentials and model
configuration. This uses the real model and allowed read-only OpenConnector tools,
so it consumes API quota. The education scenario only reads the approved test ticket
`#3726`; product and bug scenarios use independent questions. The script does not
load the Worker, enqueue jobs, send replies/notes, or sync evaluation runs.

Output is metadata only: executed actions, reviewed evidence phases, failures,
duration and acceptance. Passing requires an English unsent draft, actual relevant
body reads, and no pending evidence reviews. Explicit unresolved evidence is allowed;
it is not a verified fact. A pass tests retrieval behavior, not the semantic truth of
every conclusion or parity with a human investigator. Use the recorded failures and
latency to decide whether a change is ready for production.

## Production & Deployment (`deploy/`)

- **Release Orchestration**: Production runs via `deploy/compose.release.yaml`, managing PostgreSQL, Ingress, Pi-Agent, and Admin. CI deploys the three application images by digest; Compose refuses to start without them, and every container runs the code baked into its image. `deploy/rollback-production.sh` restores the images recorded in `previous-release.env`.
- **Worker Reasoning**: `apps/pi-agent` operates as the primary concurrency worker, consuming admissions from PostgreSQL, executing tool calls via the MCP Mesh, and delivering replies through the Zendesk Browser Relay.
- **Tool Boundary**: OpenConnector is the sole read-only investigation gateway (including Zendesk, Dify DB, SigNoz, source, docs, GitHub MCP, and approved native providers). Zendesk Browser Relay is the separate and only public-reply write path; Gmail is excluded.
- **Zero-Secret Boundary**: Production credentials are mount-injected via `/run/casework-credentials/credentials.env` and never committed to the repository. The Admin console allows hot-updating model endpoints and tokens without container rebuilds.
- **Feishu Alerts**: Human-takeover and failure alerts go to the Feishu bot in `FEISHU_WEBHOOK_URL`. Set or rotate it with the GitHub production secret `FEISHU_WEBHOOK_URL`; without that secret, the value already saved in `runtime.env` is kept (the server source env only seeds a first deployment). Alerts are disabled when it is unset.

The production source environment must provide `OPENCONNECTOR_RUNTIME_TOKEN`. `prepare_runtime.py` stores it as `CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME` and removes legacy per-MCP credentials from the mounted Pi Agent credential file. Configure the OpenConnector token and every granted connection as read-only. The five GitHub semantic read tools must also be classified as `read` in the `github-mcp` connection's trusted `toolOperationTypes`; the Agent refuses them if live discovery reports `write`.
