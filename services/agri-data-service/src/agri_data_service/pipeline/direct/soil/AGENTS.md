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

Production forward commands persist verified, entirely non-null chunks across executor turns for
up to seven days through `pipeline/parquet/source_checkpoint.py`; see that directory's AGENTS.md
"Source response checkpoints" for canonical-byte receipts, full support/chunk binding, CAS, and
lifecycle bounds. Chunks with any null value remain in-memory only, including coastal/ocean chunks
with a stable null mask. Resumption precedes budget checks and never bypasses provider quota.
Cross-turn cooldowns and expired-object cleanup remain unresolved operational work.

# Candidate publication edge and unsettled frontier

Open-Meteo documents ERA5-Land with a five-day publication delay. The soil product table uses that
delay to calculate the newest candidate day; it is not a measured guarantee that the mirror has
completed that day. The older nine-day value captured one production observation and must not be
used as a permanent source ceiling.

An archive response containing nulls for every support cell at the candidate edge is not a governed
absence because no later published day proves the mirror has moved past it. The adapter returns
`source_unsettled`. The forward walk may step backward through four such frontier days without
charging them against `--max-days`, then try the next older owed day. Four is the product/source
lookback because it covers the measured 2026-08-11 redistributor shape: a nine-day actual edge
beneath the documented five-day candidate. The request budget therefore covers `max_days + 4`
complete chunk fan-outs. This remains a hard bound: the fifth unsettled day spends the ordinary day
slot and ends the walk. The writer never probes a day newer than the documented candidate ceiling.

A provider quota deferral also reports `source_unsettled`, but it sets
`SoilSourceCache.deferred_refusal`. That circuit is the distinction: a throttled turn never steps to
an older day and re-asks a provider that has already refused it. Reports expose both
`source_unsettled_days` and `unsettled_frontier_days`, and the product outcome names what materially
happened (`published`, the stopping budget, or `source_unsettled`) rather than treating every
non-empty backlog as a publication.
