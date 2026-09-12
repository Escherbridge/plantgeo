---
type: reference
slug: platform_experience_qa_20260911
---

# Browser QA matrix and evidence contract

This is the expansion contract, not a completed test report. The executing task
creates one case per requirement and materially different behavior. Cover every
required dimension; any reduced combination set needs a documented rationale
and independent verifier acceptance. Unavailable infrastructure is `blocked`.

| Dimension | Minimum expansion |
| --- | --- |
| Surface | Every surfaced route, control, map layer, detail, workflow and agent/MCP capability; compare current main with incoming child requirements. |
| Identity | Anonymous plus local synthetic viewer, contributor, expert and admin; allowed and denied operations, session transitions and ownership boundaries. |
| Device/input | Desktop and mobile viewports; mouse, keyboard and touch; portrait/landscape where layout changes. Record emulation versus physical device. |
| Accessibility | Accessible names/roles, logical tab order, visible focus, dialog/sheet focus restoration, Escape, screen-reader announcements, text equivalents, contrast/non-colour cues, 200% zoom/reflow, reduced motion and touch target size. |
| State/recovery | Loading, populated, empty, missing, stale, error, retry, cancellation, refresh and deep links; geometry invalidity and permission denial where applicable. |
| Time/data | Latest, historical, governed absence, unavailable/outside coverage; rapid day/place changes, delayed responses and neighbour disclosure; pinned static release or forecast run/valid interval. |
| Spatial/rendering | Coarse/middle/detail zoom, pan, selected feature, declared support/domain edge, seams, overlaps and rung transitions; saved camera and canvas/pixel evidence. |
| Cache/performance | Named cold and warm browser/application/data-cache states; catalogue time, request TTFB, request-to-paint, errors, request/byte counts and interaction/frame budgets where relevant. Local resets only; do not assert cold upstream state without proof. |
| Agent/MCP | Same role/place/day/window/filter/release/run/units as UI; values, provenance, temporal/spatial neighbours, refusal and missingness; tool response and final answer. |

Each case records:

- case ID, exact requirement citation, feature owner and dependent track/task;
- expected behavior, preconditions, synthetic role/fixture identity, steps and
  observed result, with no credentials or real-user personal data;
- candidate commit, tree hash, review base, dirty-state/diff identity, local
  service revisions/configuration and immutable data release or fixture identity;
- browser/version, viewport/device/input, camera, day/time/run, cache setup and
  UTC timestamps; distinguish measured cache status from assumptions;
- original screenshot/canvas capture paths and hashes, relevant request/response
  traces, console errors, accessibility observations and measured timings;
- status, defect/blocker link, author task and independent verifier task/verdict.

Evidence lives under `evidence/` with stable relative links. Screenshots must
show the state being asserted; pixel comparisons document region, baseline,
tolerance and result. Fix or environmental drift makes old evidence historical,
not automatically applicable to the new tree. Link production receipts with
their exact deployed revision and scope separately from local captures.

The task ledger binds task ID/title, role, track, checkout, owned paths, base,
candidate commit/tree, dependencies, integration disposition and receipt hashes.
The final verdict enumerates passed, failed, blocked, unrun and justified
not-applicable cases and reconciles that ledger against the final integrated
tree. A missing required cell prevents GREEN.
