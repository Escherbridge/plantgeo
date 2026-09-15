---
type: review-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-14
reviewer: /root/independent_verifier
review_base: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
source_manifest_sha256: 96c4c1c374ab5297de0507649713ee701796adaf1a9b2fd724028b2f70f81c73
bounded_engineering_verdict: approved
platform_qa_verdict: RED
---

# Independent Session 4 review

The public-request disclosure and forecast catalogue corrections are approved as bounded source
changes on candidate 8, with the exact verification scopes below. Four existing Chromium
fixture tests and sixteen deployed typed-tool reads add useful behavioral evidence.
**Overall platform QA remains RED and unaccepted.** Neither those fixtures nor source-check
success closes the 220-case inventory, live authenticated social journeys or release gates.

The reviewer inspected source, installed dependency behavior, logs, retained request/response
bodies and candidate hashes. No application tests, database commands or runtime edits were made
by this lane. Separate read-only local environment/Podman planning is preserved in the
[local lifecycle prerequisite handoff](local-lifecycle-prerequisites-session4-20260914.md).
Authored review output consists of these two evidence documents and the authorized verifier
disposition in the task ledger. Any coordinator-directed final link-scan/check-hash refresh is
documentation verification, not another application test sweep.

## Candidate and source review

Independent hashing verified all 1,667 files in [candidate 8](source-candidate-20260914-8.json),
with zero mismatches and aggregate SHA-256
`96c4c1c374ab5297de0507649713ee701796adaf1a9b2fd724028b2f70f81c73`.
Candidate 7's aggregate is
`6c68a9ec0faa2a49f333cfd358c4efc2b2cbe85008734003cb3c6c37bc43b481`.
The sole candidate 7-to-8 delta is `CommunityDetails.test.tsx`; no runtime changed after the
integrated checks. The earlier candidate 6-to-7 batch changed twelve files, covering the public
copy, its regression tests/directory rationale and the forecast catalogue description/regression.
The pre-existing about-page owner changes were preserved rather than rewritten by this batch.

Community metadata, heading, page body, ledger, request modal and details now agree with the
actual router policies: requests publish immediately with public location/title/description;
anonymous readers can inspect published requests; comments require authenticated reads; request,
comment and like writes require contributor access. Consented proposals can be visible to
signed-in readers while awaiting review and become public after approval. The change does not
alter authorization or activate dormant voting. Regression assertions cover the prior false
private-request assurances, actual public publication and social access distinctions.

The model-facing forecast description now states the existing
`forecast_parquet_lane_not_published` refusal, unavailable forecast values and null resolved
cell. The implementation already returned that result after input checks without a database or
forecast fallback. Signature, execution path and provider behavior are unchanged. The HTTP
catalogue regression checks the exported description instead of merely inspecting a local
string. Deployed description verification remains separate from this local correction.

No remaining material correctness or security defect was found in this bounded authored source
batch. D260914-16 is source-corrected, with live/authenticated acceptance still open.

## Engineering check scopes and retained failures

All nine initial command-log hashes and five browser-artifact hashes in the
[Session 4 check receipt](check-receipt-session4-20260914.json) matched at substantive review.
The full frontend selector ran on candidate 7: 191 files passed and one failed; 2,511 tests
passed and one failed. The failure was the stale `/on the map/` assertion, which rejected the
new truthful phrase `on the public map`. The reviewed candidate 8 test correction normalizes
whitespace and explicitly asserts immediate public publication and the access disclosure.

The first focused confirmation failed before tests because the default esbuild config loader
could not access a parent directory. The preserved corrected invocation uses the repository's
`--configLoader runner` and passed all eleven tests in the affected file. This is a full
candidate-7 run reconciled with a candidate-8 affected-file pass, **not a clean full-suite run on
candidate 8**. Neither original nonzero exit is hidden or rewritten.

Candidate 7 type checking and the data-boundary gate passed. Lint completed with zero errors
and 9,887 warnings. Python format, lint, mypy and the full selected pytest surface passed; the
selector explicitly records `mode: full` and `receipt_eligible: false`. Database integration
environment variables were removed, and no database-backed/full Python quality certification
receipt was produced. Python is unchanged in candidate 8.

The historical missing `coverage.json`, `coverage.csv` and environmental-track
`fanout-launch-20260913.md` references remain a nonzero documentation gate. Final link counts
and their log binding belong to the appended documentation check; they are not application-test
results. The final scan includes both new reviewer documents and their ledger links: 254 local
links checked, with exactly those three historical references missing. After that refresh the
reviewer rehashed all ten command logs and five browser artifacts, finding zero mismatches;
all 1,667 candidate source hashes still match. The final check-packet SHA-256 is
`88e43843c72575be1b4861c60d0c6919bb15924a421d19f5f4a3cc62dbd04b6d`.

## Browser isolation review and corrected execution timeline

The runner was reviewed before execution. It uses a fresh server on `127.0.0.1:3127` with
`reuseExistingServer: false`, a system-only ambient environment allowlist, scrubbed dotenv keys,
synthetic auth configuration, disabled loopback database/Redis endpoints and disabled background
workers/email. Two pre-execution review findings were fixed: service workers are now blocked
globally, and dotenv key extraction covers Next's installed parser grammar including colon
assignments and names containing digits, dots or hyphens. No application DB was created for
this fixture suite.

The final report contains four expected passes, one explicitly skipped routing test, zero
unexpected or flaky tests, and no report-level errors. Passed assertions cover canonical
saved-location focus with late service-area data, coordinate-search flight while the dock
closes, MapLibre canvas/basemap fixture loading, and fixture search/recent-history behavior.
These are real browser executions against synthetic map/API fixtures. They do not test real
authenticated writes, live environmental data, proposal drawing recovery or AI streaming.
Server logs also record Google font download failures and fallback fonts, limiting typography
and visual-fidelity conclusions.

Individual tests ran from `19:59:01.299Z` through `19:59:30.502Z`. Next teardown remained pending
afterward. Root's stop receipt records subtree intervention at `20:05:40.918589Z`; the runner's
environment receipt completes at `20:05:41.184Z`, about 441.85 seconds after its
`19:58:19.333Z` start. The report duration is about 438.62 seconds from its own later start.

The stop receipt's original diagnosis, that no browser worker had launched after approximately
ten minutes, was incorrect. The final report disproves the no-launch claim and establishes
that tests had completed several minutes earlier. Preserve that original diagnostic receipt
with this correction. Report flushing and exit zero followed manual cleanup; this is not
unattended clean runner termination. The fixture assertions nevertheless retain their recorded
passes. No browser retry occurred. The restored runner and configuration are byte-identical to
the executed attempt-1 copies; an unexecuted temporary draft contributes no validation claim.

## Independent typed-agent evidence review

The [canonical reconciliation](agent-parity-results-session4-20260914.json), SHA-256
`fd793239f486ab7458a075b059830d1c31cd091162d9204efa67d44045f49cdf`, binds the retained run receipt
`fb9972488d8874d1ae1710a13d521295de2d79b0dc6302e638ab67c8959a50d9` and deployed catalogue
`7895c5e471736f2b26543b292cc8b85f1464613dbfca6f2da38fb3c12e86c00b`.
Independent local checks verified all sixteen exact request/response hashes, response sizes,
matrix inputs, returned tool names, complete HTTP-200 bodies, and the executed script/matrix
hashes. The reviewer inspected the result objects and reproduced their substantive interpretation;
no additional remote reads or model calls were performed.

The observations support bounded date/state behavior:

- Shortwave May 31 returns a published May 31 feature; June 1 remains unwritten with no served
  day/features. Coverage and temporal-neighbor tools retain the hole; the neighbor is May 31,
  offset -1 within May 25-June 8, rather than a replacement June 1 value.
- Weather September 6 is unwritten. September 14 is published/served that day with no features
  in this 25 km point query; that is not global source absence or a mismatch with a larger bbox.
- Sensors September 6 repeats the disputed legacy export absence, including its explicit
  statement that the upstream system was not contacted. September 9 returns bounded published
  readings dated that day. Neither checksum agreement nor this read certifies upstream emptiness.
- All three soil-moisture depths and four soil-temperature depths are represented separately
  for September 5, with matching requested/served and observed dates in returned records.
- Watershed requests on August 7 and September 14 both retain the August 7 static release.
  Centroid-distance and containment fields remain distinct from the older observation instant.
- Soil survey remains unavailable. Its static-resolution `day_not_written` versus the raw day
  reader's earlier `lane_never_written` is a vocabulary difference, not evidence of publication.
- Drought history is anchored to September 8 and returns the August 25, September 1 and
  September 8 releases within the stated scan window.
- Interventions and forecasts return their exact typed refusals; forecast values are empty and
  the resolved cell is null. HTTP success does not imply data availability.

Ten lane feature lists explicitly hit the cap: sensors, seven soil depth lanes and two watershed
calls. HTTP bodies are complete, but feature populations are truncated. The shortwave feature
also retains `allowed_client_exposure=false`, an unresolved admission field already documented
by the owning contract. This review neither resolves that policy nor relabels the provenance.
These deployed reads are on the deployed base, not the local uncommitted candidate. They do not
prove browser-to-agent mixed-date propagation, full spatial populations, every surface or final
report citation behavior.

## Remaining work

The [session ledger](runbook-session4-20260914.md) correctly separates engineering checks,
fixture browser execution, direct typed-tool reads and production acceptance. The local lifecycle
handoff identifies package/image/role prerequisites without starting a database. Native PostGIS
and pgvector package absence, unverified pgvector in the supported container path, and the lack
of a supported platform-viewer fixture remain concrete setup gates.

The 220 journey cases remain 178 not_run and 42 blocked; four fixture tests are not four completed
full matrix cases. Real local consent/review/social workflows, real-human contributor acceptance,
drawing fidelity, all-layer sliders/neighbours/agent parity, source admission, disputed absence,
missing historical evidence, immutable release identity and sustained operation remain open.
**No GREEN, deployment acceptance, archive or full-runbook completion is issued.**
