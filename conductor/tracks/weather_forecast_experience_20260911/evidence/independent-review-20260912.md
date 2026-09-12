---
type: independent-review
track: weather_forecast_experience_20260911
recorded_on: 2026-09-12
status: approved_local_fixture_only
review_base: 0ee4f5bd99666760a5881ed1427301944dc7b001
---

# Independent local forecast experience review

The verifier reviewed the new forecast page, TypeScript contract and loopback
HTTP boundary in a separate context from the runtime writers. The review scope
is the local synthetic slice; it does not establish X0–X4 or real-data acceptance.

Static review finds a persistent synthetic-data notice, explicit selected
coordinates and pinned run, UTC valid times, following-hour precipitation
intervals, deterministic uncertainty disclosure and sampled-point support.
Selection changes clear the previous response, abort pending work and invalidate
late results. Partial UTC days are visibly identified; daily wind is labelled
mean vector wind. The loopback reader rejects production activation, redirects,
oversized bodies and request/run/place/time identity mismatches.

The independent pre-sweep review is complete with no remaining static blocker.
The writer corrected the precipitation header to an interval amount and preserved
the backend's daily missingness reasons through the TypeScript schema and UI.
Unit/statistic drift, request/run/place/time mismatch, unexplained hourly nulls,
false daily completeness and values in refusal envelopes are checked before
rendering. Passing tests cover future hourly navigation, zero precipitation,
late request supersession, production/remote-origin refusal, response-size caps
and executable local tool parity.

Semantic labels, a textual table and minimum target classes are source-inspection
evidence only; browser layout, assistive technology and measured mobile
performance have not been verified in this context.

Shared tRPC, map, capability and agent dispatcher files remain owner-gated.
An executable unregistered tool descriptor is a local parity handoff, not live
agent/MCP integration.

## Final verification and verdict

The verifier inspected the passing frontend logs: 11 forecast tests passed and
the selected contract batch had 221 passed plus 13 database-dependent skips.
TypeScript and the data-boundary checks passed; ESLint reported zero errors and
545 existing warnings. The Python result and its explicit failed-case recovery
are recorded in the [independent plane review](../../weather_forecast_parquet_lane_20260911/evidence/independent-review-20260912.md).
The final recovery ran the complete forecast file and two existing tests: 22
passed with one pytest cache warning. This is not a wholly green full-suite run.

The orchestrator also exercised the actual Next handler against the restarted
live local Python fixture service: 48 selected hours, two UTC summaries, exact
outside-domain refusal, and a 21,466-character response. That establishes the
local handler/Parquet seam; it does not establish browser rendering. Attempts to
start the application preview encountered framework/environment failures.
No desktop/mobile screenshot, screen-reader, canvas, animation, GPU, or measured
request-to-paint acceptance is claimed. Component tests are not a replacement for
those later checks.

**Verdict: approved for the isolated fixture experience and its exact-file commit
and push to `codex/weather-forecast-local-slice`.** No remaining actionable defect
was found in the reviewed local scope. The approval requires the persistent
fixture labels, production-disabled proxy, exact-selection refusal behavior and
unregistered capability/tool status to remain intact.

Main merge and X0–X4 completion remain outside this verdict. Real source
admission, shared map/tRPC/catalogue/agent ownership transfers, the April 28
historical evidence packet and real-data scientific/design/accessibility review
remain the next integration and release gates.
