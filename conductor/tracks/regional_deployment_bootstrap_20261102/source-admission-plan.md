---
type: implementation-plan
---
# Source admission after bootstrap compilation

The next coherent multi-layer runtime increment is water, FIRMS detections and current weather. This is an intermediate admission group, not completion of arbitrary-US coverage. Climate, soil and vegetation stay explicitly unadmitted until their runtime support and completeness migrations below are proven.

## Shared gate

Replace `guard_custom_operation`'s unconditional custom-source refusal only with verified per-stream support admission. The geographic receipt alone remains insufficient. Observations, absence markers, checkpoints and availability publication must resolve the same actual support descriptor. A compiler's proposed descriptor or successful offline preflight does not establish publication proof.

Water already derives regional source units; connect that real descriptor to admission. FIRMS `NasaFirmsForwardStrategy.plan_requests` currently ignores its passed region for `INGEST_BBOX`; use the passed bounded envelope and digest actual envelope/product/request rules and schema. Preserve required-product completeness checks before allowing an empty day to settle.

Current weather sampling is region-derived, but `open_meteo_current.plan_requests` labels requests with the requested historical window. Add an explicit current-capture execution path and reject historical backfill before upstream calls. Keep current capture scheduled independently of historical completion. Digest generated points, spacing, fields, units, model semantics and request assignments.

## Gridded follow-up

- Climate's active direct forward loads 397 pinned sparse support points. A region-aware resolver must drive fetching, row IDs, expected support, checkpoints and repair. Retain the exact PNW IDs/order and do not reinterpret the sparse support as its containing rectangle.
- Soil's 1,568-cell support and 1,470-valued settlement rule encode a PNW mask. Custom support needs coordinates, stable IDs/ordinals, native-box assignments and explicit mask provenance. Do not generalize the 98-null allowance to another region.
- Vegetation support identity currently uses grid name plus count and requires 1,568 cells. Hash actual geometry/IDs and sampling semantics. Keep expected cells through cloudy/no-scene responses; fewer returned cells do not reduce expected support. Preserve the pinned PNW affine/pixel behavior.

## Acceptance evidence

For the first group, prove a passed FIRMS region wins over conflicting environment bounds; source size limits and all-product completeness remain; historical current-weather requests fail before fetch; current capture still runs during incomplete history; missing/mismatched stream proof rejects both observation and absence reuse; equal-count/different-coordinate support hashes differ; and neither region can reuse the other's checkpoint/object proof.

For the grids, add reordered-input determinism, changed-coordinate identity, native-box collision handling, explicit masks, cloudy/no-scene outcomes and partial-support repair to existing support tests. Keep existing PNW fixtures unchanged. Final acceptance still requires a fresh non-PNW multi-layer deployment and a second isolated region, followed by truthful serving/UI/agent checks.

Status: read-only implementation plan from inspected runtime seams; no source in this document is newly admitted by writing the plan.

## Inspected storage and activation seams

Physical data, absence, completion and pointer keys share `layer=<layer>/kind=<kind>/...`, but source checkpoints use opaque `source-response-checkpoints/v1/<hash>.json`, turn receipts use `lane-turn-receipts/v1/<stream>/<day>.json`, cooldowns use `lane-provider-cooldowns/v1/<host>.json`, and the rollup is `availability/_COVERAGE_ROLLUP.json`. Key-prefix parsing alone cannot authorize all of these.

Separate namespace metadata verification from exact stream permission. HTTP identity/census checks may verify namespace metadata; individual readers still verify the requested stream before listings or DuckDB access. Keep root listings denied. Bind custom operational checkpoints/receipts to the admitted support digest while retaining builtin v1 compatibility. Provider cooldown access is limited to the admitted provider's declared host; exact metadata reads do not grant publishing permission.

Use a small audited source registry that recomputes descriptors from the real three planners, rather than trusting arbitrary compiler descriptions. Immutable per-stream receipts authorize the expected code-derived descriptor; changed support under an occupied root refuses until an explicit migration exists. Adding an unrelated layer does not invalidate unchanged streams.

An eventual `region-admit` operation should produce a pinned plan, then conditionally create matching namespace/stream receipts and a migration-backed database binding. Reject pre-existing unproven objects and a populated unbound executor ledger. Require a dedicated database whose singleton binding matches geography/storage identity before registry synchronization or lease acquisition. Database and object storage changes are not atomic: interrupted application must remain inert, be idempotently resumable for the same plan, and never start acquisition. These are next-step requirements, not implemented capabilities of the inactive compiler.

## Current capture must preserve weather history

Use the existing direct weather capture/retain/merge path for initial custom admission. The generic config runner currently clamps provisional daily windows to yesterday and passes only the current poll's rows to its writer; a simple executor flip would both misdate acquisition and risk replacing earlier instants. The actual weather grain is latitude/longitude/observation timestamp. Bind custom support to the actual ordered sample coordinates, spacing, 150-point cap and source parameters; reject conflicting custom bbox overrides while preserving PNW identities.

Partition by source UTC observation date, not requested/local/retrieval date. Preserve yesterday/today midnight captures and all earlier timestamps through `merge_weather_observations_day`. Explicitly handle next-UTC-day clock skew rather than allowing a third date bucket to evict a legitimate older one. Historical recovery replays retained captures without polling current data. Existing 2018 published history remains readable. Spatial sampling completeness does not establish full-day temporal coverage.

For FIRMS, derive requests from the admitted region, preserve detection IDs and the existing Decimal grid, and validate returned coordinates against that envelope. Keep all covering products required: a failed/capped product prevents complete publication, and zero detections are governed absent only after every covering product answers empty. Preserve the existing SP-over-NRT overlap rule.

## Automatic soil-mask prerequisite

Official [ECMWF ERA5-Land documentation](https://confluence.ecmwf.int/pages/viewpage.action?pageId=501029136) and the [CDS download catalogue](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=download) expose 0.1-degree invariant fields including land-sea mask, lake cover, glacier mask and soil type. An automated acquisition/cache path is possible; a manually drawn operator mask is not the prerequisite. No invariant file was downloaded during this research, so neither anonymous attachment access nor exact subset retrieval is proven.

PlantGeo uses Open-Meteo's [`nearest` selection](https://open-meteo.com/en/docs/historical-weather-api), not its default land-seeking selection. Bind the mask to the exact resolved native node, with tie handling, longitude convention and returned-coordinate checks. Preserve fractional coastal/lake classifications and variable-specific applicability. A simple threshold or repeated nulls cannot establish permanent noncoverage for all eight streams. Pin dataset/version, request or URL, coordinate metadata, content hash and conversion policy; unknown classifications remain explicitly unresolved.

The [CDS API route](https://cds.climate.copernicus.eu/how-to-api) requires an account/token. A separately acquired redistributable pinned artifact could avoid that extra key for ordinary operators, but contents, retrieval and applicable redistribution terms must be verified before promising it. Documentation captures are `.omc/research/soil-mask-*-2026-11-03.md`; no observation or bulk-mask acquisition was performed.
