# method/monte_carlo (L1)

One seeded forecaster per lane: fire detections (hurdle bootstrap), sensors, signal, NDVI
seasonal anomaly and water gauges. Every draw comes from an explicitly seeded numpy PCG64, so
the same artifact plus the same inputs plus the same seed reproduce a byte-identical partition.

Import rules: `foundation` only. `method/ml` is a forbidden sibling, and so are `warehouse`,
`pipeline`, `planes`, `interface` and every storage or web dependency.

A forecaster refuses on insufficient history rather than returning a flat ensemble; the caller
writes a governed absence for that day.

## sensors.py

Deterministic Monte Carlo forecast for the sensors lane's day-grain NWS readings.

Layer L1 (method/monte_carlo): pure domain computation. No I/O, no `sqlalchemy`, no `httpx`.
See `docs/lanes/sensors.md` section 7 for why this lane needs its own history gate: NWS gives
this producer no deeper archive than a rolling ~6 days (`ingest/sensors.py:94-96`), so the
warehouse's own history is only as deep as days elapsed since the producer started
(2026-08-04) -- roughly three weeks as of this module's writing, growing by one day per day,
never backfillable further. Fitting a 30-day-ahead ensemble on three weeks of history is
fitting noise, not signal (docs/lanes/sensors.md section 7). This module implements option (a)
from that section -- "wait, and gate on minimum history" -- rather than option (b), borrowing a
longer prior from `weather-observations`' NASA POWER/ERA5 record: doing that honestly requires
plumbing a second lane's governed series into this one's fitting code as a documented input,
which is a genuine cross-lane statistical decision this module does not make. Anyone adding a
borrowed prior later MUST label its provenance as borrowed and name the source lane; nothing
here may claim a borrowed value is this lane's own observation.

The sixteen NWS measurement fields are different physical quantities and are forecast under
three different treatments, dispatched by `measurement_family`:

- TEMPERATURE_LIKE (temperature, dewpoint, windChill, heatIndex, and the two 24-hour extremes):
  unbounded real-valued quantities. The NDVI seasonal-anomaly-bootstrap shape
  (`vegetation_ndvi_forecast.py`) transfers cleanly MINUS its day-of-year climatology, which
  three weeks of history cannot support: the "seasonal level" here is the trailing sample mean
  of the cell's own history, and the innovation pool is the whole residual series rather than a
  day-of-year-windowed subset. Persistence decay and residual-pool bootstrapping are otherwise
  the same architecture.
- BOUNDED_NONNEGATIVE (relativeHumidity, windSpeed, windGust, barometricPressure,
  seaLevelPressure, visibility): the same architecture as above, with paths clipped at a
  physical floor of 0.0 (and `relativeHumidity` additionally capped at 100.0) so a forecast can
  never claim a body could weigh negative pressure or ground could hold negative humidity.
- ZERO_INFLATED_NONNEGATIVE (the three precipitation windows): additive anomaly bootstrapping is
  the wrong shape for a quantity that is legitimately exactly zero on most days -- an anomaly
  bootstrap around a positive mean would routinely propose negative rainfall. Simulated
  independently per horizon day instead: a Bernoulli "did it rain" draw at the history's own wet
  probability, and on a wet draw, a bootstrap amount from the pool of the history's own positive
  readings. This deliberately does NOT model day-to-day wet/dry persistence -- three weeks of
  history cannot support fitting a transition matrix either, and an unfitted one would be a
  fabricated default, which the engineering principles forbid.
- CIRCULAR_UNSUPPORTED (windDirection): a compass bearing wraps at 360, so neither an additive
  bootstrap nor a zero floor is meaningful (390 degrees is not "extreme wind", it is 30 degrees
  with wraparound). This module refuses every request for this field outright, by measurement
  family, independent of how much history exists.

## fire_detections.py

Deterministic seasonal-hurdle Monte Carlo for the fire-detections cell-day aggregate.

Layer L1 (method/monte_carlo): pure domain computation, no I/O, no SQLAlchemy, no httpx. May NOT
import `plantgeo_ml_service.warehouse` (or `method.ml`) -- see `tests/test_layer_import_contract.py`.

WHY A HURDLE MODEL, NOT A GAUSSIAN ANOMALY BOOTRAP: `detection_count` is a non-negative integer and
heavily zero-inflated -- the exported cell-day stream (`warehouse/schemas/fire_detections.py`) only
ever carries a row where at least one detection landed; every other monitored day at that cell is an
implicit, un-materialized zero. A Gaussian innovation added to a seasonal level (the NDVI approach in
`vegetation_ndvi_forecast.py`) can and will draw a negative "count", which is not a measurement error
to clip away, it is proof the distributional family is wrong for this variable. A hurdle model -- a
Bernoulli "does anything ignite" stage, then a magnitude stage strictly conditioned on the first stage
firing -- is the standard treatment for zero-inflated counts precisely because the two questions
("will it happen" and "how big if it does") have different variance structure here: ignition is rare
and seasonal, magnitude among fires is skewed and instrument-driven. This module estimates BOTH stages
from one cell's own governed history and refuses when that history cannot support the estimate, rather
than fabricating a rate from too little evidence.

WHY THE MAGNITUDE STAGE IS A JOINT HISTORICAL ROW, NOT FOUR INDEPENDENT DRAWS: `detection_count`,
`frp_sum`, `frp_observation_count` and `high_confidence_detection_count` are not independent -- the
observed schema's own null-handling rule (`frp_sum` is NULL exactly when no detection that day
published FRP, never a fabricated 0.0) only survives forecasting if all four columns for one simulated
day come from ONE actually-observed historical cell-day, sampled together. Drawing each column
separately would let a magnitude draw pair a positive `frp_sum` with a `high_confidence_detection_count`
that never happened together, and could just as easily invent a non-null `frp_sum` for a day whose own
history recorded none. Every value below is a value some historical day of THIS cell actually reported.

WHY DETECTION_COUNT CAN NEVER GO NEGATIVE: the hurdle stage emits exactly 0 (an integer literal) when
the Bernoulli draw misses, and the magnitude stage only ever emits `detection_count` values copied
verbatim from `ObservedCellDay` rows, which are constructed to reject anything less than 1
(`ObservedCellDay.__post_init__`). The output space of one simulated cell-day is therefore
`{0} | {n : n is some historical day's observed detection_count}` -- a finite set of non-negative
integers by construction, never an interpolated or synthesized value.

WHY QUANTILES ARE A NEAREST-RANK ORDER STATISTIC, NOT A LINEAR-INTERPOLATED PERCENTILE: linear
interpolation between two ensemble draws (`vegetation_ndvi_forecast.py`'s `numpy.percentile(...,
method="linear")`) can synthesize a `detection_count` no draw ever produced, and worse, an
interpolated point between two DIFFERENT historical rows would recombine mismatched `frp_sum`/
`detection_count` pairs, reintroducing exactly the joint-consistency problem the row-level bootstrap
above exists to avoid. `numpy.argsort` plus a nearest-rank index selects one WHOLE simulated draw --
every reported quantile row is therefore internally consistent and reproduces values this method
actually simulated.

## signal.py

Deterministic per-signal seasonal Monte Carlo forecaster for the governed weather-signal plane.

Layer L1 (method/monte_carlo): pure domain computation, no I/O, no SQLAlchemy, no httpx. Every one
of the 19 `agri.signal_observation` signals (docs/lanes/weather-observations.md section 4) is
forecast independently, per cell, through `simulate_signal_forecast`. Two resampling strategies
are dispatched by `SignalSeriesSpec.bootstrap_kind`:

  * ADDITIVE_ANOMALY -- day-of-year climatology + a persistence-decayed anchor anomaly + a
    resampled seasonal innovation, then clipped to the series' own physical bounds. The shape
    `method/monte_carlo/vegetation_ndvi_forecast.py` already uses for NDVI, generalised here to an
    arbitrary bounded series.
  * EMPIRICAL_SEASONAL_RESAMPLE -- precipitation only. Precipitation is zero-inflated and
    right-skewed; adding a symmetric anomaly to a seasonal mean can and does draw negative
    rainfall. Resampling REAL seasonally matched historical values instead makes a negative draw
    structurally impossible, at the cost of never projecting a value the record has not shown for
    that time of year.

Every `kind=forecast` row this module's output eventually becomes carries all six provenance
columns `conductor/code_styleguides/layer-lanes.md` section 3 requires: `forecast_run_id`,
`random_seed`, `ensemble_size`, `horizon_days`, `issued_on`, and `quantile` -- see
`SignalForecastRun`. The RNG is seeded directly from the caller-supplied, recorded
`SimulationRequest.seed`; `forecast_run_id` is a separate sha256 fingerprint of the whole
parameter set (including that seed), never the seed itself, so "what reproduces the run" and
"what identifies the run" stay two distinct, both-recorded facts.

`issued_on` must respect each signal's own producer lag (section 2 of the same doc): NASA-POWER
signals anchor at `today - 5d`, Open-Meteo ERA5-Land signals at `today - 9d`, and
`surface_shortwave_radiation` carries its own measured ~2-month ceiling rather than inheriting
NASA's blanket 5-day constant. `SIGNAL_SERIES_SPECS` declares all three; `issued_on_for` is the
one place that turns a lag into an issue day, and it is pure -- callers pass `today` in rather
than this module calling `datetime.now()` itself.
