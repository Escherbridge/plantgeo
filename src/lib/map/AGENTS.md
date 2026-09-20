# `src/lib/map` — module notes

Browser-safe map contracts and pure helpers: what a layer *is* (`layer-registry.ts`), which
partition rung serves a zoom (`zoom-tiers.ts`), and — since 2026-09-01 — which spatial *form* a
layer is allowed to be drawn in (`layer-render-contract.ts`). No network, no database, no React.
The map components in `src/components/map/` read these; nothing here reads them back.

## The layer render contract

`layer-render-contract.ts` freezes the render table from
`conductor/tracks/multiscale_polygon_surface_20260901/spec.md` and the RUNBOOK's "Spatial
aggregation and polygon contract" as typed, testable code. It answers a specific production
assessment, on 2026-09-01, of what the browser actually drew:

- **Raw fire dots at coarse zoom.** A continental view painted one circle per FIRMS detection.
  At that scale the dots are neither legible nor honest: they read as a scattering of individual
  fires when what the data supports is a density.
- **Separated rectangular climate blocks.** The continuous fields drew as detached rectangles
  with map background visible between them. A field that leaves gaps is asserting the gaps are
  unmeasured, which is not what the lattice says.
- **Nested ERA5 soil blocks with visible seams.** Source-part and batch boundaries surfaced as
  blocks inside blocks — a rendering artefact of how the data was written, presented as though it
  were structure in the ground.
- **MTBS rendered coherent source polygons.** It is the continuity reference in this directory:
  the one product that already looked right, because it draws its producer's own geometry and
  generalizes it rather than re-deriving a footprint. Everything else is measured against it.

The contract is the *vocabulary* half of the fix. It does not render anything. It says, per layer
and per zoom band, which of nine closed `SupportKind` forms is permissible, and it declares the
envelope (`AggregateEnvelopeSupport`) a reader must return alongside its features.

**Three bands over four rungs.** `ZoomTier` (z0/z5/z9/z13) is a partition path and is named after
its minimum zoom so the name survives the ladder growing a rung. A `ZoomBand`
(`coarse`/`middle`/`detail`) is a rendering decision, and the spec's table has exactly three
columns of those. `ZOOM_TIER_BANDS` maps each rung onto exactly one band, with z0 and z5 sharing
`coarse` — not an arbitrary grouping, but the split the serving side already uses:
`granularityForZoomTier` (`src/lib/server/services/zoom-granularity.ts`, read by the soil-field
and climate-field readers) reports `granularity` as `detail` at z13, `regional-average` at z9 and
`coarse-average` for both lower rungs. A fourth band here would be a vocabulary the server
never speaks.

**Why the envelope exists at all.** Support was previously *inferred* on the client. The
soil-field reader encodes "this row is an aggregate" as a null cell id
(`decodeSoilFieldRows` in `parquet-trpc-readers.ts` rejects a row whose `cell_id` nullability
disagrees with its rung), and cell extent came from a private per-layer tier table the client had
to know about.
`ParquetFireDetectionCell` (`parquet-trpc-readers.ts`) still carries a longitude, a latitude
and a count, and nothing at all about what area that count covers. Two consequences: a rung whose
rows happened to carry ids was indistinguishable from raw observations, and a renderer had to
guess a footprint. `AggregateEnvelopeSupport` replaces both guesses with declarations — a stable
non-null `supportId`, an explicit `supportKind`, the cell's width and height, its aggregation
method, its contributor count and its provenance. `origin` is the one field easy to mistake for
decoration: `soilFieldPolygon` (`parquet-trpc-readers.ts`) already offsets by half a cell for
the base lattice and not at all for the aggregated rungs, and getting that wrong shifts an entire
field by half a cell — which reads as a registration error, not a bug.

**Tripwire: fire density cells are not perimeters.** The polygons this track adds under the
`fire` layer are satellite *detection-density* cells. A cell says "n hotspots were detected in
this square". It does not say "this square burned". Rendering one as `native_polygon` publishes an
authoritative burned extent that nobody measured, and it does so in the same visual language as
the two products that legitimately carry that claim — `fire-perimeters` (active incident
perimeters) and `burn-severity` (MTBS burned-area boundaries), which are separate layers with
separate source geometry. `assertNotPerimeter(layerId, supportKind)` throws
`PerimeterMisrepresentationError` for any `event_point` layer handed `native_polygon`. It throws
rather than silently downgrading the form, because a wrong form here is a truth claim and a
quietly corrected one would ship the calling bug to the next renderer.

**Vegetation is pinned to its measured support.** It is a `continuous_field`, but with
`declaredSupportDegrees: 0.25` and only `tessellated_cell` permitted at every band. The 0.25° grid
is the ground this platform actually observed; an isoband or a raster surface across it asserts
smooth variation between samples the lane never took. That is the same argument
`src/lib/environmental/climate-field.ts:114` makes when it withholds `isoline` from precipitation
and from the soil-wetness pilot, and the reason `VegetationLayer` draws a deliberate outline from
z9 up: to keep the cells legible as discrete samples. An envelope for this layer may never
declare a `cellWidthDegrees` or `cellHeightDegrees` finer than the declared support.

**Fire's detail band is cells, not raw points.** The spec's `event_point` row reads "raw source
points" in its detail column, and that is right for `water`, `weather` and `sensors` — the layers
its own carve-out means by "Genuine stations remain points at detail zoom". FIRMS is not one of
them: it publishes no raw rung at all, and its z13 rows are cells
(`cell_longitude`/`cell_latitude`/`detection_count`). So `fire`'s detail band permits
`aggregate_cell` and, deliberately, **only** that. Dropping `raw_point` rather than adding
`aggregate_cell` beside it was a choice with two reasons: permitting a form the lane cannot serve
licenses a renderer to claim individual detections that do not exist, and it would have forced the
invariant "no band permits both `raw_point` and an aggregate form" to be weakened to a narrower
one about `heatmap`/`cluster`. The invariant is the thing that keeps an aggregate from being
captioned as an observation, so the contract narrowed instead of the rule.

**A deviation is recorded, never legalised.** `shippedDeviation` on a contract entry names a form
the renderer draws today that `permittedForms` still excludes, with an owner and a date. Widening
the band to match would make the contract describe the code instead of governing it, and would
erase the one artefact that makes the gap findable. The test asserts every deviation names an
owner and a date, because an anonymous undated one is just a second contract.

*Closed 2026-09-02:* vegetation carried one for a few hours. `presentParquetVegetation` emitted a
Point at each cell's centre and `VegetationLayer` painted it as a zoom-scaled circle — `raw_point`
on this vocabulary. Slice m3 made both draw the declared 0.25° square, so the entry is gone
because the gap is, not because the band was widened; `raw_point` is still permitted at no
vegetation band, and the test asserts the entry has no `shippedDeviation` at all.

*Open:* `soil-survey`. It is a `native_polygon` product that stops drawing its producer's geometry
at the default PNW camera: `readSummaryFeatures` (`server/services/usda-soil.ts`) answers one
counted Point per lattice cell once the viewport exceeds the polygon-union budget, and
`soilSurveySummaryLayer` (`map/layers.ts`) paints those as count-scaled circles — an aggregate
point summary, recorded as `aggregate_cell`. It is a real answer to a real constraint: a ~98
sq deg viewport cannot be unioned honestly against a 0.48 sq deg budget, and the union path's
`LIMIT` would otherwise pick an arbitrary subset of delineations to merge. Closing it is an owner
decision between declaring a tessellated cell for the summary rung and re-classing the layer, not
a renderer fix, which is why it is recorded rather than patched. `burn-severity` is the
counter-example and must stay deviation-free: it draws MTBS's own geometry at every band.

**The contract also builds the square.** `supportCellPolygon(longitude, latitude, support)` is the
client's ONE derivation of a cell's extent, and it derives it from the envelope's own numbers
rather than from a private per-layer tier table — the guess `AggregateEnvelopeSupport` exists to
replace. It lives here because the envelope does: a footprint derived anywhere else would be a
second place a renderer could disagree about what a cell covers.

**There is exactly ONE function that computes a cell edge, and it is `latticeCellSpan` in
`zoom-tiers.ts`.** Both edges of every square — on the serving side through
`tessellatedCellPolygon`, on the client side through `supportCellPolygon` — are
`offset + index * size` evaluated by that one helper, so cell *i*'s east edge and cell *i+1*'s west
edge are the SAME expression over the same operands and are equal to the bit. That is the spec's
"neighbouring cells share bit-identical boundaries" gate. Computing east as `west + size` instead
disagrees with the neighbour in the last bit for about 30% of cells on the 0.005°, 0.01° and 0.2°
grids — measured, not feared — and those sub-ULP disagreements are the hairline cracks of map
background the gate forbids. The contract's own `latticeEdges`, a second implementation of the
same arithmetic, was deleted on 2026-09-02.

**What made the two agree is `cellOriginDegrees`, closed 2026-09-02.** The serving lattice has a
PHASE as well as a pitch, and the phase was not on the wire — so `supportCellPolygon` assumed a
lattice anchored at whole multiples of the cell size and disagreed with `servedCellLattice` by up
to half a cell wherever that assumption was wrong. **Vegetation at z9 and z5 was exactly that
case**: a 0.25° base grain KEPT across the ladder's 0.01 and 0.2 grids. `cellSupport`
(`parquet-trpc-readers.ts`) now states the snapped south-west corner on every per-cell envelope,
and `supportCellPolygon` takes it verbatim.

**THE PHASE IS A PER-LANE FACT, and it is declared, not inferred** (`LaneBaseLattice.centroidOffsetDegrees`,
corrected 2026-09-02 after being inverted for the two lanes it matters most for).
`ServedCellLattice.originOffsetDegrees` is *zero* for the ladder's own grids AND for the
quarter-degree vegetation and soil-field lanes, and *minus half a cell* only for the one-degree
climate lattice. The quarter-degree lanes centre each cell a half step above `row * 0.25`
(`ingest/vegetation.py:344-347`; `ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES = 0.125` and a westmost
centroid of `-124.875` in `pipeline/direct/soil/support.py:51-57`), so their CENTROIDS are the odd
multiples of 0.125 and their EDGES are the multiples of 0.25 — while `CLIMATE_FIELD_LATTICE_ROWS`
samples the whole degrees, so its cell has to straddle its sample. Reading the second shape onto
the first drew every vegetation and soil-field cell half a cell off at every rung: at z5, 13 of the
56 longitude columns held two measurements and 13 were empty stripes of basemap. The domain sweep
in `src/__tests__/lib/map/zoom-tiers.test.ts` walks all 1,568 real centroids at z13/z9/z5 and
asserts 1,568 squares, no collisions, no interior gaps, and each square containing its own
centroid.

The corner is enough, and the far edge is not `corner + size`: the phase and the index are both
recoverable from the corner (`round(corner / size)`, and whatever residue that leaves), so the
client re-evaluates `latticeCellSpan`'s own expression and lands on the same double the neighbour's
west edge did. An envelope carrying no corner — a payload replayed from before 2026-09-02 — derives
one from the anchor and reads the phase back the same way, which keeps a producer's deliberate
off-lattice phase where it put it rather than snapping the cell half a width away.

The collection-level envelopes (`soilFieldSupport`, climate's `collectionSupport`) carry no corner
and must not: they describe a lane's whole lattice rather than one cell, and their features are
built server-side by `tessellatedCellPolygon` already.

**The permitted-form table is keyed by the RUNG, and it is enforced at presentation time.**
`permittedFormsForTier`/`isFormPermittedForTier` take a published rung because presentation never
holds a zoom — it holds features whose envelopes declare the rung they were read at, and a retained
frame outlives the zoom it was fetched for. Resolving that frame's forms through the current zoom
would ask the contract about a band its cells were never aggregated for, which is why the
zoom-keyed twins (`permittedFormsFor`, `isFormPermitted`) were deleted on 2026-09-02 rather than
kept beside them. `assertFormPermittedForTier` is the production caller the whole table lacked
until then: `presentParquetFireDetections` and `cellFeatures` (`parquet-climate-field.ts`) each
check the form they are about to draw against the rung the row was read at, and throw
`UnpermittedRenderFormError` rather than paint a shape the contract never licensed. A rule reached
only from tests describes the renderers; it does not bind them.

**Who may import it.** Anything that decides how a layer is *drawn* or *served*: map layer
components under `src/components/map/layers/`, the presentation modules in
`src/lib/environmental/` that choose a feature's geometry, the tRPC readers in
`src/lib/server/services/parquet-trpc-readers.ts` that build serving envelopes, and panels that
caption an aggregate. It imports only `layer-registry.ts` (for the toggle-id type),
`zoom-tiers.ts` and `climate-field.ts`, so it stays browser-safe and stays free of a cycle back
into the registry. **`zoom-tiers.ts` imports nothing at all** — it is the tree's one true leaf
here — which is why the contract may take `latticeCellSpan` from it without closing a cycle. If
`zoom-tiers.ts` ever needs a name from this module, the lattice helpers move to a third leaf
rather than the import being added.

**`weather` is an `event_point` layer, and that is a live question rather than a settled one.**
The lane is shaped like the streamflow one — a sampled point at z13, a `GridAggregation` cell on
every rung above — and since 2026-09-02 it declares an envelope saying exactly that: `raw_point`
with no footprint at the detail rung, `aggregate_cell` on the ladder's own grid above it. What the
contract does NOT say is whether the sampling LATTICE behind those points is itself a support the
way `climate-field`'s one-degree lattice is. Treating it as one would let the layer fill ground
rather than dot it; treating it as it stands keeps every weather mark a point that claims no ground
at all. **That ruling is m0's and is still open**; nothing in the reader, the presenter or this
contract anticipates it, and a slice that decides it changes `LANE_BASE_LATTICES`, the contract
entry and `WeatherLayer` together or not at all.

**Removed.** `time-format.ts`'s `resolveObservationIso` (deleted 2026-09-02) resolved a
per-detection FIRMS `acqDate`/`acqTime` pair; the 2026-09-01 Parquet cutover removed the last
producer of those keys, since cells carry `newestObservedAt`, which `toIsoTimestamp` handles
directly.

**What may not happen.** The `SupportKind` union is closed. A renderer that needs a tenth form
edits this module and its test, never its own call site — an unlisted form drawn locally is
exactly the drift the contract exists to end. The registry is a total `Record<LayerToggleId, …>`,
so a new toggle fails to compile here rather than reaching the map with no declared form.

## Two facts about `layers.ts` that a zoom measurement must not be taken without

Recorded, not changed — both are statements about the shipped renderer that an acceptance
measurement will otherwise mis-read.

**Three native-polygon layers carry `minzoom: 4`, so z0–z3.99 draws nothing at all.**
`firePerimetersLayer` (`layers.ts:175`), `evacuationZonesLayer` (`:255`) and `burnSeverityLayer`
(`:313`) each set it, as do their outline twins (`:192`, `:272`, `:333`) and `sensorsLayer`
(`:224`). At the coarse band's lower half the map is not showing a generalized
perimeter, an empty tile or a failed request: it is showing a layer MapLibre was told not to draw.
This is owed to the acceptance track's z2 tile-byte measurement, which cannot distinguish "the
tile is small because the geometry generalizes well" from "the tile is never requested" without
it. Whether 4 is the right floor is a separate question from knowing that it is the floor.

**`getMetricAtDate` simplifies polygons; the tile path does not.** The two ways the same
native-polygon product reaches the screen therefore differ in vertex count and in edge position,
and a comparison of one against the other is measuring the simplification and not the product. Say
which path a number came from whenever one is quoted.

## The hover tooltip and the caption modules

USDM `validDate` is a publisher calendar day, not an instant. Its tooltip uses
`formatCalendarDay` with UTC formatting so a September 1 release stays September 1
in Denver. The presentation also carries a synthetic midnight `observedAt`; localizing
that timestamp used to display August 31. Other event timestamps keep local date/time
formatting. Never infer the release day by localizing the synthetic observation instant.

`hover-fields.ts` is the pure per-layer field selection for the shared hover manager, and
`fire-cell-caption.ts` and `water-cell-caption.ts` are the one caption a fire cell and a coarse
streamflow cell get in BOTH the tooltip and their layer's click popup — and, for water, in the
legend as well.

The direction of the dependency matters: `hover-fields.ts` imports both caption modules and
neither imports back. `water-cell-caption.ts` was extracted on 2026-09-02 for exactly that reason.
Its three exports — `WATER_CELL_CAPTION_TITLE`, `WATER_CELL_AGGREGATE_NOTE` and the degree
formatter `formatSupportCellSize` — had lived in `hover-fields.ts`, so `WaterLayer.tsx` and
`layer-legends.ts` each imported a *tooltip* module to caption a popup and a legend. A leaf module
lets all three read the same words from a module that owns nothing else. `fire-cell-caption.ts`
still keeps its own degree formatter: merging the two is a tidy-up rather than a correctness
question, and both format identically (`Number(value.toFixed(4))`).

**A polygon needs a polygon hit-test.** `HOVERABLE_LAYER_IDS` lists a layer per *shape*, not per
toggle: `published-fire-cells-fill` and `water-gauge-cells-fill` are there beside their circle
counterparts because a circle layer cannot hit-test a Polygon — the same reason
`interventions-points` and `soil-survey-summary` are listed. Both shapes of one aggregate share a
formatter, so the square and the dot can never caption the same cell differently.

`burn-severity` and `drought-fill` are native-polygon products an event aggregate must be
distinguishable from. A reader who cannot hover a real burn scar to see whose it is has no way to
check that the fire cell beside it is a different kind of thing.

## The interventions toggle draws two sources

`interventions` is ONE registry entry over SIX style layers since 2026-09-13
(`conductor/tracks/unified_intervention_layer_20260913`, OQ-1): the three Martin-tile layers over
`intervention_tiles` (published rows only) and the three `intervention-drafts-*` layers over the
client-side `intervention-drafts-source` GeoJSON (`useInterventionDraftsOverlay` — the caller's own
submissions plus the consenting `pending_review` queue). The drafts' layer and source specs are
unchanged; only their separate toggle is gone, because which source a site's bytes came from is an
implementation fact and a reader looking for their own just-submitted intervention should not have
to know it. `applyVisibility` needed no change: it folds over `styleBackedLayerEntries()`, so all
six ids flip off one boolean.

Since 2026-09-13 the same six layers also draw PUBLIC STRATEGY REQUESTS (`properties.kind =
"request"`, written by `interventions.submitRequest` straight to `published`, no review queue). They
get the leading arm of `INTERVENTION_STATUS_COLOR` and their own blue (`INTERVENTION_REQUEST_COLOR`,
`#2563eb`) — leading because a request is always `published`, so an arm behind the status check could
never win and every request would paint as a recommendation. `kind` reaches the drafts GeoJSON
directly but **not yet the published tiles**: `geo.intervention_tiles()` must project it, which lands
with the track's Phase 3 migration (and, as ever, a Martin restart). Until then a tile-sourced
request falls through to the category arm — the colour it would have had anyway, never a wrong one.

All six paint with `INTERVENTION_STATUS_COLOR` (layers.ts) — one `case` on `status` over a `match`
on `category`, so `pending_review` is orange whatever its category, everything else is land teal /
air violet, and an unclassified row falls to the shared neutral grey. It replaced a `priority`-keyed
palette for a field `submitIntervention` never writes. `category` reaches the PUBLISHED tiles only
via `drizzle/0002_intervention_tiles_category.sql`, and **Martin must be restarted after that
migration** or the tiles keep serving the old attribute set and every published site draws grey.

## Environmental layer serving

No environmental layer reads Martin. Martin publishes only the relational
`intervention_tiles` source; environmental layers use the governed Parquet plane.

| layer | drawn from | read through | rung mapping |
| --- | --- | --- | --- |
| `sensors` | `sensor-station-features` (GeoJSON) | `environmental.getSensorStations` | z13 stations; z9/z5/z0 are `GridAggregation` cells with no station identity |
| `evacuation-zones` | `evacuation-zone-features` (GeoJSON) | `environmental.getEvacuationZones` | one full re-snapshot per release day, simplified per rung |
| `burn-severity` | `burn-severity-features` (GeoJSON) | `environmental.getBurnSeverity` | union of every release at or before the day, simplified per rung |
| `watersheds` | `watershed-features` (GeoJSON) | `environmental.getWatershedBoundaries` | z13 HUC12, z9 HUC10, z5 HUC8, z0 HUC6 |
| `fire-perimeters` | `fire-perimeter-features` (GeoJSON) | `environmental.getFirePerimeters` | newest snapshot at or before the day, simplified per rung; `GeometrySimplification` only, no dissolve |

**These five layers are still `renderKind: "style"`, and that is not a leftover.** They are baked
into all three styles in `styles.ts`, and `LayerManager`'s appliers write their visibility, their
opacity multiplier and their date filter by walking `LAYER_REGISTRY[toggleId].styleLayerIds`. Moving
them into React components would have meant emptying `styleLayerIds`, which silently disarms all
three appliers and `styleLayerOpacityTargets()` with them. What changed is the source behind the
layer — a Martin vector source became a GeoJSON source that `LayerManager` fills — so the paint
expressions, the layer ids, the legends and the `hover-fields.ts` formatters are all untouched.
Two consequences worth stating: a layer on a GeoJSON source must carry **no `source-layer`** (that
key is required for vector sources and prohibited for every other kind, and MapLibre rejects the
layer outright when it is present), and `LayerManager` must re-`setData` on every `style.load`,
because a basemap swap rebuilds each source from the empty spec `styles.ts` declares.

**The presenters rebuild an MVT attribute table, absences included.** `ST_AsMVT` omits an attribute
whose value is NULL, and the style expressions were written against that: `burnSeverityLayer`'s fill
is `["case", ["has", "acres"], <ramp>, <grey>]` and `tileLayerDateFilter` keeps an undated feature
with `["!", ["has", "observed_day"]]`. A GeoJSON `properties` object carrying `acres: null` answers
`has` with **true**, so `mvtProperties` in `parquet-presentation.ts` drops null keys rather than
writing them. Deleting that helper would repaint every unacreaged scar through the log ramp and
start filtering features that have no date to be filtered on.

**One field is genuinely gone: `basin_count`.** `geo.watershed_tiles()` computed how many HUC12s a
coarse feature merged with a `count(*)` while building `geo.watershed_rollup`; the lane's
`HierarchicalDissolve` declares no counting aggregation, so no such number is published. It is
omitted rather than approximated, which costs `formatWatershed` one line on rollup features.
Restoring it is a `ColumnAggregation` on the watersheds lane in `services/agri-data-service`.

**`fire-perimeters` moved last, and its date handling is the one thing to read before touching
it.** The lane was registered `daily_series` on a per-incident `observed_day` (`polygonDateTime`,
falling back to `fireDiscoveryDateTime`) while `geo.features` holds WFIGS's current-incident set
refreshed *in place* — one row per incident, never one per incident-day — so its 177 perimeters
sat across 45 partition days and no bounded read reproduced the union the map draws. It is now
registered `static_lookup` on `("snapshot_day", "unique_fire_identifier")`: one published snapshot
IS the standing set, and `observed_day` survives as a nullable per-row column.

`getParquetFirePerimeters` therefore answers in two steps, and BOTH matter:

1. **Resolve** the newest snapshot at or before the requested day. This is the generic release
   route (`resolve_release`), the same rule `planes/fire_perimeters.py::resolve_fire_perimeters_as_of`
   states for the Polars path; the reader does not re-implement it. `servedDay` is the snapshot's
   capture day, `requestedDay` the slider day, and a gap between them means the map is drawing the
   newest capture at or before the request rather than a same-day reading.
2. **Filter in frame** on `observed_day IS NULL OR observed_day <= as_of`, where `as_of` is the
   **requested** day and never the answering snapshot's day. An undated incident is kept at every
   date — the identical rule `tileLayerDateFilter` applies client-side — and the retired
   `daily_series` export's `= :observed_day` predicate, which silently deleted every undated row,
   is the regression to avoid re-introducing. `presentParquetFirePerimeters` then has to DROP the
   `observed_day` key for those rows rather than write `null`, or the client-side filter starts
   excluding exactly the rows step 2 kept.

**Checking the fire-perimeters cutover in a browser, at the default PNW camera.** The map opens on
the coverage bbox (`DEFAULT_VIEWPORT`, `src/stores/map-store.ts`), which `resolveZoomBand` puts in
the **coarse** band — so the first request is for a DERIVED rung (z5 or z0, whatever
`resolveZoomTier` returns for that camera), never z13. What to look at, in order:

1. **Network.** One `environmental.getFirePerimeters` tRPC call carrying `bbox`, `zoom` and the
   row's day. There must be **no** request to `.../fire_risk_tiles/{z}/{x}/{y}` and no
   `fire_risk_tiles` entry in the style's sources — that Martin id is gone from
   `DYNAMIC_TILE_SOURCE_IDS`, and a request for it after this change means a stale tab.
2. **Style.** `map.getSource("fire-perimeter-features")` exists and
   `map.getLayer("fire-perimeters").source` names it. A layer that silently fails to appear with
   nothing in the console is the `source-layer` trap: that key is prohibited on a GeoJSON source
   and MapLibre rejects the whole layer for it.
3. **Canvas.** Perimeters fill in the severity palette with a red outline, and the Fire Perimeters
   row's slider still moves them. Scrub to a day before the newest snapshot: the layer must not
   blank — an older snapshot answers, and any undated incident stays drawn at every date.
4. **Today, expect empty and say so.** The lane's 45 pre-re-registration partition days are
   structurally unreadable on purpose, so until one ordinary tick writes a snapshot the read
   answers `not_generated` and the layer draws nothing. That is the honest state, not a broken
   cutover; the discriminator is the network call in step 1 succeeding with a `not_generated`
   state rather than failing.

The presenter rebuilds the tile's vocabulary exactly: `severity` and `observed_day`. The SELECT
list also named `risk_level` and `name`, but no producer has ever written either key
(`ingest/wfigs.py` writes `incidentName`), so `ST_AsMVT` omitted both from every tile ever served
and nothing reads them. `hover-fields.ts`'s `formatFirePerimeter` reads camelCase keys
(`incidentName`, `gisAcres`, `percentContained`, ...) that the tile never emitted either, so today
it shows a title and a severity line; widening it is a hover-fields change with its own review, not
something the cutover should have decided by shipping extra columns.

## deferred-analysis-surfaces

As of 2026-09-10, `isLayerDeferred` excludes untrained Strategy Recommendations from dock groups.
The registry entry remains for future implementation and persisted identifiers; LayerManager
also stops mounting StrategyLayer, so a stale active selection cannot request its tile endpoint.
Deferred entries are exempt from UI reachability checks, but no observed layer is hidden because
its ingestion is behind. Restoration criteria live in the Parquet pivot track's deferred UI section.

Community publication standings describe user-facing review and privacy requirements. The old
claim that no code invoked intervention publishing was stale: its callable expert API and queue
survived, but the queue was unmounted. The 2026-09-10 route repair restores that queue beside the
separate proposal-moderation panel. Keep Interventions and Demand Heatmap exposed; these are real
community workflows, unlike the deferred untrained Strategy Recommendations layer.

### Weather support decision (2026-09-10)

The sampled-grid question above is resolved only for already-declared aggregation
support: weather aggregate cells render their declared footprints; raw detail samples
retain points because their sampling grid does not establish native model support.
No LANE_BASE_LATTICES/native-resolution claim changes. Hover and legend distinguish
Open-Meteo model estimates from stations and daily captured-reading aggregate means
from latest detail samples. The aggregate timestamp is the newest contributing reading,
not the instant at which the mean was measured. The separate sensors layer remains
station-based.

## Measured scalar labels (2026-09-12)

`measured-value-label.ts` builds one collision-aware symbol layer over the existing served
source. It adds no features, source, aggregation, interpolated values, or inferred support.
Only finite numeric `value` properties receive text; missing, string, NaN and infinite values
receive none. Units come from each environmental definition. Moisture and wetness use three
decimal places, VPD two, other scalars one; nonzero values below that displayed precision
use a signed threshold caption rather than falsely reading as zero. Real zero stays zero.

`aggregated: true` prefixes the value with `avg` only for the climate/soil cell callers whose
readers declare means at aggregated rungs. This helper is not a general-purpose statistic
classifier. Climate isobands carry representative band values and never call it. Text remains
11–14 px across zoom, with halo and ordinary MapLibre collision placement; dense cells may
have no placed label. Existing server row caps remain the only feature budget. Polygon label
anchors are MapLibre placement positions within the served geometry, not new observations.

## §scalar-field

`scalar-field.ts` and `scalar-field-layer.ts` implement an opt-in nearest-cell value field.
Vegetation is its only caller. This preserves the existing prohibition on inventing smooth
variation between NDVI supports: the fragment shader maps each unchanged cell scalar through
`NDVI_COLOR_RAMP`, with flat values over exactly the served rectangular polygon. It removes
native fill antialias seams at low spacing without interpolation, density accumulation or
invented gap coverage. Negative NDVI remains valid. Missing/nonrectangular supports, invalid
values/dates, inconsistent units/days/grids/dimensions, shifted overlapping grids or conflicting
duplicates keep the entire collection native. Identical duplicates are deduplicated only in
the custom mesh. Cell-specific support IDs need not match. The mesh caps input at 20,000 cells.

At 64 CSS pixels of projected support spacing the paint hands back to the original native
fill and collision-placed numeric labels. Both renderers use the same value/ramp/opacity, so
the handoff is direct: an alpha crossfade would double-darken shared pixels. The 64px threshold
allows a two-decimal NDVI caption more room than the audit's provisional 32–48px thresholds.
Tiny signed values use threshold captions rather than rounding nonzero values to zero. The
original fill remains present for native feature picking even while its paint is transparent;
hover/tap shows the exact measurement and observed day, grid and cell identity. Blank supports
remain blank and cannot be picked as a custom invented value.

The custom path requires WebGL2, flat Mercator without terrain or pitch, valid complete input,
and at most 16 visible world copies. Uniform-grid spacing needs only the extreme-latitude cells
in this flat projection; unsupported views remain native. Each visible world copy is drawn from
viewport bounds. Shader compilation/link/allocation, uploads, render errors and context loss
restore native paint. A transparent validation draw must succeed before native paint is hidden.
Context restoration retains native paint until a fresh style rebuild; it does not reuse lost
GPU objects. Style removal deletes buffers/program/VAO and listeners. GPU work outside render
restores the prior program/buffer/VAO bindings to preserve MapLibre's state cache. The contract
was checked against installed MapLibre 5.22 custom_style_layer.ts and draw_custom.ts.

The SDK projection uniform uses `defaultProjectionData.mainMatrix`, as declared by
`geo/projection/projection_data.ts` and supplied by `render/draw_custom.ts`. The installed
`CustomRenderMethodInput` comment still says `projectionMatrix`; that comment is stale. The
lifecycle test constructs the complete typed render input so a guessed matrix field cannot
hide behind an assertion cast again.

Label layout visibility changes wait for the native source's tiles to finish loading and retry
on its `sourcedata` event. MapLibre 5.22 can otherwise interleave a layout-triggered GeoJSON tile
reparse with `setData`, pairing new symbol indices with old empty raw tile data; unrestricted
feature queries then throw in `FeatureIndex.lookupSymbolFeatures`. Waiting after the bad
mutation does not repair that index. Gate the mutation itself with `isSourceLoaded` and avoid
redundant `setData` when only opacity changes. Keep the native source mounted throughout.

Source readiness retries also accept the SDK's final tile event without `sourceDataType` and
its `idle` event; only `metadata` is excluded because it precedes replacement tile loading.
On removal, unbind this layer's currently bound program/VAO/buffer before deletion. WebGL
otherwise defers deletion of the current program, allowing the next style initialization to
capture a doomed program as its previous binding and fail when restoring it. Bindings owned
by other layers are left intact.

A missing projection during style replacement is a temporary native-view condition, not a GPU
failure. Both eligibility and render guards accept that transient state and retry on style/source
readiness; real shader, upload, draw and context failures still latch native fallback. The
reattached layer requests a frame for its transparent validation draw. Do not drain unrelated
GL errors to make initialization appear successful.

The source readiness guard alone is insufficient when a populated-view zoom event changes
label layout just before React receives a new collection. The opt-in controller therefore
serializes native GeoJSON `setData` with source-layer layout changes. A pending symbol/native
visibility reparse holds only the latest replacement collection; map idle releases it.
Conversely, pending data prevents label relayout. Native fill/outline source-mode flips use
the same queued layout boundary. The GPU waits for its queued data to reach the loaded native source before painting,
so native inspection and custom values refer to the same collection. Source identity is checked
before draining the queue, and style removal discards it. The default-off component keeps its
normal native `setData` path. Normal populated transitions retain layout-based label collision
and picking semantics. Immediate invalidation additionally suppresses paint and inspection as
described below; it does not bypass the serialized layout mutation.

The controller also owns queued fill/outline visibility, so a source-mode switch cannot bypass
serialization while earlier data is loading. Hiding measured NDVI immediately zeros its fill,
outline, labels and custom paint; deferred layout cannot stack it with the satellite composite.
A layout lock releases only at map `idle`, because a source can report loaded before a scheduled
style reparse has started. Source completion retries data readiness but never unlocks pending
layout. This covers both orderings: layout then data, and data then source-mode layout.

An empty or null replacement invalidates the previously measured collection immediately,
even while label layout owns the serialization lock. Its fill, outline and text paint must
become transparent without a transition, and both an existing tooltip and subsequent
hover/tap or map-click inspection must exclude the invalidated source. MapLibre still returns
transparent geometry from native feature queries, so paint opacity alone is insufficient.
Keep this suppression latched through a later populated replacement until the native source
has accepted and loaded that replacement. Nonempty unsupported meshes remain eligible for
native fallback. Idle still releases the queued data update; source completion alone cannot
release a pending layout lock. The repair is presentation-only and does not change the reader's
acceptance or refusal contract.

## §coverage-region

`coverage-region.ts` turns an ingestion bbox into something a person can read, and gives the map a
camera it can paint before any network call resolves.

**`federation.md` §5 step 1 landed: the first named row and the opening-camera fallback now
read `getRegion()` (`src/lib/region/region.ts`) instead of restating a literal.** `"California"`,
`"Western United States"` and `"North America"` are still footprint literals on the migration
list — they describe footprints outside this deployment's one manifest, so there is nothing yet
to read them from; see `src/lib/region/AGENTS.md` for where a future multi-region registry would
put them.

**Both manifest reads are PER CALL, never at module scope.** `namedCoverageRegions()` and
`fallbackCoverageBbox()` are functions; the `NAMED_COVERAGE_REGIONS` array and the
`FALLBACK_COVERAGE_BBOX` constant they replaced called `getRegion()` during module evaluation,
which snapshots whichever region resolved first and -- once `getRegion()` started refusing an
unregistered slug -- threw at import time, taking down every importer instead of one render
(STYLE-REVIEW-W8 S2; `federation.md` §1; the standing manifest-moves-must-be-lazy rule).
`src/__tests__/region/no-module-scope-region-read.test.ts` is the guard that fails on the next one
anywhere under `src/`, and `tests/test_module_scope_region_read.py` is its Python twin.

**The fallback camera kept its pre-manifest value, on purpose.** `fallbackCoverageBbox()` reads
`getRegion().defaultCameraEnvelope`, NOT `getRegion().envelope` — the manifest step deliberately
did not fold the fallback into the wider named-region box. `defaultCameraEnvelope` is
`foundation/region`'s own field for exactly this literal (`-125,42,-111,49`, the MTBS burn
envelope it happened to be typed with); see `foundation/region/AGENTS.md`
"`default_camera_envelope`". An earlier version of this migration pointed the fallback at
`.envelope` instead, widening the opening camera by ~1° on every side as an unintended side
effect; that regression was caught and reversed before landing, and
`src/__tests__/lib/map/coverage-region.test.ts` pins the narrower value so it cannot silently
recur.

**Why the fallback box was chosen this size, historically.** It mirrors the `INGEST_BBOX` value
verified directly against production on 2026-08-16 — every layer's feature extent fell inside
it, and fire-detections was exactly this box. `NEXT_PUBLIC_INGEST_BBOX` always wins when present,
which keeps the value server-configurable, and the fallback only matters when the variable has
not been plumbed through the Docker build's ARG list. If a deployment truly has no ingestion
coverage (server `INGEST_BBOX` unset, `getIngestionCoverage` returns `configured: false`),
`ServiceAreaLayer` never masks or locks bounds, so the fallback paints an opening camera rather
than making a wrong service-area claim.

**`viewportForBbox` is a spherical Web Mercator (EPSG:3857) inverse fit**, unfloored, matching what
MapLibre's `fitBounds` would choose. It exists so an initial camera and a later `fitBounds` to the
same bbox agree closely enough that the second can be instant rather than animated.

## drawing.ts

`douglasPeucker` implements Ramer-Douglas-Peucker line simplification on planar degrees: https://en.wikipedia.org/wiki/Ramer%E2%80%93Douglas%E2%80%93Peucker_algorithm . The link lives here, not in the source, because `check:data-boundary` rejects bare URLs in `src/**`.

## rung-selection.ts

`selectFinestAdmittingRung` is the ONE walk that decides which published rung answers a viewport,
shared by three call sites that each own a different ladder:

- `botanicalServingBandForViewport` (`src/lib/botanical-occurrences.ts`) over
  `detail` / `grid-0.05` / `grid-0.25`;
- `selectServingRung` (`src/lib/server/services/land-context/parquet-reader.ts`) over `ZOOM_TIERS`,
  gated by what the census proved published;
- `landContextRungForViewport` (`src/hooks/useLandContextViewport.ts`), the client's label for the
  same selection.

It lives in `src/lib/map/` because that is the one directory all three may import: the reader is
server-only and the hook may not reach into `@/lib/server/**`
(`scripts/check-client-server-imports.mjs`), so a helper in either of their own trees could not be
shared without a boundary violation. The module holds no ladder and no ceiling of its own --
those stay where their provenance is, beside the plane that publishes them.

**The rule, in one line: the finest published rung, no finer than `finestAllowed`, whose ceiling
admits the bbox area.** `finestAllowed` is normally the rung the map zoom selects, so selection
only ever moves COARSER -- serving a continental viewport from a detail rung would answer a
regional question with point evidence. Owner decision 2026-09-18 (`conductor/RUNBOOK.md`
"Finding 1"): a viewport too wide for its zoom's own rung is answered from the next rung out
rather than refused, which is what the botanical plane was doing to an ordinary wide PNW viewport
at z7-z10 (~140 square degrees against a ceiling of 100).

**Null is a refusal, not a fallback.** When no rung admits the area the walk returns null and the
caller must say so. Returning the coarsest rung instead would answer a question about one area
with evidence about another and give the reader no way to tell.

## layer-region-binding.ts

`federation.md` §2's last bullet on the web side: "the platform must run with a layer unbound". A
region that binds no soil source gets a soil layer that says **not available in this region** —
never a crash, never an empty map that reads as an outage, never a silent fallback to the pilot's
source.

**The binding status is DATA, not a registry literal.** Which layers a deployment binds a source
for is a property of the region, so it cannot live beside `permanentlyUnavailableReason`: that field
says "this build never publishes this layer", which stays true wherever the tree is deployed. The
binding arrives per request on the slider capabilities payload (`layerBindings`), carried unchanged
from `/api/v1/parquet/coverage`'s `layer_bindings`, whose authority is
`foundation/region/layer_availability.py`.

**One seam does the work.** `useLayerVisibility` (`layer-toggle-context.ts`) is the single place
every layer component reads to decide whether to mount, so gating there is what makes "no fetch
issued" true without teaching twenty-odd layer components about regions. `LayerRow` disables the
switch and states the reason next to it; the legend needs no change at all, because it renders
drawn layers and an unbound layer cannot be drawn.

**One rule, one function: `layerBindingInRegion(capabilities, slug)` → `bound` | `unbound` |
`not_federated`.** The payload row wins when it states one; otherwise the COMPILED manifest answers:
in `platformLayers` and in `enabledLayers` → `bound`; in `platformLayers` and not in `enabledLayers`
→ `unbound`; in neither → `not_federated`, which means binding is not a question that applies to the
slug (an uploaded layer, `interventions`, a slug from a newer manifest) and is treated as available.
`toggleBindingInRegion` is the same verdict keyed by `LayerToggleId`; a toggle with no manifest layer
is `not_federated`. Only `unbound` switches anything off, in `useLayerVisibility` and in
`useLandContextViewportBoundaries` alike.

**Why "fail open on silence, always" was wrong, and what replaced it.** The old rule read payload
silence as availability unconditionally, and a second helper next to it (`isRegionLayerBoundHere`)
read manifest silence as unavailability — same evidence, two verdicts, three files apart
(STYLE-REVIEW-W5 B1). The repair is to distinguish the two SILENCES rather than the two callers.
Payload silence is a gap: the coverage response is a deploy behind, or has not arrived, and reading
it as `unbound` would blank a working layer for a poll cycle. Manifest silence is not silence at
all, once the vocabulary is compiled in: `platformLayers` enumerates every layer the platform has,
so a slug listed there and missing from `enabledLayers` is the manifest STATING that nothing fills
it here, and drawing that toggle as available produces exactly the outage-shaped empty map
`federation.md` §2 forbids. A slug outside the vocabulary remains a genuine unknown and fails open.

**Fail-open on an unknown slug is for slugs from OUTSIDE this build only.** That third answer is
sound for a newer manifest's layer and unsound for a typo, and the slugs reaching it come from
`REGION_LAYER_SLUG_BY_WAREHOUSE_NAME` — hand-spelled, and until STYLE-REVIEW-W6 S1 pinned to no
vocabulary at all, so one character would have routed a federated layer to `not_federated` =
available with its caption suppressed, permanently and silently. Two things close it:
`layer-region-binding.test.tsx` diffs that table's VALUES against `platformLayers` in both
directions (its two documented exceptions are `land-context`, which has no toggle, and
`botanical-occurrences`, whose three toggles carry `warehouseLayerName: null` — BACKLOG N40), and
`layerBindingInRegion` answers `unbound` with a logged contract error, never `not_federated`, for
any slug a compiled-in caller names (`TOGGLE_REACHABLE_REGION_LAYER_SLUGS` or
`LAND_CONTEXT_REGION_LAYER_SLUG`). Only a slug this build never mentions can fail open.

**`platformLayers` is the manifest field that makes the third answer possible.** It mirrors
`foundation/region/layer_availability.py`'s `PLATFORM_LAYER_SLUGS`, is diffed by
`src/__tests__/region/manifest-parity.test.ts`, and is the reason `land-context` — a platform layer
no region binds a source for — now answers `unbound` from a statement instead of from an omission
the serving side could never have emitted a row for.

**The warehouse-name → manifest-layer table is hand-spelled**, mirroring `agent/surfaces.py`'s
`SURFACE_REGION_LAYER_SLUGS` entry for entry, because the two namespaces disagree exactly where it
matters: `drought-areas` is served by the manifest layer `drought`, and all twelve climate and soil
field streams are derived products of the one `signal` plane — they bind no source of their own and
go dark together with it. `interventions` and `strategy-recommendations` are absent from the table
on purpose; neither is a federated layer, so neither can be "unavailable in this region".
**Two refusals used to share one `null` (S8, W3 review, fixed 2026-09-18).**
`selectFinestAdmittingRung` returns `null` for BOTH "no rung on the ladder admits this area" and
"`finestAllowed` names a rung that is not even on the ladder" -- a configuration defect, not a
viewport that is too wide. Every caller that only checked `=== null` rendered the same "no
published rung answers a bbox wider than..." sentence for either, so a ladder/band mismatch was
reported as a wide viewport at every zoom, forever. `selectFinestAdmittingRungResult` is the
preferred entry point for a new caller: it returns
`{ kind: "selected", rung } | { kind: "no_rung_admits_area" } | { kind: "rung_not_on_ladder", rung }`
so the two refusals stay distinguishable. `botanicalServingBandForViewport` and `selectServingRung`
both moved onto it; `selectFinestAdmittingRung` itself keeps its original `TRung | null` signature
because `landContextRungForViewport` (`src/hooks/useLandContextViewport.ts`) still calls it
directly and migrating that hook is a separate change.

## Concrete environmental layer bindings

Climate and soil field surfaces bind their own concrete map-layer names in the region catalogue.
NASA POWER climate fields use `nasa_power`; ERA5-Land soil moisture, temperature and VPD use
`era5_land`. The retired generic signal layer is not an availability dependency or an agent
retrieval route. Physical metric column names remain part of the numeric source schema.
