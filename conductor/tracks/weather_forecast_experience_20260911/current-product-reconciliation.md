---
type: evidence
track: weather_forecast_experience_20260911
status: partial
evidence_scope: local-repository-only
reviewed_head: 362422e3dffebb61a68fd4d7303234c3a14a43ed
---

# Current sampled weather product reconciliation

This inspection establishes source identity and executable reader/presentation behavior at the
named HEAD. It does not establish a production response for April 28, 2025. No external service,
Railway environment, object store, or database was accessed, and no runtime file was changed.
The date is reported in the track specification and `conductor/RUNBOOK.md`; no exact-reader response
fixture was found in this checkout. The subsequent orchestration handoff supplied the saved
September 11 catalogue from the integration checkout, as recorded below.

## Saved catalogue handoff

Parsed `conductor/tracks/gapless_parquet_publication_20260901/evidence/live-reinventory-20260911.json`
in integration worktree `4ccb`; source SHA-256
`c60c582102a8915437a45f72ca042f6218ae329af3fd8fb1d23199e724993295`.
The weather entry selected by `lanes[].lane == "weather-observations"` declares
`internal_name_level_gap_ranges` containing `["2021-11-27", "2026-07-31"]`, which
includes `2025-04-28`. `catalogue-unavailable-fixture.json` preserves the parsed
keys, source hash and availability pointer. This establishes catalogue unavailability
at the saved snapshot, not the layer or exact response that painted the screenshot.
The transferred reader integration must clear the sampled frame, abort or suppress
old requests, and show no data for that date. Forecast mode cannot satisfy History.

## Product identity and current writer

The `weather` toggle maps to `weather-observations`, with label `Wind & Weather`
(`src/lib/map/layer-registry.ts:224`). This is an Open-Meteo current-condition sampled model
estimate product. It is neither a physical station network nor the NASA POWER / ERA5-Land
historical signal archive. The separate `sensors` toggle really is labelled Weather stations.

The active source-direct package is
`services/agri-data-service/src/agri_data_service/pipeline/direct/weather_observations/`.
Its `source.py` calls the existing current-condition fetcher; `support.py` computes bounded
sample coordinates from bbox and configured spacing. These coordinates do not prove native
provider cell support. `adapter.py` fixes the publication kind to `observed`; `forward.py` polls
current conditions and merges the days touched by that poll. There is no historical backfill
module in this package. The registry explicitly retires the older export adapter in favor of
this direct writer (`pipeline/parquet/lane_registry.py:1118`). Comments in the older schema,
plane and export module still describe the legacy `geo.features` ingestion lineage; they must
not be mistaken for the present writer or permission to restore a PostgreSQL read fallback.

The schema at `warehouse/schemas/weather_observations.py` preserves temperature in Celsius,
humidity in percent, wind speed in m/s, direction in degrees and precipitation in mm. Its
grain is latitude, longitude and observed timestamp. `observed_at` and `ingested_at` are
distinct; the schema has no authoritative model-run, provider issue or PlantGeo publication
timestamps. These fields cannot be invented by renaming either existing timestamp.

The sampled product remains horizon zero: `parquet-slider-capabilities.ts:707` explicitly emits
`forecastHorizonDays: 0`. A URL containing `/forecast` is not evidence of a published future
forecast product. Likewise existing deterministic/ensemble scaffolding does not admit that
product into this catalogue. Forecast admission belongs to the separate forecast-plane track.

## Catalogue and exact-day behavior

`src/lib/server/services/parquet-slider-capabilities.ts:99` declares this lane as a Parquet
daily series. `proveCapability` requires exactly one valid observed coverage record per
required zoom rung, compatible nature, nonempty bounds, intersecting published ranges and
no source-ceiling violation. The outer resolver withholds capabilities when availability
evidence is withheld or its evaluated day is not server today. It does not use the historical
signal archive to repair missing weather coverage. A missing catalogue entry, an unknown
coverage interval, a declared coverage gap, and governed absence are different evidence states.

The lane registration currently uses **history floor 2026-08-01 and publication lag 2 days**.
Its own `floor_basis` explicitly calls both fallback guesses and requests measured replacement
evidence (`pipeline/parquet/lane_registry.py:1131–1151`). The fallback status is intentionally
asserted in `tests/parquet/test_lane_registry.py:540`. April 28, 2025 predates this configured
floor, but the configuration does not establish that no historical partition exists, nor does
it prove what the screenshot's catalogue returned. This is an unresolved publication-contract
defect owned by the existing publication lane, not a forecast-data opportunity.

The mounted map calls `wildfire.getWeatherForBbox` (`LayerManager.tsx:480`); the router delegates
to `getParquetWeatherObservations` (`src/lib/server/trpc/routers/wildfire.ts:210`). For any day
other than server today, `parquet-trpc-readers.ts:1829` makes one exact `getParquetLayerDay`
request and retains the newest reading per sampled coordinate. It does not search nearby
days, invoke current weather, or switch products to answer missing history. Only server today
uses yesterday plus today and the three-hour freshness filter. This narrow live-edge exception
must not be generalized to April 28, 2025.

`presentParquetWeather` returns no features unless the typed reader state is `ready`. Terminal
absent, not-generated and upstream-unavailable results therefore clear the drawable data once
that answer is consumed. The raw Python `planes/weather_observations.py` reader scans only one
observed rung and filters the exact requested days; a typed empty raw frame is not by itself a
governed absence. Publication authority comes from the shared serving envelope above that scan.

## What currently paints and where the gap remains

`decodeWeatherRows` (`parquet-trpc-readers.ts:1170`) declares raw points at detail and aggregate
cells at derived rungs. The producer's derived tiers average captured readings over each source
day, take the newest contributing timestamp as provenance, and deliberately null wind direction.
Consequently coarse cells cannot safely acquire wind vectors merely from their mean speed.
Detail uses the latest sample at each coordinate. Contributor count is not a trustworthy count
of all samples merged upstream.

`WeatherLayer.weatherFeatures` (`src/components/map/layers/WeatherLayer.tsx:99`) draws exactly
the declared aggregate support polygon, keeps raw samples as points, excludes cells from the
temperature-dot layer, and anchors any supported wind glyph at a point. Spaced filled squares
are therefore consistent with sparse sample aggregation bins. They do not establish either a
continuous source field or permission to enlarge bins/interpolate the gaps. The legend already
explains model estimates, zoom-dependent means versus latest detail samples, and wind TO arrows
(`src/lib/map/layer-legends.ts:375`). Hover already calls model-estimate points and aggregates
estimates (`src/lib/map/hover-fields.ts:462`).

There is a remaining **catalogue-to-frame integration risk**, demonstrable from code but not
reproduced against the screenshot. `useLayerVisibility` checks toggle and permanent withholding
only (`src/lib/map/layer-toggle-context.ts:74`). The weather query is enabled by that visibility
and bbox, uses `keepPreviousData`, and passes the returned payload directly to presentation
(`LayerManager.tsx:476–492`). No selected-day catalogue availability check occurs in this weather
query/presentation block. `useDebouncedLayerDay` does preserve a concrete selected date before
the server clock arrives (`layer-toggle-context.ts:310`), so “unknown clock silently sends today”
is not supported as the current cause. The drawn-day reporting includes weather and distinguishes
retained data, but retaining a correctly labelled older frame is not sufficient for the track's
new requirement to clear a catalogue-unavailable selected day immediately.

An observed cross-date retained frame, a delayed-response repaint, a stale catalogue cache and
an exact-day producer gap remain hypotheses until the exact request, selected/settled dates,
zoom, response envelope, catalogue generation/evaluated date and rendered features are captured
together. Do not mark the screenshot resolved based on this static trace.

## Safe labels and required regressions

After the shared-file handoff, change the `weather` registry label to **Sampled weather estimates**
without changing its toggle or warehouse identity. Use the subtitle **Open-Meteo current-condition
model estimates at sampled locations.** Keep the existing detailed legend statistic/support
disclosure; its title can become **Sampled temperature & wind**. Change the weather service-fault
notice to **Sampled weather estimates are temporarily unavailable from the data service.**
Do not rename observed timestamps to forecast run/update times, label sparse bins native cells,
or apply the new wording to the separate physical station layer. The forecast toggle/card must
have a distinct identity and admitted run contract.

Existing relevant regressions, inspected but not executed in this documentation-only pass:

- `src/__tests__/services/parquet-trpc-readers.test.ts:887`: newest weather row and live freshness;
  `:953`: two publisher days at the live edge; `:1035` and `:1082`: raw-point versus aggregate support.
- `src/__tests__/components/WeatherLayer.test.tsx`: aggregate footprints, point exclusions,
  wind TO orientation, nullable direction, opacity and teardown.
- `src/__tests__/services/parquet-slider-capabilities.test.ts`: exact publication evidence,
  missing/current coverage and capability withholding; no April 28 screenshot fixture.
- `src/__tests__/components/LayerManager.test.tsx`: explicit date inputs, retained-frame accounting
  and weather upstream-fault notice; the existing tests are not the required catalogue-unavailable
  April 28 end-to-end case.
- Python `tests/parquet/test_weather_observations_serving.py`: kind/rung isolation and no forecast
  fallback; `tests/direct/test_weather_observations_*`: source, support, rows, adapter and forward path.

Add a fixture-backed matrix for 2025-04-28 only after evidence capture: catalogue withheld,
unknown coverage, known gap and governed absence; exact reader ready-empty, absent,
not-generated and fault; prior successful day pending as placeholder; delayed prior-day response;
style reload; selected day changes again before response. Assert request policy, immediate source
clear, no late repaint, truthful selected/drawn-day caption, legend/missing state and absence of
card fallback together. Include the positive exact-day ready case so a blanket date blacklist
cannot pass. Keep same-day pan retention separate from forbidden cross-day/catalogue retention.

## Shared ownership and handoff boundary

The existing gapless-publication track owns the floor/lag, forward/gap governance and availability
evidence. Reader-cutover acceptance owns exact envelopes and selected-day reader parity. The
active multiscale/integration lane owns shared renderer/reader fixes; this weather task must wait
for its exact commit/file handoff. Relevant shared paths are:

- `src/components/map/LayerManager.tsx`, `src/components/map/layers/WeatherLayer.tsx`,
  `src/lib/map/layer-registry.ts`, `src/lib/map/layer-legends.ts`, `src/lib/map/hover-fields.ts`,
  `src/lib/map/layer-toggle-context.ts`, `src/lib/environmental/parquet-presentation.ts`.
- `src/lib/server/services/parquet-trpc-readers.ts`, `parquet-slider-capabilities.ts`,
  `parquet-context-readers.ts`, `regional-context.ts`, and `src/lib/server/trpc/routers/wildfire.ts`.
- Python `interface/http/parquet_routes.py`, `agent/tools.py`, shared serving-envelope/route/MCP
  registration and `pipeline/parquet/lane_registry.py`; their corresponding tests and directory docs.

The orchestration instruction specifically reserves shared HTTP/agent/MCP reconciliation from
botanical commit `edc6afdeb23f339b40049ec0828551c2fe1a4d45` until transfer. That instruction is an
ownership boundary, not evidence that these changes have landed here. New isolated forecast
contracts/readers can proceed without editing the paths above. No runtime ownership was claimed
by this reconciliation pass.

The Next.js regional context already uses the same weather Parquet reader at detail through
`parquet-context-readers.ts:43`, with nearest published sample within a bounded half-degree box
for a selected location. This is evidence of shared sampled-product reading, not forecast parity
or exact interpolation to the chosen point. Forecast agent/MCP acceptance must preserve selected
coordinates, run, valid-time window, units, sample distance/support and explicit missingness.

## Receipt and archive readiness

Receipt: static local inspection at the named HEAD; one documentation artifact added; zero
runtime changes; no tests run and no suite-pass claim. Independent review and the parent's final
integrated sweep remain outstanding. Neither weather track is archive-ready: source admission,
governed forecast implementation, exact screenshot evidence, shared ownership transfer and
independent scientific/UI/agent acceptance remain required.
