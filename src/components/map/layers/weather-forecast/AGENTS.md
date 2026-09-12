# Forecast samples

These components are prepared for the transferred forecast integration. They are
not registered with LayerManager yet. The admitted candidate exposes samples,
so scalar values draw circles and wind draws static arrows only at those samples.
Neither component fills cells, interpolates between points, nor starts particles.
Static arrows satisfy reduced-motion preference without an animation loop.

Every presentation binds run and exact valid time. Missing/nonfinite values,
unpaired wind, and duplicate samples refuse painting. Maximum draw count is
2,000 points; oversized responses refuse instead of silently truncating support.
An owner-provided source id must be unique. Style reload rebuilds from the latest
data; effect cleanup removes both layer and source. No request/cache ownership
lives here: the transferred request owner must abort superseded fetches and clear
frames when product, run, place, catalogue availability, or valid time changes.

The browser contract and location card use metric units from the same variable
catalogue. Daily summaries group UTC instants in the selected IANA timezone,
including 23/25-hour DST days. Partial temperature ranges are labelled partial;
precipitation totals require complete nonoverlapping hourly accumulation intervals.
No daily precipitation probability is derived from hourly probabilities.

Real canvas continuity, request-to-paint, mobile performance, and accessible shell
acceptance require integration and real-data UI evidence. Pure presentation tests
do not establish those acceptance criteria.
