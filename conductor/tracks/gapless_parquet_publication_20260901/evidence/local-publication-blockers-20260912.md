---
type: track-evidence
track: gapless_parquet_publication_20260901
artifact: local-publication-blockers
audited_on: 2026-09-12
status: blocked_on_ownership_and_runtime_evidence
source_commit: 6c08907ba392d8dcdff3e96c41fdb27c6a74fe9d
source_tree: 92f3baef253389b737f3e36fd47a96b52c43c312
---

# Local gapless-publication blocker receipt

This continuation re-read the current plan, September 12 ownership audit,
retirement boundary and root task ledger against the source tree above. The
publication/executor implementation paths inspected by the prior audit have not
changed since base `8c14ea0117ba14d5584f737b793f989566102514`.
No Railway, production object storage, PostgreSQL, `pgt`, writer, scheduler,
publication pointer or data load was accessed or controlled.

## Historical-gap ownership is not complete

The tree proves bounded scheduled scopes and conflict rules; it does not prove
that all requested history has an acquisition owner. The current direct windows
remain bounded: climate, soil and vegetation look back at most 400 days; drought
uses 60 settled release weeks; fire detections use five settled days; sensors use
at most seven buckets; water, weather and snapshot-style geometry lanes poll
current source state. A registered generic `parquet-*` gap-fill command can repair
known retained inputs, but it does not acquire missing upstream history.

The following facts remain unmeasured or unassigned:

- exact required floors and already-satisfied intervals for the older
  soil-wetness, precipitation, dew-point, drought and burn-severity requests;
- one current source-direct or preserved-Parquet recovery owner for every still
  required interval outside the bounded forward windows;
- receipt-backed governed absences for source-unavailable intervals; and
- a current all-rung/availability reconciliation after any authorized repair.

The dated September 2 lane identities and old database archive commands are
historical evidence, not authority to resume them. The current retirement plan
must govern any future operational work.

## Executor cutoff, lease and recovery remain runtime gates

The tree and inspected unit definitions describe retry/backoff, restart catch-up,
failed-checkpoint release, breaker/replay holds, expired-lease reclamation and
stale-worker fencing. They do not establish the effective production state. The
following packet is still required:

1. exact deployed executor definition and command identities;
2. active/required lane settings and the effective environmental cutoff;
3. current lease and checkpoint readback proving no overlapping writer;
4. one transient failure followed by retry and terminal publication, with exact
   run/work-item/attempt/output identities;
5. one process restart showing retained cursor/definition and bounded catch-up;
6. one expired-lease reclaim showing the stale worker fenced and the new attempt's
   outcome; and
7. at least three consecutive scheduled advances for every activated lane,
   followed by rung, receipt, coverage and availability reconciliation.

The September 10 pause receipt was `configured_pending_deployment`; a later
release identity alone does not prove this cutoff or lease state. Older incident
receipts likewise do not authorize a new supersession or substitute for a current
recovery observation.

## Verdict

No P3 or P4 operational gate can close from this continuation. The current plan's
open ownership, cutoff/lease, retry/restart/expired-lease, three-advance and final
handoff items remain accurate. This is a blocker receipt only: it records no new
publication, repair, absence, deployment, activation, recovery or passing test.
