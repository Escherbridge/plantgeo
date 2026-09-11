---
type: retrospective
status: historical
---

# Parquet operational checkpoints — September 11, 2026

This archive records completed slices against repository head `fa20223` and dated
operational receipts. It does not close their parent tracks or report new production
measurements from this documentation pass. The [registry](../../tracks.md) remains
the current work entrypoint.

| Completed slice | Evidence | Acceptance boundary |
| --- | --- | --- |
| Drizzle baseline collapse and production rebuild, September 8–9 | Commits `05e3165`, `93812b8`, `38ce9ac`; [September runbook archive](../../RUNBOOK-archive-2026-09.md), “THE DRIZZLE TREE IS NOW A BASELINE” and “PRODUCTION WAS REBUILT FROM EMPTY.” | The recorded rebuild reduced 40 GB to 30 MB, restored the preservation set and matched all 5,479 catalogue rows. This is a dated rebuild result. The executor remained running and could refill environmental tables; it does not establish permanent retirement of producers or today's database size. |
| NASA POWER temperature historical materialization and availability publication, September 10 | [Runtime repair record](../../tracks/environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md#final-temperature-publication-verified) and [exact generation receipts](../../tracks/environmental_postgres_retirement_20260904/evidence/temperature-bootstrap-receipts-20260910.json), bound by `323e45a`. | Each mean/min/max lane has 1,560 complete days, April 30, 2022–August 6, 2026, at z0/z5/z9/z13; each generation has 6,240 fully digested rows. Source ceiling September 5 does not fill the later interval. Historical publication does not prove forward burn-in or the full reader matrix. |
| Current MTBS capture, publication, selected-day serving and schedule reconciliation, September 10–11 | [Preserved rollout receipt](../../tracks/environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md), implementation `3632d61`, canonical-base-marker correction `fa20223`. | 747 fires in the bounded 2018–2026 population were published for September 11. Exact physical/public identity checks, historical-response preservation and browser inspection passed in that session. Seasons 2023–2026 remain partial; the 3,077 separately inventoried 1984–2017 fires remain outside this replacement. Daily publication/weekly capture configuration was verified; future scheduled execution was not observed. |

## Plan pruning and retained evidence

The [retirement plan through September 10](retirement-plan-through-20260910.md)
preserves the previous plan body and its corrections, with evidence links rebased. Its September 4 drop sequencing,
PostgreSQL backfills, migration filenames and unchecked boxes are historical evidence,
not executable instructions. The [current retirement plan](../../tracks/environmental_postgres_retirement_20260904/plan.md)
replaces those instructions with the remaining repair, source-direct ownership and
acceptance gates after the rebuild.

No evidence files were deleted. Existing receipts stay at their original paths;
the MTBS rollout summary was copied from ignored session evidence into the track so
the publication result survives session cleanup. Original logs, failed attempts,
candidate artifacts and publication JSON receipts remain preserved in `.omc/research/`.

## Lessons carried forward

- Capture completion, serving publication, deployment and scheduled advancement are
  different facts. Record each with its own receipt and stop condition.
- A large availability publication can be healthy before its first marker appears.
  The bounded eight-worker verification in `4b841b3` still performs checksum and
  final identity checks; the measured successful budget is not permission to omit them.
- A database baseline does not necessarily recreate configuration rows. The rebuild
  nearly lost 50 executor job definitions; the preservation set must include state
  that is neither schema nor reproducible source data.
- Retire stale instructions as soon as their successor is evidenced. Keep their exact
  historical text accessible and place the current gate beside the completed slice.

## Remaining ownership

- [Environmental retirement](../../tracks/environmental_postgres_retirement_20260904/plan.md):
  signal/sensor candidate admission, soil restoration, source-direct archive replacement,
  effective runtime cutoff and remaining removal proofs.
- [Gapless publication](../../tracks/gapless_parquet_publication_20260901/plan.md):
  historical gap authoring, governed absences and three scheduled advances with recovery.
- [Reader](../../tracks/parquet_reader_cutover_acceptance_20260901/plan.md),
  [renderer](../../tracks/multiscale_polygon_surface_20260901/plan.md) and
  [production acceptance](../../tracks/parquet_production_acceptance_20260901/plan.md):
  the full current product/day/zoom/cold-warm packet and exact release verdict.
- [Offline export](../../tracks/offline_export_service_20260908/plan.md): reconcile the
  completed builder/publication subset against the original phase reviews and measured
  performance acceptance; recover deferred relative-humidity 1981–2017 indexing.
  The standalone service was rejected; do not rebuild it or verified temperature history.
