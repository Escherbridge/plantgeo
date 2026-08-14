---
type: lane-brief
track: ingestion_warehouse_consolidation_20260803
lane: H
status: ready
depends_on: none
---

# Lane H — Browser QA of the shipped UI

Read [`lanes/README.md`](./README.md) first for the inherited rules and the wave plan. This lane is
wave 1, owns no schema, and blocks nothing.

## 1. Goal

Seven UI behaviours fixed in `0e194f8` and `2869fb5` were verified **statically only** — by reading
code and MapLibre's dist bundle — and have never been opened in a browser. When this lane is done,
each of the seven has been exercised live at the viewport sizes where the defect actually
reproduces, every finding is written up with a file:line, and any fix applied is small, obvious and
named in the report. The track plan's two out-of-band items —
`conductor/tracks/ingestion_warehouse_consolidation_20260803/plan.md:230-231` ("Community features
do not work on the map", "Navigation can trap the user in the organization settings page") — are
either closed with browser evidence or reopened with a reproduction.

## 2. Prerequisites

Nothing in this track needs to land first. What must be **running** locally:

| Step | Command | Expected |
|---|---|---|
| 0 | `podman ps -a --format "{{.Names}}"` | the compose trio's **actual** names. `plantgeo_postgis_1` / `plantgeo_redis_1` / `plantgeo_martin_1` is the expected podman-compose form, but the project prefix depends on the directory name — read the real names here and substitute them in step 1 rather than assuming |
| 1 | `podman start <the three names from step 0>` | three container names echoed back |
| 2 | `podman ps --format "{{.Names}}\t{{.Ports}}"` | postgis `127.0.0.1:5434->5432`, redis `127.0.0.1:6379`, martin `127.0.0.1:3100->3000`. Host port mappings on this machine are crossed (`README.md` §"Environment") — if postgis does not show `5434`, use whatever it does show |
| 3 | `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3100/style.json` | `200` — if this is not 200 the map renders nothing and **no toggle test is meaningful** (`.env.local` sets `NEXT_PUBLIC_MAP_STYLE_URL=http://localhost:3100/style.json`) |
| 4 | `curl -s http://localhost:3100/catalog \| head -c 400` | JSON listing sources including `interventions` |
| 5 | `npm run dev` | `Local: http://localhost:3001` (`package.json:7` pins `-p 3001`) |

All containers on this machine were stopped deliberately at the end of the previous session
(`plan.md:160-166`); nothing was removed, volumes are intact. Do not start the warehouse or
rehearsal containers — this lane does not need them.

## 3. Files you own

[`lanes/README.md`](./README.md) §"File boundaries", lane H row: **"small fixes only, reported
before applying"**.

Concretely:

- You may edit **only** presentational/client files under `src/app/**` (**excluding
  `src/app/api/**`** — those are server routes, and lane D is deleting
  `src/app/api/cron/ingest/route.ts`), `src/components/**`, `src/styles/globals.css`, and their
  sibling `AGENTS.md` files — and only for a fix you have described in your report first.
- You must **not** touch `src/lib/server/db/**`, `drizzle/**`,
  `src/lib/server/db/migration-contract.ts`, `services/agri-data-service/**`, or
  `services/agri-data-service/db/agri/**`.
- **`src/__tests__/**` and `src/types/**` are not yours at all.** Lane B owns
  `src/__tests__/lib/geometry-migration.test.ts`, lane D deletes two files there, and lane G owns
  `src/__tests__/stores/**` and `src/__tests__/components/**`. Propose regression tests in your
  report (see §7 Q4); do not write them.
- **Lane G is `in-progress` and its boundary was widened on 2026-08-03.** It owns
  `src/types/time-slider.ts`, `src/stores/**`, `src/components/panels/**`,
  `src/components/map/TimeSlider.tsx` and `src/components/map/LayerManager.tsx`, and it is
  **deleting** `src/components/ui/time-slider.tsx`. Do not edit any of those. If a defect's fix
  lives there — including `CommunityPanel.tsx`, `panel-store.ts` and `LayerManager.tsx` — report it
  and stop. Reading them is fine. `LayerManager.tsx` matters for you specifically: item 4.5's
  `interventions` toggle runs through it, so you may well land on it — **read, do not edit**.
  Of `src/components/map/`, only `MapView.tsx`, `Legend.tsx` and `PanelManager.tsx` are editable in
  principle, and §7 Q3 rules most of that out in practice.
- **Lane S owns the soil serving path** (carved 2026-08-03): `src/components/map/layers/SoilLayer.tsx`,
  `src/lib/server/services/{soilgrids,usda-soil,carbon-potential,usle}.ts` and
  `src/app/api/ingest/soil/route.ts`. Do not edit any of them. If soil misbehaves in the browser,
  that is lane S's investigation — record what you saw and hand it over.
- Lane F owns `infra/tiles/**` and `data/**`, lane D owns `infra/cron-ingest/**`, and
  `docker-compose.yml` and `infra/martin/martin.yaml` are nobody's this wave. Starting a container
  is not editing it; changing any of those files is, and is out of bounds.

If a fix needs a file outside this list, write it up and hand it back rather than reaching across.

## 4. The work

Run in this order — it is ordered by risk, and item 1 is the one that traps a user.

### 4.1 `/dashboard/org/settings` — the Save button must be reachable

The fix made `<main>` the scroll surface with the header pinned as a non-scrolling flex child:
`src/app/dashboard/org/layout.tsx:31` (`viewport-below-top-bar flex flex-col`), `:32`
(`header shrink-0`), `:66` (`main mx-auto min-h-0 w-full max-w-4xl flex-1 overflow-y-auto`).
Rationale: `src/app/dashboard/org/AGENTS.md:18-23`.

**This is viewport-conditional.** It reproduced on a laptop and on full-screen 1080p (~940 px of
usable viewport) but not on 1440p. Testing at 1440p proves nothing.

1. Sign in, open `/dashboard/org/settings`.
2. Size the window so the viewport is ~900–950 px tall. Confirm the "Save changes" button
   (`src/app/dashboard/org/settings/page.tsx:199-206`) is reachable by scrolling, and that the
   header — which holds the only exit link back to `/dashboard`
   (`src/app/dashboard/org/layout.tsx:34-40`) — stays pinned and does not scroll away.
3. Switch DevTools to a mobile width (≤640 px, e.g. iPhone SE 375×667). At that width
   `OrganizationTypePicker`'s `grid grid-cols-1 gap-2 sm:grid-cols-2`
   (`src/components/onboarding/OrganizationTypePicker.tsx:63`) drops to one column, and there are
   **five** archetypes (`:20, :26, :32, :38, :44`), so a 3-row picker becomes 5 rows — roughly two
   extra rows of form height. This is the worst case; the picker is rendered at
   `src/app/dashboard/org/settings/page.tsx:142`. Confirm Save is still reachable and the header
   still pinned.
4. Confirm the page body itself never scrolls horizontally and the document does not scroll —
   `globals.css:73-79` sets `body { overflow: hidden }` for the map, which is exactly why the
   shell has to own its own scroll surface.

### 4.2 The global top bar must appear on the newly-exposed subtrees

`ApplicationShell` decides per route. `/dashboard` was moved from a prefix deny-list to an
**exact-match** list so its children get the bar:
`src/components/layout/ApplicationShell.tsx:20-21` (`EXACT_ROUTES_WITHOUT_GLOBAL_NAVIGATION =
["/dashboard"]`) and `:23-28` (`hasGlobalNavigation`).

Check each:

| Route | Top bar |
|---|---|
| `/dashboard` | **absent** (ships its own header) |
| `/dashboard/org` | present |
| `/dashboard/org/settings` | present |
| `/dashboard/conversations` | present |
| `/dashboard/conversations/<id>` | present |
| `/onboarding` | absent (`ApplicationShell.tsx:14`, subtree deny) |
| `/login` | absent (`:9`) |
| `/community`, `/about`, `/` | present |

While the bar is present, confirm nothing is clipped: the bar is 3.5 rem
(`globals.css:249-251`), and barless routes zero those vars via
`.application-shell-without-top-bar` (`globals.css:519-522`). A route that renders both a full
`100dvh` child *and* the bar would overflow — `globals.css:504-511` rewrites `h-screen`/`min-h-screen`
children of the with-bar shell to compensate, so watch for a double scrollbar or a cut-off footer.

### 4.3 `/dashboard/conversations` — long list must scroll

New layout: `src/app/dashboard/conversations/layout.tsx:4` (`viewport-below-top-bar flex flex-col`)
and `:6` (`main min-h-0 w-full flex-1 overflow-y-auto`). Rationale:
`src/app/dashboard/conversations/AGENTS.md:5-12` — the list is up to 50 rows and without this the
tail is unreachable under `body { overflow: hidden }`.

Confirm the list scrolls to its last row, at the same small viewport as 4.1. If the account has few
conversations, shrink the viewport until the list overflows rather than declaring it untestable.

### 4.4 `/onboarding?create=1` for a user who already has an organization

- `src/app/onboarding/page.tsx:38` reads `searchParams.get("create") === "1"` into
  `creatingAdditionalOrganization`; `:39-41` seeds `screen` to `"create"`.
- `:70-73` is the guard: the bounce to `/dashboard/org` only fires when
  `belongsToOrganization && !creatingAdditionalOrganization`.
- `:130-141` gates "Skip for now" on `!belongsToOrganization`.

Verify, signed in as a user with ≥1 organization:

1. `/onboarding?create=1` renders `CreateOrganizationForm` immediately (`:75-77`) and does **not**
   redirect.
2. **"Skip for now" is absent.** That button writes
   `pg_onboarding_skipped=1; max-age=2592000` (`:134`) — a 30-day bypass of the middleware org gate
   at `src/middleware.ts:41-47`. Offering it to a member who already has an org is a real defect.
3. Bare `/onboarding` (no query) still bounces that user to `/dashboard/org`.
4. Cancel on the create form returns to the chooser (`:76`), and from the chooser "Skip for now" is
   still absent for this user.

If you accidentally set the cookie, clear it: DevTools → Application → Cookies →
`http://localhost:3001` → delete `pg_onboarding_skipped`. Leaving it set silently disables the org
gate for 30 days and will make later testing lie to you.

### 4.5 Community panel — two toggles with opposite expectations

Open the map (`/`), open the Community panel. Panel wiring:
`src/components/map/PanelManager.tsx:134-139`.

- **`demand-heatmap` must be DISABLED with its reason visible.**
  `src/components/panels/CommunityPanel.tsx:110-114` passes `unavailableReason`;
  `src/components/ui/layer-toggle.tsx:15-16` forces `isActive` false whenever a reason is set, `:29-30`
  sets `disabled` + `aria-disabled`, `:54-56` renders the caption. Confirm the switch cannot be
  clicked on, the caption text is legible (it is 10 px — check it is not truncated or clipped by the
  sheet), and `aria-disabled="true"` is on the `role="switch"` element in the accessibility tree.
  This is a deliberate governance stub, not a bug — see `src/components/map/AGENTS.md`
  ("DemandHeatmapLayer is parked, not dead").
- **`interventions` must be ENABLED.** `CommunityPanel.tsx:115` deliberately passes no reason. It is
  a live Martin style layer: `src/lib/map/layers.ts:108-113` (`interventionsLayer`, `minzoom: 6`),
  `:133-139` (outline, also `minzoom: 6`), `:208-212` (`STYLE_LAYER_TOGGLE_MAP`). Confirm the switch
  flips, and that **at zoom ≥ 6** geometry appears. At zoom < 6 an empty map is correct behaviour,
  not a failure. Read §5 before concluding "the layer is broken".
- Also check the tRPC error path: `CommunityPanel.tsx:165-169` now only shows the sign-in copy for
  `code === "UNAUTHORIZED"` and otherwise surfaces `requestsError.message`. Signed out, you should
  get the sign-in sentence, not a raw message.
- The Submit button is gated on `canSubmitToActiveTeam` (`:132-139`) with a title explaining why —
  confirm the tooltip appears when disabled.

### 4.6 `/community` — the pin-drop promise is gone

`src/app/community/CommunityLedger.tsx:344-349` now reads "centre it on the parcel … press Submit;
the request is recorded at the map's centre point." Confirm no copy anywhere on `/community` still
instructs the user to drop a pin. Then confirm the behaviour actually matches: the map centre is
threaded through `PanelManager.tsx:137` (`mapCenter={mapCenter}`) into the submit modal, which
displays `Approximate area: {lat.toFixed(2)}, {lon.toFixed(2)}`
(`src/components/panels/RequestSubmitModal.tsx:112`). Pan the map, reopen the modal, and check the
displayed coordinates track the new centre.

### 4.7 The map itself — toggles and render-mode round trips

`0e194f8` was verified statically against `maplibre-gl@5.22.0` dist and never reproduced live. The
governing note is `src/components/map/AGENTS.md` §"Style swaps and render-mode state" and
§"Style.load listener order". The code lives at `src/components/map/MapView.tsx:195` (comment),
`:205-213` (restore handler registered **before** `setStyle`, terrain and projection re-asserted in
both directions), `:226-237`, `:252`.

Exercise, in this order, and after **each** step re-check that every toggle you had on is still on
*and still rendering*:

1. Toggle several layers on (including `interventions` at zoom ≥ 6, and weather, which is on by
   default).
2. Switch basemap style. Terrain and globe must survive the swap — the pre-fix bug was
   `setTerrain(undefined)` / `setProjection(undefined)` emitted by the diff while the toggles still
   read "on".
3. Toggle globe on and off. Zoom must **not** be clamped; the pre-fix bug clamped to ≤5 on entry,
   crossing the `minzoom` thresholds listed in `AGENTS.md` (interventions 6, `osm-roads` 10,
   `building-footprints` 13, `buildings-3d` 14, `osm-waterways` 8) and hiding toggled-on layers.
4. Toggle 3D/pitch and use "reset view"; confirm the 3D indicator matches the restored pitch.
5. Check the Legend (`src/components/map/Legend.tsx:22`): rows with no renderer must be disabled,
   and clicking an enabled row must actually toggle the corresponding layer.

## 5. Traps specific to this lane

1. **`.env.local` points the app at PRODUCTION, but Martin at your local Postgres.**
   `DATABASE_URL=postgresql://…@switchback.proxy.rlwy.net:37967/plantgeo` and
   `REDIS_URL=…@junction.proxy.rlwy.net:44220`, while `docker-compose.yml:54` gives Martin
   `postgresql://geo:…@postgis:5432/plantgeo` (the local container) and `infra/martin/martin.yaml:18-19`
   consumes it. Two consequences, both load-bearing for this lane:
   - **Every write you make while testing hits production.** Creating an organization in 4.4,
     changing settings in 4.1, submitting a strategy request in 4.6 — all real production rows.
     Use a throwaway org name, and prefer verifying the *form* is reachable and submittable over
     actually submitting. Do not delete production rows to clean up.
   - **The panel list and the map tiles come from different databases.** The Community panel's
     request list is read over tRPC from production; the `interventions` fill comes from local
     postgis via Martin. An empty `interventions` layer with a populated request list is the
     expected split, not a rendering bug. Before filing it, check the tile actually contains
     features: `curl -s -o /dev/null -w "%{http_code} %{size_download}\n" "http://localhost:3100/interventions/8/40/88"`
     — a 204 or a zero-byte 200 means no data in the local DB.
2. **Empty layers are usually deliberate.** `lanes/README.md` §"Rules every lane inherits". Before reporting a blank layer,
   check `src/components/map/AGENTS.md` — `demand-heatmap` is a documented governance stub, and the
   `sensors` style layer was removed on purpose (`src/lib/map/layers.ts:103-106`). "An empty data
   feed is not the same as a toggle being off" is the stated invariant.
3. **1440p hides defect 4.1 entirely.** If you only test at your native resolution the whole item is
   vacuous. Force the viewport height.
4. **The `pg_onboarding_skipped` cookie is a 30-day middleware bypass** (`src/middleware.ts:41-47`,
   cookie written at `src/app/onboarding/page.tsx:134`). Setting it once poisons every subsequent
   org-gate test in this lane. Clear it after any accidental click.
5. **`interventions` colours by `priority`, never by `intervention_type`** (`src/lib/map/layers.ts:114-129`)
   — everything grey (`#9ca3af`) means `priority` is unset on those rows, which is a data
   observation, not a style bug.
6. **Do not "fix" a listener-order symptom by adding a `once("style.load", …)`.**
   `src/components/map/AGENTS.md` §"Style.load listener order" is explicit: handlers register once
   per map with `[map]`-shaped deps and read changing inputs from a ref; pairing `once` with `on`
   re-introduces the bug. Any fix in this area is not a "small obvious fix" — report it instead.
7. **`CommunityPanel.tsx` and `panel-store.ts` belong to lane G's boundary** (`lanes/README.md` §"File boundaries", lane G row).
   Read them, do not edit them.

## 6. Definition of done

1. A written report covering all seven items in §4, each marked pass / fail / not-testable, each
   failure carrying a `file:line` and a reproduction (route + viewport size + steps).
2. Any fix applied is listed explicitly with its diff summary and the file it touched, and every
   touched file is inside §3's boundary.
3. If — and only if — you changed code, run the sweep **once** at the end (`lanes/README.md` §"Rules every lane inherits"):

```powershell
npm run type-check
npm run lint
npm run check:data-boundary
npm run test
```

Baseline to match, from `plan.md:203-205`: **299 Next.js tests passing**, type-check clean, lint
**0 errors**, `check:data-boundary` clean. Any regression against that baseline is a fail.

4. Stop the containers again when finished so the machine is left as you found it:
   `podman stop plantgeo_martin_1 plantgeo_redis_1 plantgeo_postgis_1`.
5. If you changed nothing, say so plainly and skip step 3 — do not run the suite to pad the report.

## 7. Open questions

| # | Question | Recommendation |
|---|---|---|
| 1 | **Credentials.** Every item except 4.6 and part of 4.7 needs a signed-in user who already belongs to an organization, and the auth DB is production. This brief cannot supply one. | Ask the owner for a test account, or register one through `/register` against production and note the address in your report so it can be cleaned up. Do **not** invent credentials or mutate an existing user's org. |
| 2 | **Local `interventions` data.** It is unknown whether `plantgeo_postgis_1` currently holds any `geo.features` rows for the `interventions` layer. If it holds none, item 4.5's render half is untestable locally. | Run the tile `curl` in trap 1 before testing. If the tile is empty, mark 4.5's render as **not-testable** with that evidence rather than reporting a broken layer — and note that `NEXT_PUBLIC_DYNAMIC_TILES_URL` is unset, so the client falls back to `http://localhost:3100` (`src/lib/map/sources.ts:17-18`), i.e. there is no production tile source to compare against. |
| 3 | **Scope of "small obvious fix."** A one-line copy change or a missing `min-h-0` qualifies. Anything touching `MapView`'s style-load lifecycle, the `ApplicationShell` deny lists, or the middleware gate does not. | When in doubt, report and do not apply. Lanes B, D, F and G are running concurrently and a merge conflict costs more than a deferred fix. |
| 4 | **Whether 4.1 and 4.3 deserve a regression test.** Both are layout defects invisible to the existing suite, and both recur trivially if `min-h-0` is ever dropped. | Recommend a jsdom assertion that `org/layout.tsx` and `conversations/layout.tsx` render a `<main>` carrying `min-h-0`, `flex-1` and `overflow-y-auto`. Propose it in the report; do not write it in this lane — `src/__tests__/**` is not in §3's boundary. |
