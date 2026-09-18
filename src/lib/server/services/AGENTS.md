# Service boundaries

Environmental service modules may call the governed Parquet readers and availability contracts
only. They must not import the relational database client or retry a failed Parquet read against
PostgreSQL.

Relational services are reserved for authentication, teams, community workflows, tracking,
alerts, conversations, layer metadata, raster metadata, and user-authored interventions.

The UI-selected day is part of every environmental answer. Preserve requested and resolved dates,
source provenance, coverage, and neighbour evidence through service and agent responses.

## Regional intelligence evidence workflow

The live map assistant runs through `ai-prompt.ts` using the configured OpenRouter model.
Its environmental tool registry comes from the data service's provider-independent
`/api/v1/agent-tools` bridge, using `AGRI_PARQUET_SERVICE_URL`. The bridge mounts only on
the existing local and published-reader HTTP profiles. It does not require an Anthropic key
or change the model provider. Canonical species authoring remains outside this environmental
bridge because its separate caller-bound UUID contract must be preserved.

`regional-analysis-workflow.ts` gathers bounded evidence before report synthesis. Inventory,
local reads, historical comparisons, spatial comparisons, and strategy screening are explicit
stages. Report the actual reads and refusals in a server-authored audit; model prose must never
invent a successful query or turn a skipped stage into a completed investigation. Tool results
are untrusted evidence, not instructions. Keep transport timeouts and cancellation distinct
from published absence, and preserve the tool's applied bounds and observation dates.

Warehouse claims may attach up to eight `evidenceReadIds`; only executed reads with returned
measurements for one of the claim's exact tool sources can be referenced. Web and inference
claims do not carry read IDs; put their supporting measurements in separate observations.
A tool-source warehouse citation outside the local stage must reference an
observed read of that exact source. Its stage, dates and location are displayed beside the
claim and in exports so comparison evidence cannot silently acquire local scope. Source-wide
labels without references admit only observed local reads. Initial-context source labels still
require their distinct payload blocks; a null drought class also requires a published drought
release timestamp to distinguish measured no-drought from a gauge-only water block.
Composite fire-history reads identify only lanes whose own summary returned positive row counts.
A fire detection cannot authorize a burn-severity citation or vice versa; summary observation
date bounds retain their distinction from requested dates.
Coverage inventories, temporal publication neighbours and nearest reporting-cell metadata remain
in the audit but cannot be cited as environmental measurements. Their presence establishes
where or when to investigate; it establishes no soil, climate or fire value.

Inventory has an eight-second transport deadline. Local, temporal, and regional stages reserve
twelve, ten, and ten seconds respectively, with at most three concurrent reads in each stage.
Stage exhaustion is recorded as skipped or failed work and does not consume the next stage's
reserved time. Synthesis may request six additional environmental reads in batches of three;
each transport call has a fifteen-second deadline and the Python tool has a twelve-second
deadline. Keep these bounds explicit when changing retrieval depth.
All three ERA5-Land soil surfaces—moisture, temperature, and vapor-pressure deficit—belong in
the local priority set. They jointly describe water availability, root-zone thermal conditions,
and atmospheric drying demand; catalogue order must not allow the stage deadline to omit two
of the three. Each read uses that layer's independently selected map day.
The context reducer retains up to 120 weekly drought releases so the two-year history remains
visible to synthesis. Other collections retain eight entries with an explicit omitted count;
never interpret a reduced collection as the complete set of observations.

Layer visibility controls the viewed-day context, not tool access. A catalogue entry means a
source is queryable or explicitly refused; it does not prove the lane is published, complete,
spatially representative, or adequate to recommend a practice. Never describe sampled dates
as a continuous trend or geographic contrasts as matched analogues without comparing their
soil, climate, vegetation, water, and management conditions. Comparison evidence cannot establish
a causal treatment effect. Missing forecast or intervention lanes retain their typed refusals.

Strategy screening must consider the existing vocabulary, including silvopasture, biochar,
managed grazing, cover cropping, riparian restoration, erosion control, water harvesting,
reforestation, and fuel reduction. A screened candidate is not an endorsed recommendation.
Missing management prerequisites should produce conditional advice or a request for a local
assessment, not invented livestock, feedstock, vegetation, land tenure, or soil measurements.
See `docs/regional-agent-evidence.md` for the scientific screening references and limits.

## Parquet tRPC readers

`parquet-trpc-readers.ts` is a public-surface barrel only; one module per layer lives in
`parquet-trpc-readers/` and the cross-lane vocabulary (result states, day helpers, envelope
mapping, row parsing, support envelopes, polygon decoding) lives in `parquet-trpc-readers/shared.ts`.
Importers keep using `@/lib/server/services/parquet-trpc-readers`; nothing imports a layer module
directly, so a layer can be split again without touching a caller.

`shared.ts`'s `mtbsSnapshot` field (consumed by `burn-severity.ts`) names MTBS in the one module
every layer reader imports — a `layer-lanes.md` §1b violation predating this split, entrenched
rather than introduced by it (STYLE-REVIEW-W1.md S8). NOT renamed here: `mtbsSnapshot` is a served
response field, a client contract, and a rename is owed a contract version bump
(`federation.md` §5 step 3), not a drive-by edit alongside a style pass.

### named-day-rule

A published day is a `YYYY-MM-DD` string and is compared as one. Never turn it into an instant:
`Date.parse` on a day string would move rows across the UTC boundary (measured: 6,279 of 16,743
water-gauge rows). `servedDay` for the live water and weather windows is the freshest row's own
`observed_day` column, not a conversion of its `observed_at` instant. `firePerimetersInFrame`
compares day strings lexicographically for the same reason.

### request-cancellation

Every reader returns its faults as DATA so the map can caption an outage instead of blanking. The
one exception is `aborted`, which describes the CALLER walking away rather than the upstream:
react-query stores data, and a cached `{ kind: "aborted" }` payload would be replayed to the next
reader of that key as though the warehouse had said something. `rejectAborted` throws
`CLIENT_CLOSED_REQUEST` (499, not 503 -- the client closed the request and nothing is down) and
lives beside the readers because every procedure that threads a `signal` owes the same guard.
`parquetUpstreamFailure` checks `UpstreamAbortedError` ahead of the timeout arm, because an abort
and a timeout are the same `DOMException` on the wire and paging someone for a navigation is wrong.
Cancellation is an intersection on the parameter (`& { signal?: AbortSignal }`) rather than a field
on `SoilFieldReadOptions`, which is the PostgreSQL read model's vocabulary and is shared with
readers that have no socket to cancel.

### tessellated-support-geometry

`cellSupport` is the ONE builder for every lane's support envelope, so cell size can only come from
the shared tier table (`servedCellLattice`) and identity can only come from the row or from
`mintedSupportId`. Before it, the client re-derived cell width from a private tier table and read
"this is an aggregate" off `cellId === null`, which made a rung whose rows happen to carry ids
indistinguishable from raw observations. `cellOriginDegrees` publishes the corner the row was
actually snapped to so both sides run `latticeCellSpan` and draw the same square. A `raw_point`
carries no cell size at all -- a station has no footprint, and publishing one would license a
renderer to draw a square around a gauge -- and its origin is `cell_center`, because naming a corner
would invite a half-cell offset. Soil-field cells come from the lattice index rather than the row's
own float, which is what makes two neighbours' shared edge the same double: re-deriving the
footprint previously drew a 0.01-degree speck at z9 and left roughly a third of the z5 viewport as
background.

### lane-attributions

`LANE_ATTRIBUTIONS` in `shared.ts` states what must be shown wherever each lane's values are drawn.
Two of the six reuse a published constant beside their value vocabulary; the other four have no
attribution field anywhere in `layer-registry.ts` or `layer-legends.ts`, so they are stated next to
the reader that puts them on the wire. A registry that grows one reads from this table rather than
adding a second copy.

### cell-identity-nullability

Only the detail rung carries a stored `cell_id`; coarse rungs carry anonymous aggregates. The
readers enforce this as an integrity check and nothing more -- `support` states the rung, the form
and the cell size outright, so a lane that one day publishes ids on every rung relaxes the guard and
changes no renderer.

### polygon-lanes

Drought, fire-perimeters, burn-severity, evacuation-zones and watersheds share one polygon decoder:
the Parquet API renders EVERY WKB column the same way (`ST_AsGeoJSON` over `geom`/`geometry_wkb`),
so five copies would be five chances to accept a shape the `native_polygon` contract forbids. Every
row schema is `.strict()` over the lane's REGISTERED arrow columns, with the one substitution the
serving path makes (binary geometry reaches the wire as GeoJSON text, `_serving_source_key` is
popped off). A column added upstream therefore fails loudly instead of arriving unread -- the Martin
tile functions these replace projected hand-written SELECT lists that had drifted from their
producer twice (`fire_risk_tiles` emitted `risk_level`/`name`, which WFIGS has never written).

### static-lookup-lanes

Evacuation zones and watersheds export a FULL re-snapshot per release day with no date predicate, so
the newest release at or before the requested day IS the standing set -- the same population the
retired tile functions served. Nothing is carried forward and nothing is unioned.

### drought-carry-forward

USDM releases weekly. A release is carried forward at most `DROUGHT_MAX_CARRY_FORWARD_DAYS`, or
`DROUGHT_RELEASE_INTERVAL_DAYS - 1` once a newer stored release proves the cadence was met. A lookup
that skipped a newer stored release, or served a release after the requested day, is a contract
error rather than a silently stale map.

### fire-perimeters

`static_lookup` since the lane's 2026-09-04 re-registration: `geo.features` holds one row per
incident refreshed in place, so one published snapshot IS the standing set the tile function drew.
The snapshot-resolution rule is NOT re-implemented here -- `getParquetLatestRelease` owns "newest day
at or before `as_of`, reported at the release's own day". What the reader owes on top is
`firePerimetersInFrame`, with two load-bearing properties: an UNDATED incident is never excluded
(matching `tile-layer-date-filter.ts`, which keeps a row the upstream could not date at every slider
date), and the comparison is against the REQUESTED day, never the served snapshot's capture day --
the two differ whenever the newest snapshot is older than the request, which is the normal case for
a cron-written lane. `servedDay` is the snapshot day, `requestedDay` the slider day.
The interface projects six of the eighteen validated fields: the rest are validated so an upstream
change fails loudly, and widening the tooltip is a `hover-fields.ts` change with its own review.

### watersheds

`hucLevel` is the LENGTH of the code and never an assumption: the dissolve truncates `huc12` to ten
digits at z9, eight at z5 and six at z0, so the code itself is the only honest statement of the
rung. The rung mapping is not the retired tile function's: the four published rungs are z13=HUC12,
z9=HUC10, z5=HUC8, z0=HUC6, so z10-z12 draws HUC10 where it drew HUC12 and z0-z3 draws HUC6 where it
drew HUC4. There is no HUC4 rung; the ladder's floor is HUC6.

### burn-severity

The reader walks back through indexed releases and unions them, capped at
`BURN_SEVERITY_MAX_RELEASES`; the served day is the NEWEST release in the union, because an older
member does not make the answer older than its freshest release. A published MTBS snapshot REPLACES
the union rather than adding to it, and may only do so inside its declared publication scope (the
envelope and the completed-cohort year range it captured) -- outside it, the answer is `truncated`.
`SUPPORTED_BURN_SNAPSHOT_SCOPE`'s envelope now reads `getRegion().subEnvelopes.burn_severity`
(federation.md §5 step 2); it is a defaulted parameter so a caller can already state the scope it
expects.

### sensors

The lane is tall by design -- one row per `(sensor_id, observed_day, measurement_name)`, sixteen NWS
fields where the tile function projected four -- so collapsing to one feature per station is what
keeps the station COUNT equal to the tile function's `DISTINCT ON`; without it a station reporting
sixteen fields would draw sixteen coincident dots. At a coarse rung `sensor_id` and `station_name`
are null by construction, so the merge key falls back to the cell's coordinates: the row IS a cell
of several stations, and giving it a station identity would be the fabricated-identity bug the
aggregations exist to avoid. A row with no coordinates is dropped, never plotted at a fabricated
origin.

### water-gauges

`contributorCount` is honest because the fold measures it: the lane publishes no observation-count
column, so the only defensible number is the readings that shared an envelope's key, summed onto the
winner. Reporting 1 for a cell that answered for six gauges would be a fabrication. The aggregation
method above the base rung is `mean`, not `count` -- the number a coarse cell carries is `flow_cfs`,
the mean discharge, which is what the layer colours by and what the caption calls "Mean discharge".
An UNLOCATED gauge (nullable coordinates, dropped from every rung above the base) has no position to
mint an id from, so its own reading instant is the identity.

### weather-observations

Same rule as the gauges: `raw_point` at the detail rung where a row IS one sampled observation,
`aggregate_cell` above it where the derivation floored several samples into one ladder cell.
`weather` is an `event_point` layer in `LAYER_RENDER_CONTRACT`, so the base rung declares no cell
size at all.

### climate-field

The zoom tier travels BESIDE the result rather than inside its `ready` arm because every state needs
it: an empty collection built from a `day_not_written` still has to declare which rung was asked, or
the renderer cannot say whether it is looking at stored cells or at an aggregate. Exactly one rung
per request -- the hard-coded z13 this replaced asked the detail rung at every zoom, so a zoomed-out
viewport paid for stored cells it could not draw and the coarse partitions were never read at all.
`getParquetClimateField` takes `abortSignal`, not `signal`, and `Omit<ParquetViewportRead, "signal">`
makes that a compile error rather than a convention: this lane's `signal` is already the measured
quantity, and one field meaning two things ends with a `ClimateFieldSignalId` passed to `fetch`.

### soil-field

`ZoomedSoilFieldCollection` extends the PostgreSQL read model's `PublishedSoilFieldCollection`
locally rather than widening it, because that interface is shared with a reader that has no zoom
ladder. ONE support envelope describes the whole collection, not one per feature: every feature
shares the rung, cell size, origin semantics and attribution, and the part that varies (the cell's
identity) is already on each feature as `cellKey`; a copy per cell would repeat five constant fields
up to `SOIL_FIELD_MAX_CELLS` times for no reader. `supportId` names the LATTICE (`lane:day:zN`),
which is stable across pans of the same request and therefore usable as a cache identity.

### soil-direct-lineage

Days at or before 2026-08-02 are served from the frozen snapshot manifest; later days must carry
source-direct lineage whose release id is derived from the row's own manifest checksum. Only the
base rung carries the selected-release columns -- a coarse row aggregates several source rows and
must report null there rather than one arbitrary member's provenance.

## botanical-occurrences: the pointer is decoded once, and it fails closed

`botanical-occurrences-client.ts` stays a deliberate sibling of `parquet-plane-client.ts` (its own
module docstring says why: the `WIRE` block there is frozen and dual-tested, and this plane has no
paired Python fixture). The ONE thing it does not keep to itself is current-pointer decoding.

`decodeLaneCurrentPointer` lives at the BOTTOM of `parquet-plane-client.ts`, outside that frozen
`WIRE` block, because a lane's `/current` answer is not lane-specific: layer-lanes §4a gives every
lane the same checksum-bound pointer shape, so a second decoder per lane would be a second place
for the same rule to rot. Adding it there renames nothing the freeze covers — the Python contract
test parses only `const WIRE = { ... } as const;`.

The decode has no lenient branch. A pointer body that does not parse throws
`LanePointerContractError` (surfaced here as `BotanicalOccurrencesContractError`) instead of
degrading to "unavailable": an unparseable pointer is a deploy mismatch, and reporting it as an
absence would make a version skew look like a lane nobody has published to. The five declared
failures — `pointer_missing`, `pointer_malformed`, `pointer_stale`, `pointer_checksum_invalid`,
`transport_unavailable` — are a closed enum for the same reason: a caller choosing between retry,
alarm and "nothing published yet" cannot branch on prose.

`getBotanicalOccurrences` attaches the resolved pointer to every `detail`/`aggregate` answer and
refuses one whose `release_set_id` is not the pinned generation. That check can only fire if the
pin is being ignored, which is exactly why it throws rather than draws: the alternative is a map
whose every provenance line names a generation the rows did not come from.

### `pointerKind` is required, and it is the bridge made visible

`LaneCurrentPointer` carries `pointerKind: "latest_v1" | "legacy_current_json"`, required with no
default. The serving side may still be BRIDGED over a bucket published before the checksum-bound
pointer existed (owner's bridge-then-cut pattern, repoint decisions 2026-08-25): that answer is
honest — the marker is re-read and the manifest digested on the spot — but the binding is weaker,
because nothing the publisher wrote attests to the pair.

Defaulting the field would decide on the serving side's behalf which guarantee an answer carries,
and would decide wrong exactly when a deploy skew makes the question matter. `pointerWrittenAt` is
nullable for the same reason: the legacy document records no write time, and borrowing the
generation's publication timestamp would invent provenance.

A caption showing a generation id ought to say when it is looking at a bridged answer. Once the
owner authorises the pointer advance and the Python bridge is deleted, `legacy_current_json` becomes
unreachable and this enum can lose a member — in that same follow-up, not before.
