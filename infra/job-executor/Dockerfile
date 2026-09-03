FROM node:22.16.0-bookworm-slim AS soilgrids-dependencies

WORKDIR /app/plantgeo

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && node -e "require.resolve('jiti')"


# Same locked quality receipt gate as services/agri-data-service/Dockerfile, because this image
# copies the same Python source. It verifies that the sha256 over `src/**`, `tests/**`,
# `scripts/**`, `pyproject.toml` and `uv.lock` still equals the digest the last all-green
# `scripts/check.py --write-receipt` sweep recorded, so an image cannot be built from an unjudged
# tree. `tests/` and `scripts/` are digest inputs and stay in THIS stage; the final stage copies the
# verified receipt out, which is also what stops BuildKit pruning the gate as unreferenced. The
# digest hashes CRLF-normalized (LF) content, not raw disk bytes, so a receipt written on a Windows
# checkout still verifies against this Linux build context.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS quality-receipt

WORKDIR /app/agri-service

COPY services/agri-data-service/pyproject.toml services/agri-data-service/uv.lock ./
COPY services/agri-data-service/QUALITY_RECEIPT.json ./
COPY services/agri-data-service/src/ src/
COPY services/agri-data-service/tests/ tests/
COPY services/agri-data-service/scripts/ scripts/
RUN python scripts/verify_quality_receipt.py


FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/
COPY --from=soilgrids-dependencies /usr/local/bin/node /usr/local/bin/node

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/agri-service/.venv/bin:$PATH" \
    NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1 \
    JITI_FS_CACHE=false

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libatomic1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/agri-service

COPY services/agri-data-service/pyproject.toml services/agri-data-service/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY services/agri-data-service/src/ src/
RUN uv sync --locked --no-dev

# The proof this image's Python source passed a green sweep, taken from the stage that verified it.
COPY --from=quality-receipt /app/agri-service/QUALITY_RECEIPT.json ./QUALITY_RECEIPT.json

COPY --from=soilgrids-dependencies /app/plantgeo/node_modules /app/plantgeo/node_modules
COPY tsconfig.json /app/plantgeo/tsconfig.json
COPY src/ /app/plantgeo/src/
COPY scripts/warm-soilgrids.mjs /app/plantgeo/scripts/warm-soilgrids.mjs

RUN python -c "from agri_data_service.parquet_ops.duckdb_session import install_serving_extensions; install_serving_extensions()" \
    && groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo \
    && chown -R plantgeo:plantgeo /opt/duckdb-extensions

USER plantgeo

RUN python -c "from agri_data_service.parquet_ops.duckdb_session import open_guarded_connection; open_guarded_connection().close()" \
    && node -e "require.resolve('jiti', { paths: ['/app/plantgeo'] })"

CMD ["agri-service", "ops", "jobs-executor"]
