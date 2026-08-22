---
type: lane-contract
---

# `water-gauges` lane

Written for the agent that will build `warehouse/schemas/water-gauges.py`,
`pipeline/lanes/water-gauges.py`, `pipeline/validation/water-gauges.py`,
`method/monte_carlo/water-gauges.py` and `planes/water-gauges.py` per
[`conductor/code_styleguides/layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md) §1. No
Parquet path, filename or column list is asserted here — that contract is being written concurrently
by another agent this session (`conductor/RUNBOOK.md` §0.24.1, stream S0).

**Headline finding, because it resolves the question the brief asked to verify**: `geo.layers` seed
text calls this "Current persisted USGS streamflow observations"
(`drizzle/0001_handy_riptide.sql:311`), but that is 2026-08-03 boilerplate — the identical phrase
("Current persisted weather observations") is stamped on `weather-observations` too, so the wording is
not evidence about this layer specifically. The actual write contract keys every reading on
`{siteNo}:{updatedAt}` (`services/agri-data-service/src/agri_data_service/ingest/identity.py:229-238`),
which mints a **new** entity version per gauge per reading rather than overwriting one row. This is a
genuine append-only time series, not a latest-value cache. See §4.

## 1. Source system

- **Publisher**: USGS, via the National Water Information System (NWIS) Water Services API. Public
  federal data; `docs/rebuilding-the-dataset.md:40` records auth as "No" and cost as "—" for
  `ingest-streamflow` ("Public water services API"). No `credential_variable` is set on either the
  forward source or the archive lane
  (`services/agri-data-service/src/agri_data_service/ingest/lanes.py:214-226`, contrast with
  `FIRMS_ARCHIVE_LANE`'s `credential_variable=FIRMS_API_KEY_VARIABLE` two lines above it).
- **License**: **UNVERIFIED** — no explicit license string is recorded for USGS NWIS the way
  `ingest/mtbs.py:150` records one for MTBS ("U.S. federal public domain"). USGS water data is
  conventionally U.S. federal public domain, but nothing in this repo states that for NWIS
  specifically; confirm against `https://waterservices.usgs.gov` or USGS's own data policy page
  before asserting it in a public-facing attribution.
- **Two distinct endpoints, not one**, both parameterized to `parameterCd=00060` (discharge, cfs) and
  `siteType=ST` (stream):
  - **Instantaneous Values (`/nwis/iv/`)** — the forward/live feed. Pinned `siteStatus=active`.
    `STREAMFLOW_QUERY_TEMPLATE` at `usgs_nwis.py:61-63`.
  - **Daily Values (`/nwis/dv/`)** — the archive/history feed, used only by the backfill lane. **Not**
    filtered to active sites, deliberately: "A gauge discontinued in 2024 still measured real water in
    2022, and filtering history to currently-active sites would silently delete those years from the
    record" (`usgs_nwis.py:75-77`). `DAILY_VALUES_QUERY_TEMPLATE` at `usgs_nwis.py:78-81`.
  - The code comment at `usgs_nwis.py:70-73` is explicit about why these can't share a query: `/nwis/iv/`
    keeps roughly 120 days and answers a well-formed, silently empty response for anything older —
    "a walk built on it would report four years of successful, empty chunks."
- **Geographic scope**: bounded to `INGEST_BBOX`, the same PNW extent every layer in this warehouse
  shares (measured elsewhere in the repo as ≈(−125, 42, −111, 49)), tiled into ≤4°×4° NWIS requests
  by `tile_bbox()` (`usgs_nwis.py:150-165`) because NWIS rejects a bbox wider than 25 square degrees.
  This is **not** a nationwide layer.

## 2. Cadence

- **Forward path is now hourly, not every 30 minutes.** `infra/cron-ingest/railway.json` sets
  `cronSchedule: "0 * * * *"`, and `ingest-streamflow` runs as one step inside the consolidated
  `ingest-all` on `plantgeo-ingest-cron` (`docs/deployment.md` "Cron consolidation, 2026-08-14",
  lines 586, 600-602). The **30-minute figure is stale**: it is the *former*, pre-2026-08-14
  per-source schedule (`docs/deployment.md:590`, "Former `cronSchedule`" column), and it survives
  as a live comment inside `usgs_nwis.py:116-125` ("25s, not the 10s this carried until 2026-08-05
  ... against a 30-minute cadence") and in
  `services/agri-data-service/src/agri_data_service/ingest/validation/models.py:108`
  (`cadence_basis="infra/cron-streamflow/railway.json runs */30 * * * *"` — that directory no longer
  exists, per `docs/deployment.md:670-676`). **Treat both of those in-code cadence claims as
  superseded by the hourly `cron-ingest` schedule, not as current fact.**
- **Archive/backfill is also hourly**, via a different mechanism: `jobs-pulse`'s durable-archive
  namespace, folded into the same `plantgeo-ingest-cron` tick since the same 2026-08-14
  consolidation (`docs/deployment.md:604-620`). It walks `STREAMFLOW_ARCHIVE_LANE`
  (`ingest/lanes.py:214-226`) in `chunk_days=10` / `window_days=30` steps toward the floor in §3.
- **Observed lag**: not separately measured for this lane in the repo. USGS daily values are
  conventionally provisional-same-day-to-next-day and get revised; nothing here confirms or
  quantifies that lag for this warehouse's ingest — **UNVERIFIED**, would need a same-day vs.
  published-value diff to confirm.
- One nuance for the eventual `pipeline/lanes/water-gauges.py`: the forward parser keeps only the
  **newest** reading per site per tick (`parse_gauge`, `usgs_nwis.py:229-280` — "the query pins no
  `period` and NWIS then returns only the newest value"), so the forward path is a poll of current
  state, not a sub-hourly time series; sub-daily resolution only exists insofar as consecutive hourly
  ticks each capture whatever NWIS reported as "latest" at that moment.

## 3. Historical horizon

This is the part the brief asked to verify carefully, and the evidence is genuinely mixed — report it
as mixed rather than picking the more flattering half.

- **Declared code floor**: `USGS_DAILY_VALUES_EARLIEST = datetime(2022, 8, 5, tzinfo=UTC)`
  (`usgs_nwis.py:87`). The comment is explicit that this is **not** a source-imposed limit: "USGS DV
  nominally reaches the 1890s for a few gauges. The floor is set at the vegetation layer's own
  earliest observed day instead... because that is the axis the slider actually draws and a deeper
  walk would spend hours on years no other layer can populate." So "earliest obtainable" from NWIS
  itself is much deeper than what this warehouse asks for — **UNVERIFIED exactly how deep** for the
  specific gauges inside `INGEST_BBOX` (the 1890s figure is a general NWIS characteristic asserted in
  a comment, not measured against this bbox).
- **A handful of rows genuinely reach 1990**, but they are not a real historical series: "A handful of
  DISCONTINUED USGS gauges carry an upstream `updatedAt` of their final-ever reading — two on
  1990-10-01, one on 1990-12-14, then scattered singles through the 1990s and 2000s"
  (`src/lib/server/services/environmental-read-model.ts:2842-2848`). A bare `min(observed_day)` over
  this layer reports 1990-10-01 and is explicitly called a trap in that same file and in
  `src/types/time-slider.ts:157-161` — do not use it to answer "how far back does this lane go."
- **The measured DENSE/continuous record, as last captured in this codebase, starts far later than
  the 2022-08-05 floor**: `environmental-read-model.ts:2863-2872` records that gap-clustering plus a
  density floor moved water-gauges' effective start to **2026-05-24** ("clustering moves water-gauges
  from 1990-09-30 to 2026-05-24"), with the days between 2026-05-24 and 2026-08-01 carrying only 1-7
  readings each against 15,844 on the last three days sampled. That comment carries no explicit
  measurement date, so treat 2026-05-24 as a snapshot that predates this session, not today's true
  value — **re-run the clustering query before relying on a specific date.**
- **Why the gap between the 2022-08-05 floor and the ~2026-05-24 dense start is plausible, not a
  bug**: the archive/backfill lane that would populate 2022-08-05→2026-05 is `STREAMFLOW_ARCHIVE_LANE`,
  and it only became a scheduled, automatic hourly job on 2026-08-14 (§2). Before that date it existed
  in code (`BACKFILL_LANES`, `ingest/lanes.py:228-230`) but had to be triggered manually
  (`jobs-run --lane streamflow-archive`). `conductor/RUNBOOK.md` §0.16.4-0.16.5 (dated 2026-08-16/17)
  independently corroborates partial progress: a 2026-08-07 measurement recorded "46 of 95 streamflow
  windows still needed more than the first [retry] attempt" (`ingest/archive_walk.py:99-101`) — i.e.
  the walk was actively running and retrying, not idle. **Net read: the floor is declared, the
  automated walk toward it has been running since 2026-08-14, but nothing in the repo confirms it has
  reached 2022-08-05 as of today. Confirm with a fresh gap-clustered earliest-date query
  (`environmental-read-model.ts`'s own logic) or a `job_work_item` completion count for the
  `streamflow-archive` lane before assuming 4 years of dense history exists.**
- **Row-count corroboration that this is a real, growing series** (supports §4's identity finding,
  not the horizon question): an older in-repo snapshot recorded "16,743 stored water-gauges rows"
  (`environmental-read-model.ts:127`, undated); `conductor/RUNBOOK.md` §0.18.1 (dated 2026-08-2x)
  measured **953 gauges carrying 1,417,935 readings total** in production. Two independent snapshots,
  taken at different times, both consistent with continuous accretion — not a static or overwritten
  count.

## 4. Grain

- **One row = one gauge's discharge reading at one instant** (forward path) or **one gauge's
  discharge on one calendar day** (archive/daily-values path). Both are keyed by USGS site number
  (`entity_local_id=site_number`, `identity.py:237`); the version-distinguishing key is
  `{siteNo}:{updatedAt}` (`identity.py:235`), so each tick/day is a **new** version of the entity
  rather than an update to a single row — confirmed both by the identity contract and by the
  RUNBOOK's Tier-2/3 redesign analysis, which treats `geo.station` (the entity) and
  `geo.streamflow_reading` (the reading log) as separate concerns precisely because ~1,488 readings
  accrue per station (`conductor/RUNBOOK.md` §0.18.1, lines 1811-1831).
- **Unit**: discharge in cubic feet per second (`flowCfs`), USGS parameter code `00060`.
  `percentile`/`condition`/`trend` are populated defensively but are effectively always
  `null`/`"unknown"`/`"stable"` from this pipeline: NWIS's instantaneous feed supplies no percentile
  (`classify_condition(None)`, `usgs_nwis.py:168-180`, called with a literal `None` at both call
  sites), and `trend` only ever reads `"declining"` off a qualifier code, never a real historical
  comparison (`infer_trend`, `usgs_nwis.py:183-188`).
- **Geometry**: one point per gauge (`lat`/`lon` from NWIS `geogLocation`), written as GeoJSON in
  `properties.geometry` (`build_gauge_write`, `usgs_nwis.py:418-434`). Per the RUNBOOK's known defect
  (not specific to this lane, but it disproportionately affects this layer's reads): the current
  writer never populates `geo.features.geometry_id`, so a meaningful fraction of rows are geometry-orphaned
  until `ingest-geometry-repair` runs. Measured 2026-08-04: "streamflow-cfs on that day had 4,314
  water-gauges rows of which 1,617 were unlinked" — **37% orphaned on that sample**
  (`environmental-read-model.ts:4148-4152`).
- **`geo.streamflow_reading`, the month-partitioned Postgres table the brief flagged, does not
  exist.** It is a proposed DDL in `conductor/RUNBOOK.md:1836-1843` (§0.18, "Tier 3 — one narrow
  typed table per stream, RANGE-partitioned by month"), part of an entity/observation Postgres
  redesign that itself predates and is **superseded by** the Parquet/DuckDB pivot in §0.23 — that
  redesign was never applied. Confirmed: repo-wide search finds `streamflow_reading` in
  `conductor/RUNBOOK.md` only, in no `.py`/`.sql`/`.ts` file. Today's persisted shape is still one row
  per version inside `geo.features` (properties JSONB) joined to `geo.geometry`, exactly as described
  above — not a typed reading table.

## 5. Known gaps and traps

- **The NWIS missing-value sentinel is `-999999.0` and arrives as an ordinary numeric string.**
  Unguarded, it writes as a real discharge reading and corrupts every downstream percentile/colour
  ramp; the forward path shipped without the guard once and wrote 680 sentinel rows in six days
  before it was caught (`usgs_nwis.py:89-100`, `is_missing_value_sentinel`). Both the Python ingest
  side (`USGS_NO_DATA_SENTINEL` at `ingest/validation/constants.py:57`) and the TS read side
  (`environmental-read-model.ts:137`) carry independent copies of this constant — keep them in sync
  if it ever moves.
- **Do not filter on sign.** Genuine reverse flow at these gauges is recorded down to **−172,000
  cfs** (`usgs_nwis.py:97-99`, `environmental-read-model.ts:134-135`). "Negative means missing" would
  silently delete real measurements; the sentinel must be compared to its exact value.
- **A day with sentinel-only readings is dropped, not written as a null.** `parse_gauge` treats an
  all-sentinel tick as "gauge not reported this tick" and returns `None` rather than a row with
  `flowCfs: null` — a fabricated "no value" observation is worse than a missing one
  (`usgs_nwis.py:248-254`, `parse_daily_value_series` at `:344-349` does the same for the archive
  path).
- **`min(observed_day)` lies about how far back this layer's usable record goes** (§3). The correct
  earliest-date logic lives server-side as gap-clustering (`OBSERVATION_CLUSTER_GAP_DAYS = 21`) plus a
  density floor (`OBSERVATION_DENSITY_FLOOR_FRACTION = 0.01`) in
  `environmental-read-model.ts:2839-2932`. A Parquet-era gap-detector for this lane should reuse that
  logic's *intent* (continuity, not volume, and relative to each layer's own peak) rather than a fixed
  date or a fixed row-count threshold.
- **Cadence comments in three places are stale post-2026-08-14 cron consolidation** (§2): the "every
  30 minutes" note in `usgs_nwis.py:116-125`, the `cadence_basis` string in
  `ingest/validation/models.py:108`, and `docs/services.md:145-168`'s description of a Redis-cached,
  percentile-alerting `usgs-water.ts` — that TS file's actual exports
  (`getStreamflowGauges`, `getGroundwaterWells`, `src/lib/server/services/usgs-water.ts:163,211`) are a
  live-only NWIS passthrough with no DB write and, like the Python side, an always-null percentile
  (`usgs-water.ts:185-188`); it does not back the persisted lane at all and should not be read as a
  spec for it.
- **NWIS's bBox/spatial-filter path degraded on 2026-08-05**: non-spatial lookups stayed sub-second
  but every bBox query answered in 7-14s or timed out, which is why the forward timeout was raised
  from 10s to 25s (`usgs_nwis.py:116-126`). If a future exporter re-derives timeouts, 25s (forward) /
  90s (archive, `NWIS_ARCHIVE_BOUNDS`, `usgs_nwis.py:114`) are the measured, not arbitrary, values.
- **The archive floor (2022-08-05) is borrowed from the vegetation layer, not derived from this
  source or this bbox.** If vegetation's own floor is ever revisited, this lane's floor should be
  re-examined rather than assumed to still be the right alignment.
- **No coupling to the `watersheds` (HUC12) lane was found in ingest code.** `usgs_nwis.py` and
  `identity.py` contain no reference to HUC12 or watershed boundaries;
  `services/agri-data-service/src/agri_data_service/ingest/watersheds.py` is a fully separate module.
  Treat the two lanes as independent unless a future join is added.

## 6. Validation approach

- **Sentinel guard is applied at parse time on every ingest path** (§5) — this is the one
  water-gauges-specific data-quality check that exists today.
- **Gap detection exists and runs hourly, but only reports — it does not itself close gaps.**
  `validate-streams` runs as the third pass of `jobs-pulse` for every registered stream, including
  this one (`docs/deployment.md:678-689`).
- **water-gauges is one of only two of eleven layers (with `fire-detections`) whose gaps
  automatically become claimable work**, via `BACKFILL_LANES` / `STREAMFLOW_ARCHIVE_LANE`
  (`ingest/lanes.py:214-230`; `conductor/RUNBOOK.md` §0.16.5 counts this explicitly as "2 of 11").
  The other nine layers' detected gaps require a human to notice and run a CLI verb.
- **No source-system reconciliation validator exists yet for this lane** in the sense
  `conductor/code_styleguides/layer-lanes.md` §4 requires (`pipeline/validation/water-gauges.py`,
  comparing written Parquet against a fresh NWIS pull) — that file does not exist; §4's contract is
  forward-looking for wave 2, not a description of current state.
- **The day-semantic used for this layer's read-side gap logic is `updatedAt`, not `observedAt`**:
  measured basis in `conductor/RUNBOOK.md:1805-1809` — "water-gauges carries **no** `observedAt` but
  `updatedAt` on all sampled rows." Any validator built for this lane should key day-boundedness off
  `updatedAt`, matching what the ingest identity and the existing read model both already do.
- **Governed absences are not implemented for this layer.** Per `conductor/RUNBOOK.md` §0.16.5, the
  governed-absence plane (a detected gap the source genuinely cannot fill, recorded as such rather
  than left open) is unimplemented across all 11 layers. Until it exists, `validate-streams` will
  re-report a legitimate absence (e.g. a genuinely discontinued gauge) as an open gap indefinitely.

## 7. Forecast recommendation

**`horizon: 30d`**, per `conductor/RUNBOOK.md` §0.24.2's classification of `water-gauges` as a "yes"
for the 30-day Monte Carlo, and this document does not find evidence to override that declaration —
but the recommendation comes with a load-bearing caveat the classification table does not carry.

- **Reason it's a good candidate in principle**: discharge is continuous, strictly non-negative in
  the vast majority of readings (reverse-flow excursions aside), materially autocorrelated day to day,
  and seasonally driven by snowmelt/precipitation — exactly the shape a Monte Carlo ensemble forecast
  is suited to, and the brief's own framing is correct on this point.
- **The caveat §3 exists to surface**: a seasonal Monte Carlo model needs enough dense history to
  estimate a seasonal cycle, and the best evidence in this repo puts the *dense, continuous* record at
  as little as ~3 months deep as of its last measurement (2026-05-24 onward), not the ~4 years the
  declared 2022-08-05 floor implies. **Before building `method/monte_carlo/water-gauges.py`, re-measure
  the actual dense-record depth** (the gap-clustering query in
  `environmental-read-model.ts:2839-2932` is the reusable logic for this) rather than assuming the
  floor date reflects real coverage.
- **What that implies for the method, not the horizon**: if the dense record is still short, the
  first version of this lane's forecaster should lean on a model that degrades gracefully with
  limited history — e.g. an autoregressive/persistence model with volatility bands per gauge, widening
  with `horizon_days` — rather than one that assumes a full seasonal cycle is already observable. It
  can be upgraded to a seasonally-aware model once the backfill (§3) has had more time to run; nothing
  here blocks shipping a simpler model first and revising it later, since the lane contract already
  requires provenance columns (`forecast_run_id`, `random_seed`, `ensemble_size`, `horizon_days`,
  `issued_on`, `quantile`/`draw_index` — `conductor/code_styleguides/layer-lanes.md` §3) that make
  swapping the method later auditable.
- **Projected quantity, order of magnitude only**: ~953 gauges (§3, most recent count) × 30 forecast
  days × however many ensemble draws or quantiles the eventual method publishes. That last factor is
  genuinely undecided — no `method/monte_carlo/water-gauges.py` exists yet to read it from, and this
  document does not invent one. As a rough floor (one quantile per gauge-day, the minimum the lane
  contract could accept), that is on the order of **~29,000 forecast rows per run**; a five-quantile
  ensemble would be on the order of **~143,000**. Treat both only as sizing intuition for the writer,
  not as a spec.
