---
type: track-spec
title: "Postgres shrink via ingest repoint to Parquet — bridge, then cut per lane"
description: >-
  Restore a writer to the forward path immediately, then move each of the twelve
  lanes from "upstream -> Postgres -> Parquet" to "upstream -> Parquet directly",
  verifying row-level parity before deleting that lane's Postgres write path.
  Collapse the alembic history to a greenfield baseline that matches production
  as it actually is. Shrink Postgres only after serving no longer reads it.
tags: [feature, postgres_shrink_ingest_repoint_20260825, active]
status: active
timestamp: 2026-08-25
resource: ./metadata.json
---

# Postgres shrink via ingest repoint to Parquet

## Historical premise at charter

The statements in this section describe the verified 2026-08-25 starting point. The 2026-08-27
execution checkpoint below supersedes them for current scheduling.

Production Postgres is 38 GB and 96.6% of it is three relations that the Parquet
warehouse already holds a 92×-smaller copy of. The shrink is not blocked on
courage; it is blocked on two facts:

1. **Nothing ingested at charter time.** Both runners (`plantgeo-parquet-drain`,
   `plantgeo-ingest-cron`) are down, and `infra/cron-ingest/railway.json` has no
   `cronSchedule`, so the forward path runs exactly once per `git push` and never
   otherwise.
2. **Parquet was a derivative of Postgres.** Every one of the twelve lane adapters
   in `pipeline/parquet/lane_registry.py` reads *from* Postgres. Deleting the
   Postgres ingest path today severs the thing that produces Parquet.

This track executes the owner's 2026-08-25 shape: **bridge, then cut per lane.**
Restore the cron as a transition writer, build direct-from-source Parquet writers
lane by lane, retire each lane's Postgres path only after row-level parity is
proven, and take the final shrink only when the serving plane has stopped reading
Postgres.

## Execution checkpoint — 2026-08-27

The premise that historical data still needs a shared Postgres drain is superseded. Production now
has an immutable canonical signal snapshot plus independently reconciled product lanes, and all
reusable artifacts are integrated on `main` at
`949e20ee38405781a3e2a8978b2fc769bb7659d6`. The track is **active**, not complete, because its
retirement half has deliberately not started.

| phase | current state |
|---|---|
| P0 bridge | Forward cron configuration is present and PostgreSQL ingestion/data remain intact; scheduled cadence still needs production observation. |
| P1 baseline | Migration contracts through `20260827_0027` are integrated. The live migration head and production parity must be verified through the repaired readiness path; the immutable baseline was not rewritten. |
| P2-P4 writers | Partial. Fire detections, water gauges, and vegetation have direct/queued Parquet-forward machinery; weather has an exact audit path. Snapshot builders are integrated for the governed climate and soil products, but a historical builder is not proof of a durable forward source writer. |
| P5 retirement | Not started. No PostgreSQL producer, reader, table, row, or ingestion registration was removed. |
| P6 shrink | Blocked on production API health, tRPC repoint, browser acceptance, and per-lane forward-writer evidence. |

Updated pivot dependencies: `d1` and governed-product `d5` are cleared by the canonical snapshot
work; `d3` is code-complete but production-blocked by the API service's pre-existing `/ready`
timeout; `d4` has not run. The next safe action is API-path verification and repointing, not a drop
migration.

### Current retirement gates

For each lane, all of the following must be recorded before its PostgreSQL path is removed:

1. private Parquet day/window/release/coverage routes healthy in production;
2. tRPC reader and capability census repointed with no silent fallback;
3. browser acceptance across day and zoom changes;
4. shared `parquet_ops/`, the `interface/cli/` split, and the coordinated `agri-service` rename land;
5. agent/MCP data tools are repointed off obsolete Postgres views per §0.42.30;
6. direct upstream-to-Parquet writer advances across scheduled intervals;
7. exact parity and gap/absence audit stays clean after that advancement;
8. environment-gated PostGIS/reader tests pass on the exact cutover tree;
9. reviewed rollback and relation-drop migrations exist.

The known API `/ready` timeout is a serving blocker, not evidence against the completed Parquet
data. Do not weaken readiness to make the deployment green.

## Background — verified state (2026-08-25, do not re-derive)

### Storage

| relation | total | heap | index | rows |
|---|---|---|---|---|
| `agri.signal_observation` | 25.86 GB | 10.71 | 15.14 | 46,068,872 |
| `geo.features` | 7.90 GB | 3.80 | 2.56 | 5,025,009 |
| `geo.geometry` | 2.93 GB | 1.16 | 1.34 | 3,277,801 |

No bloat, WAL 512 MB — the volume is honestly full. The same signal table is
0.280 GiB in Parquet.

### Extensions and migrations

- `timescaledb` and `timescaledb_toolkit` were **dropped by hand on 2026-08-25**.
  `tracking.positions` survived as a plain table with all four of its indexes.
- `shared_preload_libraries` still names `timescaledb` (Railway-managed image,
  not ours to change). Harmless, but it means the extension is still installable.
- Production `alembic_version` = **`20260817_0025`**. The tree holds
  `20260825_0026_drop_timescaledb_extensions.py`, and
  `tests/conftest.py:34` plus `routes/health/contracts.py:17` are already pinned
  to `20260825_0026` — so the tree is one revision ahead of production.
- `alembic/versions/20260719_0001_agri_foundation.py:34` **requires
  `timescaledb` to be installed** before it will create the `agri` schema, and
  `infra/local-warehouse/enable-extensions.sql` no longer creates it
  (`tests/test_migration_runtime_contract.py:34` asserts that absence). A fresh
  `alembic upgrade head` from revision zero is therefore **deadlocked today**
  unless an operator installs timescaledb by hand purely so a later revision can
  drop it again.
- `pg_stat_statements` is available but not preloaded.

### Parquet warehouse

- z13 base rungs exist: `signal` 1,560 days (2022-04-30..2026-08-06),
  `fire-detections` 8,357, `vegetation` 1,195, `burn-severity` 8 parts / 4
  complete. **z9/z5/z0 exist for no lane.**
- The written `signal` z13 files carry **10 columns and lack
  `cell_longitude`/`cell_latitude`**, which `warehouse/parquet/schema.py:198-211`
  now requires and which the `GridAggregation` tier derivation keys on. The base
  must be re-exported before any coarse rung can be derived from it.
- `signal` days **2026-08-08..2026-08-16** carry `absent.json` markers written
  because Postgres held no rows yet — not because the source cannot serve them.
  `write_partition` refuses a day that has an absence marker, so they are sticky
  until explicitly retracted.
- `burn-severity 2024-08-22` has a base rung and no coarse rungs, is therefore
  never markable, and the drain re-takes it on every census pass.

### Runners

- `infra/cron-ingest/railway.json` has **no `cronSchedule`** and
  `restartPolicyType: NEVER`. Every `git push` auto-deploys it and runs exactly
  one forward pass (`ingest-all` → `jobs-pulse` → `parquet-gap-fill`). The drain
  service behaves the same way.
- **NWS (`sensors`) keeps roughly 6 days upstream and Postgres is its only
  archive.** Last ingest ~2026-08-25 01:45 UTC. Days after ~2026-08-31 are
  unrecoverable if nothing ingests before then. `weather-observations`
  (Open-Meteo current conditions) has the same rolling-window shape.

### Lanes

All twelve database-backed adapters read from Postgres. Four carry governance or
dedup logic in SQL that a direct writer must reproduce:

- **signal** — the governed `(signal_name, normalized_unit, lane)` contract
  VALUES table, release dedup via `agri.source_release`
  (`array_agg(... ORDER BY release_retrieved_at DESC, observation_id DESC)[1]`),
  and the post-aggregate cell-centroid join
  (`sql/pipeline/signal_plane_day_export.sql:46-141`).
- **vegetation** — eight lineage references.
- **sensors** — day-scoped station list (`_sensor_station_ids`).
- **fire-detections** — cell-day aggregate.

The remaining eight are near-plain projections. `calendar` has no source system
and needs no repoint.

### Performance (why this repoint is the memory lever)

Warm Postgres serving reads are 41–110 ms against DuckDB's ~50–100 ms in-region —
a wash. Cold Postgres is 25 s (17,336 pages off a ~5 MB/s volume) against one
26 KB DuckDB file. The DB container's 7–11 GB is page cache plus **26 GB
cumulative temp spill from BATCH jobs** (drain, ingest, quality gates,
matview-refresh with 106 standing dead letters) — not from serving. The memory
win comes from taking batch jobs off Postgres, which is precisely what this
repoint does.

## Goals

- **G1** A writer runs on a schedule again, before the `sensors` /
  `weather-observations` rolling windows lose days permanently.
- **G2** Every lane's Parquet partition is produced from its upstream source
  directly, with no Postgres round trip.
- **G3** Each lane's Postgres write path is deleted only after its direct writer
  is proven row-equal to the Postgres-derived partition.
- **G4** `alembic/versions/` is one greenfield baseline that reflects production
  as it is, and a fresh `alembic upgrade head` reproduces production's extension
  set without installing timescaledb.
- **G5** Postgres shrinks — signal indexes, then the signal table, then
  `geo.features` data-plane rows — on an **architectural** justification, never
  an `idx_scan` counter.

## Non-goals

- Fixing the coarse-rung derivation, re-exporting `signal` z13 with positions,
  materialising z9/z5/z0, or retracting the sticky `signal` absences. All of that
  is owned by pivot slice **d1** and is consumed here as an external dependency.
- Building the Parquet read API or repointing tRPC. Pivot slices **d3** and
  **d4**.
- The `soil-field` lane. Pivot slice **d5**.
- Community features (auth, orgs, tracking, interventions). They stay in
  Postgres, permanently, by the 2026-08-22 pivot decision.
- Running PlantGeo locally, or restarting `continuous-warehouse-loop.sh`. Both
  are standing owner prohibitions.

## Functional requirements

### FR-1 — Bridge writer restored (P0, time-critical)

Restore `"cronSchedule"` to `infra/cron-ingest/railway.json` so the existing
Postgres ingest + `parquet-gap-fill` forward path runs on a clock instead of on a
`git push`. This is the transition writer and it stays running until the last
lane is cut over.

**Acceptance criteria**

- `infra/cron-ingest/railway.json` contains a `cronSchedule` and the service is
  registered as a cron service in Railway (a deployment appears without a push).
- Between two consecutive scheduled runs,
  `pg_stat_all_tables.n_tup_ins` for `geo.features` advances.
- `agri.job_*` ledger shows a `jobs-pulse` pass inside each interval.
- The `sensors` lane's newest z13 partition day advances by at least one day
  within 48 h of the schedule landing.
- The `parquet-drain` service is **not** restarted by this change (the two jobs
  collide; `infra/cron-ingest/Dockerfile:5-16` records the measurement).

**Priority: P0 — must land within days.**

### FR-2 — Direct-writer contract and parity harness

A new package `pipeline/direct/` holds one module per lane, each exposing a
uniform "fetch upstream for day D → `pa.Table` at that lane's registered schema"
entry point, plus a registry mapping lane slug → direct writer. A new CLI verb
drives it. A parity harness compares a direct-writer table against the
Postgres-derived partition for the same lane-day.

**Acceptance criteria**

- `agri-cli parquet-ingest --lane <slug> --day <YYYY-MM-DD>` exists and writes
  through the same `ObjectStore` contract the gap-fill driver uses (completion
  marker last, absence semantics unchanged).
- The parity harness reports **byte-identical sorted rows** for a lane-day, or
  names every differing column and row key.
- A lane with no direct writer registered is refused by name, not silently
  skipped.
- The harness runs against the object store only — it never requires the two
  writers to run in the same process.

### FR-3 — Wave 1: rolling-window lanes (`sensors`, `weather-observations`)

Direct writers for the two lanes whose upstream keeps only a few days.

**Acceptance criteria**

- For **5 sampled days** per lane, the direct writer's partition is byte-identical
  (sorted rows, same schema) to the Postgres-derived partition for that day.
- A day on which no station published is recorded as a **governed absence**, not
  a zero-row partition — matching `_fill_sensors`' current behaviour.
- The direct writer needs no `AsyncSession`; the module imports nothing from
  `agri_data_service.db`.
- Both lanes' newest partition day advances on the cron schedule with the
  Postgres ingest path for those two lanes disabled.

### FR-4 — Wave 2: plain projections

Direct writers for `drought`, `water-gauges`, `fire-perimeters`,
`burn-severity`, `watersheds`, `evacuation-zones`, `soil-survey`.

**Acceptance criteria**

- Per lane, 5 sampled days byte-identical to the Postgres-derived partition
  (for `static_lookup` lanes: the single watermark-dated snapshot).
- Watermark-driven lanes still key their partition day to the **source's own
  change time**, resolved without Postgres — the resolver moves to the upstream
  response's vintage field, and the substitution is documented in the lane
  module.
- `soil-survey` is flagged, not forced: its current producer is the Next.js
  viewport warm path (`src/lib/server/services/usda-soil.ts`), so a direct writer
  is a new fetcher, not a repoint. If that proves out of scope, the lane stays on
  the bridge and the decision is recorded rather than the criterion waived.

### FR-5 — Wave 3: governance-heavy lanes

Direct writers for `signal`, `vegetation`, `fire-detections`.

**Acceptance criteria**

- `signal`: the governed `(signal_name, normalized_unit, required_source_key)`
  contract table is reproduced **as data, not as SQL**, and a test asserts the
  direct writer's accepted-signal set equals the SQL VALUES list in
  `sql/pipeline/signal_plane_day_export.sql:63-86` exactly.
- `signal`: release dedup produces the same winner as
  `ORDER BY release_retrieved_at DESC, observation_id DESC` for a fixture with
  two releases of one cell-day; `observation_count` retains its meaning
  ("how many times an archive republished this cell-day").
- `signal`: `cell_longitude`/`cell_latitude` are present and are the **cell
  centroid**, never an observation location.
- `signal`: `min_value` / `max_value` / `avg_value` are **not** re-added.
- `vegetation`: all eight lineage references are carried; a test names them.
- `fire-detections`: cell-day aggregation matches the Postgres export for 5
  sampled days, including the FIRMS 10,000-record cap behaviour being surfaced
  rather than silently dropped.
- Per lane, 5 sampled days byte-identical to the Postgres-derived partition.

### FR-6 — Per-lane Postgres retirement

For each lane whose direct writer has passed FR-3/4/5, delete its Postgres write
path: the `ingest/<lane>.py` producer, its registration in the `ingest-all`
runner, and its Postgres-reading exporter once nothing consumes it.

**Acceptance criteria**

- `agri-cli ingest-all` no longer names the retired lane, and `--help` proves it.
- No module under `ingest/` references the retired lane's layer binding.
- The retired lane's rows stop growing: `pg_stat_all_tables.n_tup_ins` for its
  target table is flat across two scheduled intervals.
- **Tripwire honoured:** a lane's `pipeline/lanes/<slug>.py` exporter is not
  deleted while `pipeline/parquet/drain.py` (pivot d1) still imports the lane
  registry adapters. Deletion is sequenced after d1 reports the ladder complete.
- Table drops are a migration on the new baseline, never a hand-run `DROP`.

### FR-7 — Alembic greenfield baseline

Collapse `alembic/versions/` to one baseline revision reflecting production as it
is: no timescaledb, `tracking.positions` a plain table, extensions = `postgis`,
`vector`, `pgcrypto` (+ `btree_gist`, `hypopg`, `pg_buffercache`, `plpgsql` as
present). Stamp production to the baseline. Archive the old versions directory
with a pointer.

**Acceptance criteria**

- `alembic heads` prints exactly one revision, and it is the baseline.
- `alembic upgrade head` against an **empty** database creates the full `agri`
  schema and **creates no timescaledb** — asserted by a test on the baseline's
  text and by a real run against a disposable database.
- `pg_dump --schema-only --schema=agri` of a freshly built database is
  **diff-empty** against `pg_dump --schema-only --schema=agri` of production.
  This diff is the safety check; a non-empty diff downgrades this requirement to
  the fallback in FR-7a.
- Production is moved with `alembic stamp <baseline>`, never `alembic upgrade`.
- The baseline touches **no drizzle-owned object**: nothing in schema `geo`,
  nothing in schema `tracking`, no community table, and
  `drizzle.__drizzle_migrations` is untouched. A test asserts the baseline's text
  contains no `geo.` / `tracking.` DDL.
- `EXPECTED_ALEMBIC_HEAD` (`tests/conftest.py:34`),
  `EXPECTED_ALEMBIC_REVISION` (`routes/health/contracts.py:17`) and
  `scripts/readiness.py` all name the baseline, changed in the same commit.
- `EXPECTED_DRIZZLE_MIGRATION` (`src/lib/server/db/migration-contract.ts:1-4`) is
  **unchanged** — the Next.js `/api/ready` route pins the *drizzle* migration,
  not the alembic one, and touching it is out of bounds for this track.
- `tests/test_migration_runtime_contract.py` is rewritten against the baseline
  (it currently asserts the literal string `'timescaledb'::text` appears in
  `20260719_0001`, which the baseline removes), and every other
  `test_*_migration_contract.py` that reads a now-archived revision file is
  re-pointed or retired with its reason recorded.

#### FR-7a — Fallback

If the `agri` schema diff is not empty, or production cannot be safely stamped,
abandon the reset and ship a **forward migration** instead: keep the existing
history, add one revision that reconciles the drift, and record why the reset was
refused. The fallback must be decided on the diff, not on nerve.

### FR-8 — Final shrink (gated)

Drop `uq_signal_observation_release_cell_signal_time` (10.78 GB, a UNIQUE
constraint — `ALTER TABLE ... DROP CONSTRAINT`, not `DROP INDEX`), then the
remaining signal indexes, then `agri.signal_observation`; then the `geo.features`
data-plane rows for retired lanes.

**Acceptance criteria**

- Pivot **d3** (serving API) and **d4** (tRPC repoint) have shipped, and no
  application code path reads `agri.signal_observation` — proven by grep over
  `src/` and `services/agri-data-service/` plus a review verdict, **not** by an
  `idx_scan` counter (`pg_postmaster_start_time` invalidates that counter; see
  RUNBOOK §0.39.4).
- Every dropped relation's data is provably held in Parquet: the lane's z13 day
  count equals its expected window, with completion markers.
- Executed as migrations on the new baseline, one relation per revision.
- Post-shrink `pg_database_size` is recorded, with the before figure (38 GB)
  beside it.

## Non-functional requirements

- **NFR-1 Performance.** A scheduled cron tick must fit inside its interval. The
  measured collision — a `signal` lane-day costs ~8 s alone and ~25 minutes
  beside a cron tick — means the bridge cron and any drain pass must not overlap.
- **NFR-2 Safety.** No hand-run DDL against production. Every schema change is a
  migration; every destructive step is preceded by its parity proof.
- **NFR-3 Reviewability.** Every phase boundary gets an adversarial review in a
  separate context, recorded as a one-line verdict. `code-review high` is
  mandatory for the direct writers and the alembic reset. Security review is not
  required unless a phase moves secrets.
- **NFR-4 Test discipline.** Tests run in **one sweep at the end of each phase**,
  never per task.
- **NFR-5 Operational.** Never run PlantGeo locally. Never restart
  `continuous-warehouse-loop.sh`.

## User stories

**US-1 — The operator gets a heartbeat back**
*As* the operator, *I want* ingestion to run on a schedule, *so that* the NWS
rolling window does not silently drop days I can never recover.
- **Given** `cronSchedule` is restored, **when** two intervals pass, **then**
  `geo.features` `n_tup_ins` has advanced and the `sensors` lane's newest
  partition day is later than it was.

**US-2 — A lane is cut over with proof, not hope**
*As* a maintainer, *I want* a direct writer to be proven row-equal before the
Postgres path is deleted, *so that* a cutover cannot quietly change what readers
see.
- **Given** a direct writer and 5 sampled days, **when** the parity harness runs,
  **then** it reports byte-identical sorted rows, **and** only then is the
  Postgres path deleted in a separate commit.

**US-3 — A fresh database can be built**
*As* a maintainer, *I want* `alembic upgrade head` on an empty database to
succeed without timescaledb, *so that* a rebuild is possible at all.
- **Given** the greenfield baseline, **when** `alembic upgrade head` runs against
  an empty database, **then** the `agri` schema matches production's and
  `pg_extension` holds no `timescaledb` row.

**US-4 — The shrink is justified architecturally**
*As* the owner, *I want* the index and table drops to cite "nothing reads or
writes this", *so that* a stats-window artefact can never be the reason 25 GB was
deleted.
- **Given** d3 and d4 have shipped, **when** the shrink migration is proposed,
  **then** its rationale is a grep-backed absence of readers plus a Parquet
  coverage proof.

## Technical considerations

- **Direct writers are new `agri-cli` verbs.** The agri service is a CLI plus a
  Sanic app; the cron invokes verbs. Wiring the writers as verbs keeps the
  deployment surface unchanged.
- **`lane_registry.py` and `cli.py` are shared registries.** One slice owns each;
  later slices append. See the partition hypothesis in `plan.md`.
- **Absence semantics are load-bearing.** `write_partition` refuses a zero-row
  table so a gap cannot masquerade as a present day, and it refuses a day with an
  absence marker. A direct writer must route "source served nothing" to
  `write_absence` and "source could not be reached" to a failure, exactly as the
  Postgres-derived path does.
- **Day semantics are the client contract.** Days are opaque `YYYY-MM-DD`
  strings; `PUBLISHER_NAMED_DAY_RULE` exists because a single instant-based
  conversion moves 6,279 of 16,743 water-gauge rows onto the next calendar day. A
  direct writer must not "improve" a day by parsing it.
- **The drain and the cron collide.** Sequencing, not parallelism.
- **Railway push-deploy semantics.** Any commit touching this tree redeploys the
  cron service. That is a hazard during P1 (alembic) — a redeploy mid-reset runs
  the old image against a stamped database.

## External dependencies

| id | track | what this track needs from it | blocks |
|---|---|---|---|
| **d1** | `parquet_duckdb_pivot_20260823` | Coarse-rung derivation fix (`burn-severity 2024-08-22` spin), `signal` z13 re-export **with** `cell_longitude`/`cell_latitude`, z9/z5/z0 materialised for every lane-day, retraction of the sticky `signal` absences 2026-08-08..16. Owns `warehouse/parquet/tiers.py`, `pipeline/parquet/drain.py` and their tests. | P5 lane-exporter deletion, P6 |
| **d3** | `parquet_duckdb_pivot_20260823` | `interface/http` serving API over the twelve planes. Owns `interface/http/`, `app.py`, `tests/interface/`. | P6 |
| **d4** | `parquet_duckdb_pivot_20260823` | tRPC repoint onto `parquet-plane-client.ts`. Owns `src/lib/server/trpc/routers/environmental.ts`, `wildfire.ts`. | P6 |
| **d0** | `parquet_duckdb_pivot_20260823` (done) | Completion markers, object-store contract. Owns `foundation/parquet/paths.py`, `pipeline/parquet/objectstore.py`, `pipeline/parquet/gap_fill.py` and their tests — this track reads them and must not edit them. | — |
| **d5** | `parquet_duckdb_pivot_20260823` | `soil-field` lane. Not consumed here; listed so its paths are excluded from this track's partition. | — |

## Risks

| # | risk | impact | mitigation |
|---|---|---|---|
| R1 | **Sensors deadline.** NWS keeps ~6 days; last ingest ~2026-08-25 01:45 UTC. Days after ~2026-08-31 are unrecoverable. | Permanent data loss in two lanes. | P0 lands first and alone. It is the only phase with a calendar deadline. |
| R2 | **Push-triggered redeploys.** Every commit to this tree redeploys the cron service, and `restartPolicyType: NEVER` means it runs one pass immediately. | A commit during a migration runs the old image against a changed schema. | Sequence P1 commits against a known-quiet window; verify the deployed image after any push during P1. |
| R3 | **Absence stickiness.** `write_partition` refuses a day with an `absent.json`; the `signal` 08-08..16 markers were written for the wrong reason. | Nine real days can never be written by any writer, direct or bridged. | Retraction is d1's; this track asserts the retraction happened before claiming `signal` coverage. |
| R4 | **Drizzle / alembic dual ownership.** Both migration systems target one database; drizzle owns `geo` and `tracking` (`src/lib/server/db/schema.ts:22-23`), alembic owns `agri`. | A greenfield alembic baseline that recreates `geo`/`tracking` would fight drizzle's own contract. | Baseline is `agri`-only, asserted by a text test; the `agri`-scoped `pg_dump` diff is the acceptance proof; `EXPECTED_DRIZZLE_MIGRATION` is untouched. |
| R5 | **Governance drift.** A direct writer that "simplifies" the signal contract silently changes what every reader sees. | Wrong data, invisibly. | The contract VALUES list is asserted against the SQL file; parity is byte-level, not row-count. |
| R6 | **`soil-survey` has no Python producer.** Its rows are warmed by the Next.js viewport path. | FR-4 unachievable for that lane as stated. | Explicit fallback: the lane stays on the bridge and the decision is recorded, not the criterion waived. |
| R7 | **Baseline reset is irreversible in practice.** `alembic stamp` cannot be un-stamped meaningfully if the schema diverged. | Production migration state becomes fiction. | The `pg_dump` diff gates the stamp; FR-7a fallback is a forward migration; the stamp is its own reviewed step. |
| R8 | **Cron / drain collision.** Measured: a `signal` lane-day goes from ~8 s to ~25 min beside a cron tick. | The bridge starves the ladder work, or vice versa. | Never both scheduled at once; d1's passes and the bridge cron are sequenced by the operator. |

## Out of scope

- Any edit to `conductor/tracks.md`, `conductor/RUNBOOK.md`, `AGENTS.md`,
  `PLAYBOOK.md`, `product.md`, `tech-stack.md`, `docker-compose.yml` or `docs/`
  as part of *this specification*. Track execution updates the runbook normally.
- New upstream sources (`cds_only_products_20260808` owns those).
- Static-lookup lanes leaving the lane registry (RUNBOOK §0.33.3 item G).
- Open-Meteo ensemble/flood/CAMS lanes and the fire-risk feature plane (item F).

## Open questions

1. **`soil-survey`'s producer.** Does a Python direct writer get built, or does
   the lane stay bridged indefinitely? Decide at P3, on the cost of porting the
   USDA fetcher out of `usda-soil.ts`.
2. **Static-lookup watermarks without Postgres.** Three watermark resolvers read
   `geo.features` change times. The upstream substitute (a source vintage field)
   must be identified per lane during P3; if a lane has none, it keeps a
   Postgres-backed watermark until item G moves it out of the registry entirely.
3. **Where the bridge stops.** Once wave 3 lands, does the cron keep running
   `ingest-all` for the unretired remainder, or does it switch to
   `parquet-ingest` only? Answer falls out of P5's per-lane progress.
