M5 deferred by owner 2026-09-28: shortwave stays in the legacy climate turn (and its repair binding stays) until its config lane replaces it.

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

# Shortwave measures the UTC edge before walking its backlog

`edge.py` gates legacy shortwave with three bounded time-series requests on the actual pinned
support: its first, middle and last ordered cells. Each request names `time-standard=UTC` and
only `ALLSKY_SFC_SW_DWN`, covers the census window (at most 400 days), and reuses the point parser
for every requested date. The response must echo UTC and the exact support centroid and include
every requested date. The newest valued day in the union of the three responses is a scheduling
ceiling only. No point sample establishes whole-support data, absence, or a publication receipt.

The original lag-6 inference was based on LST responses. The config-driven ingestion spec already
identified this mismatch in section 2.2 / FR-5. October 2, 2026 probes found UTC solar values only
through June 30 while the corresponding LST series reached September 27. The old newest-first
walk spent both 397-request fan-outs on September fill values every hour; a cross-turn cache of
refusals would still waste tens of thousands of calls establishing what three probes can measure.
Evidence: `.omc/research/receipt-investigation-20261001/source-edge/time-standard-comparison.json`.
The historical September edge readings below do not justify a UTC lag or a time-standard change.

Shortwave normally walks genuinely owed days at or below the measured edge oldest-first. If the
oldest gap has no probe value and no later published day, the turn first full-fetches the newest
probe-valued candidate. Only that candidate's successful four-rung publication can supply the
anchor needed to settle older all-fill days in later turns. Without this bootstrap, an empty
bucket with June 1 and 2 all-fill but June 3-30 valued would re-fetch the two null days every turn
and never establish the later publication needed for an absence. The probe itself supplies no
absence evidence, and the two old days remain unmarked until each full-support fetch completes.
Completed partial-day and absence rechecks retain their existing rotation behind real gaps. Dates
above the edge stay owed and are counted in `probe_gated_days`; `backlog_days` still counts the
entire census. A new process resumes from the completed partition census, so a newly arriving
calendar day cannot restart a refused frontier. The edge is re-probed each hourly turn, including
after UTC midnight; the next real upstream release can become eligible without a restart.
This is progress within the moving, at-most-400-day census only. Days older than its floor fall
outside this legacy operator; a drained visible census is not proof of whole-history completion.

The default request allowance is 797 when selecting shortwave or all products: the existing
794 fan-out allowance plus three explicitly charged probes. Other product selections keep their
previous cap. Probe requests count in both `requests_spent` and `probe_requests_spent`, have the
existing byte/transport bounds, and share the turn deadline. A 429 stops the probe immediately,
sets the existing turn-wide quota circuit and reports `probe_status=deferred`. A malformed body,
wrong point, missing day or LST response reports `invalid`; transport/HTTP failure or exhausted
time/budget reports `unavailable`. None authorizes fan-out. A valid all-fill probe reports `ok`
with no edge and likewise starts no fan-out. The report distinguishes these from publication
and from an idempotent no-op; the top-level legacy outcome stays `source_unsettled` unless a
request/time budget explicitly stopped it.

Full-support fetches still use the existing lane-day lock, four-rung finalizer and availability
extension. An interior all-fill day still needs a later fully published day for an absence proof;
the probe never supplies that proof. An old measured UTC edge cannot make the availability index
fresh under a lag-6 serving policy. This repair prevents waste and drains eligible history; it
does not claim that current UTC solar data exists or relax the serving freshness contract.

Each product retries its owed availability ledger before selecting source days, including when
the probe subsequently gates every source request. Generic climate writers are inactive, and a
completed day is absent from the owed source census, so only this explicit retry can recover a
terminal day whose availability extension failed previously.

The three-point, 400-day probe is a bounded interim repair for the retained legacy lattice,
not the config runner's S19 2-point/14-day weighted-provider probe policy. Retire it with this
legacy writer when the config shortwave successor completes its cutover; do not add a second
scheduler or operational frontier store. `tests/direct/climate/test_edge.py` covers a roughly
90-day source/calendar mismatch, fresh runner caches and moving days, four-rung oldest-first
progress, a valued-day bootstrap before two leading all-fill gaps, source refusal distinctions,
strict UTC, point/date identity, and no manufactured absence.

# Requested-parameter parsing

`source.parse_climate_point_body` verifies the exact support point, requested date and every
requested parameter. Ordinary full-support fetches use the default eleven-parameter contract.
The shortwave edge probe passes its one requested solar parameter, so every date is checked
against the actual smaller request. The frozen eight-parameter capture from September 2, 2026
also names its historical request subset in tests because it predates the soil-wetness parameters.
Neither caller may omit a parameter that its corresponding request actually asked POWER to return.

# An unsettled frontier is stepped past, not retaken

This remains the meteorology fallback walk. The account below records the pre-probe shortwave
failure and proposed recovery; the measured-edge implementation above supersedes that proposal.

`forward._publish_product` walks the owed backlog newest first and lets `--max-days` (1) count the
days that TOOK A SLOT. A day the source refuses as `source_unsettled` -- every support cell a fill,
no later day published to mirror against -- does not take one: the walk records it in the product
report's `unsettled_frontier_days`, steps to the next older owed day, and fetches that under the
same `can_afford` and deadline checks. `CLIMATE_UNSETTLED_FRONTIER_SKIPS` (1) bounds the steps per
turn; a second unsettled day in a row spends the slot and the turn reads `source_unsettled`.

Why the walk and not the lag. The refusal at the frontier is the safety design and is correct: an
all-fill newest day has nothing to mirror against, so governing it absent would manufacture a
permanent claim about a day POWER simply has not reached. But with newest-first selection and one
slot per turn, that correct refusal selected the SAME frontier day every hour and the days beneath
it never drained. Measured on the first post-deploy turn of `c150250d` (2026-09-18 13:40Z):
shortwave radiation, `settled_through` 2026-09-12, `scan_first_day` 2026-06-01, `backlog_days` 101,
the one selected day `source_unsettled` ("POWER answered for all 397 support cells and every
`ALLSKY_SFC_SW_DWN` value was a fill value"), `requests_spent` 397 of 794 with the ten lag-5
siblings `idempotent_noop`. The lane is `withheld_reason: availability_stale` and so outside
autonomous repair; the hourly walk is its only drain, and it was standing still on a correct answer.

What ONE skip covers, and what it does not. With the edge at most lag+1 days back the turn asks F
(unsettled, skipped) then F-1 (written): one day drained per turn, the same rate a settled frontier
drains it, and F is re-asked next turn. At edge >= lag+2 the skip does NOT drain: F and F-1 are
both unsettled, F-1 spends the slot, nothing is written, and the next hour re-asks the same two
days -- at 794 requests per hour instead of 397. That residual is structural, not a matter of the
constant: the 794 budget is exactly two 397-cell fan-outs, so `can_afford` refuses a third fan-out
whatever `CLIMATE_UNSETTLED_FRONTIER_SKIPS` says. The cure for deeper jitter is a CROSS-TURN skip:
persist the refused frontier (a checkpoint keyed by product and day) so the next turn starts one
day deeper, or walk oldest-first on the turn after a refused frontier. That is the named follow-up;
it is not implemented here. The second fan-out also fits the 794 budget only when the ten siblings
were no-ops; on the daily ceiling-advance turn they are not, `can_afford` refuses the step, and the
turn reports `request_budget_exhausted` for the older day beside the frontier's `source_unsettled`
-- the honest word, and `unsettled_frontier_days` still names the frontier. So while the frontier
is unsettled, one of every 24 hourly turns drains nothing.

An idle product can pay one redundant fan-out. With no owed days the backlog is the recheck list
(oldest-first, rotated by the clock hour). A recheck refused unsettled -- a newest governed absence
with nothing later published -- is skipped to the NEXT recheck in the same turn, and the rotation
puts that same recheck first next hour, so it is visited twice in a row. Bounded to one extra
397-cell fan-out; accepted rather than special-cased.

A 429 deferral is NOT a frontier. It reports the same `source_unsettled` word, but `cache.deferred_refusal`
is set and every queued request is stopped before it starts; `_steps_past_unsettled_frontier` reads
that flag and takes no skip, so the walk never re-asks a provider that just throttled it.

`_product_outcome` names the turn by the first day AFTER a stepped-past frontier: a written older
day is `published`, a budget stop is the budget's word, and only a turn whose every day was
unsettled reads `source_unsettled`. `source_unsettled_days` still counts the frontier.

# Historical solar edge measurement record (LST; not UTC evidence)

`CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS` is 6 (`products.py`, measured 2026-09-15; see
`pipeline/direct/AGENTS.md`, "Lags and floors, both measured"). The edge is not a constant:

- 2026-09-15: `ALLSKY_SFC_SW_DWN` real through 2026-09-11 at five PNW points -- a 4-day solar edge
  (`products.SHORTWAVE_LAG_MEASUREMENT_EVIDENCE`).
- 2026-09-18 13:40Z: the lag-6 settled day 2026-09-12 answered all-fill at every one of the 397
  support cells -- the edge was at least 7 days back that hour.
- 2026-09-18T14:03Z, three PNW cells (-122.3/47.6, -116.2/43.6, -120.5/45.0): `ALLSKY_SFC_SW_DWN`
  last real day 2026-09-13 at all three, fill only 09-14..09-17; `T2M` last real 09-15. So
  2026-09-12 -- all-fill 23 minutes earlier -- was REAL by then: the edge moved from >=7 d back to
  5 d back within the hour. The 13:40Z refusal was a transient at POWER's daily publication
  boundary, and the 14:40Z turn writes 09-12 with or without the skip.

Edge jitter observed: 4 d (09-15) -> >=7 d (09-18 13:40Z) -> 5 d (09-18 14:03Z). The edge advances
in bursts around a daily publication boundary, so a lag pinned to one reading is unsettled at the
frontier for part of most days; that is what the one-turn frontier skip above absorbs, up to lag+1.
The lag stays at the measured 6 -- raising it to the worst reading would hold every settled day
back by the jitter, whereas the skip costs nothing while the frontier is settled. Re-measure before
moving it, and add the reading here.
