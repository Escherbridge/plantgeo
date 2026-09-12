---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
observed_at: 2026-09-12T06:25:23Z
status: conditionally_approved
---

# Historical weather visual approval

The historical Wind & Weather repair is approved for the bounded local fixed
desktop unavailable-state scope. This approval covers the implementation owned
by `8e53b416ce5dc5287295a707dae2f9c121e1e993`, plus the independently reviewed
style-readiness recovery in `5cf7f59b23d61d8291c05ff9915e523710606ca1`, now
integrated in root commit `5bbe3dc039714d785cfe347ba5d9ffc61582b0bf`, and the
ready-to-outage regression in `994760e`, integrated in root `37eb50a`. It does
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
- A ready selected-day report loses its readings and map-marking action when
  the next response is typed `upstream_unavailable`; the explicit no-fallback
  state is preserved through that transition.

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
- The same continuation independently re-reviewed the ready-to-outage report
  regression and found no actionable defect; its test is synthetic-fixture-only
  and does not widen the data or forecast approval scope.
- A follow-up presentation-only candidate, `a9ff85e`, was independently
  reviewed and integrated locally as root `e54d091`. It gives stronger wind
  labels placement priority while retaining collision avoidance and documents
  that rule in the legend. After restoring the lockfile dependencies locally,
  the data-boundary check, type-check, lint and full frontend test run passed
  (150 files passed, 2 skipped; 2,232 tests passed, 13 skipped). Live
  dense-label behavior remains unverified because the governed reader/data
  service was unavailable.
- The root recheck receipt records that JavaScript dependencies were absent in
  the earlier current checkout; the restored local dependency run supersedes
  that limitation for this candidate while the owner receipts remain the
  authority for the historical browser evidence.

## Follow-up local presentation update — 2026-09-12T12:45Z

The historical report now exposes a keyboard-accessible `Retry weather`
control for transport errors and typed `upstream_unavailable` responses. The
control calls the existing TanStack query refetch, disables itself while a
request is in flight and changes its label to `Retrying…`; it does not add a
fallback frame, alter the governed reader, ingest data, or widen the forecast
scope. The focused report test covers the upstream-unavailable retry action.

The final integrated frontend sweep passed with 150 test files passed and 2
skipped, 2,233 tests passed and 13 skipped. The data-boundary check and
type-check passed; ESLint reported zero errors and eleven pre-existing React
hook warnings in the report component. Live populated-data, mobile/touch,
hover and forecast conditions remain open below.

An independent final review returned PASS after the transport-error branch
was covered directly. It confirmed both retry paths use the existing query
refetch, preserve the no-fallback behavior and remain outside the reader,
forecast, ingestion, database, writer and deployment boundaries.

## Conditions that remain open

The platform QA track remains active. Governed populated-data browser evidence
is still required for raw and aggregate temperature labels, live dense wind-label
collision behavior, precipitation hover and real selected-day transitions.
Narrow-mobile reflow and touch behavior are also open. The weather forecast
plane and forecast-experience tracks still own model-run/valid-time fields,
continuous wind rendering and selected-location hourly/daily cards.

One data-contract follow-up remains recorded for the reader track: define the
allowed relationship between `requestedDay` and `servedDay` for populated
current-day responses and expose an explicit notice or refusal when they differ.

This receipt authorizes neither deployment nor Railway, production, database,
object-store or data-writer mutation.

## Follow-up selected-day stale-frame correction — 2026-09-12T13:47Z

A read-only visual audit identified a concrete contract mismatch: the historical
report and `LayerManager` accepted `query.isPlaceholderData` as permission to
present a previous weather day while a newly selected day was pending. The
local correction now withholds that mismatched result from both the report and
`WeatherLayer`, keeps the query cache warm, and publishes loading rather than
a retained drawn day. The focused regressions cover a prior-day placeholder,
a delayed day-A/day-B report transition, and the corresponding map draw plus
registry state. This is a local uncommitted candidate pending the final
integrated check sweep and independent code review; it does not widen this
approval to populated data, forecast work, or deployment.

## Follow-up review and verification — 2026-09-12T13:52Z

The selected-day stale-frame candidate received an independent PASS. The
review confirmed that both report and map apply the same requested-day match,
that a withheld placeholder produces no weather rows or `WeatherLayer` data,
and that the drawn-day registry reports the requested day as loading rather
than a prior drawn day. The delayed day-A/day-B report and map regressions are
meaningful and passed together with the full integrated sweep. The candidate
is accepted as a local presentation correction under this approval, but its
files remain uncommitted because repository ref locking is denied in the
sandbox. No release, populated-data, forecast, Railway, database,
object-store, writer, deployment or push approval is implied.

## Follow-up font-safe wind labels — 2026-09-12

The visual lane identified a presentation risk in the map's Unicode wind
glyphs: missing glyph coverage can make direction labels appear as tofu boxes,
while aggregate-cell labels may disappear under collision handling. A bounded
candidate now uses explicit ASCII meteorological `from <cardinal> <speed>` map
labels and updates the legend and map guidance; the existing Unicode arrow
helper remains exported for compatibility. Independent review and the final
affected-check sweep passed; see
[root-integrated-checks-20260912-weather-labels.md](root-integrated-checks-20260912-weather-labels.md).
It changes no reader, data, database, source, writer or deployment behavior and
does not widen the existing local approval.
