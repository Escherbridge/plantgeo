---
type: Track Spec
title: Public strategy requests and real display names
description: Reverse the private/team-scoped design of "Strategy requests" so they become public, map-visible, social objects like interventions, and close the known "no user-directory lookup" gap so real display names render everywhere a contributor is identified.
tags: [feature, public_strategy_requests_20260913, pending]
timestamp: 2026-09-13
resource: ./metadata.json
---

# Public strategy requests and real display names

## Overview

Today a "strategy request" (`communityRouter.submitRequest`) writes to a wholly separate,
non-geospatial, privacy-scoped table (`strategy_requests`/`request_votes`) that is never shown on
the map and never readable by anyone but the submitter or their team (`getRequests`). This is the
opposite of the unified, social, map-visible `geo.features` + `feature_likes` + `feature_comments`
system this session just shipped for interventions
(`conductor/tracks/unified_intervention_layer_20260913/`), which has click-to-inspect, per-user
toggle likes, and flat author-attributed comments.

The product owner, reacting to a screenshot of the current private panel, wants strategy requests
to become the same kind of public, discussable, map-visible object as an intervention — "it's fine
if people know who they are because people can make recommendations even if they don't even live
here" — and has separately confirmed both halves of that reaction: (1) merge strategy requests into
the public/social system, and (2) build the missing user-id → display-name lookup and use it
everywhere a contributor is identified (comments, submissions, requests), landed in one pass rather
than three small fixes.

This track changes what "submitting a strategy request" structurally means (private ask → public,
map-visible, discussable object) and adds a first real identity-display surface to a codebase that
currently shows every contributor as "You" or an id fragment. It does not change intervention
moderation, drawing/geometry validation, or the review pipeline owned by prior tracks named below.

## Background

- **The privacy boundary being reversed.** `src/lib/server/trpc/routers/community.ts:162-209`
  (`getRequests`) is `protectedProcedure` scoped to `eq(strategyRequests.userId, userId)` (own,
  no team) or, with a `teamId`, `requireTeamAccess`-gated team membership — there is no code path
  today under which one account can see another unrelated account's strategy request. `submitRequest`
  (`community.ts:76-108`) writes to `strategyRequests`, a plain `pgTable` with bare `lat`/`lon`
  `doublePrecision` columns (`src/lib/server/db/schema.ts:364-376`) — no PostGIS geometry, no
  `geo.features` row, no Martin tile, nothing the map layer pipeline can ever draw. `voteOnRequest`
  (`community.ts:110-160`) writes to `requestVotes`
  (`schema.ts:378-384`, composite PK `(request_id, user_id)`, `onConflictDoNothing`) and then
  increments a **denormalized** `strategyRequests.voteCount` via `sql\`... + 1\``, team-gated and
  with **no un-vote path** — structurally different from `featureLikes`'s per-user toggle
  (`schema.ts:260-278`, unique `(feature_id, user_id)`, count always derived via `count(*)`, delete
  row to unlike). `getPriorityZones`/`priorityZones` (`schema.ts:386-395`) is a DBSCAN-cluster
  rollup over the same private table; it is read-only aggregation, not itself a privacy boundary,
  but it currently only ever summarizes rows the caller could already see.
- **The UI the product owner is reacting to.** `RequestSubmitModal.tsx:112-115` says "this private
  request is not shown on the public map"; the consent checkbox
  (`RequestSubmitModal.tsx:172-181`) says "It is never shown on the map. To propose a site that can
  appear on the map after review, use '+ Recommend' instead." `CommunityDetails.tsx:211-222` and
  `:253-261` repeat this in the panel copy ("Logs a private community request... Requests are
  private to your account... Submitting one never publishes its location, creates a public
  waypoint, or draws anything on the map"). Every one of these strings is now factually the opposite
  of the product decision and must change together with the backend, not be left stale.
- **The target shape, shipped this session.** `interventionsRouter.submitIntervention`
  (`src/lib/server/trpc/routers/interventions.ts:207-271`) writes one `geo.features` row
  (`layerId` resolved from the `interventions` `geo.layers` row, `status = 'pending_review'`,
  full geometry + `submittedByUserId`/`submittedByTeamId`/`publicationConsent` inside `properties`).
  `contributions.publishContribution`/`rejectContribution` (expertProcedure) are the only
  transitions to `published`/`rejected` — `castModerationVote`/`transitionLifecycleState`
  (`interventions.ts:503-597`) are a dead, non-authoritative parallel vocabulary that no reader
  honors; this track must not add a second one. Visibility is one rule, restated three times
  (`isFeatureVisibleTo` in `interventions.ts:152-182`, `requireVisibleFeature` in
  `intervention-social.ts:77-131`, and the same shape in `listMySubmissions`/`listProposed`):
  `published` → everyone; own row → submitter always; team row → re-checked team membership; a
  `pending_review` row with `publicationConsent = 'true'` → every signed-in reader. Every miss is
  `NOT_FOUND`, never `FORBIDDEN` (NFR-2 in the sibling spec, restated here as NFR-2). `feature_likes`/
  `feature_comments` (`schema.ts:260-307`) are generic on `feature_id`, deliberately not
  intervention-specific, "so `/feed`'s eventual consumption... and any future content type can reuse
  them without a second schema" (sibling spec, Technical Considerations) — strategy requests are
  exactly that future content type, if OQ-A resolves toward `geo.features`.
- **The users table, read in full.** `users` (`schema.ts:52-65`) has `id`, `name` (nullable
  `text`), `email` (`text`, unique, not null), `emailVerified`, `image` (nullable `text`, presumably
  an avatar URL — never populated by any code path grepped in this session), `passwordHash`,
  `platformRole`, `verified`, `createdAt`, `activeTeamId`. **There is no username/handle field and no
  separate display-name concept** — `name` is the only candidate, and nothing in this session's grep
  confirms it is ever populated at signup (NextAuth credential/OAuth flows were not read in this
  session; OQ-E flags this as unverified and must be confirmed before Phase 1 closes). No user
  directory/lookup-by-id procedure exists anywhere in `src/lib/server/trpc/routers/`; the closest is
  `teams.listMembers`, which is team-scoped, not a general directory.
- **The two vocabularies, compared.** `STRATEGY_TYPES` in `community.ts:23-30` and
  `RequestSubmitModal.tsx:6-13`/`CommunityDetails.tsx:10-18`: `keyline`, `silvopasture`,
  `reforestation`, `biochar`, `water_harvesting`, `cover_cropping`. `InterventionType` in
  `src/lib/environmental/intervention.ts:10-16`: `reforestation`, `silvopasture`, `cover_cropping`,
  `biochar`, `keyline`, `cloud_seeding`, partitioned into `LAND_INTERVENTION_TYPES` (the first five)
  and `AIR_INTERVENTION_TYPES` (`cloud_seeding` only) via `InterventionCategorySchema` (`land`/`air`).
  Four types are identical in name (`keyline`, `silvopasture`, `reforestation`, `biochar`); one more
  (`cover_cropping`) matches too, just alphabetized differently across the two files — five of six
  strategy-request types already exist verbatim in `InterventionType`. The mismatch is exactly two
  types: `water_harvesting` (strategy-request only, no intervention equivalent) and `cloud_seeding`
  (intervention-only, air-category, no strategy-request equivalent).
- **Geometry.** `strategy_requests.lat`/`lon` are bare `doublePrecision` columns with no PostGIS
  type at all — there is no `geom` column, no trigger populating one, and no way for Martin or the
  vector-tile pipeline to serve a strategy request today. `intervention_drawing_visibility_20260912`
  (read for precedent) added `Point`/`Polygon`/`MultiPolygon` drawing with a
  `MAX_INTERVENTION_GEOMETRY_POSITIONS` vertex ceiling and a 500-acre land / much-larger air-category
  area cap on top of `InterventionGeometrySchema`
  (`src/lib/server/services/intervention-geometry.ts`) — the same validation surface `submitIntervention`
  already reuses (`interventions.ts:68-80`, `225-234`).

## Open Questions

**Resolved 2026-09-13 by the product owner, and both hard preconditions verified directly against
production:**
- **OQ-A/OQ-F → promote into `geo.features`, with real geometry.** Requests reuse the exact
  intervention pipeline (submission, unified layer, click-to-inspect modal) rather than a parallel
  system; a request gets a Point via the same `InterventionGeometrySchema` tool interventions use.
- **OQ-B → retire the private/team-scoped path entirely; public becomes the only mode.** Verified
  against production directly (`strategy_requests`: 3 rows, all from one user; `request_votes`: 0
  rows) — cheap enough to **migrate**, not discard: the 3 existing rows become published
  request-kind `geo.features` rows rather than being dropped.
- **OQ-C → keep votes and likes as separate concepts**, diverging from this spec's original
  recommendation. `request_votes` is NOT replaced by `feature_likes`; it keeps its own
  no-toggle-off, denormalized-count semantics, but its foreign key moves from
  `strategy_requests.id` to `features.id` once the request row lives there.
- **OQ-D → unify the type vocabulary.** Merge into `InterventionType`, adding `water_harvesting`;
  requests stay land-category-only for now (no `cloud_seeding` via the request flow).
- **OQ-E → confirmed safe.** Both current production users have `users.name` populated — the
  precondition holds; proceed with `users.name`-backed display-name resolution as recommended, no
  new column needed.

### OQ-A: Promote into `geo.features`, or build a parallel public table?

- **(a) Promote**: a strategy request becomes a `geo.features` row in a request-flavored layer
  (new `geo.layers` row, e.g. `"strategy-requests"`, or a `requestKind`/`isRequest` property inside
  the existing `interventions` layer — see OQ-D), reusing `submitIntervention`'s exact pipeline
  shape (properties bag, `status` lifecycle, `feature_likes`/`feature_comments` verbatim, the same
  visibility predicate, the same detail-panel component).
- **(b) Parallel table**: a new `geo.strategy_request_features`-style table (or reuse `strategy_requests`
  itself, migrated to carry a `geom`), with its own Martin/GeoJSON source and its own click-to-inspect,
  optionally reusing `feature_likes`/`feature_comments`'s *shape* via a lookalike table or by widening
  those tables to a polymorphic `contentType` + `contentId` key instead of a bare `feature_id` FK to
  `geo.features`.

**Recommendation: (a), promote into `geo.features`, distinguished from an intervention proposal by a
`kind: "request"` (or `"recommendation"`) field in `properties`, not a separate table or a separate
social schema.** The generalized shape `feature_likes`/`feature_comments` already committed to
(`feature_id` FK to `geo.features.id`, documented in the sibling track's Technical Considerations as
built "so... any future content type can reuse them without a second schema") exists specifically to
avoid (b)'s polymorphic-key alternative, which the codebase deliberately did not build. Promoting
also gets the request the entire pipeline for free — the merged layer toggle, click-to-inspect
modal, comment/like backend, visibility rule, Martin serving — with one new discriminator field
rather than a second copy of every one of those systems. The cost is real and must be named plainly:
a "request" (an ask, not a proposal someone is committing to build) becomes structurally identical
to an intervention recommendation, which means it needs geometry (OQ-F) and enters the same
`pending_review` → `published`/`rejected` moderation lifecycle intervention proposals already use —
unless OQ-B keeps a private path alongside it, a request-submitter opts into review the same way an
intervention-submitter does. If the product owner wants requests to skip review and post directly
(the way a comment posts immediately, per the sibling spec's OQ-4), that is a fifth open sub-question
this track should also confirm at the Phase 1 checkpoint: **does a public request enter
`pending_review` like an intervention, or post directly to `published` the way a comment posts
immediately?** This spec recommends **direct-to-published, no review queue** — a strategy request is
an ask/observation ("this area could use X"), not a claim about what was built or a drawn site whose
accuracy matters for the map's factual record the way an approved intervention's does; requiring
expert review before an ask is visible contradicts the "show up and talk about what you're doing"
social framing the product owner used, and mirrors this session's own decision not to pre-moderate
comments (sibling spec OQ-4).

### OQ-B: What happens to the existing private/team-scoped path?

`strategyRequests`/`requestVotes`/`priorityZones` and `communityRouter`'s team-private sharing
(`teamId`, `requireTeamAccess`, editor-only submit, member-only vote) currently support a workspace
sharing a request privately before/instead of going public.

**Recommendation: public becomes the only mode; retire the private path rather than running two.**
The product owner's stated framing — "it's fine if people know who they are... people can make
recommendations even if they don't even live here" — is explicitly arguing against gatekeeping by
locality or team membership, which is what the private/team-scoped path exists to do. Keeping a
"share with my team privately" option alongside "post publicly" adds a second submit flow, a second
consent checkbox, and a second visibility rule to maintain, for a use case the product owner did not
ask to preserve and that the screenshot reaction was specifically against. If a genuine
private-coordination need resurfaces later (e.g., a partner org wants to workshop a request before
publishing it), that is better served by reusing the *existing* `pending_review` + team-visibility
rule interventions already have (a team-authored, unpublished `geo.features` row is already visible
only to that team plus, once consented, the shared review queue) than by keeping a structurally
separate private table alive. Concretely: delete `submitRequest`/`voteOnRequest`/`getRequests`/
`getPriorityZones`/`getRequestById` from `communityRouter` (or the whole router, if nothing else
lives in it — confirm at Phase 1), drop `strategyRequests`/`requestVotes`/`priorityZones` in a
migration, and route all new submissions through the (possibly renamed/extended) interventions
pipeline. **This is the one irreversible step in this track (a table drop) and must be confirmed at
the Phase 1 checkpoint before the migration lands**, mirroring the sibling track's own
irreversible-migration gate.

### OQ-C: Do `requestVotes` and `feature_likes` merge into one mechanism?

Already settled by OQ-A/OQ-B: once a request is a `geo.features` row, it has no separate vote
concept to preserve — **`feature_likes`'s existing per-user toggle becomes the request's only
"support" signal**, replacing `requestVotes`'s no-toggle-off, denormalized `voteCount` outright.
This is a behavior change worth naming explicitly: a strategy request can currently accumulate an
ever-growing vote count with no way to retract a vote; under the merged model, "supporting" a
request becomes toggleable, exactly like liking an intervention. If preserving "requests sort by
total historical support, never decreasing" matters as a distinct product property from "current
like count," that is a **new, unasked-for requirement** — this spec assumes it does not, since
nothing in the product owner's stated intent asked for it, and recommends closing `requestVotes`
without replacement rather than inventing a hybrid.

### OQ-D: Do the two type vocabularies unify?

**Recommendation: unify onto `InterventionType`, extended with `water_harvesting` as a new
land-category member; do not add `cloud_seeding` anywhere near "strategy request" UI.**
Concretely: add `"water_harvesting"` to `InterventionType`'s union and to
`LAND_INTERVENTION_TYPES` in `src/lib/environmental/intervention.ts`, then have both the request
submission form and the existing intervention submission form share one `InterventionTypeSchema`
(the request form simply omits `cloud_seeding` from its rendered `<select>`, or the request kind is
restricted to `category: "land"` entirely — since every one of the six original strategy-request
types is land-category, and nothing in the product owner's framing asked for an "air-category
request," restricting request submission to land types is the more defensible default; confirm at
Phase 1 if air-category requests should be reachable at all). This is a one-member, additive union
change (`water_harvesting` needs no migration of existing rows — the eight rows, if any exist in
`strategy_requests`, are handled by whatever backfill OQ-B's migration performs, see Technical
Considerations) rather than maintaining two parallel enums that a submission form and a display
label map (`STRATEGY_TYPES`/`STRATEGY_LABELS`/`STRATEGY_COLORS` in `CommunityDetails.tsx:6-38` and
`RequestSubmitModal.tsx:6-13`, vs. `INTERVENTION_TYPE_LABELS` in `CommunityDetails.tsx:46-52`) must
otherwise keep in sync by hand — that duplication is exactly what produced the current mismatch.

### OQ-E: What does "real display names" resolve to, concretely, and where does it land?

`users.name` (`schema.ts:54`, nullable `text`) is the only existing candidate field; there is no
separate display-name or username column. **Recommendation: resolve display name as `users.name`
when non-null and non-empty, falling back to a deterministic, non-identifying label
(`"Contributor " + first 8 chars of the user id`, i.e. keep today's fallback string exactly, do not
invent a new one) when `name` is null/empty — do not add a new column or an onboarding step in this
track.** Justification: the product owner's ask is "show real display names," and `users.name`
already exists and is plausibly populated by whatever NextAuth provider config this app uses (OAuth
providers typically populate `name` from the identity provider automatically; a credentials-only
signup path may leave it null — **this must be verified against the actual auth/signup code before
Phase 1 closes; it was not read in this session and is a hard precondition for OQ-E**, since if
`name` turns out to be reliably null for most accounts, "real display names" needs a signup-form
field addition instead, which is materially bigger scope). Concretely, this needs:
- **One new tRPC procedure** (a small, generic user-directory read — e.g.
  `users.getDisplayNames(input: { userIds: string[] })` returning `{ id, name, image }[]` for a
  bounded batch, or a `users.getDisplayName(input: { userId: string })` singular form) that every
  comment thread, every submission list, and every strategy request card can call. It must be
  narrowly scoped: **only `name`/`image`, never `email` or any other column** — `email` is
  unique-indexed account-identifying PII with no product reason to be exposed to another user, and
  exposing it would be a new, unrequested privacy regression sitting right next to a track whose
  whole point is deliberately relaxing a *different* privacy boundary; conflating the two must not
  happen. Auth tier: `protectedProcedure` (signed-in readers only — this codebase already gates
  every comment/like read behind `protectedProcedure`/`contributorProcedure`; a real-name directory
  should not be reachable by a signed-out crawler even though the underlying feature/comment content
  may itself be `publicProcedure`-visible in the intervention detail case — confirm this asymmetry
  is acceptable at Phase 1, since it means a signed-out reader sees a comment body but an id
  fragment instead of a name).
- **Three call sites to thread through in one pass** (per the product owner's explicit "land all
  three consistently... rather than three separate small fixes"):
  1. `InterventionCommentThread.tsx`'s `authorLabel` (`:236-238`) — replace the id-fragment fallback
     with a resolved name, batched per rendered page of comments (one `getDisplayNames` call per
     page load, keyed by the distinct `authorUserId`s on that page, not one call per comment).
  2. Intervention submission surfaces that currently show "submitted by" only as a raw id or omit it
     (`InterventionDetailRecord.submittedByUserId`, rendered in the detail modal) — resolve and show
     the name there too.
  3. Strategy requests, once public (OQ-A) — the request card and its detail view show the
     submitter's name the same way.
- **Caching**: a `getDisplayNames` batch read is a small, cheap query
  (`select id, name, image from users where id = any($1)`) and does not need Redis caching in this
  track's scope; if comment-heavy pages start issuing many redundant calls, that is a follow-up
  perf pass, not a Phase-1 blocker.

### OQ-F: Does a public request need real geometry?

Follows directly from OQ-A: once a request is a `geo.features` row rendered by the same merged
layer/detail-panel pipeline an intervention uses, it needs the same `geom`/`properties.geometry`
shape that pipeline expects — a bare `lat`/`lon` pair has no path onto a Martin tile or the client
draft overlay. **Recommendation: yes — reuse `InterventionGeometrySchema` and the same drawing tool
(`intervention_drawing_visibility_20260912`'s polygon/point picker), but default the request
submission flow to a single Point** (the existing `RequestSubmitModal` only ever collects a
lat/lon pin today, never a drawn shape) **rather than forcing every request-submitter through the
polygon-drawing UI.** A request is an ask about an area, not a claim about an exact parcel boundary
the way an intervention recommendation is; requiring a drawn polygon for every request would raise
the submission bar for a lighter-weight social action the product owner explicitly wants to be easy
("people can make recommendations even if they don't even live here" implies a low-friction ask, not
a surveyed site). The existing area-cap validation
(`intervention_drawing_visibility_20260912`'s 500-acre land cap) does not apply meaningfully to a
Point (zero area per that track's own spec, line 113), so a Point-only request needs no new cap
logic; if a future track wants to let a request-submitter optionally draw a polygon area of
interest, that reuses the same `InterventionGeometrySchema` validator already in place with no
schema change.

## Functional Requirements

### FR-1: Strategy requests become public `geo.features` rows on the unified intervention layer
- **Description**: `submitRequest` is replaced by (or folded into) `submitIntervention`-shaped
  submission that writes a `geo.features` row tagged as a request (`properties.kind = "request"`,
  per OQ-A), with a Point geometry (per OQ-F) and the unified `InterventionType` vocabulary (per
  OQ-D). Per OQ-A's recommendation, the row is written directly with a `published`-equivalent
  status (no review queue) rather than entering `pending_review`.
- **Acceptance Criteria**:
  - A submitted request appears on the unified intervention/request map layer (or a `kind`-filtered
    view of it, per whatever the Phase 1 checkpoint settles for OQ-A/OQ-D's layer question) without
    requiring expert publication.
  - The submission is visible to every reader, signed in or not, exactly as a `published`
    intervention is (per the reused `isFeatureVisibleTo` rule).
  - The submitted geometry (a Point at minimum) renders on the map and in the click-to-inspect
    detail panel; it is not merely a non-geospatial `lat`/`lon` pair.
  - The submission requires `contributorProcedure` (signed-in), matching every other authored write
    in this codebase.
  - `strategyType` accepts the unified `InterventionType` vocabulary including the newly added
    `water_harvesting`.
- **Priority**: P0

### FR-2: Strategy requests are commentable and likeable via the existing feature-social backend
- **Description**: A public strategy request's `geo.features.id` is a valid `feature_id` for
  `interventionSocial.toggleLike`/`getLikeState`/`listComments`/`postComment`/`deleteComment` with
  no procedure changes — the existing generic, feature-id-scoped design already supports this.
- **Acceptance Criteria**:
  - Liking/commenting on a strategy request feature works through the exact same tRPC procedures an
    intervention uses, with no new backend code beyond what FR-1 already wires up via `geo.features`.
  - The click-to-inspect detail panel (`InterventionDetailModal`/`InterventionDetailRecord`) opens
    for a request feature the same way it opens for an intervention, distinguishing a request from a
    recommendation visually/textually (per `properties.kind`) but reusing the same modal component.
- **Priority**: P0

### FR-3: The old private strategy-request path is retired
- **Description**: Per OQ-B's recommendation, `strategyRequests`/`requestVotes`/`priorityZones` and
  the corresponding `communityRouter` procedures (`submitRequest`, `voteOnRequest`, `getRequests`,
  `getPriorityZones`, `getRequestById`) are removed; `RequestSubmitModal.tsx` and the "Strategy
  requests" section of `CommunityDetails.tsx` are replaced by (or merged into) the public submission
  flow FR-1 provides.
- **Acceptance Criteria**:
  - A migration drops `strategy_requests`, `request_votes`, `priority_zones` (after confirming, at
    the Phase 1 checkpoint, that no other reader depends on `priority_zones` beyond
    `getPriorityZones` itself — grep confirms none as of this spec's writing).
  - No UI copy anywhere states a strategy request is private, team-only, or "never shown on the
    map" — every such string identified in Background is corrected or removed.
  - `communityRouter` is deleted entirely if nothing besides the five retired procedures lives in
    it (confirm at Phase 1 by re-reading the file's full procedure list); otherwise only the five
    procedures are removed.
- **Priority**: P0

### FR-4: Real display names resolve and render at every contributor-identifying surface
- **Description**: Per OQ-E, a new `users.getDisplayName(s)` procedure resolves `users.name` (with
  the existing id-fragment fallback when null) for a batch of user ids; comment authorship,
  intervention/request submitter display, and any other "who submitted this" surface use it instead
  of the current "You"/id-fragment scheme.
- **Acceptance Criteria**:
  - `InterventionCommentThread`'s author label shows the resolved `users.name` for every comment
    whose author has a non-null name; the existing `"You"` shortcut may remain for the viewer's own
    comments as a UX nicety, but a name must be preferred over it when available (confirm at Phase 1
    whether "You" is kept as a viewer-only override or replaced by the viewer's own real name too —
    recommend keeping "You" for the viewer's own comments specifically, since it is a clearer signal
    than seeing one's own name repeated down a thread).
  - The intervention/request detail panel shows the submitter's resolved name (or the fallback) for
    `submittedByUserId`, wherever that field is currently rendered or newly surfaced.
  - The display-name procedure exposes only `id`, `name`, `image` — never `email` or any other
    column — and requires a signed-in caller (`protectedProcedure`).
  - A batch of comment authors on one rendered page issues at most one `getDisplayNames` call, not
    one call per comment.
- **Priority**: P0

## Non-Functional Requirements

### NFR-1: Performance
- Display-name resolution batches by distinct user id per page/panel render; no N+1 pattern across a
  comment thread, submission list, or request feed (mirrors the sibling track's NFR-1 for
  comment/like reads).
- Retiring `strategyRequests`/`requestVotes` removes the `priorityZones` DBSCAN rollup's data
  source; if any scheduled job computes `priority_zones`, it must be updated or retired in the same
  change (confirm at Phase 1 whether such a job exists — none was found in this session's grep of
  `community.ts`/`community-activity.ts`, but `summarizeStrategyActivity` was not read in full).

### NFR-2: Security / Privacy
- The retired private/team-scoped visibility boundary is not silently reintroduced by accident: once
  FR-3 lands, no code path may still gate a strategy request's visibility by `teamId`/`userId`
  ownership the way `getRequests` did — the reused `isFeatureVisibleTo` rule (published → everyone)
  is now the only rule, since OQ-A recommends direct-to-published with no review queue.
  A miss on any by-id read remains `NOT_FOUND`, never `FORBIDDEN`, matching the existing convention.
- The new display-name directory procedure exposes strictly `id`/`name`/`image`; it must be
  code-reviewed specifically for accidental over-selection (e.g. a `select *` that leaks
  `email`/`passwordHash`/`platformRole`) given that this track is otherwise actively *reducing*
  privacy protections elsewhere in the same change — the two must not be conflated, and a reviewer
  should treat any widening of the users-read surface as its own scrutiny point.
- Comment/request `body`/`title`/`description` continue to render as escaped JSX text, never
  `dangerouslySetInnerHTML` (carried over from the sibling track's NFR-2, unchanged here).

## User Stories

**US-1**: As a signed-in contributor, I want to submit a strategy request and have it show up on the
public map immediately, so other contributors — local or not — can see it and respond.
- Given I submit a request with a type, title, description, and a map point, When I submit, Then the
  request appears on the map layer visible to every reader, without waiting for expert review.

**US-2**: As any reader, I want to click a strategy request on the map and see who asked for it and
what they said, so I can evaluate and respond with real context.
- Given a strategy request feature is visible on the map, When I click it, Then a detail panel opens
  showing the request's fields and the submitter's real display name (or the existing fallback if
  they have none set).

**US-3**: As a signed-in reader, I want to comment on and like a strategy request the same way I can
on an intervention, so requests are genuinely part of the same social system.
- Given the detail panel is open for a strategy request, When I post a comment or toggle a like,
  Then it is saved using the same backend an intervention comment/like uses, and my real name is
  shown next to it.

**US-4**: As a returning contributor, I want to see other people's real names on their comments and
submissions instead of "Contributor a1b2c3d4," so the platform feels like people talking to people.
- Given another contributor has a `name` set on their account, When I view their comment or
  submission, Then I see their name, not an id fragment.

## Technical Considerations

- **Layer/kind discriminator (OQ-A/D)**: adding `properties.kind: "request" | "recommendation"`
  (defaulting existing/new intervention rows to `"recommendation"` for backward compatibility) is the
  smallest schema-free way to distinguish the two without a new `geo.layers` row; if the merged
  layer's style/click-handler code needs to visually distinguish requests from recommendations (a
  request marker style vs. an intervention marker style), that is a `layers.ts` paint-expression
  addition parallel to the existing status/category expression from the sibling track, not a new
  layer toggle — confirm this specific styling question at Phase 1 rather than defaulting silently.
- **Migration ordering (OQ-B)**: the `strategy_requests`/`request_votes`/`priority_zones` drop and
  any `InterventionType` union extension (`water_harvesting`, OQ-D) should land in the same migration
  pass so there is never a window where the vocabulary exists in code but not in the enum a new
  request write validates against.
- **Data loss acknowledgment (OQ-B)**: dropping `strategy_requests` discards any existing private
  requests rather than migrating them forward into `geo.features` as already-`published` rows. If
  real production data exists in that table, a one-time backfill (insert one `geo.features` row per
  existing `strategy_requests` row, `properties.kind = "request"`, Point geometry from the existing
  `lat`/`lon`) is the alternative to outright deletion — **this spec recommends backfilling rather
  than discarding**, since a submitter's past ask suddenly vanishing is a worse experience than it
  becoming public (which is exactly the direction the product owner wants going forward); confirm
  at Phase 1 whether production has any real rows worth backfilling before deciding whether this
  step is needed at all.
- **Display-name procedure placement (OQ-E)**: lives naturally as a new `users` router (or a new
  procedure on an existing one, if a `users` router already exists — grep at Phase 1) rather than
  folded into `interventionSocial` or `interventions`, since it is a cross-cutting read every content
  type needs, not intervention-specific.
- **Reused validators**: `InterventionGeometrySchema`, `getInterventionAreaCapIssue`,
  `MAX_INTERVENTION_GEOMETRY_POSITIONS` (`src/lib/server/services/intervention-geometry.ts`) apply
  unchanged to a Point-geometry request per OQ-F; no new geometry-validation code is needed.

## Out of Scope

- Any change to intervention moderation, `castModerationVote`/`transitionLifecycleState`, or the
  `pending_review` → `published`/`rejected` pipeline for actual intervention recommendations — this
  track only adds a `kind: "request"` sibling that (per OQ-A) skips that pipeline entirely, it does
  not alter it.
- A new signup-time display-name field or onboarding step — OQ-E resolves to reusing the existing
  `users.name` column; if Phase 1's verification finds `name` is reliably null in practice, adding a
  signup field is named as a required follow-up, not built here.
- Avatars/`users.image` rendering polish beyond passing the field through the new directory
  procedure — no new avatar-upload capability is added.
- Notifications (push/email) for a new comment, like, or request — unchanged from the sibling
  track's own out-of-scope list.
- A `getPriorityZones`-equivalent rollup over the new public request data — the DBSCAN
  cluster-summary feature is retired with the private table (per FR-3) and not rebuilt against
  `geo.features` in this track; a future track can revisit "priority zones" as a `geo.features`
  aggregation if the product still wants it.

## Open Questions

See "Open Questions" above (OQ-A through OQ-F), each with a stated recommendation. Two items inside
those recommendations are flagged as **hard preconditions that must be verified, not assumed, before
Phase 1 closes**: (1) whether `users.name` is actually populated by the current NextAuth signup/OAuth
flow (OQ-E), and (2) whether any production rows exist in `strategy_requests` worth backfilling
rather than discarding (OQ-B's migration). Everything else follows the convention set by
`unified_intervention_layer_20260913`: a decision checkpoint before any schema/migration work lands,
staged in the plan below.
