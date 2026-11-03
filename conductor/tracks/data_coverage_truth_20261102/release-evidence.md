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

## Acceptance still required

- Separate data-fix release and post-deployment serving/availability probes.
- Post-deployment rendered verification of the changed notices and distance units. The bounded pre-release Playwright desktop pass is recorded in `live-serving-audit.md`.
- Broader US regional bootstrap and legacy-writer coverage adoption remain follow-up implementation, not delivered capabilities of this slice.
