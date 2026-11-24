# Digest-pinned multi-stage sandbox image for fastapi-react-postgres (A7-1, A7-4)
# Re-verify base image digests with: python3 scripts/pin_docker_digests.py
# Intentionally contains NO git binary and runs as non-root uid 1000.

FROM node:22-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS node_stage

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Copy Node 22 runtime and corepack/pnpm from digest-pinned node_stage
COPY --from=node_stage /usr/local/bin/node /usr/local/bin/node
COPY --from=node_stage /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
    && corepack enable \
    && corepack prepare pnpm@9.15.4 --activate

# Create writable pre-baked /opt/venv + non-root user (uid 1000) with no git installed (A8-2)
RUN groupadd --gid 1000 foundry \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash foundry \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir \
        uv \
        ruff \
        mypy \
        pytest \
        pytest-asyncio \
        httpx \
        fastapi \
        sqlalchemy \
        alembic \
        pydantic \
        pydantic-settings \
        pyjwt \
        asyncpg \
        pyyaml \
        uvicorn \
        email-validator \
        schemathesis \
    && mkdir -p /workspace/frontend/node_modules /tmp \
    && chown -R 1000:1000 /opt/venv /workspace

ENV HOME=/home/foundry \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/backend \
    MYPYPATH=/workspace/backend \
    RUFF_CACHE_DIR=/tmp/.ruff_cache \
    MYPY_CACHE_DIR=/tmp/.mypy_cache \
    PYTHONPYCACHEPREFIX=/tmp/__pycache__ \
    NODE_OPTIONS="--max-old-space-size=768"

USER 1000:1000
WORKDIR /workspace
