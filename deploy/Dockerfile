FROM python:3.12-slim

# System deps: psql client (for sql_query), git (for saas_branch_switch), curl+ca (general)
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency manifests first for better Docker layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source + scripts + config
COPY src ./src
COPY scripts ./scripts
COPY config.yaml ./config.yaml

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

# Note: real deployment mounts /resources volume and /app/.env (read-only)
# The CLI 'zd-agent serve' entrypoint starts the aiohttp webhook server
# + scheduler + silence_waker. Make sure that command exists; fall back to
# a module invocation if it doesn't.
CMD ["uv", "run", "zd-agent", "serve"]
