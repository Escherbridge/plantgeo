---
type: track-plan
slug: platform_experience_qa_20260911
status: active
resource: ./spec.md
---

# Execution plan

## Q0 — establish ownership and candidate ledger

- [x] Coordinating owner adds the initial active registry entry and current
  runbook/handoff routing now; final closure/status updates wait for GREEN.
- [ ] Before spawning, check existing owner/track, dependencies, checkout, paths
  and authority; reuse the current owner where appropriate and bind every spawned
  task with its role and ownership in the ledger. Keep unresolved owners open.
- [ ] Bind coordinating, integration, author and independent verifier task IDs,
  checkouts, review bases and owned files in `evidence/task-ledger.md`.
- [ ] Reconcile each child task's current status against its actual track,
  commits, tree and receipts; preserve historical claims with their date/scope.
- [ ] Inventory all surfaced requirements from current code and child specs;
  expand `matrix.md` into individually identified cases in `evidence/cases.md`.
- [ ] Record shared-file ownership, explicit transfers and dependency merge
  order before overlapping edits; the integration owner serializes registry,
  capability, routing, auth, agent and shared UI changes.
- [ ] Freeze local endpoints, disposable database identity, synthetic accounts,
  browser/device coverage, fixture releases and performance budgets.

## Q1 — execute complete user journeys

- [ ] Run anonymous/viewer discovery, search, map navigation, layers, time,
  details, routing and every other inventoried public surface.
- [ ] Run contributor submissions and boundary create/edit/cancel/validation,
  expert approve/publish/reject and contributor outcome visibility locally.
  Label agent-created rows as synthetic mechanics evidence; they cannot close
  the community track's real human contributor requirement or production/training
  acceptance.
- [ ] Run expert/admin access and deny checks using actual local sessions;
  verify UI and server boundaries agree after refresh and role changes.
- [ ] Exercise species profile, weather and research-derived surfaced claims
  when their author candidates and data contracts are ready; mark blocked
  prerequisites explicitly instead of inventing available features or data.
- [ ] Capture desktop/mobile, keyboard/touch, screen-reader, focus, zoom/reflow,
  reduced-motion and non-colour state evidence for each relevant journey.
- [ ] Capture selected-day availability, stale-response, cold/warm cache,
  map canvas/pixels and agent/MCP parity evidence under the matrix contract.

## Q2 — return defects and integrate corrections

- [ ] Record requirement-linked failures in `evidence/defects.md`, assign the
  owning task/track and reopen any premature completion or archive.
- [ ] Authors apply the complete fix batch in their owned files and hand over
  immutable commits with base, dependency, diff and receipt references.
- [ ] Integrate prerequisites before consumers: contract/reader changes before
  dependent UI/tool changes, then shared-file reconciliation, then QA evidence.
  Record the actual order and conflict resolutions; do not assume every child
  workstream is serial or that a task status proves integration.
- [ ] Freeze the integrated tree and calculate which earlier evidence must be
  rerun after changes; preserve superseded captures rather than relabelling them.
- [ ] Reconcile every task, base, commit and receipt against the integrated tree
  in `evidence/reconciliation.md`; distinguish merged, unmerged, superseded and
  intentionally excluded work with reasons before requesting the Q3 verdict.

## Q3 — independent integrated verdict

- [ ] After all fixes, the integration owner runs one final check sweep using
  `docs/testing.md`: full type/lint/boundary checks and appropriately selected
  tests, or full suites for shared harness changes or a release gate.
- [ ] Complete the reconciliation packet with final check receipts and hashes;
  resolve all task/base/commit/tree/receipt mismatches before independent review.
- [ ] An independent verifier repeats affected browser journeys and shared
  interactions against the exact integrated candidate and reviews all receipts.
- [ ] Publish `evidence/final-verdict.md` with candidate/tree/base identities,
  requirement totals, failed/blocked/unrun cases, evidence limitations, scope,
  independent reviewer identity and GREEN/RED verdict.
- [ ] If RED, return to Q2 with an explicit defect owner and new candidate;
  never turn a failed case into a scope exclusion solely to obtain GREEN.
- [ ] Close or mark a child owner done only after its immutable candidate is
  integrated and independently verified; keep blocked/unresolved owners open.

## Q4 — final status and proof-gated retention

- [ ] Integration owner serializes shared registry/runbook status changes after
  independent GREEN, retaining separate production and child-track gates.
- [ ] Record final status changes and retained evidence links in
  `evidence/closure-handoff.md`, using the reconciled Q3 verdict packet.
- [ ] Archive/prune sessions only after integrated proof, evidence links and
  unresolved handoffs are preserved; do not archive blocked owners or delete
  their only evidence. Keep this track active until its full scope is verified.

## September 12 — local scalar-renderer evidence intake

The [bounded climate/soil label candidate](../multiscale_polygon_surface_20260901/evidence/scalar-labels-20260912/README.md)
adds synthetic desktop and narrow-mobile canvas evidence after the historical
weather repair. It is an author handoff awaiting root integration. The actual
components render unit-bearing scalar labels and preserve empty/zero-opacity
frames, with independent source review. This does not satisfy Q1 live selected-day,
full mobile application, hover, dense-basemap or Q3 integrated-candidate gates.
