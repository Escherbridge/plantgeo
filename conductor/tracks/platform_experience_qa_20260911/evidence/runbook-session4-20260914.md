---
type: session-evidence
status: active
date: 2026-09-14
---

# Session 4: public-request disclosure, forecast capability, and isolated browser evidence

This continues [Session 3](runbook-session3-20260914.md) under the active RUNBOOK scope.
The source changes below have been independently reviewed. Engineering checks, the bounded
browser fixture run, and the sixteen-query agent packet now have recorded outcomes. Platform QA
remains RED and the runbook remains active; these results do not close the full journey matrix.

## Candidate and bounded changes

[Candidate 7](source-candidate-20260914-7.json) binds 1,667 files at aggregate SHA-256
`6c68a9ec0faa2a49f333cfd358c4efc2b2cbe85008734003cb3c6c37bc43b481`.
It is an uncommitted working-tree candidate, not an immutable commit or deployed acceptance.
Prior-session work and the user's pre-existing changes are retained.

[Candidate 8](source-candidate-20260914-8.json) binds the same 1,667 files at aggregate SHA-256
`96c4c1c374ab5297de0507649713ee701796adaf1a9b2fd724028b2f70f81c73`.
Its only change from Candidate 7 is an independently reviewed correction to a stale assertion in
`CommunityDetails.test.tsx`; application and Python runtime source are unchanged between them.

D260914-16 is corrected at the source level. The community page's metadata and hero no longer
promise private requests or concealed locations. The community ledger, request submission
explanation and consent checkbox, and map-panel help consistently disclose immediate public
publication of the location, title, and description. Published requests can be read without
signing in. Comment reads require sign-in; request, comment, and like writes require contributor
access. This follows the actual public/protected/contributor procedure boundaries and does not
introduce a new access policy. Recommendation copy distinguishes signed-in review visibility from
public publication after approval and describes both points and drawn boundaries.

The nine-file disclosure batch is:

- `src/app/community/page.tsx`, `CommunityLedger.tsx`, and `AGENTS.md`.
- `src/components/panels/RequestSubmitModal.tsx`, `CommunityDetails.tsx`, and `AGENTS.md`.
- `src/__tests__/components/CommunityPage.test.tsx`, `RequestSubmitModal.test.tsx`, and
  `CommunityDetails.test.tsx`.

The regressions cover visible-page and metadata disclosure, the consent checkbox's public
audience, accurate social-access wording, and reachable map/review destinations. No authorization,
submission, counting, or voting behavior changed. `src/app/about/page.tsx` was not edited by this
batch; its pre-existing dirty hunks remain intact. Root retains responsibility for the final
initial-patch preservation check.

A separate three-file Python change aligns the exported `forecast_summary_for_cell` description
with its existing typed `forecast_parquet_lane_not_published` behavior. The registry no longer
advertises forecast values, uncertainty bands, or resolved cells that the unavailable lane cannot
serve. The change touches `services/agri-data-service/src/agri_data_service/agent/tools.py`, its
directory `AGENTS.md`, and `services/agri-data-service/tests/test_agent_tools_http.py`.
It changes capability disclosure and its regression coverage; it does not admit a forecast lane,
activate forecast reads, or add a fallback.

## Integrated checks and assertion correction

The Candidate 7 full frontend run executed 192 files and 2,512 tests: 191 files and 2,511 tests
passed, with one file/test failure. The failure was the existing `CommunityDetails` assertion
`/appears on the map|on the map/i`, which rejected the corrected disclosure “appears on the public
map.” The received text correctly disclosed immediate public publication and authenticated social
access. This failed full run is retained; it is not relabeled a full-suite pass.

Candidate 8 changes only that test assertion. It normalizes whitespace once and checks the actual
public disclosure, preserving the anonymous-reading and contributor-access checks.
Independent review approved the correction. The focused `CommunityDetails.test.tsx` confirmation
passed all 11 tests using the repository's `--configLoader runner` invocation. An earlier focused
attempt used the default esbuild config loader and failed on configuration access; that attempt is
retained separately and is not a test execution result. The focused pass resolves the stale
assertion; it is not a second full-suite run or a full-suite pass on Candidate 8.

Type checking and data-boundary checks exited zero. Full lint exited zero with no errors and
9,887 warnings. Python formatting, lint, mypy, and the selector-chosen full pytest suite passed.
No database-backed Python quality receipt was produced. Exact commands, candidate bindings,
attempts, scopes, and exits are retained in the
[Session 4 check receipt](check-receipt-session4-20260914.json).

## Isolated browser fixture run

The approved fixture harness runs installed Chromium against `127.0.0.1:3127`. The local runtime
scrubs inherited application environment settings, points database and Redis dependencies at the
disabled loopback port 1, and blocks service workers globally. This is an isolated test runtime;
it is not evidence of production connectivity or real authenticated application state. The font
download failed and the runtime used its fallback font; retain that limitation with visual output.

The existing five-test selection completed with four passed fixtures and one explicitly skipped
routing case. The report places all test execution between 19:59:01 and 19:59:30 UTC. These four
fixture passes are valid evidence within the isolated harness scope.

Next.js teardown then hung. After verifying the helper process subtree, root stopped that subtree
at 20:05:41 UTC; the runner exited zero and the report flushed. Total execution was approximately
442 seconds, not ten minutes. An initial diagnostic interpretation that workers had never
launched was incorrect: the report proves the tests had already run. Preserve that correction and
the teardown intervention with the run. There was no browser-test retry, and this session does not
claim hands-free harness completion. Report/runtime artifacts are bound in the check receipt.

Fixture execution does not close the 220-case platform inventory or its live/authenticated social
matrix. It also does not establish production map data fidelity, real drawing recovery, sustained
agent streaming, full desktop/mobile/accessibility coverage, or release readiness. Preserve the
distinction between an intentionally skipped test, an executed failure, and a passed fixture.

## Read-only agent parity packet

The sixteen-query read-only run is reconciled in the
[canonical results](agent-parity-results-session4-20260914.json) and
[agent-parity interpretation](agent-parity-session4-20260914.md). Independent analysis matched all
request/response hashes, byte counts, matrix inputs, deployed argument schemas, and returned tool
names. All sixteen calls returned complete HTTP 200 JSON bodies; two correctly carried typed
refusals rather than data: `surface_not_served_from_parquet` for interventions and
`forecast_parquet_lane_not_published` for forecasts.

Ten lane results explicitly reached the three-feature cap: sensors, seven grouped soil lanes,
and two watershed calls. Complete HTTP bodies do not make those spatial populations complete.
Within the bounded probes, requested/served dates and grouped depths were preserved, static
watersheds retained their August 7 release for later selected dates, shortwave neighbors retained
the requested search interval and the May 31 offset rather than substituting June 1 data, and
drought history ended at its selected September 8 day. No selected-day substitution, grouped-lane
loss, or future static-release leakage was observed in these probes.

The sensor absence retains disputed legacy export provenance and is not certified as upstream
emptiness. The shortwave result retains the unresolved `allowed_client_exposure=false` admission
field. Soil survey's unavailable state vocabulary differs between static-release resolution and
the earlier raw day reader; neither result proves publication. The deployed catalogue remains
the pre-deployment version, so the local forecast-description correction still needs release
verification. These reads do not establish browser-to-agent mixed-date propagation, every layer,
full populations, or final report citation behavior. No source corrections or production mutations
were performed by this probe run.

## Remaining gates and handoff

D260914-16 has an independently reviewed source fix and passing focused regression confirmation;
live/authenticated request acceptance remains open. Dormant request voting still requires its owning contract, and
the broader social/authentication, map/slider, land-context, botanical, and agent matrix remains
open. Environmental source custody, disputed legacy absence, sustained refresh/gap scheduling,
production/operator evidence, and independent release acceptance also remain separate gates.

The four browser fixture passes and sixteen agent reads are bounded evidence, not completion of
the 220-case inventory. No GREEN, archive, production acceptance, or full-runbook closure is issued.

The [independent review](independent-review-session4-20260914.md) binds the candidate and retained
checks separately from platform acceptance. The [local lifecycle prerequisites](local-lifecycle-prerequisites-session4-20260914.md)
give the next isolated social QA setup: native PostgreSQL lacks PostGIS/pgvector packages, and the
cached legacy container must not be mistaken for the current image. No QA database was created.
