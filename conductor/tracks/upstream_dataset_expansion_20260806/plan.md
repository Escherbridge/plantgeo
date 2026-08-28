---
type: track-plan
track: upstream_dataset_expansion_20260806
status: planned
---

# Plan

See [`./spec.md`](./spec.md) for the measured findings this plan implements against.

Five phases. Phase 0 fixes the existing lane and ships first. Phases 1-4 each add one
dataset and are independently shippable in any order once Phase 0 lands — they touch
disjoint files.

| Phase | Scope | Standard | Status going in | Status as of 2026-08-06 review |
|---|---|---|---|---|
| 0 | Retire the CDS soil lane; close the et0-model trap structurally | — | unblocks trust in the lane | **partially done** — task unregistered, guard test and doc pass still open |
| 1 | `vapour_pressure_deficit_max` | durable | verified (3.32 kPa) — no probe needed | **built, not run** — whitelisted and plan authored, backfill not launched |
| 2 | Open-Meteo Flood API (GloFAS discharge) | cron | needs a live-endpoint probe | **built, blocked on persistence** — lane fetches/caches, warehouse writer missing |
| 3 | Open-Meteo Air Quality API (CAMS) | cron | needs a live-endpoint probe | **built, blocked on persistence** — same blocker as GloFAS |
| 4 | Open-Meteo Ensemble API | cron, schema-gated | needs a probe and an owner decision | **built, schema-gated** — quantile carriage done, receipt persistence blocked on a migration |

## Status as of 2026-08-06 (post-implementation review)

The quality review that closed out slices B-D returned `needs-changes` (1 critical, 3 major, 3
minor findings). Detail folded into
[`docs/unused-upstream-datasets.md`](../../../docs/unused-upstream-datasets.md); this section
records what each phase of *this* plan actually shipped against its own acceptance criteria above.

**Phase 0.** The `PlantGeo-ERA5-PNW-backfill` scheduled task is confirmed unregistered
(`schtasks /query` returns "cannot find the file specified") — that acceptance criterion is met.
The other two are not: no test anywhere in the repository asserts
`et0_fao_evapotranspiration` is absent from `OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` (the
structural guard this phase exists to add), and `docs/rebuilding-the-dataset.md` still does not
name the CDS soil-state retirement — `services/agri-data-service/plans/AGENTS.md` was edited this
session, but for an unrelated lattice-plan-authoring note, not the CDS costing-section pointer this
phase called for.

**Phase 1.** The whitelist entry and its bounds test landed
(`test_vapour_pressure_deficit_is_a_bounded_atmospheric_covariate_not_a_soil_signal`), and
`open-meteo-era5-land-pnw-vpd-20220430-20260430.json` was authored as its own plan/release set,
byte-identical to the soil-temperature lattice plan except for `description`, `parameters` and
`release_set_key`. The backfill run itself has not happened — the plan is unrun.

**Phases 2-3.** Both lanes (`historical_glofas.py`, `historical_cams.py`) validate, fetch, chunk,
checkpoint and cache end to end and are reachable via four new CLI verbs, but neither can write a
warehouse row — see follow-up (a). The two staged `infra/cron-*/railway.json` configs were found
non-functional by the review (see follow-up (e)) and were removed rather than shipped broken.

**Phase 4.** Ingest, reduction, and quantile-carriage validation are complete and independently
verified (a synthetic 21-member payload staged 2 receipts, 24 values each, correct quantile keys).
No schema migration was written, deliberately — see follow-up (b). Ensemble member counts
(`ecmwf_ifs04`=51, `gfs025`=31, `gem_global`=21) are reviewed constants, not yet confirmed against
the live endpoint; a mismatch fails the chunk loudly rather than silently re-scaling.

**Why Phase 0 goes first, concretely, not just as policy.** It edits the same
`OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` block in `historical_open_meteo.py` that
Phase 1 adds `vapour_pressure_deficit_max` to (spec finding 1), so landing the guard
before the new entry avoids the two changes colliding in review. It also removes the
scheduled task and the two CDS soil plan files that a Phase-1-only reading of the repo
would otherwise still show as live and current.

## Effort

This is small, incremental work against a pattern the repo already runs nine times in
production — not a redesign. Call it a guess from structure, the same caveat the
layering track's estimate carries.

Phase 0 is under a day: one whitelist guard test, one doc pass, one operator action
(unregistering the scheduled task) that costs nothing in engineering time but must not be
forgotten. Phase 1 is under a day — the value is already verified; the work is the entry,
the plan file, and one backfill run. Phases 2 and 3 are a day or two each: a live probe,
one new ingest module, one new cron service. Phase 4 is the largest — a day for the probe
and cron scaffold, then an open-ended amount bounded by how the owner answers open
question 1, since a new storage decision is a different size of work than a quantile
reduction into existing columns.

## Phase 0 — retire the CDS soil lane, close the et0 trap

Nothing here adds a dataset. It removes a wrong belief (that the CDS soil lane is still
the right route) and a wrong possibility (that `et0_fao_evapotranspiration` could be
silently whitelisted).

- **Unregister the `PlantGeo-ERA5-PNW-backfill` Windows scheduled task.** This is an
  operator action outside the repository — record it as a required step in this track's
  completion, not an assumption. Confirm removal with `schtasks /query /tn
  PlantGeo-ERA5-PNW-backfill` returning "not found" rather than trusting memory.
- **Stop authoring new plans against, and stop scheduling,
  `era5-land-pnw-soil-20220430-20260430.json` and
  `era5-land-western-na-soil-20220430-20260430.json`.** Leave `historical_era5.py` and the
  CDS client (`_require_cds_credentials`, the licence-refusal handling) in place — CDS
  stays the only route to AgERA5, CEMS fire danger indices, and seasonal forecasts, none of
  which Open-Meteo redistributes (spec, "Retiring the CDS soil lane").
- **Add a structural guard, not a comment.** A test asserting
  `"et0_fao_evapotranspiration" not in OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` for as
  long as `HistoricalOpenMeteoArchivePlan.model` stays `Literal["era5_land"]` — so a future
  addition that reintroduces the trap fails the suite instead of landing nulls with a valid
  checksum. Land it in `tests/test_historical_open_meteo.py`, beside the existing bounds
  tests it is a sibling of.
- **Update the docs that currently describe the CDS soil plans as live or reviewed** —
  `docs/rebuilding-the-dataset.md`'s credential table and `services/agri-data-service/plans/AGENTS.md`'s
  CDS costing section both need a line stating the soil-state retirement and pointing at
  this track, so a future reader does not re-propose re-chunking a lane that is being
  retired rather than tuned.

**Acceptance:** the scheduled task is confirmed absent; no committed doc or script still
names the CDS soil plans as current; the et0 guard test exists and fails if the trap
parameter is added back while the plan schema still hardcodes one model per plan;
`historical_era5.py` and its credential contract are untouched and still pass their
existing tests, because they are staying for AgERA5/CEMS/seasonal.

## Phase 1 — `vapour_pressure_deficit_max`

The fastest phase in the track, because the value is already verified.

- Add one entry to `OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS`, following the existing
  `OpenMeteoArchiveSignal(warehouse_signal_name, original_unit, normalized_unit, minimum,
  maximum)` shape. Open-Meteo documents VPD as non-negative; the reviewed upper bound
  needs its own citation in the entry's comment, the same way the soil-temperature bands
  cite their CDS-level alignment.
- Author a new plan (new `plan_checksum`, new `release_set.logical_key`) the same way
  `author_pnw_soil_moisture_plans.py` authors the soil plans — either extend that generator
  or add a sibling, but do not hand-type a plan JSON, for the same reason the soil plans
  are generated: a wrong `nasa_lattice_plan_checksum` (or here, a wrong cell/parameter set)
  looks valid forever while pointing at nothing.
- Run it through `durable-backfill.sh open-meteo <plan>` against the local warehouse;
  confirm at least one full chunk lands with `is_observed=true`, `quality_flag='accepted'`
  where the provider reported a value, and every value inside the reviewed bounds.
- **Record, do not solve, the ML-invisibility caveat.** `agri.covariate_feature_schema`
  pins its signal list to the immutable `agri_covariates_v1`; a newly ingested signal is
  invisible to training until a new schema version is authored (the same finding recorded
  against the soil-moisture landing in this repo's own history). This phase makes the
  covariate correct and present in the warehouse; it does not, by itself, make any model
  better. Say so in the release notes rather than implying otherwise.

**Acceptance:** a new plan file and release set exist, independent of the soil-moisture and
soil-temperature plans' checksums; a real backfill run lands governed, bounded rows; the
ML-invisibility caveat is recorded, not silently assumed away.

## Phase 2 — Open-Meteo Flood API (GloFAS river discharge)

- **Probe first.** Confirm the live endpoint's field names, units, and — this is the part
  most likely to differ from the ERA5-Land lane — its spatial identity. GloFAS is a
  river-reach product, not a 0.1-degree grid; the probe needs to establish whether it
  addresses points/reaches that fit the existing `AnalysisGridCell` / `agri.spatial_cell`
  lattice contract, or needs its own entity concept the way sensor stations and stream
  gauges already have theirs. Do not assume the ERA5-Land cell contract transfers.
- Register a new `data_source` (DML, no migration, per spec finding 2), a new
  `ingest/flood_discharge.py`, and a new `ingest-flood-discharge` command through
  `register_ingest_commands`.
- Add `infra/cron-flood-discharge/railway.json`, reusing `infra/cron-ingest/Dockerfile`
  (spec finding 4) — no new Dockerfile. Cadence pairs with `ingest-streamflow`'s
  `*/30 * * * *` as a starting point, revisited once real response latency is known.
- Wire the Railway dashboard: Root Directory `/`, config-as-code path pointed at the new
  `railway.json` — the same two-setting pair `plantgeo-ingest-cron` needed, changed
  together.
- Decide `ingest-all` membership per spec open question 4; default to excluded, matching
  `ingest-mtbs`'s precedent, until the source has run cleanly on its own schedule.

**Acceptance:** the probe's findings are recorded before any plan or table shape is
committed to; the cron command runs end to end against the local warehouse and produces
governed rows tied to a real, newly registered `data_source`; the cron image builds with
the new command reachable inside it (`agri-service data ingest-flood-discharge --help`), matching
the manual check every existing cron service already relies on.

## Phase 3 — Open-Meteo Air Quality API (CAMS)

- **Probe first**, same discipline as Phase 2: confirm PM2.5, PM10, and dust field names
  and units against the live endpoint before any whitelist or table shape is authored.
- Register a new `data_source`, `ingest/air_quality.py`, and an `ingest-air-quality`
  command.
- Add `infra/cron-air-quality/railway.json` against the shared image. Cadence does not
  need to match FIRMS's `30 */3 * * *` exactly — smoke and dust concentration move faster
  than a 3-hourly fire-detection sweep needs, so this is worth setting on its own evidence
  rather than copying FIRMS's schedule by default.
- Same Railway dashboard two-setting wiring as Phase 2.
- Same `ingest-all` decision as Phase 2, independently — a source can join while its
  sibling stays excluded.

**Acceptance:** same shape as Phase 2's — probed fields, an end-to-end local run producing
governed rows, and the command reachable inside the built cron image.

## Phase 4 — Open-Meteo Ensemble API (probabilistic forecast members)

The largest phase, because it is schema-gated rather than purely additive.

- **Probe first**, unconditionally — this does not wait on the owner's schema decision.
  Confirm member count, timestep grid, and variables on the live endpoint.
- **The schema question (spec open question 1) gates backfill authoring, not the probe or
  the cron scaffold.** Two paths:
  - *Quantile reduction* — bin the raw ensemble members into a fixed quantile set at
    ingest time and land them in the existing `forecast_receipt.quantile_levels` /
    `forecast_value.quantile_values` (spec finding 6). No DDL. Before treating this as
    fully open-and-shut, confirm during the probe what `forecast_series.input_adapter`
    needs to be — the column is check-constrained to `('signal_observation',
    'forecast_observation')` today, and an upstream ensemble import may or may not fit
    either literal cleanly. If it does not, the fix is a small, reviewed check-constraint
    addition, not a schema redesign — but that is still a migration and should be sized
    honestly rather than folded into the "no DDL" claim.
  - *Raw per-member storage* — accurate, preserves member correlation, needs a new table
    or column and therefore a real migration. Not scoped in this track unless the owner
    chooses it; if chosen, this phase's acceptance criteria change and the phase should be
    re-estimated rather than squeezed into the "small, incremental" effort class the rest
    of this track sits in.
- Register a new `data_source`, `ingest/ensemble_forecast.py`, and an
  `ingest-ensemble-forecast` command, cadenced against the forecast issue schedule the
  live endpoint documents (probe-dependent).
- Add `infra/cron-ensemble-forecast/railway.json` and the matching Railway dashboard
  wiring.

**Acceptance:** the probe's findings are recorded regardless of the schema decision; if the
owner has chosen quantile reduction, at least one `forecast_receipt`/`forecast_value` pair
is populated end to end for one entity/metric window from a real Open-Meteo ensemble
response; if the decision is pending or is raw storage, this phase ships the probe and the
cron scaffold only, and backfill/storage work is explicitly deferred rather than implied
done.

## Verification

One sweep per phase, at the end of that phase — never test → fix → test inside one:
`ruff check`, `mypy` (strict), `pytest`. The floor is whatever the suite reports at the
start of this track; the number must not drop.

This repo has no checked-in cron-image smoke script yet (that instrument is proposed by a
different track, not built). Until it exists, verify each new cron service the way the
existing nine are verified today: build `infra/cron-ingest/Dockerfile` locally and run the
new `ingest-<name> --help` inside the built image, confirming the command is reachable and
that nothing in its import path needs `alembic.ini` or `db/agri/**` — the same
deliberately-missing-files condition the Dockerfile's own comment explains. This is a
manual step in this track; do not claim automated coverage it does not have.

Route the approval pass to `quality-reviewer`. The author does not self-approve, and every
phase here writes a new, real upstream integration — exactly the class of change a second
reader exists to catch.

## Follow-up work opened by the 2026-08-06 review

The quality review returned `needs-changes`. These are the items still required before the four
datasets this track built are actually usable in the warehouse or on the map, not merely present
in the codebase — not scoped to any single phase above, so recorded here rather than folded back
into the phase acceptance criteria they postdate.

**(a) `historical_writer.py` persist verbs.** GloFAS and CAMS both fetch, validate and cache
locally but cannot write a warehouse row: `persist_glofas_flood_chunk` /
`finalize_glofas_release_set` and the CAMS equivalents (plus `_ensure_<lane>_source_release` /
`_ensure_<lane>_artifact`) need to be added to `execution/historical_writer.py`. The Ensemble
lane's migration-free path is different in shape — land raw members in `agri.forecast_observation`
under a `ForecastSeries` with `input_adapter='forecast_observation'` (the `signal_adapter_identity`
CHECK exempts that adapter) — but needs the same file's `_ensure_*`/`_advisory_lock` helpers, which
were outside every slice's declared boundary this session. Assign to a slice that owns
`historical_writer.py`; do not fork its helpers into a new module to route around the boundary.

**(b) The `forecast_method` / `model_kind` widening migration.** `ForecastRun.forecast_method` is
CHECK-constrained to `('sql_linear', 'ml')` (`models/forecasting.py:404`), mirrored by
`ForecastModel.ck_forecast_model_kind` and the `Literal` at `routes/forecasts.py:86`. An upstream
NWP ensemble reduced to empirical quantiles is neither value, and forcing one asserts false
provenance on a public serving surface — `'ml'` additionally implies a validated local training run
and model artifact that do not exist. Widening the CHECK to add an ensemble method has to move all
four places together: the DB constraint, `ForecastRun.__table_args__`,
`ForecastModel.ck_forecast_model_kind`, and the route `Literal`. This is a real migration; "the
ensemble lane needs no DDL" is true only of the quantile columns themselves (`quantile_levels` /
`quantile_values`), never of the receipt's `forecast_run_id` ownership.

**(c) The et0 model-guard (architect ruling, topic `et0-model-guard`) was not implemented.** The
ruling calls for `OpenMeteoArchiveSignal.required_model`, a frozen `OpenMeteoArchiveProduct` keyed
by model, and a `require_governed_lattice` rule tying every parameter's `required_model` to the
plan's product — with the model threaded through `_archive_daily_parameters` /
`archive_daily_url` / `archive_daily_request` so it actually reaches the wire, not just the plan
schema. None of this landed: `HistoricalOpenMeteoArchivePlan.model` is still the plain
`Literal["era5_land"]` it was before this track (`historical_open_meteo.py:155`), and
`ingest/open_meteo.py:224` still hard-codes `models=OPEN_METEO_ERA5_LAND_MODEL` inside
`_archive_daily_parameters`, never reading `plan.model` — confirmed unchanged this session. Phase
0's own structural-guard acceptance criterion was also not met: no test anywhere asserts
`et0_fao_evapotranspiration` stays absent from `OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS`. Until
either lands, the trap this track set out to close structurally is closeable only by convention.

**(d) Serving-side reads, geo views, and layer-registry entries for GloFAS/CAMS.** Per architect
ruling 1 (`glofas-cams-serving`): GloFAS as a viewport-bboxed point/reach GeoJSON read joined to the
existing water layer, CAMS as an isoband field reusing `geo.soil_field`'s measure-agnostic
aggregation — both through the environmental read model (the `getPublishedSoilField` pattern),
never a Martin tile function, because tiling would ship every day per tile against a 4-year daily
lattice where the read model bounds the payload by construction. Needs: one geo-side serving view
per family in `drizzle/`, following the `0016_soil_field.sql` pattern (`is_observed`,
`quality_flag='accepted'`, the `(signal, unit)` enumeration as the exposure gate), paired with the
matching update to `src/lib/server/db/migration-contract.ts` (that coupling fails at deploy, not at
edit time); registry entries in `src/lib/map/layer-registry.ts`; and a shared hook in
`src/hooks/useViewportProxiedLayers.ts`. Confirmed unstarted — no reference to `glofas` or `cams` in
any form exists yet in `historical_writer.py`, `layer-registry.ts`, or
`useViewportProxiedLayers.ts`.

**(e) Launch preconditions for the cron configs.** The quality review found both staged Railway
configs non-functional as shipped: `infra/cron-ingest/Dockerfile` copies only `pyproject.toml`,
`uv.lock` and `src/`, so there is no `/app/plans` in the built image and no plan file for either
`startCommand` to find even once one exists; the image's shell `ENTRYPOINT` runs four grouped
`agri-service` commands and is
never cleared by a Railway `startCommand`; and `settings.local_execution_root` defaults to an
unmounted, ephemeral path, so the checkpoint and raw cache never survive a restart and
`--max-chunks` would re-fetch the same chunks against provider quota every wake. The
`infra/cron-flood/railway.json` and `infra/cron-air-quality/railway.json` files were removed
deliberately rather than shipped broken — the review's own first minimal-fix option. Before either
is re-added: the persist verb from (a) must exist, a `plans/` `COPY` (plus the two lanes' plan
JSONs) must be added to `infra/cron-ingest/Dockerfile`, `ENTRYPOINT` must be cleared so the Railway
`startCommand` actually runs, and a Railway volume must back `local_execution_root` so state
survives between wakes.

### Recorded, not fixed — minors from the same review

Deliberately left in place on 2026-08-06. Each is a duplication or a local shortcut, none changes
what is served or what is stored, and each has a named owner-boundary reason for waiting.

**(f) CLI-layer duplication across the three new lanes.** `cli.py` now carries a third and fourth
and fifth copy of the same per-lane plumbing: `_historical_glofas_failure_reason` /
`_write_historical_glofas_blocked_checkpoint` (`cli.py:2174-2198`), and the byte-identical CAMS
(`:2341`) and Ensemble (`:2535`) pairs, plus each lane's own fetch-wave loop. The generalization
target already exists but is misnamed: `_write_historical_blocked_checkpoint` (`:2894`) is bound to
`HistoricalNasaCheckpoint` and `_historical_nasa_failure_reason` despite its lane-neutral name, so
generalizing means parameterizing it on (writer, reason-namer) — the same shape
`open_meteo_lane.py` already uses for `lane.label` — and collapsing all copies onto it. Not done
here because it touches every historical lane's CLI verb at once, which no slice owned.

**(g) `SoilPanel.tsx` hardcodes two `layerId` literals to satisfy a text-matching test.**
`SoilPanel.tsx:~170` renders `<LayerToggle layerId="soil-moisture" …>` /
`layerId="soil-temperature"` in two literal branches instead of `layerId={definition.toggleId}`,
and says so in a comment: the layer-registry contract test reads every rendered `layerId` out of
the source **statically**, so a computed id is indistinguishable there from an absent switch. The
fix is to invert the test — have it resolve the registry and assert each definition's `toggleId` is
reachable — and then restore `definition.toggleId` in the panel. Until then the panel and the
registry can drift and only a human would notice.

**(h) `SuitabilityBar` aliases the erosion ramp.** `SoilPanel.tsx:~312` colours a suitability score
from `EROSION_COLORS` (comment: "suitability has one fewer tier than erosion, so `very_high` is
unused here"). Reusing a ramp keyed to a different quantity means a future erosion re-key silently
recolours suitability, and the unused stop is evidence the ramps are not the same shape. Give
suitability its own 4-stop ramp next to the erosion one.

**(i) `_atomic_write` / `_require_aware_utc` / `_date_range` are copied per lane.**
`historical_open_meteo.py:871-891` and `historical_era5.py:820-840` hold the same three helpers,
and `_require_aware_utc` exists again in `historical_backfill.py:799`, `contracts.py:617`,
`historical_usdm.py:621`, `hot_projection.py:26`, `historical_promotion.py:45` and
`promotion.py:1121`. Converge the archive-lane trio into `open_meteo_lane.py` (the module those two
lanes already share) — but **only once those lanes' active backfills complete**: both files are
being executed by long-running fetches right now, and editing a module under a running four-year
job risks a restart that re-fetches against provider quota.

**(j) URGENT — Martin auto-publishes the whole database, and 0016's view made that expensive.**
Measured 2026-08-06 immediately after the c01ed48 deploy + Martin restart: the deployed
`plantgeo-martin` runs the official image with env-only config (`DATABASE_URL`, `PORT`,
`TILE_CORS_ORIGIN` — no config file), so `infra/martin/martin.yaml`'s `auto_publish: false`
allowlist has never applied and the live catalog exposes 35 sources, including raw
`geo.features`, `geo.geometry`, `agri.spatial_cell` — and now `geo.soil_field_observation`,
for which one public z6 tile request streams **>27 MB and runs >40 s** (every cell × every
day; the exact unbounded payload the architect ruling kept out of Martin). The app itself
never requests these sources (soil is served as tRPC isobands), but each request is an
unauthenticated multi-GB scan against the production database. Fix is the recorded "Martin
lockdown" gap, with two traps measured tonight: (1) `infra/martin/martin.yaml` is STALE —
the live app composes sources the yaml does not allowlist (`fire_detections`,
`drought_areas`, the OSM layers), so the allowlist must be reconciled against
`src/lib/map/sources.ts` before it is applied or live layers break; (2) the service has no
repo source connected, so shipping the config needs `connect_service_source` (Railway MCP —
re-auth required) or a `railway up` of an `infra/martin/` Dockerfile, with a rollback path
tested first. Until then every new geometry-bearing table or view added by a migration is
publicly served the moment it exists.
