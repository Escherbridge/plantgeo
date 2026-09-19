# `src/lib/region` — the web tree's half of the manifest contract

`conductor/code_styleguides/federation.md` §1 wants one typed manifest per deployment. The service
tree owns the frozen Pydantic model and the data
(`services/agri-data-service/src/agri_data_service/foundation/region/`); this directory owns the
TypeScript shape (`region.ts`'s Zod schema) and a hand-written literal that must equal it
(`pnw.ts`'s `satisfies Region` object). `src/__tests__/region/manifest-parity.test.ts` is the only
thing that proves the two agree; nothing here reads `pnw.json` at runtime.

## Why a literal object instead of importing the JSON

`pnw.json` lives in the Python package tree, outside `src/`. Importing it directly here would
either need a build-time path across the two trees or a runtime `fetch`/`fs.readFileSync` for a
constant that is the same eleven bytes-per-field every time the process starts. A hand-written
`satisfies Region` object costs one extra place to edit when a value changes, in exchange for
being an ordinary browser-safe module with no filesystem dependency — the same trade
`coverage-region.ts`'s own literals already made. The parity test is what keeps the extra place
from silently drifting.

## `platformLayers` is the vocabulary; `enabledLayers` is this region's bindings

Two different lists, and the web tree needs both to answer one question honestly. `platformLayers`
mirrors the service's `PLATFORM_LAYER_SLUGS` — every layer ANY region may bind a source for — while
`enabledLayers` is what THIS deployment binds. A slug in the first and absent from the second is a
governed absence the manifest STATES (`land-context` today); a slug in neither is not a federated
layer at all and binding is simply not a question that applies to it. Without the vocabulary
compiled in, `layerBindingInRegion` (`src/lib/map/layer-region-binding.ts`) could not tell those
apart offline and answered the second case from an omission (STYLE-REVIEW-W5 B1). `regionSchema`
refuses a manifest whose `enabledLayers` names a slug its own `platformLayers` omits, and the parity
test diffs the list against `pnw.json` order for order.

## Why `envelope`, `defaultCameraEnvelope` and `subEnvelopes` differ

See `foundation/region/AGENTS.md`'s "`default_camera_envelope`" and "Why `envelope` and
`sub_envelopes` disagree" sections for the full account. Short version: `envelope` is the
platform's named "Pacific Northwest" footprint (`-126,41,-110,50`, the widest of the region's own
boxes); `defaultCameraEnvelope` is `coverage-region.ts`'s `FALLBACK_COVERAGE_BBOX` value
(`-125,42,-111,49`) — kept as its own field, not read from `envelope`, so pointing that constant at
the manifest stayed behaviour-neutral; `subEnvelopes.burn_severity` is MTBS's own admitted bbox
(also `-125,42,-111,49` — the same numbers as `defaultCameraEnvelope` by coincidence, not by rule);
and `subEnvelopes.botanical_seed` is what the botanical-occurrence classifier's
`botanical_seed_envelope()` reads (`-125,41,-110,50`). All are real, distinct claims; none is a typo of another.

## Remaining footprint literals `coverage-region.ts` still carries

This step points only the `"Pacific Northwest"` row of `NAMED_COVERAGE_REGIONS` and
`FALLBACK_COVERAGE_BBOX` at the manifest. `"California"`, `"Western United States"` and
`"North America"` stay literal tuples in `coverage-region.ts` — they describe footprints this
deployment does not serve, so there is no manifest to read them from yet. A future multi-region
manifest registry (`federation.md` §1: "Region names never appear in symbol names... a future
multi-region bucket adds `region=<slug>/` as an outer partition") is where those rows move, not
this push.

## `getRegion()` is the only sanctioned read

Everything that used to read `FALLBACK_COVERAGE_BBOX` or a named-region literal for the PNW row
should call `getRegion()` and read `.envelope` for the named-region footprint,
`.defaultCameraEnvelope` for the opening-camera fallback, or (for MTBS/botanical callers migrating
later) `.subEnvelopes.<purpose>` — instead of importing a new constant. That registry now exists:
`getRegion()` reads `NEXT_PUBLIC_PLANTGEO_REGION`, looks the slug up in
`REGISTERED_MANIFEST_BY_SLUG` and caches the parsed manifest BY SLUG, so the selection is re-read on
every call and no caller ever picks a manifest itself. An unset value is the pilot; an unrecognised
one throws rather than serving the pilot's footprint under another region's name.

## Admin codes are declared once and derived twice

`pnw.ts` keeps `PNW_ADMIN_CODES` as a `const` tuple and spreads it into `adminCodes` under a
`satisfies` clause, so the string literals survive into the type system AND the spread cannot be
edited apart from the tuple. `region.ts` derives everything else from that one tuple:
`RegionAdminCode` is `(typeof PNW_ADMIN_CODES)[number]`, and `REGION_SUBDIVISION_CODES` is
`subdivisionCodesOf(PNW_ADMIN_CODES)`, which maps the codes to their two-letter suffixes while
keeping the TUPLE shape `z.enum` and Drizzle's enum builders require.

Both the value and the type therefore come from the same tuple. Until 2026-09-18 the value came
from `getRegion().adminCodes` and only the type came from the tuple, joined by an `as` cast that
checked nothing — and, being a module-level `getRegion()` call, it was the import-time region read
`federation.md` §1 forbids, with `budgets.ts`, `shared.ts` and `land-context-contract.ts` as three
importers of the hidden dependency (STYLE-REVIEW-W4 S1/S2). On the day this function gains the
`NEXT_PUBLIC_PLANTGEO_REGION`-keyed registry above, a module-level constant would have frozen
whichever region resolved first.

The manifest is still checked against the tuple, in the one place a read belongs:
`assertAdminCodesMatchDeclaredTuple` runs inside `getRegion()` on first call, and a manifest whose
`adminCodes` are not exactly `PNW_ADMIN_CODES` fails closed there.

`PNW_STATE_CODES` (`db/schema/land-context/shared.ts`), `PILOT_STATES`
(`services/land-context/budgets.ts`) and `PilotState`
(`lib/environmental/land-context-contract.ts`) are now aliases of that one definition. Each was a
separate hand-written `["WA","OR","ID"]`, and `PNW_STATE_CODES` joined its runtime value to its
declared type with `as unknown as` -- so a changed `pnw.ts` would have left every typed surface
promising three codes the value no longer had (STYLE-REVIEW-W2 S1/S2).

## The second manifest is data, not a fixture

`kenya_highlands.ts` is a second real manifest, mirroring
`foundation/region/kenya_highlands.json`, registered in `region.ts` and selected with
`NEXT_PUBLIC_PLANTGEO_REGION=kenya-highlands`. It could have been a fabricated object inside a test
— `src/__tests__/region/layer-region-binding.test.tsx` already fabricates the coverage PAYLOAD a
global-only region would emit — and it deliberately is not.

A fixture proves the code paths. It cannot prove that a second manifest compiles under
`satisfies Region`, that `crs: null` and an empty `subEnvelopes` survive `regionSchema`, that the
admin-code check works against a tuple that is not the pilot's, or that the two trees' copies of a
non-pilot manifest agree. It also is not the thing the next deployment copies. This file is: an
engineer standing up region three edits a copy of it and a line of the registry, and the tests tell
them what they got wrong.

**What the second manifest changes about the pilot: nothing.** `PNW` stays the default,
`REGION_SUBDIVISION_CODES` and `RegionAdminCode` stay derived from `PNW_ADMIN_CODES` (the only
surfaces they serve are land-context's, and `land-context` is bound by no region), and every
existing caller of `getRegion()` reads the pilot exactly as before unless the env var says otherwise.

**What it deliberately does NOT carry.** No data, no tiles, no lanes. The manifest declares what a
deployment over that footprint would serve and what it will honestly refuse; `layerBindingInRegion`
answers `unbound` for burn-severity, drought, soil-survey, land-context, evacuation-zones,
fire-perimeters, sensors and water-gauges, `LayerRow` disables those toggles with the
"not available in this region" caption, and `useLayerVisibility` keeps them false so no layer
component mounts and no fetch is issued for them. Proving that chain is the whole of the web-side
second-region proof.
