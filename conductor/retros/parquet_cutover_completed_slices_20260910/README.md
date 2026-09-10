---
type: retrospective
---

# Completed historical Parquet cutover slices

Archived 2026-09-10 as a reference index. This retrospective archives completed
information; it does not close or move a partially completed track. Original
evidence and paths remain intact so existing citations continue to resolve.

| Completed scope | Recorded evidence | Boundary retained |
| --- | --- | --- |
| Pivot d0: completion markers, lane-day coordination and missing-lane audit | [Pivot metadata](../../tracks/parquet_duckdb_pivot_20260823/metadata.json), slice d0, records completion on 2026-08-23 and RUNBOOK 0.35. | This historical completion is not a current lane-by-lane burn-in result. |
| Pivot d1: historical construction superseded by the immutable canonical snapshot | [Pivot metadata](../../tracks/parquet_duckdb_pivot_20260823/metadata.json), d1 and checkpoint 20260827, records 46,146,568 fact rows and manifest SHA-256 `465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`. | Do not restart the replaced shared PostgreSQL drain. Forward repair remains active. |
| Pivot d3: private API, bounded client and readiness repair | [Pivot metadata](../../tracks/parquet_duckdb_pivot_20260823/metadata.json), d3, records `complete_production_healthy`. | Comprehensive current product/browser acceptance remains a separate gate. |
| Pivot d5: dedicated governed climate and soil product construction | [Pivot metadata](../../tracks/parquet_duckdb_pivot_20260823/metadata.json), d5, records `superseded_done` for the original generic soil-field proposal. | Historical product construction does not close direct-source gaps, availability repair or retirement. |
| Static soil-survey partition design | [Existing archived metadata](../soil_survey_lane_shape_20260825/metadata.json) records resolution by `68da7af`: bounded streaming parts and four zoom rungs. | Production reader/browser acceptance was explicitly excluded. |
| SDK lattice phases 0–3 and supersession of phases 4–8 | [Existing archived metadata](../agri_sdk_layering_20260805/metadata.json) records shipped phases and the explicit 2026-08-22/23 package-boundary decision. | Preserve the import contract; current conformity work has its own owner. |

## Work that remains current

- [Reader acceptance](../../tracks/parquet_reader_cutover_acceptance_20260901/plan.md):
  its [authored verdict](../../tracks/parquet_reader_cutover_acceptance_20260901/evidence/reader-cutover-verdict.md)
  explicitly leaves production timing/request traces and acceptance gates open.
- [Gapless publication](../../tracks/gapless_parquet_publication_20260901/plan.md):
  direct-source repairs, governed absences, scheduled advancement and recovery evidence.
- [Production acceptance](../../tracks/parquet_production_acceptance_20260901/plan.md):
  cross-product probes, cold/warm browser and spatial matrices, burn-in and exact release verdict.
- [Environmental retirement](../../tracks/environmental_postgres_retirement_20260904/plan.md):
  remaining readers, signal/sensor issues, parity and per-relation retirement proofs.
- [Repository conformity](../../tracks/repository_conformity_hardening_20260901/plan.md):
  evidence-backed removal of obsolete code and current ownership boundaries.

The [current registry](../../tracks.md) remains the execution entrypoint. AI
acceptance, analytical/model work and unfinished gap fills are not completed by
this archive. The temperature publication and serving subset cannot substitute
for the whole reader or environmental retirement acceptance packet.

## Obsolete signal-view test intent retained — 2026-09-10

The cleanup removes `src/__tests__/services/signal-census-contract.test.ts`, which
checked the 19 governed signal-name literals in the archived
`drizzle/archive/0029_pre_aggregation_layer.sql` definition of
`mv_signal_cell_daily`. Its own September 8 stale-subject notice identifies the
relation as dropped by `drizzle/archive/0034_record_signal_cell_daily_drop.sql`;
the active `0000` baseline does not recreate it. The migrations and their evidence
remain preserved. This retires a test of that obsolete view, not the current
signal census or the governed signal vocabulary.

`climate-field-sql-contract.test.ts` remains because its exported SQL builders
remain present. `pre-aggregation-catalogue.test.ts` also remains: its signal and
drought census views are different relations that still exist in the baseline.
The tracked [implementation audit](cleanup-proof.md) preserves the cleanup proof
and its original authoring-time validation statement.
Final verification belongs to the coordinating lane; this note claims no new
passing test result.

The coordinating lane's subsequent [local verification record](verification.md)
records the completed checks and corrected environment setup without changing the
original authoring-time audit.
