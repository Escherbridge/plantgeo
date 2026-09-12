# Provider pacing and durable deferral

The NASA source permits four concurrent requests and spaces primary starts by 0.5 seconds across
every product and day in one turn. This is an operator policy adopted 2026-09-11 after the 02:45 UTC
turn reported 761 of 794 budgeted requests and a solar HTTP 429, not a NASA per-second quota. NASA's
[official API guidance](https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/)
warns about excessive concurrency without guaranteeing a per-second allowance. The configured
75-day solar lag remains unchanged.

The cache gate checks the monotonic deadline and latched refusal before and after waiting and
charges only an allowed start. NASA selects one transport attempt and disables redirects on its
dedicated client, so each charged start makes at most one HTTP request. Other bounded-HTTP callers
retain existing retries and redirect behavior. The remaining-time timeout also bounds an active
NASA request. Already-running requests may finish; good responses are retained immediately.
Queued requests never start after a known provider refusal.

## Outcomes and scheduler behavior

429 is `provider_rate_limited`; 401/403 is `provider_access_denied`; the local request cap is
`request_budget_exhausted`; the wall clock is `time_budget_exhausted`; an unproven all-fill day
remains `source_unsettled`. The adapter retains these separately across generic gap-fill exception
handling. Provider refusals never enter publication retry sleep or author governed absences.

Products report written/governed-absence/deferred selected-day counts. An unfinished selected day
makes the product `incomplete`; backlog alone cannot make it `published`. The run then reports
`status=partial` and an incomplete-product count. Ordinary provider/budget deferral returns zero,
preserving hourly executor turns without creating held lanes. Genuine publication or storage
errors still fail. No scheduler or database controls are changed.

## Persisted Retry-After

`cooldown.py` owns `source-provider-cooldowns/v1/nasa-power.json`, outside serving and availability
namespaces. A fresh cache reads at most one 4-KiB constraint before starting missing source calls.
Missing state permits access; unreadable, malformed, oversized, checksum-invalid or invalidly dated
state fails closed. Expired constraints remain stored to avoid deletion races.

The HTTP helper retains only Retry-After, capped at 128 characters. Valid seconds are anchored at
actual response receipt UTC in production (`now=None`); HTTP dates require an aware instant.
Invalid, naive, missing or unrepresentable values invent no deadline. Any 429 still stops the
current turn; absent a valid hint, the next ordinary turn may retry.

The source latches refusal before saving a valid hint. At most three read/CAS attempts retain the
later of that hint and any concurrent constraint. Shorter hints cannot reduce the pause, and the
retained diagnostic reports the effective maximum while preserving an earlier access-denial
classification. Future turns make no new requests before the stored instant. A turn started inside
the pause conservatively stays paused until the next turn.

Cooldown read/CAS failure raises `ClimateCooldownError` through gap-fill and fails the command
without further provider calls for that request. A failed save cannot promise a durable pause;
the explicit operational failure leaves the response to existing executor/operator policy.

Complete cached days remain publishable because they require no provider call. Cross-turn response
checkpoints still require all eleven parameters to be non-fill and expire after seven days.
Recent meteorology with solar fill remains in-memory only. Checkpoint eligibility was not relaxed.

## Focused verification

Tests cover paced starts across days, deadline/refusal during waits, distinct 403/429 outcomes,
one-attempt/no-redirect accounting, bounded Retry-After parsing, early subsequent turns making no
calls, monotonic concurrent CAS, read/malformed/write failures, positive-response reuse, truthful
zero-write/mixed summaries and ordinary partial-run exit behavior. Their execution belongs to the
single integrated batch verification pass after authoring.
