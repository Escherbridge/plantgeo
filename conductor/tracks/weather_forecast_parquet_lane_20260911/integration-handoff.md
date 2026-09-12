---
type: implementation-handoff
track: weather_forecast_parquet_lane_20260911
status: awaiting-canonical-shared-file-transfer
base: 362422e3dffebb61a68fd4d7303234c3a14a43ed
---

# Independent weather implementation and integration gate

This batch prepares an isolated, local sampled deterministic forecast product.
It does not establish production admission, active scheduling, remote object-store
publication, mounted UI, or registered HTTP/agent/MCP tools. Both weather tracks
remain open. No Railway, production data, external write, or deployment was used.

## Exact dependency gate

Orchestration task `01a0904b-756b-7961-b991-cab666123be2` owns the handoff from
integration task `01a0919f-40dd-7ae2-a5a3-cd8cf7e13489`. Reported runtime freeze
`9cd72857a590aac6c0c5e9e7d6332681c7ce83aa` and sweep tree
`89e8494422b8232c8f16dbffdcf2321c7ea17bc8` are reference evidence, not transfer.
Botanical commit `edc6afdeb23f339b40049ec0828551c2fe1a4d45`, tree
`01ad55b6220a13604e8fbf8a4ceda773e35d5658`, must first reach the canonical
integration tree. Do not independently cherry-pick those shared files.

## Prepared seams

| Owner/module | Contract |
| --- | --- |
| `warehouse/weather_forecast/` | Frozen UTC run/value/series contracts, canonical variables and units, u/v math. Domain types live at warehouse L1 because foundation explicitly forbids domain nouns and same-commit callers. |
| `pipeline/direct/weather_forecast/source.py` | Probed midnight ECMWF IFS Single Runs request, strict source/units/240-hour inventory, sampled coordinates, null preservation and derived u/v. |
| `pipeline/direct/weather_forecast/artifacts.py` | Bounded local immutable source and Parquet artifacts, complete hourly inventory, manifest, immutable publication receipt with recorded commit time, conditional preferred-run pointer, replay/conflict/rollback. Preparation cannot serve data; published pinned runs survive later pointer changes. This local filesystem protocol is not a production object-storage adapter. |
| `planes/weather_forecast.py` | Explicit product/run + hourly window; bounded bbox field or selected-location sample-distance reader. Run never changes mid-response. |
| `agent/weather_forecast.py` | Host-bound picked/search forecast context with same pinned plane response; registration withheld. |
| `src/lib/environmental/weather-forecast.ts` | Wire mirror, metric vocabulary, exact selection match, hourly/local-day presentation and wind direction. |
| `WeatherDetails.tsx` | Picked/search context supplied by owner, explicit run/source/support and missingness, hourly table/buttons and partial-day-aware daily summary. |
| `WeatherFieldLayer.tsx`, `WeatherWindLayer.tsx` | Scalar sample circles and static u/v wind arrows only. No interpolated field or particles. |

## Shared registration packet

Apply only against the exact canonical post-botanical transfer, with conflict
reconciliation before the next final integrated sweep:

1. `layer-registry.ts`: preserve `weather` and `weather-observations`; label
   **Sampled weather estimates**, subtitle **Open-Meteo current-condition model
   estimates at sampled locations.** Add a distinct forecast identity only after
   reader and published availability are wired.
2. `LayerManager.tsx` and `LayerTimeSlider.tsx`: preserve separate Now/History/
   Forecast states. History selection uses exact catalogue availability; clear
   the weather frame on unavailable day and suppress late other-day responses.
   Use the parsed April 28 fixture in the experience track. Abort superseded
   requests; keep keys bound to product/run/place/window/variable and refresh an
   entire series when the selected run changes. Do not use viewport centre as
   a selected point. Mount prepared card and map components only with the same
   response context; refuse overflow rather than silently downsampling support.
3. `layer-render-contract.ts`, `layer-legends.ts`: declare sampled points and
   static vectors, canonical units, actual sample distance/source resolution.
   Do not register continuous support. Add a scalar legend derived from the
   exact browser variable vocabulary before enabling a colored scalar map.
4. `parquet-slider-capabilities.ts`, `wildfire.ts`: a separate forecast capability
   must index actual published run and valid-time availability. Retain zero
   forecast horizon for weather-observations. Transport validates the response
   before passing it to the browser mirror and enforces request/response byte caps.
5. Python HTTP blueprint + `app.py`: register bounded selected and field routes
   after botanical bootstrap is canonical. Validate product/run/context and
   expose honest ungenerated/outside-domain/stale/missing/provider failure states.
6. `agent/graph.py`, `tools.py`, `mcp_server.py`, `prompts.py`: bind the new tool
   to the selected UI forecast context. Model-supplied coordinates or live weather
   may not overwrite it. Preserve botanical list/call dispatch and registrations.
7. `pipeline/parquet/lane_registry.py`, `execution/job_executor_service.py`:
   separately schedule forward discovery/publication, repair that authors work,
   and coverage/status publication. Resolve the run-valid-time registry contract
   before adapting it to daily lane APIs. The current shared bounded HTTP helper
   drops Retry-After headers; add a reviewed durable cooldown receipt before
   enabling provider retries. Local artifacts are not a scheduled writer.
8. Add a governed remote immutable object-store adapter with conditional pointer
   writes, retention/repair budgets, operator rollback and source/artifact
   reconciliation receipts. Never introduce PostgreSQL forecast observation I/O.

## Remaining acceptance

Provider entitlement and production admission are unresolved. The successful
credential-free public probe does not establish commercial deployment permission.
Only midnight cycles and one source sample have real fixture evidence. General
run discovery, domain census, model-update changes, quotas/cooldowns, retention
ceilings and performance measurements require further evidence.

Continuous field seams and particles cannot be certified for a sampled product.
A future native-grid or separately derived-field admission needs actual support
geometry, interpolation/version/mask evidence, refusal outside support, and new
scientific review. UI acceptance still needs mounted desktop/mobile real-data
canvas evidence, keyboard/screen-reader testing, request-to-paint and cleanup
budgets. Local tests are not a production or accessibility acceptance receipt.
