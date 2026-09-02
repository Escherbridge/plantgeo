---
type: lane-contract
---

# `fire-perimeters` lane

Written for the agent that will build `warehouse/schemas/fire-perimeters.py`,
`pipeline/lanes/fire-perimeters.py`, `pipeline/validation/fire-perimeters.py`,
`planes/fire-perimeters.py` and, if §7 below is overridden, `method/monte_carlo/fire-perimeters.py`,
per [`conductor/code_styleguides/layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md) §1.
No Parquet path, filename or column list is asserted here — that contract is being written
concurrently by another agent this session. All facts below are cited to the file/line that
establishes them; anything the repo does not establish is marked **UNVERIFIED** with what would
confirm it.

## 1. Source system

- **Publisher**: National Interagency Fire Center (NIFC). The TypeScript fetcher's own docstring
  calls it "the public NIFC ArcGIS FeatureServer"
  (`src/lib/server/services/wfigs-fire-perimeters.ts:181-182`).
- **Feed**: `WFIGS_Interagency_Perimeters_Current` — WFIGS (Wildland Fire Interagency Geospatial
  Services), the **current-incidents-only** service, distinct from any historical archive (see §3).
- **Endpoint**:
  `https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query`
  (`services/agri-data-service/src/agri_data_service/ingest/wfigs.py:62-65`, byte-identical in
  `src/lib/server/services/wfigs-fire-perimeters.ts:28-29`).
- **Auth**: none. Neither implementation sends an API key or Authorization header — the query is
  built from `where`, `outFields`, `geometry`, `geometryType`, `inSR`/`outSR`, `spatialRel`,
  `resultRecordCount`/`resultOffset`, `f=geojson` and nothing else
  (`wfigs.py:173-176`, `wfigs-fire-perimeters.ts:120-134`). The TypeScript docstring states this
  explicitly: "No API key required" (`wfigs-fire-perimeters.ts:183`).
- **Licensing**: **UNVERIFIED** for this exact endpoint. A separate, unrelated catalogue entry —
  `services/agri-data-service/src/agri_data_service/ingest/validation/source_manifests.py:390-402`,
  key `"nifc-fire-perimeters"`, `adapter_status="planned"` — records `licence_identifier="Federal
  public service"` and `access_policy="open"` for NIFC fire-perimeter data generally, but that entry
  is **not wired to the live `wfigs.py` producer** (it belongs to a separate intervention-source
  catalogue; do not conflate the two — see §5). Confirm licensing by reading NIFC's/Esri's terms of
  use for this specific ArcGIS Online organisation (`T4QMspbfLg3qTGWY`) before treating the
  "federal public domain" assumption as settled for redistribution purposes.
- **Fields pulled** (10, both implementations byte-identical):
  `attr_UniqueFireIdentifier`, `attr_IrwinID`, `poly_IncidentName`, `attr_FireDiscoveryDateTime`,
  `poly_GISAcres`, `attr_FireCause`, `poly_PolygonDateTime`, `attr_IncidentTypeCategory`,
  `attr_POOState`, `attr_PercentContained` (`wfigs.py:66-79`).

## 2. Cadence

- **Executor schedule**: hourly at minute 0. Lane `postgres-fire-perimeters` runs
  `agri-service data ingest-fire-perimeters` as an independent command. Railway carries no cron
  schedule for this source.
  **CORRECTED 2026-08-22 at the wave-1 join.** This doc originally cited
  `infra/cron-fire-perimeters/railway.json` at `20 * * * *`, quoting
  `ingest/validation/models.py:133`'s `cadence_basis` string. **That directory does not exist.**
  Only three `infra/cron-*` directories are real — `cron-ingest`, `cron-mtbs`, `cron-soilgrids`.
  `models.py` carries **six** such phantom citations (`cron-firms`, `cron-streamflow`,
  `cron-weather`, `cron-ndvi`, `cron-fire-perimeters`, `cron-evacuation-zones`), all stale since the
  superseded cron topologies. **Never treat a `cadence_basis` string as a file reference.** The
  stream's declared `publication_cadence_days` is `1` (`models.py:132`), i.e. the completeness
  report expects at least one landed observation per calendar day.
- **Observed lag / reliability, both from documented production incidents**:
  - **2026-08-06 →**: an unpaged query over the PNW bbox on an *ordinary* day (114 active perimeters)
    answered 18,091,373 bytes against a 16 MiB response cap, crashing the job every hour until
    pagination (`resultOffset`, page size 100) plus `geometryPrecision=5` landed
    (`services/agri-data-service/src/agri_data_service/ingest/AGENTS.md:273`).
  - **2026-08-10 22:21 UTC →**: ArcGIS began throttling with "Too many requests"; the retry budget
    (2 attempts, ~0.5s/2.5s apart) was too thin to survive it, and because
    `restartPolicyType: NEVER` on the former cron, **that hour's fetch was lost outright, not retried until
    the next scheduled tick** (`ingest/AGENTS.md:269`). Fixed by widening to 6 attempts with a
    doubling 1s/2s/4s/8s/16s backoff under a wall-clock ceiling.
  - **Current production blocker, 2026-09-02:** the first executor turn entered retry backoff with
    `UpstreamPayloadError: upstream response exceeded the byte limit`. The executor preserves the
    failure; fix the WFIGS response bound/paging before forcing a retry.
- The upstream feed itself is described (in the separate, unwired catalogue entry noted in §1) as
  `refresh_cadence="updated daily; historical products downloadable"`
  (`source_manifests.py:400`) — **UNVERIFIED** as authoritative for this exact `_Current` service,
  but if accurate it means our hourly poll is far tighter than the upstream's own update rhythm, so
  most ticks likely re-confirm an unchanged shape rather than find a new one. That is cheap: the
  writer's diff rejects an unchanged payload and the geometry adapter classifies an unchanged shape
  as `confirmed` (`ingest/AGENTS.md:359`, `geometry.py`).

## 3. Historical horizon

- **Declared floor**: 2020-01-01 — `WFIGS_PERIMETER_HISTORY_EARLIEST`, with
  `HistoryCapability(supported=True)` (`wfigs.py:108-112`). The AGENTS.md rationale: "WFIGS
  publishes historical perimeter services distinct from the `_Current` feed this job reads, and the
  IRWIN-integrated interagency data model those services carry begins with the 2020 fire year"
  (`ingest/AGENTS.md:725-727`). **This floor is explicitly documentation-derived and NOT live-probed**
  (`ingest/AGENTS.md:727`, dated 2026-08-10).
- **No fetcher exists for that floor.** `ingest/AGENTS.md:730-732`, verbatim: "A real history source
  exists here and is unimplemented — wiring the archive service is scheduled work, not a closed
  question, and it needs a live probe of the service URL and its field spellings before the floor
  above should be trusted operationally." `fire-perimeters` does not appear in
  `commands._build_backfillable_sources()` — the only two backfillable sources today are
  `sentinel2-ndvi` and `nws-sensors` (`.claude/skills/agri-pipelines/SKILL.md:95`). No
  `ingest-backfill --source` walk has ever run against this lane.
- **A deeper archive exists and is deliberately unusable under the current identity contract.**
  NIFC's separate `InterAgencyFirePerimeterHistory` service reaches back to **1878**, but "publishes
  no `attr_UniqueFireIdentifier`, so ingesting it under this producer would fork the layer's key
  rather than deepen it, and `build_fire_perimeter_identity` would have nothing to key on"
  (`ingest/AGENTS.md:728-730`). Reaching 1878 requires a genuinely new identity/keying design, not a
  bbox+date parameter change to the existing WFIGS producer.
- **What is actually held today is not a backfilled archive — it is the residue of the hourly
  `_Current` poller.** Because `geo.features` refreshes a row in place and nothing deletes a row once
  its incident drops out of the `_Current` feed (§5), the oldest dates present in the warehouse are
  whatever a since-closed incident's *last-observed* WFIGS timestamp happened to be, not evidence of
  a deliberate historical load. `environmental-read-model.ts:2854-2855` measured this directly: "
  fire-perimeters largest gap inside the real record 12 days; the gap back to its own isolated
  2025-07-28 row is 324 days" — one stale row sits 324 days before the nearest cluster of activity.
  The exact start date of that "real," clustered record is **UNVERIFIED** here — confirm by running
  the `observed_days` census (`sql/ingest/observed_days.sql`) scoped to the `fire-perimeters` layer
  and applying the same 21-day clustering `environmental-read-model.ts:2861` already uses.
- **Net**: earliest *obtainable* under the current identity contract is 2020-01-01 (declared, unverified,
  no fetcher wired); earliest *actually held* is a thin, accidental tail of stale current-rows reaching
  back roughly one year at most, not a continuous multi-year record.

## 4. Grain

This is the section that decides whether history exists to backfill, and the answer is **it depends
which table you mean** — `geo.features` and `geo.geometry` disagree.

- **`geo.features`: one row per incident, refreshed in place — NOT (incident, day).** The identity is
  `producer="wfigs"`, `producer_local_id=uniqueFireIdentifier` — the bare fire identifier, with no
  date component (`identity.py:266-272`, `build_fire_perimeter_identity`). Every tick that sees an
  incident in the `_Current` feed **updates that same row** rather than inserting a new one
  (`writer.py`'s `_ingest_resolved_batch`: existing external ids go through `_REFRESH_FEATURES`, not
  `_INSERT_FEATURES`). `run_fire_perimeters_ingestion_job`'s own docstring: "refresh them in place as
  their polygons and containment advance" (`wfigs.py:290`). One `geo.features` row therefore means
  *the incident's current known state*, holding: `uniqueFireIdentifier`, `incidentName`,
  `fireDiscoveryDateTime`, `polygonDateTime` (most recent), `gisAcres` (a scalar, not a series),
  `fireCause`, `incidentTypeCategory`, `pooState`, `percentContained`, a derived `severity` bucket
  (`perimeter_severity()`, `wfigs.py:142-156`: `critical <25`, `high <50`, `moderate <75`, else
  `low`, returning `None`/null rather than a fabricated `"low"` when WFIGS reports no containment —
  `wfigs.py:263`), and the current polygon/multipolygon geometry.
- **`geo.geometry` IS a Type-2 slowly-changing dimension, and it IS keyed the same way for this
  producer.** `geometry_key_for` returns `FeatureIdentity.entity_key`; for `wfigs`,
  `entity_local_id` is `None` so `entity_key` and `natural_key` coincide by construction
  (`ingest/AGENTS.md:375`) — unlike `usgs-nwis`/`open-meteo`, WFIGS was never affected by the
  keying bug that made their dimension fan out uselessly. So growth history *can* accumulate: each
  time the incoming polygon differs from the currently-open version **and** the incoming
  `observed_at` (`coalesce(polygonDateTime, fireDiscoveryDateTime)`) is strictly later than that
  version's `version_valid_from`, the adapter closes the old version and opens a dated successor
  (`geometry.py`, `_plan_versions`; `ingest/AGENTS.md:339-341`).
- **But that version chain is event-dated, not day-partitioned, and it is thin.** A version opens
  only when WFIGS both reports a materially different polygon (`geom = ` exact-equality first,
  `ST_Equals` second — never a tolerance, `ingest/AGENTS.md:331`) and supplies an instant later than
  the open version's own start. Measured directly against production 2026-08-04: of 23,690 features
  joined to their dimension row across every producer, only **6 were WFIGS perimeters that had
  actually drifted to a second version** (`ingest/AGENTS.md:331`) — the overwhelming majority of
  fire-perimeter entities sit at `v1`, meaning no growth trajectory was ever captured for them at
  all, only the shape as of whenever this producer first saw them.
- **A shape that keeps changing can permanently freeze, silently.** When the geometry differs but
  the incoming timestamp is not strictly later than the open version (this includes every `observed_at
  is None`, since undated sorts as `-infinity`), the adapter records the outcome `undatable`, leaves
  the chain untouched, and does **not** update staleness either — "the same divergence is
  re-detected on the next tick, and the next, forever" (`ingest/AGENTS.md:391`). Two production rows
  are in exactly this state: one with a JSON-null `polygonDateTime`, one whose `polygonDateTime`
  equals its `version_valid_from` to the second and fails the strict `>` check
  (`ingest/AGENTS.md:391`).
- **Consequence for wave 2**: history to backfill exists only partially, inside `geo.geometry`, not
  `geo.features` — and even there it is sparse (most incidents never version at all), event-dated
  rather than daily, and has a known silent-freeze failure mode. An exporter that reads only
  `geo.features` per day will produce a Parquet dataset that *looks* like a daily perimeter history
  but is actually one row per currently-tracked incident's last-known state, replayed unchanged
  across every day it happens to run — see the validation-approach gotcha in §5/§6 for the same trap
  as it appears in the existing gap-detection query.

## 5. Known gaps and traps

- **Tiles are cold-TOAST-read bound, and row count is a useless proxy.** The layer's Martin tile
  function is measured **10.9 s cold vs 0.23 s warm against 11.7 MB of geometry, a 40-68×
  swing** (`conductor/RUNBOOK.md` §0.21, "`fire_risk_tiles` / `burn_severity_tiles` are cold TOAST
  reads"). Before the 2026-08-21 composite-split and cache-first fixes, the same function was
  measured returning just 26,765 bytes after **117.2 s** inside the six-layer composite — "the worst
  work-to-output ratio in the system" (RUNBOOK, §9 live measurements). This layer holds **177
  published rows** (measured 2026-08-21), fewer than sibling `burn-severity` (541 rows, 37.5 MB), yet
  each fire-perimeter row averages **130,583 bytes** — the heaviest per-row geometry cost of any
  layer besides `geo.drought_areas` and `burn-severity`
  (`.claude/skills/agri-pipelines/SKILL.md:129-135`). Size the geometry, never the rows, when
  budgeting this lane's Parquet/tile cost.
- **The Martin function name has never matched the layer it serves.** `geo.fire_risk_tiles` (the
  function, the Martin source id, and its emitted MVT layer tag `fire_risk`) serves
  `geo.layers.name='fire-perimeters'` — pure historical accident, no functional impact today because
  every consumer uses the same literal string, but renaming either in isolation would break the layer
  with no compiler or schema error to catch it (RUNBOOK §9 findings table; `infra/martin/martin.yaml:47-49`
  registers it under the `fire_risk_tiles` name). Do not assume the tile/function name tells you the
  layer slug.
- **The `_Current` feed is server-side scoped to active incidents already, so no client-side
  freshness rejection is applied — unlike FIRMS.** "That absence is deliberate, not an omission"
  (`ingest/AGENTS.md:263`). Staleness for a given row is entirely a function of whether WFIGS still
  reports that incident as current.
- **Closed/contained incidents are never deleted from `geo.features`.** No delete, expire, or reap
  logic for this layer was found anywhere in `ingest/wfigs.py`, `ingest/writer.py`, the cron
  container docs, or a repo-wide search for delete/reap/expire patterns — this is an absence-of-code
  finding, not a positively documented behaviour, so treat it as **UNVERIFIED-by-omission**: confirm
  by checking whether a specific known-closed `uniqueFireIdentifier` still holds a row. If true (as
  the evidence suggests), a row's age alone does not tell you whether an incident is still active.
- **`polygonDateTime` is frequently null.** 13 of 112 production perimeters carried a null
  `polygonDateTime` at the 2026-08-10 measurement, and all 13 had a parseable `fireDiscoveryDateTime`
  fallback (`ingest/identity.py`; `ingest/AGENTS.md:395`). `build_fire_perimeter_identity` coalesces
  to discovery time for exactly this reason — a perimeter cannot predate the discovery of its own
  fire.
- **The `observed_days` gap-detection census reads `geo.features`, and for this lane that means it
  reports "last known day per still-tracked incident," not "days this incident was observed."**
  `sql/ingest/observed_days.sql` computes one row per `(layer, day)` from
  `geo.feature_observation_day(features.properties)` over the **current** `geo.features` row per
  incident (`sql/ingest/observed_days.sql:107-112,162-174`). Because this lane's `geo.features` grain
  is current-snapshot-per-incident (§4), the census's "days" are the union of each still-present
  incident's own last-known date — not a genuine daily publication record. This matches the layer's
  own declared temporal shape: `"fire-perimeters": "event"` in
  `src/lib/server/services/environmental-read-model.ts:3000`, explicitly **not** `"daily_series"`,
  with the surrounding comment warning that treating an event-shaped layer as continuous "draws its
  whole record" with no date filter if the classification is ever dropped
  (`environmental-read-model.ts:3001-3009`). A wave-2 exporter that partitions this lane's Parquet by
  day using the same pattern other lanes use will silently manufacture a daily time series that isn't
  one.
- **A separate, unrelated "nifc-fire-perimeters" catalogue entry exists and is not this lane.**
  `ingest/validation/source_manifests.py:390-402` carries an `InterventionSourceManifest` keyed
  `"nifc-fire-perimeters"` with `adapter_status="planned"`. It documents NIFC fire data generally for
  a different (community-interventions) source catalogue and is not wired to `wfigs.py`. Do not
  confuse its `refresh_cadence="updated daily"` claim with a verified property of the live producer.
- **Byte-cap tuning is calibrated to "an ordinary day," not peak fire season.** Page size 100 and
  `geometryPrecision=5` (~1.1 m) were sized against a 114-active-perimeter measurement
  (`ingest/AGENTS.md:273`). The per-page byte cap is an intentional backstop, not raised: "a single
  page that is still too heavy (one pathologically complex perimeter) still fails that page rather
  than being silently permitted through a wider ceiling." A denser fire season is the condition most
  likely to exercise this.

## 6. Validation approach

- **What exists today is the generic completeness report, and — per §5 — it is measuring the wrong
  thing for this lane's actual grain.** `StreamDefinition(stream="fire-perimeters", kind="time_series",
  store="features", publication_cadence_days=1, ...)` (`ingest/validation/models.py:128-134`) feeds
  the same `observed_days.sql` census every stream uses. No fire-perimeters-specific reconciliation
  against the WFIGS source exists, and no backfill walk exists to reconcile against (§3).
- **No pre-count completeness check exists for this host family in `wfigs.py`, though the pattern is
  already established elsewhere in the repo.** `mtbs.py`, which pages a different ArcGIS host,
  validates completeness with an authoritative `returnCountOnly` request issued *before* paging and
  asserts the reassembled feature count matches it (`ingest/AGENTS.md:319`). `wfigs.py` instead
  trusts `exceededTransferLimit` alone as its keep-paging signal (`wfigs.py:216`,
  `parse_perimeter_collection`) with no independent count cross-check. The lane contract's §4
  requirement — "reconciles what the lane *wrote* against what the source system *holds*" — is not
  met by anything in this lane today.
- **"What the source holds" is only meaningful pointwise, because `_Current` is a live mutable
  snapshot, not a versioned release.** Unlike MTBS's quarterly releases or USDM's dated weekly
  publications, WFIGS's `_Current` feed does not retain what it reported yesterday. Validation for
  this lane can only ever answer "did we capture everything active right now," never "did we capture
  everything active on day X in the past" — the latter question has no oracle to check against
  outside the separate, unwired historical archive service (§3).
- **Recommended for `pipeline/validation/fire-perimeters.py`**: (a) add the `returnCountOnly`
  pre-count check `mtbs.py` already established for this same host family, closing the one gap that
  is a straight port rather than new design; (b) preserve `IngestionJobResult.truncated` as a
  governed-absence signal — it already exists (`wfigs.py:312`) but is not currently surfaced past the
  job log; (c) surface the `undatable` geometry-version tally per run as a queryable metric, since
  `ingest/AGENTS.md:391` documents it as the one failure mode that silently and permanently freezes
  an incident's growth history and today it is only a WARNING log line.

## 7. Forecast recommendation

`horizon: none` — **for the polygon geometry**, and this is a deliberate departure from
`conductor/RUNBOOK.md` §0.24.2's starting classification of `fire-perimeters` as a "yes" 30-day
Monte Carlo lane. That table is explicitly a "starting classification," and
`code_styleguides/layer-lanes.md` §2 states plainly that "each lane's `AGENTS.md` declares its own
horizon and that declaration wins" — this document is that declaration for this lane.

Reasoning, laid out because the instruction was to reason about it honestly rather than default to
the table:

1. **The observed grain cannot calibrate a spread model.** A Monte Carlo forecast needs a
   distribution of past outcomes to draw ensembles from. Per §4, the overwhelming majority of
   fire-perimeter incidents in the warehouse sit at a single geometry version — measured
   2026-08-04, only 6 of thousands of dimension entries across every producer had ever reached a
   second WFIGS version. There is close to no per-incident growth trajectory captured to fit
   anything against, dense or otherwise.
2. **A perimeter's growth is a physically different problem than the lanes the "yes" column
   actually suits.** Fire spread is driven by fuel, wind, terrain and suppression effort — none of
   which this lane carries as covariates. The other "yes" lanes (`weather-observations`, `sensors`,
   `water-gauges`, `vegetation`, `fire-detections`) forecast a scalar or point-lattice signal from
   its own historical variance; that same technique applied to a polygon's *shape* would produce
   geometries with no physical grounding in fire behaviour — exactly the "wrong-but-plausible
   output" `layer-lanes.md` §2 warns a drifted lane produces.
3. **Most of today's active perimeters will not exist as an open incident in 30 days.** This layer's
   own declared temporal shape is `"event"`, not `"daily_series"`
   (`environmental-read-model.ts:3000`), and incidents routinely contain and drop out of the
   `_Current` feed within days to a few weeks. Nothing in this lane can say in advance which of
   today's 177 tracked perimeters will still be an open incident to forecast when the horizon
   arrives.
4. **A forecast here could not be honestly validated even if it were produced**, because scoring a
   spread forecast against reality needs the same dense, dated ground truth this lane does not
   capture (§4) — the observed side of the `kind=observed`/`kind=forecast` pair would be too thin to
   ever confirm or refute what the forecast side claimed.

**A narrower, scalar-only forecast (e.g. `gisAcres` or `percentContained` trajectory for currently-open
incidents) was considered and is also not recommended today**, for the same underlying reason: this
lane does not currently capture a dense per-day scalar series per incident to calibrate against — only
a current-snapshot value that is overwritten on refresh (§4). Building that series would itself be a
grain change beyond what §4 finds already decided, and is a candidate for a future revision of this
document if the observed stream is redesigned to capture it deliberately.

Per `layer-lanes.md` §2 ("A lane that genuinely cannot forecast declares `horizon: none` and ships
**no** `method/monte_carlo/<slug>.py`... An empty forecast module is worse than an absent one"): **no
`method/monte_carlo/fire-perimeters.py` should be written.** Projected quantity: zero forecast rows,
by design, not by omission.
