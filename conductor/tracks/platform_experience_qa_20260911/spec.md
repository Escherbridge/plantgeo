---
type: track-spec
slug: platform_experience_qa_20260911
status: active
---

# Platform experience QA and track orchestration

## Outcome and ownership

The coordinating task owns requirement-by-requirement browser acceptance of
PlantGeo's surfaced features and reconciliation of the tasks delivering them.
Each visible control, route, map layer, detail view and agent capability must
have an owning requirement, reproducible user journey and independent verdict.
The inventory includes current main and each incoming candidate; absence from
an initial checklist does not exempt a surfaced feature from QA.

This track authors the matrix, defects and integration evidence. Feature authors
own fixes in their existing tracks. A separate verifier task reviews the frozen
candidate and evidence; authors cannot approve their own changes. The integration
task owns shared application files, merge order and candidate assembly. This
track does not independently patch those files during a verdict run.

The coordinating owner must register this track as active and route current
handoffs in the shared registry/runbook now. Initial registration does not wait
for GREEN; final closure/status changes do. Shared edits remain with that owner.

Before spawning work, the coordinator checks existing task/track ownership,
dependencies, checkout, owned paths and authority, then reuses an existing owner
where appropriate. Bind every spawned task in the ledger with those fields and
its author or verifier role. Close or mark an owner done only after its immutable
candidate is integrated and independently verified. Keep blocked or unresolved
owners open. Archive or prune sessions only after final integrated proof and
retention of evidence and handoff links.

## Local execution boundary

Use an isolated local application and disposable local QA database with synthetic
viewer, contributor, expert and admin accounts, plus an anonymous session. Record
the database identity and local endpoints before any write, keep credentials out
of receipts, and ensure external writes, email and notifications are disabled.
Exercise submissions and boundary authoring through the real local user flow;
do not seed intervention records or bypass consent, review or publication rules.
Synthetic fixtures must be labelled and never enter production or training data.
Agent-created local intervention rows are synthetic workflow-mechanics evidence
only. They cannot satisfy the governing community track's real human contributor
requirement or establish production or training acceptance, even when created
through the normal UI and successfully reviewed and published locally.

No Railway, production database, production object storage or deployed service
mutation is authorized by this track. Local QA cannot replace production data,
deployment or scheduled burn-in acceptance. Existing production receipts may be
linked with their original scope and age; they are not fresh measurements.

## Required coverage

The [matrix contract](matrix.md) expands every requirement across desktop and
mobile, relevant roles, keyboard and touch, accessibility, normal and failure
states, and cold and warm cache behavior. Test complete journeys and recovery,
including refresh, navigation, denied access, loading, empty, unavailable, stale
and error states. Record actual browser, viewport and input method; simulated
mobile coverage must not be described as physical-device proof.

Map acceptance includes screenshots and canvas/pixel evidence of the painted
result, selected feature and geometry, zoom transitions, seams, support limits,
legends and interaction feedback. DOM presence and successful network responses
alone do not prove a correct map. Preserve original captures with camera, day,
layer, data release and capture timing so comparisons can be reproduced.

Selected-day acceptance reconciles catalogue availability, exact reader response,
painted frame, legend, details and agent/MCP answer. Latest, populated historical,
governed-empty, missing and outside-coverage cases must remain distinct. Rapid
day/location changes and delayed responses must not repaint stale data or silently
substitute adjacent times. Neighbour suggestions must identify their distance or
time offset. Static lookups identify their release; forecasts identify run, issue
time, valid time, interval and units without pretending to be observations.

Agent and MCP journeys use the same selected coordinates, day/window, filters,
role, release/run and units as the UI. Compare values, support, provenance,
missingness and refusal behavior, including unavailable tools. A successful tool
invocation alone does not prove parity or a truthful user answer.

## Dependencies and task binding

The following are scope and handoff relationships, not claims of completion.
Bind each live task ID, checkout, review base, immutable candidate commit and
receipt in the evidence ledger before consuming a handoff.

| Workstream | Relationship and QA intake |
| --- | --- |
| Integration task | Shared-file owner; supplies merge order, integrated candidate and final check receipts. Bind its actual task ID at intake. |
| [Botanical species profile](../botanical_species_profile_lookup_20260911/plan.md) | Release-pinned species details and agent evidence, per-value provenance, missing traits and refusal. |
| [Botanical occurrence plane](../botanical_occurrence_parquet_lane_20260911/plan.md) and [experience](../botanical_occurrence_experience_20260911/plan.md) | This is separate from the current species-profile candidate. Occurrence UI QA remains blocked until source admission, a governed occurrence release, multiscale rendering and reader-cutover gates are ready; then cover occurrence-detail, documented-richness and collection-effort claims without implying abundance, current occupancy or surveyed absence. |
| [Weather forecast lane](../weather_forecast_parquet_lane_20260911/plan.md) and [experience](../weather_forecast_experience_20260911/plan.md) | Admit the data contract before testing forecast claims; cover map/card/wind/time/unit parity and the unavailable-day regression. |
| [Community engagement](../community_engagement_completion_20260805/plan.md) and community/boundary authoring task | Local contributor submission, geometry editing, consent, expert review, publish/reject, contributor outcome and map visibility; bind the boundary task and its actual track at intake. Existing ML decision gates remain separate. |
| PNW/GIS contact research task and [PNW source admission](../pnw_herbaria_source_admission_20260911/plan.md) | Bind the research task and source/rights evidence; inspect only surfaced claims or admitted data. Research is not authorization to contact people, ingest or publish. |
| [Parquet production acceptance](../parquet_production_acceptance_20260901/plan.md) | Independent production gate; consume scoped receipts and return UI findings. Local GREEN never closes its all-product or scheduled burn-in requirements. |

## Status and closure gates

Track statuses follow the registry: `planned`, `active`, `blocked`, `complete`
and `historical`. `active` means execution is open, not acceptance granted.
`blocked` records a concrete missing prerequisite with an owner and next action.
`historical` preserves a superseded or archived record; it is not a passing verdict.

Matrix cases use `not_run`, `blocked`, `fail`, `pass` or `not_applicable`.
Blocked and unrun cases cannot count as passed; not-applicable needs a requirement
scope reason accepted by the independent verifier. A defect includes severity,
reproduction, expected/observed behavior, evidence and owning task/track.

Closure requires all gates:

1. Inventory: every surfaced requirement maps to cases, owners and evidence;
   omitted surfaces and scope exclusions receive independent review.
2. Candidate: task/base/commit/tree/dependency/receipt identities agree, all
   intended changes are integrated, and shared-file conflicts are resolved.
3. Behavior: all required desktop/mobile, role, input, accessibility, cache,
   temporal, canvas and agent/MCP cases pass on that candidate. A fixture-only
   pass is labelled and cannot establish real-data correctness.
4. Engineering: apply all fixes first, then perform one integrated verification
   sweep under the repository testing policy. Record commands, scope, base,
   exit codes and receipt hashes; scoped tests are never called a full-suite pass.
5. Independent verdict: a verifier in a separate task approves the exact tree
   and evidence only after complete task/commit/tree/receipt reconciliation,
   with no unresolved in-scope failures, blocked or unrun cases.
6. Final status: the integration owner updates the shared registry and runbook
   consistently after GREEN; dependencies retain their own unfulfilled gates. Archive or
   prune completed sessions only after this proof and retained evidence links.

Rendering, unit tests, an author's completion message or a merged commit alone
cannot close UX work. Failed evidence reopens the owning track, including an
archived track when necessary, and records the previous verdict as superseded.
Fixes require a new immutable candidate and independent re-verification of the
affected cases plus shared journeys. Any candidate change invalidates the old
integrated verdict until its impact and required new sweep are reconciled.
