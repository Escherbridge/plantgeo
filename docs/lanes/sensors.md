---
type: lane-contract
---

# `sensors` lane

Written for the agent that will build `warehouse/schemas/sensors.py`, `pipeline/lanes/sensors.py`,
`pipeline/validation/sensors.py`, `method/monte_carlo/sensors.py` and `planes/sensors.py` per
[`conductor/code_styleguides/layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md) §1, and
per [`conductor/RUNBOOK.md`](../../conductor/RUNBOOK.md) §0.23–§0.24 (the Postgres→Parquet pivot) and
§0.24.2 (this lane's row in the eleven-lane table). No Parquet path, filename or column list is
asserted here — that contract is being written concurrently by another agent this session.
Everything below is cited to a `path:line` in this repo; anything the repo does not establish is
marked **UNVERIFIED** with what would confirm it.

## 1. Source system

- **Publisher**: NOAA's National Weather Service, via `api.weather.gov` — the same public
  ground-station network api.weather.gov aggregates from ASOS, RAWS, NonFedAWOS, HADS, MesoWest and
  APRSWXNET
  (`services/agri-data-service/src/agri_data_service/ingest/sensors.py:650-653`, docstring of
  `run_sensor_ingestion_job`).
- **Endpoint**: `https://api.weather.gov`
  (`ingest/sensors.py:58`, `NWS_API_BASE_URL`). Two calls compose the lane: `GET /stations` to drain
  a per-state roster (`ingest/sensors.py:59`, `NWS_STATIONS_URL`), then either
  `GET /stations/{id}/observations/latest` (forward poll) or
  `GET /stations/{id}/observations?start=…&end=…` (bounded history walk) per station
  (`ingest/sensors.py:336-347`, `observation_url`).
- **Auth**: none. api.weather.gov issues no API key and checks no token; it only asks a caller to
  identify itself via `User-Agent`, which is why the code comment is explicit that this is an
  identification header, not a credential (`ingest/sensors.py:62-65`). `NWS_API_USER_AGENT`
  (default `"plantgeo-agri-data-service"`) is read at call time, never cached
  (`ingest/sensors.py:140-142`).
- **Licensing constraint**: **UNVERIFIED**. Nothing in this repo records a license, attribution
  requirement, or redistribution term for NWS/api.weather.gov data — no `evidence_source` entry for
  it in `docs/ecological-knowledge-source-register.md`, and no comment analogous to the NASA POWER
  license citations in the `weather-observations` lane. U.S. federal agency data is conventionally
  public domain under 17 U.S.C. §105, and that is consistent with NWS issuing no key at all, but the
  repo asserts nothing about redistribution rights. Confirm by reading
  `https://www.weather.gov/disclaimer` directly before this lane exports NWS station data past
  internal serving (e.g. into a public download or a third-party feed).

## 2. Cadence

- **Ingestion schedule**: `infra/cron-ingest/railway.json:7` runs `cronSchedule: "0 * * * *"` —
  hourly, on the Railway `plantgeo-ingest-cron` service (per project memory
  `plantgeo-railway-topology`). `ingest-sensors` is one of six jobs `ingest-all` runs sequentially
  each tick (`ingest/AGENTS.md:63`, "Deliberate deviations" §1). There is no dedicated
  `infra/cron-sensors/` — unlike `weather-observations` (`cron-weather`, `10 * * * *`),
  `water-gauges` (`cron-streamflow`, `*/30 * * * *`) or `fire-perimeters` (`cron-fire-perimeters`,
  `20 * * * *`), all declared with an explicit `cadence_basis` in
  `ingest/validation/models.py:100-141`.
- **Upstream publication rate is per-station and irregular, not fixed.** A station is not
  contractually bound to one reading per hour: the migration-0038 header measured "~13 rows per
  station per day, up to 30 — KMSO, KDLN and KGPI each published 30 separate features for
  2026-08-06 alone" (`drizzle/0038_tile_low_zoom_routing.sql:55-59`), consistent with ASOS's
  hourly METAR plus irregular SPECI (special) reports. There is no per-station guarantee that a
  reading lands every hour, and none is enforced anywhere in this code.
- **This lane's own `StreamDefinition` declares no cadence.** `ingest/validation/models.py:142`:
  `StreamDefinition(stream="sensors", kind="snapshot", store="features", cadence_basis=None)`. Every
  other feature-backed time-series lane in that table (`fire-detections`, `water-gauges`,
  `weather-observations`, `vegetation`, `fire-perimeters`, `evacuation-zones`) cites the cron file
  and schedule that justifies its declared cadence; `sensors` cites nothing.
  `lane_publication_cadence_days` (`ingest/validation/models.py:168-178`) falls back to the daily
  default for an unclaimed lane rather than failing, so gap detection currently treats `sensors` as
  if it published once a day — an assumption nobody measured against the actual per-station rate
  above.
- **Observed lag**: not separately measured. The forward job polls the `/latest` endpoint per
  station once an hour and writes whatever NWS has published by then
  (`ingest/sensors.py:583-585`, `fetch_current_sensor_records`); how stale NWS's own `/latest`
  answer is relative to the reading's own `timestamp` is **UNVERIFIED** — nothing in this repo
  measures observation-to-ingest lag for this producer specifically.

## 3. Historical horizon

- **Earliest obtainable from the source, ever, for any run**: a rolling ~6-day window.
  `NWS_OBSERVATION_RETENTION: Final = timedelta(days=6)` (`ingest/sensors.py:96`), with the code
  comment: "api.weather.gov keeps a rolling week of per-station observations. Six days is the depth
  a live bisect actually returned records at, so six days is the only depth this source will
  promise" (`ingest/sensors.py:94-96`). `nws_sensor_source` stamps `HistoryCapability(supported=True,
  earliest=moment - NWS_OBSERVATION_RETENTION)` at call time, per run, not as a frozen constant
  (`ingest/sensors.py:593-611`), because the floor itself rolls forward every day. **There is no
  deeper archive for this producer** — unlike FIRMS or USDM, NWS does not publish a separate
  standard-processing/reanalysis product that reaches further back. Whatever this lane does not
  capture on a given hour is gone from the source within about a week.
- **`nws-sensors` is a registered backfillable source** (`ingest/commands.py:321-344`,
  `_build_backfillable_sources()` — one of only four sources this function returns, alongside
  `sentinel2-ndvi`, `firms-archive-source`, `usgs-streamflow-archive-source`), but it is **not a
  registered lane**: "`vegetation` and `sensors` are ingest-backfill-capable but are not registered
  lanes: closing a reported gap requires a human to notice the report and run the CLI"
  (`conductor/RUNBOOK.md:1027-1028`). A backfill for this lane is therefore a manual CLI run, not an
  automatic response to a detected gap, and it can only ever reach ~6 days back regardless of when
  it is run.
- **Earliest actually held, as measured**: `drizzle/0038`'s header, measured 2026-08-21, records
  "625 distinct locations · 23 distinct days" layer-wide (`drizzle/0038_tile_low_zoom_routing.sql:24`).
  An earlier measurement on 2026-08-16 recorded "sensors spans 17 days" (`conductor/RUNBOOK.md:1690`).
  This is **consistent with, but not a substitute for, a direct query**: `ingest/sensors.py` (the NWS
  puller — the only producer this layer has ever had, per its own docstring at
  `ingest/sensors.py:648-650`: "The sensors layer has always had a push endpoint
  (`src/app/api/ingest/sensors/route.ts`) and no producer, which is the whole reason it holds zero
  features. This job is that producer") was first added in commit `54bedc5`, dated **2026-08-04**
  (`git log --follow --diff-filter=A -- services/agri-data-service/src/.../ingest/sensors.py`). 17
  days of coverage measured 12 days after that commit, and 23 days measured 17 days after it, both
  land almost exactly at "days since the producer started" plus the ~6-day rolling backfill window
  the first run could have reached — i.e. an earliest observation day of roughly **2026-07-29 to
  2026-08-04**. This date is a **derivation from three independent measurements, not a value read
  directly off a table**; confirm with `SELECT MIN(geo.feature_observation_day(properties)) FROM
  geo.features f JOIN geo.layers l ON f.layer_id = l.id WHERE l.name = 'sensors' AND f.status =
  'published'` before treating it as settled.
- Row growth continued between measurements: 149,466 (2026-08-16, `conductor/RUNBOOK.md:3592`) →
  180,654 (2026-08-20) → 184,733 (2026-08-21 early) → 186,904 (2026-08-21, post-dedup-fix
  measurement) → 745,755 tile bytes after `DISTINCT ON` (`conductor/RUNBOOK.md:236,742,804,2896`,
  `drizzle/0038_tile_low_zoom_routing.sql:22-32,381`). The layer accretes roughly 625 raw rows per
  hour it is fully populated and is monotonically append-only — nothing in this producer deletes or
  supersedes a stored row.

## 4. Grain

**Two different grains coexist today, and the gap between them is the design question this section
exists to hand to wave 2.**

1. **Stored grain (what `geo.features` actually holds, one row per):** `(station, upstream
   observation instant)`. The natural key is `stationIdentifier:timestamp`, byte-identical to the
   live push route's `${sensor_id}:${timestamp}` (`ingest/sensors.py:428-447`,
   `build_sensor_reading_identity`). One row's `properties` carries a `readings` object — a sparse
   dict keyed by whichever of sixteen possible NWS measurement fields the station actually reported
   for that instant: `temperature`, `dewpoint`, `relativeHumidity`, `windDirection`, `windSpeed`,
   `windGust`, `barometricPressure`, `seaLevelPressure`, `visibility`,
   `precipitationLastHour/Last3Hours/Last6Hours`, `maxTemperatureLast24Hours`,
   `minTemperatureLast24Hours`, `windChill`, `heatIndex`, plus a free-text `textDescription`
   (`ingest/sensors.py:104-121,367-377`). A station that does not report a given measurement omits
   the key entirely; nothing is defaulted or zero-filled (`ingest/sensors.py:350-364`,
   `_measurement`). Each value keeps NWS's own `unitCode` and `qualityControl` verbatim — **units
   are never normalized in this lane**; a consumer must read `unitCode` per reading, not assume a
   fixed unit across rows.
   - **Entity dimension**: `entity_local_id` is the bare station id (not the reading), so
     `geo.geometry` keys one Type-2 version chain per station rather than per reading — a ground
     station never moves, and the position is confirmed/versioned as readings arrive
     (`ingest/sensors.py:433-439`, `ingest/AGENTS.md`'s identity.py paragraph). This entity/reading
     split should carry into the Parquet warehouse schema: it is what keeps a station's identity
     stable while its readings accumulate.
2. **Served grain (what the map tile actually draws, one feature per):** `(sensor_id, geom,
   observation_day)` — the **latest** reading of each day, everything earlier in that day dropped
   at read time by `DISTINCT ON` in `geo.sensor_tiles`
   (`drizzle/0038_tile_low_zoom_routing.sql:399-427`). This is a **read-time SQL reduction, not a
   stored table** — the 186,904+ raw rows in `geo.features` are untouched by it; the migration
   header is explicit that "this file's `DISTINCT ON` is the same reduction computed on the fly: it
   produces the right ANSWER but still reads all 47,842 rows to get there... A sensors equivalent
   needs a table keyed (observation_day, cell) holding one row per station-day with a pre-reduced
   geometry, plus a producer and a refresh — a layer-lane change, not a function rewrite"
   (`drizzle/0038_tile_low_zoom_routing.sql:199-207`). **That layer-lane change is this Parquet
   lane.**

**The consequence for the served grain is narrower than it first looks — read this before choosing
the Parquet grain.** The tile function's `SELECT` list is `id, geom, network, sensor_id,
station_name, observed_at, observed_day` — it never emits the `readings` object
(`drizzle/0038_tile_low_zoom_routing.sql:397-431`). Checked independently: the map's own hover
tooltip for `sensors` shows only network, station ID and observed timestamp
(`src/lib/map/hover-fields.ts:133-140`, `formatSensorStation`), and the agent's
`feature_value_near_point` tool projects properties through a single hand-spelled allowlist shared
across all eleven feature layers, `FEATURE_PROPERTY_KEYS`
(`services/agri-data-service/src/agri_data_service/agent/tools.py:176-219`) — it contains `network`,
`observedAt`, `id`, `name`, `status`, `source` and similar generic keys, but **no `readings`,
`sensor_id`, `station_name`, `temperature`, or any other measurement field**. So today, **no serving
surface in this repo — not the map, not the agent — exposes a single actual weather reading from
this lane.** The temperature/wind/pressure/precipitation data this producer has been capturing
hourly since 2026-08-04 is captured and stored, and nothing downstream reads it.

**Recommendation for the Parquet `kind=observed` grain, given the above and given the layer must also
serve a 30-day forecast (layer-lanes.md §2 requires observed and forecast to share one grain):**
store **both**, in the sense that the observed-day reduction is derived, not primary:

- The day-grain `(sensor_id, day)` — one row holding the day's readings, however wave 2 chooses to
  reduce them (latest-of-day, to match today's tile logic exactly; or min/max/avg per measurement,
  which changes the tile's current "latest reading" semantics and should be a deliberate decision,
  not a silent one) — is the grain a 30-day Monte Carlo forecast should share, because
  `kind=observed` and `kind=forecast` partitions must have identical shape (layer-lanes.md §2). This
  is also the grain the map currently renders (one dot per station per day).
- Whether the **raw sub-daily readings** also get their own Parquet stream is a genuinely open
  product question this document does not resolve: nothing currently reads them, but they are the
  only place any of the sixteen actual measurements survive, and collapsing to one row per day
  before ever serving them once would make that data permanently inaccessible below daily
  resolution. Recommend deciding this explicitly rather than defaulting to whichever grain the
  export happens to be easiest to write — the cost of storing both is measured to be small (10.7 KB
  gzipped per day at 625 stations per the tile-byte figures above) and dropping the raw grain is not
  reversible without re-polling NWS, which is impossible past ~6 days.

## 5. Known gaps and traps

- **The dedup fix lives only in `geo.sensor_tiles`, and it is dormant in the migration ledger.**
  `drizzle/0038` was applied by hand to production 2026-08-21 (`conductor/RUNBOOK.md:2879`,
  `:4244`) but **`drizzle/meta/_journal.json` was deliberately not updated**
  (`drizzle/0038_tile_low_zoom_routing.sql:253`, "It ships DORMANT... matching 0030 and everything
  after it"). It is one of **seven migrations applied-but-unregistered**
  (`conductor/RUNBOOK.md:2949`, §0.21.6) that the owner has chosen not to reconcile, preferring a
  greenfield reset once Parquet lands (`conductor/RUNBOOK.md:2941-2942`). A fresh database built
  from `drizzle db:migrate` alone will **not** have this fix; only the live production Postgres
  does. Do not assume any local/CI Postgres reflects it.
- **Readings are captured but unserved everywhere** — see §4. Building a 30-day forecast over a
  quantity nothing currently displays is a real risk of shipping unused work; confirm with the owner
  whether serving actual readings is in scope for this wave before investing in per-measurement
  forecasting.
- **No source-reconciling validation exists for this lane today.** `pipeline/validation/sensors.py`
  does not yet exist (it is this wave's deliverable). The only existing check is `validate-streams`,
  which compares the warehouse against its own stored census, not against NOAA
  (`conductor/RUNBOOK.md:1015-1017`: "`validate-streams` detects and reports gaps for all registered
  streams every hour. For the other nine layers nothing converts a detected hole into a claimable
  `job_work_item`"). Per §2, that census currently applies the *default daily* cadence to this lane
  because `cadence_basis=None` — an assumption nobody has checked against the real ~13–30
  reports/station/day rate.
- **Governed absences are structurally unimplemented for every layer including this one**
  (`conductor/RUNBOOK.md:1031-1035`): a day NWS genuinely could not answer for is not currently
  persisted as a certified absence anywhere, so a real gap and a not-yet-checked gap look identical
  today.
- **Coverage is capped, deterministic, and silently narrows.** `SENSOR_MAX_STATIONS` defaults to 750
  (`ingest/sensors.py:85`); the live roster (625 stations,
  `drizzle/0038_tile_low_zoom_routing.sql:24,55`) sits under that cap today, but the roster is capped
  by sorting `station_identifier` alphabetically and truncating
  (`ingest/sensors.py:330-333`) — if the roster ever grows past 750 (a wider bbox, an added
  network), the stations dropped are whichever sort *after* the cutoff, silently, with only a debug
  log (`ingest/sensors.py:305-306` covers roster-page truncation; the station-count cap itself logs
  nothing when it binds).
- **Only four states are drained by default**, `("WA", "OR", "ID", "MT")`
  (`ingest/sensors.py:78`, `DEFAULT_STATION_STATES`) — chosen because "of the states this box
  touches, only these four place any station inside `PACIFIC_NORTHWEST_COVERAGE_BBOX`; NV/CA/UT
  rosters sit south of the 42N line" (`ingest/sensors.py:74-77`). This list must be widened by hand
  if `INGEST_BBOX` ever moves; the bbox filter itself is authoritative regardless, but a state whose
  roster is never drained contributes zero stations no matter what the bbox says.
- **Only four named networks are ingested by default**, `("ASOS", "ASOS-HFM", "RAWS",
  "NonFedAWOS")` (`ingest/sensors.py:83`) — roughly 1,350 additional in-box stations whose
  `provider` field is blank are excluded on purpose rather than ingested unlabelled
  (`ingest/sensors.py:80-82`).
- **A full historical backfill cannot complete in one run.** `INGEST_MAX_SOURCE_RECORDS` defaults to
  10,000 records per run (`ingest/policy.py:27`), while a full 6-day/625-station history walk is on
  the order of 90,000+ readings (625 stations × up to ~144 readings over 6 days at the measured
  13–30/day rate). `collect_sensor_records` stops at the ceiling rather than buffering the whole
  sweep and logs a truncation warning (`ingest/sensors.py:543-557`) — a backfill into Parquet needs
  to chunk explicitly, not assume one CLI invocation captures the full rolling window.
- **The roster pagination cursor never terminates on its own.** api.weather.gov keeps returning a
  `pagination.next` link past the end of a state's actual roster; the walk is bounded by
  `feature_count == 0`, not by cursor absence, with a hard `MAX_STATION_PAGES_PER_STATE = 30` ceiling
  and a warning log if that caps a real roster (`ingest/sensors.py:265-271,294-306`).
- **`geo.feature_observation_day` must stay `IMMUTABLE`-returning-`date`** for the tile's
  `DISTINCT ON`/`ORDER BY` pair to agree within one row
  (`drizzle/0038_tile_low_zoom_routing.sql:330-338`) — an analogous invariant will matter for
  whatever function derives the Parquet day partition from `observedAt`.
- **`observedAt` is stored as NWS's verbatim ISO-8601 string, not a parsed timestamp**, kept
  specifically so `geo.feature_observation_day` reads "the day the PUBLISHER named, not a re-zoned
  one" (`ingest/sensors.py:465-474`). Re-deriving day boundaries by parsing-and-re-zoning in the
  Parquet exporter would silently disagree with what the current tile (and any downstream
  comparison against it) considers "the same day".

## 6. Validation approach

Nothing source-reconciling exists for this lane yet (see §5). What layer-lanes.md §4 requires of
`pipeline/validation/sensors.py` maps onto this source's specific shape as follows:

- **Reconcile against api.weather.gov itself**, not against the Parquet the lane just wrote — e.g.
  re-fetch a station's roster/`/latest` and compare against what was captured for that hour, the same
  class of check `fetch_station_roster` already performs for freshness
  (`ingest/sensors.py:310-333`).
- **The only reconciliation window this source will ever support is ~6 days** (§3) — a validation
  job that tries to check "did we capture everything NWS published on day N" for any N older than
  that is checking a question NWS itself can no longer answer; it must record "unverifiable, source
  window closed" rather than a false pass or a false gap.
- **A missing hour for a specific station is not evidence of a warehouse defect** — a station can
  legitimately go offline, and NWS's own roster can drop or re-add a station between polls. Validate
  at the roster level (is the station still published by NWS at all) before treating a station's
  silence as this lane's failure.
- **The undated-day path is not exempt for this lane.** `kind="snapshot"` only skips the
  `UNDATED_DAY_CHECK` for `kind="reference"` layers (`ingest/validation/report.py:60,90`); `sensors`
  is `"snapshot"`, not `"reference"`, so a record `build_sensor_reading_write` cannot date (there are
  none observed — every accepted record already passed `build_sensor_reading_identity`'s
  `_parse_upstream_timestamp`, `ingest/sensors.py:414-422,450-460`) would still be checked. Confirm
  this class of record genuinely cannot occur before assuming the check is moot for this lane.
- **A cadence needs to be declared, not inherited from the daily default.** Per §2, nothing has
  measured this lane's real publication rate to set `cadence_basis` deliberately; do that
  measurement (or record why per-station cadence is not the right gap-detection unit for a
  station network, e.g. gap detection per station rather than per layer-day) before wiring
  `pipeline/validation/sensors.py` to a specific expectation.

## 7. Forecast recommendation

**`horizon: 30d`** — per `conductor/RUNBOOK.md:3339` (§0.24.2's eleven-lane table: `sensors | station
network | **yes**`). Ground-station point measurements (temperature, humidity, wind, pressure,
precipitation) are the same physical category `weather-observations` already forecasts, and the
lane's whole purpose is to serve the map a plausible near-future reading at a station the user is
looking at.

Two things should shape *how* this lane implements that horizon, both flowing from findings above:

- **Train the Monte Carlo on the day-grain (§4's recommended `kind=observed` shared grain), not on
  raw sub-daily readings.** The forecast and observed streams must share grain, units and column
  names (layer-lanes.md §2); the day grain is also the only one currently rendered, so it is the
  version of "sensors" a forecast is actually standing in for on the days it projects.
- **This lane's own history is too shallow to fit a Monte Carlo from alone, today.** §3 puts the
  warehouse's own record at roughly three weeks (measured 17→23 days over 2026-08-16→08-21) and
  growing by one day per day — there is no deeper archive to backfill from (§3's 6-day upstream
  ceiling). A 30-day-ahead ensemble fit on three weeks of trailing history is fitting noise. Two
  ways forward, and this document does not pick one: (a) wait — the record deepens on its own every
  day this producer keeps running, so a fixed minimum-history gate (e.g. "do not ship a forecast for
  a station until it has N observed days") is cheap and honest; or (b) borrow structure from
  `weather-observations`' longer NASA POWER/ERA5 record for the same geography as an informative
  prior, since both lanes cover the same physical quantities over the same Pacific Northwest
  footprint — this is a genuine cross-lane statistical choice, not a code coupling, and does not
  violate layer-lanes.md's "a lane never imports another lane" rule (§1) as long as it is implemented
  as a documented input to this lane's own fitting code rather than an import of
  `method.monte_carlo.weather_observations`.
- **Projected quantity — recommend scoping down from all sixteen measurement fields.** §4 and §5
  establish that no serving surface reads any measurement field today; forecasting all sixteen
  (`temperature`, `dewpoint`, `relativeHumidity`, `windDirection`, `windSpeed`, `windGust`,
  `barometricPressure`, `seaLevelPressure`, `visibility`, three precipitation windows, two 24-hour
  extremes, `windChill`, `heatIndex`) before any of them is served would be building a forecast for
  numbers nobody can see. Recommend forecasting the small set the legend/hover UI would need if §5's
  "readings are unserved" gap is closed in this wave (temperature and precipitation are the two most
  legend-relevant, by analogy with `weather-temperature`'s existing hover formatter,
  `src/lib/map/hover-fields.ts:371`) and treating the rest as available-but-not-yet-forecast, rather
  than building sixteen provenance-carrying ensembles this wave has no consumer for.
