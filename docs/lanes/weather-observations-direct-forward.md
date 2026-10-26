---
type: lane-contract
---

# `weather-observations-direct-forward` lane

**This is a different lane from `docs/lanes/weather-observations.md`.** That document describes the
governed historical archive (NASA POWER + Open-Meteo ERA5-Land) backing `agri.signal_observation`.
This document describes the Open-Meteo **current-conditions** poll — `ingest/open_meteo.py`'s
`WEATHER_LAYER`, historically written into `geo.features` — which shares the bare stream name
`weather-observations` with that other producer (whose code, `execution/weather_observations/`,
was purged 2026-10-26 while its rows remain; `docs/lanes/weather-observations.md` section 5 item
3). This document is filed under a disambiguated path so it does not overwrite that unrelated,
pre-existing content; see `pipeline/lanes/weather_observations/AGENTS.md` "Deviations" for the
open item this leaves for an owner.

The legacy producer this lane's config strategy is derived from is
`pipeline/direct/weather_observations/**` (READ-ONLY twin; its own `AGENTS.md` carries the
operational detail — recovery, checkpoints, CLI shape — this document does not restate). This
document covers only what batch-2's config-lane migration (track `config_driven_ingestion_20260926`)
added.

## 8. Config-lane strategy (`s-weather-observations`, track `config_driven_ingestion_20260926`)

A dark `daily_series` TOML and strategy now sit beside the legacy direct writer, built against the
Phase 4 "frozen" runner contract:

- **Files.** `services/agri-data-service/lanes/weather-observations-direct-forward.toml` (new,
  `executor = "legacy"`, `enabled = true`, mirroring the live legacy lane
  `execution/lane_specs.py:636-645`, `WEATHER_OBSERVATIONS_DIRECT_LANE_ID`);
  `pipeline/lanes/weather_observations/open_meteo_current.py` (strategy key
  `weather_observations.open_meteo_current`).
- **A poll is not a per-day fetch, so every unit covers the whole window.** Every other config-lane
  strategy in this track asks its source FOR a day; the `current` endpoint has no
  `start_date`/`end_date` and only ever answers the instant closest to the poll
  (`pipeline/direct/weather_observations/source.py` module docstring). `plan_requests` therefore
  gives every sample point's `SourceRequest.days` the WHOLE asked window (the contract's own
  allowance: "may be wider than the days the runner asked for"), and `settle`/`rows` bucket each
  response's own `observedAt` into a day afterwards. `absence_recheck_days = 2` is the smallest
  window that can never drop the midnight-straddle bucket legacy's
  `WEATHER_OBSERVATIONS_DEFAULT_MAX_DAYS` guards against (one poll can bucket into at most today
  and, within `MAX_OBSERVATION_AGE` = 3h of UTC midnight, yesterday).
- **`write_and_recheck`, with no revision window.** Unlike `water-gauges-daily` (which re-asks a
  550-day block of published days for late approvals), this lane has no settled-past-day concept at
  all: a reading that lands on a day is never revisited once that day ages out of the 2-day forward
  window, because the source has nothing further to say about it. `revision_window_days` is
  therefore omitted from the TOML rather than set — there is no block of older published days a
  later turn could usefully re-ask.
- **Two kinds of bad reading, two different outcomes.** A point whose FETCH fails (network fault, a
  stale reading, an out-of-range `CURRENT_VALUE_BOUNDS` value) simply produces no `SourceResponse`,
  and the day stays correctly partial under `write_and_recheck` — the ordinary case for a
  ~100-point fan-out where a handful of points miss any given poll. A reading that ANSWERS but
  cannot be bucketed into any day at all (an `observedAt` whose leading ten characters are not a
  canonical `YYYY-MM-DD`) raises `WeatherObservationsRowError` out of `settle`/`rows`, which the
  runner's own `strategy_error` handling turns into a refusal of the WHOLE day — never a partial
  write that silently drops the one bad reading and keeps the rest. This reproduces legacy
  `pipeline/direct/weather_observations/forward.py::WRITER_CONTRACT`'s `REFUSE_WHOLE_RELEASE`
  policy_basis ("a dropped point silently shrinks the grid a day was written against") inside the
  new contract's vocabulary, rather than adopting `sensors/`'s more tolerant per-reading-drop
  policy. In practice `parse_current_weather` already guarantees a canonical `observedAt` before a
  response ever reaches `settle`/`rows`, so this is a defensive re-check against future format
  drift, not a normal-path outcome; it is exercised directly in
  `tests/lanes/weather_observations/test_open_meteo_current.py` with a hand-built response, since
  the real fetch path cannot currently produce one.
- **Same-day freshness at G6 (OWNER decision, not resolved here).** The legacy writer polls hourly
  and lands current conditions in TODAY's own bucket every time. `pipeline/runner/windows.py`'s
  `candidate_edge` clamps every `write_and_recheck` lane's edge to `today - 1` (the O1 invariant: "a
  write_and_recheck lane never reaches past yesterday UTC") — a runner-level invariant this TOML
  cannot opt out of, only state honestly (`publication_lag_days = 1`, matching the clamp rather than
  claiming `0` and relying on the clamp to silently correct it). Under the config runner this lane
  can therefore never write TODAY, only TODAY − 1: an `executor = "config"` cut-over as-is would
  regress current-conditions freshness by up to ~24h for a lane whose purpose is near-real-time
  conditions. The track records this as an open OWNER decision — an O1 carve-out for
  zero-settlement-delay lanes versus accepting the day lag — and the lane must not flip to
  `executor = "config"` before that decision is made; see
  `pipeline/lanes/weather_observations/AGENTS.md` "Same-day freshness at G6" for the same note next
  to the code.
- **The sample grid moved from `INGEST_BBOX` to `region.default_camera_envelope`.** A config lane
  has no CLI flag to carry a bbox override, and the region manifest is this track's one source of a
  lane's spatial footprint (`pipeline/lanes/AGENTS.md`). DEVIATION, flagged rather than silently
  absorbed: if production's `INGEST_BBOX` ever differs from the active region's camera envelope,
  this strategy will sample a DIFFERENT grid than the legacy writer's. Reconcile the two before any
  G6 cut-over; `pipeline/lanes/weather_observations/AGENTS.md` carries the same note next to the
  code it describes.
- **`[[streams]].history_floor` mirrors `lane_registry.py`'s S18 registration literally, and says so
  honestly.** That registration's own `floor_basis` text calls its number ("2026-08-01") a
  "FALLBACK -- NOT DECLARED ANYWHERE, AND THE GUESS IS DELIBERATELY SHALLOW"; the TOML's
  `floor_basis` repeats that admission rather than dressing it up as a measured fact.
  `complete_history_floor` is deliberately OMITTED: the registration's value for it (2018-01-01)
  resolves through a dict entry (`_MEASURED_COMPLETE_HISTORY_FLOORS["weather-observations"]`) whose
  own comment says it actually describes the UNRELATED archive producer this stream name collides
  with. Mirroring it here would launder that mismatch forward instead of reporting it; an owner
  should give this stream its own constant.
- **No `probe_edge` needed.** S19's probe_edge rule binds only a `refuse` lane on a weighted
  provider (`foundation/lane_config/models.py::lane_requires_probe_edge`); this lane's
  `partial_day = "write_and_recheck"` takes it out of scope, the identical reasoning
  `docs/lanes/drought.md` section 8 "No probe_edge needed" gives for a different lane on the same
  rule.
- **Known gap: the TOML's `turn_timeout_seconds` (300s) is an estimate, not a measurement.** SKETCH
  scope (this slice shipped with no pytest/mypy/ruff run); the legacy writer's own default CLI
  budget is 120s for a single poll under its OWN, differently-shaped per-point retry ladder
  (`WEATHER_CURRENT_RETRY_LADDER`). The runner's ladder (`pipeline/runner/fetch.py`) is not
  re-derived against this lane's ~100-point fan-out here. Measure a real turn before relying on
  this number — open item for `i-coordinator`.
- **Known gap #2: this TOML has not been run through `pytest`/`mypy`/`ruff`.** The hard rule this
  slice shipped under was SKETCH ONLY; every claim above about the TOML parsing and the strategy
  resolving is pattern-matched against `lanes/water-gauges-daily.toml` and
  `pipeline/lanes/water_gauges/usgs_water_data.py` (the closest existing `daily_series` /
  `write_and_recheck` / multi-unit precedent) rather than executed. Run the full sweep once before
  any `executor = "config"` cut-over.
