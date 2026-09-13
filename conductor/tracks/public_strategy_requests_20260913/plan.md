---
type: Implementation Plan
title: Public strategy requests and real display names
tags: [public_strategy_requests_20260913]
resource: ./spec.md
---

# Implementation Plan: Public strategy requests and real display names

## Overview

Five phases. Phase 1 is a decision checkpoint — OQ-A through OQ-F must be confirmed (or the spec's
recommendations explicitly accepted) before any migration or schema code lands, following the same
staging convention `unified_intervention_layer_20260913` used before its own OQ-2/OQ-4 schema
commitments. Phase 1 also resolves the two hard preconditions flagged in the spec's closing section
(whether `users.name` is actually populated today, and whether production has real
`strategy_requests` rows worth backfilling) — both are verification tasks, not code. Phase 2 unifies
the type vocabulary and promotes strategy requests into `geo.features` (FR-1, OQ-A/D/F). Phase 3
retires the old private path (FR-3, OQ-B/C) and rewires the UI. Phase 4 builds the display-name
directory and threads it through the three call sites (FR-4, OQ-E). Phase 5 wires request
comment/like reuse onto the existing detail panel (FR-2) and runs the full verification sweep.

Phases 2 and 3 are ordered together deliberately: promoting requests into `geo.features` (Phase 2)
before dropping `strategy_requests` (Phase 3) means there is never a window where a submitted
request has nowhere to land. Phase 4 (display names) is independent of Phases 2/3 and could run in
parallel if capacity allows; it is sequenced after them here because one implementer is the default
assumption, matching the sibling track's own note.

## Phase 1: Decision checkpoint and precondition verification — RESOLVED 2026-09-13

- OQ-A/F → promote into `geo.features` with real Point geometry, reusing the intervention pipeline.
- OQ-B → retire the private path entirely; **verified against production directly**:
  `strategy_requests` = 3 rows (all from one user), `request_votes` = 0 rows. Cheap enough to
  **migrate**, not discard — Phase 3's backfill task runs.
- OQ-C → votes and likes stay separate concepts. `request_votes` keeps its own no-toggle-off,
  denormalized-count semantics; its FK moves from `strategy_requests.id` to `features.id`.
- OQ-D → unify vocabulary into `InterventionType`, add `water_harvesting`; requests stay
  land-category-only (no `cloud_seeding`).
- OQ-E → **verified against production directly**: both current users have `users.name` populated.
  Precondition holds — proceed with `users.name`-backed resolution, no new column.
- Sub-decision (a): a public request posts **directly to published**, no review queue — it's a
  lighter-weight social ask, not a formal land-use claim needing expert review, unlike a drawn
  intervention.
- Sub-decision (b): land-category-only, per OQ-D above.
- Sub-decision (c): `"You"` stays as the viewer's-own-comment override once real names exist.
- `community.ts` contains exactly `submitRequest`/`voteOnRequest`/`getRequests`/`getPriorityZones`/
  `getRequestById` — confirmed, nothing else to preserve.

[checkpoint marker: decisions + both precondition findings recorded]

## Phase 2: Unify the type vocabulary and promote requests into `geo.features` (FR-1, OQ-A/D/F)

Goal: a strategy request becomes a `geo.features` row with a real geometry, using the unified
`InterventionType` vocabulary, visible to every reader without review.

Tasks:
- [ ] Task (TDD): Write a failing test asserting `InterventionType`/`LAND_INTERVENTION_TYPES` in
      `src/lib/environmental/intervention.ts` include `"water_harvesting"` as a land-category member.
      Implement the one-member union/array extension.
- [ ] Task (TDD): Write a failing test for a new or extended submission mutation (e.g.
      `interventions.submitRequest` or a `kind` parameter added to `submitIntervention`, per Phase 1's
      OQ-A confirmation) asserting: it accepts the unified `InterventionTypeSchema` restricted to
      land-category types (per Phase 1's confirmation of OQ-D's air-category question), accepts a
      Point geometry via `InterventionGeometrySchema` (per OQ-F), writes a `geo.features` row with
      `properties.kind = "request"`, and (per OQ-A's default recommendation) writes directly to the
      published-equivalent `status` rather than `pending_review` — unless Phase 1 confirmed the
      review-queue alternative, in which case this test asserts `pending_review` instead. Implement.
- [ ] Task (TDD): Write a failing test asserting the merged intervention/request map layer renders a
      `kind: "request"` feature (visual distinction from a recommendation, per Technical
      Considerations, if Phase 1 confirmed one is wanted; otherwise assert it renders using the
      existing status/category paint expression unchanged). Implement any new paint-expression arm
      needed in `src/lib/map/layers.ts`.
- [ ] Task (TDD): Write a failing test asserting the click-to-inspect detail panel
      (`InterventionDetailModal`/`InterventionDetailRecord`) opens for a request feature and renders
      `properties.kind` distinctly (e.g. a "Request" label vs. the existing type/category display).
      Implement the minimal `InterventionDetailRecord` field addition and modal rendering change.
- [ ] Verification: Run the extended intervention-submission test suite, the layer-registry/layer
      tests, and the detail-panel component test together; manually confirm (test against prod + live
      Martin, per "Never run PlantGeo locally") that a submitted request appears on the map
      immediately and is clickable by a signed-out reader. [checkpoint marker]

## Phase 3: Retire the private path and rewire the UI (FR-3, OQ-B/C)

Goal: `strategyRequests`/`requestVotes`/`priorityZones` and the five `communityRouter` procedures
are gone; `RequestSubmitModal.tsx`/`CommunityDetails.tsx` reflect the public flow with no stale
"private"/"never shown on the map" copy anywhere.

Tasks:
- [ ] Task: If Phase 1 confirmed production rows exist worth preserving, write and run a one-time
      backfill script inserting one `geo.features` row per existing `strategy_requests` row before
      the drop migration runs (properties per Phase 2's shape). Skip this task if Phase 1 found no
      rows worth preserving, and record that finding here.
- [ ] Task (TDD): Write a failing migration test asserting `strategy_requests`, `request_votes`,
      `priority_zones` no longer exist in the schema. Implement the migration (drop tables), landed
      together with Phase 2's `InterventionType` union extension per the spec's migration-ordering
      note, and re-pin `src/lib/server/db/migration-contract.ts` per
      `src/lib/server/db/AGENTS.md`'s existing convention.
- [ ] Task: Delete `submitRequest`/`voteOnRequest`/`getRequests`/`getPriorityZones`/`getRequestById`
      from `community.ts` (or the whole file, per Phase 1's confirmation of what else lives there).
      Delete `src/lib/server/services/community-activity.ts`'s `summarizeStrategyActivity` if it has
      no other caller after `getPriorityZones` is removed (confirm via grep before deleting).
- [ ] Task (TDD): Write a failing test for `RequestSubmitModal.tsx` (or its replacement/merge into
      the intervention submission modal, per Phase 2's chosen mutation shape) asserting no "private"/
      "never shown on the map"/"shared only with authenticated members of" copy remains, and that
      submitting calls the new public request mutation from Phase 2. Implement, removing the
      `locationConsent` "private storage" checkbox copy and replacing it with copy consistent with a
      public submission (still requiring an explicit consent action if OQ-A's `publicationConsent`
      pattern is reused for requests too — confirm at Phase 1 whether requests need the same explicit
      consent checkbox interventions use, given they post directly to published).
- [ ] Task (TDD): Write a failing test for `CommunityDetails.tsx`'s "Strategy requests" section
      asserting the panel copy and the request list no longer read from `community.getRequests`
      (removed) and instead read from whatever new public request list read Phase 2 exposes (or, if
      requests now render solely on the map/detail-panel pipeline with no separate panel list,
      asserting the section is removed and replaced by a pointer to the map). Implement.
- [ ] Verification: Run the updated component tests, the migration test, and a full grep sweep for
      the retired strings ("private request", "never shown on the map", "Requests are private",
      "Requests are shared only with") confirming zero remaining matches in `src/`. [checkpoint marker]

## Phase 4: Real display-name directory (FR-4, OQ-E)

Goal: a signed-in reader sees resolved `users.name` values (with the existing fallback) everywhere a
contributor is identified, via one new batched procedure.

Tasks:
- [ ] Task (TDD): Write a failing test for a new `users` router procedure (e.g.
      `users.getDisplayNames`) asserting: it accepts a bounded array of user ids, returns `{ id, name,
      image }` per id (never `email`/`passwordHash`/`platformRole`), requires `protectedProcedure`
      authentication, and returns the requested fallback shape (name-or-null, letting the client apply
      the existing id-fragment fallback) rather than baking the fallback string server-side (keeps the
      fallback logic in one place, the client component, matching where it lives today). Implement.
- [ ] Task (TDD): Write a failing test for `InterventionCommentThread.tsx`'s `authorLabel` asserting
      it renders a resolved name from `getDisplayNames` when available, batched once per page of
      loaded comments (assert the mock/spy is called once per distinct-author-set page load, not once
      per comment), keeps `"You"` for the viewer's own comments per Phase 1's confirmation, and falls
      back to the existing `"Contributor <id8>"` string when no name is set. Implement.
- [ ] Task (TDD): Write a failing test asserting the intervention/request detail panel
      (`InterventionDetailModal`) resolves and shows `submittedByUserId`'s display name using the
      same procedure, with the same fallback. Implement.
- [ ] Task: Grep for any other place a raw `authorUserId`/`submittedByUserId`/`userId` is rendered
      directly to a viewer (e.g. `/feed`'s `ProposalRow`, if it shows submitter identity) and thread
      the same resolution through in this pass, per the product owner's "land all three consistently"
      instruction — record every site touched in this task's completion note.
- [ ] Verification: Run the new procedure test, the comment-thread and detail-panel component tests,
      and confirm via a manual pass (two accounts, one with `name` set and one without) that both the
      real name and the fallback render correctly, and that `email`/`passwordHash`/`platformRole`
      never appear in the network payload for `getDisplayNames`. [checkpoint marker]

## Phase 5: Comment/like reuse on request features and full sweep (FR-2)

Goal: confirm no new backend code is needed for request comments/likes (FR-2's core claim), wire any
remaining UI gaps, and run the complete affected-boundary verification sweep once.

Tasks:
- [ ] Task (TDD): Write a failing test asserting `interventionSocial.toggleLike`/`getLikeState`/
      `listComments`/`postComment`/`deleteComment` operate on a `kind: "request"` feature id
      identically to an intervention feature id (no procedure changes expected — this task is
      primarily a regression-pinning test proving FR-2's "no new backend code" claim rather than new
      implementation). If the test reveals a gap (e.g. `requireVisibleFeature`'s published-status
      check assumes an intervention-specific status value that a request's published-equivalent
      status does not match), fix the minimal gap found.
  - [ ] Task: Confirm the click-to-inspect detail panel's comment/like UI (`InterventionLikeButton`,
      `InterventionCommentThread`, already generic on `featureId`) requires no changes to mount on a
      request feature; if any request-specific copy is needed (e.g. "Comment on this request" vs.
      "Comment on this intervention"), add it as a `kind`-conditional label, not a new component.
- [ ] Task: Update `RUNBOOK.md` (per `conductor/workflow.md`'s session/archive maintenance section) to
      reflect that strategy requests are now public `geo.features` rows, not a private table, and that
      a display-name directory now exists — this corrects any stale institutional-knowledge entries
      that describe the old private design.
- [ ] Verification: One full sweep per `conductor/workflow.md` — run the complete affected-boundary
      test suite (community/interventions/intervention-social procedure tests, the migration tests,
      the updated component tests, layer-registry/layer-manager tests, `map-view-render-count.test.tsx`),
      typecheck, and lint once at the end covering every file touched across all five phases. Manually
      re-verify every acceptance criterion in FR-1 through FR-4 against the running app (test against
      prod + live Martin, per "Never run PlantGeo locally"): submit a request as one account, confirm
      it appears on the map and is clickable by a signed-out session, comment/like it as a second
      account, and confirm both accounts' real names (or fallbacks) render correctly throughout.
      Record which OQ-A through OQ-F answers were actually implemented, and the two Phase 1
      precondition findings, in the track's retrospective. [checkpoint marker]
