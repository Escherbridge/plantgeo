---
type: Implementation Plan
title: Unified intervention layer with click-to-inspect detail panel
tags: [unified_intervention_layer_20260913]
resource: ./spec.md
---

# Implementation Plan: Unified intervention layer with click-to-inspect detail panel

## September 14 social QA correction

The [long-horizon QA session](../platform_experience_qa_20260911/evidence/runbook-session-20260914.md)
removes the feed's unsupported constant soil-moisture benefit claim and adds regression coverage.
The unified layer, detail modal, likes and comments already exist. Their role, browser, geometry
and publication acceptance remains open; automated synthetic mechanics cannot close the separate
real-human contributor gate. The source candidate remains uncommitted.

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

## Phase 1: Decision checkpoint (no code) — RESOLVED 2026-09-13

- OQ-1 → alias visibility (no registry restructure).
- OQ-2 → migrate + unify styling (category projection, shared status/category paint expression).
- OQ-3 → standalone component, as a large expandable modal (Facebook-lightbox-style: compact card
  that expands to take up most/all of the viewport), not a MapLibre popup, not a workspace mode.
- OQ-4 → per-user-toggle likes, flat comments, no pre-publication comment moderation,
  visibility-scoped access.
- OQ-5 → build BOTH the map detail panel's and `/feed`'s comment/like UI in this pass (not
  deferred — diverges from the spec's original recommendation). Phase 5 below is updated to cover
  both mount points.

[checkpoint marker: decisions recorded in spec.md, plan updated below]

## Phase 2: Merge the two layer toggles (FR-1)

Goal: one layer-panel row controls both the published Martin-tile layer and the draft/proposed
client GeoJSON overlay, styled by status.

Tasks:
- [x] Task (TDD, per OQ-1's resolution): Write a failing test asserting that toggling
      `activeLayers.interventions` (or whatever the single surviving toggle id is) controls the
      visibility of all six current style layer ids (`interventions`, `interventions-outline`,
      `interventions-points`, `intervention-drafts-fill`, `intervention-drafts-outline`,
      `intervention-drafts-points`) together, and that `intervention-drafts` no longer appears as a
      separately togglable row exposed to the layer panel. Implement by aliasing the drafts
      overlay's visibility read (in `LayerManager.tsx` and wherever `applyVisibility` iterates
      `styleBackedLayerEntries()`) onto the `interventions` toggle, per OQ-1(b) unless the checkpoint
      chose OQ-1(a).
- [x] Task (TDD, per OQ-2's resolution): Write a failing test for the SQL/migration change to
      `geo.intervention_tiles()` asserting it now projects `category` (and `status`, always
      `'published'`) alongside its existing `priority` column. Implement the migration; note the
      Martin-restart-after-tile-migration step explicitly in this task's completion notes (a
      correct migration with no restart silently serves the old column set).
- [x] Task (TDD): Write a failing test for a new shared `INTERVENTION_STATUS_COLOR` (or equivalently
      named) style expression asserting: a `pending_review` feature (from either source) paints
      orange regardless of `category`; a `published` feature paints by `category` (land/air); an
      unclassified/missing-category feature falls back to the existing neutral color. Implement by
      replacing `INTERVENTION_DRAFT_COLOR`'s current definition and `interventionsLayer`/
      `interventionsOutlineLayer`/`interventionsPointsLayer`'s `priority`-keyed `fill-color`/
      `circle-color` paint with the shared expression, applied identically across all six style
      layers.
- [x] Task: Update the layer panel's rendered toggle list and the `interventions` entry's
      `description` copy to describe the merged behavior; remove or unlist the `intervention-drafts`
      registry entry per the Phase 1 checkpoint's OQ-1 answer (confirm whether
      `unreachableLayerToggleIds` or another registry consumer needs an explicit allowance for an
      entry that stays declared but unlisted).
- [~] Verification (automated half done; manual prod/Martin half owed): Run the layer-registry and layer-manager test suites; manually confirm (per
      "Never run PlantGeo locally" — test against prod + live Martin) that a signed-in reader with
      an in-review draft sees it orange under the single toggle, a signed-out reader sees only
      published sites in their category color under the same toggle, and the Martin tile source
      actually serves the new `category` column post-restart. [checkpoint marker]

## Phase 3: Click-to-inspect detail panel — geometry and fields (FR-2)

Goal: clicking any intervention feature opens a detail panel with the real geometry and full field
set, without disrupting the AI-analysis click handler or `AiInterventionWorkspace`.

Tasks:
- [x] Task (TDD): Write a failing test for a new tRPC procedure (e.g.,
      `interventions.getPublishedInterventionDetail`) asserting it returns the full record
      (properties including full geometry, status, reviewNote, createdAt/updatedAt, submitter) for a
      given feature id, applies the same authorization `listMySubmissions`/the drafts overlay already
      apply (a `pending_review` row not owned by or shared with the caller must not be returned by
      id even when guessed), and 404s/NOT_FOUNDs for an id that does not exist or is not visible to
      the caller. Implement.
- [x] Task (TDD): Write a failing test for new per-style-layer click handlers (mirroring
      `WaterLayer.tsx`'s `map.on("click", layerId, handler)` pattern) on all six merged style layers
      asserting: a click on a drafts-overlay feature resolves its full record from the already-held
      `useInterventionDraftsOverlay` data (no network call); a click on a published-tile feature
      calls the new by-id procedure from the prior task. Implement, mounted from `LayerManager.tsx`
      or a new small hook/component it owns.
- [x] Task (TDD): Write a failing test asserting `MapView.tsx`'s existing bare click handler (the one
      opening the AI-analysis coordinate popup, gated by `isScalarFieldInspectionAllowed`) stands
      down when the click hits any of the six merged intervention style layers — i.e., add those
      layer ids to whatever allow-list function gates that stand-down, and assert a click on an
      intervention feature does not also open the AI popup.
- [x] Task (TDD): Write a failing test for the new detail-panel component (standalone, per OQ-3: an
      expandable modal that opens as a compact card and can grow to take up most/all of the
      viewport, Facebook-lightbox-style — not a MapLibre popup, not a mode of
      `AiInterventionWorkspace`) asserting it renders: the actual polygon shape and/or point(s) from
      the resolved record (not a centroid), name, type, category, status, description,
      submitted-by, created/updated dates, and — only when `status === 'rejected'` — the reviewer
      note. Implement, reading `status`/`reviewNote` exactly as
      `contributions.publishContribution`/`rejectContribution` write them (never
      `castModerationVote`'s vocabulary).
- [x] Task (TDD): Write a failing test asserting the detail panel opening (via a map click) does not
      close or reset `AiInterventionWorkspace` if it is currently open in either mode — the two
      surfaces coexist per OQ-3.
- [~] Verification (automated sweep done; manual prod/Martin click-through owed): Run the new tRPC procedure test, the click-handler tests, the detail-panel
      component test, and `map-view-render-count.test.tsx` (to confirm the new click wiring did not
      regress `MapView`'s render-count contract) together; manually click through a published
      polygon, a published point, the caller's own pending-review draft, and (if reachable as a
      signed-in contributor) another contributor's pending-review proposal, confirming the correct
      full geometry and fields render for each, and confirming a rejected intervention (if one
      exists in the test/staging data) shows its reviewer note. [checkpoint marker]

## Phase 4: Comment and like backend (FR-3)

Goal: a generic, feature-id-scoped comment/like data model and tRPC surface exists, ready for a UI
consumer, per OQ-4/OQ-5's confirmed answers from Phase 1.

**As built 2026-09-13.** Tables: `geo.feature_likes` (surrogate `id`, `feature_id` FK ->
`geo.features.id` ON DELETE CASCADE, `user_id` FK -> `public.users.id`, `created_at`, UNIQUE
`uq_feature_likes_feature_user (feature_id, user_id)`) and `geo.feature_comments` (`id`,
`feature_id` FK, `author_user_id` FK, `body` text, `created_at`, `deleted_at`,
`deleted_by_user_id` FK ON DELETE SET NULL). Soft delete, filtered by a partial index
`ix_feature_comments_feature_created ... WHERE deleted_at IS NULL`. Migration
`drizzle/0001_feature_social.sql`; `src/lib/server/db/migration-contract.ts` re-pinned in the same
change per `src/lib/server/db/AGENTS.md` (a Phase 2 `0002` later stacked on top and re-pinned
again, which is the same convention applied twice, not a conflict). Procedures live in
`src/lib/server/trpc/routers/intervention-social.ts`, mounted as `interventionSocial`:
`toggleLike`, `getLikeState`, `listComments`, `postComment`, `deleteComment`.

Tasks:
- [x] Task (TDD): Write a failing Drizzle schema test / migration test asserting a new likes table
      (unique on `(feature_id, user_id)`) and a new comments table (`feature_id`, `author_user_id`,
      `body`, `created_at`, and a deletion marker/column supporting author-or-admin delete) exist and
      carry a foreign key to `features.id`. Implement the migration.
- [x] Task (TDD): Write a failing test for a `toggleLike` (or equivalently named) mutation asserting:
      idempotent toggle behavior (call twice, ends up in the opposite state each time), returns the
      caller's own like state and the current total count, requires `contributorProcedure` (or
      equivalent) authentication, and respects FR's visibility-scoping (cannot like a feature the
      caller cannot see, per OQ-4's "who can comment/like on what" answer). Implement.
- [x] Task (TDD): Write a failing test for a `listComments` query (paginated, feature-id-scoped) and
      a `postComment` mutation (`contributorProcedure`-gated, `body` length-bounded the way
      `submitIntervention`'s `description` is bounded, visibility-scoped the same way `toggleLike`
      is). Implement.
- [x] Task (TDD): Write a failing test for a `deleteComment` mutation asserting only the comment's
      author or a caller with `platformRole` `expert`/`admin` (mirroring `castModerationVote`'s
      existing role check) may delete it, and that deletion is visible to subsequent `listComments`
      calls (soft- or hard-delete, per the migration's chosen shape). Implement.
- [x] Task: Confirm (via a short written note in this plan or the track's retrospective) that none of
      the four procedures above write through or duplicate `castModerationVote`/
      `transitionLifecycleState` or `contributions.publishContribution`/`rejectContribution` —
      grep every new procedure's write path against those four existing procedures' tables/columns
      to confirm no overlap.
      **Confirmed 2026-09-13, by enumeration rather than assertion.** Every write in
      `intervention-social.ts` is one of exactly four statements, and all four target the two new
      tables: `delete(featureLikes)`, `insert(featureLikes)`, `insert(featureComments)`,
      `update(featureComments)`. There is no `insert`/`update`/`delete` against `features` in the
      file at all, so the `status` + `reviewNote` columns those four existing procedures write are
      untouched by construction. `geo.features` is read exactly once, in `requireVisibleFeature`,
      projecting `id`, `status` and three `properties ->>` keys — `status` as a *visibility*
      predicate (`published` / `pending_review`), never re-written and never re-interpreted through
      `castModerationVote`'s `approved`/`active`/`monitored` vocabulary, which appears nowhere in
      the file. `review_note` appears nowhere either, including in the migration (asserted by
      `feature-social-schema.test.ts` -> "does not touch the review vocabulary owned by the
      contributions router"). The two new tables are net-new in `0001_feature_social` and have no
      reader or writer outside this router.
- [x] Verification: Run the schema/migration tests and the four procedure test suites together;
      confirm (per NFR-2) that a signed-out or unrelated caller cannot toggle a like or read/post a
      comment against a `pending_review` draft that is not theirs and not in the shared review
      queue, by writing and passing an explicit negative-authorization test for each procedure.
      [checkpoint marker]

## Phase 5: Mount comment/like UI on the detail panel AND /feed, and full sweep (FR-4)

Goal: the FR-2 detail panel gains a working comment thread and like control backed by Phase 4's
procedures; per the Phase 1 checkpoint's OQ-5 resolution, `/feed` gets the same UI in this pass
too, not deferred.

**As built 2026-09-13 — OQ-5 implemented in full, BOTH surfaces, nothing deferred.** Two shared
client components under a new `src/components/intervention/` directory, imported by the map detail
modal and by `/feed` so the two surfaces cannot drift:
`InterventionLikeButton({ featureId, className? })` and
`InterventionCommentThread({ featureId, pageSize?, className? })`, plus
`sign-in-gate.tsx` (`SOCIAL_SIGN_IN_HREF`, `SocialSignInPrompt`) carrying `/feed`'s `SignedOutGate`
treatment inline.

- Like update strategy: **refresh-from-server-response**, not optimistic — `toggleLike` already
  returns the authoritative `{ liked, count }`, so the click applies that response to local state.
  No invented count to roll back, no refetch round trip, and the same DOM node throughout (the test
  asserts node identity across the toggle, which is what "no remount" means here).
- Comment paging: offset-based `Load more comments` driven by `nextOffset`, accumulating pages into
  local state via a merge that returns the SAME array when a page adds nothing (so a query hook
  handing back a fresh object each render cannot spin the effect). Posting appends the returned row;
  deleting drops it locally. Neither remounts the thread.
- Author display: this codebase has **no** user-id -> display-name read for an arbitrary user
  (`teams.listMembers` is team-scoped and is not a directory), so an author renders as `You` or as
  `Contributor <first 8 chars of the id>`. **Known gap**, deliberately not closed here: closing it
  means a new user-lookup surface, which is its own decision.
- Signed-out viewers: both `getLikeState` and `listComments` are `protectedProcedure`, so both
  queries are issued with `enabled: false` when signed out and the viewer sees a sign-in prompt
  rather than an UNAUTHORIZED. The procedures' auth tier was NOT changed.
- Delete gating: `authorUserId === session.user.id || platformRole ∈ {expert, admin}`, read the way
  `moderation/page.tsx` and `TopBar.tsx` read it, mirroring `deleteComment`'s own server-side check.
- `/feed` reuses the Phase 3 modal rather than growing a second comment UI: `ProposalRow` renders
  the shared like button inline and a `Comments on <name>` button that calls
  `useInterventionDetailStore.openById(proposal.id)`; `InterventionFeed` mounts one
  `<InterventionDetailModal />` inside a click-through viewport frame (the modal positions itself
  `absolute` for the map canvas). `/feed` was already entirely behind its `SignedOutGate`, verified
  by test, so no new gating was needed there.
- Escaping (NFR-2): every body renders as JSX text (`{comment.body}`), never
  `dangerouslySetInnerHTML`, pinned by a regression test asserting a `<script>`/`<b>` body appears
  as inert text and executes nothing.

Files: created `src/components/intervention/InterventionLikeButton.tsx`,
`src/components/intervention/InterventionCommentThread.tsx`,
`src/components/intervention/sign-in-gate.tsx`,
`src/__tests__/components/InterventionLikeButton.test.tsx`,
`src/__tests__/components/InterventionCommentThread.test.tsx`,
`src/__tests__/components/InterventionFeedSocial.test.tsx`; modified
`src/components/map/InterventionDetailModal.tsx` (mounts both into the card's `children` slot),
`src/app/feed/InterventionFeed.tsx`, and the two affected test files
(`InterventionDetailModal.test.tsx`, `intervention-detail-coexistence.test.tsx`).

Tasks:
- [x] Task (TDD): Write a failing test for the detail panel asserting it renders the current like
      count and the signed-in viewer's own like state, and that clicking the like control calls
      `toggleLike` and updates the displayed count/state without a full panel remount or reload.
      Implement.
- [x] Task (TDD): Write a failing test for the detail panel asserting it renders existing comments
      (author, timestamp, body) via `listComments`, shows a compose box and working submit for a
      signed-in viewer (mirroring `/feed`'s `SignedOutGate` pattern for a signed-out one), and that a
      successfully posted comment appears in the list without a full panel remount. Implement.
- [x] Task (TDD): Write a failing test asserting a comment's author, or a viewer with `expert`/
      `admin` `platformRole`, sees a delete affordance on that comment, and that using it calls
      `deleteComment` and removes the comment from the rendered list; a viewer with neither
      permission sees no delete affordance. Implement.
- [x] Task: Sanitize/escape comment `body` on render per NFR-2, consistent with how this codebase
      already renders other user-authored strings (`description`, strategy-request `title`); add a
      regression test asserting a comment containing markup does not execute/inject.
- [x] Task (TDD): Write a failing test for `InterventionFeed.tsx`'s `ProposalRow` (or a new row
      component it renders) asserting each feed row shows the same like count/toggle and a comment
      affordance (either an inline compact thread or a link/click-through into the same standalone
      detail modal Phase 3 built, implementer's choice — document which) backed by the identical
      Phase 4 procedures, with the same `SignedOutGate` treatment this file already uses for signed-
      out viewers. Implement. Reuse the Phase 3 modal component if that keeps the two surfaces from
      drifting; do not hand-roll a second comment UI unless there's a concrete reason `/feed`'s needs
      differ.
- [~] Verification: One full sweep per `conductor/workflow.md` — run the complete affected-boundary
      test suite (layer-registry, layer-manager, the new tRPC procedures, the detail-panel component,
      `map-view-render-count.test.tsx`), typecheck, and lint once at the end covering every file
      touched across all five phases (per the "one sweep" convention — do not re-run tests after
      each individual task). Manually re-verify every acceptance criterion in FR-1 through FR-4
      against the running app (test against prod + live Martin, per "Never run PlantGeo locally").
      Record which OQ-1 through OQ-5 answers were actually implemented, and whether `/feed`'s UI
      mount was deferred or pulled forward, in the track's retrospective. [checkpoint marker]

      **Automated sweep run 2026-09-13, all green, once at the end:** `npx tsc --noEmit` clean;
      `node scripts/check-data-boundaries.mjs` clean (12 URL rules, client/server imports,
      observation fabrication); `vitest run` over
      `InterventionLikeButton.test.tsx` (4), `InterventionCommentThread.test.tsx` (9),
      `InterventionFeedSocial.test.tsx` (3), `InterventionDetailModal.test.tsx` (13),
      `intervention-detail-clicks.test.tsx`, `intervention-detail-coexistence.test.tsx`,
      `trpc/intervention-detail.test.ts`, `trpc/intervention-social.test.ts`,
      `security/feature-social-schema.test.ts` (10), `map-view-render-count.test.tsx`,
      `LayerManager.test.tsx` (72), `LayerPanel.test.tsx` (36) — 205 tests, 0 failures.
      Answers actually implemented: OQ-1 alias visibility, OQ-2 migrate + unified paint, OQ-3
      standalone expandable modal, OQ-4 toggle likes + flat comments + visibility scoping, OQ-5
      **pulled forward — both surfaces shipped in this pass, `/feed` not deferred.**
      **Still owed (same manual half as Phases 2 and 3):** the against-prod click-through of FR-1
      through FR-4 on the running app with live Martin. Nothing in this phase can be verified that
      way from a local run, per "Never run PlantGeo locally".
