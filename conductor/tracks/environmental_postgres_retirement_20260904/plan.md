---
type: track-plan
slug: environmental_postgres_retirement_20260904
status: active
---

# Plan — environmental Postgres retirement

Five waves. Waves A and B run concurrently on disjoint trees; C depends on B's parity receipts; D
depends on C. Each wave ends in one sweep and one adversarial review by a separate context.

## Wave A — unblock the time slider and clear the dead objects

- [ ] **A1 — availability bootstrap compiler (D3).** New `scripts/compile_availability_bootstrap.py`:
      per lane walk the ladder, read manifests and completion markers, hash parts inside the recent
      window, mark older days manifest-trusted, emit the `availability-bootstrap-input-v1` document
      plus its sha256, run offline validation, record the receipt. Extend
      `pipeline/parquet/availability_index.py` so a manifest-trusted row is a declared provenance
      class, and `foundation/parquet/completion.py` so new completion markers carry per-part digests.
      Owns: `scripts/compile_availability_bootstrap.py`,
      `src/agri_data_service/pipeline/parquet/availability_index.py`,
      `src/agri_data_service/foundation/parquet/completion.py`, their tests.
- [ ] **A2 — matview refresh spec (RUNBOOK step 1d).** Remove `geo.mv_signal_observation_day` from the
      refresh spec: it fails on the 302 s per-view statement timeout and dead-letters the
      `matview-refresh` shard for a view the Parquet pivot replaced. Same shape as the two absent
      relations removed in p3. Owns: `src/agri_data_service/jobs/matview_refresh.py`, its tests.
- [ ] **A3 — retirement inventory (read-only).** Every environmental relation in `geo.*` and
      `db/agri/**`, every matview over one, and every lane or command that fills one — from
      `execution/job_executor_service.py` `LANE_SPECS`, `jobs/dispatch.py`, `ingest/lanes.py`,
      `db/agri/**`, `src/agri_data_service/sql/**`, `drizzle/*.sql`. Each row marked: drop after
      Parquet proof / drop now (zero readers, no live filler) / keep (executor `agri.job_*` ledger).
      Produces the drop ledger every wave-D packet indexes into. Owns:
      `evidence/retirement-inventory.md`. Writes no code.
- [ ] **A1c — thread the written-object ledger into the base-rung completion marker.** A1b wired
      per-part digests into `derivation.py::_write_tier` (coarse rungs) but stopped at
      `gap_fill.py::_finalize_written_day` (base rung), which still writes v1 markers, so **base-rung
      days stay manifest-trusted indefinitely and D3's "the trusted region stops growing" holds only for
      coarse rungs.** Cause: `LaneRunResult` carries folded totals — `lane_registry.py:250-262`
      (`_from_parts`) sums each adapter's receipts into three integers and discards `relative_path` and
      `sha256`, so nothing bearing a digest reaches the marker. The receipts DO survive in
      `fill_one_lane_day`'s open ledger (`gap_fill.py:1794`, `store.recording_written_objects()`), whose
      `parts_for(...)` returns them pre-sorted. Thread that ledger down — and carry the soft guard
      `availability_extension.py:848-862` (`_rung_objects_from_ledger`) already uses, because
      `_fill_static_day` (`gap_fill.py:1510-1521`) retries the same day through one ledger up to
      `MAX_STATIC_EXPORT_ATTEMPTS` times: a stale ledger without that guard makes `_validate_parts`
      RAISE inside the marker write, turning a correctly-finished export into `raised`. Its own review;
      do not fold it into another lane. `lane_registry.py` is serialized — a join agent owns any edit
      there.
- [ ] **A4 — bootstrap all time-bearing lanes in one pass, then flip.** Run A1's compiler through
      `railway run` so it uses the executor's R2 credentials; record every receipt key and sha in
      `evidence/availability-bootstrap-receipts.md`; then set `PARQUET_COVERAGE_AUTHORITY=availability`
      (owner-confirmed) and prove `/api/v1/parquet/coverage` answers `coverage_authority:
      "availability"` for every lane and the zero-LIST tripwire holds in the service logs.
      **This is the step that ends the startup cost.** Depends on A1.
- [ ] **A5 — drop-now objects.** The relations A3 marks as zero-reader, no-filler (the matviews the
      Parquet pivot replaced, `agri.spatial_cell`-class orphans, dead ledgers). One Alembic migration,
      rehearsed on `agri_sweep`, each object carrying its three-part packet. Depends on A3.

## Wave B — direct-to-Parquet writers, eight layers (D2, D4)

One agent per layer, each owning `src/agri_data_service/pipeline/direct/<layer>/` and its tests.
Ordered by map impact: the first three are the layers the lane stop froze.

- [ ] **B1 vegetation NDVI** · **B2 weather-observations** · **B3 drought**
- [ ] **B4 fire-perimeters** · **B5 sensors** · **B6 watersheds** · **B7 evacuation-zones**
- [ ] **B8 burn-severity**

Each layer is done when: a `--max-days 1` proving run publishes at every required rung; a **parity
receipt** (D2) shows the Parquet twin covers at least the PostgreSQL holding, with the shortfall days
backfilled and any upstream-never-served days recorded in the gap census; and the layer's generic
`parquet-*` lane is retired in favour of the direct lane.

**Serialized, single-owner-at-a-time** (never edited by a layer agent):
`pipeline/parquet/lane_registry.py`, `execution/job_executor_service.py` (`LANE_SPECS`),
`interface/cli/data.py`. A join agent lands every registration after the layer work is reviewed.

## Wave C — move the readers off PostgreSQL (D4)

- [ ] **C1 — Martin's FIVE environmental tile functions.** Corrected by the A3 inventory 2026-09-04:
      `burn_severity_tiles` reads `geo.features` identically to `fire_risk_tiles`, `sensor_tiles`,
      `evacuation_zone_tiles` and `watershed_tiles`, so the "four tile functions" figure inherited from
      the RUNBOOK is wrong and C1 covers five. Served from PMTiles or the Parquet API, with browser
      parity evidence at the default PNW camera. Restart Martin after any tile migration — a missing
      tile function 404s the whole composite and hides every layer.
- [ ] **C2 — agent signal queries** (`src/agri_data_service/sql/agent/*.sql`, `agent/tools.py`) served
      from the Parquet API, with agent parity evidence per tool. Includes the four agent tools that
      still hard-error against the already-dropped `geo.mv_signal_cell_daily`.
- [ ] **C3 — the observation-day census consumer. ON THE TIME SLIDER'S CRITICAL PATH — raise its
      priority accordingly.** `src/lib/server/services/environmental-read-model.ts:3198` and
      `:3464-3465` read `geo.mv_signal_observation_day` live through `geo.v_observation_day_census`,
      and they back **`getSliderCapabilities`** — a `publicProcedure` cached 30 min via
      `STREAM_CAPABILITIES_CACHE_TTL_MS`, introduced as the fix for the 2026-08-15 Cloudflare 524. Its
      own comment at `:3467-3471` assumes "the refresh cadence upstream of it is the real staleness
      floor", which is now false for this relation: the REFRESH was already timing out at 300 s before
      A2 removed it from the spec, so the slider's capability catalogue is served from a frozen view
      today. This lane repoints the consumer to the Parquet API; only then may the view and its wrapper
      be dropped. Sequence it with A4 — both concern what the slider believes exists.
      Second consumer, same relation: `agent/tools.py:229-234` (`SIGNAL_CENSUS_RELATION`, folded into
      `CENSUS_RELATIONS`) is probed by `_unbuilt_planes` at `:959` and `:1001`, but that probe only
      checks `relispopulated` — **a populated-but-frozen relation passes it**, so
      `query_observation_coverage_on_day` and `query_observation_temporal_neighbors` will serve stale
      census answers without the typed refusal that was supposed to protect them. Fix the probe in C2.

## Wave D — the drops (D1)

- [ ] **D-per-layer.** For each layer whose parity receipt (B) and reader move (C) are green: write the
      three-part packet to `evidence/drop-packets/<relation>.md` (parity receipt, zero-reader proof,
      `pg_dump` key + sha256 on R2), then one small Alembic migration rehearsed on `agri_sweep` and
      applied with owner confirmation. Regenerate the declarative tree; refresh the quality receipt.
- [ ] **D-shared — `geo.features` is the exception to per-layer drops.** A3 proved it is one shared
      polymorphic table serving SEVEN of the eight D4 layers (only drought owns its own table), so it
      cannot be dropped layer by layer. Each layer still earns its own parity receipt for its own rows;
      the table itself is gated on the LAST of the seven clearing waves B and C. Sequence its drop
      after every dependent tile function has moved in C1.
- [ ] **D-scope-gap — `signal-plane` and `soil-survey`.** Both are separately registered Parquet lanes
      with live PostgreSQL relations and live readers (`agri.signal_observation`,
      `geo.soil_survey_coverage`), and neither appears in D4's eight-layer list. Classify them at the
      next owner touchpoint: either they join wave B as B9/B10, or their relations are explicitly
      recorded as out of scope for this track. Do not force-fit them into an existing layer.
- [ ] **D-fills.** Deactivate and then delete the remaining PostgreSQL fill lanes and their commands,
      SQL and tests with removal packets (zero-imports proof): `jobs-firms-archive`,
      `jobs-streamflow-archive`, the four `maintenance-*`, `jobs-matview-refresh`,
      `jobs-strategy-mv-refresh`, `mtbs-forward`, `vegetation-catch-up`.
- [ ] **D-verdict.** Hand every packet to `parquet_production_acceptance_20260901` A3–A4.

## Standing rules for every wave

- **Authors never verify their own work.** Implementation agents run no tests; they predict what will
  fail. A separate monitor/reviewer context runs the single sweep over the combined tree and judges.
- **One sweep at the end** of a wave: `npm run type-check`, `npm run lint`, `npm test` (vitest alone);
  and for Python `git add services/agri-data-service` first, then `scripts/check.py --write-receipt`,
  then verify against a `git archive` extraction the way the image will.
- Every Python command is `UV_NO_SYNC=1 uv run --no-sync …`. A bare `uv sync` strips pytest.
- One step per push; RUNBOOK and track updates before the final sweep.
