---
type: track-plan
slug: environmental_postgres_retirement_20260904
status: active
updated_on: 2026-09-11
---

# Environmental retirement — remaining work

The [September 11 ingestion throttle audit](evidence/ingestion-throttle-audit-20260911.md)
is preserved byte-for-byte from legacy candidate `3a5f3902` as dated operational
and validation evidence. The [legacy reconciliation](../parquet_duckdb_pivot_20260823/evidence/legacy-candidate-reconciliation-20260911.md)
records its selective code repair and current successors. The audit's older
candidate/release request is superseded by the current
[integration release packet](../parquet_duckdb_pivot_20260823/evidence/release-packet-20260911.md);
its recorded observations are not fresh production acceptance.

## Wind & Weather acceptance addendum — September 11

- [ ] Jointly with gapless publication, reconcile weather product/source
  identity, uncited history floor/lag, published intervals and the recorded
  2021-11-27..2026-07-31 gap containing 2025-04-28. The current toggle is sampled
  estimates/observations with forecast horizon 0. Reanalysis cannot fill
  current-poll history under the same identity; retain explicit source/product
  authority for any historical replacement. See the
  [audit handoff](../parquet_duckdb_pivot_20260823/evidence/wind-weather-handoff-20260911.md).

This plan reconciles deployed repository head `fa20223` with dated September 9–11
evidence and the September 11 local product-repair preparation. Local preparation
is not deployed admission. The latest runtime observations are in the
[read-only checkpoint](evidence/runtime-and-retirement-readback-20260911.md).
The previous wave plan is preserved in the
[operational checkpoint archive](../../retros/parquet_operational_checkpoints_20260911/retirement-plan-through-20260910.md).
Its original wave-A/B/D instructions are not a current queue: the owner chose a
production rebuild on September 9, and subsequent repairs use preserved Parquet
or source evidence. Do not resume environmental PostgreSQL ingestion or export
from those historical checkboxes.

## Completed slices

- [x] **Production baseline/rebuild:** the September 9 runbook records 40 GB to
  30 MB, restoration of the preservation set, exact 5,479-row catalogue parity
  and readiness. The executor remained running and could refill tables; this
  checkpoint does not establish permanent producer retirement or today's size.
- [x] **Temperature historical materialization and availability:** mean/min/max
  each published 1,560 complete days, 2022-04-30 through 2026-08-06, with all four
  rungs independently checked. [Generation receipts](evidence/temperature-bootstrap-receipts-20260910.json)
  bind the exact result. The September 5 source ceiling does not fill the later gap.
- [x] **Current MTBS rollout:** 747 fires in the bounded 2018–2026 population were
  published for September 11 and verified through physical, public and browser
  reads after `fa20223` deployed. The existing job definition was reconciled and
  restored enabled. [Rollout receipt](evidence/mtbs-live-rollout-20260911.md).
  Seasons 2023–2026 remain partial. The later September 11 08:55 scheduled run
  succeeded; this proves execution, not three new publication advances.
- [x] **Signal artifact preservation:** the verified 222-day candidate archive
  was transferred from executor temporary storage into the local workspace with
  hash/member verification. [Preparation and transfer evidence](evidence/repair-preparation-20260910.md).
  Preservation does not admit those candidates into serving.

These slices are indexed in the
[retrospective](../../retros/parquet_operational_checkpoints_20260911/README.md).
The parent track remains active until the work below has exact evidence.

## Repair and admit preserved source evidence

- [x] **Signal and sensor local revalidation:** verify all 222 archived signal
  days and preserve 3,506,555 original rows and coordinate-witness lineage;
  reconstruct 2,212 ordinary multipart physical objects. Reproduce all 5,935
  positive sensor rows in 1,030 blocks without promoting incomplete source
  pagination into a complete-population claim.
  [Revalidation and correction packet](evidence/signal-sensor-candidate-revalidation-20260911.md).
- [x] **Signal current-state preparation:** complete two read-only passes over
  all 222 days, binding 444 current original-object pins and 2,212 proposed
  replacement pins. The September 11 19:05–19:29 UTC run made 4,446 read calls and
  no writes. This dated request does not prove external-writer quiescence or
  authorize correction/publication.
- [x] **Product preparation:** freshly capture and prepare the older
  3,077-fire MTBS population at every rung while reproducing all original
  747-fire rollback artifacts; verify all twelve static-soil asset hashes and
  actual native-pixel point values; provide a soil-survey preparer that requires
  a complete, hash-pinned source population and produces all four rungs.
  [Older MTBS packet](evidence/mtbs-older-recovery-20260911.md) and
  [soil packet](evidence/soil-restoration-preparation-20260911.md).
  Independent source review and byte/value replay accepted these local
  preparations; the admission gates remain stated separately.
- [ ] **Signal:** establish fresh mutation-time ownership and quiescence for the
  prepared 222-day request under ordinary locks and the publication barrier;
  preserve all original values and
  coordinate-witness lineage; publish and independently read back every required
  rung and availability generation. Follow
  [repair preparation](evidence/repair-preparation-20260910.md), not the old
  PostgreSQL re-export/retraction path.
- [ ] **Sensors:** resolve the September 5–6 positive-poll/governed-absence
  conflict using the preserved source responses and their pagination/roster
  limits. Candidate rows do not prove complete source coverage or justify absence.
  Preserve correction lineage and independently verify the final publication.
  [Sensor repair evidence](evidence/sensors-and-availability-repair-20260910.md).
- [ ] **Static soil and soil-survey:** use the
  [saved static-soil admission preparation](evidence/soil-static-admission-preparation-20260910.md)
  for its bounded point-reader scope. Restore soil-survey through governed
  Parquet with the required generalized low-zoom geometry as well as native
  detail; the September 9 owner decision expressly accepted temporary darkness,
  not a detail-only definition of done. Do not conflate these two product scopes.
- [ ] **Older MTBS admission:** define distinct-scope catalogue composition and
  the shared lane/day collision policy, then admit the prepared 3,077-fire
  1984–2017 candidate through ordinary publication and availability. Its earliest
  eligible day is September 12 from the actual fresh capture. Preserve the
  current 747-fire component, partial seasons and historical truncation, and
  obtain deployed selected-day acceptance. The local independent value/geometry
  replay is complete in the final repair review.
  [Exact owner handoff](evidence/mtbs-older-recovery-20260911.md).

## Prove source-direct operation and finish retirement

- [x] Read the exact deployed executor definitions, effective allowlist,
  handoff acknowledgements and operational leases. The September 11 deployed
  executor has the intended 20-lane allowlist and the separate complete lease
  query returned no live leases. The earlier `configured_pending_deployment`
  statement is superseded for this observed process by the
  [runtime checkpoint](evidence/runtime-and-retirement-readback-20260911.md).
- [ ] Complete producer retirement beyond that dated allowlist. Archive,
  vegetation catch-up and soilgrids definitions still exist enabled, their
  backlog is preserved, and external/manual quiescence is not established by a
  point-in-time operational lease query. Fresh quiescence remains an admission
  precondition; this task made no queue or service changes.
- [x] Implement bounded source-only FIRMS and NWIS archive preparers and strict
  response/value/day identity checks, with explicit refusal for ambiguous or
  incomplete input. [Archive preparation](evidence/archive-source-preparation-20260911.md).
  A bounded NWIS source read returned seven daily discharge readings for a small
  Sandy River bbox on August 5, 2022. No complete historical archive candidate was
  obtained in this slice.
- [ ] Complete the real source captures and fenced generic scheduler/admission
  replacement for old PostgreSQL archive/recovery handlers. Preserve current
  readings and the old backlog; neither a bounded source sample nor a failed
  request authorizes requeueing its old database-writing command.
  [Exact integration handoff](evidence/repair-handoff-20260911.md).
- [ ] Keep per-product forward refresh, historical gap detection that authors
  work, governed absences and recovery ownership aligned with
  [gapless publication](../gapless_parquet_publication_20260901/plan.md).
  Verify effective MTBS daily publication and weekly capture on later ticks;
  configuration readback alone is not scheduled execution evidence.
- [x] Re-inventory surviving relations, readers, fillers and operator recovery
  paths after the rebuild. The expanded proof scans root operator scripts and
  refuses shell-comment false clears. All 27 historical non-KEEP packets remain
  blocked. [Current inventory](evidence/retirement-reinventory-20260911.json).
- [ ] Remove only proven retired code or relations with current ownership,
  exact parity, preservation and rollback evidence. The
  original [inventory](evidence/retirement-inventory.md) is a historical map;
  its corrected zero-reader claims and old migration filenames are not warrants.
- [ ] Reconcile the legacy A1/A1c completion-marker provenance requirements and
  broad A4 bootstrap scope against current per-lane receipts. Temperature and
  MTBS results are bounded subsets, not proof that every lane has all-rung
  lineage and complete availability.

## Acceptance and final handoff

- [ ] Obtain the remaining reader/agent selected-day, spatial-neighbour and
  temporal-neighbour evidence, including explicit refusal/truncation semantics.
- [ ] Hand exact product/day/rung, deployed service/commit and rollback evidence
  to [production acceptance](../parquet_production_acceptance_20260901/plan.md).
  Full browser/cold-warm coverage, three scheduled advances and recovery gates
  remain open there; a successful bounded rollout does not close that matrix.
- [x] Run the checks selected by [testing policy](../../../docs/testing.md),
  disclose scope and environment skips, and obtain independent source review and
  saved-artifact replay. The broad Python run's two residual failures were fixed
  together and verified by all 179 affected soil-survey/retirement tests plus full
  static gates; the passing frontend gates and unrelated Python results are
  retained separately. This does not produce a full Python quality receipt.
  [Independent review](evidence/independent-repair-review-20260911.md).
- [ ] Complete the cross-lane admission and production gates above, then record
  the exact closure verdict and hand it to the registry/integration owner. The
  current [reviewed local handoff](evidence/repair-handoff-20260911.md) keeps those
  residuals open; this product lane does not edit the shared registry.

Production mutations continue to follow existing explicit authorization and the
release policy. The earlier September 11 documentation reconciliation performed
no production repair. The subsequent local preparation and read-only runtime
investigation are identified by their separate evidence receipts. The parent
track remains active; no relation drop, source publication, service deployment or
full production acceptance follows from those receipts.
