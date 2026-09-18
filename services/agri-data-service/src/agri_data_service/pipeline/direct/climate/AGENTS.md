# Provider quota handling

A provider quota response and an exhausted per-turn request budget are typed provider deferrals.
The adapter records them in `unsettled_refusal`, so the forward driver reports `source_unsettled`
without consuming the generic publication retry series. Malformed responses, transport failures,
and publication failures keep their existing bounded error behavior.

A 429 is a PAUSE before it is a deferral. `source.fill_cell_day_cache` answers one with a cooldown
shared by every worker of the turn -- 20 s doubling to 160 s, at most `NASA_POWER_QUOTA_PAUSE_LIMIT`
(4) pauses per turn, never past the turn deadline -- then asks again; one burst of 429s across the
four concurrent workers is one pause, judged by the pause count each worker saw rather than by the
clock. Only a turn that has spent its pauses, or has no time left for one, sets `deferred_refusal`,
and from then on the shared turn cache stops queued requests before they start. Why: the production
turn reports of 2026-09-15/16 (`.omc/research/runbook-20260915-shortwave/prod-logs/`) show the
first 397-cell fan-out of a turn completing and the SECOND distinct day's fan-out meeting a 429 at
request ~330-360 -- and "a later turn" always selected a newer day and met the same 429, so
deferring on the first 429 stalled the shortwave product for 107 days. Each pause is reported on
stderr as `climate_forward_quota_pause`.

Already-running requests may finish and their successful responses are retained immediately.
Accounting charges started point requests (climate) or started chunk captures (soil), not queued
work skipped by the quota circuit or deadline. A paused request that is asked again is a second
started request and is charged again, so a quota-paused day may run at most
`NASA_POWER_POINT_CONCURRENCY x (NASA_POWER_QUOTA_PAUSE_LIMIT + 1)` requests past the pre-flight
`can_afford` bound; the bound is checked before a day starts, not mid-fan-out. Soil capture retains
the existing bounded internal transport/minute-quota retry policy, so its counter measures captures
rather than HTTP attempts.

Production forward commands persist verified, entirely non-fill responses across executor turns
for up to seven days through `pipeline/parquet/source_checkpoint.py`; see that directory's AGENTS.md
"Source response checkpoints" for original-byte receipts, support binding, CAS, and lifecycle bounds.
Responses with any fill parameter remain in-memory only, including recent meteorology whose solar
parameter is not settled. Resumption precedes budget checks and never bypasses provider quota.
Cross-turn cooldowns and expired-object cleanup remain unresolved operational work; the in-turn
pause above is the only quota handling there is, and `ingest/http.py::BoundedResponse` exposes no
`Retry-After`, so the pause series is fixed rather than read off POWER's answer.
