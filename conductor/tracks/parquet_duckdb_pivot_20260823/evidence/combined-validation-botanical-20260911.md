---
type: verification
---

# Post-botanical integrated verification — September 11, 2026

All sixteen local quality, archived-source and image gates passed for commit
`416342fa5bea836e6a062549b87c2706f53ce45e`, tree
`df12542ffa00937e0213d10c938e715849030492`. The run started at
`2026-09-12T01:45:11.166959+00:00` and finished at `2026-09-12T01:58:21.644616+00:00`
(UTC, September 11 in the local America/Denver timezone). The runner's final source guard
passed before it wrote the terminal result. No host gate was repeated in this batch.

This verifies the botanical intake `32a13c604e6da4331051e6a584f7942e7165af1e`, QA planning
`577adb20830bed1f6153aaec8f25c98a97bdc599`, and QA ledger `416342f` on the existing integrated
source. The botanical source was composed exactly once. The independent seven-document QA
review records 390 insertions, zero deletions and zero runtime changes. Its dated task ledger
is a snapshot, not a fresh task census.

| Check | Observed result |
| --- | --- |
| Frontend data boundary and TypeScript | Both passed |
| ESLint | 0 errors; **1,084 warnings** with the scope explained below |
| Full frontend Vitest | **2,260 passed, 13 skipped**; 145 passed files and two skipped files |
| Frontend Node tooling | **6 passed**, zero failures or skips |
| Full Python checker | Formatting, lint, mypy and pytest passed |
| Python JUnit | **6,383 passed, 149 ordinary skips, one expected failure**; 6,533 cases, zero failures/errors |
| Python receipt | Generated and verified against 1,380 governed input files |
| Archived-source receipt | Passed against the Git archive plus the newly generated receipt |
| Web, data-service and job-executor images | Each build, bounded smoke and image identity check passed |

The Python source digest is
`sha256:f4f846ee0999299813e895bc9f034807d80414e0aefa6be4f16393bde9bd6f93`.
The 149 ordinary Python skips retain their existing opt-in database/rehearsal, capability,
live-model and SQL-fixture conditions. The expected failure remains
`test_cli_is_a_thin_click_adapter`, covering 18 owner-contingent transaction boundaries.
The frontend skips are nine climate SQL-contract and four PostGIS checks. The runner removed
four optional database environment variables; these results do not certify those optional
integration cases. It used the current worktree's Python import root and did not synchronize
dependencies.

The host lint invocation included the first retained `image-context`: 542 warnings across 26
current-source files plus 542 matching warnings in their archived copies. Independent review
matched the source-relative locations, severities and normalized messages, and confirmed the
current diagnostics match the first candidate's canonical warning population. This measured
1,084-warning result is retained without adjustment. Vitest's root-anchored `src` inclusion and
the TypeScript compiler's resolved file list exclude both archived contexts; TypeScript had
570 root inputs and 2,582 resolved files, with zero `.omc` inputs. Before the next batch, only
the two completed, reproducible image-context directories may be relocated under the existing
`.tmp` exclusions. Archives, receipts and original command/log metadata remain in custody.
That scratch correction requires no source or lint-configuration edit.

| Local image | Immutable image ID |
| --- | --- |
| web | `e13b01f4aab6ac3a0cd4c3c6fd3205d4740c4451574046a28545a07ab7024e30` |
| data-service | `78a5fca94552262243679edd20b91dfde3dadcbcc86d7e395481298de92133a4` |
| job-executor | `191e9f2f7e6e2c4e16fee7ad0b83f31d686950ba9d31bd49ee1db82a8024c0e6` |

The web Dockerfile ran its normal embedded boundary, type, lint and test gates during the
image build. The web smoke was `node --check server.js`; Python image smokes were
`agri-service --help`. Smokes used `--network none`. These are bounded local syntax/CLI checks,
not deployed readiness, scheduler execution, provider calls or browser acceptance.

The [machine receipt](combined-validation-botanical-20260911.json) retains exact command
results, durations, image IDs and **43 compressed evidence artifacts** with original and gzip
SHA-256 hashes. The [scope review](validation-botanical-20260911/verification-scope-review.json.gz),
[final-gate review](validation-botanical-20260911/botanical-final-gates-check.json.gz), and
[QA intake review](validation-botanical-20260911/qa-planning-intake-review.json.gz) are retained
with it. The prior [first-candidate receipt](combined-validation-20260911.json) remains
unchanged at the `9b46e86` checkpoint, including its disclosed JUnit-path wrapper interruption
and reviewed continuation. It is not substituted for this second run.

The [119-file source baseline](shared-source-handoff-20260911.json) pins this tested commit.
It is **pre-intervention** and does not release shared weather ownership. The accepted
intervention `2fc6b30ac1b024e1c955dbf95552495608608a96` and newly supplied QA/PNW documentation
remain a later intake. After their review, the weather task receives a new exact canonical
base and owns the remaining shared weather implementation. Root will compose that reviewed
weather candidate before the next single combined runtime sweep. Preserve the canonical
Retry-After field, botanical registrations, exact April 28 selected-day gap regression,
independent fire clock, horizon-zero observations, and intervention MapView/router scope.
The retained old weather candidate's scoped checks do not become a full Python receipt here.

Production remains **HOLD**. This packet authorizes no image publication, deployment,
provider restart, production migration, source admission or remote data publication. The
four-taxon botanical WCVP release remains an unaccepted local candidate; production authoring
census, crosswalk/admission, environmental sufficiency, scientific recommendations, live-route
acceptance and existing successor-track gates remain open. Shrink closeout and legacy task
custody are separate documentation decisions and are not claimed complete by this receipt.
