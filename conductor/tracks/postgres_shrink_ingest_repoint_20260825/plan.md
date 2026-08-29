---
type: track-plan
title: "Postgres shrink via ingest repoint to Parquet — bridge, then cut per lane"
tags: [postgres_shrink_ingest_repoint_20260825]
resource: ./spec.md
---

# Implementation Plan: Postgres shrink via ingest repoint to Parquet

## Overview

Seven phases. **P0 is the only one with a calendar deadline** — the NWS rolling
window loses days permanently after ~2026-08-31 if nothing ingests. P1 (alembic
baseline) is independent of the lane work and can run in parallel with P2. P2→P4
build direct writers in three waves; P5 retires Postgres write paths per lane as
each one verifies; P6 is the shrink itself and cannot start until pivot slices
**d3** and **d4** have shipped.

Dependency order:

```
P0 ─┬─> P2 ──> P3 ──> P4 ──┐
    │                      ├──> P5 ──> P6
P1 ─┴──────────────────────┘            ^
                                        |
      external: pivot d1 (ladder) ──────┤
      external: pivot d3 + d4 (serving) ┘
```

## Execution checkpoint — 2026-08-29

The unchecked task list below is the original implementation plan, not a claim that none of it has
run. Use this checkpoint and the track spec before scheduling work:

- **Complete/integrated:** canonical snapshot plus governed climate/soil product breakdowns;
  historical fire, water, weather, vegetation, and NASA soil-wetness reconciliation; reusable
  schemas/builders/auditors; private Parquet routes; bounded TypeScript client; shared top-level
  `parquet_ops/`; `interface/cli/`; the coordinated `agri-service` rename; and migration contracts.
- **Authored, independently approved, fully swept, but uncommitted:** manifest-bound snapshot-product registration plus exact `/day` and
  `/window` reads; snapshot `/release` refusal; explicit Parquet ownership for climate and soil
  readers/capabilities with no silent PostgreSQL fallback; explicit Martin/PostgreSQL ownership for
  Burn History/MTBS; and a water selectable dense-history floor of `2022-08-05` that preserves
  older sparse records as audit evidence. The adversarial integrated review returned **APPROVE**.
  Data-boundary, typecheck, lint, Python format/Ruff/Mypy, 1,477 frontend tests and 4,432 Python
  tests passed; only the documented 13 frontend and 136 Python environment-gated tests skipped.
- **Partially complete:** historical direct/queued writers and audits exist, but snapshot climate
  and soil products still require durable forward-source ownership. The stateful job executor is
  not activated as the production scheduler and has not demonstrated leases, checkpoints, parity
  or recovery across scheduled intervals.
- **Audit gate cleared:** dew-point manifest
  `c2972ea61ebfb66a86fa1e834625fae163e5d0a0abfd39f8c701edca3e59b71a` passed its first full
  read-only audit and the hardened inventory/receipt audit verified exactly 691 objects.
- **Not executed for this cutover:** commit/push, deployment, production R2
  day/window/release/coverage probes, browser acceptance, job-executor activation, PostgreSQL
  producer or reader deletion, relation/index drops, and final database shrink.

Current dependency order:

```
commit/push/deploy exact reviewed and swept tree
  -> production R2 probe of day/window/release/coverage
  -> browser acceptance + environment-gated integration tests
  -> stateful job-executor activation + per-lane forward observation/parity
  -> P5 retirement batches
  -> P6 shrink
```

Do not restart historical snapshot/drain work to satisfy a retirement gate. The immutable snapshot
and its derived lanes are already complete; the remaining proof is forward operation and serving.
Exact `/day` and `/window` mean no cross-day carry, and `/release` must continue to refuse snapshot
products. MTBS remains on its explicit cumulative PostgreSQL/Martin reader until a separately
designed cutover changes that ownership.

Conventions for every phase: TDD per task (write the failing test, implement,
refactor); **one test sweep at the end of the phase, not per task**; the phase
ends with a verification task and an adversarial review recorded as a one-line
verdict.

---

## Phase 0: Bridge — restore the writer

Goal: a writer runs on a clock again. Nothing is retracted, nothing is deleted,
no lane changes shape. **Must land within days.**

Tasks:

- [ ] Task: Add `"cronSchedule"` back to `deploy` in
      `infra/cron-ingest/railway.json`, keeping `restartPolicyType: NEVER` as the
      concurrency guard. Choose the interval from the measured tick cost
      (`ingest-all` alone is ~86 min, so an hourly schedule is skipped to roughly
      two-hourly by Railway — pick the schedule that states the real cadence
      rather than one that relies on being skipped).
      Files: `infra/cron-ingest/railway.json`.
- [ ] Task: Update the schedule narrative in `infra/cron-ingest/Dockerfile`'s
      header (lines 5-16 currently say the schedule is removed on purpose and
      that the local loop replaces it — both are now false; the loop is retired
      and must never be restarted).
      Files: `infra/cron-ingest/Dockerfile`.
- [ ] Task: Confirm the drain service stays **down**. The cron and the drain
      collide (a `signal` lane-day: ~8 s alone, ~25 min beside a cron tick), so
      P0 restores exactly one writer.
      Files: none — an assertion recorded in the phase verdict.
- [ ] Verification: after the push, confirm (a) Railway lists the service as a
      cron service and a deployment appears without a further push;
      (b) `pg_stat_all_tables.n_tup_ins` for `geo.features` advances between two
      consecutive scheduled runs; (c) the `agri.job_*` ledger records a
      `jobs-pulse` pass in each interval; (d) the `sensors` lane's newest z13
      partition day advances within 48 h. [checkpoint marker]
- [ ] Verification: adversarial review of the config change — scope is one JSON
      key and one comment block; the reviewer's job is to refute "this is the
      only change needed to restart the forward path". Record the verdict.
      [checkpoint marker]

---

## Phase 1: Alembic greenfield baseline

Goal: `alembic/versions/` is one baseline that reproduces production's `agri`
schema on an empty database without timescaledb. Independent of the lane work;
may run in parallel with P2. **Does not block P2–P5; blocks P6.**

Tasks:

- [ ] Task: Write the safety check first — a script that builds a disposable
      database from a candidate baseline, dumps `--schema-only --schema=agri`
      from it and from production, and diffs. Non-empty diff means the reset is
      refused and FR-7a's forward-migration fallback is taken.
      Files: `services/agri-data-service/db/tools/` (new script),
      `services/agri-data-service/tests/test_alembic_baseline_parity.py` (new).
- [ ] Task: Write the failing text tests for the baseline before generating it:
      exactly one head; no `timescaledb` anywhere in the versions directory; no
      `geo.` or `tracking.` DDL in the baseline (drizzle owns those schemas —
      `src/lib/server/db/schema.ts:22-23`); `tracking.positions` declared as a
      plain table with its four indexes.
      Files: `services/agri-data-service/tests/test_alembic_head_pin_contract.py`,
      `services/agri-data-service/tests/test_alembic_baseline_contract.py` (new).
- [ ] Task: Generate the baseline revision from production's live `agri` schema
      (extensions `postgis`, `vector`, `pgcrypto`; `hypopg`, `btree_gist`,
      `pg_buffercache`, `plpgsql` as present; **no** `timescaledb`).
      Files: `services/agri-data-service/alembic/versions/` (new baseline),
      `services/agri-data-service/alembic/env.py` if the preflight moves.
- [ ] Task: Retire the twenty-six historical revisions to a documented archive
      directory that alembic does not scan, with a README pointing at the
      baseline and at this track.
      Files: `services/agri-data-service/alembic/versions/` (removals),
      `services/agri-data-service/alembic/archive/` (new).
- [ ] Task: Re-point or retire every test that reads an archived revision file.
      `tests/test_migration_runtime_contract.py:30-34` is the sharp one — it
      asserts the literal `'timescaledb'::text` appears in `20260719_0001`, which
      the baseline removes. The others read revisions 0002, 0003, 0004, 0005,
      0009, 0010, 0013, 0014, 0015.
      Files: `services/agri-data-service/tests/test_migration_runtime_contract.py`,
      `tests/test_geospatial_evidence_migration_contract.py`,
      `tests/test_gate_hardening_migration_contract.py`,
      `tests/test_forecast_iteration_migration_contract.py`,
      `tests/test_strategy_selection_migration_contract.py`,
      `tests/test_forecasting_migration_contract.py`,
      `tests/test_signal_evaluation_migration_contract.py`,
      `tests/test_security_definer_lockdown_migration_contract.py`.
- [ ] Task: Move the readiness pin in the same commit as the revision — that
      coupling is what `test_alembic_head_pin_contract.py` exists to enforce.
      Files: `services/agri-data-service/tests/conftest.py` (line 34,
      `EXPECTED_ALEMBIC_HEAD`),
      `services/agri-data-service/src/agri_data_service/routes/health/contracts.py`
      (line 17, `EXPECTED_ALEMBIC_REVISION`),
      `services/agri-data-service/scripts/readiness.py`.
      **Do not touch** `src/lib/server/db/migration-contract.ts` — the Next.js
      `/api/ready` route pins the *drizzle* migration, not the alembic one.
- [ ] Task: Run the safety check against production. If diff-empty, `alembic
      stamp <baseline>` on production (a stamp, never an `upgrade`). If not,
      stop, take FR-7a, and record the diff as the reason.
      Files: none — an operation, with its output recorded.
- [ ] Verification: `alembic heads` prints one revision; `alembic upgrade head`
      on an empty disposable database succeeds and `pg_extension` holds no
      `timescaledb` row; production `alembic_version` equals the baseline;
      `drizzle.__drizzle_migrations` is unchanged. [checkpoint marker]
- [ ] Verification: **`code-review high`** in a separate context, prompted to
      refute the reset — specifically the claim that the baseline reproduces
      production and touches nothing drizzle owns. Record the verdict.
      [checkpoint marker]

---

## Phase 2: Direct-writer foundation + wave 1 (rolling-window lanes)

Goal: the direct-writer contract, the parity harness, and the two lanes whose
upstream forgets. Blocks on P0 only.

Tasks:

- [ ] Task: Define the direct-writer protocol — "upstream + day → `pa.Table` at
      the lane's registered schema", with absence and failure as distinct
      outcomes (source served nothing → `write_absence`; source unreachable →
      raise). Test the protocol against a fake upstream before any real lane.
      Files: `services/agri-data-service/src/agri_data_service/pipeline/direct/__init__.py`
      (new), `.../pipeline/direct/contract.py` (new),
      `services/agri-data-service/tests/direct/test_direct_contract.py` (new).
- [ ] Task: Build the parity harness: read the Postgres-derived partition and the
      direct-writer table for one lane-day, sort both to the lane's grain, and
      report byte-identical or name every differing column and row key.
      Files: `.../pipeline/direct/parity.py` (new),
      `services/agri-data-service/tests/direct/test_parity.py` (new).
- [ ] Task: Add the driver verb `agri-service data parquet-ingest --lane --day` plus the
      lane→writer registry it resolves through. `interface/cli/data.py` is a shared registry —
      this slice owns it for this track (see the partition hypothesis).
      Files: `services/agri-data-service/src/agri_data_service/interface/cli/data.py`,
      `.../pipeline/direct/registry.py` (new),
      `services/agri-data-service/tests/direct/test_direct_registry.py` (new).
- [ ] Task: `sensors` direct writer against api.weather.gov, reproducing the
      day-scoped station selection that `_fill_sensors` gets from
      `lane_registry_sensor_station_ids.sql` and routing a no-station day to a
      governed absence.
      Files: `.../pipeline/direct/sensors.py` (new),
      `services/agri-data-service/tests/direct/test_direct_sensors.py` (new).
- [ ] Task: `weather-observations` direct writer against Open-Meteo current
      conditions (`ingest/open_meteo.py`'s `WEATHER_LAYER` sampler is the shape
      to reproduce, not to import).
      Files: `.../pipeline/direct/weather_observations.py` (new),
      `services/agri-data-service/tests/direct/test_direct_weather_observations.py` (new).
- [ ] Task: Register both lanes and record, in `pipeline/parquet/lane_registry.py`,
      which lanes now have a direct writer — the registry keeps one source of
      truth about lane state.
      Files: `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py`.
- [ ] Verification: run the parity harness on **5 sampled days per lane**; assert
      byte-identical sorted rows against the Postgres-derived partition. Record
      the sampled days and the results. [checkpoint marker]
- [ ] Verification: **`code-review high`** on the writers and the harness, in a
      separate context, prompted to refute parity. Record the verdict.
      [checkpoint marker]

---

## Phase 3: Wave 2 — plain projections

Goal: direct writers for the seven near-plain lanes. Blocks on P2.

Tasks:

- [ ] Task: `drought` direct writer (USDM weekly release; the partition day is
      the release's own `valid_date`, cadence 7).
      Files: `.../pipeline/direct/drought.py` (new),
      `tests/direct/test_direct_drought.py` (new).
- [ ] Task: `water-gauges` direct writer (USGS NWIS). Preserve the named-day
      rule — days are opaque `YYYY-MM-DD`, never instants.
      Files: `.../pipeline/direct/water_gauges.py` (new),
      `tests/direct/test_direct_water_gauges.py` (new).
- [ ] Task: `fire-perimeters` direct writer (WFIGS), including the 2025-08-02 ..
      2025-09-30 contiguous hole as a known gap rather than a silent absence.
      Files: `.../pipeline/direct/fire_perimeters.py` (new),
      `tests/direct/test_direct_fire_perimeters.py` (new).
- [ ] Task: `burn-severity` direct writer (MTBS release series; a non-release day
      is a governed absence, as today).
      Files: `.../pipeline/direct/burn_severity.py` (new),
      `tests/direct/test_direct_burn_severity.py` (new).
- [ ] Task: `watersheds` and `evacuation-zones` direct writers, **plus** their
      watermark substitution: both resolvers currently read `geo.features` change
      times; identify the upstream vintage field that replaces them, or record
      that the lane keeps a Postgres-backed watermark until RUNBOOK §0.33.3 item
      G moves it out of the registry.
      Files: `.../pipeline/direct/watersheds.py` (new),
      `.../pipeline/direct/evacuation_zones.py` (new),
      `tests/direct/test_direct_static_lookups.py` (new).
- [ ] Task: `soil-survey` — decide and record. Its rows are warmed by the Next.js
      viewport path (`src/lib/server/services/usda-soil.ts`), so a direct writer
      is a **new fetcher**, not a repoint. Either build it or leave the lane
      bridged with the decision written down; do not waive the criterion.
      Files: `.../pipeline/direct/soil_survey.py` (new, conditional),
      `tests/direct/test_direct_soil_survey.py` (new, conditional).
- [ ] Task: Register wave 2 in the direct registry (appending to the file P2
      owns).
      Files: `.../pipeline/direct/registry.py`,
      `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py`.
- [ ] Verification: parity harness, 5 sampled days per lane (for the two
      `static_lookup` lanes, the single watermark-dated snapshot). Record days and
      results. [checkpoint marker]
- [ ] Verification: adversarial review focused on the watermark substitution and
      the `soil-survey` decision. Record the verdict. [checkpoint marker]

---

## Phase 4: Wave 3 — governance-heavy lanes

Goal: `signal`, `vegetation`, `fire-detections` — the three whose SQL carries
governance a direct writer must reproduce exactly. Blocks on P2.

Tasks:

- [ ] Task: Lift the governed signal contract out of SQL into data, and write the
      test that fails if the two ever disagree: the accepted
      `(signal_name, normalized_unit, required_source_key)` set must equal the
      VALUES list at `sql/pipeline/signal_plane_day_export.sql:63-86` exactly.
      Files: `.../pipeline/direct/signal_contract.py` (new),
      `tests/direct/test_signal_contract_matches_sql.py` (new).
- [ ] Task: Reproduce release dedup without `agri.source_release`: the winner for
      a grain key is the newest source release
      (`ORDER BY release_retrieved_at DESC, observation_id DESC`), and
      `observation_count` keeps its meaning — how many times an archive
      republished the cell-day, never a reading count. Test with a two-release
      fixture for one cell-day.
      Files: `.../pipeline/direct/signal.py` (new),
      `tests/direct/test_direct_signal_dedup.py` (new).
- [ ] Task: `signal` direct writer over NASA POWER + ERA5-Land
      (`execution/weather_observations/nasa_power.py`, `.../era5_land.py` are the
      existing fetchers), emitting `cell_longitude`/`cell_latitude` from the cell
      **centroid** and **not** re-adding `min_value`/`max_value`/`avg_value`.
      Files: `.../pipeline/direct/signal.py`,
      `tests/direct/test_direct_signal.py` (new).
- [ ] Task: `vegetation` direct writer carrying all eight lineage references, with
      a test that names them.
      Files: `.../pipeline/direct/vegetation.py` (new),
      `tests/direct/test_direct_vegetation.py` (new).
- [ ] Task: `fire-detections` direct writer reproducing the cell-day aggregate,
      and surfacing the FIRMS 10,000-record cap as a reported truncation rather
      than a silent drop.
      Files: `.../pipeline/direct/fire_detections.py` (new),
      `tests/direct/test_direct_fire_detections.py` (new).
- [ ] Task: Register wave 3.
      Files: `.../pipeline/direct/registry.py`,
      `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py`.
- [ ] Verification: parity harness, 5 sampled days per lane, byte-identical.
      For `signal`, sample at least one day from each producer era (pre- and
      post-2022-08-06) so both governance branches are exercised.
      [checkpoint marker]
- [ ] Verification: **`code-review high`** in a separate context, prompted to
      refute "the governance is reproduced" — the contract table, the dedup
      ordering, the centroid join, the dropped columns. Record the verdict.
      [checkpoint marker]

---

## Phase 5: Per-lane Postgres retirement

Goal: each verified lane's Postgres write path is deleted. Blocks on P2/P3/P4 per
lane, and — for exporter deletion — on external **d1**.

Tasks:

- [ ] Task: Switch the cron entrypoint to drive `parquet-ingest` for every
      verified lane, leaving `ingest-all` covering only the unretired remainder.
      Files: `infra/cron-ingest/Dockerfile`.
- [ ] Task: Per verified lane, delete its Postgres producer and its registration
      in the ingest runner, one lane per commit.
      Files: `services/agri-data-service/src/agri_data_service/ingest/<lane>.py`,
      `.../ingest/runner.py`, `.../ingest/commands.py`,
      `services/agri-data-service/tests/` (the producer's own tests).
- [ ] Task: Delete the lane's Postgres-reading exporter **only after d1 reports
      the ladder complete** — `pipeline/parquet/drain.py` (pivot d1) still
      imports the lane-registry adapters, and removing an exporter under it
      breaks another track's slice.
      Files: `services/agri-data-service/src/agri_data_service/pipeline/lanes/<lane>.py`,
      `services/agri-data-service/src/agri_data_service/sql/pipeline/<lane>_*.sql`,
      `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py`.
- [ ] Task: Drop the retired lane's Postgres tables as a migration on the P1
      baseline — one relation per revision, never a hand-run `DROP`.
      Files: `services/agri-data-service/alembic/versions/` (new revisions).
- [ ] Verification: `agri-service data ingest-all --help` no longer names the retired
      lanes; no module under `ingest/` references their layer bindings; each
      retired lane's target table shows flat `n_tup_ins` across two scheduled
      intervals while its Parquet partition day keeps advancing.
      [checkpoint marker]
- [ ] Verification: adversarial review per retirement batch — the reviewer's job
      is to find a reader of the deleted path. Record the verdict.
      [checkpoint marker]

---

## Phase 6: Final shrink (gated on pivot d3 + d4)

Goal: reclaim the 25.86 GB signal table and the `geo.features` data-plane rows.
**Cannot start** until d3 (serving API) and d4 (tRPC repoint) have shipped and
serving no longer reads Postgres.

Tasks:

- [ ] Task: Write the architectural justification and its evidence: a grep over
      `src/` and `services/agri-data-service/` showing no reader of
      `agri.signal_observation`, plus a Parquet coverage proof (z13 day count
      equals the lane's expected window, completion markers present). **An
      `idx_scan` counter is not evidence** — `pg_postmaster_start_time` was
      2026-08-24 14:40 UTC and `stats_reset` is NULL.
      Files: track evidence note under this track directory.
- [ ] Task: Drop `uq_signal_observation_release_cell_signal_time` (10.78 GB) —
      it is a UNIQUE **constraint**, so `ALTER TABLE ... DROP CONSTRAINT`, not
      `DROP INDEX`. One migration.
      Files: `services/agri-data-service/alembic/versions/` (new revision).
- [ ] Task: Drop the remaining signal indexes
      (`ix_..._cell_time_signal`, `pk_signal_observation`, `ix_..._release_time`),
      then `agri.signal_observation`. One relation per revision.
      Files: `services/agri-data-service/alembic/versions/` (new revisions).
- [ ] Task: Delete the `geo.features` / `geo.geometry` data-plane rows for
      retired lanes — a data migration, scoped by layer, with the community
      features left untouched.
      Files: `drizzle/` (a drizzle migration, since these are drizzle-owned
      schemas — coordinate with the drizzle migration contract rather than
      reaching in from alembic).
- [ ] Verification: record `pg_database_size` before and after beside the 38 GB
      baseline; confirm the map still renders the repointed layers through d4's
      tRPC path; confirm no application error rate change.
      [checkpoint marker]
- [ ] Verification: **`code-review high`** on the drop migrations, in a separate
      context, prompted to refute "nothing reads this". Record the verdict.
      [checkpoint marker]

---

## Partition hypothesis

Prebaked write-ownership for `/slice`. **Every path owned by pivot slices d0–d5
is excluded** (`foundation/parquet/paths.py`, `pipeline/parquet/objectstore.py`,
`pipeline/parquet/gap_fill.py`, `warehouse/parquet/tiers.py`,
`pipeline/parquet/drain.py`, `interface/http/`, `app.py`, `tests/interface/`,
`pipeline/lanes/soil_field.py`, `src/lib/server/trpc/routers/environmental.ts`,
`src/lib/server/trpc/routers/wildfire.ts`, and the matching pivot tests).

Confidence: **planned** — computed from the planning read, rebaselined to commit
`440d9b5` (the tree state when the track was registered). `metadata.json` carries
the canonical copy, restructured to the `partitions` schema (top-level
`shared_writes` / `tripwires` / `integration_slice`). Re-verify every `owns` list
with a real grep once HEAD has moved; only a consumer may upgrade this to
`verified`.

```json
{
  "computed_at": "2026-08-25T00:00:00Z",
  "computed_at_commit": "440d9b5",
  "confidence": "planned",
  "note": "Prebaked from the planning analysis, no separate recon pass. Excludes every path owned by parquet_duckdb_pivot_20260823 slices d0-d5. lane_registry.py, interface/cli/data.py, alembic/versions/ and the cron Dockerfile are SHARED registries with a single named owner each; later slices append and must be sequenced, never run concurrently on those files.",
  "slices": [
    {
      "id": "s0",
      "task": "P0 bridge: restore cronSchedule so a writer runs on a clock again. Correct the Dockerfile header, which still claims the schedule was removed on purpose and that continuous-warehouse-loop.sh replaces it (both false; the loop is retired and must never be restarted). Verify the drain stays down.",
      "owns": [
        "infra/cron-ingest/railway.json",
        "infra/cron-ingest/Dockerfile"
      ],
      "model": "sonnet",
      "depends_on": [],
      "shared_writes": [
        {
          "path": "infra/cron-ingest/Dockerfile",
          "owner": "s0",
          "note": "s5 rewrites the ENTRYPOINT verb list when lanes retire; sequence after s0."
        }
      ],
      "tripwires": [
        "Do NOT restart plantgeo-parquet-drain in this slice: a signal lane-day costs ~8 s alone and ~25 min beside a cron tick.",
        "Do NOT restart continuous-warehouse-loop.sh (standing owner prohibition).",
        "Every push to this tree auto-deploys the cron service and runs one pass immediately; do not push during another slice's migration window.",
        "restartPolicyType NEVER must stay: it is the concurrency guard."
      ]
    },
    {
      "id": "s1",
      "task": "P1 alembic greenfield baseline: one revision reflecting production as-is (no timescaledb, tracking.positions plain), archive the 26 historical revisions, re-point every migration-contract test, move the readiness pin, stamp production. Fallback to a forward migration if the agri-schema pg_dump diff is non-empty.",
      "owns": [
        "services/agri-data-service/alembic/versions/",
        "services/agri-data-service/alembic/env.py",
        {"path": "services/agri-data-service/alembic/archive/", "status": "future"},
        "services/agri-data-service/tests/conftest.py",
        "services/agri-data-service/tests/test_alembic_head_pin_contract.py",
        "services/agri-data-service/tests/test_migration_runtime_contract.py",
        "services/agri-data-service/tests/test_geospatial_evidence_migration_contract.py",
        "services/agri-data-service/tests/test_gate_hardening_migration_contract.py",
        "services/agri-data-service/tests/test_forecast_iteration_migration_contract.py",
        "services/agri-data-service/tests/test_strategy_selection_migration_contract.py",
        "services/agri-data-service/tests/test_forecasting_migration_contract.py",
        "services/agri-data-service/tests/test_signal_evaluation_migration_contract.py",
        "services/agri-data-service/tests/test_security_definer_lockdown_migration_contract.py",
        {"path": "services/agri-data-service/tests/test_alembic_baseline_contract.py", "status": "future"},
        {"path": "services/agri-data-service/tests/test_alembic_baseline_parity.py", "status": "future"},
        "services/agri-data-service/src/agri_data_service/routes/health/contracts.py",
        "services/agri-data-service/scripts/readiness.py",
        "services/agri-data-service/db/tools/"
      ],
      "model": "opus",
      "depends_on": [],
      "shared_writes": [
        {
          "path": "services/agri-data-service/alembic/versions/",
          "owner": "s1",
          "note": "s5 and s6 add drop revisions on top of the baseline; both must land strictly after s1."
        }
      ],
      "tripwires": [
        "The baseline is agri-ONLY. drizzle owns schemas geo and tracking (src/lib/server/db/schema.ts:22-23); any geo./tracking. DDL in the baseline is out of bounds.",
        "Do NOT edit src/lib/server/db/migration-contract.ts — /api/ready pins the DRIZZLE migration, not the alembic one.",
        "Production is moved with `alembic stamp`, never `alembic upgrade`.",
        "Gate the stamp on a diff-empty `pg_dump --schema-only --schema=agri` between a freshly built database and production; a non-empty diff means take the forward-migration fallback and record why.",
        "tests/test_migration_runtime_contract.py:30-34 asserts the literal string 'timescaledb'::text inside 20260719_0001. Archiving that revision breaks the test by construction — rewrite it in the same change, do not delete it.",
        "EXPECTED_ALEMBIC_HEAD (conftest.py:34) and EXPECTED_ALEMBIC_REVISION (routes/health/contracts.py:17) must move in the SAME commit as the revision."
      ]
    },
    {
      "id": "s2",
      "task": "P2 direct-writer foundation: the protocol, the parity harness, the `parquet-ingest` CLI verb and the lane->writer registry, plus wave 1 lanes sensors and weather-observations (the two rolling windows).",
      "owns": [
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/", "status": "future"},
        "services/agri-data-service/src/agri_data_service/interface/cli/data.py",
        "services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py"
      ],
      "model": "opus",
      "depends_on": ["s0"],
      "shared_writes": [
        {
          "path": "services/agri-data-service/src/agri_data_service/interface/cli/data.py",
          "owner": "s2",
          "note": "3,900-line shared verb registry. s3/s4/s5 add NO new verbs; they register lanes in pipeline/direct/registry.py instead."
        },
        {
          "path": "services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py",
          "owner": "s2",
          "note": "Shared lane registry. s3/s4 append lane-state entries; s5 removes retired adapters. Strictly sequential, never concurrent."
        },
        {
          "path": "services/agri-data-service/src/agri_data_service/pipeline/direct/registry.py",
          "owner": "s2",
          "note": "s3 and s4 append their lanes here."
        }
      ],
      "tripwires": [
        "Direct writers must import nothing from agri_data_service.db — a Postgres session in a direct writer defeats the whole track.",
        "Days are opaque YYYY-MM-DD strings. Never parse or format one (PUBLISHER_NAMED_DAY_RULE: an instant conversion moves 6,279/16,743 water-gauge rows to the next day).",
        "Source served nothing -> write_absence. Source unreachable -> raise. Never a zero-row partition.",
        "write_partition refuses a day that already has an absent.json. The sticky signal absences 2026-08-08..16 are pivot d1's to retract; do not work around them here.",
        "Do NOT edit pipeline/parquet/objectstore.py or gap_fill.py (pivot d0) or drain.py (pivot d1)."
      ]
    },
    {
      "id": "s3",
      "task": "P3 wave 2 direct writers: drought, water-gauges, fire-perimeters, burn-severity, watersheds, evacuation-zones, and the soil-survey decision. Includes replacing the three Postgres-backed static-lookup watermark resolvers with an upstream vintage, or recording that a lane keeps its Postgres watermark.",
      "owns": [
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/drought.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/water_gauges.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/fire_perimeters.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/burn_severity.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/watersheds.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/evacuation_zones.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/soil_survey.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_drought.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_water_gauges.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_fire_perimeters.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_burn_severity.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_static_lookups.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_soil_survey.py", "status": "future"}
      ],
      "model": "sonnet",
      "depends_on": ["s2"],
      "shared_writes": [
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/registry.py", "owner": "s2"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py", "owner": "s2"}
      ],
      "tripwires": [
        "soil-survey has NO Python producer: its rows are warmed by the Next.js viewport path (src/lib/server/services/usda-soil.ts). A direct writer is a new fetcher, not a repoint — decide and record, do not silently skip.",
        "A static_lookup lane's partition day is a VERSION STAMP from the source's own change time, never the run date and never a calendar day.",
        "burn-severity's cadence stays 1: its five release dates sit on no fixed step, and a cadence above one steps past real releases.",
        "fire-perimeters' 2025-08-02..2025-09-30 hole is a real gap, not an absence to paper over."
      ]
    },
    {
      "id": "s4",
      "task": "P4 wave 3 direct writers for the governance-heavy lanes: signal (governed contract table + release dedup + centroid), vegetation (eight lineage refs), fire-detections (cell-day aggregate + FIRMS cap surfaced).",
      "owns": [
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/signal.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/signal_contract.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/vegetation.py", "status": "future"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/fire_detections.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_signal.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_signal_dedup.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_signal_contract_matches_sql.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_vegetation.py", "status": "future"},
        {"path": "services/agri-data-service/tests/direct/test_direct_fire_detections.py", "status": "future"}
      ],
      "model": "opus",
      "depends_on": ["s2"],
      "shared_writes": [
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/direct/registry.py", "owner": "s2"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py", "owner": "s2"}
      ],
      "tripwires": [
        "The governed contract is (signal_name, normalized_unit, lane) — NOT signal_name alone. A NULL required_source_key means 'no lane gate applies', never 'unknown'.",
        "Do NOT re-add min_value/max_value/avg_value: measured equal to normalized_value on 100% of 701,257 rows and 3.81x larger.",
        "cell_longitude/cell_latitude are the CELL centroid, resolved AFTER the aggregate. Carrying geometry through the aggregate took one production day from inside the 120 s timeout to CANCELLED at 151 s.",
        "observation_count is republish count, not reading count. No reader may weight by it.",
        "signal.py (the Postgres exporter under pipeline/lanes/) stays untouched here — it is still the drain's source until s5.",
        "The written signal z13 files lack the two position columns the schema now requires; re-export is pivot d1's, and parity sampling must account for which layout each sampled day was written in."
      ]
    },
    {
      "id": "s5",
      "task": "P5 per-lane Postgres retirement: delete each verified lane's ingest producer and runner registration, switch the cron entrypoint to parquet-ingest for verified lanes, and drop the lane tables as migrations on the s1 baseline. Exporter deletion waits on pivot d1.",
      "owns": [
        "services/agri-data-service/src/agri_data_service/ingest/",
        "services/agri-data-service/src/agri_data_service/pipeline/lanes/",
        "services/agri-data-service/src/agri_data_service/sql/pipeline/"
      ],
      "model": "sonnet",
      "depends_on": ["s1", "s2", "s3", "s4"],
      "shared_writes": [
        {"path": "infra/cron-ingest/Dockerfile", "owner": "s0"},
        {"path": "services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py", "owner": "s2"},
        {"path": "services/agri-data-service/alembic/versions/", "owner": "s1"}
      ],
      "tripwires": [
        "EXTERNAL BLOCK: do not delete pipeline/lanes/<slug>.py while pivot d1's pipeline/parquet/drain.py still imports the lane-registry adapters. Deleting one breaks another track's slice.",
        "pipeline/lanes/soil_field.py is pivot d5's — excluded from this slice's ownership of that directory.",
        "One lane per commit; the deletion commit is SEPARATE from the parity-proof commit.",
        "No hand-run DROP against production. Table drops are migrations.",
        "interventions stays in Postgres by design (RUNBOOK 0.26.1) — it is not a lane and is not retired."
      ]
    },
    {
      "id": "s6",
      "task": "P6 final shrink: drop the signal unique CONSTRAINT (10.78 GB), the remaining signal indexes, then agri.signal_observation; then the geo.features/geo.geometry data-plane rows for retired lanes via a drizzle migration. Gated on pivot d3 and d4.",
      "owns": [
        {"path": "conductor/tracks/postgres_shrink_ingest_repoint_20260825/evidence/", "status": "future"}
      ],
      "model": "opus",
      "depends_on": ["s1", "s5"],
      "shared_writes": [
        {"path": "services/agri-data-service/alembic/versions/", "owner": "s1"},
        {"path": "drizzle/", "owner": "s6", "note": "geo and tracking are drizzle-owned; the row deletion is a drizzle migration with its own contract bump, coordinated with src/lib/server/db/migration-contract.ts by whoever owns that contract at the time."}
      ],
      "tripwires": [
        "EXTERNAL BLOCK: cannot start until pivot d3 (interface/http serving API) and d4 (tRPC repoint) have shipped and serving no longer reads Postgres.",
        "The justification is ARCHITECTURAL — a grep-backed absence of readers plus a Parquet coverage proof. An idx_scan counter is NOT evidence: pg_postmaster_start_time was 2026-08-24 14:40 UTC and stats_reset is NULL.",
        "uq_signal_observation_release_cell_signal_time is a UNIQUE CONSTRAINT: ALTER TABLE ... DROP CONSTRAINT, not DROP INDEX.",
        "ix_signal_observation_cell_time_signal had 4,163,429 scans and is serving production — it goes LAST, after d4 proves nothing reads it.",
        "One relation per revision, so a bad step reverses without taking the rest with it.",
        "Community features (auth, orgs, tracking, interventions) stay in Postgres permanently. Do not touch them."
      ]
    }
  ]
}
```
