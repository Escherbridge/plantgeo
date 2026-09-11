---
type: integration-ledger
date: 2026-09-11
status: active
---

# Active-track integration ledger

The serialized integration lane starts at `fa202230958fb55521963e886eb031be5fc266c4`
on `codex/active-track-integration-20260911`. Its task ID is
`01a0919f-40dd-7ae2-a5a3-cd8cf7e13489`. The inherited working tree contains the
September maintenance reconciliation and three planned botanical tracks. Those
documents are preserved inputs, not accepted implementation or release evidence.
The local inherited patch and path lists are retained in
`.omc/research/active-track-integration-20260911/`.

The [registry](../../../tracks.md), [authority order](../../../README.md),
[workflow](../../../workflow.md), [release governance](../../../release-governance.md)
and [testing contract](../../../../docs/testing.md) govern this ledger. August
partition maps remain historical; none of their ownership or execution arrows
are revived here.

## Owners and intake

The exact task titles below come from the parent task's dispatch. Stable task
IDs, final changed-file manifests and reviewed commit ranges must be recorded
before intake. A queued task's client ID is not a stable task ID.

| Task | Exclusive implementation boundary | Intake state |
| --- | --- | --- |
| Finish Parquet reader acceptance | TypeScript server/tRPC readers and capabilities, agent selected-day/neighbour semantics, TimeSlider and capability presentation, reader tests and its track | Dispatched; stable ID and reviewed handoff pending |
| Finish gapless Parquet publication | Generic Python availability/publication/object-store/gap/absence/tier code, direct-writer registration, lane registry, executor ownership/planning/lease/retry/restart, generic tests and its track | Dispatched; stable ID and reviewed handoff pending |
| Finish environmental retirement repairs | Product-specific signal/sensor/static-soil/soil-survey/MTBS repair, source/archive replacement, retirement tooling and relation/migration proof, product tests and its track | Dispatched; stable ID and reviewed handoff pending |
| Finish multiscale map surfaces | Map rendering/layers, render contract and map/geo utilities, necessary rendering stores/hooks, visual tests and its track | Dispatched; stable ID and reviewed handoff pending |
| Finish repository conformity hardening | Non-overlapping CLI/domain structure, canonical snapshot/schema structure outside offline builders, proof inventories and standards, conformity tests and its track | Dispatched; stable ID and reviewed handoff pending |
| Finish offline Parquet construction | Selected in-repository snapshot staging/build/upload/verification scripts and tests, eight-lane manifests, deferred humidity history construction, performance evidence and its track | Dispatched; stable ID and reviewed handoff pending |
| Integrate and close active PlantGeo tracks | Two umbrella tracks, this cross-track ledger, integration branch, final registry and current runbook reconciliation | Active; implementation files excluded |

The parent confirmed that its maintenance/botanical baseline remains local and
uncommitted and must not be duplicated as active-track output. Integration
commits therefore exclude inherited modifications. A peer changing an already
dirty track file must supply its isolated post-fork patch or wait for the parent
baseline commit; staging the whole inherited file is not an isolated delta.

Each lane commits only its own delta from the inherited snapshot. A handoff
must name the exact branch/base/commit range and changed files, disclose inherited
changes, attach validation commands/results and an independent review, classify
remaining production gates, and identify every dependency or shared-file request.
Local completion, deployed behavior, data publication and schedule burn-in are
separate fields in the acceptance decision.

## Shared files and sequencing

| Shared boundary | Single owner | Consumer rule |
| --- | --- | --- |
| `pipeline/parquet/` generic availability, extension, object store, census and tier derivation | Gapless | Retirement and offline submit exact required behavior/evidence; they do not patch the generic core. |
| `pipeline/direct/registry.py`, `pipeline/parquet/lane_registry.py`, `execution/job_executor_service.py` | Gapless | Product-specific modules can be prepared independently; registrations and scheduler edits land through gapless. |
| Product-specific signal, sensor, SoilGrids, soil-survey and MTBS modules | Retirement | Gapless does not rewrite product repairs; conformity submits findings before removal/refactoring. |
| `src/lib/server/services/**`, `src/lib/server/trpc/**`, TimeSlider and capability presentation | Reader | Renderer consumes the reader contract; it does not fix captions or server behavior. |
| Map renderer components and `src/lib/map/layer-render-contract.ts` | Multiscale | Reader submits missing rendering-state dependencies; map changes remain with multiscale. |
| Canonical snapshot staging/build/upload/verification scripts | Offline | Conformity excludes selected builders; generic-core needs go to gapless. |
| `interface/cli/**`, shared canonical snapshot/schema structure outside selected builders | Conformity, subject to exact file manifest | Registry or command registration needed by gapless/retirement/offline must be assigned before editing the same file. |
| Migrations and matching database contracts | Retirement, only after a current proof packet | No inherited shrink s1/s6 ownership is executable. Review includes preservation and rollback; no production apply is authorized here. |
| `services/agri-data-service/QUALITY_RECEIPT.json` | Integration for the final combined receipt | A peer receipt proves only its own committed source domain. Do not resolve overlapping receipts by choosing the newest timestamp. |
| `conductor/tracks.md`, `conductor/RUNBOOK.md`, both umbrella tracks | Integration | Peers update their own track only; integration applies accepted status changes together. |

Directory shorthand in this table is relative to
`services/agri-data-service/src/agri_data_service/` where no repository root is
shown. These are ownership reservations, not claims that every listed file will
change. Exact changed-file intersections are checked for every handoff. If two
lanes need the same file, choose one author and record the decision before intake.

Dependency order is determined by reviewed diffs: shared gapless contracts before
their product/offline consumers; accepted reader contracts before dependent map
changes; conformity moves after dependent modules settle. Independent commits
may land earlier if their manifests do not overlap. Registry/runbook reconciliation
and the final combined receipt follow accepted implementation. Conflicts or
behavior findings return to the owner with the failing contract and exact revision.

## Intake blockers and stop conditions

1. Six stable task IDs and reviewed commit ranges are not yet available in the
   task listing. The parent is resolving dispatch setup. No branch is accepted.
2. The inherited maintenance and design changes have no baseline commit in this
   checkout. Preserve them; reconcile the parent's baseline before integrating
   overlapping peer track edits.
3. Production acceptance still lacks the complete reader/renderer/writer matrix,
   three scheduled advances per activated product and recovery evidence. No
   umbrella closure or GREEN production verdict follows from an implementation
   handoff.
4. Historical package completion and the September rebuild must be reconciled
   with current conformity/retirement proof before changing shrink's status.

The three PNW Herbaria/botanical tracks are design-only and outside this
implementation acceptance. No deployment, push to `main`, data publication or
retraction, Railway/database/object-store mutation, migration, service removal or
relation drop is performed by this integration lane without exact authorization.

## Final verification and release boundary

After all accepted fixes land, freeze the combined commit and changed-file
manifest, run one full integrated boundary/type/lint/frontend/Python sweep under
the testing contract, then obtain independent review. Report actual skips and
environment limits. A receipt-producing Python invocation requires the full
source domain to be committed and stable. Do not reuse peer counts or receipts
as combined-tree evidence.

The final handoff records the combined SHA, source digest, service/deployment
matrix, product/day/rung/generation evidence, outstanding owner gates and exact
rollback revisions. Until those identifiers and authorization are present, the
release decision is HOLD. Production acceptance remains with
[`parquet_production_acceptance_20260901`](../../parquet_production_acceptance_20260901/plan.md).
