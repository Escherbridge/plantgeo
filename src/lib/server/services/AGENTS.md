# Parquet reader services — rationale

Why the Parquet-plane modules in this directory are shaped the way they are. The "what" stays in
the one-line doc comments beside each symbol; this file holds the reasoning a reader cannot
reconstruct from the code. Scope: `parquet-plane-client.ts`, `parquet-envelope.ts`,
`parquet-slider-capabilities.ts`, `parquet-trpc-readers.ts`, `parquet-climate-field.ts`, and the
cancellation seam they share with `../http/bounded-upstream.ts`.

## §availability-authority — the census is the weaker witness

Coverage lanes now name their own **authority**. `coverage_authority: "availability"` means the
serving side answered from the immutable `_LATEST.json` pointer and the checksummed
`availability.parquet` generation it names; `"census"` means it walked the object store instead.

Nothing in TypeScript reads either artefact, and nothing here should start. The pointer and the
generation live on the Python side; this side receives six fields
(`coverage_authority`, `availability_generation_sha256`, `availability_pointer_key`,
`source_ceiling_day`, `required_rungs`, `withheld_reason`) and treats them as testimony. Reading
R2 from Next would mean a second implementation of the pointer-resolution rules, and two
implementations of "which generation is current" is exactly how a slider ends up describing a
generation the reader is not reading from.

The two authorities are carried rather than collapsed because a walk is a **weaker claim than an
index**: a part still being written is visible to a listing before it is readable, so a census
lane can report a day that a read of the same day will not serve. A row synthesised from several
rungs takes the weakest authority any of them reported (`rowCoverageAuthority`) — a row is not
"index-backed" because three of its four rungs were.

`requiredRungs` is a **label, not a gate**. `REQUIRED_ZOOM_TIERS` still demands all four rungs
before a capability is published. If the wire ever declares fewer, the gate does not relax: a lane
must not be able to lower its own bar by declaring a shorter list.

## §fail-closed — what a withheld index licenses

`withheld_reason` is a statement about the **availability index**, never about the data. An
unpublished, stale, malformed or checksum-invalid index says nothing about which days exist —
which is precisely why a lane carrying one may not be described from census facts instead, and
may not fall back to the PostgreSQL passthrough either. Both fallbacks would answer a question the
warehouse had just declined to answer, in a form the client cannot tell apart from a proved one.

This is why `availabilityWithholding` runs **first** in `getParquetSliderCapabilities`, ahead of
`coverage_not_current` and ahead of the passthrough filter, and why `retainedPostgresCapabilities`
takes a withheld-name set: burn-severity is deliberately still served from PostgreSQL, but if its
Parquet lane withheld its index then something is wrong with that layer's published evidence and
the older reader is not a second opinion about it.

The four wire spellings are carried through into `WithheldParquetCapabilityReason` **unchanged**.
A translation table between two enums that mean the same thing is a place for the two to drift,
and an operator reading a withheld proof should be able to grep the serving side for the identical
string. Precedence, when rungs disagree, is the wire's own declaration order
(`PARQUET_AVAILABILITY_WITHHELD_REASONS`) — one list, so there is no second ordering to diverge.

## §source-ceiling — withheld, never clamped

`sourceCeilingDay` is the newest day the **source** can offer; `latestDay` is what the warehouse
**holds**. A rung holding a day past its own source's ceiling is wrong about something — a
mislabelled partition, a clock skew, a forecast row written into an observed stream — and the
capability is withheld with `ceiling_violation` rather than clamped to the ceiling.

Clamping was considered and rejected. It produces a plausible axis out of a lane that has just
demonstrated it disagrees with its own source, and a lane that is wrong at the live edge has not
earned belief on the days below it either. The withheld proof names the offending rungs, which is
actionable; a silently shortened axis is not.

The ceiling is also the lane's **freshness horizon**, and `coverageTailRanges` runs the closing
gap to it rather than to `evaluatedThroughDay`. The two answer different questions: the ceiling is
how far the source reaches, the evaluated day is merely when the census ran. Running the tail to
the later of the two reports every day the upstream has not published yet as an ingest hole, so a
lane correctly waiting on a weekly or lagged release reads as dead. The golden fixture
(`coverage_availability.json`) is exactly that shape — held through 2026-08-05, ceiling 2026-08-07,
censused 2026-08-25: two owed days, not twenty.

## §climate-zoom — one rung per request

`getParquetClimateField` used to pin `zoomTier: 13`. The comment justifying it ("this lane has one
serving tier") described the reader, not the warehouse: the climate lanes publish z13/z9/z5/z0 like
every other lane, so the three coarse rungs were written and never once read, and a zoomed-out
viewport paid for stored cells it could not usefully draw.

The tier is now resolved with `resolveZoomTier(input.mapZoom)`, exactly as
`getParquetFireDetections` does, and **exactly one physical rung answers each request**. Never read
two and merge: rungs are separately-published aggregations, and a merged answer would double-count
the ground both describe.

The rung travels back beside the result (`ParquetClimateFieldRead`) rather than inside the `ready`
arm, because every state needs it — an empty collection built from `day_not_written` still has to
declare which rung was asked, or the renderer cannot say whether it is looking at stored cells or
at an aggregate.

Two consequences the renderer had to respect while the support geometry was missing, **both now
superseded** by §tessellated-support below. They are kept here because the reasoning that produced
them is right and only its premise changed — the module was not *told* the coarse pitch, so it
refused to guess one:

- ~~**No coarse cell polygons yet.**~~ `CELL_DEGREES = 0.5` was the detail rung's pitch, and
  `tierRenderForm` degraded every rung below z13 to `"symbol"` — points at the aggregate's own
  centre, honest and needing no pitch. The pitch now comes from `servedCellLattice`, so every rung
  is a filled tessellation and the only degrade left runs the other way (a contour at the detail
  rung, which the render contract does not permit).
- ~~**`latticeCellCount` is a detail-rung denominator.**~~ It published 0 below z13 rather than a
  number measured against a lattice the answer was not drawn from. It is now measured on the
  SERVED rung: frozen lattice centres are folded onto that rung's cells and the distinct cells are
  counted, so the numerator and the denominator are again on one lattice.

Cell identity follows the rule `decodeSoilFieldRows` already enforces: only z13 carries `cell_id`,
coarse rungs carry an anonymous aggregate, and `(zoomTier === BASE_ZOOM_TIER) !== (row.cell_id !==
null)` is a contract error. The duplicate check keys on the coordinate pair where there is no
identity, so it survives the coarse rungs instead of silently passing over a set of nulls. That
guard is now a READER-SIDE integrity check and nothing more: `aggregated` is read off the declared
`support.zoomTier`, so a lane that one day publishes ids on every rung relaxes the guard and
changes no renderer.

## §tessellated-support — the cell a served row stands for

Every reader in this directory that returns cells now attaches an `AggregateEnvelopeSupport`
(`src/lib/map/layer-render-contract.ts`): the rung, the form, the stable id, origin-versus-centre
semantics, cell width and height, the aggregation method, the contributor count and provenance.
The client never infers support from a null cell id or from a layer name again.

**One tier -> cell-size table, and it lives in `src/lib/map/zoom-tiers.ts`.**
`DERIVED_TIER_CELL_DEGREES` mirrors `TIER_RESOLUTION_DEGREES` in
`services/agri-data-service/src/agri_data_service/warehouse/parquet/tiers.py` — `{9: 0.01, 5: 0.2,
0: 5.0}`, four web-map pixels of each tier's own zoom — and `LANE_BASE_LATTICES` carries each
lane's own base grain beside it. `src/__tests__/lib/map/zoom-tiers.test.ts` pins both against the
Python literals. The base rung is deliberately absent from the tier table: it is not derived, so it
has no ladder resolution.

**The served cell is `max(tier grid, lane base grain)`, and that single rule fixes two defects.**
0.01 at z9 and 0.2 at z5 are both FINER than the quarter-degree cells the signal, soil-field and
vegetation lanes publish, so those rungs merge nothing — they are a relabelling of the base rung.
Drawing them at the ladder's pitch painted a quarter-degree measurement as a 0.01-degree speck at
z9, and at z5 as a 0.2-degree cell on a grid 0.25 does not divide, which leaves one lattice column
in five empty — roughly a third of the viewport showing map background. That is the "separated
rectangular climate blocks and nested ERA5 soil blocks with visible seams" the 2026-09-01
production assessment recorded. Taking the coarser of the two is exactly true: **a derived rung
cannot describe ground finer than the rung it was derived from.**

**Corners come from an integer lattice index, never from the row's own float.** `latticeCellIndex`
adds back half the grid step the derivation's `floor` moved the coordinate by, then rounds — the
error is at most `tierCellDegrees / 2` against a cell at least that wide, so the recovery is exact
rather than approximate. `latticeCellSpan` then rebuilds both edges from the index, so cell `i`'s
upper edge and cell `i+1`'s lower edge are the SAME expression over the same operands and therefore
the same double to the last bit. `coordinate + size` for one row against `coordinate` for its
neighbour are two different computations that agree only to within rounding, and that difference is
the hairline of background between two cells that should touch.

**`origin` is not decoration.** `fire_detections_day_export.sql` floor-snaps to 0.005 and writes the
cell ORIGIN; `signal_plane_day_export.sql` and `vegetation_day_export.sql` write
`ST_X(cell.centroid)` and so write the CENTRE. Every derived rung writes the floored origin
(`GridAggregation` in tiers.py). Reading one as the other shifts a whole field by half a cell,
which looks like a registration error rather than a bug.

**The climate lane's base grain is 1 degree, not the 0.5 degrees NASA POWER measures.** The lane
samples that product on a one-degree lattice (`CLIMATE_FIELD_LATTICE_ROWS` steps by whole degrees),
so a half-degree cell leaves three quarters of the viewport blank. The drawn cell is the ground
NEAREST its sample — what a tessellation of a regular lattice means — and the measured support is
carried in the panel caption rather than in the geometry. Permitted because `LAYER_RENDER_CONTRACT`
sets `declaredSupportDegrees: null` for these lanes and already licenses `isoband`, which claims
strictly more ground than a nearest-sample cell. Contrast `vegetation`, whose
`declaredSupportDegrees` IS 0.25 — and whose lattice pitch is also 0.25, so nothing is claimed
beyond what was measured. That lane is served as `tessellated_cell` at every rung for exactly that
reason; `raw_point` there would be the fictitious finer footprint the contract forbids.

**Form follows the contract, not the zoom.** `isoband` is permitted at the coarse and middle bands
only, because a band asserts the field varies smoothly BETWEEN samples and the detail rung serves
those samples. A contoured signal at z13 is therefore served filled, and `renderForm` reports it.
Isobands are dissolved over the SERVED rung's lattice: handing `buildIsobands` the detail pitch for
a coarse answer makes it read a regular lattice as a scatter, every square fails its corner test,
and the band comes back empty or in pieces — a seam wherever one batch of rows met the next.

**Attribution lives in `LANE_ATTRIBUTIONS`** (`parquet-trpc-readers.ts`) because neither
`layer-registry.ts` nor `layer-legends.ts` carries an attribution field. Two of the five reuse the
constant published beside their value vocabulary; a registry that grows one should read from this
table rather than adding a second copy.

**Where support is per-row and where it is per-collection.** Fire, water, vegetation and climate
readers return arrays of rows and attach support to each, because `supportId` and
`contributorCount` genuinely vary per row. The soil-field and climate-field *presentations* return
one collection whose features all share the rung, the pitch, the origin semantics and the
attribution, so those carry ONE envelope — `supportId` there names the lattice (`lane:day:zN`) —
and the per-feature part that varies is already on each feature as `cellKey`. A copy of the
envelope per cell would repeat five constant fields up to `SOIL_FIELD_MAX_CELLS` times for no
reader.

**Water's contributor count is measured by the fold, not read off a column.** The lane publishes no
observation count and its derivation nulls `site_number`, so the only defensible number is the one
`newestWaterRows` can see: the readings that shared this envelope's key, summed onto the row that
survives. Reporting 1 for a cell that answered for six gauges would be a fabricated count.

## §request-cancellation — an abort is not an outage

`AbortSignal` reaches the socket: `ParquetViewportRead.signal` → the three row requests in
`parquet-plane-client.ts` → `BoundedJsonOptions.signal` → `boundedSignal` in `bounded-upstream.ts`,
which combines it with the request's own timeout via `AbortSignal.any`. The timeout is always
present, so a dropped caller signal can only make a request live *longer than asked* — never
longer than the bound. `AbortSignal.any` rather than a hand-rolled listener pair: `node:22.16`
(`Dockerfile:1`) and the jsdom the suite runs under both implement it, and a manual combiner owns
listener teardown that `any` does for free.

`UpstreamAbortedError` is its own class because an abort and a timeout are **the same
DOMException on the wire and mean opposite things**. A timeout is a claim about the upstream that
a retry may plausibly beat; an abort is a claim about the caller. Relabelling one as the other
would page someone for a user who navigated away. Classification reads the caller's own
`signal.aborted` rather than the DOMException name, so a custom abort reason still classifies
correctly. Both the response head and the body read are guarded — the body streams after the head
resolves, so an abort can land in either.

`fault.kind: "aborted"` exists so the taxonomy stays total, and `rejectAborted` — exported from
`parquet-trpc-readers.ts`, beside the readers whose faults it inspects — immediately turns it into
a thrown `CLIENT_CLOSED_REQUEST`. The readers return their faults as *data*, which is what lets the
map caption an outage instead of blanking — but react-query stores data, and an aborted payload
cached against a viewport would be replayed to the next reader of that key as though the warehouse
had said something. It lives here rather than in one router because the obligation is the
procedure's, not the router's: **every** procedure that threads a `signal` into a reader wraps its
result (`environmental.getStreamflow`/`getVegetationIndex`/`getDroughtClassification`,
`wildfire.getFireDetections`/`getWeatherForBbox`), so no reader that can report an abort can
resolve one as a 200 payload.

The browser half is `abortOnUnmount: true` on `createTRPCReact` in `src/lib/trpc/client.ts`.
Without it tRPC passes `signal: null` for every query (`@trpc/react-query`
`shared-*.mjs` — `shouldAbortOnUnmount` decides between `queryFnContext.signal` and `null`), so
the resolver's `signal` on the server is one that never fires and the whole seam below it is
inert. That flag is also overridable **per query**: a `trpc.<router>.<procedure>.useQuery(input,
{ trpc: { abortOnUnmount: false } })` silently restores `signal: null` for that one query, so its
reads never cancel and its aborted-fault path becomes unreachable — no query may add it without
updating this note.

**A batch is aborted only when every request in it has been abandoned.** `httpBatchLink` merges the
ops' signals with `allAbortSignals` (`@trpc/client` `httpBatchLink-*.mjs`), which counts aborts and
fires the merged controller only on the last one — and skips a `null` signal entirely, so one
un-aborted op in the batch pins the whole HTTP request open:

```js
const onAbort = () => { if (++abortedCount === count) ac.abort(); };
```

That is the correct trade for a shared connection, and it is why the server-side guard is not
optional: an abandoned query still gets its row read, and `rejectAborted` is what stops that read's
`aborted` fault from landing in the cache as an answer. Cancelling one op's *server* work early is
therefore best-effort; cancelling one op's *browser-side* result is exact.

**`getParquetWarehouseCoverage` deliberately takes no signal.** Its answer is single-flighted and
memoized across every session, so one caller's cancellation would abort a read other callers are
already awaiting: one browser tab closing would blank the slider for everyone else. Its 8-second
budget is the only bound it needs.

## §wire-freeze — where a coverage field may be added

The six availability fields are mandatory on **every** lane, `null` where they do not apply. An
omitted field is a contract break, not a healthy lane, and zod strips unknown keys by default — so
the strict side of this contract is Python's, and the fixtures under
`services/agri-data-service/tests/contract/fixtures/` are what make the two agree by consuming
identical bytes. Adding a field means editing `wire_contract.py`, the `WIRE`-adjacent schema in
`parquet-plane-client.ts`, and those fixtures in one change.

The coverage body names its own shape in `coverage_schema_version`, mirrored here as
`COVERAGE_SCHEMA_VERSION = 2`. It is decoded as a plain integer, echoed on
`ParquetWarehouseCoverage.coverageSchemaVersion`, and then **gated in `decodeCoverage`** rather
than pinned with `z.literal`: a zod failure would report "the census does not match the contract"
for what is really a half-landed deploy, and the version number is the one fact that distinguishes
them. The gate itself is non-negotiable — a version-1 body carries no `withheld_reason` at all, and
reading that silence as "every lane's index is healthy" is precisely the fail-open §fail-closed
exists to prevent, so a service that has not been redeployed yet must blank the slider rather than
quietly narrow it. Bumping the version is a change to `wire_contract.py`, `parquet_ops/wire.py`,
the fixtures and this file in ONE commit.

Note also that the Python cross-check parses `basePath`, `routes` and `params` out of the `WIRE`
block by regex — adding a key to `routes` or `params` without the matching entry in
`WIRE_ROUTES`/`WIRE_PARAMS` fails the Python suite, which is the intended coupling. The response
schemas live *below* that block and are not parsed, so they are freed to change only in step with
the fixtures.
