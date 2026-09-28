# Sensors direct writer

This package owns the independently scheduled NOAA NWS station-observation producer. `source.py`
polls the bounded rolling provider window, narrowed per station by `watermark.py` (below);
`rows.py` conforms station observations, `adapter.py` performs the durable station-day merge, and
`forward.py` owns the turn, parser, and `WRITER_CONTRACT`. `__main__.py` is the supported module
entrypoint.

There is no separate products/support module because this is one stream and the shared behavior is
small. `--max-days` bounds the calendar buckets returned by one poll, not a historical backlog.
Ordinary boundary failures should use the shared pipeline operational error; a specialized type is
reserved for a tested semantic branch rather than a lane-specific wrapper name.

## Fetch only what can still change a published block

**Evidence (production, `agri-service ops jobs-usage-report --days 1 --by host`, 2026-09-28):**
api.weather.gov served 601 requests and 447.30 MB to `sensors-direct-forward` in ONE attempt (about
0.744 MB a request, no 429s, no 5xx, about 72 s). Every other lane pulls a few MB a day. The 601 are the
Idaho/Montana/Oregon/Washington roster pages plus one `/stations/{id}/observations?start=..&end=..`
request per station (about 591). Until 2026-09-28 every station was asked for the whole six-day rolling
window on every run. About 144 hourly reports per station came back, and the lane keeps ONE per
station-day: the day's latest (`rows.py`, `adapter.py`). So five of the six days were reports already
held.

**The rule.** `watermark.py` reads z13 inside the rolling window before the poll, and each station is
asked for `[max(window.start, min(lane_frontier, newest_s - OVERLAP)), now)`:

- `newest_s` is the station's newest published `observed_at`. Anything older than it cannot win its
  day (the merge discards an older block). Using the station's own value also re-covers a station
  whose request failed on the last run.
- `lane_frontier` is the earliest point any window day can still change. A day is settled once its z13
  completion marker's `completed_at` minus `SENSORS_SETTLE_OVERSCAN` falls after the day's end: the
  run that last wrote it had already seen the whole day, with margin. An unsettled day contributes
  `completed_at - OVERLAP`. A day that is not `data` (missing, absent, incomplete, conflict) contributes
  its midnight minus the overlap. This covers a day whose write failed while a newer day's write
  succeeded. Such a day stays unsettled until a later run rewrites it with the day-final report. A
  completion marker stamped in the future (a fast writer clock) is clamped to the poll's own `now`
  before either comparison, so it cannot settle a day early.
- A station with no published report in the window (a first run, a new roster member, a station whose
  reports carry no numeric measurement) gets the whole window, the old request shape.
- `SENSORS_WATERMARK_OVERLAP` (3 h) and `SENSORS_SETTLE_OVERSCAN` (12 h) are deliberately different
  constants, not one shared value. `OVERLAP` is only the per-station re-fetch floor: it must cover NWS
  publication lag plus the gap between a run's fetch and its own completion marker (time budget up to
  900 s plus the retry tail). Do not shrink it below that. The flow test in
  `tests/direct/test_sensors_direct_watermark.py` shows a 23:53 report that reached NWS after the
  midnight poll; it is recovered only because of the overlap. `SETTLE_OVERSCAN` is the larger bound
  that decides when a day stops being re-fetched at all: it also has to cover (a) a correction or
  quality-flag update NWS applies to a report that already won its day, which arrives long after
  publication lag would predict, and (b) `rows.py`'s own admission that NWS's `observedAt` "is not
  provably a UTC date" -- this module's day boundaries assume UTC midnight, so the true day could end
  up to one US UTC offset early or late. 12 h was chosen to comfortably exceed both.
- A window day whose z13 read raises (a corrupt part, a bucket error that outlived its own retries) is
  folded in as reopened from its own midnight rather than aborting the whole plan; `SensorsFetchPlan`
  and `SensorsPollResult` both carry the count as `days_unreadable`, and `sensors_forward_fetch` reports
  it. This can only widen a request, never cause a skip.

**Expected cost.** At a daily cadence the window is about 27 h instead of 144 h, so roughly 81% fewer
observation bytes (about 447 MB down to about 85 MB). At the scheduled hourly cadence it is about 4 h,
roughly 97% fewer. The request count does not change: still one per station.

**What this does not cover.** A compound failure: a station's request fails on the run that settles an
older day, AND the run that later captures that station's newer report fails to write that older day.
That station-day then keeps a non-final winner. It is not a missing day. The completion marker's
`completed_at` is trusted as "fetched no earlier than this minus the overlap" only because this forward
is the sole live z13 writer for `layer=sensors/kind=observed`. A future re-export or repair writer must
not stamp a later `completed_at` over content it did not re-fetch. If it does, a mid-day day reads as
settled.

The bucket is now opened before NWS is called (`forward.py::run`). A run with no object-store
credentials therefore fails without spending any upstream requests.
