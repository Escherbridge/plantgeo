# plantgeo-ml-service

PlantGeo's machine-learning and Monte Carlo service. It reads governed observed Parquet, writes
`kind=forecast` partitions and its own `ml/` artifacts back to the same bucket, and serves them over
`/api/v1/ml`. Chartered by track `plantgeo_ml_service_20260918`; decisions D1-D8 in that track's
`spec.md` section 1 are settled and are not re-opened here.

## The six layers

```
foundation -> method -> warehouse -> pipeline -> planes -> interface
```

- **`foundation/` (L0)** canonical JSON, digests, finiteness and credential-custody guards. Stdlib
  only; imports nothing first-party but itself.
- **`method/` (L1)** pure domain computation over already-loaded arrays. Three sub-packages: `ml/`
  (estimators), `monte_carlo/` (seeded ensemble forecasters) and `kernels/` (numeric cores with a
  Python reference and, from phase 3, a Mojo implementation). `ml` and `monte_carlo` are siblings
  that never import each other; `kernels` may import `foundation` only.
- **`warehouse/` (L2)** pinned Arrow schemas for the streams read and written (`streams.py`), each
  lane's publication clock (`lanes.py`), and the availability generation and pointer documents
  (`availability.py`).
- **`pipeline/` (L3)** object-store I/O (`object_store.py`), the bounded DuckDB session
  (`duckdb_session.py`), the leakage-gated observed reader (`observed_reader.py`), the availability
  publisher (`availability_publisher.py`), the expert label reader (`expert_labels.py`), and from
  phase 2B feature building and daily lane orchestration.
- **`planes/` (L4)** bounded readers and the Sanic blueprints.
- **`interface/` (L5)** the `plantgeo-ml` click adapter. Outermost; nothing imports it.

`tests/test_layer_import_contract.py` enforces the lattice by AST walk and then imports every layer
module for real. A rule about a package that no longer exists is noise; delete the rule with the
package.

## Two rules that are not style preferences

**Zero Postgres (decision D5).** There is no `DATABASE_URL`, no SQLAlchemy, no Alembic and no `db/`
directory. `config.Settings` scans the process environment and *refuses to construct* while any
variable whose name contains `DATABASE_URL` is present, naming the decision in the error. Fail-closed
rather than ignore-silently: a deployment that inherited the sibling service's variables would
otherwise look configured and quietly grow the dependency the decision removed. Features, labels,
artifacts (canonical JSON, never pickle), receipts and predictions all live in the bucket under the
`ml/` prefix or a lane's `kind=forecast` stream.

**Parity, not import (spec section 4).** This service never imports `agri_data_service`. Knowledge
the two must agree on -- the canonical serializer, the custody guards, and since phase 2A the
object-key grammar, the marker payloads, the stream schemas, the lane clocks and the availability
documents -- is COPIED, and each copy is pinned by a parity test that reads the sibling's module from
the monorepo and asserts identical output. That is what lets the two deploy independently while
making drift fail a sweep instead of corrupting a checksum. See `tests/AGENTS.md` for why each
parity test asserts twice, and for the one value set that cannot cross.

**Writing a partition does not publish it (spec FR-4a).** No forecast day is selectable until its
availability generation and pointer exist at every rung of the ladder. `pipeline/object_store.py`
writes objects; `pipeline/availability_publisher.py` is what makes a day readable, and acceptance
reads back through agri-data-service's `parquet_ops/availability_coverage.py`, never a raw listing.

**A feature never reads past its producer's clock.** `warehouse/lanes.py` carries each lane's
publication lag; `pipeline/observed_reader.py` refuses, by name, any window that reaches past
`as_of - lag`. It refuses rather than narrowing the window, because a quietly shorter window scores
better than the live lane ever can.

## Where rationale lives

Per-directory `AGENTS.md` files carry the "why": `foundation/`, `method/` (and `method/ml`,
`method/monte_carlo`, `method/kernels`), `warehouse/`, `pipeline/`, `planes/`, `interface/`,
`scripts/` and `tests/`. Source carries terse one-line doc-comments only. Operational state,
outstanding work and the continuation plan live in `RUNBOOK.md`. Track-level decisions live in
`conductor/tracks/plantgeo_ml_service_20260918/`.

## Tripwires (carried from the track plan)

- `random_seed` is recorded on every forecast row; an unseeded ensemble does not ship.
- The `signal` lane's `cell_id` resolves through a dimension this service does not have, so every
  ML-written row carries `cell_longitude` and `cell_latitude`.
- Bin latitudes with `round(lat / base)` integers, never IEEE division: Polars division is
  frame-length dependent and flips cells at envelope edges.
- The quality receipt is refreshed by the sweep, never edited; a CRLF tree writes a different digest.
- A Mojo function imported from Python takes at most six arguments, so a kernel passes one bundle.
- Never `git mv` while two agents share the index; copy and delete, and let rename detection work.
- Bash heredocs containing backticks write nothing in this harness; use a file write.
