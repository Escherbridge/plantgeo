# Layer L4: Interface

## Responsibility
Command-line interface wiring (`interface/cli`) and Sanic HTTP API routes (`interface/http`).

## Dependency Rules
- **May import**: All lower layers (`foundation` L0, `method` L1, `warehouse` L1, `pipeline` L2, `planes` L3).

## Invariants
- `agri-cli` entry point string `agri_data_service.cli:cli` in `pyproject.toml` remains unchanged and functional.
- All 52 CLI leaf commands must remain byte-compatible and callable without alterations.
- No `ingest-*` command import path reads `alembic.ini` or touches `db/agri/**` at import time.
