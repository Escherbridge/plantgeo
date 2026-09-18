---
type: track-plan
status: active
date: 2026-09-13
resource: ./spec.md
---

# Freshness and publication resolution

This is the actionable follow-up to the [September 13 audit](evidence/freshness-audit-20260913/audit.md).
The audit is complete; implementation and operational recovery remain open. Existing environmental,
gapless, multiscale and acceptance tracks retain ownership. No production repair is claimed.

Local implementation started in the [dedicated session fan-out](fanout-launch-20260913.md).
Workstreams run in isolated worktrees; shared integration, final checks and production recovery
remain with the coordinating session.

## R0 — Bind a repair candidate and protect concurrent work

- [x] Capture deployed revisions, effective activation, executor tick and all returned coverage rungs.
- [x] Separate actual missing publication, disabled/held work, expected provider lag and static release age.
- [ ] Re-read runtime state at execution time; inspect the exact failed sensor attempts and original cause.
- [ ] Inventory physical objects, complete ladders, availability generations and missing indexes for
  each proposed repair interval; sample upstream source receipts to validate ceilings and absences.
- [ ] Bind the implementation candidate to a clean commit/worktree. Preserve the in-flight Herbaria
  and intervention work; coordinate any narrow edits to shared routers, registries and UI files.

Exit: exact per-product repair inventory with supported floor, measured source ceiling, gaps,
governed absences, geometry/rungs, current owner, intended owner, affected files and rollback scope.

## R1 — Restore interrupted forward operation

Owner: gapless publication. Priority P1; follows R0.

- [ ] Determine why perimeter direct refresh was withheld; validate capture, provenance, geometry
  and static publication before adding only `fire-perimeters-direct-forward` to the active set.
- [ ] Fix the sensor attempt failure, then supersede the held run with the recorded incident and
  observe a new successful attempt plus public publication. Prioritize the six-day source replay
  window and preserve prior failure evidence.
- [ ] Inspect shortwave product-level results and June backlog. Keep the 75-day policy until a
  measured replacement is justified; distinguish source-unsettled, acquisition failure and index lag.
- [ ] Preserve active MTBS daily/current capture ownership; do not enable `mtbs-forward` by assumption.

Exit: attributable source/output receipts and public readbacks for each restored product; three
consecutive scheduled publication advances, or explicit unchanged-source checks with separate
capture evidence for static releases. A `not_due` tick does not count as an advance.

## R2 — Make publication repair durable

Owner: gapless publication; shared infrastructure. Priority P1; can be developed alongside R1.

- [ ] Add bounded executor reconciliation for object-complete/index-missing days, pending claims,
  missing bootstrap and incomplete ladders, consuming retained source evidence where valid.
  - Status 2026-09-15 (lane A3): bounded gap repair scaffold landed -- `execution/gap_repair_contract.py`,
    `execution/gap_repair.py` (`ops jobs-plan-gap-repair`, needs one registration line in
    `interface/cli/ops.py`), repair kind + `_plan_repair_runs` in `job_executor_service.py`. Repairs run
    the lanes' own writers with `--max-days`; index-missing/ladder reconciliation is NOT covered.
- [ ] Report acquisition, object completion and serving publication separately. A green acquisition
  must not satisfy the end-to-end freshness gate while availability is owed.
- [ ] Keep immutable generations, lane-day locks, conditional pointer updates and marker-last writes.
  Run bootstrap only in the explicit offline path; never add request-time historical scans.
- [ ] Add independent freshness reporting that compares publication against a current expected
  horizon and a measured source horizon, retaining the age of each measurement.
  - Status 2026-09-15 (lane A3): `parquet_ops/freshness.py` computes `expected_horizon_day`,
    `staleness_days`, `behind_provider` on every coverage row from the registered lag, independent of the
    pointer. NOT on the frozen `/api/v1/parquet/coverage` wire: requires the schema-3-to-4 contract
    change recorded in `parquet_ops/AGENTS.md`, "Independent freshness". Measurement age not yet carried.

Exit: injected availability failure, restart, retry, missing bootstrap and pointer-race cases recover
without refetching already durable data or declaring an unserved product published.

## R3 — Repair history from admitted sources

Owner: gapless publication. Priority P1 for measured gaps; follows inventory and R2 contracts.

- [ ] Add NDVI source-direct historical ownership covering the measured September 1–5 gap and
  any other missing portion of the declared supported history.
- [ ] Repair water/weather September 6 and the remaining in-scope reported intervals; apply the
  UI-supported water floor rather than blindly replaying every older stored range. Weather's
  current-only feed cannot recreate missed observations: recover retained source captures, admit
  a separately identified source if valid, or record an evidence-backed unrecoverable gap.
- [ ] Reconcile shortwave June gaps and relative-humidity historical gaps against actual source
  availability. Use bounded oldest-gap work so an unsettled newest day cannot starve older repairs.
- [ ] Add FIRMS historical/outage repair outside the five-day window and across August 24/25.
  Preserve SP-over-NRT precedence and product availability rules. Current detection coverage has
  no reported temporal gap; do not republish its full history merely to test the new path.
- [ ] Reconcile burn-severity cohort and release semantics for the two measured gap intervals;
  separately admit any older source scope instead of relabeling the current cohort complete.
- [ ] Register periodic gap detection that authors durable work for every supported product horizon.
  - Status 2026-09-18 (lane A3): the executor leader now authors the same bounded work itself every
    `PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS` (default 6 h) via `_author_due_repairs`, and a new
    process releases a breaker-held lane once (`ProcessStartRelease`); lanes without a bounded writer
    knob are still reported `no_repair_binding` with the reason. Verb kept for on-demand turns.

Exit: every targeted missing interval has validated objects and required rungs plus an availability
entry, or a source-backed governed absence/refusal. Include an outage longer than the forward
lookback in regression verification and record remaining unsupported history explicitly.

## R4 — Restore static soil survey and geometry

Owner: environmental serving with multiscale geometry. Priority P1; independent of R1/R3.

- [ ] Admit a bounded resumable source-direct USDA SSURGO acquisition and publication path.
  Preserve survey-area/vintage identity, `mupolygonkey` grain, geometry and immutable source receipts.
- [ ] Reconcile surviving schema/point reader/validator with the new publisher; implement the
  missing governed viewport reader and HTTP-to-TypeScript map integration.
- [ ] Register static release watermark refresh, missing spatial coverage repair, complete supported
  geometry tiers and coverage reporting. Release vintage must remain distinct from publication time.
- [ ] Restore the SSURGO toggle only when the release is admitted; provide truthful unavailability
  and remove the dangling historical backfill entry point under the repository retirement contract.
- [ ] Treat SoilGrids point properties and raster assets as a separate admitted static product.
  Correct the broken point-lookup guidance and restore each intended surface with its own receipts.

Exit: per-survey-area counts/vintages reconcile; no duplicate delineation keys; valid native and
derived geometry; complete tiers; known-point and viewport API/browser/agent parity; bounded resume
and release refresh proven. Do not restore PostgreSQL environmental payload fallbacks.

## R5 — Verify the final implementation and sustained operation

Owner: production acceptance and platform QA; independent review required.

- [ ] Apply all implementation fixes before one final verification sweep. For an application batch,
  retain boundary/type/lint checks and changed-surface tests; shared harness or release work gets
  the required full suite. Python scoped checks do not produce a full quality receipt.
- [ ] Verify source date, requested day, served day, release date, publication age, missingness and
  spatial support at every supported rung in cold/warm API, browser and agent journeys.
- [ ] Prove retries, restart/lease recovery, publication reconciliation and sustained scheduled work.
- [ ] Confirm the exact deployed candidate, exclusive lane ownership, protected botanical release
  identities and intervention behavior. Keep immutable rollback artifacts.
- [ ] Submit the dated evidence to production acceptance. Close only the products whose full gates
  pass; retain RED for unresolved cases rather than issuing a platform-wide success claim.
