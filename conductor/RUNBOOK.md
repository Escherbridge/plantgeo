---
type: runbook
reviewed: 2026-09-11
source_revision: fa202230958fb55521963e886eb031be5fc266c4
---

# PlantGeo operational runbook

This is the current entry point for operations and handoffs. The September 11
integration builds on documentation baseline `a7e6b22` and the peer evidence for
deployed `fa20223`. The [integration ledger](tracks/parquet_duckdb_pivot_20260823/evidence/integration-ledger-20260911.md)
identifies accepted local commits, exact reviews and remaining gates. Integration
performed no production checks or mutations. Dates below identify when the cited
evidence was collected; they are not fresh guarantees of runtime state.

Start from the [work registry](tracks.md) and the owning plan. The
[authority order](README.md#authority-order), [release policy](release-governance.md)
and [runtime deployment contract](../docs/deployment.md) govern execution.

## Current checkpoint

| Area | Latest recorded result | What remains open |
| --- | --- | --- |
| Environmental architecture | Governed Parquet is the environmental serving plane; PostgreSQL retains operational and community responsibilities. The September 9 rebuild and preservation are recorded in the [operational retrospective](retros/parquet_operational_checkpoints_20260911/README.md). | The rebuild did not prove that every legacy environmental writer, support lookup or schema dependency was retired. Follow the [database-boundary audit](tracks/environmental_postgres_retirement_20260904/evidence/active-lane-database-boundary-20260910.md). |
| Temperature history | September 10 [publication evidence](tracks/environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md#final-temperature-publication-verified) binds all three temperature lanes to 1,560 complete days each, April 30, 2022–August 6, 2026, at all four rungs. [Machine receipts](tracks/environmental_postgres_retirement_20260904/evidence/temperature-bootstrap-receipts-20260910.json) preserve the exact generations. | The later source ceiling does not fill the forward interval. Verify fresh selected-day reads and sustained forward publication under the current deployed revision. |
| MTBS current snapshot | The [September 11 rollout](tracks/environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md) records deployed `fa20223`, 747 fires across the 2018–2026 replacement population, four verified rungs and public/UI acceptance. The later [runtime checkpoint](tracks/environmental_postgres_retirement_20260904/evidence/runtime-and-retirement-readback-20260911.md) records one successful scheduled execution. | One execution is not three new publication advances. Seasons 2023–2026 remain partial. The separately preserved 3,077 fires from 1984–2017 need catalogue composition, collision policy, admission and selected-day acceptance. |
| Soil temporal reader | The [September 10 repair checkpoint](tracks/environmental_postgres_retirement_20260904/evidence/repair-preparation-20260910.md#soil-reader-deployed-and-checked-against-source) records deployed direct-writer lineage support and six live source comparisons. | Static SoilGrids admission and dark soil-survey restoration have separate contracts; neither is completed by this daily-soil reader repair. |
| Weather observations and forecast | The current `weather-observations` product is a sampled current-condition estimate lane with forecast horizon zero. The April 28, 2025 screenshot reports incomplete catalogue coverage; its spaced squares match aggregate support bins around sparse samples, not a continuous forecast grid. | Trace the screenshot's exact request, response and painted frame, then reconcile source/product identity, catalogue intervals and floor/lag before claiming history. A separate [forecast plane](tracks/weather_forecast_parquet_lane_20260911/plan.md) and [forecast experience](tracks/weather_forecast_experience_20260911/plan.md) own real model-run/valid-time fields, wind vectors and location forecasts. |
| Ingestion cutoff | September 11 [gapless evidence](tracks/gapless_parquet_publication_20260901/evidence/generic-recovery-20260911.md) and the [retirement runtime checkpoint](tracks/environmental_postgres_retirement_20260904/evidence/runtime-and-retirement-readback-20260911.md) verify the effective eight-lane cutoff and 20 active lanes on captured `fa20223`. Six soil products each have three scheduled backlog advances. | Full-product burn-in and runtime recovery remain open. The dated zero-live-lease read does not establish external/manual quiescence or authorize a correction; refresh ownership and state pins before any mutation. |
| Platform experience QA | The active [QA and orchestration track](tracks/platform_experience_qa_20260911/plan.md) now owns the cross-feature local browser matrix, task/candidate reconciliation and independent UX verdict. It permits disposable local roles and UI-authored synthetic submissions, with no Railway or production mutation. | Bind all live tasks, bases, commits, trees and receipts; inventory every surfaced feature; reconcile incoming implementation commits; then execute desktop/mobile, role, accessibility, selected-day, cache, canvas and agent/MCP journeys on one frozen integrated tree. Local synthetic intervention evidence cannot satisfy the community track's human-contributor requirement. |
| Acceptance | Completed implementation and bounded publication slices have retained receipts. | [Production acceptance](tracks/parquet_production_acceptance_20260901/plan.md) still owns the cross-layer browser, conservation, freshness, burn-in and release verdict. No project-wide GREEN verdict follows from this cleanup. |

The previously prominent claims that availability had not flipped, temperature
history still needed building, and no retirement had happened are archived dated
statements. They must not restart completed work. Conversely, a prior rebuild or
publication must not erase the unresolved reader, history and runtime gates.

## Outstanding work by owning track

| Work | Start here | Required next evidence |
| --- | --- | --- |
| Signal coordinates and sensor absence correction | [Environmental retirement plan](tracks/environmental_postgres_retirement_20260904/plan.md) and [reviewed product handoff](tracks/environmental_postgres_retirement_20260904/evidence/repair-handoff-20260911.md) | Signal's 222-day reconstruction, physical-correction guards and sensor's 5,935 positive rows have local review and custody evidence. Fresh ownership/state checks, authorized correction, provenance-bound availability and deployed readback remain open. Incomplete sensor source population remains explicit. |
| Historical gaps, provider deferrals and scheduler ownership | [Gapless publication plan](tracks/gapless_parquet_publication_20260901/plan.md) and [scheduled repair scope](tracks/environmental_postgres_retirement_20260904/evidence/active-lane-database-boundary-20260910.md#scheduled-gap-repair-scope) | Successful bounded lookback schedules do not establish full-horizon repair. FIRMS/water archives, legacy promotion paths, source geometry refusals and expired sensor history retain explicit gaps. |
| Slider and agent reads | [Reader cutover plan](tracks/parquet_reader_cutover_acceptance_20260901/plan.md) and [local reader handoff](tracks/parquet_reader_cutover_acceptance_20260901/evidence/reader-local-handoff-20260911.md) | Selected-day binding, terminal evidence, bounded neighbours and slider captions passed local intake. Controlled cold/warm traces, missing product/rung/history samples and deployed browser/agent acceptance remain open; unsupported Python product semantics remain explicit refusals. |
| Low-zoom geometry and final browser evidence | [Multiscale plan](tracks/multiscale_polygon_surface_20260901/plan.md) and [acceptance plan](tracks/parquet_production_acceptance_20260901/plan.md) | Conservation and pixel continuity, appropriate support geometry, cold/warm requests and schedule burn-in. Soil-survey must return with generalized low-zoom rungs. |
| Saved static soil assets | [Soil restoration preparation](tracks/environmental_postgres_retirement_20260904/evidence/soil-restoration-preparation-20260911.md) | All twelve local asset hashes and native-pixel reads are verified. Immutable descriptor admission, full remote readback, pointer ownership and the admitted reader remain open. Soil-survey separately needs complete source authority and a driver sized for both full roots before all-rung restoration. |
| Offline construction and availability cost | [Offline export plan](tracks/offline_export_service_20260908/plan.md), [accepted evidence](tracks/offline_export_service_20260908/evidence/README.md) and [current verification mechanics](../services/agri-data-service/src/agri_data_service/pipeline/parquet/AGENTS.md#availability-verification-concurrency) | The eight-lane audit and 13,514-day humidity candidate have local review and complete custody. Historical stage/build/upload timings remain unavailable. Exact authorized upload/promotion, indexed-history verification and deployed acceptance remain open; verified temperature history is not rebuilt. |
| Cross-feature UX QA and task lifecycle | [Platform experience QA](tracks/platform_experience_qa_20260911/plan.md) | Check existing ownership before spawning work; bind every task and immutable candidate; return failures to their owning tracks; close and archive tasks only after integration and independent verification. Preserve blocked or unresolved owners and their evidence. |
| Weather gap, meaning and forecast experience | [Gapless publication](tracks/gapless_parquet_publication_20260901/plan.md), [reader acceptance](tracks/parquet_reader_cutover_acceptance_20260901/plan.md), [forecast plane](tracks/weather_forecast_parquet_lane_20260911/plan.md) and [forecast experience](tracks/weather_forecast_experience_20260911/plan.md) | Reconcile the April 28 screenshot with the catalogue and exact reader response, clear catalogue-unavailable selected-day frames, relabel the sampled-estimate product, then admit and publish a distinct run/valid-time forecast before rendering continuous fields, wind and hourly/daily cards. |
| Wind & Weather identity and missing days | [Audit handoff](tracks/parquet_duckdb_pivot_20260823/evidence/wind-weather-handoff-20260911.md) | Keep sampled estimates/observations at forecast horizon 0. Reconcile identity, uncited floor/lag and the recorded 2021-11-27..2026-07-31 gap; never substitute reanalysis under the current-poll identity. Retain the 2025-04-28 stale-response regression, date agreement and truthful sparse support. |

Evidence cutoff is the dated September 11 peer packets linked above. The older
parent plans retain historical acceptance obligations and exact receipts. Local
integration does not promote those observations into a current production verdict;
the [release packet](tracks/parquet_duckdb_pivot_20260823/evidence/release-packet-20260911.md)
remains HOLD until its exact combined and deployed-tree gates pass.

## Operator sequence

1. Select one current gate, read its latest dated receipt and identify the exact
   lane, day window, deployed revision and source/artifact pins. Check existing
   work and task ownership before starting another operation on the same lane.
2. Record a fresh, bounded preflight for the affected surface. A status read,
   physical partition census, availability generation, and public reader answer
   establish different facts. Cold and warm capability responses should be
   measured separately; one cold timeout is insufficient to diagnose an outage.
3. Prepare with the maintained command and directory documentation. Use the
   [script catalogue](../services/agri-data-service/scripts/AGENTS.md),
   [publication contract](../services/agri-data-service/src/agri_data_service/pipeline/parquet/AGENTS.md)
   and [executor control contract](../services/agri-data-service/src/agri_data_service/execution/AGENTS.md).
   Keep prepared candidates, uploaded evidence, completed physical ladders,
   published availability and verified serving distinct in the receipt.
4. Apply the operation only within its recorded scope and authorization. Existing
   authorization remains valid for its exact action; archival prose supplies no
   new mutation authority. Use supported lane controls, publication locks and
   compare-and-swap. Record before/after identities and the rollback reference.
5. Verify the changed boundary independently. After a publication, validate every
   required rung and normal availability readback, then the selected-day public
   reader or browser surface. Record limitations and the next scheduled proof.
6. Update the owning plan, metadata and registry together. Add a dated evidence
   receipt and retrospective for a completed slice; retain the parent track while
   its wider gates remain open. Follow the [session workflow](workflow.md) when
   pruning handoffs or archiving completed tasks.

## Recovery and rollback

- Preserve the original input, terminal receipts and published pointer before a
  correction. Resume exact supported requests; never replace a failed or missing
  history with an invented absence or direct ledger edit.
- Availability publication performs bounded parallel verification under its
  publication barrier. A missing final marker or pointer during a large request
  does not by itself establish a hang. The [September 10 request budget](tracks/environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md#publication-request-budget-and-pending-runtime-verification)
  accounts for 53,046 GETs per temperature lane and records a bounded successful
  retry. The September 8 serial-loop diagnosis is historical; do not move physical
  verification outside the lock using that old proposal.
- For scheduler rollback, disable the affected lane with the supported control,
  retain its data and audit history, and verify old work has settled. The
  [scheduler-owner directive](release-governance.md#scheduler-owner-directive--2026-09-02)
  does not permit restoring Railway cron or an old writer.
- For schema/deployment rollback, use the matching migration ledger and readiness
  pin. A bare baseline revert can fail readiness even when the migrator skips it.
  Keep the [September rebuild evidence](retros/parquet_operational_checkpoints_20260911/README.md)
  and archived preservation object references available. Restart affected serving
  processes after schema changes according to the deployment procedure.
- If a proof fails, record the exact request, revision, source identities,
  observed terminal state and named owning gate. Escalate that bounded blocker to
  the track owner; do not erase evidence to make a lane report success.

## Validation and evidence retention

Apply the complete change batch before one final sweep. The current
[testing contract](../docs/testing.md) defines scoped local checks and full release
checks; a documentation-only change skips runtime tests. A scoped pass must not
produce a full-service Python receipt or be described as release acceptance.
Retain the no-database skip disclosure when integration settings are absent.
Independent review remains a separate pass from authoring.

For Python commands, preserve the installed tool environment with
`UV_NO_SYNC=1 uv run --no-sync ...`. Receipt-producing release checks must follow
the service's receipt protocol and verify the tracked source domain. Do not copy
old test counts or a previous receipt into the current change's validation claim.

Files under `.omc/research/` linked from evidence can be the only retained source
capture or recovery archive. They are not disposable session state. Preserve
referenced inputs and receipts before pruning wrappers, stale status markers or
completed handoffs. A recorded hash pins an artifact; it does not embed that
artifact in a tracked Markdown summary.

## Historical handoffs

- [September 1–9 runbook archive](RUNBOOK-archive-2026-09.md): the complete former
  runbook body, including decisions, corrections, incident details and mutation
  history, preserved during this cleanup.
- [August runbook archive](RUNBOOK-archive-2026-08.md): older numbered sections,
  including §0.23 architecture pivot, §0.24 stream plan and §0.41 analytical
  product discussion. References to those sections in older documents resolve here.
- [Completed cutover slices](retros/parquet_cutover_completed_slices_20260910/README.md)
  and [operational checkpoints](retros/parquet_operational_checkpoints_20260911/README.md):
  completed scope and outstanding successor gates.
- [Historical warehouse ingestion](../docs/historical-backfill-runbook.md): retained
  evidence of the retired local-warehouse/rolling-projection design.

Keep this entry bounded. New operational detail belongs in a dated receipt and
the owning track; update the checkpoint and links here rather than appending a
second current handoff.
