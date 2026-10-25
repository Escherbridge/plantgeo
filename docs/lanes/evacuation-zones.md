---
type: lane-contract
---

# `evacuation-zones` lane

Written for the agent that will build `warehouse/schemas/evacuation-zones.py`,
`pipeline/lanes/evacuation-zones.py`, `pipeline/validation/evacuation-zones.py`,
`planes/evacuation-zones.py` and, per §7 below, **no**
`method/monte_carlo/evacuation-zones.py`, per
[`conductor/code_styleguides/layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md)
§1. No Parquet path, filename or column list is asserted here — that contract is
being written concurrently by another agent this session (S0). All facts below
are cited to the file/line that establishes them; anything the repo does not
establish is marked **UNVERIFIED** with what would confirm it.

**Headline finding, because it is the one this lane most needed answered:** this
is a **real, working, single-jurisdiction ingest lane**, not a seeded or manual
import. The executor's hourly `postgres-evacuation-zones` lane polls Oregon's own
emergency-management ArcGIS feed independently (§1–§2). It is **not** an aggregation across "many separate
jurisdictions" — it is one state agency's feed, and the code says outright that
no equivalent exists for the rest of this project's coverage area (§5).

## 1. Source system

- **Publisher**: the Oregon Department of Emergency Management (Oregon OEM).
  The stored property value is literally `"Oregon OEM Fire Evacuation Areas"`
  (`EVACUATION_ZONES_PROPERTY_SOURCE`,
  `services/agri-data-service/src/agri_data_service/ingest/evacuation_zones.py:52`),
  and the producer token written into every row's identity is
  `"or-oem-evacuation-areas"` (`EVACUATION_ZONES_PRODUCER`, `:65`).
- **Endpoint**: Oregon's `Fire_Evacuation_Areas_Public` hosted view on an Esri
  ArcGIS Online organization —
  `https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services/Fire_Evacuation_Areas_Public/FeatureServer/0/query`
  (`EVACUATION_ZONES_QUERY_URL`, `evacuation_zones.py:72-75`). The requested
  field list (`EVACUATION_ZONES_OUT_FIELDS`, `:76-91`) is
  `GlobalID, Fire_Name, Fire_Evacuation_Level, created_date, last_edited_date,
  County, Evac_Area_Name, StructuresWithin, AddressesWithin, PopulationWithin,
  HazardType, Editor_Name`.
- **Auth: none apparent in code.** No API key, token, or `Authorization` header
  appears anywhere in `evacuation_zones.py`; the query carries only the shared
  `upstream_client`/`fetch_bounded_json` machinery also used by the other
  unauthenticated ArcGIS sources (`:22-27`). The endpoint's own name —
  `..._Public` — is consistent with this being an intentionally open view, not
  a credentialed one.
- **Licensing: UNVERIFIED.** A search of
  `services/agri-data-service/src/agri_data_service/ingest/validation/source_manifests.py`
  and `evacuation_zones.py` for any license text, DOI, or terms-of-service
  reference returns **zero hits** — this source has no license snapshot, no
  `agri.source_release` write, and no manifest row at all (contrast with the
  `burn-severity` lane, which snapshots an explicit federal-public-domain
  license string and DOI for MTBS —
  [`docs/lanes/burn-severity.md`](burn-severity.md) §1). This lane does not
  write through `agri.source_release`/`agri.artifact` at all; it writes
  directly through `geo.features`/`geo.geometry` via `FeatureWrite`
  (`evacuation_zones.py:308-351`). **To confirm**: check Oregon OEM's ArcGIS
  Online organization terms of use directly, and whether
  `allowed_client_exposure` has ever been set for the `or-oem-evacuation-areas`
  producer — nothing in this repo currently governs that question for this
  source.

## 2. Cadence

- **The real mechanism, confirmed live in the tree**: executor lane
  `postgres-evacuation-zones` invokes `agri-service data ingest-evacuation-zones` hourly. The
  continuous `plantgeo-job-executor` owns its schedule, lease, retry and dead-letter state; there
  is no Railway `cronSchedule`. The old composite `ingest-all` path remains useful command
  provenance but is not the production scheduler.
- **A stale citation exists in the validation catalogue and should not be
  trusted.** `ingest/validation/models.py:135-141` declares
  `StreamDefinition(stream="evacuation-zones", kind="time_series",
  publication_cadence_days=1, cadence_basis="infra/cron-evacuation-zones/
  railway.json runs \`*/15 * * * *\`")`. **No `infra/cron-evacuation-zones/`
  directory exists in this repo**. The same file makes the identical claim
  for `fire-detections`, `water-gauges`, `weather-observations`, `vegetation`
  and `fire-perimeters`, citing `infra/cron-firms`, `infra/cron-streamflow`,
  `infra/cron-weather`, `infra/cron-ndvi` and `infra/cron-fire-perimeters`
  respectively — **none of those directories exist either**. This is a
  systemic leftover from superseded scheduler topologies; tracked cron configs have now been
  retired without anyone updating `validation/models.py`'s `cadence_basis` strings.
  **The real cadence is hourly, not every 15 minutes.** Whoever writes the
  Parquet lane's cadence documentation should not copy the `*/15` figure
  forward.
- **Evidence the hourly job is actually landing rows**: a same-session live
  census recorded evacuation-zones growing from 648 to 651 published rows
  within hours (`conductor/RUNBOOK.md:745`), and a 90-day `created_at`
  histogram calls the layer "**healthy** — thin but continuous, 7-381/day"
  (`conductor/RUNBOOK.md:869`) — a wide daily range consistent with a feed
  that is quiet outside active fire incidents and bursts during one.

## 3. Historical horizon

- **`HistoryCapability(supported=False)`, and it is an honest refusal, not a
  gap.** `EVACUATION_ZONES_HISTORY_CAPABILITY`
  (`evacuation_zones.py:120-123`) carries
  `EVACUATION_ZONES_NO_HISTORY_REASON` (`:111-119`): *"Oregon OEM publishes
  Fire_Evacuation_Areas_Public as a current-state hosted view: its
  definitionQuery drops an area once the upstream integration stops
  re-confirming it, and no attribute records when an area's level was raised,
  lowered or retired, so a past evacuation level cannot be reconstructed from
  it. No archive service of historical Oregon evacuation levels is published
  (checked 2026-08-10), and no equivalent government-run aggregator exists for
  Washington, Idaho or western Montana. The only history this layer has is
  what `geo.geometry` has accumulated since ingestion began."* This is pinned
  by a test that asserts calling `.require(...)` on any window raises
  `HistoryUnavailableError`
  (`tests/test_ingest_evacuation_zones.py:229-239`).
- **What "history" means for this layer is therefore not "how has a
  jurisdiction's boundary shifted"** — it means "what evacuation advisories
  were in force in the past," and Oregon simply does not publish that record.
  Whatever accumulated version history this layer has is entirely a side
  effect of PlantGeo's own polling since ingestion began, never a fact
  recoverable from the upstream.
- **No backfill path exists or is planned.** `_build_backfillable_sources()`
  (`ingest/commands.py:321-344`) does not include evacuation-zones, consistent
  with the refusal above. `conductor/RUNBOOK.md:1017-1025` cites this layer
  specifically as the *correct* contrast case: `weather-observations` and
  `fire-perimeters` declare `supported=True` with no fetcher actually wired
  behind them (a misleading claim), while "`evacuation-zones` by contrast
  declares `supported=False` — an honest refusal, since Oregon's feed is
  current-state-only."
- **The sampled `observedAt` span of the 651 currently-published rows is
  2025-04-14 → 2026-08-20** (`conductor/RUNBOOK.md:888`). Read this
  carefully: it is the range of *creation dates Oregon still reports for
  areas it currently lists*, not evidence that PlantGeo has been polling
  continuously since April 2025 — the community-engagement track already
  found 381 published rows on 2026-08-05
  (`conductor/tracks/community_engagement_completion_20260805/spec.md:34`),
  well before that span's start would suggest.

## 4. Grain

One row = **one Oregon OEM evacuation-area record from the current-state
`Fire_Evacuation_Areas_Public` view**, keyed by the upstream's own `GlobalID`
(`build_evacuation_zone_identity`, `evacuation_zones.py:283-305`). It is a
**live fire-response advisory area, refreshed in place**, not a permanent
municipal or county evacuation-zone boundary catalogue — an area disappears
from the upstream feed (and stops being refreshed here) once Oregon's own
sync stops re-confirming it.

- **Identity and dating**: the natural key is `f"{EVACUATION_ZONES_PRODUCER}:{GlobalID}"`.
  `observed_at` is dated from the upstream's `created_date` **only** —
  deliberately never from `last_edited_date`, because Oregon's sync
  re-stamps an unchanged area's edit clock every few minutes, which would
  make almost the whole layer look freshly edited on every poll
  (`evacuation_zones.py:284-294,341-347`; pinned by
  `tests/test_ingest_evacuation_zones.py:135-143`). An area with no creation
  stamp is dated `None` rather than guessed as "now"
  (`test_ingest_evacuation_zones.py:146-148`).
- **Stored properties** (current Postgres/MVT shape — cited to show what a
  lane grain needs to preserve, not as a Parquet schema prescription):
  `globalId`, `evacuationAreaName`, `fireName`, `county`, `hazardType`,
  `evacuationLevel` (integer 1-3), `evacuationLevelLabel` (`"Be Ready"` /
  `"Be Set"` / `"Go Now"`), `severity` (`moderate`/`high`/`critical`),
  `structuresWithin`, `addressesWithin`, `populationWithin`, `editorName`,
  `createdDate`, `source` (`"Oregon OEM Fire Evacuation Areas"`), `geometry`
  (`evacuation_zones.py:308-351`). Oregon's published scale
  (`EVACUATION_LEVEL_LABELS`/`EVACUATION_LEVEL_SEVERITIES`,
  `:129-134`) is a fixed public convention, not one this repo invented.
  **`lastEditedDate` is parsed but deliberately never stored** (`:341-347`) —
  see §5.
- **Geometry**: a polygon per area (`require_polygon_geometry`, `:20,238-240`).
- **Coverage is bounded twice**: once by Oregon's own statewide feed, and
  again by whatever `INGEST_BBOX` the run is configured with
  (`resolve_bounded_bbox`, `evacuation_zones.py:361-363` — an unset bbox
  skips the job entirely rather than failing,
  `tests/test_ingest_evacuation_zones.py:351-355`).

## 5. Known gaps and traps

- **Single-jurisdiction coverage, stated outright in the code, not inferred.**
  *"Coverage is Oregon only: no equivalent government-run aggregator was found
  for Washington, Idaho or western Montana, and the one vendor feed that
  reaches them carries no timestamp at all, so it cannot supply an honest
  `observed_at`."* (`evacuation_zones.py:67-71`). This is the answer to
  whether this layer is a multi-jurisdiction aggregation: **it is not.** It is
  a single state agency's current-state feed, and the rest of this project's
  coverage footprint (Washington, Idaho, western Montana) has no evacuation
  zones represented at all — an absence, silently, rather than a governed
  gap the layer declares anywhere visible to a map user.
- **Nothing ages out a zone Oregon has retired from its own view.**
  `geo.evacuation_zone_tiles(z,x,y)` (`drizzle/0015_tile_observation_day.sql:116-155`,
  filter at `:146-150`; function first created in
  `drizzle/0009_evacuation_zone_tiles.sql`) serves **every** row with
  `status = 'published'` and geometry in the tile bounds — there is no filter
  on `last_confirmed_at` recency. The write path's own comment names
  `geo.geometry.last_confirmed_at` as "the freshness signal a consumer needs
  to age out a vanished area" (`evacuation_zones.py:346`), but no consumer in
  this repo actually reads it that way for this layer. **A zone Oregon has
  quietly dropped from `Fire_Evacuation_Areas_Public` will keep rendering on
  the PlantGeo map indefinitely** unless some other, unfound process
  unpublishes it. Confirming/fixing this is squarely in scope for whatever
  reads `kind=observed` for this lane.
- **`data_available_at` is 100% NULL on all 651 published rows**, confirmed by
  a full-layer scan (`conductor/RUNBOOK.md:905`). That column is the
  migration-0025 ML-leakage boundary; it is simply unpopulated for this
  producer. Low practical risk since this layer feeds no ML target today, but
  a Parquet lane that carries the column forward should not assume it is
  populated.
- **Never censused for polygon byte size.** `conductor/RUNBOOK.md` names
  `fire-perimeters` and `evacuation-zones` as the two layers that have
  "**never been censused**" for real geometry bytes (grep hit near
  `conductor/RUNBOOK.md:3737`). The `burn-severity` lane's own experience is
  the warning here: 541 rows there hid 37.5 MB of geometry
  ([`docs/lanes/burn-severity.md`](burn-severity.md) §5). **651 rows is not
  evidence this layer is small** — measure actual bytes before sizing the
  Parquet export.
- **No authoritative live-count check exists for this source today**, unlike
  MTBS's `fetch_release_count` (`ingest/mtbs.py:674-696`, cited in
  `docs/lanes/burn-severity.md` §6). `evacuation_zones.py` pages until the
  upstream stops signalling `exceededTransferLimit`
  (`fetch_evacuation_zones`, `:268-280`) but never issues a
  `returnCountOnly=true` request to cross-check the paged total against an
  authoritative count.
- **Truncation policy exists but is unlikely to bind at today's volume.** A
  hit `resolve_max_source_records()` ceiling keeps the newest areas by
  `createdAt` and drops the rest, tie-broken by arrival order — undated areas
  sort last (`run_evacuation_zones_ingestion_job`, `:371-393`). At 651
  statewide rows this has real headroom before it matters, but a large,
  fast-moving multi-fire event could exercise it.
- **Cross-reference correction for wave 2**: `docs/lanes/burn-severity.md` §7
  groups `evacuation-zones` among the `kind="reference"` layers (alongside
  `soil-survey`, `watersheds`, `burn-severity`) that get **zero** staleness
  check from `validate-streams`. **That is incorrect for this layer.**
  `ingest/validation/models.py:135-141` declares evacuation-zones
  `kind="time_series"` with `publication_cadence_days=1`, distinct from the
  four true `kind="reference"` streams at `:142-146`. Evacuation-zones
  **does** receive an active daily staleness check today — a day with zero
  new rows trips `validate-streams`, even though the `cadence_basis` string
  explaining why is itself stale (§2).

## 6. Validation approach

`evacuation-zones` **does have a real upstream to reconcile against** —
Oregon's live ArcGIS feed — so, unlike a seeded/manual layer, this section
does not have to fall back to internal-consistency-only checks. But the
reconciliation shape is different from every other lane in this repo, because
the source has **no release or cohort structure to check against — only "what
does the upstream currently say."**

- **What is honestly checkable today, without inventing anything:**
  - **Identity integrity.** A feature with a blank or missing `GlobalID` is
    refused at parse time, never synthesized
    (`parse_evacuation_zone_collection`, `evacuation_zones.py:212-214`;
    `build_evacuation_zone_identity`, `:296-297`; pinned by
    `tests/test_ingest_evacuation_zones.py:151-155`).
  - **Dating integrity.** `observed_at` never falls back to "now" for an
    undated area (`test_ingest_evacuation_zones.py:146-148`), and is never
    derived from the constantly-re-stamped `last_edited_date`
    (`:135-143`).
  - **Seen-vs-written accounting.** `IngestionJobResult` already reports
    `records_seen`, `records_written`, `truncated` and a `rejected` count per
    run (`evacuation_zones.py:388-395`) — a validation pass can reconcile
    these against what actually landed in the store without adding new
    instrumentation.
- **What a real `pipeline/validation/evacuation-zones.py` would need to add**,
  because it does not exist yet:
  - **A live re-query and diff**, not a historical reconciliation: fetch the
    current `Fire_Evacuation_Areas_Public` state again and compare its set of
    `GlobalID`s and `evacuationLevel`/geometry values against what was last
    written. This is the closest analogue to MTBS's per-cohort count check,
    but there is no cohort — only "current state now" versus "current state
    at last write," which can legitimately differ between two polls minutes
    apart during an active incident.
  - **An honest read on zero-row days.** A day with no new/changed rows is
    either (a) genuinely no active evacuations statewide — plausible outside
    fire season — or (b) an upstream or ingest failure. Nothing in this repo
    can currently distinguish those two from stored state alone; doing so
    needs a live call to Oregon's feed at validation time, not a replay of
    what was written, and per the lane contract (`layer-lanes.md` §4) the
    honest answer on a day the source genuinely serves nothing is a governed
    absence, never an interpolated row.
  - **Retirement tracking**, tied to the §5 gap above: if the validation pass
    is the place that finally acts on `last_confirmed_at`, it should record
    (not silently drop) a zone the live feed no longer lists, so a "zone
    vanished" event is visible rather than the area just quietly stopping
    updates while still rendering.

## 7. Forecast recommendation

**`horizon: none`. Ship no `method/monte_carlo/evacuation-zones.py`.**

This is not a novel conclusion — `conductor/RUNBOOK.md:3348` already
classifies `evacuation-zones` as `"no — static; horizon: none"` in the
layer-lanes stream table, and `ingest/validation/models.py:135-141`'s framing
of it as a low-cadence `time_series` (not a governed reference constant like
`soil-survey`/`watersheds`) does not change that. Per the lane standard's own
test (`layer-lanes.md` §2: *"A lane that genuinely cannot forecast declares
`horizon: none` and ships no `method/monte_carlo/<slug>.py`. An empty
forecast module is worse than an absent one."*), the reasoning worked through
against this layer's specific facts:

1. **There is no walkable past to fit a model to, and the code already says
   so at full strength.** `HistoryCapability(supported=False)` (§3) is not a
   coverage gap the way MTBS's ungoverned fire years are — it is a structural
   fact that Oregon's feed carries no past state at all, only a current
   snapshot. A 30-day Monte Carlo needs a sampleable process with observed
   history to project forward; this layer's only "history" is whatever
   PlantGeo has itself accumulated by polling, which is an artifact of when
   ingestion started, not a signal.
2. **What would need forecasting is a government administrative decision in
   response to an active hazard, not a measurable physical quantity.** An
   evacuation level is issued or lifted by Oregon OEM in response to fire
   behavior that `fire-detections` and `fire-perimeters` already exist to
   project forward at genuine daily grain (`conductor/RUNBOOK.md:3336-3349`
   marks both `yes` for a 30-day Monte Carlo). Forecasting "will this area be
   under a Go Now order in 12 days" is a policy-response projection layered
   on top of a hazard projection, categorically different from projecting the
   hazard itself.
3. **Single-jurisdiction coverage makes any statewide-and-beyond forecast
   claim actively misleading.** Even setting aside §1 and §2, a forecast
   module for `evacuation-zones` could only ever be trained on Oregon's
   current-state feed (§5) — it would have no data at all for Washington,
   Idaho or western Montana, so a `kind=forecast` partition for this lane
   would silently forecast "zero evacuations" everywhere outside Oregon,
   which is an absence of data being read as a projection of safety.
4. **Concretely, for the serving layer**: no `kind=forecast` object stream
   should exist for this lane at all — not an empty one. A future date on the
   time slider for `evacuation-zones` should read as an honest snapshot with
   no time-varying claim, the same posture `docs/lanes/burn-severity.md` §7
   recommends for the layers that are genuinely `kind="reference"` — even
   though, per §5's correction above, `evacuation-zones` is technically
   `kind="time_series"` in the validation catalogue and does get an active
   staleness check that those layers do not.

## 8. `evacuation-zones-direct-forward`: the config-lane strategy (config-driven ingestion, spec §7)

Track `config_driven_ingestion_20260926`, Phase 4 (slice `s-evacuation-zones`). This section covers
the swarm's dark TOML and strategy only; sections 1–7 above (and the direct writer they describe,
`pipeline/direct/evacuation_zones/forward.py`) are unchanged and still serve `evacuation-zones` in
production. **DARK**: `executor = "legacy"` in the shipped TOML, so nothing in this section
dispatches before the owner's p5-cutover go (plan Phase 5).

| piece | where |
|---|---|
| lane TOML | `services/agri-data-service/lanes/evacuation-zones-direct-forward.toml` |
| provider TOML | `services/agri-data-service/lanes/_providers/oregon-oem.toml` (shipped inert by `p4-contract-freeze`, 2026-10-25) |
| strategy (S14) | `pipeline/lanes/evacuation_zones/oregon_oem.py::STRATEGY` |
| landed leaves (p4-extract) | `pipeline/lanes/evacuation_zones/{watermark,products,rows,source,support}.py` |
| schema | `warehouse/schemas/evacuation_zones.py::EVACUATION_ZONES_SCHEMA` (unchanged; the config lane writes the same stream) |
| registration (S18) | none added: §8.2 explains why a `static_lookup` stream stays on its EXISTING literal registration (`pipeline/parquet/lane_registry.py`) rather than gaining a `config_stream_registrations.py` mirror row |
| tests | `services/agri-data-service/tests/lanes/evacuation_zones/test_oregon_oem.py` |

### 8.1 Why this lane needed its own kind of `static_lookup` strategy

Every other `static_lookup` this track ships reads one cheap upstream metadata field as its
watermark (watersheds' `max(loaddate)`, fire-perimeters' `editingInfo.lastEditDate`), so
`probe_edge` is a tiny read and the generic `pipeline/runner/contract.py::EdgeProbingStrategy`
Protocol fits it cleanly: probe cheaply, fetch the full population only once a new version is
owed. Oregon OEM publishes no such field at all — §1–§5 above already established this, and
`pipeline/lanes/evacuation_zones/watermark.py`'s module docstring works through it in full —
`created_date` never moves when a level is raised, and `last_edited_date` is re-stamped on
unchanged areas every few minutes. The only honest change test is a CONTENT DIGEST of a full
statewide capture, and deciding that watermark structurally requires the same full capture
`fetch` would otherwise make. `oregon_oem.py::probe_edge` therefore does that capture itself and
caches it (`self._captured`) so the turn's later `fetch` call, when one happens, never re-asks
Oregon a second time for the same state a few seconds apart — see the module's own docstring,
"Why `probe_edge` does the whole statewide capture", for the full reasoning and its citation of
`forward.py`'s "one capture per turn" invariant.

### 8.2 Why no S18 mirror row

`pipeline/parquet/config_stream_registrations.py::registration_from_config_stream_row` refuses a
mirror row whose `nature` is `static_lookup` outright ("a version-stamped stream keys to a source
watermark, which a data row cannot carry, so it is registered by hand" —
`tests/parquet/test_config_stream_registrations.py::test_the_checker_names_each_unmirrored...
[id=static-lookup-row]` pins this). `evacuation-zones` has carried a literal
`LaneRegistration` since 2026-09-06 (`pipeline/parquet/lane_registry.py`, `history_floor
2025-04-14`, `publication_lag_days 0`), and `_literal_registration_disagreement` only compares a
`[[streams]]` entry's `history_floor`/`complete_history_floor` when the TOML actually declares
them. The shipped TOML's `[[streams]]` entry therefore declares neither, repeating the literal
registration with zero possible disagreement rather than adding a second registration this stream
structurally cannot have. `publication_lag_days` compares `0 if lane.days is None else
lane.days.publication_lag_days` against the registration's own `0` — equal by construction, since
every `static_lookup` registration is required to declare lag `0`
(`lane_registry.py::LaneRegistration.__post_init__`).

### 8.3 Fetch stays off the generic provider client

`oregon_oem.py::fetch` never calls the `ProviderClient.get(endpoint, parameters)` the
`SourceRequest`/`fetch` Protocol is built around. Oregon OEM's real contract is
`source.py::fetch_evacuation_zones_snapshot`'s adaptive, byte- and page-bounded, paged walk
(`ingest/evacuation_zones.py::fetch_evacuation_zones`, already GL-2 metered through
`ingest/http.py::upstream_client`), which that module's own docstring warns against
re-deriving: "Re-implementing it would fork the 64 MiB per-run transfer budget, the 20-page
ceiling and the `INGEST_MAX_SOURCE_RECORDS` record ceiling." The strategy calls it directly
instead. **Residual, named for the per-lane adversarial review (spec FR-38):** the runner's own
per-turn ledger (`requests`/`weighted_calls`/`fetch_attempts` in the S5 report) never sees these
sends, because they never pass through the `BudgetedClient` wrapping `client`. This is a
documentation/observability gap, not a cost-control one: Oregon OEM is unweighted and keyless
(`lanes/_providers/oregon-oem.toml`), so no dollar budget is at risk, and `source.py`'s own
ceilings are the real cap regardless of what the S5 report shows. `lanes/evacuation-zones-direct
-forward.toml`'s `[budget]` comment states this inline.

### 8.4 The quiet-day and ladder-absence semantics, mapped onto this runner

`oregon_oem.py::settle` returns `Absent(reason=..., proof=...)` whenever a capture holds zero
areas — a real, valued answer, never a failed read (diverging deliberately from
`watermark.py::watermark_for_capture`'s `day=None`/`source_empty` rule, which backs the legacy,
absence-blind `resolve_static_lane` census and predates `Absent` settlements entirely; see the
strategy's own docstring, "Why this diverges from `watermark.py`'s `day=None`"). From there the
runner's OWN generic absence machinery does the rest, and the strategy does nothing else:

- **The ladder write** (every zoom tier, coarse rungs first, base last) is
  `pipeline/runner/writer.py::ObjectStoreLaneWriter.write_absence` →
  `pipeline/parquet/derivation.py::govern_day_absent`. Contrast
  `pipeline/direct/evacuation_zones/adapter.py::_govern_quiet_day`, which has to loop over
  `_ABSENCE_LADDER_TIERS` itself because the legacy driver has no generic absence writer; this
  strategy needs no such loop.
- **The same-day stand-down refusal** — the legacy adapter's own pinned test,
  `tests/direct/test_evacuation_zones_direct_adapter.py::
  test_a_same_day_stand_down_is_refused_rather_than_deleting_a_published_snapshot` — is likewise
  generic here: `pipeline/runner/writer.py::_decide_unverified_rewrite`'s `Absent` branch refuses
  whenever `census.status(stream, day) == "data"`, returning `"data_never_retracted_by_absence"`
  and never writing; `turn.py::_Turn._write_absence` logs
  `plantgeo_lane_turn_absence_over_data_refused` for exactly that case, and the strategy never has
  to name it.
- **A repeated quiet day** reads `"absence_unchanged"` the same way, also for free.

### 8.5 A documented residual: the day-granular "current" check

`turn.py::_Turn._static_forward` fast-paths a `static_lookup` turn by comparing DAYS
(`served >= watermark`, `served` from `LaneReader.newest_data_day`) before ever calling `settle`.
Every other `static_lookup` this track ships can trust that comparison because its watermark is a
real upstream clock. This lane's watermark is a DERIVED day stamp — today's UTC capture date,
whenever the content changes — so two *different* statewide states captured hours apart on the
*same* UTC day both decide `watermark = today`. If an earlier turn that day already wrote zones,
the fast path reads `served == today == watermark` as "current" and never calls `settle`/`rows`
again: a same-day stand-down (a fire contained in the afternoon) is silently not checked until the
UTC day rolls over, at which point the new day's watermark finally differs from `served` and the
quiet state is written. No data is lost and no absence is ever written over live data — the
collision the legacy adapter's refusal test protects against structurally cannot reach
`decide_rewrite` at all here, because the fast path intercepts it first — but a same-day stand-down
is up to one UTC day later to show. `tests/lanes/evacuation_zones/test_oregon_oem.py::
test_a_same_utc_day_stand_down_is_masked_until_the_day_rolls_over` pins this as the lane's current,
reachable behaviour rather than leaving it undiscovered. `ProviderEdge.valued_days` is
`frozenset[date]` with no sub-day resolution, so nothing in `oregon_oem.py` can route around this;
fixing it, if the owner wants to, is a `pipeline/runner/turn.py` change outside this slice's file
boundary, and the same question applies to any other same-day-volatile `static_lookup` a later
slice ships.

### 8.6 Gates (owner)

- **Before any `executor = "config"` cutover**, the owner should see §8.5's residual and decide
  whether a same-day stand-down's up-to-24-hour visibility lag is acceptable for a life-safety
  layer, or whether `turn.py`'s fast path needs a follow-up change first (tracked here, not fixed
  by this slice: out of `s-evacuation-zones`'s file boundary).
- **p5-cutover** (plan Phase 5) flips `executor = "config"` on this lane alongside burn-severity,
  drought, fire-detections, fire-perimeters, sensors, soil, vegetation, watersheds and
  weather-observations, in one commit, after a `--compare` run shows no unexplained delta against
  the legacy writer (G6).
