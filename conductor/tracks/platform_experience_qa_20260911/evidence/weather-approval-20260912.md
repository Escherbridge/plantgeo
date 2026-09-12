---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
observed_at: 2026-09-12T06:32:00Z
status: conditionally_approved
---

# Historical weather visual approval

The historical Wind & Weather repair is approved for the bounded local fixed
desktop unavailable-state scope. This approval covers the implementation owned
by `8e53b416ce5dc5287295a707dae2f9c121e1e993`, plus the independently reviewed
style-readiness recovery in `5cf7f59b23d61d8291c05ff9915e523710606ca1`, now
integrated in root commit `5bbe3dc039714d785cfe347ba5d9ffc61582b0bf`. It does
not approve a populated-data release or the separate forecast tracks.

## Accepted behavior

- Wind & Weather enters an explicit retrying or unavailable state when the
  governed data service cannot provide the selected day.
- The unavailable path passes an empty typed feed to `WeatherLayer`, so the
  former spaced-square placeholder grid is not painted.
- Climate & Weather History renders a traditional report card with the
  selected day, source/product and SI units, plus a clear temporary-
  unavailability message that says no fallback frame is shown.
- A settled response for a different day is withheld instead of displaying a
  stale neighboring frame. No interpolated or fabricated readings are added.
- Any remaining colored cells represent declared aggregate support from the
  served contract. The report distinguishes sampled points from aggregate
  cells and identifies gaps as unmeasured.
- If the component mounts after the basemap's `style.load` event, a later
  `styledata` readiness transition recreates the weather source and all four
  visual layers idempotently; the layer does not remain blank until a future
  basemap swap.

## Evidence and verification

- [Owner implementation and focused receipts](integrated-weather-botanical-candidate-2026-09-11.md)
  record data-boundary, type-check, lint, changed-test and contract results.
- [Root browser evidence](browser-weather-20260911.md) passes the requested
  traditional report, Climate placement, retry/unavailable and no-stale-frame
  behavior on the available fixed 1280x720 desktop surface.
- An independent approval review rechecked the owner commit, current root
  weather files, tests and receipts. It found no actionable defect in the
  bounded approval scope.
- The multiscale visual acceptance continuation independently reviewed the
  style-readiness correction and approved it for this same local scope. Its
  focused test reproduces a missed `style.load` event and verifies source plus
  temperature, label and wind layers are restored.
- The root recheck receipt records that JavaScript dependencies were absent in
  the current checkout, so the owner receipts remain the executable test
  authority for this change.

## Conditions that remain open

The platform QA track remains active. Governed populated-data browser evidence
is still required for raw and aggregate temperature labels, dense wind-label
collision behavior, precipitation hover and real selected-day transitions.
Narrow-mobile reflow and touch behavior are also open. The weather forecast
plane and forecast-experience tracks still own model-run/valid-time fields,
continuous wind rendering and selected-location hourly/daily cards.

One data-contract follow-up remains recorded for the reader track: define the
allowed relationship between `requestedDay` and `servedDay` for populated
current-day responses and expose an explicit notice or refusal when they differ.

This receipt authorizes neither deployment nor Railway, production, database,
object-store or data-writer mutation.
