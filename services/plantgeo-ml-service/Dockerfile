# Declared BEFORE the first FROM so it is global: an ARG declared after a FROM belongs to that
# stage only and `FROM mojo-kernels-${MOJO_KERNELS}` below would expand to an invalid name
# (deployment 4a4dac5f failed to parse for exactly that reason on 2026-09-19).
ARG MOJO_KERNELS=disabled

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# --- quality-receipt gate: refuse a tree whose locked receipt was not refreshed by a green sweep. ---
#
# This stage does not RE-RUN the checks; it verifies that the sha256 over `src/**`, `tests/**`,
# `scripts/**`, `pyproject.toml`, `uv.lock`, `mypy.ini` and `ruff.toml` still equals the digest
# `scripts/check.py --write-receipt` recorded after its last all-green run. Editing source without
# re-running the sweep therefore fails the BUILD instead of shipping unjudged code.
#
# `tests/` and `scripts/` are digest inputs, so they are copied HERE and never into `runtime` -- a
# deleting `RUN` would not shrink an already-written layer, so the only way to keep tests out of the
# production image is to keep them out of its stage. `runtime` copies the verified receipt back out,
# and that COPY is what forces this stage to build at all: BuildKit prunes an unreferenced stage,
# and a pruned gate is not a gate.
#
# The verifier is deliberately stdlib-only and never calls git, so it runs on the base interpreter
# with no virtualenv.
FROM base AS quality-receipt

COPY pyproject.toml uv.lock QUALITY_RECEIPT.json ./
COPY mypy.ini ruff.toml ./
COPY src/ src/
COPY tests/ tests/
COPY scripts/ scripts/
RUN python scripts/verify_quality_receipt.py

# --- Mojo kernels (spec FR-9, phase 3), OFF by default. ---
#
# `--build-arg MOJO_KERNELS=enabled` compiles `kernels/*.mojo` into the extension modules
# `method/kernels/_native/` holds. OFF is the default on purpose, and the reason is the tripwire
# itself: Mojo is an optimisation and never a dependency, so an unreachable
# `https://conda.modular.com/max` must not be able to fail a deploy of a live service. The stage
# downloads roughly 1 GB of toolchain and is discarded; only three ~150 KB `.so` files reach the
# runtime image. Proven end to end in WSL2 on 2026-09-19 (`evidence/phase3-parity.json`); NOT yet
# proven inside a Railway build, because this box builds no container images.
#
# Even with the kernels present the runtime still defaults `PLANTGEO_ML_KERNELS=python`. Turning
# them on is a separate, reversible Railway variable change, taken only once a parity receipt from
# the image itself is in evidence.
# Re-declared without a value so the stage-selecting FROM below sees the global default or the
# build-arg override; the global declaration at the top of this file is the one that matters.
ARG MOJO_KERNELS

FROM base AS mojo-kernels-disabled

# An empty directory so the runtime `COPY` has something to copy and the two paths stay identical.
RUN mkdir -p /kernels

FROM base AS mojo-kernels-enabled

ARG PIXI_VERSION=0.81.0
ENV PIXI_HOME=/opt/pixi \
    PATH="/opt/pixi/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://pixi.sh/install.sh | PIXI_VERSION="v${PIXI_VERSION}" bash

WORKDIR /build
# `pixi.lock` is copied WITH `pixi.toml` and is not optional: `--locked` below refuses to run when
# the lock does not satisfy the manifest, so a hand-edited `pixi.toml` fails the BUILD instead of
# silently re-solving and shipping a different Mojo compiler than the one the parity receipt in
# `evidence/phase3-parity.json` names. The lock is committed; see RUNBOOK "the lock pins the
# toolchain".
COPY pixi.toml pixi.lock ./
COPY kernels/ kernels/
COPY scripts/ scripts/
COPY src/ src/
COPY tests/ tests/

# `probe-kernels` depends on `build-kernels`, so this one command compiles the kernels AND fails
# the build unless they reproduce the Python reference on the fixture cases. A Mojo build that
# disagrees with the reference never reaches a registry.
RUN pixi run --locked build-kernels \
    && pixi run --locked probe-kernels \
    && mkdir -p /kernels \
    && cp src/plantgeo_ml_service/method/kernels/_native/*.so /kernels/

FROM mojo-kernels-${MOJO_KERNELS} AS mojo-kernels

# --- runtime stage: lean, --no-dev. ---
FROM base AS runtime

# Resolve only from the committed lock before adding frequently changing source.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ src/

# The compiled kernels, or an empty directory when `MOJO_KERNELS=disabled`. It lands BEFORE
# `uv sync` so the extension modules are inside the package the wheel installs.
COPY --from=mojo-kernels /kernels/ src/plantgeo_ml_service/method/kernels/_native/

RUN uv sync --locked --no-dev

# The proof this image's source passed a green sweep, taken from the stage that verified it so the
# gate cannot be skipped. Kept in the image so a running container can name its own quality receipt.
COPY --from=quality-receipt /app/QUALITY_RECEIPT.json ./QUALITY_RECEIPT.json

RUN groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo

# Both DuckDB extensions are LOADED and never INSTALLED on a run path, so the image installs them
# here -- the same step `infra/cron/Dockerfile` already carries. Without it EVERY `/api/v1/ml`
# partition read refuses with `DuckDbExtensionError` rendered as `serving_fault`: the correct
# failure, and also every read (see `pipeline/duckdb_session.py`). The directory is the one
# `config.Settings.duckdb_extension_directory` names, and it is owned by the service user because
# a serving session opens read-only and must not be the thing that first creates it.
# The rollback is the default, in the image as well as in `config.Settings`: even an image that
# compiled the kernels runs the Python reference until a Railway variable says otherwise.
ENV PLANTGEO_ML_KERNELS=python

ENV PLANTGEO_ML_DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb-extensions
RUN python -c "from plantgeo_ml_service.pipeline.duckdb_session import install_extensions; install_extensions(directory='/opt/duckdb-extensions')" \
    && chown -R plantgeo:plantgeo /opt/duckdb-extensions \
    && chmod -R a+rX /opt/duckdb-extensions

USER plantgeo

# Fails the BUILD, not the first request, if the wheel did not land where the interpreter looks.
RUN python -c "import plantgeo_ml_service; print(plantgeo_ml_service.__version__)"

# Fails the BUILD, not the first read, if the extensions did not land where the SESSION looks. The
# probe runs as the service user and opens the same guarded connection a request would.
RUN python -c "from plantgeo_ml_service.pipeline.duckdb_session import open_guarded_connection; open_guarded_connection().close()"

EXPOSE 8000

CMD ["/bin/sh", "-c", "exec sanic plantgeo_ml_service.app:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\""]
