---
type: lane-contract
slug: fire-detections
horizon: 30d
---

# fire-detections lane

Source-of-truth spec for the `fire-detections` layer lane, one of the eleven
layers named in `conductor/RUNBOOK.md` §0.24.2 (`conductor/RUNBOOK.md:3336-3348`)
and governed by `conductor/code_styleguides/layer-lanes.md`. This document does
not assert any Parquet path layout, filename, or column list — that contract is
being written concurrently by another agent (stream S0, `layer-lanes.md` §1).
Where the repo does not establish a fact, it is marked `UNVERIFIED` with what
would confirm it.

Current producer code: `services/agri-data-service/src/agri_data_service/ingest/firms.py`.
Prose walkthrough: `services/agri-data-service/src/agri_data_service/ingest/AGENTS.md`
§"firms.py" (from line 70) and §"firms.py: the archive is the same endpoint with
a date, and the product is the discriminator" (from line 86).

## 1. Source system

**Publisher:** NASA FIRMS (Fire Information for Resource Management System),
served from `firms.modaps.eosdis.nasa.gov`.

**Endpoint(s):** one CSV endpoint, used two ways — there is no separate archive
host (`ingest/AGENTS.md:88-91`):

- Current/forward window: `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/{source}/{area}/{day_range}`
  (`firms.py:72-74`).
- Dated (archival) window: the same URL with a trailing `/{start_date}`
  (`firms.py:75-77`).
- Availability table (per-product coverage window, read before every history
  walk): `https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{api_key}/all`
  (`firms.py:78`).

**Two access paths, one producer.** `nasa-firms` (current window, three-satellite
VIIRS NRT fan-out) and `nasa-firms-archive` (past windows, NRT+SP+MODIS) are two
`--source` tokens over the *same* producer, layer, channel and identity contract
— not two producers (`ingest/AGENTS.md:86-91`). The forward job explicitly
refuses archive-style dated requests and vice versa (`firms.py:789-791`,
`FIRMS_ARCHIVE_CURRENT_REFUSAL`, `firms.py:114-118`): `nasa-firms-archive` serves
past windows only, because `ingest-firms` is the path that reports a
partial-constellation outage as a reason rather than a clean empty run. A wave-2
lane pipeline module needs to reconcile both fetch shapes.

**Auth: `NASA_FIRMS_KEY` is REQUIRED.** This corrects a claim this document was
briefed against ("FIRMS reportedly needed no credential") — verified false by
three independent sources:

1. Code: `_require_api_key()` reads `NASA_FIRMS_KEY` and raises
   `ValueError(f"{FIRMS_API_KEY_VARIABLE} environment variable is not set")`
   when absent (`firms.py:325-330`); every fetch call routes through it
   (`firms.py:341`, `:401`).
2. Tests: `tests/test_ingest_firms.py:229-242` assert exactly that `ValueError`
   is raised with the key unset.
3. README: `NASA_FIRMS_KEY` is listed as "Required for `ingest-firms`,
   `ingest-all`, `jobs-run --lane firms-archive`" (`README.md:337`), sourced from
   a free MAP_KEY issued by `firms.modaps.eosdis.nasa.gov/api/area/`, and stated
   to live **only on the cron/production service, never in a local `.env`**
   (`README.md:389`, citing `ingest/lanes.py:209-210`). `README.md:410` warns the
   key must never reach a log because FIRMS interpolates it straight into the
   request path.

The memory note this briefing's "no credential" framing likely traces to
(`plantgeo-pipeline-credentials`) is about a *different* NASA product — "NASA
historical lattice" (NASA POWER) is plan-blocked, not key-blocked. The same note
states plainly: *"`NASA_FIRMS_KEY` is already set on `plantgeo-ingest-cron` and
is used by the FIRMS acquisition path only."* Do not conflate the two NASA
sources.

**Licensing/redistribution constraint: UNVERIFIED in this repo.** No file in
`services/agri-data-service` or `docs/` states FIRMS' redistribution terms. NASA
Earth science data is commonly public/open with attribution, but that is general
public knowledge, not something this repo documents — confirm against FIRMS'
published use policy (`firms.modaps.eosdis.nasa.gov`) before treating downstream
Parquet republication as unrestricted, particularly if any consumer outside this
project gets read access to the object store.

## 2. Cadence

**Upstream publication:** VIIRS near-real-time (NRT) products publish per
satellite overpass; three satellites are polled (`VIIRS_SNPP_NRT`,
`VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `firms.py:83`). Observed NRT-to-ingest
lag is **UNVERIFIED** — nothing in this repo measures wall-clock delay between
satellite acquisition (`acqDate`/`acqTime`) and `geo.features.created_at` for
recent rows. Confirm with a query comparing the two on a sample of the newest
rows.

**Ingestion cadence (forward path):** hourly. `infra/cron-ingest/Dockerfile`
runs `agri-service data ingest-all` to completion on Railway's `cronSchedule`, and the
process exit code is the run's verdict (`ingest/AGENTS.md:303`). `ingest-all`
runs its jobs sequentially, not concurrently (`ingest/AGENTS.md:63`).
`FIRMS_DAY_RANGE` (default `2`, clamped `1`-`5`, `firms.py:98,181-191`) is a
rolling lookback, so a given detection is typically re-seen across 2+ hourly
runs before it ages out — re-ingestion of an already-written detection is a
correct `records_written == 0`, not a failure (see §5).

**Direct Parquet forward path:** `pipeline/direct/fire_detections.py` separately
fetches exact settled UTC days from every product whose live FIRMS availability window covers the
day, applies the archive path's SP-over-NRT identity precedence, and writes the deduplicated
0.005-degree cell-day aggregate directly to the dedicated
`layer=fire-detections/kind=observed/zoom={13,09,05,00}` namespace. Its Railway
definition is `services/agri-data-service/railway.fire-detections-forward.json`
and is deployed as the hourly `plantgeo-fire-detections-forward` Railway service. Each tick refreshes
the bounded settled window at or after `FIRE_FORWARD_START_DAY=2026-08-25`, including
already-complete direct-owned days,
so late NRT revisions are not hidden by an earlier marker. The run is bounded to a five-day lookback and a finite number
of days, fails closed above 50,000 source records per day, takes the shared
session-scoped lane-day advisory lock, polls transient contention, and retries
source/R2 failures. PostgreSQL remains the lock coordinator and the existing
FIRMS PostgreSQL ingestion entry points remain enabled; this direct publisher
does not insert the fetched rows into PostgreSQL. An operator may use
`--force-day YYYY-MM-DD` for one already-settled day inside the same bounded NRT
window when an immediate one-day source-to-R2 proof is required.

An initially empty direct-owned day may later acquire detections. Under the same lane-day advisory
lock, a complete non-empty response retracts that day’s z13 governed-absence marker immediately
before publishing data, so the forward window self-heals absence-to-data revisions. The reverse
data-to-absence transition remains an explicit manual decision and never removes published data.

**Archival/backfill cadence:** the durable `firms-archive` lane walks 1-day
chunks inside 5-day windows (`README.md:491`, matching `MAX_FIRMS_DAY_RANGE = 5`,
`firms.py:104`). Standard-processing (SP) products carry a **measured
months-long lag** between acquisition and publication — `ingest/AGENTS.md:207`:
*"the SP series' `observedAt` is acquisition time and carries a months-long lag
behind the day the product actually published."* The direct settled-day writer therefore consults
the live availability table before each fetch; it never assumes either NRT or SP coverage.

**Day-range ceiling is a measured, previously-wrong constant.** `MAX_FIRMS_DAY_RANGE`
was 10 until measured live on 2026-08-05: day ranges 1-5 answered HTTP 200, and
6/7/10 all answered `400 Invalid day range. Expects [1..5].` — the same for
dated and undated requests (`firms.py:100-104`, `ingest/AGENTS.md:93-98`).
**Latent trap for the future serving layer:** the TypeScript serving side
(`src/lib/server/services/environmental-time.ts:55,69`,
`src/lib/server/services/nasa-firms.ts:96,110`) still clamps to 10, so
`FIRMS_DAY_RANGE=7` today would *ingest* 5 days but *serve* a 7-day window — a
window the ingester never filled. Nothing sets the variable in production today
so this is latent, not live (`ingest/AGENTS.md:104-111`), but whoever builds the
DuckDB/Polars serving read path (stream S20) should not silently inherit the
10-day clamp from the old TypeScript.

## 3. Historical horizon

**Earliest obtainable:** varies by product, resolved from FIRMS' own live
availability table per chunk, never assumed (`firms.py:405-420`). The walk's
outer floor is pinned to `2000-11-01` — `MODIS_SP`'s own published `min_date`,
read from the live availability table on 2026-08-05, and the oldest floor any
`FIRMS_HISTORY_SOURCES` member offers (`VIIRS_SNPP_SP` starts `2012-01-20`,
`firms.py:110-113`). This constant only decides whether a walk is refused
outright; per-day coverage is still decided live per chunk.

**Earliest actually held:** the exact production reconciliation on 2026-08-26 found four eligible
`MODIS_SP` detections on **2000-11-01**, so the held minimum and declared lane floor now agree.
The settled PostgreSQL-to-Parquet proof covers every month through 2026-08-24; use its manifest and
the `firms-archive` job ledger together rather than inferring completeness from a sampled min/max.

Row-count context, all point-in-time measurements from `conductor/RUNBOOK.md`
and not necessarily reconciled to the same instant: 3,022,196 (§0.16.3, full
`geo.features` scan, 2026-08-20/21), 3,019,709 and 3,022,314 (two other census
passes, `RUNBOOK.md:232,740`), 3,009,567 (`geo.tile_fire_detections_detail`, a
materialized rollup, `RUNBOOK.md:4860`). Treat small deltas between these as
normal across different measurement times/queries, not as a discrepancy to
chase.

## 4. Grain

**One row = one active-fire detection** (a "hotspot") reported by a single
VIIRS or MODIS overpass — a point-in-space, point-in-time event, not a
continuous field sample.

- **Entity:** the detection itself. There is no persistent "fire" or "incident"
  entity at this grain — that concept lives in the separate `fire-perimeters`
  lane (`RUNBOOK.md:3610,4516` warn explicitly against conflating the two).
- **Natural key (frozen, do not widen):**
  `satellite:acqDate:acqTime:lat(4dp):lon(4dp)` (`identity.py:206-226`,
  `build_firms_identity`). This key is contractually byte-identical to the
  legacy TypeScript's `properties->>'id'`, which 148,460+ stored rows already
  depend on (`firms.py:718-721`). `product` (NRT vs SP, or MODIS vs VIIRS) is
  deliberately **not** part of the key — see the SP-supersedes-NRT precedence
  rule below. A Parquet warehouse schema that re-keys this lane needs to either
  preserve this identity or explicitly declare a break in detection lineage.
- **Time unit:** one acquisition instant — `acqDate` + `acqTime`, not a fixed
  daily bucket. (Existing tile-serving rollups *do* bucket by day at three grid
  resolutions; see §7.)
- **Geometry:** a single point (`{"type": "Point", "coordinates": [lon, lat]}`,
  `firms.py:307`).
- **Units/fields:**
  - `confidence` — raw, product-native scale: VIIRS emits categorical
    `l`/`n`/`h`; MODIS emits a 0-100 percentage. Both land in one
    `properties.confidence` (measured on production: 136,303 categorical rows,
    12,157 numeric, `firms.py:153-156`). A normalized `confidenceNormalized`
    (`low`/`nominal`/`high`) is also carried, banding MODIS' percentage per
    FIRMS' own published equivalence (<30 / 30-80 / >80,
    `firms.py:157-171,232-243`).
  - `brightness` (Kelvin) — VIIRS's 4µm channel (column name `bright_ti4`) or
    MODIS's `brightness`. **Omitted, never zero-filled, when a product does not
    publish it.** This corrects a historical bug: an earlier parser defaulted an
    unread channel to 0, and 0 K is physically impossible — 6,297 stored
    detections once carried that placeholder (`firms.py:201-215`). Consumers
    must check for the key's presence, not assume `0` means "not measured."
  - `frp` (Fire Radiative Power, megawatts) — present when the product
    publishes it.
  - `spatialSupportMeters` — nominal instrument pixel footprint: 375 m (VIIRS)
    or 1000 m (MODIS), omitted (never defaulted to VIIRS) for an unrecognized
    product (`firms.py:143-151,218-229`). **Measured consequence: MODIS_SP's
    median FRP is 33.10 MW against VIIRS' 4.27 MW** — roughly an order of
    magnitude, driven by pixel area (1 km² vs 0.14 km²), not fire intensity
    (`firms.py:143-147`, `ingest/AGENTS.md:159-164`). A consumer must not plot
    both products on one FRP color scale without accounting for this field.
  - `product` — the FIRMS product token the row was read from (e.g.
    `VIIRS_NOAA20_SP`), distinct from `satellite` (`N20`), because both series
    of one satellite report the same satellite token (`firms.py:283-287`).
  - `supersededProduct` — present only on a row where standard-processing (SP)
    displaced a near-real-time (NRT) row at the same natural key
    (`firms.py:700-707,710-757`). SP is the reprocessing of the same physical
    detection and wins on key collision regardless of arrival order — this is a
    precedence rule, not a dedup convenience (`firms.py:710-733`).
  - `productCoverageThrough` — the answering product's published coverage
    frontier, stamped on archive-walk rows only (`firms.py:124-130,612-617`); a
    stand-in for the `data_available_at` concept the schema does not yet model
    for FIRMS' publication lag.

## 5. Known gaps and traps

### 5.1 THE HEADLINE TRAP — the 10,000-record cap drops data silently, and looks identical to a healthy no-op

**Mechanics.** `INGEST_MAX_SOURCE_RECORDS` defaults to **10,000** (min 1,000,
max 50,000, env-overridable; `policy.py:12,27-29`). The forward FIRMS job
merges all three VIIRS NRT products into one deduplicated map, sorts newest
first by `observedAt`, and keeps only `fresh[:max_source_records]`
(`firms.py:566-570`) — **the oldest detections in that tick are the ones
dropped.** The result carries `truncated = len(fresh) > len(selected)` and
`details = {"rejected": ..., "dropped": len(fresh) - len(selected)}`
(`firms.py:573-580`).

**Measured incident:** a tick saw 12,239 detections, wrote 0 new rows (because
they had already landed on a prior tick), and silently dropped 2,239 — the
`INGEST_MAX_SOURCE_RECORDS` cap discarding real detections with no failure
surfaced (`conductor/RUNBOOK.md:2602`, memory note
`agri-firms-record-cap-drops-silently`). `INGEST_MAX_SOURCE_RECORDS` is set
**nowhere** in the repo, so production runs at the 10,000 default.

**Why "0 written" and "2,239 dropped" look like the same failure and are not:**

- `records_written == 0` is **correct, by-design idempotency.** `FeatureWriter`
  reports rows inserted plus genuinely-changed refreshes; re-walking a window
  that already landed writes zero rows on purpose, and the archive walker
  explicitly refuses to treat that as a failure trigger
  (`ingest/archive_walk.py:606`, per the memory note above).
- `dropped > 0` (in `details.dropped`, or `truncated: true`) is a **real,
  silent loss** — detections FIRMS published that never reached storage, on
  *this specific tick*. It is completely independent of `records_written`: a
  tick can write 0 rows and still have dropped detections (a busy tick that
  re-sees mostly-already-stored data, but truncates a real batch of brand-new
  ones), or write thousands of rows and drop nothing.

**How a wave-2 implementer tells the difference — read `details.dropped` /
`truncated`, never `records_written` alone:**

`IngestionJobResult.to_summary()` (`results.py:49-63`) always includes
`truncated` and `details` in the emitted JSON when they are set, and `firms.py`
always sets both (`truncated` is never `None` for this job). So the signal is
already in every forward-path log line — it is just not *acted on*:

- **Forward path (`ingest-firms`, hourly cron):** truncation is silent by
  deliberate design. `run_fire_ingestion_job` always reports
  `status="ingested"` (`firms.py:573`), and `any_job_failed()`
  (`results.py:97-99`) only inspects `status`, never `truncated` — so a capped
  tick never turns the hourly cron red. This is a documented, intentional
  trade-off (`ingest/AGENTS.md:403`): *"a forward window is whatever the
  producer publishes as 'now' ... failing there would only turn the hourly cron
  red on a busy fire day with no action to take."* **The only way to catch this
  today is to actively watch `details.dropped` / `truncated` in the job log or
  result JSON** — nothing surfaces it as an alert, dashboard flag, or non-zero
  exit code.
- **Backfill/archive path (`ingest-backfill --source nasa-firms-archive`, and
  the durable `jobs-run --lane firms-archive`):** truncation is loud. A chunk
  that hits the cap **fails and writes nothing at all** — the archive walker
  checks `result.truncated` and maps it to
  `TRUNCATED_FAILURE_CLASS = "record_cap_truncation"`
  (`archive_walk.py:135-137,621-622`); the reason names a narrower
  `--chunk-days` that would fit under the cap (`ingest/AGENTS.md:403`). Measured
  incident: the week 2022-09-04..09-10 held 60,779 published detections; a
  `--chunk-days 7` walk under the 10,000 default wrote 10,000 and discarded
  50,779 — the walk failed that chunk (writing zero of the 60,779) rather than
  silently keeping a thinner sample (`ingest/AGENTS.md:403`). The walk continues
  past a failed chunk so one over-large chunk cannot erase the ones after it.

**Recommended remediation for wave 2** (not yet done anywhere in the repo, per
the memory note and `RUNBOOK.md:2602`, which lists this as an outstanding owner
decision): raise `INGEST_MAX_SOURCE_RECORDS` on the hourly cron toward its
50,000 ceiling, and/or make the forward path check `truncated` the same way the
archive lane does, so a capped hourly tick is at minimum observable rather than
indistinguishable from a quiet day.

### 5.2 Other traps

- **Two incompatible confidence scales share one field.** See §4 — a naive
  filter on raw `properties.confidence` (`> 50`, say) silently treats every
  VIIRS row (categorical `l`/`n`/`h`) as failing the check. Use
  `confidenceNormalized`.
- **MODIS and VIIRS are different instruments, not more satellites of the same
  kind.** An unrecognized product omits `spatialSupportMeters` rather than
  defaulting to VIIRS — a wrong spatial support was judged worse than an absent
  one (`firms.py:218-229`).
- **SP-supersedes-NRT collisions are reachable, not just historical.** The
  natural key omits `product`, so `VIIRS_NOAA20_NRT` and `VIIRS_NOAA20_SP` key
  identically if they ever answer for the same acquisition. Their windows are
  disjoint upstream today (`VIIRS_NOAA20_SP` ends 2026-05-31, the day before
  `VIIRS_NOAA20_NRT` begins) but that boundary moves with reprocessing
  campaigns, and `products_covering_span` deliberately over-asks across a
  chunk's full width, so the overlap is a live possibility, not a closed case
  (`firms.py:710-733`, `ingest/AGENTS.md:126-152`). Measured on production
  2026-08-05, the collision has **not yet happened** — every stored SP row
  dates 2022-08-05..2023-01-14, every stored NRT row 2026-08-05, no shared
  acquisition day.
- **`data_available_at` is unmeasured for this exact layer.** It is confirmed
  100% NULL on eight smaller layers, but the existence probe **timed out** at
  10 s on fire-detections specifically (along with water-gauges and sensors) —
  "there is no supporting index," so this is the wrong 90% of the table to be
  guessing about (`conductor/RUNBOOK.md:898-919`). Whether the ML leakage
  boundary `drizzle/0025` added is meaningfully populated for fire-detections is
  **open**, not settled either way.
- **`fire-perimeters` is a different layer and is missing `observedAt` on
  100% of its rows.** Do not import that gap's fix (or its absence) into this
  lane — fire-detections is unaffected (`RUNBOOK.md:891-896,3610,4516`).
- **Row-count precision is inherently approximate across sources.** Several
  slightly different totals exist for fire-detections depending on which table
  and which instant was queried (§3). None of them is "the" count; a validation
  pass should always name its own query and instant rather than compare against
  a number quoted from memory.

## 6. Validation approach

`pipeline/validation/fire-detections.py` (per `layer-lanes.md` §4) must
reconcile what was *written* against what FIRMS itself *holds* — never against
this pipeline's own intermediate state.

1. **Read the job result's `truncated`/`details.dropped` fields on every run**,
   forward and archival alike (§5.1). This is the primary, cheapest signal and
   it already exists in the code today — it is simply unconsumed on the forward
   path. A validation pass that only checks `records_written` will report a
   capped tick as healthy.
2. **Cross-check against FIRMS' own availability table**
   (`fetch_product_availability`, `firms.py:399-402`) for the day(s) in
   question. A day no product's `min_date..max_date` window covers is a
   **governed absence** — "no product publishes this day," not "FIRMS had no
   fires" — and must be recorded as such, never inferred from a zero-row
   result. The archive walker already distinguishes this case explicitly and
   logs it as `firms_history_spans_uncovered` (`firms.py:676-682`) when it
   happens; that event is the model for what the validation lane should persist
   as a governed-absence record rather than silently allow to fall through as
   an apparent gap.
3. **Re-fetch and count, don't just trust the write count.** For a spot-check
   day, re-request the same dated-area CSV FIRMS itself would answer with
   (`FIRMS_AREA_CSV_DATED_TEMPLATE`) and compare its row count against what
   this lane stored for that day, watching specifically for the gap between
   "rows FIRMS answered with" and "rows actually written" — that gap is exactly
   `rejected` (failed identity/freshness build) plus `dropped` (cap truncation),
   both already itemized in `IngestionJobResult.details`
   (`firms.py:580`).
4. **Report a named day, lane, and source response — never a bare row-count
   delta.** Per `layer-lanes.md` §4: `"N rows mismatched"` is not actionable.
   State the day, which product(s) were asked, what they answered, and what was
   written.
5. **Do not conflate `nasa-firms` and `nasa-firms-archive` validation.** They
   share an identity contract but different freshness rules (`max_observation_age`
   is the rolling day-range for the forward job, unbounded for the archive job
   subject only to the future-skew guard, `firms.py:493-497,777-779`) — a
   validation query written against one path's semantics will misjudge the
   other's.

## 7. Forecast recommendation

**`horizon: 30d`** — but only if the forecast targets an aggregate quantity,
not raw detection points. Reasoning through the "is this even meaningful"
question this brief asked for, rather than defaulting to the classification
table:

- **Raw per-detection forecasting does not make sense.** This lane's natural
  grain (§4) is one row per discrete, high-cardinality, event-driven hotspot at
  a specific point and instant. There is no statistically defensible way to
  project the exact latitude/longitude of a fire detection 1-30 days from now —
  unlike `weather-observations` or `vegetation`, which forecast a value on an
  existing continuous field at fixed grid cells, fire ignition is not a field
  sample.
- **`layer-lanes.md` §2 requires identical grain between `kind=observed` and
  `kind=forecast`** ("a forecast row and an observed row for the same cell-day
  differ in `kind` and provenance, never in shape"). Taken literally against the
  raw per-detection grain, this contract is unsatisfiable for fire-detections —
  a forecast module cannot emit synthetic future point detections and call them
  observations-shaped.
- **The resolution is an aggregate grain the repo already uses in production.**
  The existing tile-serving layer independently rolls this exact layer up to
  **grid-cell × day**, at three resolutions —
  `geo.tile_fire_detections_z9` (0.005° cell), `_z6` (0.02°), `_z0` (0.1°), each
  carrying `detection_count`, `max_frp`, `max_confidence` per cell per day
  (`conductor/RUNBOOK.md:4860-4863,4891`). This is precedent, not invention: a
  cell-day detection-count/FRP-sum aggregate is both (a) the thing a Monte Carlo
  ensemble can meaningfully project (a count/intensity process has a coherent
  30-day distribution; an exact future coordinate does not) and (b) already the
  shape this data gets reduced to for every consumer that isn't reading raw
  detail rows.
- **Recommended projected quantity:** expected detection count and/or summed
  fire-radiative-power per grid cell per day, over the same three (or a chosen
  subset of) cell resolutions already in use — reusing an existing, validated
  spatial rollup rather than inventing a new one for the forecast module alone.
  This still needs an owner/S9 decision on which specific cell size(s) the
  `warehouse/schemas/fire-detections.py` observed stream adopts as its canonical
  grain for forecast-comparison purposes; this document takes no position on
  that file or its columns, per the constraint at the top of this brief.
- **This does not contradict `RUNBOOK.md` §0.24.2's "yes" classification**
  (`RUNBOOK.md:3342`) — it makes explicit what "yes" has to mean for this lane
  to satisfy the lane contract at all, since per `layer-lanes.md` §2 each lane's
  own `AGENTS.md`/contract declares its horizon and "that declaration wins."
