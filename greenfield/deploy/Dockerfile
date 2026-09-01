FROM python:3.12-slim AS python-deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM node:22-alpine AS admin-web-build

WORKDIR /src
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/admin-web/package.json apps/admin-web/index.html apps/admin-web/tsconfig.json apps/admin-web/vite.config.ts ./apps/admin-web/
RUN pnpm install --frozen-lockfile
COPY apps/admin-web/src ./apps/admin-web/src
RUN pnpm --dir apps/admin-web build


FROM python-deps AS app-base

ENV PYTHONPATH=/app/packages/contracts/src:/app/packages/persistence/src:/app/packages/agent-core/src:/app/packages/tool-sdk/src:/app/apps/ingress/src:/app/apps/harness/src:/app/apps/sandbox/src:/app/apps/admin-api/src

RUN addgroup --system casework \
    && adduser --system --ingroup casework casework \
    && chown -R casework:casework /app
USER casework

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('SERVICE_PORT','8100') + '/readyz', timeout=2)"


FROM app-base AS ingress

ENV SERVICE_PORT=8101
COPY --chown=casework:casework packages/contracts ./packages/contracts
COPY --chown=casework:casework packages/persistence ./packages/persistence
COPY --chown=casework:casework apps/ingress ./apps/ingress
EXPOSE 8101
CMD ["/app/.venv/bin/uvicorn", "casework_ingress.composition:create_app", "--factory", "--host", "0.0.0.0", "--port", "8101"]


FROM app-base AS harness

ENV SERVICE_PORT=8102
COPY --chown=casework:casework packages/contracts ./packages/contracts
COPY --chown=casework:casework packages/persistence ./packages/persistence
COPY --chown=casework:casework apps/harness ./apps/harness
COPY --chown=casework:casework migrations ./migrations
COPY --chown=casework:casework alembic.ini ./alembic.ini
COPY --chown=casework:casework deploy/migrate.py ./deploy/migrate.py
EXPOSE 8102
CMD ["/app/.venv/bin/uvicorn", "casework_harness.composition:create_app", "--factory", "--host", "0.0.0.0", "--port", "8102"]


FROM app-base AS sandbox

ENV SERVICE_PORT=8103
COPY --chown=casework:casework packages/contracts ./packages/contracts
COPY --chown=casework:casework packages/agent-core ./packages/agent-core
COPY --chown=casework:casework packages/tool-sdk ./packages/tool-sdk
COPY --chown=casework:casework apps/sandbox ./apps/sandbox
COPY --chown=casework:casework skills ./skills
EXPOSE 8103
CMD ["/app/.venv/bin/uvicorn", "casework_sandbox.service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8103"]


FROM app-base AS admin

ENV SERVICE_PORT=8104 \
    ADMIN_WEB_DIST=/app/apps/admin-web/dist
COPY --chown=casework:casework packages/contracts ./packages/contracts
COPY --chown=casework:casework packages/persistence ./packages/persistence
COPY --chown=casework:casework apps/admin-api ./apps/admin-api
COPY --from=admin-web-build --chown=casework:casework /src/apps/admin-web/dist /app/apps/admin-web/dist
EXPOSE 8104
CMD ["/app/.venv/bin/uvicorn", "casework_admin.composition:create_app", "--factory", "--host", "0.0.0.0", "--port", "8104"]
