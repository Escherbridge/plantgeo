---
type: validation-report
---
# Recurring source-cost implementation evidence

Starting checkpoint: `0cfb17a9`. The batch implements optional unresolved retry spacing and per-historical-day correction cadence/caps, a support/source-contract-bound year-sharded CAS scheduling index, production and memory bindings, and matching inactive-bootstrap fields. It does not change completeness proof semantics or admit custom sources.

The explicit water policy is seven days between successful full unresolved evaluations, 30 days between successful historical corrections, and a 16-call correction subcap within the existing shared 32-call forward budget. Forward work retains priority. These are operator freshness/cost choices, not guaranteed revisit times or claims of scientific sufficiency. Source-free repair remains eligible while acquisition waits.

Independent preliminary review found that a stored future timestamp could remain due forever despite successful retries. The fix refreshes trusted current time after conditional reads, heals invalid future entries and preserves legitimate newer concurrent checks. Selection also now stops at the first ineligible calendar date, making the declared correction block a genuinely contiguous eligible run; the regression uses the actual water strategy under the 16-call subcap. Independent frozen-source verification cleared the changes.

Correction to the preliminary selection rationale: the existing `select_affordable_days` already reserves all units needed for each day before fetching. A scattered second day beyond the cap would be deferred while the affordable first day could complete. No budget breach or starvation from that case was established. The contiguous rule is a policy/planning improvement, not a demonstrated overspending fix.

All edits were staged and frozen before the integrated sweep. The frontend runtime/package diff against `0cfb17a9` is empty. Data-boundary checks and the frontend filesystem-contract batch passed (19 files, 515 tests); this is scoped validation, not a new full frontend-suite claim. The first full Python sweep passed format and mypy, but found one type-only-import lint issue and two pytest failures (6,929 passed, 96 skipped, one expected failure). No receipt was issued.

The budget fixture confused request count with weighted cost: two two-cell requests cost four weighted calls. The correction tests caps zero/two/four, requiring no sends below full-day cost and successful progress at four. The bootstrap CLI failure remains unexplained: the exact retained TOML compiled and wrote all 12 artifacts successfully in one authorized offline diagnostic. No speculative runtime workaround was added; its assertion now preserves the sanitized error payload on recurrence. Diagnostic evidence: `.omc/research/bootstrap-write-diagnostic-20261103/diagnostic.json`. Corrected full-gate validation remains required.

The corrected full Python gate passed formatting, lint, mypy, and pytest. Receipt generated at `2026-11-03T08:16:07.946192Z`: `sha256:946497cc1be06d3f0464f4b76a19a036baaa3f1e5434d71bacbbfb3652f5b1e9`, covering 1,273 files. The previously intermittent CLI failure did not recur; its original cause remains unconfirmed. The wrapper suppresses passing pytest output, so no new individual test count is claimed.

Logs: `.omc/research/recurring-cost-python.log`, `.omc/research/recurring-cost-python-final.log`, `.omc/research/recurring-cost-boundary.log`, `.omc/research/recurring-cost-web-contracts.log`. No deployment has occurred for this batch. The retry index starts empty: initial successful evaluations establish scheduling evidence; this release does not imply an immediate stop to all historical provider calls.
