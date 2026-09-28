# Same locked quality receipt gate as services/agri-data-service/Dockerfile, because this image
# copies the same Python source. It verifies that the sha256 over the digest set
# (`services/agri-data-service/scripts/quality_receipt.py::DIGEST_DIRECTORIES` and `DIGEST_FILES`,
# the one statement of it) still equals the digest the last all-green
# `scripts/check.py --write-receipt` sweep recorded, so an image cannot be built from an unjudged
# tree. `digest_input_paths` silently skips a directory that is not there, so a digest input left
# out of this stage reads as a stale receipt rather than a missing COPY;
# `tests/lane_config/test_image_plumbing.py` pins every entry here (spec CA4). Every digest input
# has to be copied in for the digest to reproduce, which is why the migration machinery appears
# HERE -- the executor runtime stage below deliberately ships none of it, and must keep shipping
# none of it. `tests/` and `scripts/` are digest inputs and stay in THIS stage too; the final stage
# copies the verified receipt out, which is also what stops BuildKit pruning the gate as
# unreferenced. The digest hashes CRLF-normalized (LF) content, not raw disk bytes: git materializes
# the committed bytes on checkout, and the normalization is what makes a receipt written on any
# platform agree here.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS quality-receipt

WORKDIR /app/agri-service

COPY services/agri-data-service/pyproject.toml services/agri-data-service/uv.lock ./
COPY services/agri-data-service/QUALITY_RECEIPT.json ./
COPY services/agri-data-service/mypy.ini services/agri-data-service/ruff.toml services/agri-data-service/alembic.ini ./
COPY services/agri-data-service/src/ src/
COPY services/agri-data-service/tests/ tests/
COPY services/agri-data-service/scripts/ scripts/
COPY services/agri-data-service/alembic/ alembic/
COPY services/agri-data-service/db/ db/
COPY services/agri-data-service/lanes/ lanes/
RUN python scripts/verify_quality_receipt.py


FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/agri-service/.venv/bin:$PATH" \
    NEXT_TELEMETRY_DISABLED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libatomic1 libexpat1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/agri-service

COPY services/agri-data-service/pyproject.toml services/agri-data-service/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY services/agri-data-service/src/ src/
COPY services/agri-data-service/scripts/complete_partial_ladders.py scripts/complete_partial_ladders.py
RUN uv sync --locked --no-dev

# The executor dispatches config lanes from these TOMLs (spec S13, CA4); beside `src/` so
# `default_lanes_directory` resolves `/app/agri-service/lanes` with no setting.
COPY services/agri-data-service/lanes/ lanes/

# The proof this image's Python source passed a green sweep, taken from the stage that verified it.
COPY --from=quality-receipt /app/agri-service/QUALITY_RECEIPT.json ./QUALITY_RECEIPT.json

RUN python -c "from agri_data_service.parquet_ops.duckdb_session import install_serving_extensions; install_serving_extensions()" \
    && groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo \
    && chown -R plantgeo:plantgeo /opt/duckdb-extensions

USER plantgeo

RUN python -c "import rasterio; from agri_data_service.parquet_ops.duckdb_session import open_guarded_connection; open_guarded_connection().close()"

CMD ["agri-service", "ops", "jobs-executor"]
