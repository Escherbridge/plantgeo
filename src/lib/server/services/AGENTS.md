# Service boundaries

## Application map evidence

`regional-map-evidence.ts` serves the map's public application sources through the same containing-tile
selection contract as the Parquet agent. Intervention reads repeat Martin's public-layer, published-status,
and exact tile-intersection predicates and expose only public fields. They include the current `type`
property; drafts and review submissions require a separate authenticated workflow. Community demand
reuses the map's whole-cell aggregate and minimum three-member disclosure floor.

These are current snapshots, not historical observations. Historical requests are explicitly unavailable.
SoilGrids exposes the published raster release, containing tile and legend as context, while retaining the
numeric-value refusal until a governed numeric source is admitted. Legend bounds are never measurements.
Land-context reads preserve coverage refusals even when its transport envelope is successful.

The web assistant dispatches these reads in-process. Standalone MCP uses the bounded public
`POST /api/v1/map-evidence` endpoint through its configured `AGENT_MAP_APP_URL` origin. Application reads
honour cancellation and a twelve-second tool deadline; database statements run with a transaction-local
ten-second timeout. The endpoint is rate-limited, caps request bytes and serves no private submissions.

## Slider freshness metadata

The optional coverage `freshness` object carries registry timing through the Parquet client
and slider capability mapper. The mapper publishes it when the contributing physical rows
agree; missing or conflicting timing remains unknown. Cadence is source release cadence,
while refresh interval is scheduled ingestion, independent of browser capability polling.
The source ceiling remains a separate reported bound rather than an inferred normal delay.


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

`regional-analysis-workflow.ts` gathers bounded evidence before report synthesis. Inventory, exact selected-tile reads, selected-window historical reads, and strategy screening
are explicit stages. Report the actual reads and refusals in a server-authored audit; model prose must never
invent a successful query or turn a skipped stage into a completed investigation. Tool results
are untrusted evidence, not instructions. Keep transport timeouts and cancellation distinct
from published absence, and preserve the tool's applied bounds and observation dates.

Warehouse claims may attach up to eight `evidenceReadIds`; only executed reads with returned
measurements for one of the claim's exact tool sources can be referenced. Web and inference
claims do not carry read IDs; put their supporting measurements in separate observations.
A tool-source warehouse citation at every stage, including local, must reference an
observed read of that exact source. Its stage, dates and location are displayed beside the
claim and in exports so comparison evidence cannot silently acquire local scope. Source-wide
labels without references are rejected. Initial-context source labels still
require their distinct payload blocks; a null drought class also requires a published drought
release timestamp to distinguish measured no-drought from a gauge-only water block.
Composite fire-history reads identify only lanes whose own summary returned positive row counts.
A fire detection cannot authorize a burn-severity citation or vice versa; summary observation
date bounds retain their distinction from requested dates.
Coverage inventories, temporal publication neighbours and nearest reporting-cell metadata remain
in the audit but cannot be cited as environmental measurements. Their presence establishes
where or when to investigate; it establishes no soil, climate or fire value.

Provider report schemas are narrowed each round using `reportCitationManifest`: populated legacy
payload sources and executed measurement reads from the current audit only. The canonical runtime
validator remains strict. This keeps discoverable layers separate from citable observations;
an empty manifest still permits an inference report with an empty evidence-source array. Every
prefetched and additional result names its exact report source, read ID and status. Correction
feedback repeats the current admissible source/read pairs rather than asking the model to infer
them from lane names or legacy payload labels. New reads refresh the provider schema before the
next round. Discovery rounds require a tool call, allowing either another read or the final report;
the final and correction rounds still force the report tool. Safe validation diagnostics include
the canonical `evidenceReadIds` path without recording model text, coordinates or unknown fields.
The report tool and system prompt ask for 4–6 consolidated observations (hard
maximum twelve), with 0–3 recommendations (hard maximum eight). Bounds failures report the
actual previous collection sizes and request consolidation while preserving essential dates,
units and source/read pairs. The one correction budget and all runtime bounds remain intact;
the application never truncates a report into validity.

When the current manifest has tool measurement IDs, each provider claim uses a nested `anyOf`
with complete object branches: warehouse tool citations require matching nonempty
`evidenceReadIds`; inference and web omit the field from both properties and required fields.
A legacy branch permits warehouse claims without IDs only for available legacy payload sources.
Every branch copies all shared claim properties and required fields and rejects additional
properties. Each singular-source warehouse observation or recommendation has one branch per
observed source: its source is required and fixed, and its ID choices contain only that source's
executed measurement reads. Newly executed reads refresh the corresponding branch. The
multi-source risk summary retains its combined choices and exact source/read runtime validation.
The outer report remains an object. This follows the Google structured-output
example for nested unions; live followups with partial branches omitted sibling claim fields,
and empty-array cardinality alone failed to keep IDs off inference recommendations. These are
observed provider behaviors, not a general JSON Schema limitation. The Gemini projection
preserves small zero/one cardinality constraints while moving large collection and text limits
into descriptions. Only `remediation_report` is advertised, since offering two identical report
declarations produced duplicate calls. The legacy alias remains exported and dispatchable.

For compatibility with earlier provider responses, `normalizeProviderReport` translates only
an own empty array on a known risk/observation/recommendation claim explicitly labelled inference
or web into the canonical omitted field. It never deletes nonempty IDs, changes a warehouse
citation, attaches inferred IDs, or recurses through arbitrary data. Strict canonical validation
and exact source/read matching follow this translation. With no tool measurements the provider
omits the field entirely. Sampled history describes only its sampled dates; an incomplete scan
cannot establish the extrema or continuity of the requested period.
The final consistency instruction checks comparison directions against dated values in both
findings and recommendation rationales and requires all supporting comparison reads to be cited.

The report-schema projection and OpenRouter reasoning budget apply only to the exact model IDs
`google/gemini-2.5-flash-lite` and `google/gemini-2.5-flash`. Both receive
`reasoning: { max_tokens: 2048, exclude: true }` on every model round, including the bounded
correction. OpenRouter documents the token budget and exclusion of reasoning from returned
responses in its [reasoning-token guide](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
Live followup comparisons without reasoning mixed stale values and reversed measured directions;
Flash with this bounded budget produced consistent dates, values, directions and read citations
in the reproduced case. This is observed improvement, not a guarantee of factual correctness.
The default remains Flash Lite and deployment may choose Flash through `OPENROUTER_MODEL`.
Other configured models retain their existing request settings and scoped canonical report schema.

Inventory has an eight-second transport deadline. Local and temporal stages reserve twelve and
fifteen seconds respectively, with at most three concurrent reads. Initial retrieval selects up to
six relevant layers, prioritizing visible and explicitly dated layers before VPD, vegetation,
precipitation, soil moisture, soil temperature and soil survey. Every catalogue layer stays
available for additional reads, including disabled toggles; catalogue size never forces an eager
read of every layer. Stage exhaustion is recorded as skipped or failed work.

The live regional route starts in selection-only assembly mode: location and calendar metadata,
with no environmental measurement blocks and no old radius or latest-date prefetch. All current
measurements must come from the tile workflow. The legacy assembler signature remains available
for existing non-selection callers; it is not used by the live assistant route. Legacy clients
receive an explicit default selection, so they cannot re-enable the old prefetch accidentally.

`surface_evidence_for_selection` is the general measurement reader. Its bounds are the tile
containing the authorized point at the active map zoom and each layer's selected calendar day.
Point support is determined by the source tile or cell containing the coordinate, never by a
fixed centroid radius. Local reads request only that day. Historical reads request the inclusive
calendar window selected in the analysis panel (day, month or year, with 1?10 units on either
side). Month and year arithmetic preserves month ends and leap days. Missing and future dates
remain in the request; they are never replaced by the newest available observation.

The server binds every additional generic read to the current request's point, zoom, layer day
and window, preserving only its layer choice and integer history cursor. This is recomputed on
every follow-up. Prior conversation prose and read IDs cannot establish current observations.
Synthesis may request twelve additional reads in batches of three over six model rounds, with
fifteen-second transport deadlines. Page continuation preserves the backend's balanced schedule;
it cannot narrow the window to recent dates. The context reducer retains 31 history envelopes
and their sampled day list per page (120 weekly drought entries for the specialized reader).
Other collections retain eight entries with explicit omitted counts. Coverage and empty history
envelopes are never counted as measurements. Actual requested dates, range, scale, zoom and
returned provenance dates persist in the server audit and are visible beside citations.

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

## Region identity on row reads

Style review W9, S4. W9-A put `region_slug`/`region_display_name` on the coverage payload and a
three-way verdict behind `regionIdentityVerdict()` (`src/lib/region/region.ts`), then enforced it on
ONE axis: `parquet-slider-capabilities.ts` withholds every Parquet-owned capability under
`region_identity_mismatch`, and `layer-region-binding.ts` drops the payload's bindings. The row
reads never asked. A deployment with `PLANTGEO_REGION` and `NEXT_PUBLIC_PLANTGEO_REGION` set to
different slugs therefore drew a WITHHELD SLIDER OVER RENDERED FOREIGN ROWS — the exact failure the
verdict was added to prevent, half-closed.

Both axes now consult the one verdict. `assertServedRegionMatchesBundle()`
(`parquet-plane-client.ts`) runs first in `getParquetLayerDay`, `getParquetLayerDayWindow` and
`getParquetLatestRelease` — which is every Parquet row read in the tree, since each tRPC reader and
`land-context/parquet-reader.ts` goes through one of those three — and throws
`ParquetRegionIdentityError`, carrying the SAME typed `region_identity_mismatch` reason the slider
withholds under. `botanical-occurrences-client.ts` speaks its own wire contract to its own routes,
so it calls the shared guard explicitly rather than inheriting it, before the pointer read: a
generation resolved from another region's warehouse is not a pin this deployment may hold.

FIVE CHOICES WORTH THE WORDS:

1. **`unstated` renders, exactly as today.** A census that names no region makes no claim. The
   version rule (`parquet_ops/AGENTS.md`, additive-and-silence-is-safe) is why `region_slug` did not
   bump `COVERAGE_SCHEMA_VERSION`, and refusing on silence would blank a correctly configured map
   during any deploy window. Only a STATED disagreement refuses.
2. **A census that did not ANSWER is silence too, not a claim.** The guard swallows a coverage fault
   and lets the read proceed. The slider states coverage outages on its own axis
   (`parquetCoverageUnavailable`); restating one as a region refusal would name the wrong fault and
   would make every layer in the tree fail closed on a transient census timeout.
3. **The identity is LEARNED, not re-read.** `lastStatedRegionSlug` is module state beside
   `cachedCoverage` and deliberately not part of it: the lane cache answers "may I reuse these
   lanes" and expires in five minutes, while whose region is served changes only on a redeploy.
   Every decoded census overwrites it, so the slider's own reads keep it current for free. Once an
   identity is learned a row read awaits nothing; while none is, the attempt is bounded to one per
   `REGION_LEARNING_RETRY_MS` (60s) for the whole process — enforced by the early return in
   `learnServedRegion` (`parquet-plane-client.ts:980`), cleared only by
   `resetParquetCoverageCacheForTests` (`parquet-plane-client.ts:1208`). Awaiting a census
   on every read would have put the 8-second cold-census timeout in front of every layer on a
   pre-bootstrap deployment: a latency regression paid by correct deployments to catch a broken one.
   The residual hole is one coverage window wide: a serving side redeployed into another region is
   not detected until the next census decode.
4. **`ParquetRegionIdentityError` is deliberately NOT in `parquetUpstreamFailure`'s taxonomy.**
   Nothing upstream failed — the plane answered honestly about ITS region — so classifying it as
   `upstream_unavailable` would name the wrong party and invite a retry only a deploy can fix. It
   propagates out of `boundedResult` as itself.
5. **A deployment that never decodes a census is guarded by NOTHING, deliberately, and says so.**
   Style review W10, S4: choices 2 and 3 compose into a guard that is inert for the life of a
   process whose census never answers — and memory `plantgeo-deploy-observation-2026-09-03` records
   the pre-bootstrap census at ~28s against the 8s timeout BY DESIGN, so that process is a cold
   deployment, not a hypothetical. Failing closed after N attempts was considered and rejected: it
   turns a rare, static, deploy-time misconfiguration (two environment variables naming one region)
   into a certain total outage on every cold start, which is a worse trade than the one it buys.
   Staying open is therefore paid for in visibility, and that payment is what wave 12 makes good.
   `learnServedRegion` logs `Parquet region guard inert` once per census ATTEMPT
   (`parquet-plane-client.ts:1008`), and the attempt is memoed on ENTRY rather than after the census
   settles (`parquet-plane-client.ts:980`, `:992`), so one back-off window costs one attempt and one
   line however many row reads arrive inside it — held by that early return and by
   `src/__tests__/services/parquet-plane-client.test.ts` `logs one line, however many row reads
   arrive inside one back-off window`, not by this sentence. Stamped after the await, as it was
   through wave 11, the gate stood open for the whole ~28s census and every row read in the window
   logged its own line (style review W11, S4): hundreds per window, repeating every ~88s for the
   life of the process. The counter shared that defect and is fixed with it — all those waiters read
   one stale memo, so `failedLearningAttempts` could never exceed 1 however many lines were written.
   It now counts attempts, one apiece, and `unguardedRowReads` counts the row reads those attempts
   let through, stated both in the line and in the status so the flood is a number rather than a
   log volume.

   `servedRegionGuardStatus()` (`parquet-plane-client.ts:1071`) reports that pair, plus `armed` and
   `inertReason`, for any health surface that would rather state inertness than assume safety.
   **`armed` means CAN REFUSE, not "a census decoded"** (style review W11, S3): it is
   `regionIdentityVerdict(...).kind !== "unstated"` (`parquet-plane-client.ts:1075`), so a census
   that decodes and states `null` or `""` leaves it `false` — `regionIdentityVerdict` calls both
   `unstated` forever (`region.ts:294-295`), which means the guard can never reach `mismatch`, and
   the empty slug is a producer the manifest type permits (see *Where `unstated` can come from*,
   below). The older definition
   was `lastStatedRegionSlug !== undefined`, which reported `armed: true` on exactly that
   permanently inert deployment; that state is now `statedRegionSlug !== undefined` and
   `inertReason: "census_stated_no_region"`, told apart from `"no_census_has_decoded"` because the
   two have different owners — one may heal within a back-off window, the other only on a
   serving-side deploy. Nothing consumes that status yet; it is the seam a readiness probe should
   read, and its absence is a gap, not a claim. The guard arms itself on the first census that
   decodes A REGION, including one the slider read.

**Where `unstated` can come from.** On the live path, only a serving deployment older than the
field: `interface/http/parquet_routes.py` is the sole `WarehouseCoverage` construction that reaches
`to_wire`, and it always passes `region.slug` from `load_region()`. `to_wire` always EMITS both
keys, so a current deployment cannot send them absent, and `coverage.py`/`gap_repair.py` build the
dataclass but never serialize it. Two residual producers, both stated rather than assumed: a web-tree
construction of `ParquetWarehouseCoverage` that omits the optional field (test fixtures today), and a
region manifest declaring an empty `slug` — `manifest.py` types it `slug: str` with no non-empty
constraint, and `regionIdentityVerdict` reads `""` as unstated. Neither is reachable from a shipped
manifest; if `slug` ever gains a computed or defaulted value, this paragraph is the thing to re-check.

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

### `pointerKind` is required, and the legacy bridge is retired

`LaneCurrentPointer` carries `pointerKind: "latest_v1"`, required with no default. The `current.json`
bridge (owner's bridge-then-cut pattern, repoint decisions 2026-08-25) was cut on 2026-09-18: the
Python serving side (`planes/botanical_occurrences.py::read_current_botanical_release`) no longer
resolves through `current.json` at all, so `legacy_current_json` can never legitimately reach this
decoder again. `LANE_POINTER_KINDS`/`botanicalProxyPointerSchema` enforce that at the schema level —
a legacy value is now a `contract_mismatch`, not a weaker-but-valid answer to pass through.

Defaulting the field would still decide on the serving side's behalf which guarantee an answer
carries; kept required for that reason even with one member.
