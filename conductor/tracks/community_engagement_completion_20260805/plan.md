---
type: track-plan
track: community_engagement_completion_20260805
status: active
---

# Plan

Phases 1, 2 and 3 are independent and may run in parallel — they share no files.
Phase 4 depends on phase 1. Phase 5 is design-gated on the spec's open questions.

| Phase | Scope | Status |
|---|---|---|
| 1 | Close the moderation loop — mount `ContributionQueue` behind an expert-gated surface | pending — but `ContributionQueue.tsx` is being modified by a concurrent session; re-read before starting |
| 2 | Restore the sensors style layer and toggle | **superseded 2026-08-05** — a concurrent session registered `sensorsLayer` in `LAYER_REGISTRY`. One item survives: `geo.sensor_tiles()` still selects `sensor_type`/`status`/`name`, which no producer populates, so all 750 stations paint the grey fallback until a migration swaps the SELECT list to `network`/`sensor_id`/`station_name`/`observed_at`. In flight. |
| 3 | Publish evacuation-zones end to end | **done 2026-08-05** by a concurrent session — `geo.evacuation_zone_tiles()` shipped as Drizzle `0009`, `evacuationZonesLayer`/`evacuationZonesOutlineLayer` registered, painting on `severity` |
| 4 | Return review outcomes to the submitter | pending |
| 5 | Decide, then possibly build, the community→ML label bridge | **blocked on owner** |

**Provenance warning.** Phases 2 and 3 were written against a working-tree state
that a concurrent session had already replaced. Re-read `src/lib/map/layers.ts`,
`layer-registry.ts` and `src/components/map/AGENTS.md` before acting on anything
in this file — that session holds 20+ uncommitted files across this exact stack.

## Phase 1 — close the moderation loop

The whole submission path works and no row can leave `pending_review` because
the only UI that can move it is mounted nowhere.

- Give `ContributionQueue` a reachable home. It needs an expert-only surface;
  `PanelManager` currently mounts six panels and none is role-gated, so decide
  between a seventh gated panel and a dedicated route. Prefer the route: a
  moderation queue is not map-adjacent work and does not want to fight the map
  for screen area.
- Gate the surface on `platformRole ∈ {expert, admin}`, matching
  `expertProcedure` ([trpc/init.ts:40](../../../src/lib/server/trpc/init.ts#L40)).
  Server-side gating already exists; the UI gate is presentation only and must
  not be the sole check.
- `rejectContribution` writes a `reviewNote`. Make the queue require one on
  reject — a rejection with no reason cannot be acted on by the submitter, and
  phase 4 surfaces it.
- Verify the transition end to end against a **real** submission (create one as a
  contributor through the UI). Confirm the row reaches `status = 'published'`
  and then appears through `geo.intervention_tiles`, which filters on exactly
  that value.

**Acceptance:** a contributor-submitted recommendation is publishable by an
expert and visible on the map afterwards, with no row authored by any agent.

## Phase 2 — restore the sensors layer

Everything downstream already works: 750 published rows, `geo.sensor_tiles()`
live in `martin.yaml`, and the function's `status = 'published'` filter matches
the data. This is a re-connection, not a build.

- Add the `sensors` style layer back to
  [src/lib/map/layers.ts](../../../src/lib/map/layers.ts), replacing the
  now-false comment at line 104 rather than leaving it above the new layer.
- Restore the toggle. `activeLayers` defaults to `["fire","water","weather"]` in
  [src/stores/map-store.ts:42](../../../src/stores/map-store.ts#L42); sensors
  should be toggleable but need not be on by default.
- Add legend and hover fields to match the sibling layers
  ([src/lib/map/hover-fields.ts](../../../src/lib/map/hover-fields.ts)).
- Style on a property the producer actually writes. The `interventions` layer
  carries a comment recording that it was once coloured by a field nothing
  populated, which "painted every zone the same fabricated hue" — check the
  sensor payload before choosing a paint expression, and do not repeat that.

**Acceptance:** toggling sensors renders the 750 published stations, styled on a
field with real values.

## Phase 3 — publish evacuation-zones end to end

The only item in this track that is a genuine build. 381 published rows exist
with no serving path at all.

- Add `geo.evacuation_zone_tiles(z,x,y)` as a Drizzle migration, modelled on
  `geo.sensor_tiles` including the `status = 'published'` filter and the
  `SET search_path = public, pg_catalog` hardening that
  [drizzle/0008_geometry_dimension.sql:110](../../../drizzle/0008_geometry_dimension.sql#L110)
  applies to the existing functions.
- Register it in [infra/martin/martin.yaml](../../../infra/martin/martin.yaml)
  beside `sensor_tiles` and `intervention_tiles`.
- Add the style layer, toggle, legend and hover fields.
- Adding a Drizzle migration requires the paired contract update — do not skip
  it; a migration without it is the known breakage mode for this repo.

**Acceptance:** toggling evacuation-zones renders the 381 published zones from a
first-party tile endpoint.

## Phase 4 — return review outcomes to the submitter

`listMySubmissions` already returns `status` and `reviewNote` for the caller's
own rows and their workspace's rows, and nothing renders them.

- Surface submission state in `CommunityPanel` — pending / published / rejected,
  with the expert's `reviewNote` shown on rejection.
- The panel already calls `listMySubmissions`
  ([CommunityPanel.tsx:126](../../../src/components/panels/CommunityPanel.tsx#L126)),
  so this is rendering work, not a new query.
- Do not notify by email in this phase; that is a separate decision with its own
  consent surface.

**Acceptance:** a submitter sees their own recommendation's state change after an
expert acts, including the reason when rejected.

## Phase 5 — community→ML label bridge (blocked)

Do not start. The spec's four open questions must be answered by the owner first.
The risk being managed is specific: the warehouse's credibility rests on every
row being measured, and a label plane fed by hand-authored submissions is the
same hazard the never-seed directive exists to prevent. If the answer is yes,
the bridge must carry provenance distinguishing a community label from a
measured observation, and that shape is a design task, not an implementation
detail.

## Verification

One sweep at the end, not per phase: `npm run test`, `npm run lint`,
`npm run build`, and the data-boundary gate. Phase 3's migration additionally
needs the contract check. Route the approval pass to `quality-reviewer` — the
author does not self-approve.
