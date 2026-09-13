---
type: Implementation Plan
title: Intervention drawing + visibility
tags: [intervention_drawing_visibility_20260912]
resource: ./spec.md
---

# Implementation Plan: Intervention drawing + visibility

## Overview

Four phases, ordered so validation lands before UI depends on it, and so the
map-visibility work (independent of drawing) can proceed in parallel once
`category` exists: (1) category + area validation on the server, (2) terra-draw
integration in the submission modal, (3) draft/proposed map overlay, (4)
integration hardening + regression guards for the `proposeIntervention`
boundary. Each phase ends with a verification checkpoint.

## Phase 1: Category field + server-side area validation

Goal: `intervention-geometry.ts` and the submission input schema know about
land vs. air categories and enforce area caps, with zero UI changes yet.

Tasks:
- [ ] Task: Write failing tests for `computeInterventionAreaAcres(geometry)`
  (Point -> 0; simple square Polygon of known side length -> expected acres
  within tolerance; MultiPolygon -> sum of parts), then implement it in
  `intervention-geometry.ts` by wrapping/porting `polygonArea` from
  `src/lib/map/measurement.ts` (export it if not already exported) and
  converting m^2 to acres. (TDD: Write test, implement, refactor)
- [ ] Task: Write failing tests for a new `InterventionCategorySchema`
  (`z.enum(["land", "air"])`) and a `LAND_INTERVENTION_TYPES` /
  `AIR_INTERVENTION_TYPES` mapping in `src/lib/environmental/intervention.ts`,
  then implement, mapping every existing `InterventionType` to `"land"` and
  adding at least `"cloud_seeding"` to the air list. (TDD: Write test,
  implement, refactor)
- [ ] Task: Write failing tests for the new area `superRefine` (land polygon
  under 500 acres passes; land polygon over 500 acres fails with a message
  naming the cap and computed area; air polygon at 10,000 acres passes; Point
  always passes regardless of category), then extend
  `InterventionGeometrySchema` usage (or add a wrapping schema/function that
  takes `(geometry, category)`) in `intervention-geometry.ts`, leaving
  `MAX_INTERVENTION_GEOMETRY_POSITIONS` untouched. (TDD: Write test,
  implement, refactor)
- [ ] Task: Decide (per spec Open Questions) whether `category` lives in
  `properties` jsonb or needs a first-class `features` column; if a column is
  added, write the Drizzle migration plus the paired contract update per the
  migration-contract-coupling convention, and a test asserting the migration
  applies cleanly against a scratch schema. (TDD: Write test, implement,
  refactor)
- [ ] Task: Add `category: InterventionCategorySchema` to
  `submitIntervention`'s Zod input in
  `src/lib/server/trpc/routers/interventions.ts`, thread it into the inserted
  `properties` (and/or new column), and validate geometry area against it
  before insert; write a failing integration test first (submit land polygon
  over cap -> TRPCError; submit air polygon over land cap but under air
  ceiling -> success). (TDD: Write test, implement, refactor)
- [ ] Task: Add `category` to `listMySubmissions` and `listProposed`
  projections with a test asserting it round-trips. (TDD: Write test,
  implement, refactor)
- [ ] Verification: Run the intervention-geometry and interventions-router
  test files; confirm land/air cap behavior and category round-trip with real
  assertions, not just "no errors thrown." [checkpoint: server-side category +
  area validation reviewed]

## Phase 2: Drawing tool integration (terra-draw)

Goal: `InterventionSubmitModal` supports drawing a point or polygon and
submits real drawn geometry through `submitIntervention`.

Tasks:
- [ ] Task: Spike terra-draw against the current MapLibre GL JS 5.2+ setup in
  isolation (a throwaway test page or existing map dev harness) to confirm
  adapter compatibility and bundle/dynamic-import behavior before wiring it
  into product code; record the finding (works / needs a pinned version) as a
  one-line note in this plan or the track's metadata.
- [ ] Task: Write a failing component test for a new
  `InterventionDrawControl` (or equivalent) that exposes drawn geometry as
  `InterventionGeometry | null` and a mode toggle (point/polygon/clear), then
  implement it as a thin wrapper around terra-draw, dynamically imported with
  `ssr: false`. (TDD: Write test, implement, refactor)
- [ ] Task: Write a failing test asserting `InterventionSubmitModal` disables
  submit when no geometry is drawn and shows an inline error for an unclosed
  or under-3-vertex polygon, then wire the control into the modal, replacing
  the hardcoded `{ type: "Point", coordinates: [lon, lat] }` payload. (TDD:
  Write test, implement, refactor)
- [ ] Task: Add the category selector/filter to the modal (grouping
  `INTERVENTION_TYPES` by land/air, or a separate category radio/select that
  filters the type dropdown), with a test asserting selecting "air" surfaces
  air-only types. (TDD: Write test, implement, refactor)
- [ ] Task: Rewrite the stale doc comment at
  `InterventionSubmitModal.tsx:27-32` to describe the drawing tool instead of
  the point-only limitation.
- [ ] Task: Write a failing end-to-end-style test (Testing Library, mocked
  tRPC) submitting a drawn polygon through the modal and asserting
  `submitIntervention.mutate` receives the exact drawn `Polygon` geometry and
  chosen `category`, then fix any wiring gaps. (TDD: Write test, implement,
  refactor)
- [ ] Verification: Run the modal/component test suite; manually open the
  submission flow against a dev/staging map and draw one point and one
  polygon submission end to end, confirming both reach the server without
  error. [checkpoint: drawing tool UI reviewed]

## Phase 3: Draft/proposed map overlay

Goal: signed-in users see their own and other pending-review interventions on
the map, styled distinctly from published Martin-tile interventions.

Tasks:
- [ ] Task: Write a failing test for a new data-fetch hook (or existing
  pattern) that merges `listMySubmissions` and `listProposed` results into one
  deduplicated GeoJSON `FeatureCollection`, tagging each feature with
  `isOwn`/`status`/`category`, then implement it. (TDD: Write test,
  implement, refactor)
- [ ] Task: Write a failing test asserting a new GeoJSON source/layer pair
  (e.g. `intervention-drafts-source` / `intervention-drafts-layer`) is
  registered in `src/lib/map/layer-registry.ts` alongside the existing
  `interventions` Martin layers, and is excluded from
  `DYNAMIC_TILE_SOURCE_IDS`, then implement the registration. (TDD: Write
  test, implement, refactor)
- [ ] Task: Style the overlay distinctly (dashed stroke / reduced opacity)
  with land vs. air visually distinguishable (e.g. stroke color or dash
  pattern per category), reusing existing paint-property conventions from
  `layers.ts:373-416`; add a snapshot or property-assertion test on the layer
  spec rather than a visual test. (TDD: Write test, implement, refactor)
- [ ] Task: Gate the overlay behind authentication (only fetch/render for
  signed-in users; anonymous users see only the existing published Martin
  layers), with a test asserting no fetch occurs when unauthenticated. (TDD:
  Write test, implement, refactor)
- [ ] Task: Wire overlay refresh to submission success (invalidate/refetch the
  merged query after `submitIntervention` succeeds) so a just-submitted
  polygon appears without a full page reload; test with a mocked mutation
  success firing the expected invalidation.
- [ ] Verification: Manually sign in, submit a polygon intervention, confirm
  it appears immediately as a dashed overlay; sign out and confirm the
  overlay disappears while published Martin layers remain visible.
  [checkpoint: draft/proposed overlay reviewed, including auth gating]

## Phase 4: Boundary hardening + regression guards

Goal: lock in that geometry only flows through `submitIntervention`, and
close out cross-cutting review.

Tasks:
- [ ] Task: Write a regression test asserting `proposeIntervention`'s Zod
  input schema has no `geometry` key (introspect the schema shape rather than
  relying on TypeScript types alone), so a future edit accidentally adding
  geometry there fails CI loudly. (TDD: Write test, implement, refactor)
- [ ] Task: Grep the UI tree for any call site invoking
  `proposeIntervention` and confirm none of them pass through the new draw
  control; add a comment at `proposeIntervention`'s definition
  (`interventions.ts:299`) cross-referencing this track and the geometry-less
  invariant, mirroring the existing documentation style in that file.
- [ ] Task: Add one line to `conductor/RUNBOOK.md` (or the track's own
  metadata) noting that "published interventions become visible" is gated on
  the separate publish-path fix in
  `conductor/tracks/community_engagement_completion_20260805`, so a future
  reader doesn't mistake this track's overlay for a fix to that bug.
- [ ] Verification: Run the full affected-boundary sweep (intervention
  geometry service tests, interventions router tests, map layer-registry
  tests, submission modal component tests) per workflow.md's scoped-sweep
  guidance; obtain an independent code-review pass (server validation +
  security-sensitive auth gating on the overlay) before marking the track
  complete. [checkpoint: final cross-cutting review, dependency note recorded]
