---
type: track-plan
track: weather_forecast_parquet_lane_20260911
status: in_progress
---

# Plan

An independent local implementation is prepared; see `integration-handoff.md`,
`source-reconciliation.md` and `independent-review.md`. Shared registration still
waits for the exact canonical post-botanical transfer. The source probe admits a
bounded local adapter candidate only, not a production product or active service.

## F0 — source and contract freeze

- [x] Reconcile the deterministic and ensemble capabilities already scoped by
  `upstream_dataset_expansion_20260806`; record what is implemented, what is only
  scaffolded and what still requires a provider probe.
- [ ] Admit one exact provider/model product with licence, variables, units,
  cadence, horizon, domain, native support, quotas and missingness.
- [x] Choose native-grid, sampled-point or separately derived-field publication
  per variable. Do not infer a native footprint from sampled points.
- [x] Freeze model initialization/run, provider issue/release, PlantGeo
  fetch/admission/publication, valid, interval and lead-time identities and the
  rule that one response series uses one pinned run.
- [x] Freeze deterministic-first delivery; record ensemble uncertainty as a
  compatible later slice rather than a blocker.

## F1 — immutable forecast artifacts

- [ ] Define run manifest, variable catalogue, support/domain mask, spatial field
  and selected-location window schemas.
- [x] Retain or derive wind `u`/`v` components and define valid vector
  aggregation, cancellation and direction conventions.
- [x] Define missing, outside-domain, stale, not-generated and upstream-failed
  states without overloading null or zero.
- [ ] Set measured row, byte, memory, provider-call and run-retention ceilings.

## F2 — direct ingestion and publication

- [ ] Implement bounded provider discovery/fetch with durable cooldown and
  restartable work.
- [ ] Reconcile source counts/times/variables to every staged artifact and
  quarantine contract violations.
- [ ] Publish complete immutable runs conditionally; prove replay,
  interruption recovery, supersession and rollback.
- [ ] Register forward refresh, repair/reconciliation and coverage/run-status
  duties after the active executor and lane-registry owners transfer them.

## F3 — bounded readers and agent surface

- [ ] Add field and point-window readers with explicit run and valid-time
  selection, response caps, coarsening/continuation and refusal states.
- [ ] Expose one release-pinned forecast tool to the agent and MCP surfaces with
  the same coordinates, time window, units and run as the UI.
- [ ] Hand the frozen response schema, variable catalogue and support classes to
  `weather_forecast_experience_20260911`.

## F4 — independent acceptance

- [ ] Verify source/artifact conservation, temporal semantics, unit conversions,
  vector math, missingness, recovery, schedules and rollback in a separate task
  context.
- [ ] Record a bounded production-release plan; local acceptance does not imply
  deployed data, schedules or UI availability.
