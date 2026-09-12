---
type: track-evidence
track: gapless_parquet_publication_20260901
audited_on: 2026-09-12
status: local_contracts_reconciled_runtime_gates_open
source_commit: 8c14ea0117ba14d5584f737b793f989566102514
source_tree: de840fb558a110e0aded8f2baf44a39b9717f0d7
---

# Local publication ownership and recovery audit

This receipt records static source and test-definition inspection at the commit
and tree above. It records no newly passing tests, production counts, storage
coverage or runtime recovery. Railway, object storage and all databases were out
of scope; no writer or load was started, stopped, paused or interrupted.

## Ownership and evidence boundary

The [root task ledger](../../platform_experience_qa_20260911/evidence/task-ledger.md)
row at line 18 assigns QA orchestration, the shared ledger, registry and runbook to
task `01a0904b-756b-7961-b991-cab666123be2`. This bounded continuation owns this
track's documentation reconciliation only. The metadata partitions computed on
September 2 remain dated historical assignments, not a current activation list.

The [retirement plan](../../environmental_postgres_retirement_20260904/plan.md)
supersedes the pre-rebuild operational queue: source-direct or preserved-Parquet
recovery must replace still-needed environmental database paths. The old P3 lane
names and supersession commands are not instructions to resume those paths.

The [September 10 boundary receipt](../../environmental_postgres_retirement_20260904/evidence/active-lane-database-boundary-20260910.md)
records supplied active-set dependencies and the eight-lane pause scope. The
current retirement plan still requires exact deployed definitions, active/required
lane settings and lease readback before treating that cutoff as effective.

## Derived-empty publication is implemented locally

The P2 plan still described the `derived_to_zero_rows` ladder hole as open. The
inspected source distinguishes two cases:

- `pipeline/parquet/derivation.py:233` retracts a derived rung, refuses conflicting
  governed absence, checks prune success, then writes a zero-part, zero-row
  `derived_empty=True` completion marker at line 265.
- `pipeline/parquet/availability_extension.py:825` keeps an unmarked rung as a
  named ladder gap; line 843 accepts a marked empty derived rung as published with
  zero rows, no data receipts and its completion receipt.
- `pipeline/parquet/AGENTS.md:797` documents the repaired protocol and retains
  `DERIVED_TO_ZERO_ROWS` for legacy or interrupted unmarked rungs. This does not
  claim that every existing physical rung carries the receipt.

Paths in this receipt beginning `pipeline/`, `execution/`, `jobs/` or `sql/` are
relative to `services/agri-data-service/src/agri_data_service/`; `tests/` is
relative to `services/agri-data-service/`.

Inspected regression definitions make the distinction explicit:
`tests/parquet/test_availability_extension.py:984` asserts that the marked empty
rung extends availability and keeps the day selectable; the preceding test at
line 957 retains the unmarked ladder refusal.
`tests/parquet/test_availability_index.py:693`, `:706` and `:762` cover derived
zero-row admission, base-rung refusal and bootstrap completion verification.
`tests/parquet/test_gap_fill.py:1416`, `:1479` and `:1499` cover a missing coarse
rung, a completed empty rung and legacy-marker re-derivation respectively.
These are inspected assertions, not a fresh execution receipt.

## Scheduled gap ownership has bounded scope

`execution/job_executor_service.py:437` still registers generic `parquet-*` jobs
as bounded `parquet-gap-fill` commands. Its conflict maps at lines 412–430 and
`_parquet_spec` prevent activation beside competing direct siblings. A retained
exporter schedule does not acquire missing upstream history, and local registry
presence does not prove effective deployment or activation.

The generic time-series census has a different scope from the direct lookbacks:
`pipeline/parquet/gap_fill.py:474` covers the registered history floor through
the settled ceiling, clamped to `writer_ceiling` for exports. Its derived-rung
repair census at line 813 includes the direct-writer tail through today, using
already-published base parts; out-of-scope ladder days remain counted as owed.
This implemented census does not prove missing source history was acquired.

| Direct producer | Scope supported by inspected source | Source anchor |
| --- | --- | --- |
| Climate NASA POWER | Latest 400 days, clipped to history floor; recent absence reconsideration is 14 days. | `pipeline/direct/climate/forward.py:98`, `:104`, `:275` |
| Soil ERA5-Land | Latest 400 days, clipped to history floor; recent absence reconsideration is 14 days. | `pipeline/direct/soil/forward.py:100`, `:107`, `:287` |
| Vegetation Sentinel-2 | Latest 400 days clipped to direct ownership floor; absence recheck is 14 days. Historical backfill/parity remain manual. | `pipeline/direct/vegetation/forward.py:103`, `:106`, `:255`; `execution/job_executor_service.py:743` |
| Drought | Latest 60 settled release weeks; absence recheck is eight weeks. | `pipeline/direct/drought/forward.py:93`, `:99`, `:163` |
| Burn severity | Pending days within the explicitly governed release set, bounded per turn; current capture has its separate daily/weekly contract. | `pipeline/direct/burn_severity/forward.py:168`, `:204`; `execution/job_executor_service.py:957` |
| Fire detections | Five settled days; deeper archive acquisition is outside this scheduled window. | `pipeline/direct/fire_detections.py:129`, `:130` |
| Sensors | One rolling NWS poll, at most seven day buckets; upstream retention bounds recovery. | `pipeline/direct/sensors/forward.py:94`, `:441`; `execution/job_executor_service.py:841` |
| Water gauges / weather | Current polls merged into touched publisher-day buckets; these declarations do not own historical gap acquisition. | `pipeline/parquet/water_gauges_forward.py:97`, `:413`, `:428`; `execution/job_executor_service.py:754` |
| Watersheds / evacuation zones / fire perimeters | Current source-version polling cannot reconstruct uncaptured versions. | `execution/job_executor_service.py:813`, `:869`, `:902` |

This is a source-scope reconciliation, not the complete product ownership census.
Older requested soil-wetness, precipitation, dew-point, drought and burn-severity
horizons still need current owners and source receipts. The bounded lookbacks do
not satisfy those full horizons merely because their registered jobs can run.

## Recovery implementation and operational proof are separate

| Contract | Current source / inspected tests | Proof still owed |
| --- | --- | --- |
| Retry | `jobs/lease.py:507` records failure then backoff or dead letter; `tests/test_jobs_lease.py:433` asserts policy backoff. | A current lane's transient failure, retry and terminal publication with exact run/attempt evidence. |
| Restart catch-up | `execution/job_executor_service.py:1153` selects current bucket for `coalesce_latest` and the next owed bucket for `replay_oldest`; tests at `tests/test_job_executor_service.py:1097`, `:1271`, `:1412` cover prior-version resume, lease exclusion and stored execution budget. | Observed restart, retained cursor/definition and completed publication without overlapping writers. |
| Failed checkpoint | `execution/job_executor_service.py:1424` applies clock release below the breaker or operator supersession; tests at `tests/test_job_executor_service.py:2353`, `:2379`, `:2394`, `:2409` cover release, breaker and replay holds. | Current lane/command identity and the applicable release evidence; older incident receipts do not authorize new supersession. |
| Expired lease | `jobs/worker.py:1023` reclaims by definition before claiming; `sql/jobs/reclaim_expired_leases.sql:112` scopes candidates and clears leases into retry/dead letter; tests at `tests/test_jobs_worker.py:794` and `tests/test_jobs_lease.py:569`, `:613` inspect ordering, outcomes and fence preservation. | Current expired-lease recovery and stale-worker fencing observed against the deployed lane, with run/attempt and output receipts. |

No recovery action or database test was executed here. The existing unit seams
use recording/fake sessions; inspection cannot certify real database concurrency
or a deployed process restart. None of the P3/P4 operational gates is closed.

## Latest receipt limits and remaining gates

The [September 11 retrospective](../../../retros/parquet_operational_checkpoints_20260911/README.md)
retains temperature history and current MTBS publication as bounded completed
slices. The [MTBS rollout receipt](../../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md)
records schedule reconciliation, partial recent seasons and a separate older
population; it does not observe future scheduled execution.

The newer [root integrated checks](../../platform_experience_qa_20260911/evidence/root-integrated-checks-20260912.md)
report unavailable frontend dependencies and an environment-limited Python run
with 510 errors. That receipt is not a green full-suite result. Historical
passing receipts do not replace current candidate validation.

Remaining gates: current deployment/activation/lease readback; full-horizon gap
ownership and receipt-backed absences; reconciliation of all required ladders and
availability; observed retry/restart/expired-lease recovery; three scheduled
advances per activated lane; and exact acceptance handoff. They require evidence
outside this local-only audit. This documentation correction closes only the
stale implementation claim and makes the unclosed ownership scopes explicit.
