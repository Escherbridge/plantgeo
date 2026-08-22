---
type: lane-contract
---

# `burn-severity` lane

Written for the agent that will build `warehouse/schemas/burn-severity.py`,
`pipeline/lanes/burn-severity.py`, `pipeline/validation/burn-severity.py`,
`planes/burn-severity.py` and, per §7 below, **no** `method/monte_carlo/burn-severity.py`,
per [`conductor/code_styleguides/layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md) §1.
No Parquet path, filename or column list is asserted here — that contract is being written
concurrently by another agent this session (S0). All facts below are cited to the file/line
that establishes them; anything the repo does not establish is marked **UNVERIFIED** with what
would confirm it.

## 1. Source system

- **Publisher**: MTBS (Monitoring Trends in Burn Severity), a joint USGS EROS / USDA Forest
  Service GTAC program (`services/agri-data-service/src/agri_data_service/ingest/mtbs.py:146`,
  `MTBS_SOURCE_OWNER`).
- **Endpoint**: `https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query`
  — the USDA Forest Service Enterprise Data Warehouse (EDW) ArcGIS Server, layer "Burned Area
  Boundaries (All Years)" (`mtbs.py:68-70`). **Not** the endpoint the retired TypeScript service
  used (`MTBS_Polygons_v1` on NIFC's ArcGIS Online org) — that one answers HTTP 400 and does not
  exist in that org's catalogue (`mtbs.py:65-67`;
  `services/agri-data-service/src/agri_data_service/ingest/AGENTS.md:311`). An Esri ArcGIS Online
  mirror of the same layer exists and is **deliberately not used**, specifically so no ArcGIS
  Online terms of service attach to this capture (`mtbs.py:169-171`).
- **Auth: confirmed, none required.** The request carries only a `User-Agent` and `Accept`
  header (`MTBS_REQUEST_HEADERS`, `mtbs.py:193-195`); every query parameter is public (`where`,
  `geometry`/`geometryType`/`inSR`/`outSR`/`spatialRel`, `outFields=*`, `orderByFields=fire_id`,
  paging params, `f=geojson`) and `build_release_query_parameters` explicitly documents itself as
  recording "exactly how a cohort was requested, without the credentials the contract forbids"
  (`mtbs.py:549-564`). No API key, token, or Authorization header anywhere in the module.
- **Licensing**: U.S. federal public domain. The governed license text this repo actually
  snapshots into `agri.source_release.license_snapshot` is: *"USGS/USFS MTBS data release; U.S.
  federal public domain (FGDC access constraints None, use constraints: no restrictions beyond
  reasonable and proper acknowledgement of sources). Obtained via the USDA FS EDW ArcGIS Server;
  hosting terms are separate."* (`MTBS_LICENSE_NAME`, `mtbs.py:149-153`), citing DOI
  `10.5066/P9IED7RZ` (`MTBS_LICENSE_URL`, `mtbs.py:154`).
  - **Live constraint, carry it forward:** `allowed_client_exposure` stays `false` for this
    source — *"nothing MTBS-derived may reach a public CDN without a fresh licensing review"*
    (`mtbs.py:171`). This is a real, currently-enforced restriction, not boilerplate; whatever
    serves burn-severity out of Parquet/PMTiles must preserve it until someone does that review.
  - A **separate, stale-looking catalogue entry** exists at
    `services/agri-data-service/src/agri_data_service/ingest/validation/source_manifests.py:403-415`,
    key `"mtbs-burn-severity"`: `licence_identifier="CC0"`, `adapter_status="planned"`,
    `refresh_cadence="annual, 1984-2024"`. **Do not trust this entry for current state** — the
    ingest module is fully implemented and cron-scheduled (see §2), so `adapter_status="planned"`
    is wrong today (compare `source_manifests.py:9`: only rows carrying `adapter_status
    ="implemented"` are current). `CC0` is also a looser claim than the specific federal-public-domain
    text `mtbs.py` actually governs by. Treat `mtbs.py` as authoritative for this lane, not this
    manifest row.

## 2. Cadence

- **MTBS's own publication rhythm is quarterly**, not annual: "early February, May, August and
  November" (`mtbs.gov/data-availability`, quoted at `mtbs.py:104`). A single fire-year cohort
  accretes across several of these quarterly releases spanning **two to four calendar years**
  before MTBS calls it complete (`mtbs.py:101-107`; confirmed range in
  `docs/reports/evidence/mtbs-release-announcements-2026-08-10.md` — e.g. fire year 2019 opened
  2021-04-21 and closed 2021-09-27, fire year 2020 spanned 2022-02-15→2022-04-28).
- **This repo's ingest cron runs weekly**, `55 7 * * 2` (Tuesdays 07:55 UTC),
  `infra/cron-mtbs/railway.json`, `startCommand: agri-cli ingest-mtbs` — chosen specifically to
  pick up a new quarterly release within a week while staying far cheaper than the daily lanes
  (`ingest/AGENTS.md:317`). Re-running is cheap and idempotent: the writer's diff rejects an
  unchanged payload, and the geometry adapter confirms an unchanged shape when nothing moved
  (`mtbs.py:1154-1156`).
- **Observed lag has three layers, and they compound:**
  1. MTBS's own mapping lag, ignition → publication, averages roughly 18 months and is
     enforced to be **at least 180 days** by `MIN_RELEASE_LEAD`
     (`mtbs.py:207-209`, `validate_release_window`, `mtbs.py:374-399`) — a release that claims to
     lead its cohort's last ignition by less than that is treated as an ignition-shaped date and
     rejected, never trusted.
  2. **This repo's own cron did not exist until 2026-08-10.** Before that, `ingest-mtbs` was a
     working CLI verb with nothing ever invoking it — deliberately excluded from `ingest-all`
     because a weekly-shaped feed would be pure waste on an hourly schedule, but the unintended
     consequence was that *nothing* ran it: 478 rows landed once on 2026-08-05 and the layer sat
     untouched until the cron landed (`ingest/AGENTS.md:317`).
  3. **Governance lag**: a fire year is not ingestible at all until a human manually confirms and
     dates its completion in `MTBS_ANNUAL_RELEASE_DATES` (`mtbs.py:113-142`) — see §3. Fire year
     2019 sat out "by oversight, not by evidence" until this was done on 2026-08-10
     (`ingest/AGENTS.md:315`). A MTBS release can be published and this layer can still not
     reflect it for an unbounded time, for reasons unrelated to the cron.

## 3. Historical horizon

- **Earliest technically obtainable from MTBS**: fire year **1984** — MTBS's stated program
  scope is "every large wildland fire... 1984 onward" (`MTBS_PURPOSE`, `mtbs.py:160-164`;
  `MIN_IGNITION_YEAR = 1984`, `mtbs.py:214`).
- **Earliest actually held/ingestible today: fire year 2018**, and only **five** fire years total
  — `MTBS_ANNUAL_RELEASE_DATES` carries dated, defensible completion entries for **2018, 2019,
  2020, 2021, 2022 only** (`mtbs.py:113-142`). Any other ignition year raises
  `MtbsReleaseNotPublishedError` with **no fallback** — confirmed by a parametrized test asserting
  exactly this for `[1984, 2017, 2023, 2024, 2025, 2026]`
  (`services/agri-data-service/tests/test_ingest_mtbs.py:362-365`).
- **This is a declared, typed refusal, not a silent gap.** The layer's `HistoryCapability` is
  explicitly `supported=False`, with reason: *"MTBS's ingestible history is exactly the set of
  fire years carrying an established release publication date, and every run of this job captures
  all of them. The fire years absent from `MTBS_ANNUAL_RELEASE_DATES` are a governance gap
  awaiting a dated release announcement, not a past window a backfill could walk."*
  (`MTBS_NO_HISTORY_REASON`, `mtbs.py:973-978`, wired at `mtbs.py:1107`). This satisfies
  `docs/layer-lane-standard.md`'s requirement that every layer declare "a horizon, or a typed
  refusal with a reason" (`docs/layer-lane-standard.md:306`) — the Parquet lane should carry the
  same refusal forward rather than inventing a new one.
- **A real, quantified backlog exists inside MTBS's own archive**, not yet acted on:
  - Fire years **2016 and 2017 "look similarly resolvable" but were deliberately left alone** —
    the 2019-08-29 release states it contains "all 2017 fires," but nobody has done the
    corroboration work that was done for 2019 (`ingest/AGENTS.md:315`).
  - Fire years **1984–2015 have never been evaluated at all** for a defensible completion date —
    UNVERIFIED whether they are even completable the same way; confirming this means repeating
    the announcement-chronology exercise in
    `docs/reports/evidence/mtbs-release-announcements-2026-08-10.md` for each cohort.
  - Fire years **2023 and 2024 are excluded on purpose, not by oversight**: MTBS states outright
    it is still mapping them, targeting "end of FY2026" (2026-09-30) —
    `docs/reports/evidence/mtbs-release-announcements-2026-08-10.md` §"2023 → NO DEFENSIBLE DATE"
    and §"2024 → NO DEFENSIBLE DATE". **Do not add either year before that date passes**, and even
    then confirm a genuine completion release exists rather than assuming the deadline was met.
- **The published rows' own observed-date span, sampled**: `properties->>'observedAt'` (which
  carries `data_available_at`, never ignition date — see §4) ranges **2020-11-24 → 2024-08-22**
  across the current 541 rows (`conductor/RUNBOOK.md:888`) — the five release dates for the five
  ingested fire-year cohorts.

## 4. Grain

One row = **one MTBS-mapped burned-area boundary polygon for one fire, in one ignition-year
cohort**, keyed by MTBS's native `Fire_ID` (`mtbs.py:349-351`, `build_burn_severity_identity`).
It is **not** a raster cell, a severity class, or a time-series observation in the usual sense.

- **Time axis**: the row is dated by `data_available_at` — the **publication date of the MTBS
  release that completed its fire-year cohort** — and explicitly never by `Ig_Date` (ignition
  date), which would leak the ~18-month mapping lag as false lookahead
  (`build_mtbs_identity`, `mtbs.py:1002-1018`; `run_mtbs_ingestion_job` docstring,
  `mtbs.py:1142-1157`). `observed_from`/`observed_to` (the ignition window — when the fires in the
  cohort actually happened, computed by `release_observation_window`, `mtbs.py:840-845`) are
  carried separately and "stay strictly separate from `data_available_at`, *when we could have
  known*" (`ingest/AGENTS.md:313`).
- **Verified current fields** (today's Postgres/MVT shape, cited for what a lane grain needs to
  preserve — not a Parquet schema prescription): `id`, `fire_id`, `fire_name`, `fire_year`,
  `ignition_date`, `fire_type`, `assessment_type`, `acres`, `severity_class`, `observed_at`
  (`drizzle/0012_burn_severity_tiles.sql`, cited at `conductor/RUNBOOK.md:4856`). The underlying
  normalised record additionally carries `mapping_revision` (the Type-2 change-detection key,
  composed from MTBS's own `map_id`/`asmnt_type`/`pre_id`/`post_id`/`perim_id`, since the service
  exposes no per-fire version field — `mtbs.py:466-469`, `ingest/AGENTS.md:323`) and
  `severity_thresholds` (per-fire dNBR calibration values — see §5).
- **Units**: `acres` is the one quantitative outcome measure this layer actually has (float,
  nullable). Geometry is a polygon (or multipolygon) in the fire's mapped burn extent.

## 5. Known gaps and traps

- **Row count is a false cost signal here — geometry size is what matters.** Measured
  2026-08-21: **541 rows for the whole layer**, yet **37.5 MB of geometry**, driving a **cold
  TOAST read of 28.4 s vs 0.42 s warm (68×)** against a 256 MB `shared_buffers`
  (`conductor/RUNBOOK.md:2896-2909`, §0.21.3). There is no SQL fix for this in the current
  Postgres path — only a client cache mitigates it. Whatever replaces this serving path must size
  by bytes, not rows: a 541-row layer that behaves like a multi-megabyte layer is normal for MTBS,
  because each polygon is a full-resolution mapped burn perimeter (a single 50-row upstream page
  is already ~10.7 MB, and the host 500s above ~100 rows — `mtbs.py:180-187`).
- **`severity_class` is null on all 541 published rows, and this is by design, not a bug.**
  100%-NULL confirmed by a full-layer scan (`conductor/RUNBOOK.md:905`). MTBS does not publish a
  polygon-level severity classification at all — it distributes severity as a separate thematic
  raster product outside this feed (`mtbs.py:286-289`, `resolve_burn_severity_class`,
  `mtbs.py:422-443`). The retired TypeScript service (`mtbs.ts`) used to default an unrecognized
  severity code to `"unburned"`, silently mislabeling every feature since MTBS's endpoint never
  actually served a `Severity` attribute; the current Python ingest raises
  (`MtbsUnknownSeverityCodeError`) rather than guess, and treats an absent code as "the source
  publishes none," never as `"unburned"` (`ingest/AGENTS.md:321`).
- **The colour ramp is genuinely keyed on `acres`, not a severity class — verified — but this is
  a deliberate, documented choice, already worked around at the UI layer, not a live defect
  waiting to be fixed.** `src/lib/map/layer-legends.ts:441-459` legends the layer with caption
  "Burned area" against `BURN_SEVERITY_ACRES_STOPS`, with the comment: *"Burned AREA, not a
  severity class: MTBS publishes no polygon-level class, so the fill keys to acres"*
  (`src/lib/map/layer-legends.ts:446-447`). The renderer itself carries the fuller reasoning at
  `src/lib/map/layers.ts:280-295` — same conclusion, keyed off the same null column, plus why the
  per-fire dNBR thresholds can't stand in for it either (see below). The user-facing toggle label
  was **deliberately renamed away from "Burn Severity"** to `"Burn History (MTBS)"` for exactly
  this reason — `src/lib/map/layer-registry.ts:444-447`: *"'Burn History', not 'Burn Severity':
  the published rows carry burned area and no severity class, and the fill says so."* So: nothing currently
  presented to a user claims this layer shows severity. **The trap for wave 2 is the slug, not
  the rendering**: `geo.layers.name` and the lane slug are both still literally `burn-severity`
  (inherited name, `drizzle/0011_burn_severity_layer.sql:17`:
  `('burn-severity', 'vector', 'MTBS burned-area boundaries by published release', true)`), so an
  agent building `warehouse/schemas/burn-severity.py` or `planes/burn-severity.py` from the slug
  alone could reasonably assume a severity classification exists to carry through. It does not.
  The per-fire dNBR thresholds the layer does carry (`severity_thresholds` /
  `MtbsSeverityThresholds`: `dnbr_offset`, `dnbr_standard_deviation`, `nodata_threshold`,
  `greenness_threshold`, `low_threshold`, `moderate_threshold`, `high_threshold`) are mapping
  **calibration inputs**, not an outcome measure, and are explicitly documented as unsafe to
  stand in for severity (`mtbs.py:286-288`; `drizzle/0012_burn_severity_tiles.sql` comment
  block). Carry `severity_class` through as a column anyway (nullable) — it costs nothing and
  means a future MTBS release that does classify polygons lights the map up with a paint change
  alone, per the same migration's own reasoning.
- **The staleness/validation gate structurally cannot catch any of the above lag.**
  `burn-severity` is declared `kind="reference"` with **no `publication_cadence_days`**
  (`services/agri-data-service/src/agri_data_service/ingest/validation/models.py:145`), so
  `validate-streams` applies **zero** staleness check to this layer — a documented, still-open
  gap (`conductor/RUNBOOK.md` item 4c, line 2482). Do not assume an absence of alarms means the
  data is current.
- **The published-rows creation-date pattern looks stalled but has an ordinary explanation.**
  `created_at` (ingestion date, distinct from `observed_at`/`data_available_at`) shows rows only
  on two days, 2026-08-05 (478 rows: the 2018/2020/2021/2022 cohorts) and 2026-08-10 (63 rows: the
  2019 cohort added the same day its release date was confirmed) —
  `conductor/RUNBOOK.md:870`, corroborated by `ingest/AGENTS.md:315` ("63 in-bbox fires landed on
  the first run"). 478 + 63 = 541 reconciles exactly. This is expected for a reference-cadence
  layer with a governance-gated ingest set, not evidence of an outage.

## 6. Validation approach

The lane contract requires `pipeline/validation/burn-severity.py` to reconcile what was written
against **the source system**, never against local state (`layer-lanes.md` §4). MTBS's own
completeness machinery already exists in `mtbs.py` and should carry forward rather than be
re-derived:

- **Per-cohort authoritative count.** `fetch_release_count` issues `returnCountOnly=true` for a
  given `(ignition_year, bounding_box)` before paging (`mtbs.py:674-696`); the capture path
  compares this against the paged feature count and raises `MtbsTruncatedCaptureError` on any
  mismatch (`mtbs.py:816-820`). Re-running this count against what a Parquet partition holds for
  the same cohort is the natural per-release reconciliation check — and worth re-running
  periodically even for a cohort already captured, since MTBS does occasionally **reissue**
  released data (2026-07-01, "data-migration fix, not new fires" —
  `docs/reports/evidence/mtbs-release-announcements-2026-08-10.md` line 42) without that showing
  up as a new fire year.
- **Cross-page uniqueness.** Every page is sorted `orderByFields=fire_id`, and every `fire_id` is
  asserted unique across pages; a repeat proves the paging order was unstable and raises
  `MtbsDuplicateFeatureError` (`mtbs.py:797-805`, `fetch_release_features`). This guards against
  the exact bug the retired TypeScript had: `resultRecordCount: "500"` with no `resultOffset`
  silently discarded roughly 87% of the Pacific Northwest's 3,824 all-time MTBS perimeters
  (`ingest/AGENTS.md:319`).
- **Release-window tripwires**, already enforced at capture time and worth re-asserting in
  validation: `data_available_at` must follow the cohort's last ignition by at least 180 days
  (`MIN_RELEASE_LEAD`) and must not land within 60 seconds of "now" (`MIN_PUBLICATION_AGE`) —
  either failing means the date is ignition-shaped or clock-shaped, not a real release date
  (`validate_release_window`, `mtbs.py:374-399`).
- **Absences are governed, not gaps to fill.** Fire years outside `MTBS_ANNUAL_RELEASE_DATES`
  (currently everything except 2018–2022 — see §3) are a **governance-gated absence**: they must
  be recorded as such and never interpolated or silently retried on a nightly gap-detection pass.
  The absence only changes when a human does the announcement-chronology verification described
  in §3 and adds a dated entry — the same discipline
  `docs/reports/evidence/mtbs-release-announcements-2026-08-10.md` already models for 2019, 2023
  and 2024.
- **Confirm before relying on it, UNVERIFIED here**: whether the USDA FS EDW ArcGIS Server
  exposes a `lastEditDate`/layer-metadata endpoint that would let validation detect a reissue
  without re-fetching every cohort. Not established in this repo; would need a live probe of the
  service (`mtbs.py:68`) to confirm.

## 7. Forecast recommendation

**`horizon: none`. Ship no `method/monte_carlo/burn-severity.py`.**

Reasoning, worked through against the lane contract's own test (`layer-lanes.md` §2: *"A lane
that genuinely cannot forecast declares `horizon: none` and ships no
`method/monte_carlo/<slug>.py`. An empty forecast module is worse than an absent one."*):

1. **The time axis is not a daily series to extrapolate — it is five discrete instants across
   the entire history.** `observed_at`/`data_available_at` only advances when MTBS closes a
   fire-year cohort (§4), and today that has happened exactly five times, 2020-11-24 through
   2024-08-22 (§3). A 30-day Monte Carlo needs a process with expected near-term movement; this
   layer has no such process. There is no meaningful sense in which "burn-severity 30 days from
   now" differs from "burn-severity today" as a statistical projection — the only thing that
   could change it is a new MTBS release landing, and that is a governance-and-publication event
   (§2), not a sampleable random process this repo's own history can project forward.
2. **Even if a release date could be forecast, its content could not be.** The forecast target
   would have to be the shape and location of not-yet-existing burned-area polygons for
   not-yet-mapped fires — an outcome, not a continuation of a trend. That is exactly what the
   `fire-detections` and `fire-perimeters` lanes already exist to project at genuinely daily
   grain (near-real-time detections and active incident boundaries); MTBS is retrospective by
   design; it exists to score already-completed fire seasons once mapping is finalized, not to
   monitor anything live.
3. **The layer already carries the same refusal one level down.** `HistoryCapability(supported
   =False, ...)` is already declared for the backward direction, with the reasoning that the
   ungoverned years are "a governance gap awaiting a dated release announcement, not a past
   window a backfill could walk" (§3, `mtbs.py:973-978`, `:1107`). The same structural fact —
   this layer moves by governance-gated administrative decision, not by elapsed time — applies
   symmetrically forward. A lane that correctly refuses to walk its own past should not pretend
   it can walk its own future.
4. **Concretely, for the serving layer**: no `kind=forecast` object stream exists for this lane
   at all — not an empty one. A future date on the time slider for `burn-severity` should read
   the same way it already does for the other `kind="reference"` layers (`soil-survey`,
   `watersheds`, `evacuation-zones` share this declaration —
   `validation/models.py:143-146`): as an honest snapshot/reference layer with no time-varying
   claim, never as a projection that could be mistaken for a measurement.
