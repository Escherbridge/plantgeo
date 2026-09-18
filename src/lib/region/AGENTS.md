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

## Why `envelope`, `defaultCameraEnvelope` and `subEnvelopes` differ

See `foundation/region/AGENTS.md`'s "`default_camera_envelope`" and "Why `envelope` and
`sub_envelopes` disagree" sections for the full account. Short version: `envelope` is the
platform's named "Pacific Northwest" footprint (`-126,41,-110,50`, the widest of the region's own
boxes); `defaultCameraEnvelope` is `coverage-region.ts`'s `FALLBACK_COVERAGE_BBOX` value
(`-125,42,-111,49`) — kept as its own field, not read from `envelope`, so pointing that constant at
the manifest stayed behaviour-neutral; `subEnvelopes.burn_severity` is MTBS's own admitted bbox
(also `-125,42,-111,49` — the same numbers as `defaultCameraEnvelope` by coincidence, not by rule);
and `subEnvelopes.botanical_seed` is the botanical-occurrence classifier's `SEED_ENVELOPE`
(`-125,41,-110,50`). All are real, distinct claims; none is a typo of another.

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
later) `.subEnvelopes.<purpose>` — instead of importing a new constant. `getRegion()` is a plain lookup
today because only one manifest exists; a later multi-region deployment adds a
`NEXT_PUBLIC_PLANTGEO_REGION`-keyed registry inside this function, not a second exported constant
callers have to know to switch to.
