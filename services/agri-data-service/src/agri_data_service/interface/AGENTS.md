# Layer L4: Interface

## Responsibility
Command-line interface wiring (`interface/cli`) and Sanic HTTP API routes (`interface/http`).

## Dependency Rules
- **May import**: All lower layers (`foundation` L0, `method` L1, `warehouse` L1, `pipeline` L2, `planes` L3).

## Invariants
- `agri-service` is the only console script and resolves `agri_data_service.interface.cli:cli`.
- The four command families are `forecast`, `ml`, `data`, and `ops`; the former flat surface has no aliases.
- Every leaf command is registered once and delegates business behavior to a lower layer.
- No `ingest-*` command import path reads `alembic.ini` or touches `db/agri/**` at import time.
