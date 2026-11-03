---
type: verification-evidence
---
# Data-fix release evidence

Starting commit: `23e94c80b84be6b861d4e8ff9fec415953e41dc6`.

## Scope

This release adds opt-in declared historical coverage targets to supported config-driven daily archive lanes and enables the policy for water gauges. Historical acquisition idles when the target is reached, while scheduled coverage checks, source-free publication repair, forward capture and recent revision windows remain operational. Missing coverage can resume acquisition. It does not retrofit legacy writers or eliminate object-store census cost.

The frontend changes distinguish request failure, partial/truncated data, published empty results and missing publication. Satellite vegetation no longer borrows measured-vegetation availability or served dates. Existing historical weather remains accessible.

Catalyst research and its bounded public-data pilot are a separate track. This release introduces no energy ingestion or new upstream workload.

## Review evidence

The requested dev-drive skill ran exactly two cycles with independently reviewed branches:

| Cycle | Lens | Stance | Disposition |
| --- | --- | --- | --- |
| 1 | Performance | Discriminating | Accepted publication-repair early-return finding. |
| 1 | Correctness | Enthusiastic | Independently identified the same finding. |
| 2 | Architecture | Discriminating | No further actionable findings; inspected the repair. |
| 2 | Documentation/contracts | Discriminating | No actionable findings. |

The accepted fix repairs already-fetched publication debt even when a fractional target has been met, without requesting upstream history. Regression cases cover successful repair and remaining repair debt.

## Validation status

The initial integrated sweep completed. Frontend boundary and full lint passed (542 warnings), and the full test command passed its tooling tests plus 247 Vitest files / 3,541 tests. Type checking identified a test mock typed as a different query result; the one-line annotation correction passed the subsequent full type check.

The first Python sweep passed formatting and mypy, but failed lint and pytest: 6,753 passed, 29 failed, 96 skipped and one expected failure. Twenty-seven failures came from the local PostgreSQL PROJ database being selected by process environment overrides. The other two were a new test expecting an exit-code return where the helper raises a configuration error, and an existing narrowed-window fixture retaining the new full-history target. The fixtures were corrected without weakening their assertions; lint findings were fixed together. No receipt was issued for the failed sweep.

The corrected full Python sweep passed formatting, lint, mypy and pytest with PROJ overrides removed for that process only. At `2026-11-03T04:49:30.808104Z` it wrote `QUALITY_RECEIPT.json` for 1,251 files with tree digest `sha256:17581392da1f508d542b91f50be7c9f10eda88fccee58ecfa622d66710348144`. No checks were waived. The wrapper records pass status rather than retaining passing pytest stdout; no final test-count claim is inferred from the prior failed run.

The later live UI evidence justified a meter-to-kilometer display correction and explicit satellite-versus-measured timeline notices. Independent targeted source verification found no blocking issues. Full type checks passed; focused distance/manager tests passed 95 tests, and satellite row/panel tests passed 51 tests. Focused lint passed (the first satellite lint invocation had a wrong file path; the corrected invocation passed). These follow-ups are scoped validation, not another claim of a full frontend suite on the final tree.

The data-fix commit `8119cc0568cde736d731882505846e7e7ffde47a` was pushed independently to `main`. Railway web deployment `1caf6ae3-c220-4b57-ac41-5111a0db6138` subsequently passed its full boundary/type/lint/test build stage on that exact committed snapshot: 247 test files and 3,545 tests passed at approximately `2026-11-03T04:59:48Z`. The separate Catalyst research commit is `267d6403` on `research/catalyst-agent-validation` and introduces no runtime energy lane.

Dependency audit findings surfaced during the build remain existing release risk; see [the scoped dependency assessment](dependency-risk.md). Functional gate success is not security clearance.

## Production verification

All four triggered services reached `SUCCESS` at commit `8119cc0568cde736d731882505846e7e7ffde47a`: web `1caf6ae3-c220-4b57-ac41-5111a0db6138`, executor `a5010e4d-ded0-43f1-8646-1f07201f453d`, Parquet API `b172eb97-1da3-4052-87d6-09049b395315`, and Martin `32c2fd3e-3b33-4923-b62a-4ae6d6fa1109`. Final environment inspection found all 10 services online, no pending rollout and no recent failures. Existing ML/PostgreSQL warning notifications remain unrelated, unresolved observations.

Read-only public probes after rollout returned HTTP 200 and readiness checks true. Current and January 2018 weather returned their selected days. October 31 water returned 42 rows and January 2018 water returned 39 rows in the bounded Portland-area viewport. Vegetation correctly reported October 26 as its served day for a November 3 request. Climate inside the region returned four cells; the outside-region query now returned `published / empty_viewport`, zero cells, preserving its October 29 publication day.

Post-release Playwright assertions passed for weather distance (38.9 km), January 2018 weather, the October 22 missing day, satellite timeline scope, unknown satellite acquisition date and removal of the unsupported gap-free claim. No uncaught page errors were recorded. Screenshots were visually inspected. This remains a bounded desktop pass, not full mobile/authenticated/offline acceptance.

The live executor inventory confirms both `water-gauges-daily` and `water-gauges-daily:gap-fill` active, with forward at `20 13 * * *` and history at `50 */6 * * *`. Forward work completed after restart. No historical run was forced for verification: automatic stop/resume is test-proven, and a subsequent scheduled report is still required to demonstrate its production coverage state.

Local detailed evidence: `.omc/research/post-release-probe.json`, `post-release-ui.json`, `post-release-weather.png`, `post-release-satellite.png`.

## Remaining work

- Broader US regional bootstrap and legacy-writer coverage adoption remain follow-up implementation, not delivered capabilities of this slice.
- Scheduled production coverage-target evidence, dependency remediation and broader UI/agent acceptance remain open.
