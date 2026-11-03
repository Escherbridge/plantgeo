---
type: architecture
---
# Separate source retry eligibility from data completeness

Status: inspected implementation proposal, not implemented. Prioritize this after the inactive bootstrap checkpoint and before expanding custom acquisition. The scheduled production evidence is in `../data_coverage_truth_20261102/scheduled-history-evidence.md`.

Current gap-fill prioritizes ordinary holes but falls back to all reachable unresolved days when no ordinary holes remain. Each fire can therefore revisit the oldest unresolved block. Publication receipt time does not record unchanged rechecks; source-response checkpoints are request caches and deliberately do not satisfy served-day recheck obligations.

Add an explicit configurable interval for proven fully answered unresolved days. Do not defer partial fetches, missing support, missing publication or ladder damage under this rule. Missing timestamps make work due. Preserve the strict coverage target and all incomplete/source-unresolved states.

Record successful full source evaluations in a separate support/source-contract-bound year-sharded index using existing conditional storage. Update it after successful publication or an unchanged full evaluation, never after failed, partial, unattempted or budget-deferred work. An unchanged response advances scheduling without rewriting Parquet to obtain a timestamp. Missing/corrupt/mismatched scheduling evidence makes work due and cannot grant completeness.

Select due unresolved days by oldest successful check, then calendar day, after genuine gaps. When all source debt is waiting, report the deferred count and next eligibility separately from `coverage_target.state=target_unmet`. Source-free ladder repairs still run, and forward-window acquisition/rechecks do not inherit historical deferral.

Corrections need an independent cadence and request cap, subordinate to shared provider limits and the turn deadline, with forward acquisition first. The current calendar-day modulo block rotation cannot merely be sampled every N days: common factors can starve blocks. Use oldest successful check or a persisted cursor advancing only through actually completed work. Preserve legacy behavior where the new policy is absent.

Required evidence: unchanged full unresolved responses advance retry timing without rewriting; subsequent fires within the interval make zero upstream calls; partial/failure remains due; changed support invalidates deferral; correction blocks rotate without starvation; correction caps preserve forward allocation; repairs progress while history waits. Interval values are explicit operator freshness/cost choices, not claims of scientific sufficiency. Do not mark the observed 101 changed-data rewrites unnecessary merely because writing dominated that turn.
