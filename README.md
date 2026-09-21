# ZD Agent (Zendesk SaaS Agent)

Modern event-driven, sandbox-isolated Zendesk AI Agent system.
Provides automated ticket analysis, multi-source evidence investigation, and customer inquiry response generation.

## Architecture & Services

The project is structured as a modern Polyglot Monorepo:

### Applications (`apps/`)
- `apps/ingress`: Zendesk webhook ingestion and deterministic triage / admission rules (FastAPI / Python).
- `apps/pi-agent`: AI casework reasoning engine worker (TypeScript / Node.js).
- `apps/admin-api`: Management & observability API, MCP service mesh probe (FastAPI / Python).
- `apps/admin-web`: Management console UI with live trace player & MCP mesh monitor (Vue 3 / Vite).
- `apps/feishu-mcp`: Legacy Feishu MCP adapter retained for compatibility; it is not part of the production investigation path.
- `apps/harness`: Durable queue consumer, sandbox controller and delivery engine (Python).
- `apps/sandbox`: Ephemeral per-run sandbox runtime for isolated tool executions (Python).

### Shared Packages (`packages/`)
- `packages/contracts`: Versioned schemas and JSON message contracts across all services.
- `packages/persistence`: PostgreSQL SQLAlchemy models and repository layer.
- `packages/agent-core`: State machine, context budget, and core casework reasoning domain.
- `packages/tool-sdk`: MCP tool interfaces, evidence extraction, and loop adapters.

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
pnpm --dir apps/pi-agent build

docker build -f deploy/Dockerfile --target sandbox -t casework-sandbox:dev .
docker compose -f deploy/compose.yaml up --build
```

## Production & Deployment (`deploy/`)

- **Release Orchestration**: Production runs via `deploy/compose.release.yaml`, managing PostgreSQL, Ingress, Pi-Agent, and Admin.
- **Worker Reasoning**: `apps/pi-agent` operates as the primary concurrency worker, consuming admissions from PostgreSQL, executing tool calls via the MCP Mesh, and delivering replies through the Zendesk Browser Relay.
- **Tool Boundary**: OpenConnector is the sole read-only investigation gateway (including Zendesk, Dify DB, SigNoz, source, docs, GitHub MCP, and approved native providers). Zendesk Browser Relay is the separate and only public-reply write path; Gmail is excluded.
- **Zero-Secret Boundary**: Production credentials are mount-injected via `/run/casework-credentials/credentials.env` and never committed to the repository. The Admin console allows hot-updating model endpoints and tokens without container rebuilds.

The production source environment must provide `OPENCONNECTOR_RUNTIME_TOKEN`. `prepare_runtime.py` stores it as `CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME` and removes legacy per-MCP credentials from the mounted Pi Agent credential file. Configure the OpenConnector token and every granted connection as read-only. The five GitHub semantic read tools must also be classified as `read` in the `github-mcp` connection's trusted `toolOperationTypes`; the Agent refuses them if live discovery reports `write`.
