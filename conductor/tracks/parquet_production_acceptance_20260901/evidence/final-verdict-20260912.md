---
type: acceptance-verdict
track: parquet_production_acceptance_20260901
date: 2026-09-12
status: blocked
verdict: RED
source_commit: 843b4b313e03447594b23a67f75c3062b2b1a024
source_tree: 9533bb9e5423240630935df0cd012cd8ead15504
---

# Production acceptance verdict — September 12 local intake

**RED. Production acceptance remains blocked.** This packet contains a completed
local dependency map and evidence-intake checklist; it does not contain a new
production execution, full quality pass, data certification or operational
authorization. Every required blocked/unrun case stays RED. Current complete
deployment, data, capture and rollback bindings remain absent.

The immutable source base is local `main`
`843b4b313e03447594b23a67f75c3062b2b1a024`, tree
`9533bb9e5423240630935df0cd012cd8ead15504`. The local branch is
`codex/production-acceptance-verdict-20260912`. Packet commit/tree and file hashes
are supplied in the final task handoff. No runtime/shared-ledger/registry/plan
status is changed, and no Railway, database, `pgt`, object storage, writer,
scheduler, deployment or live service was accessed. No push is performed.

## Packet

| Artifact | Purpose and result |
| --- | --- |
| [Acceptance matrix](acceptance-matrix-20260912.md) | G0–G7 dependencies; product/day/zoom/cache/coverage/day-route/window/release/refusal, device/role and metrics expansion; every gate RED |
| [Product matrix](product-matrix-20260912.csv) | 29 intake rows: 27 PGQA-L surfaces and two backend rows; 32 source-registered physical keys, 30 in public contracts; all blocked/RED |
| [QA crosswalk](qa-crosswalk-20260912.csv) | Exact 34 September 12 groups and their source requirements/owners/freeze keys/prerequisites/evidence: 30 blocked, four not_run, all RED |
| [Fan-in checklist](fan-in-checklist-20260912.md) | Future artifact filenames and exact custody/source/absence/manifest/bounds/hash/trace/canvas/metrics/recovery/burn-in/rollback fields; five separate current-production reviews are unbound/not_run/RED |
| [Source custody](source-custody-20260912.json) | 87 retained source files with source commit, Git blob ID, canonical blob byte length and SHA-256; includes 26 original scalar captures and exact three historical temperature generation/bootstrap receipts |
| [Documentation review](documentation-review-20260912.md) | Separate local packet review and final documentation-check receipt; cannot substitute for scientific/data, renderer, accessibility, agent-parity or release review |

## Decisive missing evidence and next owner actions

Accountable tracks are named below; current operational capture tasks and the five
independent production reviewers are **UNBOUND**. The coordinator must bind an
actual owner at intake. Prior archived author sessions are retained custody,
not silently reactivated operators. Detailed per-product actions remain in the CSV.

| Missing gate / artifact | Accountable owner | Next action; current state |
| --- | --- | --- |
| Literal current product/day/route/deployment freeze, thresholds and service/rollback matrix | Production acceptance + canonical QA/integration + operational release owner | Supply O1/D1/U1, registered-versus-effective lane list, literal terminal/history/absence days and route applicability; **RED / blocked** |
| Caption correction plus private/public reader and cold/warm traffic packet | Reader R0/R2 author; production A1/A2 capture owner | Bind corrected candidate for authority/source-ceiling text; capture actual request classes, zero historical availability scans, no `/api/fires`, bounds and requested/served/painted agreement; **RED / blocked** |
| Older soil-wetness, precipitation, dew-point, drought and burn-severity intervals | Gapless P0/P1/P3 + environmental retirement | Name acquisition/recovery owner for every required interval beyond bounded polling; provide floors, satisfied/unresolved intervals, receipt-backed absences and all-rung/index reconciliation; **RED / blocked** |
| Other source histories and fixed support | Gapless + retirement | Bind fire/water/weather/vegetation/ERA5 horizons and source-version limits; supply immutable climate/soil/vegetation IDs, coordinates, fractions, ordering and grid validation; **RED / blocked** |
| Signal/sensor/static-soil/SSURGO admission | Environmental retirement; reader/multiscale consumers | Supply missing archive/rescue/candidate and independent receipts; resolve sensor pagination/absence conflict; verify all twelve static-soil objects; restore SSURGO under separate native/low-zoom contract; **RED / blocked** |
| Effective executor exclusivity and recovery | Gapless P3/P4 + retirement effective-cutoff owner | Current command/definition/configuration, active/required set, process/lease/attempt/cursor readback, retry/restart/expired-lease terminal receipts and stale-worker fencing; **RED / blocked** |
| Three scheduled advances per activated lane | Gapless P4; production plan A3 | Submit consecutive scheduled run/time/source/output/index receipts and after-each-advance coverage/rung/conservation reconciliation; configured duty/manual backfill is insufficient; **RED / blocked** |
| Cross-product renderer packet | Multiscale M0/M3 + reader + QA | Real-data native/event/field baselines, one rung, support/conservation/pixels, dense-basemap/picking/mobile and predeclared byte/feature/frame/request-to-paint metrics; **RED / blocked** |
| Remaining weather/botanical/agent/role/mobile journeys | QA + owning feature/admission tracks | Execute crosswalk variants on bound prerequisites; preserve forecast/profile/occurrence admission and protected-data/refusal distinctions; 30 source groups **RED / blocked**, WX-04/AP-01/AP-04/RA-03 **RED / not_run** |
| Full exact-candidate quality sweep and five independent reviews | Integration; scientific/data, renderer, accessibility, agent and release reviewers | After all fixes and freeze, run final full release checks, then fan in exact review/evidence hashes and residual risks; production reviews **RED / not_run**, dependent release gate **RED / blocked** |

## Historical receipts retained without expanding their verdicts

| Retained evidence | Established scope in its own receipt | Limit on current acceptance |
| --- | --- | --- |
| [Temperature bootstrap receipts](../../environmental_postgres_retirement_20260904/evidence/temperature-bootstrap-receipts-20260910.json) | September 10: mean/min/max each 1,560 days, April 30, 2022–August 6, 2026; four rungs, 6,240 index rows per lane; exact generation/bootstrap/source-inventory hashes copied into custody index | Source ceiling September 5 does not publish later days. No current pointer hashes, later tail closure, all-reader trace or three-schedule burn-in follows. Availability schema 1 is distinct from coverage wire v3 |
| [MTBS rollout](../../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md) | September 11 bounded public/browser/deployment receipt on `fa202230958fb55521963e886eb031be5fc266c4`; 747 fires, 2018–2026; current manifest `4690af4629e617ffbde3f5e6ae99be3eb4c1a536fe47a931d7f0a8456c8f6468`; 746-vs-747 viewport geometry explained | 2023–2026 seasons remain partial; 3,077 older fires are separate. Preserved 540-row historical response includes its old truncation flag, so it cannot pass a current `truncated=false` case. Future scheduled execution was unobserved |
| [Scalar captures](../../multiscale_polygon_surface_20260901/evidence/scalar-labels-20260912/README.md) | 26 synthetic captures, blank basemap, 1280x720 and 390x844, z3/7/10/13; scalar zero/missing/avg/opacity/style mechanics | Direct WebGL probes unreliable; only two documented screenshot-derived seam rows carry their narrow result. Synthetic settle waits are not request-to-paint; live support/conservation, dense basemap, full touch and reader state are unproven |
| [Weather approval](../../platform_experience_qa_20260911/evidence/weather-approval-20260912.md) | Fixed-desktop unavailable-state repair; separate style-readiness and ready-to-outage regressions retained by source QA matrix | No governed populated feed, full narrow-mobile/touch, or forecast approval. Current requested/served-day relationship and notice/refusal remain owner-gated |
| [Local ownership audit](../../gapless_parquet_publication_20260901/evidence/local-ownership-audit-20260912.md) | Implemented derived-empty receipt support, bounded source writers and recovery contracts | No stored rung repair, effective production cutoff, observed retry/restart/lease recovery or later scheduled runs proven |
| [Operational retrospective](../../../retros/parquet_operational_checkpoints_20260911/README.md) | September 9 rebuild and completed bounded September 10–11 slices | Do not claim PostgreSQL remains intact or that all environmental retirement finished; no new read or mutation follows from historical authorization |
| [Root checks](../../platform_experience_qa_20260911/evidence/root-integrated-checks-20260912.md) | Boundary PASS; Ruff format/lint/mypy PASS; focused botanical 66 passed/2 skipped; combined Python 5,482 passed/147 skipped/1 xfailed/**510 errors** | Frontend type/lint/changed tests NOT RUN; combined Python PARTIAL. Dated evidence is not a fresh S0 full-suite or production release pass |

## Stop and handoff rule

Future ceiling, unexplained tail, selectable missing day, silent fallback,
unlabelled cap/truncation, cross-day substitution, request-time historical
availability scan, missing/overlapping writer, failed recovery, seam/fake
perimeter, simultaneous rung or conservation failure immediately leaves the
affected case and fan-in RED. Missing reviewer, artifact, hash, exact candidate
identity or required case also leaves RED. Return defects to their owning track
and require a new immutable candidate and affected evidence.

No production parent is closed, archived or graduated by this packet. A future
GREEN verdict could feed downstream retirement review under the
[release policy](../../../release-governance.md); it cannot itself disable a
writer, delete data, restore retired readers, publish a forecast/effect claim or
authorize a deployment. Exact rollback must preserve immutable data and exclusive
ownership; historical `2b4cfef..HEAD` rollback prose is not a current executable
rollback plan after the rebuild.
