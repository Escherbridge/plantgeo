---
type: approval-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
status: approved_bounded_local_scope
---

# Coordinator approval: historical weather presentation

The historical Wind & Weather work is approved for the fixed local desktop
unavailable-state scope defined by
[the detailed weather approval receipt](weather-approval-20260912.md).
The approval is based on the owner implementation and focused receipts, the
root browser evidence, and the independent review recorded in that receipt.

The approved behavior includes the traditional report card with selected day,
source/product and SI units; an explicit retrying or unavailable state with no
spaced-square placeholder grid or fallback frame; refusal of stale responses;
sampled, aggregate and unmeasured support labels; idempotent recovery after a
late `style.load`; and clearing a ready report when the next response is
upstream-unavailable. The follow-up local presentation candidate integrated at
root `e54d091` also gives stronger wind labels placement priority while
retaining collision avoidance and states that rule in the legend.

This approval covers local presentation behavior only. It does not approve
populated-data release, forecast implementation, provider or model admission,
database or object-store mutation, data-writer execution, Railway, deployment
or push.

The platform QA track remains active for governed populated-data evidence,
dense wind-label collision behavior, precipitation hover, real selected-day
transitions, narrow-mobile and touch behavior, and the requested-day versus
served-day policy. The two forecast tracks remain planned and still require
source admission, immutable run publication, bounded readers, agent parity and
independent scientific, accessibility and data-contract acceptance.

## Follow-up local presentation correction — 2026-09-12T13:52Z

The approved local scope now also includes the independently reviewed
selected-day stale-frame correction. `WeatherHistoryReport` and `LayerManager`
withhold mismatched `keepPreviousData` placeholders, blank the weather map and
report while a new day is pending, and publish the requested day as loading in
the drawn-day registry. The delayed day-A/day-B report and map regressions
passed in the integrated sweep. This remains a local uncommitted presentation
candidate because repository ref locking is denied in the sandbox; the original
bounded approval remains local-only and does not authorize populated-data,
forecast, Railway, database, object-store, writer, deployment or push work.

## Follow-up font-safe wind-label candidate — 2026-09-12

The visual lane has a bounded local candidate for the remaining map readability
issue: map labels now use explicit ASCII `from <cardinal> <speed>` wording so
they remain legible when the configured font lacks Unicode arrows and preserve
the meteorological from/to meaning. The legend and map directory guidance are
aligned, and the Unicode helper remains available to other consumers.
Independent review and the final affected-check sweep passed, as recorded in
[root-integrated-checks-20260912-weather-labels.md](root-integrated-checks-20260912-weather-labels.md).
The approved local weather scope and all populated-data, forecast, database,
Railway, writer, deployment and push boundaries remain unchanged.
