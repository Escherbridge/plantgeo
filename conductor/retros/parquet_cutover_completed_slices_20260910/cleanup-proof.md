---
type: track-evidence
---

# Completed-cutover cleanup audit — 2026-09-10

Scope: remove unused public Python snapshot cutover dispatch and demonstrably obsolete tests.
No bucket, database, deployment, lane-configuration, or AI source changes were performed.

## Proof and canonical replacement

- `src/agri_data_service/parquet_ops/snapshot_products.py:208` declares the active
  `SNAPSHOT_PRODUCTS` catalogue empty. `PRODUCT_BY_LAYER` derives solely from that catalogue.
  The three descriptors at line 171 are `FROZEN_SNAPSHOT_PRODUCTS`, explicit provenance only.
- The published receipt archive at
  `conductor/tracks/environmental_postgres_retirement_20260904/evidence/temperature-bootstrap-receipts-20260910.json`
  records each graduated lane's 6,240 rows, 1,560 complete days and four rungs. The associated
  runtime-repair evidence records canonical snapshot provenance and successful bootstrap verification.
  Root subsequently verified deployed exact-day temperature API and browser rendering; this cleanup
  does not manufacture new deployment evidence or claim a new release.
- Before cleanup, the only production callers of `serves_from_snapshot` were the HTTP and CLI
  Parquet adapters. Every call therefore returned false with the real catalogue. The HTTP/CLI
  snapshot helpers had no callers outside those branches; remaining references were their own tests.
  `SnapshotCoverageCache` added an empty result to every public coverage answer.
- Canonical replacements remain `serving.resolve_day`, `resolve_window`, and `resolve_release`,
  through the unchanged bounded `_run_row_read`/`_row_read` seams. HTTP coverage retains
  availability-first resolution, withholding, permitted fallback, rollup, and async single-flight.
  CLI coverage remains an explicit census audit. `coverage.registered_census_lanes:96` no longer
  subtracts the empty transitional catalogue.
- Removed snapshot-only HTTP status entries alongside their unreachable adapters; core frozen
  refusal types remain with explicit frozen readers.

## Retained recovery and proof machinery

Do not delete `snapshot_products.py` wholesale. `load_snapshot_evidence:356`,
`resolve_snapshot_evidence_day:463`, and `resolve_snapshot_evidence_window:517` operate on explicit
frozen descriptors/evidence. The canonical NASA/ERA5 builders remain replay and repair tools;
their pinned roots, manifest checks, completion checks, lineage and policy invariants remain intact.
The coverage-forward port and generic frozen coverage helpers remain explicit recovery surfaces.
`compile_availability_bootstrap.py` and `pipeline/parquet/availability_index.py` remain necessary
for other lane bootstraps, publication, revalidation and recovery. No bootstrap checks were removed.

## Tests removed and retained

- Removed HTTP fake-registry snapshot dispatch tests and the cold-snapshot-adapter admission test,
  whose target helpers were removed. Removed the empty snapshot coverage fake and its assertions.
  Kept generic coverage single-flight, policy, failure and serving admission coverage.
- Replaced the dispatch tests with real fake-warehouse exact-day checks for all three temperature
  statistics, historical and forward dates (`tests/interface/test_parquet_routes.py:145`). Added
  CLI day/window parity for historical dates (`tests/interface/test_parquet_cli.py:22`). These
  assert returned rows and exact days, not empty-catalogue implementation details.
- Removed two census tests tied to the old dual-subsystem membership rule; the existing complete
  registry membership, unique-layer, schema and per-lane cadence assertions retain the live contract.
  Removed the now-false long inline cutover chronology above frozen descriptors; the directory
  documentation retains that history, and the descriptors and frozen read implementation are unchanged.
- Deleted `src/__tests__/services/signal-census-contract.test.ts` (138 lines). Its own STALE SUBJECT
  note says it only checks archived 0029's 19 signal names for `geo.mv_signal_cell_daily`, dropped
  by `drizzle/archive/0034_record_signal_cell_daily_drop.sql`. Active baseline has no CREATE for
  that relation. The frozen migration and historical evidence remain; sensors' Conductor retrospective
  records the removed test's intent. No live SQL definition or reader was tested by this file.
- Retained `climate-field-sql-contract.test.ts`: it exercises four still-exported statement builders
  used within retained `environmental-read-model.ts` (soil at 1451/1517, climate at 2009/2089).
  Dropping this real PostgreSQL parse/UTC test before retiring its subject would lose coverage.
- Retained `pre-aggregation-catalogue.test.ts`: its seed/census subjects are distinct from the dropped
  cell matview. `drizzle/0000_baseline.sql:1054` and `:1270` retain the drought/signal observation
  census definitions, used by the UNION at `:1794`. This mixed file is not deletion-ready on the
  evidence that justified removing the signal-cell test.

## Validation and rollback

No test suite was run during authoring. Import cleanup and Ruff formatting completed for the five
touched Python files. Parent will run the relevant-surface integrated checks after all edits.
Independent review is required before release. A source revert restores the adapters; no data or
configuration rollback is required because this patch changes neither.

## Follow-up: unused MTBS PostgreSQL exporter — 2026-09-10

This additional source cleanup removes only
`pipeline/lanes/burn_severity.py::export_burn_severity_release_day`, its private part-size constant
and exclusive imports, plus the three tests that invoked that exporter. It does not change the
MTBS source registry, cadence, data, live service configuration or source fetcher.

Repository-wide reference inspection before removal found exactly three executable call sites,
all in `tests/parquet/test_burn_severity_lane.py`; other matches were the definition and historical
module documentation. There was no registered command, dynamic import or operator-script call to
this function. `pipeline/parquet/lane_registry.py` already binds burn-severity to
`_refuse_burn_severity_direct_export`, which names the canonical direct writer. The scheduled
`burn-severity-direct-forward` uses that direct writer; no registration was changed here. The
parent's prior deployed-source and QA evidence remains a prerequisite for release acceptance,
not a new production observation made by this deletion pass.

The replacement `pipeline/direct/burn_severity/adapter.py` owns its independent 100-row part cap,
sorting, upstream-evidenced governed absence and ordinary all-rung finalization. Retained
`tests/direct/test_burn_severity_direct_adapter.py` exercises complete publication, genuine
zero-fire source results, disproven-absence correction, wrong-day refusal and multipart spill.
Existing direct parity and registry-refusal tests remain. We do not carry forward the removed
exporter's inference that zero PostgreSQL rows by themselves establish an upstream absence.

The deleted test intent is retained in the [retrospective index](README.md#removed-mtbs-exporter-test-intent--2026-09-10).
The same-file `read_burn_severity_release_day`, SQL, row fixture, fake session and schema/day-binding
test remain unchanged in behavior: `pipeline/validation/burn_severity.py` still imports and calls
that reader. Reconciliation is retained machinery, not evidence of a currently scheduled caller.
The live MTBS parsing/capture module, registered `ingest-mtbs` command, TypeScript regional-context
consumer, frozen snapshot recovery, signal/sensor correction operators and migrations all remain.
Historical references to the replaced exporter describe the prior implementation.

No tests were executed during this follow-up authoring. `check.py --changed --plan` is expected to
select the full suite because `pipeline/lanes/` is shared/unmapped; the explicitly scoped
`--batch direct` includes relevant Parquet/direct/ingest/executor/import contracts but cannot mint
a full quality receipt. Parent owns the final integrated validation and independent review.
Rollback is a source revert; this cleanup requires no data or configuration rollback and does
not close any remaining ingestion, completeness, or retirement work.


### MTBS regional reader replacement and dead service removal � 2026-09-10

The regional-context burn-history subsection now calls the same selected-day governed
Parquet reader as the map. A repository-wide source reference check found no remaining
imports of `services/mtbs` or `environmental/wildfire`; `BurnSeverityClass` and
`MTBSFireProperties` occurred only in those two obsolete files. Both files were removed.
No dedicated tests imported that reader; the regional tests now assert the governed
reader's selected-day call, retained capture metadata, empty output and failure state.
The old reader's private severity mapping, Redis cache and live ArcGIS query therefore
have no surviving registration. The independently implemented public
`wildfire.getMTBSPerimeters` unpublished-risk procedure remains; its matching name is
not a call to the deleted module. Python MTBS parsing, its severity regression tests,
reconciliation SQL, and pending publication/recovery operators remain.

This follow-up supersedes only the earlier paragraph's claim that the TypeScript
regional consumer remained on the old module. It does not claim any new snapshot has
been published, full mapping is complete, or the active retirement track is closed.
Historical source references preserve the old behavior's intent. Root owns the combined
validation and independent approval; no tests were run during this authoring pass.
