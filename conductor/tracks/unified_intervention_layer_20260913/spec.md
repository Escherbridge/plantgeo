---
type: Track Spec
title: Unified intervention layer with click-to-inspect detail panel
description: Merge the published and draft/proposed intervention map layers into one status-styled toggle, add click-to-inspect for full geometry and details on any intervention, and spec (not build) net-new commenting and liking.
tags: [feature, unified_intervention_layer_20260913, pending]
timestamp: 2026-09-13
resource: ./metadata.json
---

# Unified intervention layer with click-to-inspect detail panel

## Overview

Today an intervention exists on the map as up to two entirely separate toggles: "Interventions"
(`geo.intervention_tiles`, Martin-backed, `status='published'` only — the *only* rows anyone but
the submitter can currently see) and "My & Proposed Interventions" (a client-side GeoJSON overlay,
signed-in-only, drawing the caller's own drafts and every `pending_review` proposal, with a
just-landed orange-for-pending-review paint rule). Neither is clickable: nothing on the map today
lets a reader select an intervention feature and see more than what a `match`/`case` paint
expression can encode in a fill colour. This track (1) collapses the two toggles into one, with
status driving style; (2) adds click interactivity so any intervention — regardless of geometry
type or status — opens a detail panel showing its *actual drawn geometry* (not a centroid) plus
every field a reviewer or the submitter can see; and (3) specs, without building, net-new
commenting and liking for that panel, matching whatever "parity with /feed" turns out to mean.

This is a UI/interaction and (for FR-3) a schema-design track. It changes how interventions are
displayed and inspected; it does not change intervention submission, moderation, validation, or
geometry semantics, all owned by prior tracks named below.

## Background

- **The two toggles today, and why "merge" is not free.** `src/lib/map/layer-registry.ts:468-500`
  declares `interventions` (`renderKind: "style"`, Martin source `intervention_tiles`, three style
  layers: fill/outline/points) and `intervention-drafts` (`renderKind: "style"`, client GeoJSON
  source `intervention-drafts-source`, three parallel style layers) as **two separate registry
  entries with two separate `toggleId`s**. `LayerRegistryEntry` (`layer-registry.ts:74-117`) has
  exactly one `styleLayerIds: string[]` per entry and exactly one `warehouseLayerName` — the schema
  assumes one toggle maps to one homogeneous capability. Nothing in the registry today supports one
  toggle backed by two heterogeneous sources (a Martin vector source and a client GeoJSON source)
  with two independent data-fetch/fill lifecycles. "One toggle" therefore requires either (a) a
  registry structural change (a toggle that owns two `styleLayerIds` groups from two sources), or
  (b) collapsing to *one* source's worth of registry plumbing while still drawing both origins'
  bytes through it — see OQ-1.
- **The styling both layers already half-share.** `interventionsLayer`/`interventionsOutlineLayer`/
  `interventionsPointsLayer` (`src/lib/map/layers.ts:370-434`) key colour on `priority`
  (`INTERVENTION_PRIORITY_CLASSES`), a field `submitIntervention` never writes, so nearly every
  submitted, published site renders in the deliberate "not yet prioritised" teal
  (`INTERVENTION_UNPRIORITIZED_POINT_COLOR`, line 366). `interventionDraftsFillLayer`/
  `*OutlineLayer`/`*PointsLayer` (lines 473-514) key colour on `category` (land teal / air purple)
  **unless** `status === 'pending_review'`, in which case a `case` expression (lines 466-471)
  overrides to orange (`INTERVENTION_DRAFT_PENDING_REVIEW_COLOR = "#f97316"`) regardless of
  category. The draft layer's status-driven override is the pattern a merged layer needs to
  extend: published rows have no `category` in their tile properties today (Martin projects
  whatever `intervention_tiles` selects, presently priority-only), so a single status-driven
  expression covering both `published` (solid/normal) and `pending_review` (orange) needs the
  merged source to carry `status` and (if kept) `category` uniformly — see OQ-2.
- **No click-to-inspect exists for interventions; one *does* exist elsewhere on the map, and it is
  not the hover mechanism.** `src/components/map/HoverTooltip.tsx` is exactly what its name says —
  a shared **hover** manager keyed off `TOOLTIP_TAP_LAYER_IDS` with a tap-to-pin fallback for
  no-hover devices (lines 71-142); it is explicitly scoped away from "the richer click popups owned
  by FireLayer/WaterLayer" (its own doc comment, line 73). The real precedent is
  `src/components/map/layers/WaterLayer.tsx`, which registers `map.on("click", "<style-layer-id>",
  handler)` per style layer (lines 499, 542-543, 587) and opens a `maplibre-gl` `Popup` built from
  the clicked feature's tile properties (lines 477-580). That pattern reads directly off whatever
  properties the tile/GeoJSON feature carries — it has no path to "the full original geometry" for
  a feature whose tile payload is simplified or centroid-only, which is exactly the gap FR-2 closes
  for interventions. A second, unrelated click handler already exists on the map surface itself:
  `MapView.tsx:266-285` binds a bare `map.on("click", ...)` / `map.on("contextmenu", ...)` pair that
  opens the AI-analysis coordinate picker, gated by `isScalarFieldInspectionAllowed` so it stands
  down over layers that already claim the click. A per-style-layer intervention click handler must
  be added to that allow-list/stand-down check or the AI popup and the new detail panel will both
  fire on the same click.
- **The botanical occurrence layer's click-to-inspect is the closest existing precedent for
  "click a point, open a panel with the full record," and it is instructive on where state should
  live.** `src/components/map/LayerManager.tsx:583-596` resolves a clicked botanical feature's
  `occurrence_id` against the *already-fetched* feature list held in `LayerManager` itself (not a
  second fetch), then writes the full record into `useBotanicalOccurrenceStore`'s
  `selectedFeature` — the panel that reads that store slice needs no knowledge of MapLibre at all.
  The equivalent for interventions is harder in one respect: the Martin-tile source
  (`intervention_tiles`) only ever carries whatever columns the tile function selects, and MapLibre
  vector tiles are typically simplified for the fill/outline geometry itself — so "click resolves
  against an already-fetched full record" is only true for the client GeoJSON draft/proposed
  overlay (which already holds the caller's full submitted geometry in memory via
  `useInterventionDraftsOverlay`), not for the Martin-tile published layer, where the *feature the
  map drew* may not carry the same vertex-for-vertex geometry the database holds. FR-2 requires a
  server round-trip (by feature id) for the published-tile case specifically — see OQ-1's
  resolution and FR-2's acceptance criteria.
- **`/feed` (`src/app/feed/InterventionFeed.tsx`, `src/app/feed/page.tsx`) is centroid-only, has no
  detail view beyond a name/type/description/date/map-link row, and has neither comments nor
  likes.** `InterventionFeed.tsx:130-133` reads `interventions.listProposed`, which
  (`src/lib/server/trpc/routers/interventions.ts:269-311`) projects `ST_X/ST_Y(ST_Centroid(geom))`
  specifically because "a feed row needs [...] enough to fly the camera to the site without
  shipping every parcel outline to every signed-in reader" (comment, lines 265-267) — full geometry
  was a deliberate non-goal for that surface, not an oversight this track corrects on `/feed`
  itself. **Grepped and confirmed: no comment/like/reaction table, column, or procedure exists
  anywhere in this codebase for any content type** (`src/lib/server/db/schema.ts` has zero matches
  beyond an unrelated code comment containing the word "like"; no `*comment*`/`*reaction*` router
  file exists under `src/lib/server/trpc/routers/`). The requester's "the same way as they should
  be from the feed" therefore does not name an existing pattern to copy — it names a shared
  destination: **both** the new map detail panel and `/feed` should eventually get comment/like
  capability together, built once. This track specs that shared capability's data model and API
  surface; whether `/feed`'s UI is updated to consume it in this track's implementation or a
  follow-up is an explicit open question (OQ-5), not assumed.
- **The moderation-path landmine this track must only read, never re-solve.**
  `conductor/tracks/community_engagement_completion_20260805/spec.md` documents that
  `castModerationVote`/`transitionLifecycleState`
  (`src/lib/server/trpc/routers/interventions.ts:358-452`) write a `status` vocabulary
  (`approved`/`active`/`monitored`, or overloading `pending_review`/`rejected` via a vote) that
  `geo.intervention_tiles` and every other reader **ignore** — the only status transitions that
  actually reach the published map are `contributions.publishContribution` /
  `contributions.rejectContribution` (`src/lib/server/trpc/routers/contributions.ts:49-100`), which
  write `published`/`rejected` and (for rejection) a `reviewNote`. The detail panel this track specs
  must read `status` and `reviewNote` off `geo.features` exactly as those two procedures write them,
  and must not surface `castModerationVote`'s parallel, non-authoritative vocabulary as if it were
  live — doing so would show a reader a status the map itself does not honor.
- **`ai_intervention_workspace_20260913`, shipped this session, is a relevant but not obviously
  reusable shell.** `AiInterventionWorkspace` (per that track's plan, "as built 2026-09-13") is a
  right-edge, keep-alive, two-mode (AI analysis / intervention proposal) surface, explicitly scoped
  by that track's OQ-2 as "a new dedicated surface... it strictly adds AI/intervention-specific
  controls that have no dock equivalent today" — i.e., a narrowly-justified exception to the "one
  manager, no floating surfaces" convention, not a general-purpose panel host. Folding "inspect an
  existing intervention" into it as a third mode would reuse its keep-alive plumbing and its
  existing proximity to intervention state, but would also expand a deliberately narrow exception
  (author/propose) into a browse/read surface (inspect any intervention, including other people's),
  which is a different use case with different auth exposure (see NFR-2). See OQ-3 for the explicit
  recommendation.

## Open Questions

Each open question below is genuinely undecided and gets a recommendation, not a silent default,
following the convention set by the two sibling tracks named in Background.

### OQ-1: Does "one layer" require a layer-registry structural change, or does it fold into the existing Martin-tile toggle?

Two candidate answers:
- **(a) Registry structural change**: extend `LayerRegistryEntry` so one `toggleId` can own two
  (or more) `styleLayerIds` groups from two different sources, each with its own visibility/opacity
  application path. `styleBackedLayerEntries()`, `STYLE_LAYER_TOGGLE_MAP`, and every caller that
  assumes one entry ↔ one homogeneous source (`MARTIN_SOURCE_BY_LAYER_TOGGLE`, the per-layer
  tile-refresh cache-drop) needs auditing for that assumption.
- **(b) Fold the draft overlay into the existing `interventions` toggle's mount, hide the
  `intervention-drafts` toggle entirely**: keep `intervention-drafts-source` and its three style
  layers exactly as they are (still client GeoJSON, still filled by `useInterventionDraftsOverlay`,
  still gated on auth), but stop exposing `intervention-drafts` as its own row in the layer panel —
  its visibility is driven by the same `activeLayers` flag as `interventions`, so flipping one
  toggle shows/hides both the published tile layers and the draft GeoJSON layers together. This
  requires no `LayerRegistryEntry` shape change: `applyVisibility`'s existing per-`styleLayerIds`
  loop already runs once per registry entry, so a second entry sharing one toggle's visibility read
  (rather than owning it) is a small, additive change to whatever component reads
  `layerVisibility["intervention-drafts"]` today (make it also read `layerVisibility.interventions`,
  or literally alias the two flags at the `layer-toggle-context` layer).

**Recommendation: (b).** The requester's own framing — "these should be one layer with states
determining how it shows up" — describes a *reader-facing* merge, not a demand that the registry's
one-toggle-one-source invariant be broken. (a) is real, useful infrastructure (a toggle backed by
heterogeneous sources will recur — e.g., if a future layer needs both a Martin tile and a live
client overlay), but it is a bigger, riskier change than this track's stated scope, and every
caller enumerated above (`MARTIN_SOURCE_BY_LAYER_TOGGLE`, tile-cache refresh, `unreachableLayerToggleIds`)
would need re-verification against the new shape. (b) achieves the exact requested behavior — one
switch, statuses paint differently — by aliasing visibility rather than restructuring the type that
every other layer in the registry also depends on. If a second heterogeneous-source layer is
proposed later, (a) should be built then, generalized across both cases, not built once here for
one caller.

### OQ-2: What single status-driven style expression covers both origins, and does the Martin tile need a new column?

The draft overlay's `INTERVENTION_DRAFT_COLOR` `case` expression (status === 'pending_review' →
orange, else category-based) cannot be reused verbatim against the Martin-tile layers, because
`geo.intervention_tiles` (per `src/lib/map/layers.ts:370-385`'s comment) presently projects
`priority`, not `status` or `category`, onto published features — and a published row is by
definition never `pending_review`, so the orange arm is moot for that source, but the *category*
color-mapping arm needs `category` to exist on the tile to avoid silently falling back to the
unclassified grey/teal for every published site. Two sub-questions:
- Does `geo.intervention_tiles()` need a migration to also project `category` (and, harmlessly,
  `status`, always `'published'` there) so the merged style can key one shared expression across
  both sources?
- Should "solid/normal" for published mean "keep today's priority-driven color" (a second axis
  layered under status) or "switch published styling to the same category-driven palette the
  drafts already use," unifying the whole layer's color semantics onto one property instead of two?

**Recommendation**: migrate `geo.intervention_tiles()` to project `category` alongside the existing
`priority`, and repaint the whole merged layer on `category` (land/air, matching the draft overlay's
existing two-color scheme) with status as the sole *override* for `pending_review` (orange) exactly
as the draft layer already does — retiring the `priority`-keyed `INTERVENTION_PRIORITY_CLASSES`
scheme, since `priority` is written by no current submission path and was already producing the
"not yet prioritised" fallback for nearly every real row (per the Background section's citation).
This is a one-column, additive migration (`category` is already persisted in `features.properties`
for every row `submitIntervention` writes) plus a single shared style-expression module, not two
parallel `case` chains that could drift.

### OQ-3: Is the detail panel a mode inside `AiInterventionWorkspace`, or a separate component?

Options:
- **(a) A third `AiInterventionWorkspace` mode ("Inspect")**: reuses the shell's keep-alive
  plumbing, right-edge placement, and existing proximity to intervention state; costs: that shell
  was deliberately scoped (per its own OQ-2 recommendation) as a narrow author/propose exception to
  "one manager, no floating surfaces" — widening it to also browse/inspect *any* intervention
  (including other users' submissions, and eventually other users' comments) changes its authz
  shape and its "what does this surface own" story in a way that track did not anticipate or scope.
- **(b) A separate, purpose-built detail panel/popup component**, following the
  `WaterLayer`/botanical-occurrence pattern (click → resolve full record → write to a store →
  a panel reads that store slice), placed and sized for what a detail view actually needs (full
  geometry render or summary, every field, and — per FR-3 — a comment thread and a like control,
  which is materially more content than a coordinate-seeded chat/draw pane).

**Recommendation: (b).** The detail panel's job — show any intervention's full record, permit
social interaction on it — is a read/browse/discuss surface, structurally different from
`AiInterventionWorkspace`'s author-a-new-thing job, and has no natural "which of the two existing
tabs does a viewed-but-not-authored record belong under" answer. Building it as its own component
keeps `AiInterventionWorkspace`'s recently-narrowed scope narrow (consistent with that track's own
stated reasoning) and lets the detail panel be sized/laid out for its own content instead of
inheriting a shell built for a chat transcript and a drawing canvas. If a future track wants to
cross-link them (e.g., "open this inspected intervention in the proposal workspace to suggest an
edit"), that is a follow-up, not a reason to merge the two now.

### OQ-4: Comment/like data model — public counts vs. per-user toggle, and comment moderation

Genuinely undecided, since nothing like this exists anywhere in the codebase to extend:
- **Likes**: a per-user toggle (one row per `(user_id, feature_id)`, idempotent like/unlike, count
  derived) is the only model consistent with how every other authenticated write in this codebase
  behaves (`submitIntervention`, `castModerationVote` — always scoped to the acting user, never an
  anonymous counter). **Recommendation: per-user toggle**, not a bare incrementing count — a bare
  count cannot support "un-like," cannot prevent one user inflating a count by repeated calls, and
  has no way to show a viewer whether *they* already liked something, which any reasonable UI needs.
- **Comments**: plain text, author-attributed, timestamped, no threading (matching this codebase's
  general preference for the simplest model that satisfies the stated requirement — nothing here
  asked for nested replies). **Recommendation: flat comments**, threading as an explicit non-goal
  unless a future track asks for it.
- **Comment moderation**: does a comment need its own review queue (mirroring
  `contributions.listPendingReview`), or does authorship + report/delete-by-author-or-admin suffice?
  **Recommendation: no pre-publication review queue for comments** — pre-moderating every comment
  before it is visible would make the feature nearly unusable at any real volume, and nothing in
  `conductor/product.md` names a moderation-heavy community requirement. Instead: comments post
  immediately (author must be signed in, matching `contributorProcedure`'s existing gate), and an
  admin/expert can delete any comment (reusing the existing `platformRole` check pattern from
  `castModerationVote`, not a new role). This recommendation should be confirmed by the product
  owner before Phase 3 of the plan below, the same way the sibling AI-workspace track staged its
  decision checkpoint before committing to a data-model shape.
- **Who can comment/like on what**: should a `pending_review` or `rejected` intervention be
  commentable/likeable at all, or only `published` ones? **Recommendation: comments/likes are
  scoped to whatever the *viewer* is already permitted to see** — a submitter can comment on their
  own `pending_review` draft (visible to them and, per the drafts overlay's existing auth gate,
  other signed-in contributors browsing the review queue), but `published` interventions are the
  only ones any anonymous or unrelated signed-in reader can reach at all, so in practice social
  interaction on non-published rows will be rare and self-limiting rather than needing a separate
  gate to prevent it.

### OQ-5: Does `/feed` gain comments/likes in this track's implementation, or only the map detail panel?

The requester's "the same way as they should be from the feed" is a stated *intent* that both
surfaces converge, not evidence that `/feed` already has anything to copy (confirmed empty, per
Background). Building the shared tRPC procedures and schema once and mounting the UI on both
surfaces in the same implementation pass is more work than shipping the map detail panel alone and
leaving `/feed` as a follow-up. **Recommendation: build the shared data model and tRPC procedures
to serve both surfaces from day one (so there is exactly one comment/like backend, never two), but
scope the UI mount to the map detail panel only in this track's plan, with `/feed`'s consumption of
the same procedures named as an explicit, cheap follow-up** (the backend work is the expensive
part; wiring a second UI consumer onto already-built procedures is comparatively small). This
avoids building a backend that only one surface can reach, without inflating this track's UI scope
to cover a page (`/feed`) the requester did not name a specific complaint about beyond "parity."

## Functional Requirements

### FR-1: One layer toggle for published and draft/proposed interventions, styled by status
- **Description**: Collapse the "Interventions" and "My & Proposed Interventions" layer-panel rows
  into one toggle. Flipping it shows both the Martin-tile published layer and the client GeoJSON
  draft/proposed overlay together; flipping it off hides both. Styling is status-driven: published
  features render in their (per OQ-2) category-based normal/solid style; `pending_review` features
  render orange regardless of category or origin.
- **Acceptance Criteria**:
  - Exactly one row in the layer panel represents interventions (the "My & Proposed Interventions"
    row and its current auth-gated description text are removed or folded into the single row's
    description).
  - Toggling the single row on/off shows/hides all six current style layers (three published,
    three draft) together, per OQ-1's resolution.
  - A `pending_review` feature — whether it is the caller's own draft or another contributor's
    proposal in the drafts overlay — paints the same orange regardless of which underlying source
    (Martin tile vs. client GeoJSON) drew it.
  - A `published` feature paints by category (land/air), matching the draft overlay's existing
    category palette, per OQ-2.
  - The auth-gating behavior of the draft/proposed overlay (`useInterventionDraftsOverlay`'s
    signed-in-only fetch) is unchanged: a signed-out reader sees only published interventions under
    the single toggle, exactly as they would see only the "Interventions" layer today.
- **Priority**: P0

### FR-2: Click any intervention feature to open a detail panel with its full geometry and fields
- **Description**: Clicking any intervention feature on the map — point or polygon, published or
  draft/pending, from either underlying source — opens a detail panel/popup that resolves and
  displays the feature's complete record: the actual drawn geometry (not a centroid or a
  tile-simplified approximation), name, type, category, status, description, submitted-by, created/
  updated dates, and reviewer note when rejected.
- **Acceptance Criteria**:
  - A click on any of the six merged style layers (per FR-1) opens the detail panel, following the
    per-style-layer `map.on("click", layerId, handler)` pattern already established by
    `WaterLayer.tsx`.
  - For a feature drawn from the client GeoJSON draft/proposed overlay, the panel's geometry and
    fields come from the already-in-memory record `useInterventionDraftsOverlay` holds (no extra
    round trip needed) — mirroring the botanical-occurrence click pattern's "resolve against an
    already-fetched record."
  - For a feature drawn from the Martin-tile published layer, the panel fetches the feature's full
    record **by id** from the server (a new or extended tRPC read), because the tile's own geometry
    may be simplified and its projected columns may not include every field the panel must show —
    it must not silently display a simplified/partial geometry as if it were the full one.
  - The displayed geometry renders the actual polygon shape and/or the actual point(s), not a
    single representative point, for every status and origin.
  - `status` and `reviewNote` are read exactly as `contributions.publishContribution` /
    `rejectContribution` write them; the panel never surfaces `castModerationVote`'s
    non-authoritative status vocabulary as if it reflects the feature's real, served state.
  - The map's existing bare click handler (`MapView.tsx:266-277`, which opens the AI-analysis
    coordinate popup) stands down when a click lands on an intervention feature, exactly as it
    already stands down for other inspectable layers via `isScalarFieldInspectionAllowed` (or the
    equivalent allow-list this track adds the intervention layers to) — a single click must not
    open both the AI popup and the intervention detail panel.
  - Clicking an intervention feature while `AiInterventionWorkspace` is open in either mode does not
    close or disrupt that workspace (per OQ-3's separate-component recommendation, the two surfaces
    coexist).
- **Priority**: P0

### FR-3: Comment and like data model and API (backend only in this track; see OQ-5)
- **Description**: Add a net-new, per-user-toggle like and flat-comment capability scoped to
  intervention features, per OQ-4's resolved data model, exposed through new tRPC procedures.
- **Acceptance Criteria**:
  - A migration adds the schema OQ-4 settles on (at minimum: a likes table keyed
    `(feature_id, user_id)` unique, and a comments table with `feature_id`, `author_user_id`, `body`,
    `created_at`, soft- or hard-delete support for admin/expert moderation).
  - New tRPC procedures exist to: toggle a like (idempotent, returns the caller's own like state and
    the current count), list comments for a feature (paginated), post a comment
    (`contributorProcedure`-gated, matching `submitIntervention`'s existing authoring gate), and
    delete a comment (author or `expert`/`admin` `platformRole`, matching `castModerationVote`'s
    existing role check pattern).
  - The like/comment procedures are generically scoped to `feature_id` (not intervention-specific
    table names), so `/feed`'s eventual consumption (OQ-5) and any future content type can reuse
    them without a second schema.
  - No procedure here duplicates or writes through `castModerationVote`/`transitionLifecycleState`;
    reviewer-note and status remain owned exclusively by the `contributions` router.
- **Priority**: P1 (backend); UI mount is FR-4

### FR-4: The map detail panel supports commenting and liking
- **Description**: The FR-2 detail panel includes a comment thread (list + post) and a like control
  (toggle + count), backed by FR-3's procedures.
- **Acceptance Criteria**:
  - The panel shows the current like count and whether the signed-in viewer has liked the feature;
    clicking the like control toggles the viewer's own like without a full panel reload.
  - The panel shows existing comments (author, timestamp, body) and, for a signed-in viewer, a
    compose box to post a new one; a signed-out viewer sees comments read-only with a sign-in
    prompt in place of the compose box (mirroring `/feed`'s existing `SignedOutGate` pattern).
  - An author or an `expert`/`admin` platform role sees a delete affordance on a comment they are
    permitted to remove.
  - Posting a comment or toggling a like does not require closing and reopening the detail panel.
- **Priority**: P1

## Non-Functional Requirements

### NFR-1: Performance
- The merged layer's click handlers must not issue a server round trip for the common case (drafts
  overlay features, already in memory); only the Martin-tile published case fetches, and that fetch
  is by a single feature id, not a re-query of the viewport.
- Comment/like reads for the detail panel are scoped to one feature id each; no N+1 pattern across
  a list of features (this track does not add comment counts to the layer-panel or map hover
  tooltip — only the opened detail panel reads them).

### NFR-2: Security / Privacy
- The detail panel must respect the same visibility boundary the drafts overlay already enforces:
  a signed-out or unrelated signed-in reader must not be able to fetch the full record (including
  submitter identity) of a `pending_review` draft that is not theirs and is not in the shared review
  queue, even by guessing a feature id — the new by-id read procedure (FR-2) must apply the same
  authorization the existing `listMySubmissions`/drafts-overlay read already applies, not a bare
  `features.id` lookup with no status/ownership check.
  self-authored, do so under the same terms they already agreed to when submitting
  (`publicationConsent`) — commenting/liking does not introduce a new consent surface, but the
  spec's OQ-4 "who can comment/like on what" answer must be implemented as a real authorization
  check, not a client-side-only gate.
- Comment `body` is free text from an authenticated user and must be treated as untrusted rendering
  input (escaped/sanitized on display) like every other user-authored string this codebase already
  renders (e.g., `description` on interventions, `title` on strategy requests).

## User Stories

**US-1**: As a signed-in reader, I want to see all interventions — published or pending — in one
layer with color coding, so I don't have to remember two separate toggles to know what's happening
in an area.
- Given the merged intervention toggle is on, When I look at the map, Then I see published sites in
  their category color and pending-review sites in orange, from one switch.

**US-2**: As any reader, I want to click an intervention and see exactly what was drawn and
proposed, so I can evaluate a site without guessing from a colored dot.
- Given an intervention feature (point or polygon, any status) is visible on the map, When I click
  it, Then a detail panel opens showing the real geometry and every field the platform has recorded
  for it.

**US-3**: As a signed-in reader, I want to comment on and like an intervention from its detail
panel, so I can engage with a proposal the same way I would on the feed, without leaving the map.
- Given the detail panel is open for an intervention I'm permitted to see, When I post a comment or
  toggle a like, Then it is saved and reflected immediately without closing the panel.

## Technical Considerations

- **Layer-registry aliasing (FR-1/OQ-1)**: the smallest correct change is making whatever reads
  `layerVisibility["intervention-drafts"]` today (the drafts overlay's enable gate in
  `LayerManager.tsx`, and `applyVisibility`'s style-layer loop) read `layerVisibility.interventions`
  instead, then removing `intervention-drafts` from the layer panel's rendered toggle list (while
  leaving its `LayerRegistryEntry` in place if `unreachableLayerToggleIds` or other registry
  consumers would otherwise flag its removal as a wiring gap — planning should confirm whether the
  entry itself should be deleted or merely unlisted).
- **Style-expression consolidation (FR-1/OQ-2)**: a shared `INTERVENTION_STATUS_COLOR` expression
  (parallel to today's `INTERVENTION_DRAFT_COLOR`) should replace both `INTERVENTION_DRAFT_COLOR`
  and `INTERVENTION_PRIORITY_CLASSES`-based painting, applied identically to all six style layers so
  the published and draft layers cannot visually drift from each other again.
- **Martin migration (OQ-2)**: adding `category` to `geo.intervention_tiles()`'s projected columns
  is a SQL function change plus the standard Martin-restart-after-tile-migration step (see
  `plantgeo-martin-restart-after-tile-migration` institutional knowledge) — the plan must include
  that restart, or the new column will silently not appear despite a correct migration.
  Consider naming an intervention with a `feature_id` while the read model exposes it as `id` —
  the panel and the new comment/like tables should key uniformly on `features.id`.
- **Detail panel data resolution (FR-2)**: the new by-id read procedure for the Martin-tile case is
  a natural, small addition to `interventionsRouter` (e.g., `getPublishedInterventionDetail`), not a
  new router — it reads the same `features`/`layers` tables `listMySubmissions`/`listPendingReview`
  already query, projecting `properties.geometry` in full rather than a centroid.
- **Click-handler placement (FR-2)**: new handlers belong beside `WaterLayer.tsx`'s pattern, likely
  in a new small component or hook mounted by `LayerManager.tsx`, registered per merged style layer
  id, with cleanup on unmount mirroring `WaterLayer.tsx`'s existing `map.off` calls.
- **Comment/like schema (FR-3)**: should live beside `features` in `src/lib/server/db/schema.ts`
  with a foreign key to `features.id`, generic enough (`feature_id`, not `intervention_id`) to be
  reused by a future content type without a second schema, per OQ-5's recommendation.

## Out of Scope

- Any change to intervention submission, geometry drawing/validation, or area caps — owned by
  `intervention_drawing_visibility_20260912`.
- Any change to the moderation vocabulary or `castModerationVote`/`transitionLifecycleState` — that
  landmine is explicitly not re-solved here; this track only reads the fields the real publish path
  already writes correctly.
- Mounting comment/like UI on `/feed` itself in this track's implementation — per OQ-5, the backend
  is shared but the `/feed` UI mount is named as a follow-up.
- Comment threading/replies, comment editing, comment reporting/flagging workflows beyond
  author/admin delete, and any comment moderation queue — all explicitly deferred per OQ-4.
- Any change to `AiInterventionWorkspace`'s existing two modes (AI analysis, propose intervention) —
  per OQ-3, the detail panel is a separate component, not a third mode of that shell.
- Push notifications, email, or any other out-of-band notification for a new comment or like.

## Open Questions

See the "Open Questions" section above (OQ-1 through OQ-5), each with a stated recommendation. None
should be silently defaulted by the implementer; the plan below stages a decision checkpoint before
building against OQ-2's schema/migration choice and OQ-4's comment/like data model, the same way the
sibling `ai_intervention_workspace_20260913` track staged its own OQ-1/OQ-2/OQ-3 checkpoint before
committing to a shape.
