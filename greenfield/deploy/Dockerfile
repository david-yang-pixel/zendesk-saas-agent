# syntax=docker/dockerfile:1.7

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
COPY pnpm-workspace.yaml ./
COPY apps/admin-web/package.json apps/admin-web/index.html apps/admin-web/tsconfig.json apps/admin-web/vite.config.ts ./apps/admin-web/
COPY apps/admin-web/src ./apps/admin-web/src
RUN cd apps/admin-web \
    && npm install --no-audit --no-fund --ignore-scripts \
    && npm run build


FROM python-deps AS app-base

ENV PYTHONPATH=/app/packages/contracts/src:/app/packages/persistence/src:/app/packages/agent-core/src:/app/packages/tool-sdk/src:/app/apps/ingress/src:/app/apps/harness/src:/app/apps/sandbox/src:/app/apps/admin-api/src

COPY packages ./packages
COPY apps ./apps
COPY migrations ./migrations
COPY deploy ./deploy

RUN addgroup --system casework \
    && adduser --system --ingroup casework casework \
    && chown -R casework:casework /app
USER casework

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('SERVICE_PORT','8100') + '/healthz', timeout=2)"


FROM app-base AS ingress

ENV SERVICE_PORT=8101
EXPOSE 8101
CMD ["/app/.venv/bin/uvicorn", "casework_ingress.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8101"]


FROM app-base AS harness

ENV SERVICE_PORT=8102
EXPOSE 8102
CMD ["/app/.venv/bin/uvicorn", "casework_harness.service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8102"]


FROM app-base AS sandbox

ENV SERVICE_PORT=8103
EXPOSE 8103
CMD ["/app/.venv/bin/uvicorn", "casework_sandbox.service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8103"]


FROM app-base AS admin

ENV SERVICE_PORT=8104 \
    ADMIN_WEB_DIST=/app/apps/admin-web/dist
COPY --from=admin-web-build --chown=casework:casework /src/apps/admin-web/dist /app/apps/admin-web/dist
EXPOSE 8104
CMD ["/app/.venv/bin/uvicorn", "casework_admin.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8104"]
