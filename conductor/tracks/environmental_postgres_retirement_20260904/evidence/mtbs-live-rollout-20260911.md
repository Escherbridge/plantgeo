---
type: track-evidence
status: complete
recorded_on: 2026-09-11
source: .omc/research/mtbs-live-rollout-20260911.md
source_sha256: 4cf74d1b3c71ee5a82c384f608304a1d3704dc61d014b3a839eedf2bc5d41d20
---

Preserved from the prior rollout session on September 11. The original report
follows unchanged; JSON/log basenames resolve under `.omc/research/` in the
workspace. This preservation pass did not repeat production checks. Completion
applies to the bounded rollout, not historical completeness or future burn-in.

# MTBS current snapshot rollout

User authorized source-first ingestion, the latest published maps with incomplete seasons labeled, and deployment/publication with an explicit approval override.

## Source and scope

- Official source: https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63
- Footprint: Pacific Northwest, longitude -125 to -111, latitude 42 to 49.
- Captured September 10, 2026; available September 11, 2026 under next-UTC-day availability semantics.
- Replacement scope: 2018–2026, 747 unique fires; counts 150, 63, 100, 115, 113, 69, 130, 6, 1 respectively.
- Fire seasons 2023, 2024, 2025 and 2026 remain incomplete.
- Manifest: `4690af4629e617ffbde3f5e6ae99be3eb4c1a536fe47a931d7f0a8456c8f6468`.
- Exact sorted fire-ID set SHA256: `875ead71e552c1808182f25e7c8d6c1f05621d9cb518877b5c5c895a61a4f3a7`.
- Older source maps from 1984–2017 were inventoried separately and are outside this current-snapshot replacement scope.

## Publication evidence

The approved release `3632d616dc43f3845dbcb901e625b946b82519ba` was pushed to main and deployed to the frontend, data API and executor. Publication returned `published` with 747 rows at 05:38:23 UTC. An immediate reader refusal exposed a completion-format mismatch: the ordinary base writer emits a canonical v1 marker, while the new catalog had assumed v2 embedded part receipts at every tier.

The narrow reader correction retains all availability/source/terminal/physical receipt checks and accepts the canonical v1 base marker. Read-only verification at 05:44:21 UTC passed all four tiers, exact prepared hashes, 747 unique IDs, year counts, completion/index bindings, snapshot descriptor, and empty pending queue. No data rewrite was needed.

Evidence: `mtbs-publication-outcome-20260911.json`, `mtbs-catalog-diagnosis-20260911.json`, `mtbs-publication-audit-verified-20260911.json`, `mtbs-prepared-fire-identity-proof-20260911.json`.

## Automatic refresh

The previously enabled burn forward job was paused for publication. Its persisted v2 definition still had the older weekly schedule and shorter runtime limits, because normal registration preserves existing definitions. The exact paused definition was reconciled with the approved code: daily 08:55 UTC, 2,130-second worker budget, 2,220-second lease; fresh source capture is due every seven days. Independent review and committed readback verified the change.

Evidence: `mtbs-lane-post-rollout-before-reconcile-20260911.json`, `mtbs-paused-definition-reconciliation-applied-20260911.json`.

## Final serving verification

Complete. Reader correction `fa202230958fb55521963e886eb031be5fc266c4` is deployed successfully to the web app, data API and executor. The final public readiness check passed at 05:56:45 UTC.

The five-request public verification passed. The current unfiltered response contains the exact 747-fire identity set and approved partial-season descriptor. The regional zoom-5 viewport contains 746 fires; a local spatial proof against the exact prepared bytes establishes that one simplified 2018 polygon falls outside this viewport, while its zoom-13 geometry intersects. No physical fire is missing. The tested historical response remains byte-identical to its 540-row baseline, including the existing truncation flag; this is preservation evidence rather than a historical completeness claim.

Browser accessibility and screenshot inspection confirmed the enabled burn layer, date September 11, latest-following state, rendered boundaries, and the explicit incomplete-season notice. The burn job was restored to its original enabled state; fresh committed readback shows the approved daily schedule and limits, no specification differences and no active work. Future scheduled execution has not yet occurred.

Final evidence: `mtbs-final-deployments-20260911.json`, `mtbs-public-http-readback-v2-20260911/audit.json`, `mtbs-prepared-viewport-proof-20260911.json`, `mtbs-live-ui-verification-20260911.json`, `mtbs-lane-resume-20260911.json`, `mtbs-lane-post-rollout-inspection-20260911.json`.

## Checks and review

The initial frontend sweep passed data-boundary, TypeScript, ESLint and tests (2,151 passed, 13 skipped, plus six tooling tests; ESLint had existing warnings and zero errors). Railway also completed its Docker frontend release gates. The narrow reader correction passed the full Python format/lint/mypy/pytest invocation, with database-test connection variables explicitly absent; database integration is not certified by that run. Its verified quality receipt covers 1,307 files with digest `c9739cb76c667db0820727b2f634beee725b98938c78537f8f78ec86469c53c1`.

Separate review lanes cleared the source/test correction, quality receipt, definition reconciliation, physical publication readback and public acceptance evidence. The initial failed catalog read and HTTP capture remain preserved for diagnosis; they are not passing results. Unrelated working-tree changes were left untouched.
