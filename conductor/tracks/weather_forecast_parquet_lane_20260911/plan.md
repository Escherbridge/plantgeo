---
type: track-plan
track: weather_forecast_parquet_lane_20260911
status: planned
---

# Plan

This planning slice does not change runtime behavior. Implementation starts only
after the source/product contract is frozen and the active shared-file owners
complete or explicitly transfer their files.

The [September 12 evidence/contract receipt](evidence/contract-receipt-20260912.md)
freezes the next-gate obligations, screenshot evidence limits, initial acceptance
ceilings and cross-track dependency order. Source admission remains open; this
documentation slice completes none of the implementation checkboxes below.

## F0 — source and contract freeze

- [ ] Reconcile the deterministic and ensemble capabilities already scoped by
  `upstream_dataset_expansion_20260806`; record what is implemented, what is only
  scaffolded and what still requires a provider probe.
- [ ] Admit one exact provider/model product with licence, variables, units,
  cadence, horizon, domain, native support, quotas and missingness.
- [ ] Choose native-grid, sampled-point or separately derived-field publication
  per variable. Do not infer a native footprint from sampled points.
- [ ] Freeze model initialization/run, provider issue/release, PlantGeo
  fetch/admission/publication, valid, interval and lead-time identities and the
  rule that one response series uses one pinned run.
- [ ] Freeze deterministic-first delivery; record ensemble uncertainty as a
  compatible later slice rather than a blocker.

## F1 — immutable forecast artifacts

- [ ] Define run manifest, variable catalogue, support/domain mask, spatial field
  and selected-location window schemas.
- [ ] Retain or derive wind `u`/`v` components and define valid vector
  aggregation, cancellation and direction conventions.
- [ ] Define missing, outside-domain, stale, not-generated and upstream-failed
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
