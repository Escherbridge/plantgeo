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

# --- runtime stage: lean, --no-dev. No Mojo stage yet; that arrives in phase 3. ---
FROM base AS runtime

# Resolve only from the committed lock before adding frequently changing source.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ src/
RUN uv sync --locked --no-dev

# The proof this image's source passed a green sweep, taken from the stage that verified it so the
# gate cannot be skipped. Kept in the image so a running container can name its own quality receipt.
COPY --from=quality-receipt /app/QUALITY_RECEIPT.json ./QUALITY_RECEIPT.json

RUN groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo

USER plantgeo

# Fails the BUILD, not the first request, if the wheel did not land where the interpreter looks.
RUN python -c "import plantgeo_ml_service; print(plantgeo_ml_service.__version__)"

EXPOSE 8000

CMD ["/bin/sh", "-c", "exec sanic plantgeo_ml_service.app:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\""]
