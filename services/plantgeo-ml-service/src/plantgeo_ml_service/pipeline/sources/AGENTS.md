# pipeline/sources (L3) - the only HTTP in this service

Spec FR-12. `protocol.py` declares what a weather-forecast source is; `open_meteo.py` implements it
against Open-Meteo's Single Runs endpoint. `pipeline/weather_forecast_daily.py` consumes the
protocol and never learns a provider's name, so a second provider is a new module here plus a
binding, and nothing downstream changes.

`pipeline` may import `httpx`; `method` may not, and `tests/test_layer_import_contract.py` enforces
it. An estimator that could reach a provider on its own would make the Mojo port impossible and
would put a network call inside a unit test.

## What was lifted, and the three corrections made on the way

The code came from agri-data-service's deleted `ingest/weather_forecast/` package (`c9c5256c`, fixes
`a1b3a497`; schema `e66dbc36`), which the owner moved to this service on 2026-09-19. Three things
changed, all of them evidence-driven:

1. **The one-hour precipitation shift is gone.** The deleted code set precipitation's
   `valid_time = hour - 1h` on the documented belief that Open-Meteo accumulates over the PRECEDING
   hour. The provider's own daily sums refute its docs: in
   `.omc/research/forecast-s3-probe-20260919/wet-crosscheck-raw.json`, hours `00:00..23:00` of
   2026-09-19 at `(5.45, 100.3)` sum to 17.8 mm, which is exactly that day's `precipitation_sum`.
   The timestamp therefore labels the START of the accumulation hour, and this module publishes
   `valid_time = interval_start = t`, `interval_end = t + 1h`. This also closes style finding S7:
   the shifted row's window preceded `model_init_time`, so `lead_hours` went negative on a column
   typed as the forecast lead.
2. **Multi-location pairing is verified, not trusted.** The response is a JSON array in request
   order whose first entry omits `location_id` and whose rest are numbered from 1
   (`multi-location-raw.json`). `paired_entry_indexes` checks that numbering AND re-derives the
   pairing from the coordinates the provider SNAPPED to: (a) every entry must land within
   `MAX_SNAP_DEGREES` of the request at its own index, and (b) no two entries may be better off
   swapped, meaning there is no pair where each one's answer is strictly closer to the other's
   request. A genuine reorder always produces such a pair. The rule is deliberately NOT "each
   answer must be closest to its own request": two requested cells inside one provider grid cell
   receive the SAME snapped point at unequal distances, so one of them is legitimately closer to
   its neighbour's request, and the strict rule would reject every correct response from a lattice
   finer than the provider's grid. The swap search is quadratic in the batch and bounded by the
   200-location ceiling.
3. **One variable catalogue.** `warehouse/weather_forecast.py::VARIABLES` is the only table of
   units, statistics and plausible bounds; `UPSTREAM_VARIABLES` (what the fetcher requests) is
   DERIVED from it by the `requested_from_provider` flag rather than restated here. That closes
   style finding S6, whose two "must stay equal" copies had nothing enforcing the equality.
   `tests/test_open_meteo_source.py` asserts the request list is the schema module's own object.

## Why `single-runs-api`, not `api.open-meteo.com`

Three Open-Meteo hosts answer three different questions. `api.open-meteo.com` answers "what is
happening now" and stitches the newest run into one rolling series that cannot be pinned to an
initialization. `archive-api.open-meteo.com` answers "what did ERA5 reanalyze for this settled past
day". Only `single-runs-api.open-meteo.com` preserves one model's one initialization as an
addressable object, which is what the `run_id` column exists to name.

## Wind: derived from speed and direction

Open-Meteo publishes `wind_speed_10m`/`wind_direction_10m`; the schema publishes the earth-relative
component pair as primary rows with speed and direction beside them. The conversion, at the source
boundary:

    u = -speed * sin(radians(direction))
    v = -speed * cos(radians(direction))

where `direction` is the meteorological "from" bearing, clockwise from true north. Because `u`/`v`
come from ONE point's single reading, `atan2`/`hypot` reproduce the original pair exactly. The
"scalar direction averages are forbidden" rule applies to a different step: merging many cells into
a coarse rung, which `weather_forecast_daily.py` does by averaging the COMPONENTS and recomputing
the bearing from them.

**Calm has no defined bearing.** At `speed == 0.0` the direction and both components become a
governed absence (`missing_reason = "not_generated"`) even when the provider supplies a bearing;
`wind_speed_10m` keeps the real zero, because a numeric zero is a value and never a stand-in for
absence.

## The bounded fetch budget

- `WEATHER_FORECAST_RUN_BOUNDS` is 64 MiB / 120 s for one run pull, a different shape from a
  current-conditions poll. The body is read in chunks and refused the moment it passes the ceiling,
  so an endpoint answering with something far larger costs the ceiling rather than the whole object.
- `MAX_FORECAST_RUN_LOCATIONS = 200` per request; a longer cell list is fetched in batches by the
  lane, sequentially, because concurrency here would only multiply the provider's rate limit.
- `MAX_FORECAST_RUN_DAYS = 16` is documentation-sourced, not live-probed.
- One run per invocation is STRUCTURAL: `fetch_forecast_run` takes one `model_init_time`, never a
  list. `MAX_FORECAST_RUNS_PER_DAY = 4` is published as a budget for the daily executor to schedule
  against; nothing here owns a clock or a call counter, so a stateless adapter cannot enforce it
  without gaining hidden state.
- **A 429 raises `OpenMeteoRateLimitError` exactly once.** No retry loop, no sleep. The classified
  quota scope (`day`/`hour`/`minute`/`unknown`, read from the response body because the header is
  not exposed through the bounded read) is what a caller persists as a durable cooldown. A refusal
  that slept would look like a slow fetch rather than a rate limit.
- An over-budget request is REFUSED, never truncated: a silently shortened coordinate list publishes
  a day that claims fewer cells than the caller asked for.

## The source receipt

Every fetch returns a `SourceReceipt` carrying the credential-free request URL, the SHA-256 of the
exact bytes parsed, the byte count and the location count. `redact_credentials` strips
credential-bearing query keys by construction rather than relying on the current plan being
keyless. The daily lane writes those receipts into one content-addressed evidence object and cites
it from every availability row, so a published day names the bytes it was derived from.
