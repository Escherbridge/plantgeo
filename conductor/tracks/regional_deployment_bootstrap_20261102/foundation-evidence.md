---
type: validation-report
---
# Regional manifest foundation evidence

The `data/regional-bootstrap` branch starts from deployed data release `8119cc05`. This batch implements canonical custom-manifest parsing, validation and Docker propagation. It does not admit custom production ingestion: namespace/support proof, serving/cache identity, bootstrap and source generalization remain open in the plan.

## Independent review

Review found two initial lattice risks: four-decimal cell-key collisions and unbounded grid allocation. Both runtimes now require exact pitch/centroid identities and cap a lattice at 100,000 cells before enumeration. Follow-up review found arbitrary input keys exposed through Python validation locations and coercive CRS acceptance inconsistent with TypeScript. Generic safe errors and a strict integer/null CRS contract resolve those findings. The reviewer independently inspected both fixes. Regression coverage includes input-key/value redaction, traceback/stack safety, coercion refusal and shared manifest acceptance.

This is targeted follow-up review, not an additional dev-drive cycle. The original release completed its two requested cycles.

## Live desktop baseline

The installed Playwright Chromium runner was used because no dedicated Playwright MCP tool was exposed in this session. It inspected the public site without changing production configuration or triggering historical ingestion.

At `2026-11-03T05:29:34.853Z`, four assertions passed: weather distance displayed 38.9 km; January 2018 weather retained the selected day and rendered historical aggregate data; October 22's unpublished weather displayed “Nothing is drawn”; satellite vegetation disclosed unknown acquisition timing and explicitly scoped the availability timeline to measured data. No uncaught page errors were observed. The satellite screenshot was visually inspected.

A second capture exercised water, fire and ERA5-Land soil moisture. Water reported October 31 with a November 1 source ceiling; fire reported November 1 at its source ceiling; soil moisture reported October 28 with an October 29 source ceiling. Each displayed a matching past-day notice. All three scenarios completed without uncaught page errors. The soil screenshot was visually inspected. The first capture attempt stopped after water because its off-switch locator assumed a different accessible label; correcting the script locator completed the run. This was an automation failure, not a product fix.

Artifacts: `.omc/research/regional-baseline-ui.json`, `regional-baseline-weather.png`, `regional-baseline-satellite.png`, `live-layer-status-pass.json`, and `live-layer-{water,fire,soil}.png`. These are bounded desktop observations, not mobile, authenticated, offline or complete agent acceptance.

## Integrated validation

Both changed-test selectors chose full suites for shared region/dependency/Docker changes. Full boundary, TypeScript and lint checks passed; lint retains 542 existing warnings and zero errors. The full web run passed 247 files and 3,596 tests, with one new test failing because Windows environment assignment normalized an unpaired surrogate before it reached the decoder. The correction tests that invalid input directly at the decoder. The follow-up passed all 11 region files / 130 tests, full TypeScript and changed-test lint. This is a full sweep followed by a scoped correction check, not a second full-suite claim.

The first Python run passed mypy, but failed test-file formatting/lint and pytest (6,174 passed, two failures, 676 setup errors, 96 skipped, one expected failure). The setup errors came from access denial to the shared Windows pytest temp directory. The two assertion failures were an oversized Windows environment value in a byte-limit fixture and an outdated zero-pitch validation-message expectation. Tests-only corrections preserve the byte boundary and invalid-input assertions; independent inspection found no weakening.

The full repeat used an isolated workspace temp directory with inherited PROJ overrides removed for that process. Formatting, mypy and the full pytest suite passed (pytest duration 431.10 seconds; the wrapper does not retain a passing test count). Two remaining test-file lint findings prevented a new quality receipt. After correcting those assertions without changing runtime code, full formatting/lint and the two affected Python test files passed (75 tests). This scoped follow-up does not produce a release receipt. The existing receipt belongs to deployed `8119cc05`; this foundation still requires a fresh full receipt before any Python release. No deployment is claimed by this report.

Final logs: `.omc/research/regional-foundation-web-results.json`, `regional-foundation-web-final-results.json`, `regional-foundation-region-tests-final.log`, `regional-foundation-python-final.log`, and `regional-foundation-python-scoped-final.json`. The foundation implementation is validated across the described runs; the larger bootstrap track remains in progress.
