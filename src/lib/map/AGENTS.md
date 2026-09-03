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
and from the soil-wetness pilot, and the reason `VegetationLayer.tsx:283` draws a deliberate
outline: to keep the cells legible as discrete samples. An envelope for this layer may never
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
the renderer draws today that `permittedForms` still excludes, with an owner and a date.
Vegetation carries the only one: `presentParquetVegetation` emits a Point at each cell's centre
and `VegetationLayer` paints it as a zoom-scaled circle — `raw_point` on this vocabulary, owned by
`multiscale_polygon_surface_20260901 m2`, recorded 2026-09-02. Widening the band to match would
have made the contract describe the code instead of governing it, and would have erased the one
artefact that makes the gap findable. The test asserts every deviation names an owner and a date,
because an anonymous undated one is just a second contract.

**Who may import it.** Anything that decides how a layer is *drawn* or *served*: map layer
components under `src/components/map/layers/`, the tRPC readers in
`src/lib/server/services/parquet-trpc-readers.ts` that build serving envelopes, and panels that
caption an aggregate. The **committed** consumers, as of 2026-09-02, are the reader slice m1
(`parquet-trpc-readers.ts`, the serving envelope) and the renderer slices m2 and m3 — no other
module imports this file yet, and that is the expected state for a contract landed one slice ahead
of the code it governs, not evidence it is dead. It imports only `layer-registry.ts` (for the
toggle-id type), `zoom-tiers.ts` and `climate-field.ts`, so it stays browser-safe and stays free
of a cycle back into the registry.

**Removed.** `time-format.ts`'s `resolveObservationIso` (deleted 2026-09-02) resolved a
per-detection FIRMS `acqDate`/`acqTime` pair; the 2026-09-01 Parquet cutover removed the last
producer of those keys, since cells carry `newestObservedAt`, which `toIsoTimestamp` handles
directly.

**What may not happen.** The `SupportKind` union is closed. A renderer that needs a tenth form
edits this module and its test, never its own call site — an unlisted form drawn locally is
exactly the drift the contract exists to end. The registry is a total `Record<LayerToggleId, …>`,
so a new toggle fails to compile here rather than reaching the map with no declared form.
