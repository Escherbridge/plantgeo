---
type: Track Spec
title: Intervention drawing + visibility
description: Add polygon/point drawing to intervention submission, a land/air intervention category with area caps, and map visibility for drafted/proposed interventions.
tags: [feature, intervention_drawing_visibility_20260912, pending]
timestamp: 2026-09-12
resource: ./metadata.json
---

# Intervention drawing + visibility

## Overview

Contributors can currently only drop a single point when recommending an
intervention (`InterventionSubmitModal.tsx`), even though the server-side
geometry validator (`intervention-geometry.ts`) already accepts
Polygon/MultiPolygon. Drafted and proposed interventions are also invisible on
the map: only `status='published'` rows render, through a Martin vector tile
source that is fed exclusively from published, DB-triggered geometry. This
track closes both gaps and adds a typed land/air intervention category with
matching area validation, so the drawing tool has something honest to enforce
against.

## Background

- `src/components/panels/InterventionSubmitModal.tsx:27-32` documents the
  point-only limitation explicitly, pending a drawing tool.
- `src/lib/server/services/intervention-geometry.ts:58-80` already validates
  Point/Polygon/MultiPolygon with a 10,000-vertex ceiling but has no area cap.
- `src/lib/server/db/schema.ts:214-249` (`geo.features`) has only an untyped
  `properties` jsonb column; intervention category is presently a free string
  (`InterventionType` in `src/lib/environmental/intervention.ts:8-13`) with no
  land/air axis.
- `src/lib/server/trpc/routers/interventions.ts:150` (`submitIntervention`) is
  the only mutation that persists geometry; `proposeIntervention` (line 299) is
  a second, geometry-less submission path that must remain out of scope for
  drawn geometry.
- `src/lib/map/layers.ts:77`, `src/lib/map/sources.ts:55`, and
  `src/lib/map/layer-registry.ts:405-409` wire the Martin `intervention_tiles`
  source, which only ever contains published rows.
- No draw library and no `@turf/*` dependency exist in the repo today. A
  hand-rolled spherical-excess `polygonArea` already exists at
  `src/lib/map/measurement.ts:18-31` (display-only, for a measurement tool).
- **Known dependency, not in scope:** the moderation path
  (`castModerationVote`/`transitionLifecycleState`,
  `src/lib/server/trpc/routers/interventions.ts:343+`) never sets
  `status='published'`; the working publish path
  (`contributions.publishContribution`/`rejectContribution`,
  `src/lib/server/trpc/routers/contributions.ts:49-100`) has no mounted UI.
  This track's "published interventions become visible" behavior is gated on
  that fix landing under `conductor/tracks/community_engagement_completion_20260805`.
  This track does not touch that router or mount `ContributionQueue.tsx`.
- `conductor/tracks/mycelium_cloud_seeding_spike_20260802` is a blocked
  research spike (no product, no deployed weather-modification activity
  authorized). It contributes no schema, UI, or category-naming decision this
  track can build on; the air/atmospheric category introduced here must stay a
  generic classification bucket, not a cloud-seeding-specific feature, and
  must not be read as unblocking or duplicating that spike.

## Functional Requirements

### FR-1: Drawing tool for intervention submission
- **Description**: Replace the point-only submission flow with a MapLibre-native
  drawing tool (terra-draw) supporting point and polygon drawing, feeding
  `InterventionSubmitModal`'s `geometry` field.
- **Acceptance Criteria**:
  - User can draw a single point (existing behavior preserved) or a polygon
    (new) before opening/while using the submit modal.
  - Polygon drawing supports add-vertex, undo-last-vertex, and clear/restart.
  - Drawn geometry is converted to the exact `InterventionGeometrySchema`
    shape (`Point` | `Polygon`; `MultiPolygon` out of scope for the draw UI,
    though the schema keeps accepting it for other callers) before being
    passed to `submitIntervention`.
  - Modal disables submit and shows an inline error if no geometry has been
    drawn, or if the drawn polygon is not closed/has fewer than 3 distinct
    vertices.
  - The stale doc comment at `InterventionSubmitModal.tsx:27-32` is rewritten
    to reflect the new capability (no more "point-only... until a polygon
    drawing tool lands").
- **Priority**: P0

### FR-2: Land vs. air intervention category
- **Description**: Add a typed category distinguishing land-based
  interventions (soil amendment, revegetation, prescribed burn, reforestation,
  silvopasture, cover_cropping, biochar, keyline, etc.) from air/atmospheric
  interventions (cloud seeding-style initiatives), independent of the existing
  free-text `type`.
- **Acceptance Criteria**:
  - A new `category` field (`"land" | "air"`) is added alongside `type` in the
    submission input schema and in `properties` (jsonb; no new relational
    column required, consistent with the existing `features` table shape) —
    or, if a first-class column is preferred for indexing/query needs, a
    migration adds `category varchar` with a check constraint, decided in
    planning against the "no custom DB roles, clean up as you go" 2026-08-03
    architecture note.
  - Every existing `InterventionType` value is mapped to exactly one category
    (land, by default, since no air type exists in the codebase today); a new
    `AIR_INTERVENTION_TYPES` list (e.g. `cloud_seeding`) is introduced for the
    air side, gated behind the same submission flow (no cloud-seeding-specific
    UI copy beyond a neutral label).
  - `InterventionSubmitModal` UI groups the type dropdown by category or adds
    a category selector that filters the type options.
  - `listProposed`/`listMySubmissions` read paths expose `category` in their
    projections so the client can style land vs. air differently.
- **Priority**: P0

### FR-3: Area validation by category
- **Description**: Extend `intervention-geometry.ts`'s `superRefine` pattern
  with an area check: land interventions capped at 500 acres; air
  interventions get a separate, much larger or unbounded cap.
- **Acceptance Criteria**:
  - A new validation function takes `(geometry, category)` and computes area
    in acres for Polygon/MultiPolygon geometries (Point geometries have zero
    area and always pass).
  - Land interventions with polygon area > 500 acres are rejected with a
    clear Zod issue message naming the cap and the computed area.
  - Air interventions either skip the area check entirely or use a
    documented, explicitly much larger ceiling (e.g. 50,000 acres) — the
    choice and its rationale must be written down in the module's doc comment,
    not left implicit.
  - Area computation reuses or wraps the existing spherical-excess algorithm
    in `src/lib/map/measurement.ts:18-31` (moved/exported for reuse, or
    duplicated with an explicit note tying the two together) rather than
    adding a `@turf/area` dependency, unless the team explicitly decides
    turf is warranted (call this out as an open question, resolved during
    Phase 2 implementation, not before).
  - Existing vertex-count validation (`MAX_INTERVENTION_GEOMETRY_POSITIONS`)
    is unchanged and continues to run independently of the new area check.
  - Unit tests cover: land polygon under cap (pass), land polygon over cap
    (fail with named message), air polygon far exceeding the land cap (pass),
    a degenerate/self-intersecting polygon (documented behavior, not silently
    wrong), and a Point geometry (always passes area check regardless of
    category).
- **Priority**: P0

### FR-4: Geometry only flows through `submitIntervention`
- **Description**: Confirm and lock in (via tests, not new product surface)
  that drawn geometry can only reach the database through
  `submitIntervention`; `proposeIntervention` remains geometry-less.
- **Acceptance Criteria**:
  - `proposeIntervention`'s input schema is unchanged by this track (no
    `geometry` field added to it).
  - A regression test asserts `proposeIntervention`'s Zod input schema has no
    `geometry` key, guarding against future accidental duplication of the
    submission path.
  - The drawing tool UI is wired only into the `submitIntervention`-backed
    modal; no drawing entry point is added anywhere that calls
    `proposeIntervention`.
- **Priority**: P1

### FR-5: Draft/proposed intervention visibility on the map
- **Description**: Render the current user's own draft/pending submissions
  (`listMySubmissions`) and other visible pending-review items
  (`listProposed`) as a client-side GeoJSON overlay, styled distinctly from
  published interventions (e.g. dashed outline, lower opacity), since they
  are not in the Martin `intervention_tiles` pipeline.
- **Acceptance Criteria**:
  - A new map source/layer pair (e.g. `intervention-drafts-source` /
    `intervention-drafts-layer`) is added outside `DYNAMIC_TILE_SOURCE_IDS`
    (it is a plain GeoJSON source, not a Martin tile source) and registered
    alongside the existing `interventions` layers in
    `src/lib/map/layer-registry.ts`.
  - The overlay renders the caller's own submissions regardless of status
    (`pending_review`, `rejected` optionally excluded — decide and document),
    plus other users' `pending_review` + consented submissions from
    `listProposed`, deduplicated against the caller's own rows by feature id.
  - Land vs. air category and draft-vs-published state are both visually
    distinguishable (distinct stroke/fill/opacity combination); a legend
    entry or tooltip communicates which is which.
  - The overlay only fetches `listMySubmissions`/`listProposed` for
    authenticated users; anonymous/public map view shows only the existing
    published Martin tiles (no regression for logged-out users).
  - This FR's "published interventions render" behavior is unaffected by this
    track and is explicitly NOT re-verified here — that depends on the
    separate publish-path fix noted in Background. This FR only adds the
    draft/proposed overlay; it does not attempt to fix why published rows may
    still be zero in practice.
- **Priority**: P0

## Non-Functional Requirements

### NFR-1: Performance
- The draw library (terra-draw) must be lazy/dynamically imported
  (`ssr: false`, consistent with existing MapLibre component conventions) so
  it never loads for users who never open the submission modal.
- The draft/proposed GeoJSON overlay must not poll; it refreshes on
  submission success and on-demand map data refresh, matching existing
  panel data-fetch patterns (tRPC query invalidation), not a timer.
- Area validation must run in-process (no external service call) and stay
  well under the interactive request budget (target: sub-millisecond per
  polygon, consistent with the existing vertex-count check).

### NFR-2: Security
- Area and category validation are server-side and authoritative; client-side
  drawing constraints (e.g. disabling submit past 500 acres) are a UX
  courtesy only and must not be trusted as the sole enforcement.
- `listMySubmissions`/`listProposed` continue to require authentication
  (`protectedProcedure`) — the new overlay must not introduce a public,
  unauthenticated route that leaks pending-review geometry or centroids.
- The `category` field must be validated server-side against a closed enum
  (`"land" | "air"`); it must not accept arbitrary strings even though it is
  stored in jsonb.

## User Stories

**US-1**: As a contributor, I want to draw a polygon around the actual site
boundary of my proposed intervention, so that reviewers see the true extent
rather than a single pin.
- Given the submission modal is open, When I select the polygon draw tool and
  trace a boundary, Then the modal captures a closed polygon geometry and
  enables submission once a valid name and consent are also present.

**US-2**: As a contributor proposing a cloud-seeding-style initiative, I want
to mark it as an air/atmospheric intervention, so reviewers apply the correct
(much larger) area expectations instead of rejecting it against the 500-acre
land cap.
- Given I select an air-category type in the submission form, When I draw a
  polygon larger than 500 acres, Then the submission succeeds (land cap does
  not apply) up to the air ceiling.

**US-3**: As a signed-in contributor, I want to see my own pending
submissions on the map after I submit them, so I have confidence the
submission was recorded even though it isn't published yet.
- Given I have submitted a polygon intervention, When I view the map while
  signed in, Then my submission renders as a dashed/lower-opacity overlay
  distinct from published interventions.

## Technical Considerations

- **Draw library**: terra-draw is recommended over mapbox-gl-draw because it
  is MapLibre-native (no Mapbox token/license coupling) and has an adapter
  model matching the existing MapLibre v5 setup. Confirm current terra-draw
  version compatibility with MapLibre GL JS 5.2+ during Phase 1 spike before
  committing.
- **Area math**: reuse `polygonArea` from `measurement.ts` (spherical excess,
  returns square meters) and convert to acres (1 acre = 4046.8564224 m^2)
  rather than introducing `@turf/area` as a new dependency, keeping the
  dependency surface unchanged per the "clean up as you go" architecture
  note — unless a MultiPolygon ring-hole case makes the hand-rolled version
  materially wrong, which must be checked with a test before deciding.
- **Category storage**: default to jsonb `properties.category` (no migration)
  unless query/index needs during planning justify a first-class column; if a
  column is added it needs a Drizzle migration plus the matching contract
  update per `plantgeo-migration-contract-coupling` memory.
- **Overlay layering**: the draft/proposed overlay must sit visually above or
  clearly distinct from the Martin `interventions` layers (`layers.ts:373-416`)
  to avoid ambiguity about publication status; z-order and paint properties
  need explicit review, not accidental inheritance from the published style.

## Out of Scope

- Fixing `castModerationVote`/`transitionLifecycleState` or mounting
  `ContributionQueue.tsx` (owned by
  `conductor/tracks/community_engagement_completion_20260805`).
- Any cloud-seeding product decision, culture/organism work, or
  weather-modification claim (owned by, and must not duplicate,
  `conductor/tracks/mycelium_cloud_seeding_spike_20260802`, which remains
  blocked).
- MultiPolygon support in the draw UI (schema keeps accepting it for other
  callers; the interactive tool only needs Point + single Polygon per FR-1).
- Editing/reshaping an already-submitted geometry (draw-once-submit-once;
  no in-place edit workflow in this track).
- Server-side geometry simplification, self-intersection repair, or
  topology validation beyond what already exists (ring closure, vertex
  ceiling) plus the new area cap.

## Open Questions

- Should `category` be a first-class `features` column or stay in
  `properties` jsonb? Recommend deciding in Phase 2 based on whether
  `listProposed`'s existing jsonb-projection pattern is acceptable at current
  query volume, or whether an index is already needed.
- Exact air-category acres ceiling (chose 50,000 as a strawman) — confirm
  with product owner before Phase 2 lands the validator, since it is a
  business/policy number, not a technical one.
- Should rejected submissions ever appear in a user's own draft overlay (as a
  "this was declined" marker), or only pending + the user's own published?
  FR-5 currently scopes this to pending-only by default; confirm during
  Phase 3 UI work.
