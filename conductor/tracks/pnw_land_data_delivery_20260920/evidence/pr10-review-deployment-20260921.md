---
type: evidence
track: pnw_land_data_delivery_20260920
recorded_at: 2026-09-21
status: deployed_api_verified_pr9_followup_pending
---

# PR #10 review and deployment acceptance

[PR #10](https://github.com/Escherbridge/plantgeo/pull/10) was reviewed from initial head
`e8270fab` against base `abf2f791`. The current owner request authorizes review, track updates,
merging and deployment monitoring. [PR #9](https://github.com/Escherbridge/plantgeo/pull/9)
was already merged at `2026-09-13T16:46:48Z`.

This record covers the corrective review batch. The
[initial production delivery evidence](production-delivery-20260921.md) and its
[machine receipt](production-receipt.json) remain historical publication evidence; they are
not receipts for deployment of this branch or verification of the new fixes.

## Findings and independent review

| Finding | Correction | Review disposition |
| --- | --- | --- |
| P1: Crop maintenance accepted an indexed completion marker after physical parts disappeared or changed. | Match the completion key/hash, physical row and part totals, and exact part key/hash set against the published availability receipts for every required rung. | Independently reviewed; fixed. |
| P1: BLM reconciliation accepted a partly deleted base partition while its completion marker and surviving source identity remained intact. | Match physical row/part totals and source-manifest identity on each rung; match all supplied part paths, hashes, row counts and byte counts. Missing parts select replay. | Independently reviewed; fixed. |
| P2: The contact panel discarded loading, lookup failures, bounded refusals and nonmatched coverage, presenting them as an empty-office result. | Preserve the selected boundary and show contact-specific status and source gap explanations; discard stale contacts on query failure. | Independently reviewed; fixed. |
| P1: With absent result metadata, the panel's Zustand selector returned a new empty array on every read, causing a React update loop. | Subscribe to the stored metadata reference and derive optional defaults outside the selector. Keep absent metadata in the real-store regression fixture. | Exposed by the integrated sweep; correction independently reviewed with no blocking finding. |

A separate reviewer who authored none of the corrective source or tests approved the final
source/test batch with no blocking findings. Review traced the existing object-store receipt,
availability-rung and repair contracts. The parent separately verified that a failed ladder
check reaches an actual export under the existing publication lock.
After the first sweep exposed the render loop, the same independent reviewer also approved
the stable-selector correction and native Vitest assertions. These preserve the real missing-
metadata fixture and require no new matcher extension or changes to the shared test harness.

BLM base completion markers currently use the shared v1 contract: their row/part totals and
source identity can be verified, but they contain no per-part hashes. Supplied v2 receipts are
matched in full. This compatibility limit remains explicit; the review does not claim byte
verification for a v1 base marker. Unrelated storage failures propagate rather than becoming
repair success. Crop verification reads the bounded admitted editions' physical parts.

These SHA-256 anchors identify reviewed local working-copy bytes before Git newline
normalization. They are not commit-blob hashes; the final revision will be recorded separately.

| Source | SHA-256 |
| --- | --- |
| `pipeline/direct/crop_cover/forward.py` | `35fbe919b79506e948839260667198067f74722845a9ea62c120dbe3ad8f6d6e` |
| `pipeline/direct/land_context/forward.py` | `ccbb800a64c5c94f1f43d87ec7fe4df589c77fd0ead97abc618dd7acf732870d` |
| `src/components/panels/land-context/LandContextPanelHost.tsx` | `045f62b6e7bf154b6a7d128e69d356cfbb282d8135dc1f3a6942ac472d173cbf` |
| `src/components/panels/land-context/LandContextPanel.tsx` | `a9563235c9623ca8f129348fcf7219956ec4df111d618c105f7d9d23b4cfe5c9` |
| `src/__tests__/components/LandContextPanelHost.test.tsx` | `ca4a76257e8de6fc0512e2ea98dc143ddce2c5942e1ecaf7240c3ba17221a423` |

## Executable verification

The batch adds 13 Python regression cases and five UI regression cases. The Python cases use
real object-store writes/readback for partial or total loss, extra parts, changed Parquet bytes
under unchanged markers, healthy no-op behavior, source identity and storage-error propagation.
The UI cases verify loading, transport failure, typed coverage gaps and budget refusal while
retaining boundary evidence. The five real-store cases also retain absent metadata, exposing
the render-loop defect instead of masking it with a fabricated successful lookup.

The first integrated sweep produced the following results:

| Gate | First-sweep result | Follow-up |
| --- | --- | --- |
| Frontend data boundary and lint | Passed. | Full lint passed again after the corrective changes; the earlier full boundary result remains applicable. |
| TypeScript | Failed on unsupported test matcher types in the new UI test. | Replaced matcher extensions with native Vitest assertions; full type-check passed. |
| Full frontend tests | 229 test files and 3,072 tests passed; all five new UI cases failed on the metadata selector's React update loop. | Stable-selector fix independently approved; the affected `LandContextPanelHost` test file passed all five cases. |
| Python formatting, lint and mypy | Passed. | All three gates passed again in the isolated full rerun after process-only environment correction. |
| Full Python tests | 4,785 passed, 90 skipped, one expected failure and 17 crop failures. The crop failures used inherited PostgreSQL 17 `PROJ_LIB`/`GDAL_DATA` paths incompatible with the locked geospatial environment. | The isolated full rerun passed after clearing those inherited variables for the verification process only. Its runner did not expose an aggregate pytest count, so no final count is asserted. |

Local frontend evidence consists of the full sweep followed by passing affected correction
tests, full type-check and full lint. Separately, the Railway build of merge commit `fcadc3536`
passed full boundary/type/lint gates, all 230 Vitest files with 3,077 tests, and 12 Node tests.
The recovery frontend build at `85c4b8f4` independently passed the same full 230-file/3,077-test
Vitest suite and 12 Node tests. Build captures remain locally in
`.omc/research/railway-main-gates.json` and `.omc/research/railway-recovery-gates.json`.

The final isolated Python sweep passed formatting (0.26 s), lint (0.17 s), mypy (24.77 s) and
pytest (152.89 s). The Docker-compatible quality-receipt verifier also passed. The receipt binds
928 files in validation snapshot index tree `95938f7d539c39f8290c0c99ce52f92b9c9bad0a`:

- Digest: `sha256:6f0a9a7cd40b9f3f693dfd89e86fc75d453bb8a161e831848c3bea86ae2e3db1`.
- Generated: `2026-09-21T04:47:22.101165Z`.

Independent review and the quality gates are complete. The reviewed corrective commit is
`0a9ecabebf9b53a100ad1ef8437a4fc121a479e6`. PR #10 merged at `2026-09-21T04:53:52Z` as
`fcadc3536cb46eae58c37c0e1442733249f2c43e`. No deployment acceptance follows from the local
verification results alone.

## Railway deployment observations

Project: `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`; environment:
`b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. Initial observations after merge bind the following
deployments to merge commit `fcadc3536cb46eae58c37c0e1442733249f2c43e`:

| Service | Deployment ID | Observed state |
| --- | --- | --- |
| Frontend/main | `033210c3-3ccb-4d85-8515-715a77a1d2b4` | SUCCESS |
| Parquet API | `4e21b2ae-2cc2-4f48-a3fb-595fcae1a3c3` | FAILED during startup after a successful build |
| Job executor | `5d678762-3453-4d6c-9a16-86d49ac2a2b9` | SUCCESS |
| Martin | `34835837-187c-4e73-b0a7-6c9b0c4fc9d5` | SUCCESS |
| ML | No new deployment | SKIPPED; unchanged watched scope |

The Parquet API startup failed while importing Rasterio because `libexpat.so.1` was missing
from the runtime image. Railway retained the previous healthy API deployment. The independently
reviewed runtime dependency correction shipped in `85c4b8f451355bfc2fc5aeb3073635c86de2e198`.
All four affected services then reached SUCCESS at that exact commit:

| Service | Recovery deployment ID | State |
| --- | --- | --- |
| Frontend/main | `e7cf4a41-d080-488a-99bd-2cbae5737cb5` | SUCCESS |
| Parquet API | `5bb7c545-06c9-474a-a969-d596bc173043` | SUCCESS |
| Job executor | `d56306f5-f0ac-4521-9740-540e31ee56a4` | SUCCESS |
| Martin | `2801f975-7eca-4982-8cf9-2bccaea1603a` | SUCCESS |

Runtime SSH verified commit `85c4b8f4`, imported Rasterio 1.5.1, and successfully transformed
EPSG:4326 coordinates into EPSG:5070. The Python quality receipt above remains unchanged and verified.

## Production API readback

The [bounded public probe summary](production-api-smoke-20260921.json), recorded at
`2026-09-21T05:11:17.720109+00:00`, retains counts and statuses without geometry payloads.
All 13 canonical-domain probes returned HTTP 200 using
`User-Agent: PlantGeo-Deployment-Verification/1.0`. App readiness reported configuration,
database and Redis healthy; data-service readiness passed all five checks under `published_reader`.

| Published product | Exact served release | Returned rows/features | Truncated |
| --- | --- | ---: | --- |
| Crop 2022 | 2023-01-30 | 391 | false |
| Crop 2023 | 2024-01-31 | 391 | false |
| Crop 2024 | 2025-02-27 | 391 | false |
| Crop 2025 | 2026-02-27 | 393 | false |
| BLM boundaries | 2026-09-21 | 3 | false |
| BLM offices | 2026-09-21 | 34 | false |
| BLM inquiry records | 2026-09-21 | 34 | false |

Availability exposes the four exact crop releases and BLM publication. Parcels, electric
territories and state-managed families return explicit unavailable reasons. The office lookup
returned one matched result with the qualification that overlap establishes neither program
responsibility nor permission. The initial boundary selection returned `unknown_coverage`.
A separate positive probe used `ST_PointOnSurface` on published Oregon geometry to choose
`[-120.08706734793398, 43.16310152889562]`. `resolveBoundaryInArea` at zoom 13 for
`[-120.088, 43.162, -120.086, 43.164]` returned HTTP 200, `status: ok`, two matched records
and no unresolved gaps. The public summary retains both outcomes; raw positive geometry stays
outside Git in `.omc/research/production-probes/blm-positive.json`. These are API results,
not live browser rendering evidence.

## Retrospective PR #9 follow-up

PR #9 was already merged on September 13. Its focused retrospective review found an inherited
P2: `LandContextLayer` gated source creation, result updates and toggles on all-source
`isStyleLoaded()`. Unrelated slow Martin/raster requests could suppress those writes without
replay when only source loading later completed. The bounded correction admits a parsed style
independently of source readiness and retains the existing `style.load` replay of latest results
and visibility. It includes behavior regressions and directory-level rationale.

The final four-file correction has independent source approval and passing full local boundary,
type-check and lint gates. Scoped `test:changed` verification against `85c4b8f4` passed its
source-related batch (four files, 37 tests) and contract batch (12 files, 267 tests). These
possibly overlapping sets are not summed as a unique-test count. Two new lifecycle cases
cover the loading/style transitions; the existing workspace fixture gained missing `getStyle`
and `getLayer` methods required by the real map contract. The follow-up commit and deployment
remain pending. This is new corrective work after retrospective review, not a second merge of PR #9.

## Acceptance state

- [x] Complete separate source/test review of the corrective batch.
- [x] Record passing integrated quality gates and independent review of the corrective batch.
- [x] Record the final commit revision after Git normalization.
- [x] Merge PR #10 and record the exact merge revision.
- [x] Verify frontend, data-service and job-executor recovery deployment and service health at `85c4b8f4`.
- [x] Verify post-deployment API availability, BLM product reads and all four crop editions.
- [x] Verify a positive BLM surface-boundary match in a bounded area.
- [x] Record independent review and passing affected checks for the retrospective PR #9 correction.
- [ ] Record the follow-up commit and deployment for the retrospective PR #9 correction.
- [ ] Capture live browser acceptance for controls, selected editions and unavailable-family notices.
- [ ] Under a separate activation decision, activate the four lane definitions and collect their first successful scheduled turns.

Browser acceptance is independently pending because `cua.getBrowser` reported no available
browser in this session. API/readiness evidence cannot substitute for browser evidence.
PR #10 and its runtime recovery are deployed; the retrospective PR #9 correction is pending deployment. Schedules have not been activated,
allowlists have not been changed and no new ingestion was triggered by this review request.
The delivery, BLM and crop tracks remain `in_progress`; broader deferred sources remain planned.
