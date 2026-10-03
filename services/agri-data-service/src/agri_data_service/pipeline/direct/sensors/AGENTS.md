# Sensors direct writer

This package owns the independently scheduled NOAA NWS station-observation producer. `watermark.py`
reads the day plan and per-station frontiers from z13; `source.py` asks NWS only what that plan owes,
under a hard per-turn budget; `progress.py` carries a due day's partial sweep across turns;
`rows.py` conforms station observations, `adapter.py` performs the
durable station-day merge, and `forward.py` owns the turn, parser, and `WRITER_CONTRACT`.
`__main__.py` is the supported module entrypoint.

There is no separate products/support module because this is one stream and the shared behavior is
small. `--max-days` bounds the calendar buckets one turn publishes, not a historical backlog.
Ordinary boundary failures should use the shared pipeline operational error; a specialized type is
reserved for a tested semantic branch rather than a lane-specific wrapper name.

## Ask once per day, after the day has ended

**Evidence (production, 2026-10-03, `agri-service ops jobs-usage-report --days 1 --by lane`):**
`sensors-direct-forward` made 14,400 requests a day to api.weather.gov (600 per hourly turn) and
pulled 4.39 GB (about 183 MB a turn). The executor log for each turn showed the same shape:
`stations_polled=582 stations_watermarked=473 stations_unavailable=106`, all seven window days
re-written every hour with `rows_added=0`. Two causes, both measured live on KBOI the same day:

- **Stations that never published asked for six days every hour.** The `83a41dff` watermark gave a
  station with no published report the whole six-day window. A 5-minute ASOS station's six days is
  500 features and 1.9 MB (NWS pages at 500); one day is 312 features and 1.2 MB. Both are past
  `OBSERVATION_BOUNDS`' 1 MiB cap, so the request raised, the station counted as unavailable, it
  never published, and so it never got a watermark. That loop cost about 106 MiB a turn and kept
  KBOI-class stations out of the layer entirely.
- **Watermarked stations still downloaded every report in their window** to keep one per day. A
  4-hour KBOI window is 40 features and 155 KB.

The hourly schedule multiplied both by 24, and none of it could change the product: the lane
publishes one report per station-day, the day's newest (`rows.py`).

**The rule.**

- `watermark.py::read_sensors_day_plan` sorts each window day from z13 listings and markers alone.
  A day is **done** once a write of it completed at least `SENSORS_LATE_REPORT_ALLOWANCE` (3 h) after
  the day ended. A day is **due** once it has ended, the allowance has passed, and it is not done
  (missing, incomplete, absent, a bad marker, or written before the allowance). A day with parts AND
  an absence marker is **blocked**: the adapter refuses it, so asking NWS would only burn requests.
  Today and a day inside its allowance are **waiting**.
- **No due day means no NWS request at all**, not even the roster. The turn exits 0 with
  `outcome=idempotent_noop`. In steady state that is 23 of the 24 hourly turns.
- **The sweep.** Each due day, oldest first, every roster station gets one request:
  `start=<day start>&end=<day end>&limit=1`. NWS answers newest-first, so that one feature is the
  winner `rows.py` would pick. If this lane already holds a report for that station-day, `start` is
  that report (inclusive), so a re-asked day still returns a row and the re-write marks it done.
- **The gap walk.** A roster station this lane holds nothing for after some earlier day (new to the
  roster, its request failed, or it never fit under the cap) walks backwards over the done days
  before the earliest due day: newest report in `[gap start, end)`, then the same span ending at that
  report's day, until NWS answers empty. One request per day that has data, plus one. Its reports
  land only on done days, and a done day is re-written only when the merge changes it
  (`forward.py::_unchanged_on_z13`).
- **The budget is hard.** `source.py::SensorsFetchBudget` caps observation requests (2,400, and never
  more than `--max-records`), observation bytes (32 MiB), and wall clock (half of
  `--time-budget-seconds`). It is checked before every request. Each request is also capped at
  64 KiB (`SENSORS_OBSERVATION_BOUNDS`), so an NWS that ignored `limit` would be refused per request
  and stopped by the byte budget. **A due day is published only when every station was asked for
  it.** A day the budget cut short is reported as `request_budget_exhausted` (or
  `time_budget_exhausted`) under `unwritten`, is not written, and stays due. Its progress is saved
  (next section), so a partial sweep is never marked done and never thrown away.
- **`end` is exclusive.** `start=<day start>&end=<next midnight>` never returns the next day's
  `00:00:00` report. Probed live on KBOI 2026-10-03: `end=14:45:00` answered `14:40`, `end=14:45:01`
  answered `14:45`, and the 10-02 day query answered `23:55` while a `10-03T00:00:00` report existed.
  So no `end - 1 s` adjustment is needed; the fake in `test_sensors_direct_watermark.py` models the
  same boundary and the steady-day test pins it.
- **`--max-days` caps the sweep, oldest first.** A turn sweeps at most `--max-days` due days, oldest
  first, because the oldest due day is the next to age out of NWS retention. A day past the cap is
  `unwritten` as `request_budget_exhausted` (the cap bounds the turn's quota in whole days, one
  request per roster station; the shared vocabulary has no day-cap word) with a detail naming
  `--max-days`, and costs no request. Publication then takes swept due days first, then gap-walked
  done days, each oldest first.
- **No due day vanishes from the report.** A fully swept day NWS had nothing writable for is
  `unwritten` as `no_writable_observations`; it is not a write failure, so on its own it exits 0.

## A sweep resumes across turns

**Why (review 2026-10-03).** The fetch share is half of the 300 s budget, 150 s. One day is ~582
requests at 8 concurrent, so if NWS averages about 2 s a request no turn finishes a day. Before this,
each turn re-asked the same first stations, discarded them, published nothing, exited 1 and fed the
breaker, while the day aged out of the six-day retention.

**The rule.** `progress.py::SweepProgressStore` keeps one scratch object per due day at
`source-sweep-progress/v1/sensors/day=<day>.json` (under the store prefix, outside every `layer=`
prefix, like `source-response-checkpoints/v1/`, so no coverage listing sees it). It holds each asked
station's answer (its winning report, or an empty list for "no report"), the stations whose request
raised (mapped to how many attempts have been spent, since 2026-10-03), the roster digest
(`roster_sha256`, sorted station ids), and a checksum envelope.

- A due day resumes from its scratch and asks only the stations it lacks, so a day costs exactly one
  request per roster station however many turns it takes.
- **A station's own failures are retried, bounded, within the day's own sweep.** A station whose
  request raises is re-asked on a later turn -- not left to the gap walk, which only walks days
  BEFORE the due window and only FORWARD from a station's newest held day (see "What this does not
  cover" below), so it could never recover a station that fails on one due day and succeeds on the
  next. `progress.SENSORS_STATION_ATTEMPT_CAP` (3) bounds the retries: once a station has raised that
  many times for one day, the day sweeps around it and publishes without it rather than owing the day
  forever. A day is "fully swept" when every roster station has either answered or exhausted its cap.
- **Roster identity.** Scratch from a different roster is deleted and the day is swept afresh
  (`progress_discarded: roster_changed`): a day is published from one roster only, never a mix.
  Corrupt scratch is deleted too (`unreadable`) and costs only a re-sweep. A roster shrunk by a
  TRANSIENT state-roster-page failure (`ingest/sensors.py::StationRoster.degraded`) is not trusted as
  a real roster change: the day sweeps ephemerally against the smaller roster for this turn alone, and
  the real saved scratch is neither cleared nor overwritten, so the outage costs this turn's progress
  once rather than the day's whole history twice (`SweepProgressStore.resume(allow_roster_discard=)`).
- **Publish.** A fully swept day with reports is saved before publish, so a failed publish retries on
  the next turn with zero NWS requests; `forward.py` deletes the scratch once the day is `written`. A
  fully swept day with nothing writable deletes its scratch, so the next turn asks again (the outage
  case below is unchanged).
- **Exit.** A turn that advanced and saved a sweep it could not finish reports `sweep_in_progress`
  (day, stations asked, stations remaining) on the fetch and complete records, lists the day under
  `unwritten` with its budget word, and exits 0 even if no day was written. Turn outcome stays
  `incomplete`, the shared word for "a day did not settle" -- `sweep_in_progress` is a field, not a
  new word, because `pipeline/direct/__init__.py` owns the vocabulary and
  `test_no_writer_emits_an_outcome_it_did_not_declare` forbids an undeclared one. The executor treats
  exit 0 + `incomplete` as `lane_incomplete` (a warning), never the breaker.
- **Housekeeping.** Every fetching turn prunes scratch for days no longer due (published, blocked, or
  aged out). A no-op turn touches neither NWS nor the scratch.
- **Not covered.** A process killed mid-sweep (executor timeout) loses that turn's unsaved answers:
  progress is saved once per day, after its slice of the sweep returns. The fetch share leaves half
  the budget, so a clean stop is the normal case. Scratch writes are best effort: a failed save only
  costs a re-ask.

**Expected cost.** Steady state: one fetching turn a day (the first turn at or after 03:00 UTC, so
03:20 on the `:20` schedule), about 18 roster pages plus 582 station requests (about 600 requests, a
few MB of roster pages plus about 3 MB of observations), and 23 no-op turns. That is about 600
requests and 10-13 MB a day, down from 14,400 and 4.39 GB. The first fetching turn after this change
also gap-walks the ~106 never-published stations once (about five requests each). A catch-up after an
N-day outage costs N x 582 requests, at most 2,400 a turn, and finishes on the next hourly turns. If
NWS is slow enough that a day spans several turns, the cost is unchanged (resumed sweeps never
re-ask) and only the publish moves later: yesterday lands on the first turn that finishes its sweep.

**What this does not cover.**

- **Today is no longer published intraday.** The newest published day is yesterday from about 03:20
  UTC (later by one hour per extra turn a slow sweep needs), which matches the registered
  `publication_lag_days=1`. Before then the newest day is today - 2.
- **No re-check after the allowance.** A report that reaches NWS more than about 3 h 20 min after
  its day ended, or a correction NWS applies to an already-published winner after that, is not
  chased. The old 12 h settle overscan was dropped. Its second reason (a non-UTC `observedAt`) does
  not hold for NWS: every timestamp measured is `+00:00`.
- **A due day NWS answers empty for every station** stays due and is re-asked every hour until it
  leaves the window. This writer never manufactures an absence. It is the outage case and is visible
  as `days_swept` with `days_selected` empty.
- **The gap walk only looks forward from a station's newest held day.** A station missing from an
  older done day, while it holds a newer one, is not re-walked.
- **Roster pages are not inside the byte budget.** They are bounded by `STATION_PAGE_BOUNDS` (8 MiB
  a page) and `MAX_STATION_PAGES_PER_STATE`, and are now fetched only on fetching turns.
- **The marker is trusted as "fetched after this instant".** It is written by the run that just
  fetched, because this forward is the only live z13 writer for `layer=sensors/kind=observed`. A
  future repair writer must not stamp a later `completed_at` over content it did not re-fetch.

The bucket is opened before NWS is called (`forward.py::run`). A run with no object-store
credentials therefore fails without spending any upstream requests.
