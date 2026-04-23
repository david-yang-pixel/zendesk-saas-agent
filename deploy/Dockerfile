FROM python:3.12-slim

# 国内镜像源：python:3.12-slim (Debian bookworm) 用 deb822 格式
# 替换为阿里云，避免默认 deb.debian.org 在某些国内机房不稳
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi

# System deps: psql client (sql_query), git (saas_branch_switch), curl+ca (general)
# gcc + python3-dev 有些平台 pexpect 装需要 — slim 镜像已含构建基础，不额外装
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# pip 换国内源（uv install 会用到 pip）
RUN pip config --global set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip config --global set global.trusted-host mirrors.aliyun.com

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

WORKDIR /app

# uv 也走阿里云 mirror
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    UV_INDEX_STRATEGY=unsafe-best-match

# Copy dependency manifests first for better Docker layer caching
# --no-install-project: 只装依赖，不构建本项目（src 还没 COPY）
# 不使用 --frozen：uv.lock 里 zendesk-cli 源可能是旧 SSH URL
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

# Copy source + scripts + config + README（hatchling 需要）
COPY src ./src
COPY scripts ./scripts
COPY config.yaml ./config.yaml
COPY README.md ./README.md

# 再装本项目（依赖已 cache，只加项目本身）
RUN uv sync --no-dev

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

# Note: real deployment mounts /resources volume and /app/.env (read-only)
# The CLI 'zd-agent serve' entrypoint starts the aiohttp webhook server
# + scheduler + silence_waker. Make sure that command exists; fall back to
# a module invocation if it doesn't.
CMD ["uv", "run", "zd-agent", "serve"]
