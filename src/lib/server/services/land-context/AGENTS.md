# Land-context reference plane

The land-context reader answers "who governs this place, and who do I contact about it" from a
reference plane of boundary versions, organization offices, public contact routes, place/office
topic relationships and source releases.

## parquet-reader.ts is a placeholder, not a finding

No real data source is wired in yet. Every function performs the pruning structure the spec
requires -- bbox/row-group pruning conceptually first, exact intersection second -- but always
resolves to an empty-with-gap-stated result, because there is no admitted Parquet lane to read.
**Do not treat any `no_match` or `unknown_coverage` outcome from that module as a real coverage
finding.** It reflects "nothing is wired in", never a verified absence.

What remains before the bodies can do real reads: the chartered ingest itself -- BLM is the only
family cleared to acquire (rights-gate verdicts, 2026-09-12) -- and the physical lane layout. The
schema it once waited on has landed (`@/lib/server/db/schema/land-context`,
`drizzle/0003_land_context.sql`). Keep the pruning-then-intersection call shape so callers in this
directory do not change.

## Downstream wiring (complete as of 2026-09-15)

`reader.ts` passes `boundary` through as `sourceFeature` unchanged; the router decodes
`boundary.geometryWkb` (hex WKB/EWKB, or `null` when the source has no geometry -- never an invented
shape) via `./geometry/attach-decoded-geometry.ts`; the map draws the result.

`boundary.familyType` must use the keys `useLandContextQuery` maps to toggle groups:
`parcel` | `land_use` | `electric_service_territory` | `blm_surface_management` |
`state_managed_land`.

## Federation note

`PNW_STATE_CODES` / `PnwStateCode` in `src/lib/server/db/schema/land-context/shared.ts` are now
deprecated aliases reading `getRegion().adminCodes` (`federation.md` §5 step 2 landed this).
Do not copy the pattern into new land-context code; take the region as a value.
