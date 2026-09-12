---
type: integration-handoff
track: intervention_boundary_authoring_20260911
status: ready_for_integration
---

# Shared-file handoff packet

Base commit: `89e8494422b8232c8f16dbffdcf2321c7ea17bc8`.
Base tree: `777fe20689dd68c337677cc49e3fd4e63bfd4bcc`.
Branch: `codex/intervention-boundary-publication-20260911`.

## Approved map mount (applied in candidate)

`src/components/map/MapView.tsx` base blob `5355ec7751bafe589aa9e012b3c9557d17063fdd`.
Only two added lines: import `InterventionPublicationSync` beside `MapKeyboardShortcuts`,
and render `<InterventionPublicationSync />` immediately after it. Parent task approved
this exact scope. Boundary authoring needs no root mount or LayerManager edit.
The exact patch is `mapview-publication-sync.patch` in this evidence directory.

The corresponding isolated render-count fixture adds one sibling stub in
`src/__tests__/components/map-view-render-count.test.tsx`, base blob
`66ad275ca24a17b272bd69414e43680036db1141`. The actual sync component is covered
by its own behavior tests. Parent was notified before this verification adjustment.

The pure intervention schema moved unchanged to `src/lib/geo/intervention-geometry.ts`.
The original `src/lib/server/services/intervention-geometry.ts`, base blob
`a5a22d8ddc1bb6b62d8e52415d82f2e9a2cc2563`, now compatibility-reexports its four
public symbols. Parent was notified before this boundary-check repair; existing
ingress/server callers retain their imports and schema semantics.

## Registry update (integration-owned, not applied here)

`conductor/tracks.md` base blob `8c14ecece770aac7140f0bab8511e1dc772118c6`.
Replace the existing community row with the following row and add its companion directly after it:

| [community_engagement_completion_20260805](tracks/community_engagement_completion_20260805/plan.md) | in_progress | Canonical submit/review/publish/visible-map repair: single published transition, individual legacy recovery, author revision, truthful states and cache refresh. Local acceptance passed and independent review accepted; ready for integration. No current production count is asserted. ML label bridge remains owner-gated. |
| [intervention_boundary_authoring_20260911](tracks/intervention_boundary_authoring_20260911/plan.md) | in_progress | Bounded polygon/rectangle/explicit-point editor with draft preview, keyboard/touch, undo/cancel/edit. Local acceptance and independent review passed; ready for integration. Companion to community publication; no production seed or deployment. |

## Runbook update (integration-owned, not applied here)

`conductor/RUNBOOK.md` base blob `6ab7b1b96b4eed8a6f1e0a156fba3dc1a51918d3`.
Add or replace the community/intervention row in Current checkpoint:

| Community interventions | The [canonical track](tracks/community_engagement_completion_20260805/plan.md) owns submit/review/publish/visible-map repair; [boundary authoring](tracks/intervention_boundary_authoring_20260911/plan.md) is its bounded companion. Interventions remain operational PostgreSQL data. No current production counts were measured. | Integrate the reviewed candidate and exact local receipts. Legacy approved rows require individualized review, original consent and valid geometry; no bulk publication. Lifecycle needs a separate publication/lifecycle migration before controls return. No production seeding or deployment is authorized. |

## Map library documentation append (integration-owned, not applied here)

`src/lib/map/AGENTS.md` base blob `d0b70e5174428db6fb180a77b55a4dc691c4b1c6`.
Append:

### Intervention publication refresh

`intervention-publication.ts` clears only intervention service-worker tiles before
issuing a persistent publication revision. The sync component applies that revision
to MapLibre's intervention tile URLs, which clears in-memory empty tiles and bypasses
HTTP/worker entries even when worker acknowledgement times out. Storage and same-tab
events, page return, late TileJSON and style replacement share the same idempotent
source reset; equal templates never call setTiles again. Cleanup removes every listener.
Revision state is browser-local freshness metadata, not authorization or publication
truth. The server's published filter remains authoritative. No layer toggle changes.
Custom domains are recognized by the exact intervention tile path; the worker retains
its refresh promise with waitUntil. Static basemap cache entries remain intact.
