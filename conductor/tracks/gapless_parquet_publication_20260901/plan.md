---
type: track-plan
slug: gapless_parquet_publication_20260901
status: active
resource: ./spec.md
---

# Plan

## Current checkpoint — September 11

The [current evidence packet](evidence/generic-recovery-20260911.md) records a
read-only re-inventory of 32 physical registrations, 27 time-bearing products,
all four required rungs and the deployed executor. Ninety-eight tick tables and
the lease readback prove September 10's eight-lane cutoff effective on the
captured `fa20223` deployment; 20 lanes remain active. Six soil products each
have three consecutive scheduled backlog advancements with physical evidence.
This does not close the gate for every activated product or the remaining
source-horizon and aggregation-conservation work.

Generic recovery changes are local to this work. No production deployment,
configuration, ledger, bucket or service mutation was performed. The local
verification and review receipt is recorded in that packet; live recovery,
rollback-by-disable and any later repair/removal remain separately gated.

Before executing the historical P3 recovery checklist below, use the current
[retirement plan](../environmental_postgres_retirement_20260904/plan.md): the rebuild
and later source-direct replacements changed the old lane/command identities.
Do not resume database-writing archive lanes from a dated dead-letter list.
Reconcile older unclosed soil-wetness, precipitation, dew-point, burn-severity and
drought session horizons against their own source coverage requirements; a newer
canonical snapshot does not automatically discharge broader older requests.

## Wave P0 — inventory and contract freeze

- [x] Enumerate physical product identities and the canonical rung set: the historical September 2
  census counted 28 time-bearing identities; September 11 code and live inventory establish 32
  registrations, 27 time-bearing and five static/reference, with rungs 0/5/9/13.
- [x] Freeze every provider floor, receipt-derived source ceiling, lag and cadence — DECLARED-frozen
  2026-09-02 from `LANE_REGISTRATIONS`/`LANE_SPECS`/`SNAPSHOT_PRODUCTS` source, not MEASURED against a
  fresh production R2 ladder or provider receipt; see
  `evidence/product-ownership-census.md` Tables 1b/2/3 and its "Explicit unknowns" section. Measured
  source ceilings remain owed to acceptance. September 11's fresh object-store re-list is complete
  as a name-level inventory, with ordinary pointer/generation checks; it is not a new provider census.
- [x] Record current, legacy and proposed executor ownership plus no-overlap handoff state.
- [x] Re-list live coverage and incomplete ladders read-only on September 11; preserve listing bounds,
  internal gap ranges, pointer gaps and non-atomic snapshot limits in `evidence/live-reinventory-20260911.json`.
- [ ] Independently measure every provider ceiling and reconcile full-horizon source coverage and rung conservation.
- [x] Freeze terminal-state, governed-absence and source-settlement rules.
- [x] Freeze the generational availability schema, pointer contract, conditional-update behavior,
  bootstrap receipt and no-request-time-scan tripwire.

## Wave P0A — availability core and one-time bootstrap

- [x] Implement one canonical availability schema, reader and publisher with content-addressed
  immutable generations, typed evidence cross-binding, bounded reads, pre-CAS evidence-identity
  revalidation, a lane-wide shared/exclusive publication barrier and a checksum-bound pointer written
  last. The final integrated Python gate and independent review passed.
- [x] Add an idempotent bootstrap command that consumes verified manifests/checkpoints once and
  writes an immutable receipt binding their keys/SHAs, the exact inventory root and authoritative
  required-rung set; it is never called by an HTTP or slider request. No production bootstrap ran.
- [x] Prove typed receipt cross-binding, evidence mutation refusal, bounded object reads,
  pointer-race retry, correction generation, governed absence, all-rung completeness,
  malformed/checksum refusal, rollback behavior and writer/publication exclusion. The amended
  implementation passed the root-coordinated full integrated Python gate and separate review.

## Wave P1 — parallel source-family writers

- [x] Build climate direct writers, solar first, within one bounded source-family owner
  (2026-09-02, `2b4cfef`: NASA POWER point-per-cell writer for all six products, SHADOW, never run live;
  ERA5-Land products remain CDS-credential-blocked).
- [x] Build NASA POWER and ERA5-Land soil/product writers in disjoint ownership lanes
  (2026-09-02, `12fa189`: `pipeline/direct/soil` on the Open-Meteo archive, keyless; soil-wetness in the climate writer; shadow).
- [x] Reconcile existing fire, water, vegetation, weather and sensor writer ownership — done by the
  2026-09-02 executor-only scheduler handoff, not by new work in this wave; see its "Authoritative
  responsibility matrix" for the full existing-Railway-writer-to-executor-lane mapping:
  `evidence/scheduler-handoff-20260902.md:51-69` (per-service command/product, cadence and source
  ceiling, executor registry mapping, checkpoint/concurrency fence, retry/dead-letter and
  rollback/disposition for `plantgeo-ingest-cron` [fire/water/vegetation/weather/sensor's shared
  Postgres bridge, drought, evacuation-zones, geometry repair], `plantgeo-fire-detections-forward`
  and `plantgeo-water-gauges-forward`).
- [ ] Register each product only after its source-family review passes and its writer extends the
  availability generation after terminal publication.

## Wave P2 — repair and scheduler integration

- [x] Author missing-day work and bounded idempotent repair (2026-09-02, `12fa189`: ladder-aware census, `repair_one_lane_day`, re-index claim).
- [x] Require every required rung before terminal publication (2026-09-02: `derived_empty` receipt closes emptied rungs; ladder census).
- [x] Extend availability for published and governed-absence outcomes without rescanning history
  (2026-09-02: claim-first extension after the completion marker in `fill_one_lane_day`; the
  subsequently repaired `derived_to_zero_rows` hole is no longer a current blocker).
- [x] Close generic September 11 recovery holes: retain original absence proof, author a durable
  absence-repair intent before ladder writes, refuse query-zero absence fabrication, recheck immutable
  ladders under lock, and abort executor dispatch when pinned leadership or child-reap certainty is lost.
- [x] Verify the final generic recovery batch with the full Python gate (6,016 passed, 149 skipped,
  one xfailed), repository boundary guards and independent exact-tree approval; see
  `evidence/validation-20260911.json`. Database and live production exercises remain separate gates.
- [x] Register executor schedules, leases, checkpoints, retries and dead-letter visibility.
- [x] Document source-by-source pause, lease-expiry, activation and rollback procedures.
- [x] Inventory every live Railway scheduled/one-shot writer and bind its exact command, cadence,
      source settlement, checkpoint, lease, retry/dead-letter and rollback to the executor registry.
- [x] Retire `infra/cron-ingest/railway.json` and its cron-only Dockerfile,
      `infra/cron-mtbs/railway.json`, `infra/cron-soilgrids/railway.json` and its cron-only
      Dockerfile, `infra/parquet-drain/railway.json`, both direct-forward Railway configs, and add a
      guard that rejects any tracked `cronSchedule` resurrection.
- [x] Preserve the dedicated continuous `railway.job-executor.json`; record the scheduler handoff in
      `evidence/scheduler-handoff-20260902.md`.

## Wave P3 — controlled production handoff

- [x] Record the 2026-09-02 owner authorization for executor-only scheduling and cron-object removal.
- [x] Merge the reviewed release to `main` and prove the exact executor deployment is `SUCCESS`.
      Historical release `e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1` was active in deployment
      `b1f35a20-6e05-48ff-9801-5235c9753a01`. September 11 preflight observes `fa20223` in
      `95304e24-16ed-4705-a615-c60b0e368a08`; this work's generic fixes are not deployed.
- [x] Receive the explicit orchestration follow-up, then prove all legacy owners and executor leases
      inactive before activation.
- [x] Fence all six legacy service objects with null schedules, no-op commands and `NEVER` restart;
      verify `scheduled=[]` across the production environment and activate 37 executable lanes.
- [x] Repair 200 pre-existing `matview-refresh` dead letters caused by absent
      `geo.mv_feature_observation_day_axis` and `geo.mv_signal_cell_daily`; repair the WFIGS payload
      bound that placed `postgres-fire-perimeters` in retry backoff (code repaired 2026-09-02,
      `2b4cfef`; dead letters left standing; first unassisted tick not yet observed —
      `evidence/p3-runtime-blockers-repair.md`).
- [x] Decide how a lane frozen by a dead letter is released (code 2026-09-03): a `coalesce_latest`
      lane reopens at the next bucket by itself until three consecutive failures trip the breaker; a
      `replay_oldest` lane is held by its first failure; a held lane waits for `ops jobs-supersede-run`
      with evidence (one resolved `agri.job_incident` row; no run, work item or attempt is written) and
      resumes at the current bucket. Adversarial review verdict in the RUNBOOK ledger; premise
      correction and the four replay lanes' 2026-09-02 causes in `evidence/p3-runtime-blockers-repair.md`.
- [x] Repair the geometry lanes' coarse-rung derivation (2026-09-03): `_load_spatial` now points DuckDB
      at the image's extension directory before its first LOAD, which is why every z9 rung of
      `parquet-drought`, `parquet-evacuation-zones` and `parquet-fire-perimeters` died on 2026-09-02
      (`warehouse/parquet/AGENTS.md`, "The derivation session and the extension directory").
- [x] Reconcile the September 2 dead-letter checklist against current ownership before any recovery.
      Its PostgreSQL writer commands and automatic supersession instructions are historical, not
      an executable queue. September 11 retains the eight-lane cutoff, sensor failure hold and
      separate product restoration gates; no old archive lane was resumed or incident superseded.
- [x] Prove the current eight-lane cutoff from deployed tick tables and absence of owned leases for
      that group. Fifty stored definitions remain enabled; the environment is not globally drained.
- [ ] Close the bounded observed tails without rewriting valid immutable days.
- [ ] Publish governed absences only with source receipts.
- [ ] Observe retry, restart and expired-lease recovery on the exact reviewed production release;
      local regression evidence alone does not satisfy this operational gate.
- [ ] Remove eligible remaining legacy writer objects only after exact authorization, mapped-lane
      proof and a fresh no-in-flight readback. Three no-op objects remain (fire forward, water forward,
      soil one-shot); ingest/MTBS/soilgrids are already absent, without new removal receipts from this work.
- [ ] Exercise rollback by disabling an executor lane; never restore a Railway cron schedule/service.

## Wave P4 — burn-in and handoff

- [ ] Record at least three consecutive scheduled advancements per activated lane.
- [x] Record the bounded six-product soil subset: three consecutive hourly scheduled runs added
      August 5/4/3, with 18 product-days and 72 rungs physically verified and bound to the writer IDs.
- [ ] Reconcile manifests, receipts, coverage and rung conservation after advancement.
- [ ] Hand exact evidence to `parquet_production_acceptance_20260901`.
- [ ] Leave PostgreSQL retirement blocked in the existing shrink track.
