---
type: lane-contract
slug: interventions
horizon: none
---

# interventions lane

Source-of-truth spec for the `interventions` layer lane, one of the eleven
layers named in `conductor/RUNBOOK.md` §0.24.2 (`conductor/RUNBOOK.md:3336-3348`)
and governed by `conductor/code_styleguides/layer-lanes.md`. This document does
not assert any Parquet path layout, filename, or column list — that contract is
being written concurrently by another agent, and (see §8) it is not needed
here regardless. Where the repo does not establish a fact, it is marked
`UNVERIFIED` with what would confirm it.

**This lane is architecturally different from the other ten.** It has no
upstream feed to describe, and — the single most valuable thing this document
establishes — it does not migrate to Parquet at all. See §8.

## 1. Source system

There is no upstream provider. Every row is authored by a person (or,
dormant, a partner integration) through a moderation workflow, not pulled
from an external feed. `geo.layers` describes the layer as "Ecosystem
intervention sites" (`drizzle/0001_handy_riptide.sql:316`, seeded
`is_public = true`).

`src/lib/server/trpc/routers/interventions.ts:18-40` documents three writers
as deliberately kept apart — read this comment before touching the lane, it
is the router's own map of itself:

1. **`interventions.submitIntervention`** (`interventions.ts:150-201`) — the
   interactive, NextAuth-session path. `contributorProcedure`; forces every
   row to `status = 'pending_review'` (the `RECOMMENDATION_STATUS` constant,
   `interventions.ts:52`); requires `publicationConsent: z.literal(true)`
   (`:164`); validates geometry through `BoundedInterventionGeometrySchema`,
   which reuses the machine-ingress validator under an interactive vertex
   ceiling of `MAX_INTERVENTION_GEOMETRY_POSITIONS = 10_000`
   (`src/lib/server/services/intervention-geometry.ts:80`). `type` is one of
   `reforestation | silvopasture | cover_cropping | biochar | keyline`
   (`interventions.ts:79-85`).
2. **`src/app/api/ingest/interventions/route.ts`** — `INGEST_SECRET`
   bearer-token machine ingress, described in-repo as "a future
   automated/partner feed" (`interventions.ts:33-34`). See §5.3 for a live
   defect in this path.
3. **`contributions.publishContribution` / `rejectContribution`**
   (`src/lib/server/trpc/routers/contributions.ts:49-100`, both
   `expertProcedure`) — per the router's own comment, "the only transitions
   out of review." This is not itself a submission path; it is the exit
   gate, and §5.1 is about why it is unreachable.

**Not documented in that three-writer comment, and this omission matters:** a
fourth and fifth writer exist in the same file —
`interventions.proposeIntervention` (`:299-333`) and
`interventions.castModerationVote` / `transitionLifecycleState`
(`:338-432`) — a second, parallel moderation surface the router's own header
comment never acknowledges. §5.1 is the consequence.

**UI**: two competing components exist for review, and only one is
reachable. `src/components/panels/ContributionQueue.tsx` is wired correctly
to `contributions.ts` but is mounted nowhere under `src/app/**` (confirmed by
a repo-wide grep — only its own file and its own test reference it, unchanged
since this was first recorded 2026-08-05,
`conductor/tracks/community_engagement_completion_20260805/spec.md:52-53`).
`src/components/panels/ModerationPanel.tsx` is mounted at the only
expert-gated route in the app, `src/app/moderation/page.tsx` (role-gated to
`expert`/`admin`, `:11,17-20`), and is wired to `castModerationVote` /
`transitionLifecycleState` instead. §5.1 traces the consequence in full.

**Consent** is the platform's actual publication-rights mechanism for this
lane: `publicationConsent` is persisted on the row (`interventions.ts:195`)
and re-checked by `listProposed` (`:286`), not merely validated at submit
time and discarded.

## 2. Cadence

Demand-driven, not scheduled — there is no cron, no cadence to declare in the
usual sense. The Python ingestion-side validation catalog classifies this
lane `kind="reference"` with no `publication_cadence_days`
(`services/agri-data-service/src/agri_data_service/ingest/validation/models.py:146`,
field default `None` at `:55`), which means it also receives **zero**
staleness/gap alarm from `validate-streams` — the same finding
`conductor/RUNBOOK.md:2482` (item 4c) records for all four `kind="reference"`
streams (soil-survey, watersheds, burn-severity, interventions).

That classification is shared with three genuinely static layers, but
`interventions` is idle for a different reason and a wave-2 agent should not
conflate the two: soil-survey/watersheds/burn-severity are `reference`
because their **upstream** is static or slow-moving; `interventions` is
`reference` because **demand** is idle — the count of submissions on any
given day is legitimately, correctly zero for indefinite stretches, and that
is not a data-quality gap to alarm on. Forcing a cadence declaration onto
this lane (e.g. "expect N submissions/day") would manufacture false alarms
against a rate that has no obligation to be non-zero.

## 3. Historical horizon

**Zero rows have ever reached `status = 'published'`.** Measured 2026-08-21
(`conductor/RUNBOOK.md:2967`, `:3345`) — the layer has been empty on purpose
since it was seeded 2026-08-04 under an explicit owner directive never to
seed it (`conductor/tracks/community_engagement_completion_20260805/spec.md:21-23`,
reaffirmed as a non-goal at `:101`: "Seeding interventions. Not in any phase,
for any reason, including demos."). There is no historical time series for
this lane and none is expected — a "how many were published on day X" axis
would read zero for every day since inception.

**Currently 2 rows sit at `status = 'approved'`, 0 published** — found by a
2026-08-22 track audit
(`conductor/tracks/community_engagement_completion_20260805/metadata.json`,
`pivot_reconciliation_20260822.phase_status_20260822.phase_1_moderation_loop`)
and independently corroborated by `conductor/RUNBOOK.md` at multiple points
in the same session (`:236`, `:747`, `:755`, `:810`, `:873`, `:1583`,
`:3345`). §5.1 is the full trace of why.

**Row provenance of those 2 rows is genuinely unverified from the repo, and
one existing RUNBOOK claim about them does not hold up under a closer trace —
flagging this so it is checked with a query, not assumed either way.**
`conductor/RUNBOOK.md:932-933` reports the 2 rows have `NULL geometry_id` and
no `properties.id` key, and calls this "confirming they are ungoverned seed
rows, not producer output." Traced against the actual writers, that evidence
does not discriminate: `interventions.ts:175-177`'s own comment establishes
that `geometry_id` is *always* `NULL` for any interactively-submitted row
(user submissions never enter the Type-2 conformed geometry dimension —
that's a warehouse-backfill-only concern), and neither `submitIntervention`
nor `proposeIntervention` ever writes a `properties.id` key (only the
machine-ingress route does, via `src/lib/server/services/ingest.ts:81`). So
"NULL `geometry_id` + no `id` key" is equally consistent with a genuine
interactive submission as with a raw seed insert. **What would settle it:** a
direct read of the 2 rows' `properties` bag — presence of
`submittedByUserId`/`publicationConsent` (the `submitIntervention` /
`proposeIntervention` shape) versus absence of both (consistent with a
non-router write). Do not repeat the "ungoverned seed rows" characterization
as settled fact without doing that read first.

## 4. Grain

One row = one `geo.features` row belonging to the `geo.layers` row named
`interventions` — one submitted (or, dormant, partner-fed) ecosystem
intervention site: a name, an `InterventionType`, an optional description
(≤2,000 chars), and a GeoJSON geometry validated as described in §1. `geom`
is populated — or explicitly nulled — by a `BEFORE INSERT OR UPDATE OF
properties` trigger (`geo.sync_feature_geom_from_properties`,
`drizzle/0001_handy_riptide.sql:151-181`) that parses `properties.geometry`;
no router ever writes the `geom` column directly. Provenance
(`submittedByUserId` / `submittedByTeamId` / `publicationConsent`) lives
inside the untyped `properties` jsonb bag, not a typed column —
`ContributionQueue.tsx:16-19`'s own comment warns "Nothing here is
trusted... this queue filters on status alone, not on layer."

**A second, incompatible grain exists for the same `layer_id` and nothing
in the schema prevents it.** `proposeIntervention` rows
(`interventions.ts:299-333`) carry `strategyType` / `cellId` / `causalTauEst`
— an ML-scorecard-flavored shape unrelated to `submitIntervention`'s — and
carry **no geometry at all**: the `.values()` insert never references
`input.lat`/`input.lon`, and `properties.geometry` is never set. See §5.2 for
why that is load-bearing, not cosmetic.

## 5. Known gaps and traps

### 5.1 The headline finding: the reachable "Approve" button cannot publish

Two incompatible moderation subsystems write the same `features.status`
column, and only the broken one is wired into the UI.

- `contributions.ts` defines the **only** two mutations anywhere in the
  codebase that ever write `status = 'published'` or `'rejected'` —
  `publishContribution` (`:49-70`) and `rejectContribution` (`:72-100`),
  both `expertProcedure`. Its UI, `ContributionQueue.tsx`, correctly reads
  `listPendingReview` (filters `status = 'pending_review'`,
  `contributions.ts:128`) and calls these two mutations.
- `interventions.ts` separately defines `castModerationVote`
  (`:338-385`, `contributorProcedure` plus an inline
  `platformRole` check at `:347-350` rather than `expertProcedure`) and
  `transitionLifecycleState` (`:390-432`, same inline check). `vote:
  "approve"` writes `status = 'approved'` (`:362`); `transitionLifecycleState`'s
  target-state enum is `proposed | approved | active | monitored`
  (`:394`) — **`published` is not a legal value in either procedure.**
  Neither ever reaches it.
- The **only reachable expert-gated moderation route in the app**,
  `/moderation` (`src/app/moderation/page.tsx`), mounts `ModerationPanel.tsx`
  exclusively — wired to `castModerationVote`/`transitionLifecycleState`,
  never to `contributions.ts`. Its button is labeled **"Approve & Publish"**
  (`ModerationPanel.tsx:135`) but calls `castVote.mutate({vote: "approve"})`
  (`:28-34`), which — per the previous bullet — sets `status = 'approved'`
  and publishes nothing.
- `ContributionQueue.tsx`, the component that *does* call
  `publishContribution` correctly, is mounted nowhere reachable (§1).
- **Compounding failure:** once `castModerationVote` flips a row to
  `'approved'`, it also drops out of `listProposed`
  (`interventions.ts:253-294`), which filters on
  `status = RECOMMENDATION_STATUS` i.e. `'pending_review'` (`:285`) — the
  same query `ModerationPanel` reads. The row disappears from the only UI
  that just touched it. It is now simultaneously invisible to both
  moderation queues (neither `pending_review` nor `published`) and to the
  public map (`geo.intervention_tiles` requires `f.status = 'published'`,
  `drizzle/0005_intervention_priority_tiles.sql:34`). No UI path can recover
  it — only a raw DB `UPDATE` or a direct, list-bypassing call to
  `publishContribution` can rescue a row once it lands here.
- This is exactly the gap
  `conductor/tracks/community_engagement_completion_20260805` Phase 1 owns.
  Its own acceptance criterion — "a contributor-submitted recommendation is
  publishable by an expert and visible on the map afterwards" — is recorded
  as unmet specifically because of this
  (`plan.md:47-48`; `metadata.json`
  `pivot_reconciliation_20260822.phase_status_20260822.phase_1_moderation_loop`).
  **Reference that track for the fix; do not re-plan it here.** RUNBOOK
  already frames it correctly at `:1719-1722`: "find whatever should move
  `status` from `approved` to `published`... A workflow bug, not an
  architecture one — it must not gate the map."

### 5.2 A second, independent landmine underneath the first

`proposeIntervention` (`interventions.ts:299-333`) never persists geometry —
see §4. Because `geo.sync_feature_geom_from_properties`
(`drizzle/0001_handy_riptide.sql:158-160`) sets `geom := NULL` whenever
`properties.geometry` is not a JSON object, and `proposeIntervention` never
writes that key, **`geom` is unconditionally `NULL`** for any row created
this way — and `geo.intervention_tiles()` requires `f.geom IS NOT NULL`
(`drizzle/0005:35`). So fixing §5.1's status-transition bug alone would
**not** be sufficient for a row that entered through `proposeIntervention`:
it would still never render. `proposeIntervention` currently has no UI
caller anywhere in `src/app/**` (grep-confirmed — only its own test
references it,
`src/__tests__/api/interventions-trpc.test.ts:54-64`), so this is dormant
risk rather than a live outage today. The next agent who wires a map-click
"propose" flow to this already-existing procedure walks directly into it.

### 5.3 A third landmine: the machine-ingress route can skip moderation by omission

`src/app/api/ingest/interventions/route.ts` calls the shared
`ingestFeature`/`ingestFeatures` service
(`src/lib/server/services/ingest.ts`). That service's `INSERT`
(`ingest.ts:76-84`) sets only `layerId` and `properties` — it never sets the
top-level `status` column. `geo.features.status` defaults to `'published'`
at the schema level (`src/lib/server/db/schema.ts:227`,
`varchar("status", { length: 20 }).default("published")`, no enum or CHECK
constraint). This is the **exact defect** `interventions.ts:28-29`'s own
comment says the retired `wildfire.createIntervention` had ("it never set
`status`... so rows took the column default `published` and skipped review
entirely") — except it is still live in the current, non-retired
machine-ingress route. The route's schema does require the caller to send
`properties.status` (`route.ts:32`), but that string lands inside the jsonb
`properties` bag — the domain-status tag `geo.intervention_tiles` separately
projects as a display property (`drizzle/0005:27`) — **not** the
moderation-gating `geo.features.status` column every tile function and
review query reads. A caller who diligently sends
`"status": "pending_review"` in their payload still gets an immediately
published, unreviewed row. **UNVERIFIED** whether this route has ever
actually been invoked successfully in production — it is `INGEST_SECRET`-
gated and RUNBOOK calls it "unscheduled" with no ingestion producer anywhere
in the stack (`:1048-1050`), and 0 published rows overall is consistent with
it never having fired, but that is inference, not a direct check. §3's
suggested query would also settle this (a machine-ingress row would carry
neither `submittedByUserId` nor `publicationConsent`).

### 5.4 Duplicated, drifting authorization

`castModerationVote` and `transitionLifecycleState` re-implement the
"who can moderate" check inline (`session.user.platformRole` against a
literal array, `interventions.ts:347-350`, `:398-401`) instead of using
`expertProcedure` (`src/lib/server/trpc/init.ts:40`) the way
`contributions.ts` does. Two independent gates for one question, in two
files, that can drift out of sync.

### 5.5 `status` has no database-level constraint

Plain `varchar(20)`, default `'published'`, no enum, no CHECK
(`schema.ts:227`). Nothing stops any of the status-writing paths —
`submitObservation`'s `'pending_review'`, `publishContribution`'s
`'published'`, `rejectContribution`'s `'rejected'`, `castModerationVote`'s
`'approved'`/`'rejected'`/`'pending_review'`, `transitionLifecycleState`'s
`'proposed'`/`'approved'`/`'active'`/`'monitored'`, and the machine-ingress
route's implicit default `'published'` — from writing any of seven-plus
distinct vocabulary values into the one column every tile function and
moderation query gates on with a bare string comparison. A typo in any
writer is invisible until a row silently stops matching every query that
was supposed to find it.

### 5.6 The current broken state is tested, not merely unnoticed

`src/__tests__/api/interventions-trpc.test.ts:66-79` asserts
`castModerationVote({vote: "approve"})` resolves to `status: "approved"`.
That is a green test documenting §5.1's behavior, not a red one waiting to
be found — whoever fixes the workflow needs to change this assertion, not
just the implementation.

## 6. Validation approach

There is no upstream source system to reconcile against — that is the
defining property of this lane, and `layer-lanes.md` §4's "reconciles what
the lane wrote against what the source system holds" has no counterpart
here: there is no external ground truth for a hand-typed name, description,
or hand-drawn geometry. What can honestly be checked is internal
consistency, not source-reconciliation:

1. **Every published row traces to a legitimate publish path.** No row
   should reach `status = 'published'` without having passed through an
   `expertProcedure`-gated mutation from a prior `pending_review` state.
   Checkable if publish-time provenance is recorded explicitly, rather than
   inferred from field absence the way §3 above was forced to.
2. **Geometry validity** — `geom IS NOT NULL` and `ST_IsValid` — is already
   enforced at write time by the sync trigger, which raises rather than
   silently accepting invalid or wrong-SRID GeoJSON
   (`drizzle/0001:174-176`), and by `BoundedInterventionGeometrySchema`'s
   vertex ceiling. A downstream validator can only recheck this, not add new
   ground truth.
3. **Status-vocabulary consistency** — given §5.5, a validation pass should
   assert every row's `status` is one of the small closed set an actual
   reader treats as terminal (`pending_review` / `published` / `rejected`
   at minimum) and flag the orphan states (`approved` / `proposed` /
   `active` / `monitored`) that no reader currently resolves. **A periodic
   count of rows outside `{pending_review, published, rejected}` should
   always be zero, and is not — this single check would have caught §5.1
   directly.**
4. **Moderation queue health** — age of the oldest `pending_review` row is
   the honest analogue of "staleness" for a demand-driven lane; there is no
   upstream cadence to be late against, only a backlog to not let grow.
5. **Consent integrity** — every row `listProposed` exposes carries
   `publicationConsent: true` on the row itself (`interventions.ts:286`);
   spot-checkable, not inferable.

None of this is a "reconcile against the source" check in
`layer-lanes.md` §4's sense, because there is no source. It is closer to an
internal invariant audit. Any validation module written for this lane should
say so explicitly rather than being forced into the FIRMS/USGS/NASA-shaped
contract the other ten lanes need.

## 7. Forecast recommendation: `horizon: none`

`conductor/RUNBOOK.md:3345` already classifies this lane "no — 0 published
rows; demand-driven, not projectable," and `layer-lanes.md` §2 requires a
lane that genuinely cannot forecast to declare `horizon: none` and ship no
`method/monte_carlo/interventions.py` — an empty forecast module is worse
than an absent one, because it reads as unfinished work rather than a
settled property.

Reasoning beyond restating that classification: a 30-day Monte Carlo
forecast projects a quantity with physical or ecological continuity —
tomorrow's temperature, streamflow, or NDVI is correlated with today's.
Community-submission count and location have no such structure: it is a
rate driven by how many people decide to submit a recommendation, when, and
where — human decision volume, not a process with momentum. Even setting
aside the current 0-published-rows fact, a healthy `interventions` layer
with hundreds of published rows would still have no honest 30-day-forward
answer to "how many interventions will exist at cell X on day Y." The
ensemble would encode noise around a submission rate indistinguishable from
signal, and a user reading a "forecast" for this layer would reasonably
read it as a prediction about ecological interventions, not about human
behavior — exactly the "reads like a measurement" hazard `layer-lanes.md`
§2 warns a blended `observed`/`forecast` pair against.

**Concretely: `horizon: none`. Ship no `method/monte_carlo/interventions.py`
and no `kind=forecast` partition for this lane — not "not yet," permanently
by the nature of what the layer measures.**

## 8. Postgres-or-Parquet recommendation

**`interventions` stays in Postgres. It does not move to Parquet, in any
wave.**

**Why.** `conductor/RUNBOOK.md` §0.23.4 decision 1 (owner, do not
re-litigate): *"Postgres serves community features only. Everything else
leaves"* (`:3207`; elaborated at `:3160-3161`, *"Postgres is retained for
community features only"*). `interventions` is not merely adjacent to
community features — it **is** one, definitionally: every row is authored
by a signed-in human contributor (or, dormant, a partner feed) through a
moderation workflow that shares its tables and its authorization surface
with the rest of the community-engagement stack — `geo.features`/`geo.layers`
(this lane's own storage), `teamMembers` (workspace-scoped submissions,
`interventions.ts:10,107-125`), and `contributions.ts`'s expert-gated review
mutations. `conductor/RUNBOOK.md` §0.23.6 flags *"Community-feature tables
live in `public` and were not inventoried this session... it decides what
actually stays in Postgres"* as an open item (`:3245-3246`) — this document
is itself a data point toward that inventory: `geo.features`, `geo.layers`,
and the `contributions`/`interventions` tRPC routers over them belong on the
Postgres side of the boundary.

**Scale corroborates it, though it is not the primary argument.** The
pivot's entire justification is four oversized relations
(`conductor/RUNBOOK.md:3169-3185`, §0.23.2): `agri.signal_observation`
(26 GB), `geo.features` (7,986 MB), `geo.geometry` (2,988 MB),
`geo.drought_areas` (500 MB). `geo.features` is one of the four — but its
size is driven by the *other ten* layers' geometry-heavy rows
(5,025,009 rows total across the table), not by `interventions`, whose
entire footprint is 0-2 rows. Migrating this lane specifically would save
nothing measurable and would fragment a table that ordinary
row-at-a-time SQL (`contributions.listPendingReview`,
`interventions.listMySubmissions`/`listProposed`, single-row `INSERT`s and
`UPDATE`s from live user sessions) already serves well — exactly the kind
of write pattern DuckDB-over-Parquet is a poor fit for, independent of the
community-features policy argument above.

**What this means concretely for wave-2 scope.** Under `layer-lanes.md` §1's
file-per-layer contract, `interventions` gets **none** of the five lattice
files other lanes get: no `warehouse/schemas/interventions.py`, no
`pipeline/lanes/interventions.py`, no `pipeline/validation/interventions.py`,
no `planes/interventions.py`, no `method/monte_carlo/interventions.py`. None
of S5-S15's per-lane work applies to this lane, because the lane itself is
not migrating — it is out of scope for the entire §0.24 stream plan except
where it intersects the shared `geo.features`/`geo.layers` tables other
lanes are exporting *from* (this lane simply keeps writing to them). If
`layer-lanes.md` §1's sixth concern ("the why: source, cadence, horizon,
known gaps," normally an `AGENTS.md` per directory) is owed for this lane at
all, this document is that content — a wave-2 agent should treat it as
authoritative rather than writing a parallel `AGENTS.md` to satisfy a
five-file checklist that does not apply here.

**One place this lane still touches the pivot, and it needs no new work:**
`geo.intervention_tiles()` (`drizzle/0005_intervention_priority_tiles.sql`)
is a PostGIS tile function, and it can keep being served live against
Postgres exactly as today — `conductor/RUNBOOK.md:1583` already confirms
every serving-side link is correct (`martin.yaml` publishes it,
`layers.ts` wires the style layers, `geo.layers.is_public = true`); **only
the publish workflow is broken (§5.1), not serving.** §0.23.9's open
question, "how do PMTiles get generated from Parquet," does not apply to
this function, and S19 (PMTiles generation + Martin repoint) should leave
`intervention_tiles` untouched.

**Scope boundary, restated plainly:** this document establishes that the
lane is Postgres-resident and hands wave-2 an accurate map of the current
broken state. It does not re-plan the moderation fix —
`conductor/tracks/community_engagement_completion_20260805` (spec.md,
plan.md, metadata.json) owns that, Phase 1 specifically, and Phase 5 (the
still-owner-blocked community→ML label bridge) is a separate, unresolved
decision this document does not attempt to settle either.
