---
type: Implementation Plan
title: Unified intervention layer with click-to-inspect detail panel
tags: [unified_intervention_layer_20260913]
resource: ./spec.md
---

# Implementation Plan: Unified intervention layer with click-to-inspect detail panel

## Overview

Five phases. Phase 1 is a decision checkpoint — OQ-1 through OQ-5 must be answered (or the spec's
recommendations explicitly confirmed) before any code lands, exactly as the sibling
`ai_intervention_workspace_20260913` track staged its own checkpoint before committing to a shape.
Phase 2 merges the two layer toggles (FR-1). Phase 3 adds click-to-inspect (FR-2). Phase 4 builds
the comment/like backend (FR-3). Phase 5 mounts comment/like UI on the detail panel (FR-4) and runs
the full verification sweep. Phases 2-3 and 4-5 are largely independent of each other (the layer
merge does not depend on comments existing) and could be sequenced in parallel tracks if capacity
allows, but are written here as one ordered plan since one implementer is the default assumption.

This order exists because Phase 2's `category`-projection migration (OQ-2) and Phase 4's schema
(OQ-4) are both irreversible-ish (a live migration, a new table other code will start depending on)
and both have a stated recommendation rather than a forced default — starting either before the
checkpoint risks building against an answer the product owner rejects.

## Phase 1: Decision checkpoint (no code)

Goal: get explicit product-owner sign-off on OQ-1 through OQ-5's recommendations (or their
alternatives) before any schema, registry, or component work begins.

Tasks:
- [ ] Task: Present OQ-1 (registry aliasing vs. structural change), OQ-2 (category migration +
      shared status-color expression vs. keep priority), OQ-3 (separate detail-panel component vs.
      third AiInterventionWorkspace mode), OQ-4 (per-user-toggle likes, flat comments, no
      pre-publication comment moderation, visibility-scoped access), and OQ-5 (shared backend now,
      `/feed` UI mount deferred) to the product owner for confirmation or override.
- [ ] Verification: Record the confirmed answer to each OQ in this plan (inline, below each
      relevant phase) and in the track's metadata/retrospective once the track completes. Do not
      start Phase 2 until OQ-1/OQ-2 are confirmed; do not start Phase 4 until OQ-4/OQ-5 are
      confirmed. [checkpoint marker]

## Phase 2: Merge the two layer toggles (FR-1)

Goal: one layer-panel row controls both the published Martin-tile layer and the draft/proposed
client GeoJSON overlay, styled by status.

Tasks:
- [ ] Task (TDD, per OQ-1's resolution): Write a failing test asserting that toggling
      `activeLayers.interventions` (or whatever the single surviving toggle id is) controls the
      visibility of all six current style layer ids (`interventions`, `interventions-outline`,
      `interventions-points`, `intervention-drafts-fill`, `intervention-drafts-outline`,
      `intervention-drafts-points`) together, and that `intervention-drafts` no longer appears as a
      separately togglable row exposed to the layer panel. Implement by aliasing the drafts
      overlay's visibility read (in `LayerManager.tsx` and wherever `applyVisibility` iterates
      `styleBackedLayerEntries()`) onto the `interventions` toggle, per OQ-1(b) unless the checkpoint
      chose OQ-1(a).
- [ ] Task (TDD, per OQ-2's resolution): Write a failing test for the SQL/migration change to
      `geo.intervention_tiles()` asserting it now projects `category` (and `status`, always
      `'published'`) alongside its existing `priority` column. Implement the migration; note the
      Martin-restart-after-tile-migration step explicitly in this task's completion notes (a
      correct migration with no restart silently serves the old column set).
- [ ] Task (TDD): Write a failing test for a new shared `INTERVENTION_STATUS_COLOR` (or equivalently
      named) style expression asserting: a `pending_review` feature (from either source) paints
      orange regardless of `category`; a `published` feature paints by `category` (land/air); an
      unclassified/missing-category feature falls back to the existing neutral color. Implement by
      replacing `INTERVENTION_DRAFT_COLOR`'s current definition and `interventionsLayer`/
      `interventionsOutlineLayer`/`interventionsPointsLayer`'s `priority`-keyed `fill-color`/
      `circle-color` paint with the shared expression, applied identically across all six style
      layers.
- [ ] Task: Update the layer panel's rendered toggle list and the `interventions` entry's
      `description` copy to describe the merged behavior; remove or unlist the `intervention-drafts`
      registry entry per the Phase 1 checkpoint's OQ-1 answer (confirm whether
      `unreachableLayerToggleIds` or another registry consumer needs an explicit allowance for an
      entry that stays declared but unlisted).
- [ ] Verification: Run the layer-registry and layer-manager test suites; manually confirm (per
      "Never run PlantGeo locally" — test against prod + live Martin) that a signed-in reader with
      an in-review draft sees it orange under the single toggle, a signed-out reader sees only
      published sites in their category color under the same toggle, and the Martin tile source
      actually serves the new `category` column post-restart. [checkpoint marker]

## Phase 3: Click-to-inspect detail panel — geometry and fields (FR-2)

Goal: clicking any intervention feature opens a detail panel with the real geometry and full field
set, without disrupting the AI-analysis click handler or `AiInterventionWorkspace`.

Tasks:
- [ ] Task (TDD): Write a failing test for a new tRPC procedure (e.g.,
      `interventions.getPublishedInterventionDetail`) asserting it returns the full record
      (properties including full geometry, status, reviewNote, createdAt/updatedAt, submitter) for a
      given feature id, applies the same authorization `listMySubmissions`/the drafts overlay already
      apply (a `pending_review` row not owned by or shared with the caller must not be returned by
      id even when guessed), and 404s/NOT_FOUNDs for an id that does not exist or is not visible to
      the caller. Implement.
- [ ] Task (TDD): Write a failing test for new per-style-layer click handlers (mirroring
      `WaterLayer.tsx`'s `map.on("click", layerId, handler)` pattern) on all six merged style layers
      asserting: a click on a drafts-overlay feature resolves its full record from the already-held
      `useInterventionDraftsOverlay` data (no network call); a click on a published-tile feature
      calls the new by-id procedure from the prior task. Implement, mounted from `LayerManager.tsx`
      or a new small hook/component it owns.
- [ ] Task (TDD): Write a failing test asserting `MapView.tsx`'s existing bare click handler (the one
      opening the AI-analysis coordinate popup, gated by `isScalarFieldInspectionAllowed`) stands
      down when the click hits any of the six merged intervention style layers — i.e., add those
      layer ids to whatever allow-list function gates that stand-down, and assert a click on an
      intervention feature does not also open the AI popup.
- [ ] Task (TDD): Write a failing test for the new detail-panel component (per OQ-3's resolution,
      likely a standalone component/store pair, not a mode of `AiInterventionWorkspace`) asserting
      it renders: the actual polygon shape and/or point(s) from the resolved record (not a
      centroid), name, type, category, status, description, submitted-by, created/updated dates, and
      — only when `status === 'rejected'` — the reviewer note. Implement, reading `status`/
      `reviewNote` exactly as `contributions.publishContribution`/`rejectContribution` write them
      (never `castModerationVote`'s vocabulary).
- [ ] Task (TDD): Write a failing test asserting the detail panel opening (via a map click) does not
      close or reset `AiInterventionWorkspace` if it is currently open in either mode — the two
      surfaces coexist per OQ-3.
- [ ] Verification: Run the new tRPC procedure test, the click-handler tests, the detail-panel
      component test, and `map-view-render-count.test.tsx` (to confirm the new click wiring did not
      regress `MapView`'s render-count contract) together; manually click through a published
      polygon, a published point, the caller's own pending-review draft, and (if reachable as a
      signed-in contributor) another contributor's pending-review proposal, confirming the correct
      full geometry and fields render for each, and confirming a rejected intervention (if one
      exists in the test/staging data) shows its reviewer note. [checkpoint marker]

## Phase 4: Comment and like backend (FR-3)

Goal: a generic, feature-id-scoped comment/like data model and tRPC surface exists, ready for a UI
consumer, per OQ-4/OQ-5's confirmed answers from Phase 1.

Tasks:
- [ ] Task (TDD): Write a failing Drizzle schema test / migration test asserting a new likes table
      (unique on `(feature_id, user_id)`) and a new comments table (`feature_id`, `author_user_id`,
      `body`, `created_at`, and a deletion marker/column supporting author-or-admin delete) exist and
      carry a foreign key to `features.id`. Implement the migration.
- [ ] Task (TDD): Write a failing test for a `toggleLike` (or equivalently named) mutation asserting:
      idempotent toggle behavior (call twice, ends up in the opposite state each time), returns the
      caller's own like state and the current total count, requires `contributorProcedure` (or
      equivalent) authentication, and respects FR's visibility-scoping (cannot like a feature the
      caller cannot see, per OQ-4's "who can comment/like on what" answer). Implement.
- [ ] Task (TDD): Write a failing test for a `listComments` query (paginated, feature-id-scoped) and
      a `postComment` mutation (`contributorProcedure`-gated, `body` length-bounded the way
      `submitIntervention`'s `description` is bounded, visibility-scoped the same way `toggleLike`
      is). Implement.
- [ ] Task (TDD): Write a failing test for a `deleteComment` mutation asserting only the comment's
      author or a caller with `platformRole` `expert`/`admin` (mirroring `castModerationVote`'s
      existing role check) may delete it, and that deletion is visible to subsequent `listComments`
      calls (soft- or hard-delete, per the migration's chosen shape). Implement.
- [ ] Task: Confirm (via a short written note in this plan or the track's retrospective) that none of
      the four procedures above write through or duplicate `castModerationVote`/
      `transitionLifecycleState` or `contributions.publishContribution`/`rejectContribution` —
      grep every new procedure's write path against those four existing procedures' tables/columns
      to confirm no overlap.
- [ ] Verification: Run the schema/migration tests and the four procedure test suites together;
      confirm (per NFR-2) that a signed-out or unrelated caller cannot toggle a like or read/post a
      comment against a `pending_review` draft that is not theirs and not in the shared review
      queue, by writing and passing an explicit negative-authorization test for each procedure.
      [checkpoint marker]

## Phase 5: Mount comment/like UI on the detail panel, and full sweep (FR-4)

Goal: the FR-2 detail panel gains a working comment thread and like control backed by Phase 4's
procedures; `/feed`'s own mount is explicitly deferred per OQ-5 unless the Phase 1 checkpoint
decided otherwise.

Tasks:
- [ ] Task (TDD): Write a failing test for the detail panel asserting it renders the current like
      count and the signed-in viewer's own like state, and that clicking the like control calls
      `toggleLike` and updates the displayed count/state without a full panel remount or reload.
      Implement.
- [ ] Task (TDD): Write a failing test for the detail panel asserting it renders existing comments
      (author, timestamp, body) via `listComments`, shows a compose box and working submit for a
      signed-in viewer (mirroring `/feed`'s `SignedOutGate` pattern for a signed-out one), and that a
      successfully posted comment appears in the list without a full panel remount. Implement.
- [ ] Task (TDD): Write a failing test asserting a comment's author, or a viewer with `expert`/
      `admin` `platformRole`, sees a delete affordance on that comment, and that using it calls
      `deleteComment` and removes the comment from the rendered list; a viewer with neither
      permission sees no delete affordance. Implement.
- [ ] Task: Sanitize/escape comment `body` on render per NFR-2, consistent with how this codebase
      already renders other user-authored strings (`description`, strategy-request `title`); add a
      regression test asserting a comment containing markup does not execute/inject.
- [ ] Task: If the Phase 1 checkpoint confirmed OQ-5's default (defer `/feed`'s UI mount), add a
      one-paragraph note to `src/app/feed/InterventionFeed.tsx`'s file-level or section comment
      naming the now-available shared comment/like procedures as a named, ready-to-consume follow-up
      — so the next reader of that file does not have to rediscover that the backend already exists.
- [ ] Verification: One full sweep per `conductor/workflow.md` — run the complete affected-boundary
      test suite (layer-registry, layer-manager, the new tRPC procedures, the detail-panel component,
      `map-view-render-count.test.tsx`), typecheck, and lint once at the end covering every file
      touched across all five phases (per the "one sweep" convention — do not re-run tests after
      each individual task). Manually re-verify every acceptance criterion in FR-1 through FR-4
      against the running app (test against prod + live Martin, per "Never run PlantGeo locally").
      Record which OQ-1 through OQ-5 answers were actually implemented, and whether `/feed`'s UI
      mount was deferred or pulled forward, in the track's retrospective. [checkpoint marker]
