---
type: review-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-14
reviewer: /root/independent_verifier
review_base: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
source_manifest_sha256: 31e1850a87258d7baa1264c077d5ba992fc1ae39f36b3e602dbdd7bf146e2db0
check_receipt_sha256: 11715a928ff05623cef3c3f315d9ce13555ad1c35267a6655d2234f9e61fe151
source_review_verdict: approved
bounded_browser_verdict: passed
python_quality_gate: failed
platform_qa_verdict: RED
---

# Independent Session 5 review

The reviewed Session 5 source corrections and the isolated desktop/mobile lifecycle harness are accepted within the scopes below. The fresh database bootstrap and deployment migration no-op have actual PostgreSQL evidence. Desktop and mobile Chromium journeys passed against the real local application, authentication, social APIs and Martin intervention tiles, with verified automatic cleanup. **The complete platform/runbook QA verdict remains RED and unaccepted.** The Windows Python suite still fails, environmental schema dispositions remain unresolved, and these bounded journeys do not close the complete case matrix or production gates.

The coordinator and separate authors performed implementation and execution. This verifier reviewed frozen source and retained evidence, rehashed source and receipts, and inspected actual desktop/mobile workspace screenshots. No application tests, database commands, container operations or remote requests were executed by this reviewer. The only file authored in this closure pass is this independent review.

## Candidate and receipt binding

[Candidate 13](source-candidate-20260914-13.json) is an **uncommitted working-tree snapshot**, not an immutable commit or deployment approval. Its manifest SHA256 is `31e1850a87258d7baa1264c077d5ba992fc1ae39f36b3e602dbdd7bf146e2db0`. Independent verification checked all **1,747 files**, with zero duplicate paths and zero current-file mismatches. Earlier candidate manifests are retained. Candidate 13 adds seven previously omitted configuration/database paths, including root instructions, Next/PostCSS configuration and the agri baseline; every path already present in Candidate 12 retains its hash. Historical manifests must not be described as covering those newly added paths.

The final [Session 5 check packet](check-receipt-session5-20260914.json) has SHA256 `11715a928ff05623cef3c3f315d9ce13555ad1c35267a6655d2234f9e61fe151`. Independent traversal verified **51 referenced path/hash/length entries with zero mismatches**, including 13 command records and six browser attempt entries counting preparation. Earlier failed attempts remain in the packet. Source changes present before this run remain owner changes; the coordinator's retained preservation checks do not transfer their authorship to this lane.

## Reviewed source corrections

- **Alembic ledger schema:** both online and offline environment branches now use `public.alembic_version`. The baseline clears `search_path`; explicit qualification prevents its later ledger insertion from failing. The regression executes the actual environment and compiles qualified creation, reading and insertion through Alembic's real migration context. Its initial offline-test mode/import failure was corrected before the later checks. Baseline bytes and revision identity were preserved.
- **Drizzle migration boundaries:** the shared helper sets `search_path` to `public` before migration, appends a separate boundary after each migration file in memory, and restores it after successful completion. Both callers use one connection. Original SQL order, file hashes, journal timestamps and the installed driver's single pending-batch transaction remain intact. Actual driver/dialect tests cover failure rollback, skipped historical migrations and no-op execution; the isolated database supplies separate real DDL evidence. Docker context/runtime copies and the canonical local command include the helper.
- **Windows local storage paths:** the root enters a consistent extended drive/UNC namespace before resolution. Canonical-key, resolved-containment and symbolic-link checks remain in place. Deterministic tests characterize CPython's prefix-validation transition and retain outside-root and linked-parent/leaf refusals. The original concurrent CAS test was not weakened. The simulated transition establishes a mechanism, not the captured cause of either original intermittent suite failure.
- **Tailwind discovery:** `globals.css` declares `source("../")`, limiting utility discovery to `src`. The inspected production template inventory fits that boundary; imported MapLibre CSS remains intact. The change avoids scanning inaccessible orchestration scratch. Actual later browser runs compiled and rendered the styled application. This is not a complete visual regression certification.
- **Development indicator:** `devIndicators: false` removes the development button that intercepted the Map manager. The documented setting retains compile/runtime error reporting and does not change production UI.

## Database and isolation evidence

The retained [bootstrap/no-op receipt](../../../../.omc/research/runbook-20260914/session5/resume-drizzle-sourcefix2.json), SHA256 `3eea760395e9a9c39fe33e97c507720a6724309c323e31342d1e95bf5a5208d1`, binds Candidate 10 and the existing isolated cluster. Independent reconciliation matched all six Drizzle ledger hashes/timestamps to the original migration files and found the post-bootstrap and post-no-op snapshots exactly equal. Both commands exited zero. At that checkpoint there were **zero users, zero features, eleven reference layers**, and Alembic revision `20260912_0000`. These zero counts describe the bootstrap checkpoint; later UI journeys intentionally created synthetic records.

The target was `plantgeo_qa_session5` on `127.0.0.1:5547`, cluster system identifier `7685488873518952482`, container `7c70921abb2b5923f646ac7eb458444b69c120fdce2e0c4c3d84e1785cd9f943`, image `76e9379c83d68a48877acb8b48108778bbc352932887debc2240e48d45488a18`. The initial internal-network handshake failure and subsequent explicit network repair preserved the container/volume identity. Failed migration transactions and each explicit recovery remained recorded; no automatic migration retry was introduced.

The [host support check](../../../../.omc/research/runbook-20260914/session5/support-host-check.json) records authenticated Redis `PONG` on port 6397 and Martin health/catalogue HTTP 200 on port 3147, with `intervention_tiles` as its sole tile source. The application used loopback port 3128. Runner environment scrubbing, explicit local/closed service endpoints, private credential inputs, disabled providers/workers, browser external-request guards and blocked service workers were reviewed independently. Basemap, terrain, glyph and sprite fixtures remain a limitation; application/authentication/social endpoints and intervention tile serving were real local services.

Normal application registration originally created four synthetic identities with contributor roles. Supported expert/admin promotion and the separately reviewed local viewer fixture were followed by fresh normal credentials logins. The viewer fixture is a negative-role QA arrangement, not a product promotion role. Later successful runs used explicit **resume** mode: pinned prior receipt/report/source hashes, empty registration/operator arrays, and fresh login checks of the same four names, emails, IDs and roles. No cookies or contribution rows were injected by the harness.

## Desktop and mobile results

| Attempt | Exact report / runner SHA256 | Independent result |
| --- | --- | --- |
| Desktop 4 | Report `0898b7794ec9e0f1ff71d16a01f6e9d0a18ad0e7575f66e23374a57799aa7c9a`; runner `653aabd9f5ef0decd250a2684ae67612a284f7d995172a4bf360d7548f6937a6` | Three passed, no failed/skipped/flaky tests, 50.160838 seconds. Identity reuse plus two desktop journeys. |
| Mobile 5 | Report `2ae32fff066a666945717bda571d9f28f7b4d4aa2c87cef562cdfdae956e283d`; runner `caf0e9b95dac23a406b6c44fe9f5db28a25fcf9aeca38ae6c88fcb1a89039f86` | Three passed, no failed/skipped/flaky tests, 35.18975 seconds. Identity dependency plus two mobile journeys. |

The raw reports are retained in the [desktop attempt directory](../../../../.omc/research/runbook-20260914/session5/browser-attempt-2026-09-14T21-24-09-624Z-08bfadac/browser-report.json) and [mobile attempt directory](../../../../.omc/research/runbook-20260914/session5/browser-attempt-2026-09-14T21-33-04-594Z-658183c4/browser-report.json). Embedded JSON attachments and successful assertion ordering support:

- Explicit public-request disclosure and consent before real creation; comment and like mutations; persistence across reload; unlike and comment deletion. Anonymous readers received the published detail and actual rendered tile feature, while comments required sign-in and the anonymous comments API returned 401.
- Real Point and Polygon drawing, consent-gated pending submission, approximate/default and high-precision consent selection followed by cancellation without analysis, preserved draft/canvas, and canceled discard retaining the draft.
- Expert publication and admin rejection with a required note; contributor outcomes; published Point geometry matching the original; rejected anonymous reads returning 404; actual Martin tile HTTP 200 followed by an actionable feature-detail opening.

Every inspected network attachment reported zero blocked unexpected requests and zero analysis requests. APIRequestContext probes are supported by their explicit status assertions and facts; they are not claimed as browser response-list entries. Successful mobile evidence used a 390-by-844 Chromium viewport with touch enabled, native locator taps for controls and drawing, and the actual empty-map tap entry into Location actions. Mobile mouse right-click was explicitly forbidden. Reviewer/anonymous contexts inherited mobile settings; the identity dependency retained its own desktop viewport. This is browser mobile emulation, not physical-device or cross-browser certification.

Separate mobile coordinates preserved the desktop records. Actual desktop Point and mobile Polygon screenshots showed styled workspace controls and retained geometry. Empty fixture basemaps and a map location popup remain visible limitations; no broad visual, geographic accuracy or accessibility closure is inferred.

Both successful runners exited zero. Each cleanup recorded one immediately revalidated Next process stop and two already-exited identities, no remaining/unverified processes, and a closed port; mobile recorded no process-inspection failures. This establishes successful automatic cleanup for these two runs. Historical cleanup failures remain visible: attempt 1 timed out during inspection, attempt 2 required one coordinator Playwright stop and later eleven already-exited checks, and attempt 3 timed out while processing 38 historical identities before later verification found all gone. The fixes included PS5 array enumeration, bounded filtered discovery, immediate per-PID stop validation, live-only stop worklists with known-history fallback, and pipe-handle release without claiming termination from release itself.

## Engineering checks and retained failures

Candidate 13's final frontend sweep passed **12 tooling tests and 2,512 Vitest tests across 192 files**, plus TypeScript and data-boundary checks. Lint exited zero with **9,887 warnings**. The successful lint commands explicitly excluded `.omc/**` after two earlier scratch-directory permission failures; no tracked JavaScript was excluded there. This is zero lint errors, not a warning-free repository.

The last Windows Python sweep, Candidate 11, passed format, lint and mypy but pytest recorded **one failed, 4,497 passed, 146 skipped and one expected failure**. The unresolved case is `tests/test_historical_export.py::test_spools_sorted_bounded_chunks_and_a_recoverable_checkpoint`. Candidate 10 separately retained concurrent-CAS and NASA rename failures; Candidate 11 passed those tests but did not clear the historical failure evidence. Path-length, permission and handle hypotheses must not be promoted to proven causes without captured evidence. Later Candidates 12/13 changed no Python source and did not imply another Python sweep. Database integration URLs were unset for this Python run, and no full Python quality certificate was issued. The proposed Session 6 Linux comparison is outside this acceptance and has no passing result here.

## Remaining acceptance gates

No whole case row is promoted by this review. C01's historical private/team wording needs its explicit public replacement; C05's invalid/oversized geometry, role, retry and double-click matrix remains broader; C06 other-owner/team privacy, C07 queue error/sorting behavior, and C09 expert rejection/concurrency are not covered completely. A20 confirmed discard with AI state, A21 hidden initialization/resizing permutations, A22 repeated handler/lifecycle checks, A23 actual sending/retry, and C16 real-human acceptance remain open. Mobile touch journeys add evidence without substituting for the full role, failure, browser and device matrix.

The [relation classification](../../../../.omc/research/runbook-20260914/session5/bootstrap-relation-classification.md) reconciles all **64** supplied relations exactly once: 53 clear application/control/reference purposes, four explicitly retained compact site-profile lookups requiring content/grain qualification, five land-context relations requiring reconciliation with their Parquet owner, and two unresolved raster catalogue compatibility relations. The census SHA256 is `77711030f79ba4433f24f98b75afb751558f760189b51e398c6dbb5518a0bbc3`. It has 61 ordinary tables, one partitioned table and two views, with no materialized views listed. Names/kinds do not prove row contents or absence of readers, routines and fallback paths. The environmental repository/schema-boundary gate remains unsatisfied.

Production data admission, the all-layer selected-day/zoom/state matrix, source-governed absences, scheduled advances, immutable release evidence, unresolved historical handoff references, real agent streaming and full user acceptance remain separate required work. The original long-horizon runbook goal is still active.
