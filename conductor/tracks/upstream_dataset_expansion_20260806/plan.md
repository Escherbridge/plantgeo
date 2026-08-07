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

| Phase | Scope | Standard | Status going in |
|---|---|---|---|
| 0 | Retire the CDS soil lane; close the et0-model trap structurally | — | unblocks trust in the lane |
| 1 | `vapour_pressure_deficit_max` | durable | verified (3.32 kPa) — no probe needed |
| 2 | Open-Meteo Flood API (GloFAS discharge) | cron | needs a live-endpoint probe |
| 3 | Open-Meteo Air Quality API (CAMS) | cron | needs a live-endpoint probe |
| 4 | Open-Meteo Ensemble API | cron, schema-gated | needs a probe and an owner decision |

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
the new command reachable inside it (`agri-cli ingest-flood-discharge --help`), matching
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
