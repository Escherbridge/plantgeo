---
type: track-spec
slug: weather_forecast_parquet_lane_20260911
status: planned
---

# Governed weather-forecast Parquet plane

## Outcome

Publish a real weather-forecast product with explicit model-run, valid-time,
spatial-support and uncertainty semantics. This plane is distinct from the
existing `weather-observations` lane, which contains sampled current-condition
model estimates and declares a forecast horizon of zero.

The first admitted release may be deterministic. Ensemble members or provider
quantiles are a later enrichment and must not block a truthful deterministic
forecast. No current-condition poll, reanalysis archive or historical gap is
relabelled as forecast data.

## Source and product admission

Before implementation, record the exact provider product, endpoint, licence,
model family/version, update cadence, forecast horizon, native spatial support,
domain, variables, units and provider missing-value rules. The existing
`upstream_dataset_expansion_20260806` work may supply an Open-Meteo deterministic
or ensemble source, but its scaffolding and prior authorization do not establish
an admitted or published forecast product.

Admission must distinguish three support shapes:

- a source-backed model grid whose cells or pixels may be rendered directly;
- sampled point estimates, which remain points unless a separately versioned
  interpolation product is admitted; and
- a derived field, which declares its method, output resolution, distance or
  support mask, cross-validation evidence and refusal outside supported cells.

The variable contract is source-driven. Candidate fields include air and
apparent temperature, relative humidity or dew point, cloud cover, pressure,
precipitation probability and amount by accumulation interval, weather code,
wind speed, gust and meteorological direction. Wind publication must retain or
derive eastward/northward components (`u`/`v`) so aggregation remains
mathematically valid; scalar direction averages are forbidden.

## Temporal contract

Every value binds all of:

| Time | Meaning |
| --- | --- |
| Model initialization/run | Provider model initialization and run identity used to build the forecast. |
| Provider issue/release | When the provider issued or released that run, when supplied. |
| PlantGeo lifecycle | Separate fetched, admitted and published instants for the immutable run. |
| Valid time | The instant represented by an instantaneous value. |
| Interval start/end | The period represented by an accumulation or aggregate. |
| Lead time | Valid time minus model-run time. |

Readers pin one published run for a returned series. They do not silently mix a
new run into later hours. Requests distinguish current estimates, historical
observations/reanalysis and forecast valid times. Timezone conversion is a
presentation concern; stored times remain unambiguous UTC instants.

## Logical artifacts

The governed plane contains an immutable forecast-run manifest, source-variable
and unit definitions, native or derived field partitions, point-window products
for selected-location forecasts, support/domain masks, publication completeness
markers and availability indexed by run and valid-time interval. Ensemble
products additionally bind member or quantile definitions without discarding
their provenance.

Large spatial fields remain in governed object-storage artifacts and are read
through the Python data service. PostgreSQL is not a serving or ingestion
fallback. Publication completes every required variable/support artifact before
one conditional run pointer advances.

## Serving and agent contract

The field reader is bounded by product, run, valid time, variable, bbox and zoom
or output resolution. The point reader is bounded by selected coordinates,
forecast window and variables. Responses expose source resolution, represented
support, model and run, provider issue/release, PlantGeo lifecycle,
valid/interval times, units, missingness, provenance and whether a value is
native, sampled or derived.

Agent tools use the same pinned run and selected-location/window context as the
UI. They may summarize available forecast values and uncertainty, but must not
describe forecast values as observations or silently substitute another run,
place, valid time or climatology. Exact absence, outside-domain, stale run,
not-yet-generated interval and upstream unavailability are separate states.

## Publication duties

The lane follows the project data-layer standard with separately scheduled and
observable duties for forward run discovery/publication, bounded repair or
reconciliation that authors recoverable work, and coverage/run-status
publication. Provider cooldown and `Retry-After` behavior are durable inputs to
the work planner rather than tight retry loops.

## Acceptance

Acceptance requires source-to-artifact reconciliation, unit and time conversion
tests, idempotent replay, run supersession without mixed series, interrupted
publication recovery, domain/missingness preservation, mathematically correct
wind-vector aggregation, bounded reads, the three scheduled duties, rollback and
independent scientific/data-contract review.

The April 28, 2025 screenshot case remains owned by the existing gapless,
retirement and reader tracks. Those owners must reconcile catalogue state,
product identity and the actual selected-day response. A forecast product may
only answer that historical date if an explicitly admitted hindcast/reanalysis
product supports it; it must not mask catalogue unavailability.
