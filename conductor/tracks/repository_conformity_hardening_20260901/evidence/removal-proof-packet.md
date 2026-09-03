---
type: evidence
slug: repository_conformity_hardening_20260901
wave: c3
date: 2026-09-02
---

# Removal proof packet — TypeScript surface

Base commit `ad4e015`. Every scan below was run from the repository root with
`rg --glob '!node_modules' --glob '!.next'` over the whole tree, and each candidate was searched
for **three** ways: by module path, by bare basename (which catches `import()`, `new Worker(new
URL(...))`, `next.config.ts` aliases and `public/sw.js` string literals), and by every symbol the
module exports. Documentation-only hits (`PLAYBOOK.md`, `conductor/**`) are recorded but are not
consumers.

Rollback for everything in this packet is a revert of the commit that carries it; nothing here
has a runtime feature flag, a database object or a deployed route behind it.

---

## REMOVED

### 1. `src/lib/map/webgpu-accelerator.ts` (268 lines)

- **References found:** one — `src/__tests__/lib/map/webgpu-accelerator.test.ts:2`, deleted with
  it. No production import anywhere. Remaining hits are historical prose:
  `conductor/tracks.md:99`, `conductor/RUNBOOK.md:401`,
  `conductor/retros/webworker_webgpu_acceleration_20260814/{spec,plan,metadata}`.
- **Canonical replacement:** none, deliberately. The pipeline was reverted on 2026-08-15 because
  it was net-harmful — every result it computed was discarded (`void`), so the packing pass and
  the GPU readback ran synchronously on the main thread. `LayerManager.tsx` carries the incident
  note at the point where a re-import would go.
- **Tests that would fail if it were still needed:** none exist and none could — the module had
  no caller, so no behavioural suite covers it. Its own isolation test was deleted with it.
- **Rollback:** `git revert` of this commit restores both files verbatim.

### 2. `src/workers/layer-processor.worker.ts` (121 lines)

- **References found:** one import, in the same deleted test file (`:3`), plus the prose note in
  `src/components/map/LayerManager.tsx`. The `new Worker(new URL(...))` scan over
  `src/ scripts/ public/ next.config.ts` returns exactly one worker instantiation in the whole
  repository — `src/hooks/useActionNetworkFeatures.ts:266`, which builds
  `../workers/action-network.worker.ts`, a different module that stays.
- **Canonical replacement:** `src/workers/action-network.worker.ts` is the shape a real worker
  takes here — instantiated with `new Worker(new URL(..., import.meta.url), { type: "module" })`,
  never imported directly. `layer-processor.worker.ts` was imported directly, which installed its
  module-scope `message` listener on `window` (on the main thread `self` IS `window`).
- **Rollback:** revert.

### 3. `src/types/webgpu.d.ts` — second-order, revealed by (1)

- **References found:** zero after (1). Scanning the tree for `GPUDevice|GPUBuffer|navigator.gpu|
  webgpu` outside the three files being deleted returns nothing. It is an ambient declaration
  file added for the accelerator alone (recorded as part of the same reverted feature in
  `conductor/retros/webworker_webgpu_acceleration_20260814/metadata.json:10`).
- **Canonical replacement:** none needed; nothing in the tree touches WebGPU.
- **Verification:** `tsc --noEmit` is clean with the file gone, which is the direct proof that no
  remaining source depended on the globals it declared.
- **Rollback:** revert.

### 4. Six `src/components/ui/*` modules

`coordinate-display.tsx`, `dropdown-menu.tsx`, `floating-toolbar.tsx`, `loading-overlay.tsx`,
`theme-toggle.tsx`, `zoom-indicator.tsx`.

- **References found:** zero, by all three scans.
  - By path/basename: only `PLAYBOOK.md` (the historical build script that specified them),
    `conductor/RUNBOOK.md:404`, `conductor/tracks/.../metadata.json` and
    `conductor/retros/15-ui-design-system/spec.md`.
  - By exported symbol (`CoordinateDisplay`, `DropdownMenu*`, `FloatingToolbar`,
    `FloatingToolbarItem`, `LoadingOverlay`, `ThemeToggle`, `ZoomIndicator`), excluding
    `conductor/**` and `PLAYBOOK.md`: every hit is the defining file itself.
  - Route ownership: `src/components/ui/` has no barrel (`index.ts` exists only in
    `src/components/ui/editorial/`, which does not re-export any of the six), so no `src/app/**`
    route can reach them without a direct import, and none does.
- **Canonical replacement:** the surfaces these were built for are gone. The map's chrome is the
  single left-edge manager plus `MapDateSummary`/`SyncIndicator`
  (`src/components/map/AGENTS.md`, "One manager, no floating surfaces"), which is why the
  coordinate/zoom/toolbar primitives have no caller. `LoadingOverlay`'s role is served by
  `src/components/ui/skeleton.tsx`, which MapView actually renders.
- **Tests that would fail if still needed:** none — no suite imported any of the six.
- **Dependency side effects:** none. Each imported only `@/lib/utils` (`cn`), `react`, and in
  `theme-toggle.tsx` two `lucide-react` icons; all three packages have many other consumers. No
  Radix package is orphaned by these deletions.
- **Rollback:** revert.

---

## RETAINED, with a named blocker

### 5. `teams.inviteMember` (`src/lib/server/trpc/routers/teams.ts`)

- **Blocker:** production consumer telemetry is not available from this environment. The only
  repository caller is the compatibility test at
  `src/__tests__/security/org-invitations.test.ts:557` (which covers a real security property —
  that the organization is authorized *before* the target `userId` is resolved, so an outsider
  cannot distinguish "real user" from "not your organization" by error code). Absence of a
  repository caller does not prove absence of an external one for a tRPC procedure on a deployed
  API.
- **Action taken instead of deletion:** a `@deprecated` JSDoc that names `createInvitation` as
  the replacement and records **sunset 2026-10-01**, after which the procedure and its
  compatibility test are removed together.
- **What must happen before the sunset:** one look at production request logs for
  `teams.inviteMember`. If it is unused, delete the procedure and the single `inviteMember` test
  case; the other assertions in that file cover `createInvitation` and `acceptInvitation` and
  stay.

### 6. `returnLink` — the unowned `TODO` (resolved, not deferred)

The bare `TODO` at the old `teams.ts:902-904` said: *flip to `false` once the dashboard's
one-time-reveal UI stops assuming the accept link is always present in the response.* That
condition was in-repo and cheap, so the TODO is implemented rather than re-filed:

- `src/app/dashboard/org/invitations/page.tsx` now asks for the link explicitly
  (`returnLink: true` on both `createInvitationMutation.mutateAsync` calls) and stores
  `lastSentInvite` only when `result.acceptUrl` is non-empty, so the copy-link button can never
  render over an empty string.
- `createInvitation` and `inviteMember` now default `returnLink` to `false`. The accept link is a
  live credential; a caller that does not render a one-time reveal must not be handed one. The
  invitee still receives it by email either way (`issueInvitation`).
- Both existing assertions in `org-invitations.test.ts` pass `returnLink` explicitly (`:538`,
  `:548`) and so pin both branches independently of the default.
- `docs/api-reference.md` now documents the parameter and the empty-`acceptUrl` semantics for
  both procedures.

### 7. Direct dependencies — REMOVAL-READY, not removed here

Uninstalling needs `npm install` to regenerate `package-lock.json`, plus an image build and a
bundle smoke check (proof-before-delete contract item 3). Neither is runnable in this
environment, so the scan result is recorded and the removal is a deploy-time follow-up.

| Package | Import scan | Lockfile dependents | Verdict |
| --- | --- | --- | --- |
| `@deck.gl/mapbox` | zero — no `MapboxOverlay` and no `@deck.gl/mapbox` import in `src/` or `scripts/`; only `package.json:38` and `PLAYBOOK.md:507,516` | none | **REMOVAL-READY** |
| `@deck.gl/react` | zero — no `from "@deck.gl/react"` anywhere; only `package.json:39` and `PLAYBOOK.md:507` | none | **REMOVAL-READY** |
| `jotai` | zero — no `useAtom`, no `atom(`, no `jotai` import; only `package.json:52` | none | **REMOVAL-READY** |
| `preact` | zero direct imports | `@auth/core`, `next-auth`, `@deck.gl/widgets`, `preact-render-to-string` (peer) | **RETAINED — blocker below** |

**`preact` is not removable, and the pinned-exact version is the tell.** Root `dependencies`
pins `preact` to `10.11.3` with no range. `node_modules/@auth/core@0.34.3` requires *exactly*
`preact 10.11.3`; `next-auth@4.24.13` requires `^10.6.3`; `preact-render-to-string@5.2.6`
requires only `>=10`. The root pin is what holds the single hoisted copy at the one version
`@auth/core` accepts. Dropping it makes the hoisted version a resolution outcome rather than a
declared one, and next-auth's rendered sign-in pages are what break if it floats. Removing it
needs an auth smoke test, not just a lockfile regeneration — out of scope for this track.

**Exact deploy-time follow-up command** (run together, one lockfile regeneration, then the image
build and bundle smoke):

```bash
npm uninstall @deck.gl/mapbox @deck.gl/react jotai
npm run type-check && npm run lint && npm test
# then: docker build (the Dockerfile build stage runs check:data-boundary/type-check/lint/test)
# and one browser load of the map to confirm the deck.gl interleaved path is genuinely unused.
```

**Documentation reconciliation owed with that removal:** `AGENTS.md` "Tech Stack" and the
project `CLAUDE.md` both still claim *"State: Zustand (global) + Jotai (per-layer atoms)"*.
Jotai has zero imports; the per-layer state is Zustand (`src/stores/*.ts`) plus per-layer atoms
that were never built. Fix the line in the same commit that uninstalls the package, so the doc
and the manifest stop disagreeing in opposite directions.

**Removed 2026-09-03.** Re-ran the three import scans (`@deck.gl/mapbox`, `@deck.gl/react`,
`jotai`, plus `useAtom`/`atom(`) over `src/` and `scripts/` — still zero. Ran
`npm uninstall @deck.gl/mapbox @deck.gl/react jotai` from the repo root; `package.json` and
`package-lock.json` regenerated together (155 lines removed from the lock, no lines added — no
unrelated package was bumped). The transitive-only `@deck.gl/widgets` and its private nested
`preact` copy also dropped out of the lock with them; `@deck.gl/widgets` was never a direct
import (`npm ls @deck.gl/widgets` now resolves empty). `preact` is confirmed still present and
still pinned at the exact `10.11.3` the root `dependencies` block declares (`npm ls preact`
shows `@auth/core@0.34.3` and its `preact-render-to-string@5.2.6` dedup onto that pin); `preact`
was correctly left untouched per the blocker above. `npm ls @deck.gl/core` still resolves
(`@deck.gl/core@9.2.11`, `@deck.gl/geo-layers@9.2.11`, `@deck.gl/layers@9.2.11`) — deck.gl proper
is unaffected. The doc reconciliation landed in the same pass: `AGENTS.md:18` and
`.claude/CLAUDE.md:18` now both read `- **State**: Zustand (global)`. `npm run type-check`,
`npm run lint`, and `npm test` were deliberately **not** run here — that is a separate sweep
per the owning task's boundary.

---

## Findings recorded, NOT acted on (out of this track's authorized candidate list)

Both were surfaced by the `c1` unused-symbol pass, are structural rather than local, and are
left exactly as found apart from the minimum needed to clear the gate.

### `src/lib/server/services/places.ts` — orphan module with unimplemented spatial predicates

`searchByCategory`, `searchNearby`, `searchByText`, `getById` and `POI_CATEGORIES` have **zero**
consumers in `src/` or `scripts/`. Worse than unused: three of the readers accept a spatial
argument and ignore it — `searchByCategory(category, bbox)` and `searchByText(query, bbox?)`
never filter by the box, and `searchNearby(lat, lon, radius, limit)` ignores all three and
returns an unfiltered `limit`-row scan of `geo.poi`. The gate was cleared by prefixing those
parameters `_` and adding a three-line module note that says so out loud; the parameters are
**not** "deliberately unused", they are unimplemented, and a future caller must read the note
before trusting a signature. Treat the module as a removal candidate or a build item, not as
working code.

**Correction — 2026-09-03: the zero-consumers claim above is retracted.** It is not deleted here
because this file is append-only, but it is wrong and must not be acted on. `places.ts` is
imported by `src/lib/server/trpc/routers/places.ts:3-9`, which pulls in `searchByCategory`,
`searchNearby`, `searchByText`, `getById`, and `POI_CATEGORIES` by name; that router is mounted as
`places: placesRouter` at `src/lib/server/trpc/router.ts:29`, and every procedure on it is a
`publicProcedure`. The `c1` unused-symbol pass evidently did not follow the tRPC router mount, so
the module read as orphaned when it is in fact wired to a public API surface. The underscore-prefix
workaround this packet applied did not just paper over dead code — it hid a real wrong-answer bug
on live endpoints: `searchNearby` ignored `lat`/`lon`/`radius` and `searchByCategory`/`searchByText`
ignored `bbox`, so spatial filtering silently did nothing for any caller of those tRPC procedures.
A TypeScript fix implementing real PostGIS spatial filtering for all three readers is landing in the
same commit as this correction (2026-09-03). Do not treat this module as a removal candidate.

### `src/lib/server/services/geofence.ts` — orphan module

`checkGeofences` has zero consumers. It carries a hand-written ray-casting `pointInPolygon` and
writes `alerts` rows on geofence enter/exit — real logic, no caller, and it duplicates in
JavaScript a containment test PostGIS already answers. Only the unused `sql` import was removed.
Candidate for either deletion or wiring into the tracking ingest path; that is a decision, not a
cleanup.

**Addendum — 2026-09-03 (closure review of the fix).** `geo.poi` has no producer in the repository:
only `drizzle/0001_handy_riptide.sql:108` (its `geom` column) and `:279` (its GiST index) reference
it, and the OSM import (`scripts/import-osm.sh`) loads `geo.osm_pois`, not `geo.poi`. So the
underscore-prefixed readers ignored their spatial arguments over a table that is empty in every known
environment: the defect was real in code and latent in effect. The 2026-09-03 fix keeps the readers on
`geo.poi` as correct build-ahead code (index-backed envelope pre-filter, exact `ST_DWithin`,
area-bounded required bbox) and records in `src/lib/server/AGENTS.md` §places that a future POI feature
must either populate `geo.poi` or repoint at `geo.osm_pois`.
