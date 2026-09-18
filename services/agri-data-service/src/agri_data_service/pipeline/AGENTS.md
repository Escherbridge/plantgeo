# Layer L2: Pipeline

## Responsibility
Upstream acquisition, external API fetching, raw tile/data backfill routines (`ingest/` and `historical_*`).

## Dependency Rules
- **May import**: `foundation` (L0), `warehouse` (L1).
- **May NOT import**: `method` (L1), `planes` (L3), `interface` (L4).

## Shared governed source census

`vegetation_source.py` owns the bounded PostgreSQL cell-day census shared by vegetation writers,
operators, and validators. Validation modules may re-export that contract for compatibility, but
sibling validation modules import the lower pipeline module so pytest's layer contract remains
acyclic.

## Availability follows publication, never leads it

`parquet/availability_extension.py` is the only path from a terminal lane-day into that lane's
availability generation, and it runs strictly AFTER the day's completion or governed-absence marker.
See `parquet/AGENTS.md`, "`availability_extension.py` — the terminal day joins the index", for the
write ledger it reads its receipts from, the five typed outcomes, the retry claim, and why a lane
without a bootstrap still completes its days.

`db/vegetation_publication.py` owns the vegetation-wide advisory barrier and durable per-day queue
operations. The 45-day ingestion lookback spans up to 46 inclusive UTC dates; publication rechecks
that boundary every tick and drains durable pending work independently of whether ingestion emitted
a callback in that tick.

## Retained MTBS reconciliation reader

`lanes/burn_severity.py` only reads release-scoped PostgreSQL rows and conforms them to the
registered schema for `validation/burn_severity.py`. Empty results describe that database query;
they do not prove upstream absence. Its unregistered PostgreSQL-to-Parquet exporter was removed
on 2026-09-10. Publication belongs to `direct/burn_severity/adapter.py`, whose source evidence,
all-rung finalization and bounded parts remain unchanged. The SQL and reconciliation reader stay
until their own retirement proof is discharged.

## Source bindings

`source_bindings.py` is the one table mapping a region manifest's `source_slug` to the coverage
claim the source implementation declares, and it is the only thing that knows all three of
`burn_severity/mtbs.py`, `drought/usdm.py` and `soil_survey/ssurgo.py` at once.

It sits at `pipeline/` root rather than in `pipeline/direct/` because every module directly inside
`pipeline/direct/` IS a lane under `layer-lanes.md` §1, and a registry that imports three lanes
would be a cross-lane import three times over
(`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`). One level up, it is
ordinary pipeline-layer wiring.

Its imports are inside the function, not at module scope: each source module pulls its layer's
ingest transport and lane registry, and the sole caller is `app.py`'s boot check. Paying for all of
`pipeline/direct/` merely to name the registry would put a heavy third edge into the application's
import graph for no benefit.
