---
type: track-plan
track: weather_forecast_experience_20260911
status: planned
---

# Plan

## X0 — observed-versus-forecast meaning

- [ ] Relabel the current forecast-horizon-zero product as **Sampled weather
  estimates** and explain its sampled model-estimate support.
- [ ] Add explicit `Now`, `History` and `Forecast` modes with no data fallback
  across product identities.
- [ ] Adopt the forecast plane's selected-run, valid-time, variable, support,
  missingness and unit definitions.
- [ ] Reconcile the April 28, 2025 screenshot with catalogue/product/reader
  evidence, then add catalogue-unavailable behavior as a regression across
  request, frame, card and legend state.

## X1 — traditional selected-location forecast

- [ ] Build picked/searched-location current, hourly and daily cards from one
  pinned forecast run and the selected timezone.
- [ ] Present temperature/high-low, conditions, precipitation probability and
  interval amount, wind/gust/direction, humidity, run/update age and missingness
  only where supported by the admitted source.
- [ ] Align map, card, tooltip, legend and agent coordinates, times and units.
- [ ] Provide a keyboard-accessible textual forecast table and responsive mobile
  bottom sheet.

## X2 — scalar forecast field

- [ ] Render source-backed grids without cracks, seams or simultaneous rungs.
- [ ] If interpolation is admitted, label it as derived and enforce its method,
  distance/support and domain masks; never enlarge sparse observation bins.
- [ ] Add variable selection, legends, source-resolution disclosure, unit
  conversion and explicit missing states.
- [ ] Bound bytes, textures, requests and time-slice caching; abort stale work
  and release resources on style changes.

## X3 — wind field

- [ ] Render static arrows/barbs from aggregated `u`/`v` components and verify
  meteorological direction conventions, 350°/10° wrap and cancellation.
- [ ] Add an optional bounded particle presentation with pause and reduced-motion
  behavior; do not animate across no-data.
- [ ] Keep a numerical/textual wind fallback in the location forecast.

## X4 — integration and independent acceptance

- [ ] Integrate capability, registry and agent parity only after active shared
  owners and the forecast-plane schema transfer their files.
- [ ] Run one final integrated type/lint/test/boundary sweep after the full batch.
- [ ] Record real-data desktop/mobile screenshots, canvas continuity, cold/warm
  request-to-paint, mobile frame rate, accessibility and resource cleanup.
- [ ] Obtain an independent scientific, design, accessibility and agent-parity
  verdict in a separate task context.
