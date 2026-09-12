---
type: implementation-evidence
track: weather_forecast_experience_20260911
recorded_on: 2026-09-12
status: locally_verified_fixture_only
review_base: 0ee4f5bd99666760a5881ed1427301944dc7b001
---

# Isolated forecast experience candidate

The user explicitly authorizes a local fixture-backed vertical slice without
shared-owner edits. This permits the isolated `/weather-forecast` page and
`/api/weather-forecast` HTTP boundary while F0 and production integration remain
open. It does not waive provider admission or imply an ownership transfer.
The companion [plane receipt](../../weather_forecast_parquet_lane_20260911/evidence/local-slice-20260912.md)
and [source contract](../../weather_forecast_parquet_lane_20260911/evidence/source-admission.md)
govern identity, support, variables, times and fixture limitations.

The local page is intended to request one exact selected sample and a pinned run,
with UTC hourly/daily cards, a keyboard-operable future timeline, explicit missing
states and a persistent synthetic-data notice. No continuous field, real provider
uncertainty, production forecast capability or 7–10 day outlook is claimed from
this 72-hour test product. Current map weather and its observation horizon remain
owned by the existing reader and renderer work.

## Files held for owner transfer

| File | Owner / next integration action |
| --- | --- |
| `src/components/map/layer-panel/LayerTimeSlider.tsx` | `parquet_reader_cutover_acceptance_20260901`: global Now/History/Forecast navigation. |
| `src/lib/map/layer-legends.ts` | `multiscale_polygon_surface_20260901`: product, support and unit legend. |
| `src/lib/map/layer-registry.ts` | `multiscale_polygon_surface_20260901`: distinct real forecast layer registration. |
| `src/lib/map/layer-render-contract.ts` | `multiscale_polygon_surface_20260901`: admitted support/renderer mapping. |
| `src/components/map/LayerManager.tsx` | `parquet_reader_cutover_acceptance_20260901`: synchronized run/valid-time map requests. |
| `src/lib/server/trpc/routers/wildfire.ts` | `parquet_reader_cutover_acceptance_20260901`: shared tRPC forecast registration. |

The isolated HTTP boundary fulfills local UI-to-plane transport. Exported tool
and capability descriptors remain explicitly unregistered. Shared tRPC, MCP,
agent, map and availability wiring requires the listed transfers; descriptors
are handoffs, not evidence those surfaces are active.

## Acceptance record

Implemented: the isolated page, explicit selected sample/run/UTC window form,
1–48 hour range and previous/next controls, hourly signals and textual table,
derived daily cards, vector-wind and per-variable missingness disclosures,
synthetic lifecycle/source/support notices, and clearing/abort/version guards for
superseded requests. The loopback-only HTTP boundary rejects redirects and caps
response bytes at 256 KiB; it is disabled in production. The local agent function
calls the identical validated reader. Tool/capability exports remain unregistered.

The final frontend sweep passed `check:data-boundary`, `type-check`, `lint`, and
`test:changed -- --base 0ee4f5bd99666760a5881ed1427301944dc7b001`.
Related tests: 3 files / 11 tests passed. Filesystem contract batch: 9 files / 221
tests passed, 2 files / 13 tests skipped for database prerequisites. ESLint reports
zero errors and 545 repository warnings. This is a scoped frontend pass, not a
full-suite or release pass. Tests cover units, zero precipitation, explained
missingness, exact time/run/place, future timeline, daily calm-vector explanation,
bounded transport and delayed-response clearing.

A live local Python HTTP service plus the actual Next route handler passed a
48-hour/two-day ready response and unsupported-coordinate refusal. Full browser
rendering was attempted but not accepted: Next webpack hit existing instrumentation
Node-builtin resolution errors; the default Turbopack mode rejected this
worktree's `node_modules` junction because it targets outside the filesystem root.
A runtime-only custom-root preview attempt did not resolve that refusal. No
tracked app/build configuration was changed to mask it. Local logs are
`.omc/research/forecast-browser-server.log`, `forecast-custom-preview.log` and
`forecast-http-smoke.log`; they are scratch evidence, not shipped assets.

Desktop/mobile screenshots, real provider fields, canvas continuity,
screen-reader assistive-technology testing, request-to-paint and GPU budgets remain
unmeasured. Component tests and the handler smoke cannot establish visual or
real-data acceptance or close X4. The separate verifier's final verdict and
Python check outcome are recorded with the companion plane receipt.

The companion Python correction passed full format/lint/mypy, then recorded
6,007 passing tests, 149 skips, one xfail and three failures. A final targeted
recovery passed all 22 forecast/environment-sensitive tests after removing the
optional HTTP test dependency and correcting temporary-directory/execution
permissions. This is not a final full-suite-green claim. See the companion plane
receipt for the full sequence and the [separate verifier verdict](independent-review-20260912.md).

The exact-file commit and narrow remote push result appear in the task's final
handoff. No main merge is performed. Next merge gate: an explicit local-code
integration decision accepting the recorded browser limitation and test scope.
Production integration still requires forecast-plane F0/F3 acceptance and
explicit owner transfers; the April 28 historical screenshot packet remains
outstanding with its owners.
