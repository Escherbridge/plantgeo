---
type: track-spec
slug: weather_forecast_experience_20260911
status: planned
---

# Traditional weather forecast map and location experience

## Outcome

Replace the misleading impression created by sparse weather squares with a
clear forecast experience built on an admitted forecast product. Keep the
existing toggle identity for compatibility, but relabel its current product as
**Sampled weather estimates** with an explicit model-estimate subtitle. Add a
separate **Weather forecast** mode; styling alone must not turn observations,
current-condition polls or historical gaps into forecasts.

## Experience structure

The experience has three coordinated surfaces:

1. **Forecast field.** One selected scalar variable at a time—such as
   temperature, apparent temperature, precipitation, cloud cover or humidity—is
   shown as a continuous source-backed field or as an explicitly labelled
   derived interpolation. Domain and no-data masks remain visible. Sampled
   points remain points when no field product is admitted.
2. **Wind.** Forecast wind uses `u`/`v` components to support static arrows or
   barbs and an optional bounded particle layer. Reduced-motion users receive
   static vectors by default. Vectors and particles stop at no-data boundaries.
3. **Selected-location forecast.** A picked or searched location opens a
   traditional card: current/first-valid conditions, an hourly 24–48 hour strip
   and a daily 7–10 day outlook when the admitted source supports them. Cards
   show high/low temperature, conditions, precipitation probability and amount,
   wind/gust/direction, humidity, timezone, model run and update age.

The map, tooltip, card, legend and agent all use the same selected coordinates,
variable/unit definition, pinned run and valid time. A viewport-center value
does not stand in for a selected place.

## Time navigation

The UI distinguishes `Now`, `History` and `Forecast`. Forecast navigation is
hourly where the source is hourly and daily only for daily summaries. Model run,
issue time, valid time and precipitation accumulation interval are visible and
must not be collapsed into one date label. When a newer run arrives, the current
series remains pinned until the user or refresh policy moves the whole series.

The April 28, 2025 screenshot case becomes a regression fixture after catalogue,
product identity and exact-reader reconciliation. Whenever the catalogue and
exact reader report that the selected day is unavailable, the UI must clear the
sampled-observation frame, expose an honest missing state and prevent a delayed
response from another day from repainting the map. A forecast or reanalysis may
appear only under its separately identified mode.

## Visual and scientific rules

- No map-background cracks, tile seams or simultaneous zoom rungs may appear in
  a field that claims continuous support.
- Native source support, source resolution and any derived interpolation method
  remain discoverable in the legend and details.
- Empty, outside-domain, stale, not-generated, provider-unavailable and observed
  zero precipitation are distinct states.
- Wind direction uses a documented meteorological from/to convention; circular
  boundary cases and vector cancellation are tested.
- One unit definition drives the map, legend, tooltip, location card and agent.
  Temperature, wind-speed and precipitation conversions preserve precision and
  accumulation meaning.
- Forecast language never implies certainty. Ensemble ranges or probabilities
  appear only when supplied by an admitted uncertainty product.

## Accessibility, mobile and performance

Users can select a place and step through time with the keyboard. A textual
forecast table mirrors the visual timeline; missingness does not rely only on
colour. Time changes have concise announcements without animation spam. Motion
can be paused and respects reduced-motion settings. Mobile controls use at least
44-pixel targets and a bottom sheet that preserves useful map space.

The implementation sets and measures byte, texture, feature, particle,
request-to-paint and mobile frame-rate budgets. Superseded requests abort;
adjacent time slices are bounded and cached; GPU resources are released on style
swap or unmount.

## Agent parity

The agent may answer an hourly/daily forecast for the UI-selected place and
window using the same run and units. It states the model/run, valid times,
support/sample distance and missingness. It cannot silently use live weather
while the map is historical, change the selected coordinates to viewport centre
or replace a missing exact interval with a neighbouring time.

## Acceptance

Acceptance requires real-data desktop and mobile evidence for scalar fields,
wind and location cards; canvas/pixel continuity; request and animation budgets;
keyboard, screen-reader and reduced-motion checks; exact time/run consistency;
the catalogue-unavailable selected-day regression; and independent scientific, design,
accessibility and agent-parity review.

The existing multiscale, reader and publication tracks own their current
observation defects. This track consumes those corrections and the governed
forecast schema; it does not conceal them with a presentation fallback.
