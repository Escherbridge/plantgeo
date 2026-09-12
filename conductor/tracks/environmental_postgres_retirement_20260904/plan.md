---
type: track-plan
slug: environmental_postgres_retirement_20260904
status: active
updated_on: 2026-09-12
---

# Environmental retirement — remaining work

## Local evidence checkpoint — September 12

The [admission and cutoff reconciliation](evidence/local-admission-cutoff-reconciliation-20260912.md)
confirms that the ignored signal, sensor and static-soil artifacts cited by the
September 10 summaries are absent from this checkout. Their checked-in hashes are
a recovery ledger, not re-hashable admission inputs. It also preserves the cutoff
as `configured_pending_deployment` and records the still-missing bucket-pinned
fixed-support identity. No admission or cutoff checkbox below closes from this
local-only audit.

This plan reconciles repository head `fa20223` with dated September 9–11 evidence.
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
  Seasons 2023–2026 remain partial; future scheduled execution was not observed.
- [x] **Signal artifact preservation:** the verified 222-day candidate archive
  was transferred from executor temporary storage into the local workspace with
  hash/member verification. [Preparation and transfer evidence](evidence/repair-preparation-20260910.md).
  Preservation does not admit those candidates into serving.

These slices are indexed in the
[retrospective](../../retros/parquet_operational_checkpoints_20260911/README.md).
The parent track remains active until the work below has exact evidence.

## Repair and admit preserved source evidence

- [ ] **Signal:** revalidate the 222 prepared legacy-schema days under the
  ordinary locks and publication barrier; preserve all original values and
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
- [ ] **Older MTBS:** assess and prepare the separately inventoried 3,077 fires
  from 1984–2017. Keep captured source population, availability date, partial
  seasons, historical truncation and current snapshot replacement semantics
  explicit. The completed 747-fire rollout is not a full-history receipt.

## Prove source-direct operation and finish retirement

- [ ] Read the exact deployed executor definitions, active/required lane settings
  and leases. Reconcile the September 10 eight-lane pause receipt, which recorded
  `configured_pending_deployment`, against runtime evidence before calling the
  environmental ingestion cutoff effective.
  [Active-lane boundary](evidence/active-lane-database-boundary-20260910.md).
- [ ] Replace still-needed archive/recovery paths that read or write environmental
  PostgreSQL with source-direct or preserved-Parquet paths. The streamflow archive
  failure is not permission to requeue its old database-writing command.
  [Runtime repair record](evidence/parquet-runtime-repair-20260910.md).
- [ ] Keep per-product forward refresh, historical gap detection that authors
  work, governed absences and recovery ownership aligned with
  [gapless publication](../gapless_parquet_publication_20260901/plan.md).
  Verify effective MTBS daily publication and weekly capture on later ticks;
  configuration readback alone is not scheduled execution evidence.
- [ ] Re-inventory surviving environmental relations, readers, fillers and
  operator recovery paths after the rebuild. Remove only proven retired code or
  relations with current ownership, preservation and rollback evidence. The
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
- [ ] After the full implementation batch, run the final checks selected by
  [testing policy](../../../docs/testing.md), disclose scope and environment
  skips, and obtain an independent review. Record a precise remaining-work or
  closure verdict in the registry and metadata together.

Production mutations continue to follow existing explicit authorization and the
release policy. No production action, data repair or new passing test result was
performed by this September 11 documentation reconciliation.
