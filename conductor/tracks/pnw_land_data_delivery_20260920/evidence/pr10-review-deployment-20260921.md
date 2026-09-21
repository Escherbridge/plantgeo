---
type: evidence
track: pnw_land_data_delivery_20260920
recorded_at: 2026-09-21
status: merged_deployment_in_progress
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

Frontend evidence consists of the full sweep followed by passing affected correction tests,
full type-check and full lint. It is not a fresh full-suite result of 3,077 passing tests.

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
| Frontend/main | `033210c3-3ccb-4d85-8515-715a77a1d2b4` | BUILDING |
| Parquet API | `4e21b2ae-2cc2-4f48-a3fb-595fcae1a3c3` | FAILED during startup after a successful build |
| Job executor | `5d678762-3453-4d6c-9a16-86d49ac2a2b9` | SUCCESS |
| Martin | `34835837-187c-4e73-b0a7-6c9b0c4fc9d5` | SUCCESS |
| ML | No new deployment | SKIPPED; unchanged watched scope |

The Parquet API startup failed while importing Rasterio because `libexpat.so.1` was missing
from the runtime image. Railway retained the previous healthy API deployment. A runtime
dependency correction is being prepared and independently reviewed; recovery deployment and
post-deployment API readback remain pending. The frontend is still building. No overall
deployment acceptance is claimed.

## Acceptance state

- [x] Complete separate source/test review of the corrective batch.
- [x] Record passing integrated quality gates and independent review of the corrective batch.
- [x] Record the final commit revision after Git normalization.
- [x] Merge PR #10 and record the exact merge revision.
- [ ] Verify frontend, data-service and job-executor deployment of that revision and service health.
- [ ] Verify post-deployment APIs, BLM product reads and all four crop editions.
- [ ] Capture live browser acceptance for controls, selected editions and unavailable-family notices.
- [ ] Under a separate activation decision, activate the four lane definitions and collect their first successful scheduled turns.

Browser acceptance is independently pending because `cua.getBrowser` reported no available
browser in this session. API/readiness evidence cannot substitute for browser evidence.
PR #10 is merged and deployment is in progress. Schedules have not been activated,
allowlists have not been changed and no new ingestion was triggered by this review request.
The delivery, BLM and crop tracks remain `in_progress`; broader deferred sources remain planned.
