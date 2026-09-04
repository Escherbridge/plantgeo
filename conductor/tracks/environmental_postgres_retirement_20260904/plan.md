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

**B1 finding, 2026-09-04 — a latent defect in `pipeline/lanes/vegetation.py`, found not fixed.**
`export_vegetation_day`'s docstring (`:82`) claims a source-empty day "is a governed absence …
recorded with `store.write_absence`", but the function body only ever calls `store.write_partition`,
which by its own contract (`:83`) **refuses a zero-row table**. So a Postgres-empty day RAISES instead
of governing an absence. B1 worked around it with a read-only pre-check rather than editing a file it
did not own.

**WITHDRAWN IN FULL — there is no defect. Corrected 2026-09-04 by the F-B1 fix lane, which traced the
caller instead of stopping at the function.** Two passes confirmed the code reading (the body calls
`write_partition`; `objectstore.py:551` raises `EmptyPartitionError` on zero rows) and both drew the
wrong conclusion from it. **The raise is the signal:** `gap_fill.py:1174` catches exactly that class
and calls `_govern_absent_day`, which marks all four rungs absent coarse-first/base-last and rolls the
ladder back as a unit if any rung refuses; `gap_fill.py:1704` then extends the availability index with
`terminal_state="governed_absence"`. The canonical path already produces a durable, indexed, four-rung
governed absence carrying the exporter's own zero-row result as proof. Only the docstring is loose —
it misattributes the write to the function rather than its caller.

Two earlier claims recorded here are therefore both dead: the link to vegetation's 205
ladder-incomplete days (those are days whose BASE rung exists and whose derived rungs do not, so a day
that never wrote a base rung cannot be among them), and the underlying defect itself.

**The workaround was the real bug.** `backfill.py::_postgres_has_rows` intercepted zero-row days
*before* the mechanism that would have settled them, so each one wrote nothing durable and the walker
re-selected the same oldest days every turn, forever. Deleted; every day now goes through
`fill_one_lane_day`. Lesson recorded in `pipeline/direct/AGENTS.md`: a docstring that misattributes a
responsibility reads exactly like a missing implementation — trace the caller before building around it.

**B1 note — the ownership boundary is a guess and must be verified before activation.**
`pipeline/direct/vegetation/products.py:49` pins `VEGETATION_DIRECT_WRITER_START_DAY = date(2026, 9, 5)`,
the split between the source-direct forward writer and the Postgres-reading backfill. `parity.py`
reports the real newest Postgres day; read it and correct the constant before the lane is registered.
A boundary set too early double-writes; too late leaves a hole neither driver owns.

**B2 finding, 2026-09-04 — weather-observations has NO archive endpoint, so its drop sequences
differently from every other layer.** Its Postgres producer (`ingest/open_meteo.py`'s `WEATHER_LAYER`)
polls Open-Meteo's `current` endpoint, which takes no `start_date`/`end_date` and answers only "now",
gated by a 3-hour `MAX_OBSERVATION_AGE`. There is no settled day to fetch, so the writer accumulates a
day incrementally across many polls — water-gauges' shape, not climate/soil's. Consequence for D1:
**for this layer the Postgres table IS the only historical archive.** Nothing upstream can reproduce
it. Its drop is therefore gated on the existing Postgres-reading adapter republishing history into
Parquet to completion (`parquet-drain --selection missing --layer weather-observations`), not merely
on the forward writer working. Verify that republish finished before writing this layer's drop packet.
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
- [x] **C3 — DONE, and the premise below was WRONG. The slider was never served from the frozen
      matview.** Corrected 2026-09-04 by the C3 lane, which traced the caller before changing anything.
      `src/lib/server/trpc/routers/environmental.ts:629` has read
      `getSliderCapabilities: publicProcedure.query(() => getParquetSliderCapabilities())` since
      commit `069ef90` ("cut over tRPC readers to Parquet", 2026-08-28), and
      `parquet-slider-capabilities.ts:771` awaits only `getGeoFeatureSliderCapabilities()` +
      `getParquetWarehouseCoverage()` — it never calls `readStreamCapabilities` or
      `readStreamObservationWindows`. `src/lib/server/AGENTS.md:1186-1191` already said so in writing.
      The Postgres path was **dead code with zero production callers**, reachable only from its own
      unit tests. Its real hazard was that it still worked: anyone who found and called it would get
      silently-stale-forever data with no error. Deleted rather than reimplemented — repointing a
      function nothing calls would have duplicated `PARQUET_CAPABILITY_CONTRACTS`' proof of the same
      13 layers, the "two hopeful copies" drift `parquet-plane-client.ts` warns against.

      **Three agents asserted this path was live** (the retirement inventory, the matview lane, and the
      wave-A adversarial reviewer). All three read the function and none traced the caller — the same
      error class as the `write_absence` phantom. Two occurrences in one day: **before recording a
      defect, trace the caller.**

- [ ] ~~C3 (superseded — original text kept for the record)~~ **the observation-day census consumer.** `src/lib/server/services/environmental-read-model.ts:3198` and
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
- [ ] **D-shared — `geo.features` CANNOT BE DROPPED AT ALL. Corrected 2026-09-04 by the wave-C
      adversarial review.** The earlier reading below was wrong in a way that would have wasted a wave:
      it said the last of seven layers gates the table. There is an **eighth, permanent resident**.
      `sql/agent/feature_value_near_point.sql` keeps a live PostgreSQL read of `geo.features` for
      `interventions`, which RUNBOOK section 0.26.1 keeps in PostgreSQL **permanently** — a community
      feature, not environmental data, and correctly out of scope for this track. So `interventions`
      never clears, and the relation never drops.
      **D-shared therefore becomes: drop the seven environmental layers' ROWS, keep the table** — or
      move `interventions` to its own table first and then drop `geo.features`. Owner call at the next
      touchpoint; the row-delete form is cheaper and reversible from the archived `pg_dump`.
      The new guard test (`tests/test_agent_parquet_tools.py:770-798`) that claims to be "the c2-style
      removal proof, executable" does **not** list `geo.features` or `geo.layers` among its forbidden
      relations, so it passes while the surviving statement reads the largest environmental table in
      the schema. Add both, with an explicit `feature_value_near_point.sql` exemption, so the exception
      is asserted rather than merely absent.

- [ ] ~~D-shared, original text (superseded):~~ **`geo.features` is the exception to per-layer drops.** A3 proved it is one shared
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

## C1 outcome, 2026-09-04 — four of five moved; fire-perimeters blocked upstream

**Moved to Parquet:** `sensor_tiles`, `evacuation_zone_tiles`, `burn_severity_tiles`, `watershed_tiles`.
No PMTiles proposal is owed — no layer needed vector tiles rather than GeoJSON.

**NOT moved: `fire_risk_tiles`. This is a GRAIN mismatch, not a coverage gap, and the fix is upstream.**
The lane is registered `daily_series` on a per-incident `observed_day` (`lane_registry.py:835-839`), while
`geo.features` holds WFIGS's current-incident set **refreshed in place** — "one row per WFIGS incident …
NOT one row per (incident, day)" (`warehouse/schemas/fire_perimeters.py`). So 177 perimeters sit across
45 partition days and the map draws their UNION via the tile plus an `observed_day <= day` filter. A day
read returns only incidents redrawn that day (empty on most days); a release read returns the same; a
full-history window is 404 days of envelopes. A trailing N-day window would mean "perimeters redrawn in
the last N days" — a different product.
**Fix: re-register the lane as the `static_lookup` snapshot it actually is** — the shape
`evacuation-zones` already uses for an identical current-state feed. That is a
`services/agri-data-service` change, which lane C1 was forbidden to touch. **Criterion 2 cannot close
until this lands.**

**Holes shipped deliberately, named rather than hidden:**
- `sensors` below z13 — 25 of 26 base days lacked a coarse ladder at the 2026-08-25 measurement. Those
  days now answer `not_generated` with a dock caption, where `sensor_tiles` drew dots. Repair is
  `parquet-drain --selection ladder` against production: built, dry-run verified, **zero production runs**.
- `evacuation-zones` at z9 — confirmed 2026-09-02 DuckDB casualty, dead-lettered, unverified since `152feca`.
- **`basin_count` is gone**, omitted rather than approximated: the tile function got it from a `count(*)`
  while building `geo.watershed_rollup` (`drizzle/0023:52`), and the lane's `HierarchicalDissolve`
  declares no counting aggregation. Restoring it is a `ColumnAggregation` on the watersheds lane.
- **Watershed rung mapping shifts at the extremes** — z10-z12 now draws HUC10 where the function drew
  HUC12, and z0-z3 draws HUC6 where it drew HUC4 (there is no HUC4 rung).

**The drop migration is written and DORMANT:** `drizzle/0039_drop_environmental_tile_functions.sql`,
unjournalled so `scripts/migrate.mjs` cannot run it by accident (matching `0034`'s convention). Four
idempotent `DROP FUNCTION IF EXISTS`, no CASCADE. `geo.fire_risk_tiles` is deliberately absent and must
not be added. Each object still owes its three-part packet. The drop also turns `geo.watershed_rollup`
into a zero-reader relation with a zero-value hourly refresh in `jobs/matview_refresh.py`.

**Deploy order is load-bearing: app first, then Martin.** `auto_publish: false` means an already-loaded
tab still asks for the four unpublished ids and would 404 until reload.
