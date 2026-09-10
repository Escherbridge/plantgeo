# Provider quota handling

A provider quota response and an exhausted per-turn request budget are typed provider deferrals.
The adapter records them in `unsettled_refusal`, so the forward driver reports `source_unsettled`
without consuming the generic publication retry series. Malformed responses, transport failures,
and publication failures keep their existing bounded error behavior.

Once a quota refusal is received, the shared turn cache stops queued requests before they start.
Already-running requests may finish and their successful responses are retained immediately.
Accounting charges started point requests (climate) or started chunk captures (soil), not queued
work skipped by the quota circuit or deadline. Soil capture retains the existing bounded internal
transport/minute-quota retry policy, so its counter measures captures rather than HTTP attempts.

Production forward commands persist verified, entirely non-fill responses across executor turns
for up to seven days through `pipeline/parquet/source_checkpoint.py`; see that directory's AGENTS.md
"Source response checkpoints" for original-byte receipts, support binding, CAS, and lifecycle bounds.
Responses with any fill parameter remain in-memory only, including recent meteorology whose solar
parameter is not settled. Resumption precedes budget checks and never bypasses provider quota.
Cross-turn cooldowns and expired-object cleanup remain unresolved operational work.
