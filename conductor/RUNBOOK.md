---
type: runbook
reviewed: 2026-09-12
source_revision: 06a413a
---

# PlantGeo operational runbook

This is the current entry point for operations and handoffs. Reconciled on
September 12 against the root QA checkout `d81cb3b`, committed evidence through
September 12, and the saved September 11 MTBS rollout receipt. This maintenance
pass performed no production checks or mutations. Dates below identify when the
cited evidence was collected; they are not fresh guarantees of runtime state.

Start from the [work registry](tracks.md) and the owning plan. The
[authority order](README.md#authority-order), [release policy](release-governance.md)
and [runtime deployment contract](../docs/deployment.md) govern execution.

## Current checkpoint

| Area | Latest recorded result | What remains open |
| --- | --- | --- |
| Environmental architecture | Governed Parquet is the environmental serving plane; PostgreSQL retains operational and community responsibilities. The September 9 rebuild and preservation are recorded in the [operational retrospective](retros/parquet_operational_checkpoints_20260911/README.md). | The rebuild did not prove that every legacy environmental writer, support lookup or schema dependency was retired. Follow the [database-boundary audit](tracks/environmental_postgres_retirement_20260904/evidence/active-lane-database-boundary-20260910.md). |
| Temperature history | September 10 [publication evidence](tracks/environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md#final-temperature-publication-verified) binds all three temperature lanes to 1,560 complete days each, April 30, 2022–August 6, 2026, at all four rungs. [Machine receipts](tracks/environmental_postgres_retirement_20260904/evidence/temperature-bootstrap-receipts-20260910.json) preserve the exact generations. | The later source ceiling does not fill the forward interval. Verify fresh selected-day reads and sustained forward publication under the current deployed revision. |
| MTBS current snapshot | The [September 11 rollout](tracks/environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md) records deployed `fa20223`, 747 fires across the 2018–2026 replacement population, four verified rungs, public/UI acceptance and restored daily publication scheduling with weekly capture. Seasons 2023–2026 remain explicitly incomplete. | Subsequent scheduled execution was not yet observed in that receipt. Pre-2018 recovery and broader historical completeness remain separate work. |
| Soil temporal reader | The [September 10 repair checkpoint](tracks/environmental_postgres_retirement_20260904/evidence/repair-preparation-20260910.md#soil-reader-deployed-and-checked-against-source) records deployed direct-writer lineage support and six live source comparisons. | Static SoilGrids admission and dark soil-survey restoration have separate contracts; neither is completed by this daily-soil reader repair. |
| Weather observations and forecast | The historical-weather presentation is **conditionally approved locally** for the fixed desktop unavailable-state scope in the [weather approval receipt](tracks/platform_experience_qa_20260911/evidence/weather-approval-20260912.md), with the coordinator decision recorded in the [bounded approval decision](tracks/platform_experience_qa_20260911/evidence/weather-approval-decision-20260912.md). The owner implementation is `8e53b41`; the style-readiness recovery is integrated at `5bbe3dc`, the synthetic ready-to-outage regression at `37eb50a`, and the independently reviewed stronger-wind collision priority at `e54d091`. The [integration receipt](tracks/platform_experience_qa_20260911/evidence/integrated-weather-botanical-candidate-2026-09-11.md) and [root browser evidence](tracks/platform_experience_qa_20260911/evidence/browser-weather-20260911.md) retain the exact checks. Wind & Weather is categorized under Climate, and Climate & Weather History now has a readable report card plus explicit retry/unavailable handling with no stale fallback frame. The weather layer recovers when a dynamically loaded component misses the initial basemap `style.load` event, stronger wind labels receive placement priority while collision avoidance remains enabled, and the report transition from ready data to a typed outage is covered by a fixture regression. The report also exposes an accessible retry action for transport errors and typed upstream outages, with a disabled `Retrying…` state while refetching. The local data-boundary check, type-check, lint and full frontend test run now pass after restoring lockfile dependencies locally (150 files passed, 2 skipped; 2,233 tests passed, 13 skipped). | The governed Parquet reader/data service was unavailable during browser acceptance, so populated raw/aggregate labels, dense wind-label behavior on live data, precipitation hover and real selected-day transitions remain unproven; narrow-mobile reflow/touch is also pending. A separate [forecast plane](tracks/weather_forecast_parquet_lane_20260911/plan.md) and [forecast experience](tracks/weather_forecast_experience_20260911/plan.md) still own model-run/valid-time fields, wind vectors and location forecasts. |
| Botanical species information | The bounded transitional [authoring lookup](tracks/botanical_species_profile_lookup_20260911/evidence/transitional-authoring-lookup-2026-09-11.md) is integrated in root `3e35971`. It is read-only, exact-UUID bound, explicit about per-field provenance and missingness, exposes approved companion relationships, and is wired to the API, agent graph and MCP surface. The [current census audit](tracks/botanical_species_profile_lookup_20260911/evidence/source-admission-census-20260912.md) and [latest restart reconciliation](tracks/platform_experience_qa_20260911/evidence/session-restart-20260912-latest.md) retain the later custody and target evidence. The post-context web-pass UUID exposure was closed locally in `9d895dc` by omitting `species_information` after the warehouse context exits; focused graph, Ruff and mypy checks pass. | A dated historical metadata observation identifies the scoped Railway project, production service and environment, but it was not revalidated and supplies no authorized DSN, verified read-only role or database evidence; `pgt` and local databases remain out of scope. The lookup is not a published release and cannot rank species, infer occurrence, recommend planting, or make fuel/fire claims. The final immutable profile, bounded census, independent source admission and release-pinned API/agent/MCP parity remain open. |
| PNW Herbaria admission | The [metadata-only refresh receipt](tracks/pnw_herbaria_source_admission_20260911/evidence/metadata-refresh-20260912.md) is integrated at `57ef4fc`; seven bounded HTTP metadata responses and their hashes are retained. | WTU still lacks a standalone release-bound EML/field map and UBC lacks an applicable coordinate-withholding statement bound to its distribution. Both admissions remain blocked; no archive, specimen, image or media resource was acquired. Resolve rights, coordinate policy, immutable release identity and quarantine controls before any archive pilot. |
| Multiscale polygon surfaces | The bounded [scalar-label slice](tracks/multiscale_polygon_surface_20260901/evidence/scalar-labels-20260912/README.md) is integrated at `ac4ce70`. Climate and soil cells now show unit-bearing numeric labels with aggregate qualifiers while preserving missingness, legends, opacity, style reloads and isoband semantics. | The 26 captures are synthetic mechanics evidence. Live selected-day reads, dense-basemap readability, hover registration, request-to-paint budgets, production performance and full mobile journeys remain open; vegetation and soil-survey renderer contracts were documented rather than widened. |
| Ingestion cutoff | September 10 evidence records an eight-lane pause configuration accepted with deployments skipped. | Its recorded status is `configured_pending_deployment`. A later successful code deployment alone does not prove the effective allowlist or that old invocations ended. Read current configuration, definitions, leases and attempts before declaring the cutoff effective. |
| Platform experience QA | The active [QA and orchestration track](tracks/platform_experience_qa_20260911/plan.md) owns the cross-feature local browser matrix, task/candidate reconciliation and independent UX verdict. The weather/botanical candidate, Herbaria metadata packet, scalar-label candidate, stronger-wind collision candidate and root browser receipt are recorded; the [root integrated verification receipt](tracks/platform_experience_qa_20260911/evidence/root-integrated-checks-20260912.md) records the remaining environment-limited gates. Completed owner sessions were archived with custody retained. The [live restart receipt](tracks/platform_experience_qa_20260911/evidence/session-restart-20260912-live.md), [restart continuation](tracks/platform_experience_qa_20260911/evidence/session-restart-20260912-continuation.md), and [latest restart reconciliation](tracks/platform_experience_qa_20260911/evidence/session-restart-20260912-latest.md) record the superseded forecast archival, the botanical owner restarts and clean stops, the unresolved ingestion owner being kept open, the P2 UUID-binding finding, and the visual lane integration. It permits disposable local roles and UI-authored synthetic submissions, with no Railway or production mutation. | Live populated-data, narrow-mobile/touch, role/accessibility, selected-day, cache, canvas and agent/MCP journeys remain to be executed on a service-backed frozen tree. Local synthetic intervention evidence cannot satisfy the community track's human-contributor requirement. |
| Acceptance | Completed implementation and bounded publication slices have retained receipts. | [Production acceptance](tracks/parquet_production_acceptance_20260901/plan.md) still owns the cross-layer browser, conservation, freshness, burn-in and release verdict. No project-wide GREEN verdict follows from this cleanup. |

The previously prominent claims that availability had not flipped, temperature
history still needed building, and no retirement had happened are archived dated
statements. They must not restart completed work. Conversely, a prior rebuild or
publication must not erase the unresolved reader, history and runtime gates.

The September 12 botanical agent-parity candidate `0cd9430` is locally
integrated in the coordinator checkout after independent review. It keeps the
caller-bound species context active through the web pass and adds mismatched
and omitted-UUID regressions; it is not a published profile release and does
not close source, census, immutable-publication or production gates.

## Outstanding work by owning track

| Work | Start here | Required next evidence |
| --- | --- | --- |
| Signal coordinates and sensor absence correction | [Environmental retirement plan](tracks/environmental_postgres_retirement_20260904/plan.md) and [prepared recovery evidence](tracks/environmental_postgres_retirement_20260904/evidence/repair-preparation-20260910.md) | Signal's 222-day candidates and sensor positive candidates are preparation, not publication. Preserve exact source/artifact hashes; perform fresh ownership and state checks through the reviewed correction path before applying. |
| Historical gaps, provider deferrals and scheduler ownership | [Gapless publication plan](tracks/gapless_parquet_publication_20260901/plan.md) and [scheduled repair scope](tracks/environmental_postgres_retirement_20260904/evidence/active-lane-database-boundary-20260910.md#scheduled-gap-repair-scope) | Successful bounded lookback schedules do not establish full-horizon repair. FIRMS/water archives, legacy promotion paths, source geometry refusals and expired sensor history retain explicit gaps. |
| Slider and agent reads | [Reader cutover plan](tracks/parquet_reader_cutover_acceptance_20260901/plan.md) | Verify the UI-selected day, supported zoom, bounded viewport and explicit terminal/refusal states. Keep environmental PostgreSQL fallbacks retired. |
| Low-zoom geometry and final browser evidence | [Multiscale plan](tracks/multiscale_polygon_surface_20260901/plan.md) and [acceptance plan](tracks/parquet_production_acceptance_20260901/plan.md) | Conservation and pixel continuity, appropriate support geometry, cold/warm requests and schedule burn-in. Soil-survey must return with generalized low-zoom rungs. |
| Cross-feature UX QA and task lifecycle | [Platform experience QA](tracks/platform_experience_qa_20260911/plan.md) | Check existing ownership before spawning work; bind every task and immutable candidate; return failures to their owning tracks; close and archive tasks only after integration and independent verification. Preserve blocked or unresolved owners and their evidence. |
| Weather gap, meaning and forecast experience | [Platform browser evidence](tracks/platform_experience_qa_20260911/evidence/browser-weather-20260911.md), [Gapless publication](tracks/gapless_parquet_publication_20260901/plan.md), [reader acceptance](tracks/parquet_reader_cutover_acceptance_20260901/plan.md), [forecast plane](tracks/weather_forecast_parquet_lane_20260911/plan.md) and [forecast experience](tracks/weather_forecast_experience_20260911/plan.md) | The local visual repair is approved for the unavailable-state path. Reconcile the April 28 screenshot with the catalogue and exact reader response, then re-run populated desktop and mobile evidence when the governed reader is available. Admit and publish a distinct run/valid-time forecast before rendering continuous fields, wind and hourly/daily cards. |
| Botanical profile and species-specific agent information | [Botanical profile plan](tracks/botanical_species_profile_lookup_20260911/plan.md), [transitional lookup receipt](tracks/botanical_species_profile_lookup_20260911/evidence/transitional-authoring-lookup-2026-09-11.md) and [source-admission census](tracks/botanical_species_profile_lookup_20260911/evidence/source-admission-census-20260912.md) | Keep the current API/agent/MCP read exact-UUID and fail-closed. Obtain a separately authorized, securely supplied read-only Railway DSN matched to the recorded target and run only the bounded authoring census. Independently admit non-Herbaria taxonomy, growth, water/oil/fuel and agricultural-role source fields with provenance before immutable publication and release-pinned agent/API acceptance. |
| PNW Herbaria source admission | [Herbaria admission plan](tracks/pnw_herbaria_source_admission_20260911/plan.md) and [metadata refresh](tracks/pnw_herbaria_source_admission_20260911/evidence/metadata-refresh-20260912.md) | Resolve WTU's exact-release EML/field map and stable release identity, bind UBC coordinate-withholding to the institutional distribution, and name quarantine/retention/withdrawal controls. Only after those gates pass may a bounded data-only archive pilot begin; keep images and publication excluded. |
| PNW land context and public contact planning | [Reference-plane plan](tracks/pnw_land_context_reference_plane_20260911/plan.md) and [experience plan](tracks/pnw_land_contact_experience_20260911/plan.md) | Resolve Idaho utility/state-land feeds, Oregon's current utility feed, county parcel fields/rights and every exact distribution's reuse terms before acquisition. Keep private owner names excluded; preserve boundaries, offices, advisers and contact-process routes as distinct evidence. Registration is planning only and authorizes no ingestion, outreach or implementation. |
| Saved static soil assets | [SoilGrids admission preparation](tracks/environmental_postgres_retirement_20260904/evidence/soil-static-admission-preparation-20260910.md) | Reviewed immutable admission and bounded point-reader implementation. Sampled remote byte ranges do not establish full remote object identity. |
| Offline construction and availability cost | [Offline export plan](tracks/offline_export_service_20260908/plan.md), [historical incident runbook](tracks/offline_export_service_20260908/RUNBOOK.md) and [current verification mechanics](../services/agri-data-service/src/agri_data_service/pipeline/parquet/AGENTS.md#availability-verification-concurrency) | Retain phase review/performance evidence and deferred history. The discarded standalone service is not the production builder. |

Evidence cutoff for this table is September 10; the MTBS rollout in the checkpoint
table is September 11. The older parent plans also retain September 1–9 acceptance
obligations. None of these dates records a new production probe by this cleanup.

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

The September 12 weather QA continuation corrected a stale-placeholder path in
the historical report and map: a mismatched prior-day result is now withheld
from both surfaces while the selected day loads. The focused report/map
regression, integrated frontend sweep and independent review passed; the
candidate remains locally uncommitted because repository ref locking is denied
in this sandbox. The bounded weather approval remains in force; this local
candidate does not authorize populated-data release, forecast implementation,
Railway, production, database, writer, deployment or push operations.

The September 12 follow-up also made map wind labels explicit and font-safe by
using ASCII meteorological `from <cardinal> <speed>` wording. Independent review
and the final integrated sweep passed (150 frontend files passed, 2 skipped;
2,240 tests passed, 13 skipped); the detailed receipt is
[root-integrated-checks-20260912-weather-labels.md](tracks/platform_experience_qa_20260911/evidence/root-integrated-checks-20260912-weather-labels.md).
This remains a local presentation approval; live populated-data, mobile,
accessibility, hover and separate forecast gates remain open.

The older `root-integrated-checks-20260912.md` link and 2,233-test count in the
table are retained as dated history. The current weather-label verification is
the [2,240-test receipt](tracks/platform_experience_qa_20260911/evidence/root-integrated-checks-20260912-weather-labels.md),
which is the receipt to use for the present local presentation candidate.
