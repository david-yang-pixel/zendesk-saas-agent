FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps/admin-api/src:/app/apps/ingress/src:/app/packages/contracts/src:/app/packages/persistence/src

RUN groupadd --system --gid 10001 casework \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app casework \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps

RUN uv sync --frozen --no-dev --no-editable

FROM node:22-alpine AS admin-web-build

WORKDIR /src
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/admin-web/package.json ./apps/admin-web/
RUN pnpm --dir apps/admin-web install --frozen-lockfile
COPY apps/admin-web ./apps/admin-web
RUN pnpm --dir apps/admin-web build

FROM base AS ingress

USER casework
WORKDIR /app
COPY --from=builder --chown=casework:casework /app/.venv /app/.venv
COPY --chown=casework:casework pyproject.toml uv.lock ./
COPY --chown=casework:casework packages ./packages
COPY --chown=casework:casework deploy ./deploy
COPY --chown=casework:casework apps/ingress ./apps/ingress
EXPOSE 8101
CMD ["/app/.venv/bin/uvicorn", "casework_ingress.composition:create_app", "--factory", "--host", "0.0.0.0", "--port", "8101"]

FROM base AS admin

WORKDIR /app
COPY --from=builder --chown=casework:casework /app/.venv /app/.venv
COPY --chown=casework:casework pyproject.toml uv.lock alembic.ini ./
COPY --chown=casework:casework migrations ./migrations
COPY --chown=casework:casework packages ./packages
COPY --chown=casework:casework deploy ./deploy
COPY --chown=casework:casework skills ./skills
COPY --chown=casework:casework apps/admin-api ./apps/admin-api
COPY --from=admin-web-build --chown=casework:casework /src/apps/admin-web/dist /app/apps/admin-web/dist
EXPOSE 8104
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "casework_admin.composition:create_app", "--factory", "--host", "0.0.0.0", "--port", "8104"]

FROM node:22-alpine AS pi-agent

ENV NODE_ENV=production \
    SERVICE_PORT=8102

WORKDIR /app
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/pi-agent/package.json ./apps/pi-agent/
RUN pnpm --dir apps/pi-agent install --frozen-lockfile
COPY apps/pi-agent ./apps/pi-agent
COPY skills ./skills
RUN pnpm --dir apps/pi-agent build

EXPOSE 8102
CMD ["node", "apps/pi-agent/dist/worker.js"]
