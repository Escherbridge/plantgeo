# strategy-knowledge: the HTTP transport (tool API + MCP streamable HTTP) on Railway's private network.
# Railway builds with services/strategy-knowledge as the context. See AGENTS.md section "Deploy (Railway)".

# Same pinned base image and uv as services/plantgeo-ml-service/Dockerfile and services/agri-data-service/Dockerfile.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# --- runtime stage: lean, --no-dev, non-root. ---
# Quality checks (lint, format, tests) are ad hoc, never a build gate (owner decision 2026-08-07,
# AGENTS.md "Deploy (Railway)"): run `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
# before pushing.
FROM base AS runtime

# Resolve only from the committed lock before adding frequently changing source.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ src/
RUN uv sync --locked --no-dev

# A real, writable HOME: Chroma caches the MiniLM ONNX model under Path.home()/.cache/chroma/onnx_models.
# The corpus cache (raw text, corpus files, Chroma index) is pulled from the bucket into STRATEGY_KB_CACHE_DIR on
# every start; nothing in it is baked into the image.
RUN groupadd --system --gid 10001 strategykb \
    && useradd --system --uid 10001 --gid strategykb --create-home --home-dir /home/strategykb \
      --shell /usr/sbin/nologin strategykb \
    && install --directory --owner strategykb --group strategykb /var/lib/strategy-knowledge

ENV HOME=/home/strategykb \
    STRATEGY_KB_CACHE_DIR=/var/lib/strategy-knowledge

USER strategykb

# Bake the embedding model at BUILD time, as the service user, through the same embedder the service opens, so a
# start never downloads it. The archive is deleted in the same layer; Chroma only checks the extracted files.
RUN python -c "from strategy_knowledge.config import PRODUCTION_EMBEDDING_MODEL; from strategy_knowledge.embedding import embedder_for; vectors = embedder_for(PRODUCTION_EMBEDDING_MODEL).embed(['warm-up']); assert len(vectors) == 1 and len(vectors[0]) == 384, 'unexpected embedding shape'" \
    && rm -f "$HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz" \
    && test -f "$HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/model.onnx"

# Fails the BUILD, not the first request, if the package or the HTTP transport's imports did not land.
RUN python -c "import strategy_knowledge.cli, strategy_knowledge.http_app" \
    && strategy-kb serve --help > /dev/null

EXPOSE 8000

# PORT is pinned to 8000 as a Railway variable (AGENTS.md "Deploy (Railway)"). STRATEGY_KB_BIND_HOST exists only to
# switch the bind address without a rebuild; the default is the contract's 0.0.0.0.
CMD ["/bin/sh", "-c", "exec strategy-kb serve --transport http --host \"${STRATEGY_KB_BIND_HOST:-0.0.0.0}\" --port \"${PORT:-8000}\" --pull-from-bucket"]
