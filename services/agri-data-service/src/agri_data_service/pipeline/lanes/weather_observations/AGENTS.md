# `pipeline/lanes/weather_observations/` — the `weather-observations-direct-forward` strategy

Track `config_driven_ingestion_20260926`, batch-2 swarm, slice `s-weather-observations`. One lane
(`lanes/weather-observations-direct-forward.toml`, strategy key
`weather_observations.open_meteo_current`), shipped **DARK** (`executor = "legacy"`). The legacy
twin this strategy's behaviour is derived from is
`pipeline/direct/weather_observations/**` — READ-ONLY, never imported (`pipeline/lanes/AGENTS.md`
bans `pipeline.direct` from every strategy module).

| module | what |
|---|---|
| `open_meteo_current.py` | `STRATEGY`: the sample grid, the query shape, the day-bucketing view, the digest, the rows |
| `../../../ingest/open_meteo.py` | the source contract: `bounded_sample_points`, `parse_current_weather`, `CURRENT_VALUE_BOUNDS` |
| `../../../ingest/identity.py` | `build_weather_observation_identity`: the `{lat4dp}:{lon4dp}:{observedAtISO}` identity |
| `../../../warehouse/schemas/weather_observations.py` | the stream schema, grain and coarse-rung derivation |

Imports: `ingest`, `warehouse`, `pipeline/lanes/_shared` (the digest pair) and
`pipeline/runner/contract.py` only (S14). Never `pipeline/validation` or `pipeline/direct`.

## The name trap

`weather-observations` is overloaded in this repo: this package's producer is the Open-Meteo
**current-conditions** poll (`ingest/open_meteo.py`'s `WEATHER_LAYER`, the `current` endpoint,
historically written into `geo.features`), **not** the governed NASA-POWER/ERA5-Land historical
archive that backs `agri.signal_observation` under the same stream name
(`execution/weather_observations/AGENTS.md` "Traps that will otherwise be rediscovered";
`docs/lanes/weather-observations.md` section 5 item 3 — that document describes the OTHER
producer). Confirm which one a change is aimed at before touching either.

## A poll has no day to ask for; every unit covers the whole window

Unlike every other lane in this track, the `current` endpoint has no `start_date`/`end_date`: one
request returns only the instant closest to the poll. `plan_requests` therefore gives every sample
point's `SourceRequest.days` the **whole asked window**, not one day each — `settle`/`rows` bucket
the response's own `observedAt` into a day afterwards (`_readings`), matching legacy
`pipeline/direct/weather_observations/source.py`'s module docstring ("there is no day this lane's
writer can ask the source for by date") and `rows.py`'s day-bucketing shape. The window itself
(`absence_recheck_days = 2` in the TOML) exists for exactly one reason: a poll within
`MAX_OBSERVATION_AGE` (3h, `ingest/open_meteo.py`) of UTC midnight can bucket a reading onto
*yesterday*, and a window of 1 would drop that bucket with no archive to recover it from — the
exact failure legacy `forward.py`'s `WEATHER_OBSERVATIONS_DEFAULT_MAX_DAYS` comment names.

## Two kinds of bad reading, two different outcomes

- **A point's fetch fails** (network fault, a stale reading past `MAX_OBSERVATION_AGE`, an
  out-of-range value `CURRENT_VALUE_BOUNDS` rejects) — `parse_current_weather` raises inside
  `fetch()`, that UNIT produces no `SourceResponse`, and the day is simply **partial**:
  `write_and_recheck` writes whoever did answer and rewrites the day as more points report later
  (`pipeline/runner/AGENTS.md` "A unit that stays down"). This is the ordinary, expected case for a
  150-point fan-out.
- **A reading answers but cannot be placed on any day** — `_readings` re-validates every
  successfully-parsed `observedAt`'s leading ten characters (`_observation_day`, reproducing legacy
  `rows.py::observation_day` without importing it) and raises `WeatherObservationsRowError` when
  that fails. `settle`/`rows` do **not** catch this locally, so the runner's own
  `strategy_error` handling (`pipeline/runner/turn.py::_Turn._settle`) takes over: the whole day is
  refused, nothing is written for it, and nothing is silently dropped. This is this lane's
  `REFUSE_WHOLE_RELEASE` policy (`pipeline/direct/weather_observations/forward.py::WRITER_CONTRACT`
  `policy_basis`) carried into the config-lane contract: "a dropped point silently shrinks the grid
  a day was written against." In practice `parse_current_weather` already guarantees a canonical
  `observedAt`, so this is a defensive re-check against future format drift, not a normal-path
  outcome — `tests/lanes/weather_observations/test_open_meteo_current.py` exercises it directly with
  a hand-built malformed response, since the real fetch path cannot currently produce one.

## Digest reuse (no second sha256-of-canonical-JSON idiom)

`day_source_digest` is `pipeline/lanes/_shared/digest.py`'s `canonical_json_text` +
`sha256_hex_of_lines` over each point's reading facts, **excluding** `ingested_at` — the same shape
`water_gauges/usgs_water_data.py::day_source_digest` uses for the identical reason (S11: a strategy
whose rows carry a retrieval instant must set its own digest, or every re-poll reads as a revision).
`_support_digest` is `sha256_hex` over the bbox+spacing that built the grid, binding a checkpoint to
the support it was planned over.

## Deviations from the legacy writer (recorded, not silently absorbed)

- **The sample grid is keyed off `region.default_camera_envelope`, not `INGEST_BBOX`.** A config
  lane has no CLI flag, and the region manifest is this track's one source of a lane's spatial
  footprint (`pipeline/lanes/AGENTS.md`). If production's `INGEST_BBOX` ever diverges from the
  active region's camera envelope, this strategy samples a **different** grid than the legacy
  writer's — reconcile before any G6 cut-over. The spacing (`WEATHER_SAMPLE_SPACING_DEGREES`) is
  still read from the environment at call time, matching the legacy default exactly.
- **No recovery/checkpoint replay, no absence-retraction bookkeeping, no CLI.** Those are legacy
  `forward.py`/`recovery.py`/`adapter.py` concerns the runner now owns outright (per-unit retry
  ladder, checkpoints, lane-day locking) — `pipeline/lanes/AGENTS.md` "A strategy is thin (D2)".
  Porting them would duplicate framework the runner already provides.
- **`docs/lanes/weather-observations.md` already exists and describes the OTHER producer** (the
  signal/archive plane — see "The name trap" above). This package's lane contract therefore lives at
  `docs/lanes/weather-observations-direct-forward.md` instead of overwriting that unrelated,
  pre-existing document. Open item: the task partition that assigned this slice named the colliding
  path as "NEW"; it was not, and an owner should decide whether the archive doc should be renamed to
  disambiguate the two `weather-observations` lanes permanently.
- **`[[streams]].complete_history_floor` is omitted from the TOML.** `lane_registry.py`'s
  registration for this stream carries one (2018-01-01), but it resolves through
  `_MEASURED_COMPLETE_HISTORY_FLOORS["weather-observations"]`, a dict entry the registration's own
  comment says actually describes the unrelated archive producer. Mirroring it would launder that
  mismatch into this lane's TOML; flagged instead for an owner to give this stream its own constant.
- **`turn_timeout_seconds` (TOML `[budget]`) is an estimate, not a measurement** — SKETCH scope; no
  real turn has been timed against the runner's own fetch ladder. Measure before relying on it.
