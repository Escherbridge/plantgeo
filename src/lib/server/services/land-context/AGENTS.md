# Land-context reference plane

The land-context reader answers "who governs this place, and who do I contact about it" from a
reference plane of boundary versions, organization offices, public contact routes, place/office
topic relationships and source releases.

## parquet-reader.ts reads the warehouse for real, and the warehouse has nothing to give

Rewritten 2026-09-18. The bodies are no longer placeholders: each one does the §4a request shape
(`conductor/code_styleguides/layer-lanes.md`) -- **one pointer GET** against the warehouse coverage
census, then **one data GET** against the partition that census proved published.

**No land-context lane is registered in `pipeline/parquet/lane_registry.py` today.** That was
confirmed by grep across the whole Python service: `planes/` holds sixteen modules and none is
land-context; `land_context` / `land-context` appears nowhere in
`services/agri-data-service/src/`. The two slugs this module names --
`land-context-boundaries` and `land-context-contacts` -- are therefore a **declared expectation**,
not a published fact. The census, never the constant, decides: an unregistered slug simply never
appears in it, and every read reports that as the TYPED `CoverageState` member
`source_unbound_for_region` (with the census's sentence as the refusal's `detail`), which is what
the panel, the agent tools and the slider all branch on. It was a substring of an English gap
sentence until 2026-09-18, while the typed field said `partial_area_coverage` — a positive coverage
claim standing in for a governed absence (STYLE-REVIEW-W2 B3).

So the module's outcome is unchanged in practice and changed entirely in kind. **An empty answer
here is still not a coverage finding** -- but it is now a statement the census produced, and it
names which of five things happened: the lane is unregistered, every rung is withheld by its
availability index, the lane is registered but has written nothing, no published rung admits a
bbox this wide, or the transport failed. A transport failure is never rendered as "the warehouse
published nothing": it carries `upstream_unavailable`, a state of its own, per `parquet-envelope.ts`.

"Every read" means all five entry points, and that is checkable rather than asserted:
`pruneCandidatesByBbox`, `findContainingFeatures`, `findBoundaryByParcelKey`,
`findRelationshipsAndRoutes` and `readCoverageStatus` each return `refusal: CoverageRefusal | null`,
and `reader.ts` forwards `refusal?.coverageState` at every one of its five empty answers. Until
2026-09-18 three of the five dropped the typed member one line after computing it, and this
paragraph generalised from the two that did not (STYLE-REVIEW-W4 B3). The BOUNDARY of the typed
channel: it covers the POINTER phase only. `exactIntersectCandidates` and `readProductRows` return
prose gaps and `refusal: null`, so a refusal originating in the data read has no typed state yet --
correct while that read cannot refuse, and the first thing to extend when it can.

### Two products, denormalized, because §4a allows one Parquet GET

`land-context-boundaries` is `boundary_versions` joined to its `source_releases` row;
`land-context-contacts` is `place_office_topic_relationships` joined to `organizations` and
`public_contact_routes`. Five relations, two products. Publishing them as five separate lanes
would make one contact lookup three data GETs, which §4a forbids. Column names mirror the
relational declarations in `src/lib/server/db/schema/land-context/` -- `family`, not
`family_type`; `geom_wkb` names the encoding, because Parquet has no PostGIS type and the frozen
contract carries hex WKB.

The row schemas are deliberately **not** `.strict()`, unlike the readers in
`parquet-trpc-readers/`. Those mirror registered lanes whose columns are frozen; these state a
minimum a lane that does not exist yet must publish.

`source_releases` declares no publication-date column, so `sourcePublishedTime` is `null` rather
than a restatement of `source_watermark_at`, which is a change clock and not a release date.

### Rung selection is zoom AND bbox size

`RUNG_MAX_BBOX_SQUARE_DEGREES` bounds what each rung will answer for (4 / 100 / 1600 / 64800
square degrees at z13 / z9 / z5 / z0), and `selectServingRung` takes the finest published rung
whose ceiling admits the request. This is the third of the three fix directions the 2026-09-14
handoff left undecided for the botanical plane, applied here because no published lane depends on
it yet: selecting on zoom alone is what put a normal regional viewport on a rung bounded at 100
square degrees and refused it, while the rung below would have answered at 16x the budget.

### Overlap basis is always `bbox_intersection`, even on the point read

The plane filters by rectangle, so a returned feature is a **candidate** whose containment nobody
has proved -- `findContainingFeatures` probes the point as a 0.0001-degree square because a
degenerate rectangle is not a readable bbox. Reporting `point_containment` would be the reduction
the reference-plane spec forbids: "Intersection finds candidate reported features, not legal
proof." A real containment test needs the decoded polygon and a point-in-polygon pass that this
module does not have.

### What still stops short, and why

- **`findBoundaryByParcelKey` resolves nothing.** The frozen Parquet wire
  (`parquet-plane-client.ts` section WIRE) offers day, window, release and coverage reads and no
  key-addressed one. Resolving a parcel key means either a key-index product this plane does not
  publish or a full scan of a boundary lane, and an unbounded scan is exactly what the spec
  forbids. The pointer GET still runs, so the gap distinguishes "no lane at all" from "a lane that
  is simply not key-addressable".
- **`readCoverageStatus` always answers `null` (unknown).** The census reports what a *lane*
  published, never which counties a *source* covered. Answering `false` would claim a
  proven-coverage area nothing has proved, and `reader.ts` would render it as
  `no_match_in_proven_coverage`. Closing this needs a per-region coverage product.
- **`findRelationshipsAndRoutes` reads without a bbox** and filters in memory. That is the one
  unpruned read here, bounded by the product's nature -- a relationship/office/route join is a
  small reference table with no geometry to prune on -- plus the serving row budget, whose
  `truncated` flag becomes a gap rather than a silent subset. An over-budget subject is refused,
  never truncated.
- **The ingest itself is still unchartered.** BLM is the only family cleared to acquire
  (rights-gate verdicts, 2026-09-12).

## Downstream wiring (complete as of 2026-09-15)

`reader.ts` passes `boundary` through as `sourceFeature` unchanged; the router decodes
`boundary.geometryWkb` (hex WKB/EWKB, or `null` when the source has no geometry -- never an invented
shape) via `./geometry/attach-decoded-geometry.ts`; the map draws the result.

`boundary.familyType` must use the keys `useLandContextQuery` maps to toggle groups:
`parcel` | `land_use` | `electric_service_territory` | `blm_surface_management` |
`state_managed_land`.

`decodeBoundaryGeometry` is called on the SERVER, in `attach-decoded-geometry.ts`, and never in
`useLandContextQuery.toFeature`. The 2026-09-14 handoff's "Finding 4.2" (a hardcoded empty
`GeometryCollection` at `useLandContextQuery.ts:63`) went stale on 2026-09-15: that constant is
now only the fallback for a source that carried no geometry, and `toFeature` reads
`result.geometry` from the router. Moving the decode into the hook would break
`scripts/check-client-server-imports.mjs`, which forbids browser code from importing
`@/lib/server/**` with no type-only exemption.

Since 2026-09-18 the map also reads this plane automatically on pan and zoom through
`src/hooks/useLandContextViewport.ts` (see `src/hooks/AGENTS.md` section
land-context-viewport). `MAX_AOI_AREA_SQUARE_DEGREES` = 1 is what bounds it, so the automatic read
only fires from roughly z10 in; widening it is an owner decision about server load, not a knob.

## §region-binding

`region-binding.ts` answers one question for every server surface here: **does THIS deployment's
region bind a land-context source?** It asks the one binding rule (`layerBindingInRegion` with a
`null` payload, so the compiled manifest answers) rather than re-deciding, because two helpers
answering the same question differently is the defect W5 B1 consolidated away.

**Why it exists (STYLE-REVIEW-W8 B1).** `region.ts` justified keeping `REGION_SUBDIVISION_CODES`
PNW-derived by claiming a second region never reaches those literals, because
`layerBindingInRegion` answers `unbound` first. That helper gated exactly ONE caller, the map hook
`useLandContextViewportBoundaries`. The tRPC router, the bounded readers and the agent tools were
all region-independent, so a `kenya-highlands` deployment would have offered
`resolve_land_boundary_by_parcel_key(state: "WA")`, described its own scope as "WA/OR/ID", and
answered a Nairobi AOI `budget_exceeded: outside_pilot_states` — a BUDGET refusal, which tells a
reader to ask something smaller, where the truth is that the platform holds nothing for this layer
anywhere in the region. `federation.md` §2 requires that absence to reach the slider catalogue,
the legends AND the agent tools.

**The shape of the fix.** Every reader answers the typed `source_unbound_for_region` before it
reads anything; `callLandContextTool` refuses with `not_available_in_region` before it parses
arguments, mirroring the Python tree's `agent/tools.py::_region_absence` (the tool stays
REGISTERED — a vocabulary that changed per region would make the agent claim not to know a surface
the platform has); and every caller-supplied state is validated by
`admittedSubdivisionCodeSchema`, which reads the SELECTED manifest at parse time.

**What stays the pilot's compile-time tuple, and why that reason is checkable.** `PILOT_STATES`
still backs Drizzle's `pgEnum` and `parquet-reader.ts`'s stored-row schema. Both need a literal
tuple at module scope, and both describe the pilot's own PHYSICAL plane, which a second region
could only gain through a migration and a lane of its own.
`src/__tests__/region/land-context-second-region.test.ts` fails the moment any registered manifest
binds a `land-context` source — the exact day that reason stops being true.

## Federation note

`PNW_STATE_CODES` / `PnwStateCode` in `src/lib/server/db/schema/land-context/shared.ts` are now
deprecated aliases reading `getRegion().adminCodes` (`federation.md` §5 step 2 landed this).
Do not copy the pattern into new land-context code; take the region as a value.
