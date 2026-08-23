"""Deterministic per-signal seasonal Monte Carlo forecaster for the governed weather-signal plane.

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
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import numpy
from numpy.random import PCG64, Generator

if TYPE_CHECKING:
    from collections.abc import Mapping

    from numpy.typing import NDArray

METHOD_NAME_ADDITIVE_ANOMALY: Final = "signal_seasonal_anomaly_bootstrap_v1"
METHOD_NAME_EMPIRICAL_RESAMPLE: Final = "signal_seasonal_empirical_resample_v1"
RNG_FINGERPRINT: Final = "pcg64_generator_integers"

DAYS_PER_SEASONAL_CYCLE: Final = 366
SEASONAL_WINDOW_DAYS: Final = 15
MIN_CLIMATOLOGY_SAMPLES: Final = 4
MIN_INNOVATION_SAMPLES: Final = 4
MIN_AUTOCORRELATION_PAIRS: Final = 8
MAX_AUTOCORRELATION_GAP_DAYS: Final = 30
# Daily-cadence signals (NASA POWER / ERA5-Land) have no revisit gap, unlike NDVI's ~5-day Sentinel
# revisit; two seasons of daily history is a more honest floor than NDVI's sparser 24-day minimum.
MIN_TRAINING_DAYS: Final = 60
MAX_DAILY_PERSISTENCE: Final = 0.98
LOW_QUANTILE: Final = 0.1
MEDIAN_QUANTILE: Final = 0.5
HIGH_QUANTILE: Final = 0.9
PUBLISHED_QUANTILES: Final[tuple[float, ...]] = (LOW_QUANTILE, MEDIAN_QUANTILE, HIGH_QUANTILE)
FLOAT_FINGERPRINT_FORMAT: Final = ".17g"

# Measured against production 2026-08-11 (docs/lanes/weather-observations.md section 2):
# `execution/coverage_census.py`'s PUBLICATION_LAG_DAYS. NASA POWER's newest day sits 5 days behind
# today; Open-Meteo ERA5-Land's sits 9. Radiation's lag is separately measured, not derived from
# either.
NASA_POWER_PUBLICATION_LAG_DAYS: Final = 5
ERA5_LAND_PUBLICATION_LAG_DAYS: Final = 9
# "carries a hard ~2-month publication lag that no amount of re-running fixes" -- NASA's radiation
# plan is permanently capped near 2026-05-31 while its seven sibling surface signals reach
# today-5d (weather-observations.md section 2). 60 days is a conservative, explicitly-approximate
# stand-in for that measured ceiling; it must never collapse to the blanket 5-day constant.
SURFACE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS: Final = 60


class InsufficientSignalHistoryError(ValueError):
    """Raised when a cell's own governed history cannot support the method without fabrication."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


class SignalSeriesSpecError(LookupError):
    """Raised when a signal name has no declared forecast contract."""


class SignalProducer(StrEnum):
    """`agri.data_source.key` for the two producers this lane forecasts from."""

    NASA_POWER = "nasa-power-daily"
    ERA5_LAND = "open-meteo-era5-land-archive"


class BootstrapKind(StrEnum):
    """Which resampling strategy one series' physical distribution requires."""

    ADDITIVE_ANOMALY = "additive_anomaly"
    """Climatology + persistence-decayed anchor anomaly + resampled innovation, then clipped."""

    EMPIRICAL_SEASONAL_RESAMPLE = "empirical_seasonal_resample"
    """Direct resampling of seasonally matched historical VALUES, never a synthesized anomaly."""


@dataclass(frozen=True, slots=True)
class SignalSeriesSpec:
    """One governed signal's forecast contract: its producer lag, physical bounds, and resampling kind."""

    signal_name: str
    normalized_unit: str
    support_key: str
    producer: SignalProducer
    publication_lag_days: int
    bootstrap_kind: BootstrapKind
    lower_bound: float | None
    upper_bound: float | None


def _nasa(
    signal_name: str,
    unit: str,
    *,
    lag: int = NASA_POWER_PUBLICATION_LAG_DAYS,
    bounds: tuple[float | None, float | None] = (None, None),
    kind: BootstrapKind = BootstrapKind.ADDITIVE_ANOMALY,
) -> SignalSeriesSpec:
    return SignalSeriesSpec(
        signal_name=signal_name,
        normalized_unit=unit,
        support_key="surface",
        producer=SignalProducer.NASA_POWER,
        publication_lag_days=lag,
        bootstrap_kind=kind,
        lower_bound=bounds[0],
        upper_bound=bounds[1],
    )


def _era5(signal_name: str, unit: str, *, bounds: tuple[float | None, float | None] = (None, None)) -> SignalSeriesSpec:
    return SignalSeriesSpec(
        signal_name=signal_name,
        normalized_unit=unit,
        support_key="era5-land-0.1deg",
        producer=SignalProducer.ERA5_LAND,
        publication_lag_days=ERA5_LAND_PUBLICATION_LAG_DAYS,
        bootstrap_kind=BootstrapKind.ADDITIVE_ANOMALY,
        lower_bound=bounds[0],
        upper_bound=bounds[1],
    )


# The 19 governed signals of docs/lanes/weather-observations.md section 4, minus nothing: every
# name gets its own independent forecast contract, per that document's section 7 recommendation
# ("Recommend forecasting each signal_name independently, per cell ... Do not attempt a single
# joint model across all 19"). Bounds are generous physical envelopes, not tight climatological
# ones -- they exist only to rule out an impossible draw, never to shape the distribution.
SIGNAL_SERIES_SPECS: Final[Mapping[str, SignalSeriesSpec]] = {
    spec.signal_name: spec
    for spec in (
        _nasa("air_temperature_mean", "C", bounds=(-90.0, 60.0)),
        _nasa("air_temperature_max", "C", bounds=(-90.0, 60.0)),
        _nasa("air_temperature_min", "C", bounds=(-90.0, 60.0)),
        _nasa("dew_point_temperature", "C", bounds=(-90.0, 60.0)),
        _nasa(
            "precipitation",
            "mm/day",
            bounds=(0.0, None),
            kind=BootstrapKind.EMPIRICAL_SEASONAL_RESAMPLE,
        ),
        _nasa("relative_humidity", "%", bounds=(0.0, 100.0)),
        _nasa("wind_speed", "m/s", bounds=(0.0, None)),
        _nasa(
            "surface_shortwave_radiation",
            "MJ/m^2/day",
            lag=SURFACE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
            bounds=(0.0, None),
        ),
        _nasa("soil_wetness_surface", "fraction_of_saturation", bounds=(0.0, 1.0)),
        _nasa("soil_wetness_root_zone", "fraction_of_saturation", bounds=(0.0, 1.0)),
        _nasa("soil_wetness_profile", "fraction_of_saturation", bounds=(0.0, 1.0)),
        _era5("soil_water_content_layer_1", "m^3/m^3", bounds=(0.0, 1.0)),
        _era5("soil_water_content_layer_2", "m^3/m^3", bounds=(0.0, 1.0)),
        _era5("soil_water_content_layer_3", "m^3/m^3", bounds=(0.0, 1.0)),
        _era5("soil_temperature_level_1", "C", bounds=(-40.0, 60.0)),
        _era5("soil_temperature_level_2", "C", bounds=(-40.0, 60.0)),
        _era5("soil_temperature_level_3", "C", bounds=(-40.0, 60.0)),
        _era5("soil_temperature_level_4", "C", bounds=(-40.0, 60.0)),
        _era5("vapor_pressure_deficit", "kPa", bounds=(0.0, None)),
    )
}


def series_spec_for(signal_name: str) -> SignalSeriesSpec:
    """Return the declared forecast contract for `signal_name`, or raise naming what IS declared."""
    spec = SIGNAL_SERIES_SPECS.get(signal_name)
    if spec is None:
        raise SignalSeriesSpecError(
            f"no forecast contract declared for signal {signal_name!r}; declared signals are "
            f"{', '.join(sorted(SIGNAL_SERIES_SPECS))}"
        )
    return spec


def issued_on_for(spec: SignalSeriesSpec, *, today: date) -> date:
    """The newest day it is honest to anchor `spec`'s forecast on, given its producer's own lag.

    Pure: `today` is supplied by the caller rather than read from the clock here, so a forecast's
    leakage boundary is always an explicit, reproducible input rather than an ambient side effect.
    """
    return today - timedelta(days=spec.publication_lag_days)


@dataclass(frozen=True, slots=True)
class ObservedSignalDay:
    """One publisher-named day of one governed signal series for a single cell."""

    observed_day: date
    value: float
    observation_checksum: str


@dataclass(frozen=True, slots=True)
class SeasonalHistory:
    """A series' own history reduced to a seasonal level, an anomaly pool, and a persistence anchor."""

    cutoff_day: date
    history_start_day: date
    governed_day_count: int
    training_day_count: int
    climatology_by_day_of_year: tuple[float, ...]
    climatology_sample_counts: tuple[int, ...]
    anomaly_values: tuple[float, ...]
    anomaly_days_of_year: tuple[int, ...]
    latest_observed_day: date
    latest_observed_value: float
    anchor_day: date
    anchor_anomaly: float
    lag_one_autocorrelation: float
    autocorrelation_pair_count: int
    mean_observation_gap_days: float
    daily_persistence: float


@dataclass(frozen=True, slots=True)
class SeasonalValuePool:
    """A series' own history reduced to seasonally keyed RAW values, for empirical resampling.

    Deliberately carries no anomaly, no persistence, and no climatology mean: precipitation's
    seasonal pool already contains its own zero-inflation and skew, and resampling from it
    directly is what keeps a draw physically possible without any additive arithmetic.
    """

    cutoff_day: date
    history_start_day: date
    governed_day_count: int
    days_of_year: tuple[int, ...]
    values: tuple[float, ...]
    latest_observed_day: date
    latest_observed_value: float


@dataclass(frozen=True, slots=True)
class ForecastQuantileRow:
    """One quantile of one horizon day's simulated distribution -- one row, one value, one quantile."""

    horizon_step: int
    valid_day: date
    quantile: float
    value: float


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """Bounded, reproducible simulation parameters. `seed` is recorded verbatim, never derived."""

    horizon_days: int
    simulation_count: int
    seed: int


@dataclass(frozen=True, slots=True)
class SignalForecastRun:
    """One forecast run: the six provenance columns layer-lanes.md section 3 requires, plus its rows.

    `forecast_run_id` is a sha256 fingerprint of the whole parameter set (series identity, governed
    history, issue day, and `random_seed` among them) -- an identity, not a source of randomness.
    The RNG itself is seeded directly from `random_seed`, so the two facts stay independently
    checkable: "does this id match these inputs" and "does this seed reproduce these draws."
    """

    forecast_run_id: str
    random_seed: int
    ensemble_size: int
    horizon_days: int
    issued_on: date
    signal_name: str
    normalized_unit: str
    support_key: str
    method_name: str
    rows: tuple[ForecastQuantileRow, ...]


def day_of_year_index(value: date) -> int:
    """Return the 1-based day of year used as the seasonal phase coordinate."""
    return value.timetuple().tm_yday


def circular_day_distance(first_day_of_year: int, second_day_of_year: int) -> int:
    """Return the wrap-around distance in days between two days of year."""
    direct = abs(first_day_of_year - second_day_of_year)
    return min(direct, DAYS_PER_SEASONAL_CYCLE - direct)


def _seasonal_window_mask(days_of_year: NDArray[numpy.int64], target_day_of_year: int) -> NDArray[numpy.bool_]:
    direct = numpy.abs(days_of_year - target_day_of_year)
    circular = numpy.minimum(direct, DAYS_PER_SEASONAL_CYCLE - direct)
    return numpy.asarray(circular <= SEASONAL_WINDOW_DAYS, dtype=numpy.bool_)


def _build_climatology(
    days_of_year: NDArray[numpy.int64],
    values: NDArray[numpy.float64],
) -> tuple[NDArray[numpy.float64], NDArray[numpy.int64]]:
    climatology = numpy.full(DAYS_PER_SEASONAL_CYCLE, numpy.nan, dtype=numpy.float64)
    sample_counts = numpy.zeros(DAYS_PER_SEASONAL_CYCLE, dtype=numpy.int64)
    for target_day_of_year in range(1, DAYS_PER_SEASONAL_CYCLE + 1):
        window = _seasonal_window_mask(days_of_year, target_day_of_year)
        sample_count = int(window.sum())
        sample_counts[target_day_of_year - 1] = sample_count
        if sample_count >= MIN_CLIMATOLOGY_SAMPLES:
            climatology[target_day_of_year - 1] = float(values[window].mean())
    return climatology, sample_counts


def _estimate_daily_persistence(
    observed_days: tuple[date, ...],
    anomalies: NDArray[numpy.float64],
) -> tuple[float, float, int, float]:
    leading: list[float] = []
    trailing: list[float] = []
    gaps: list[int] = []
    for index in range(1, len(observed_days)):
        gap_days = (observed_days[index] - observed_days[index - 1]).days
        if 1 <= gap_days <= MAX_AUTOCORRELATION_GAP_DAYS:
            leading.append(float(anomalies[index - 1]))
            trailing.append(float(anomalies[index]))
            gaps.append(gap_days)
    pair_count = len(gaps)
    if pair_count < MIN_AUTOCORRELATION_PAIRS:
        raise InsufficientSignalHistoryError(
            "autocorrelation_pairs_below_minimum",
            f"{pair_count} usable anomaly pairs below the required {MIN_AUTOCORRELATION_PAIRS}",
        )
    leading_array = numpy.asarray(leading, dtype=numpy.float64)
    trailing_array = numpy.asarray(trailing, dtype=numpy.float64)
    if float(leading_array.std()) == 0.0 or float(trailing_array.std()) == 0.0:
        raise InsufficientSignalHistoryError(
            "anomaly_pool_has_no_spread",
            "the series' own anomaly pool is degenerate, so no honest band can be simulated",
        )
    correlation = float(numpy.corrcoef(leading_array, trailing_array)[0, 1])
    if not math.isfinite(correlation):
        raise InsufficientSignalHistoryError(
            "anomaly_autocorrelation_not_finite", "the series' own anomaly autocorrelation is not finite"
        )
    mean_gap_days = float(numpy.asarray(gaps, dtype=numpy.float64).mean())
    clamped = min(max(correlation, 0.0), MAX_DAILY_PERSISTENCE)
    return clamped ** (1.0 / mean_gap_days), correlation, pair_count, mean_gap_days


def eligible_history(observations: tuple[ObservedSignalDay, ...], cutoff_day: date) -> tuple[ObservedSignalDay, ...]:
    """Return the leakage-free history: publisher-named days at or before the cutoff, in day order."""
    kept = [row for row in observations if row.observed_day <= cutoff_day]
    kept.sort(key=lambda row: row.observed_day)
    observed_days = [row.observed_day for row in kept]
    if len(set(observed_days)) != len(observed_days):
        raise InsufficientSignalHistoryError(
            "duplicate_observed_day", "the governed history carries more than one row for a publisher-named day"
        )
    return tuple(kept)


def build_seasonal_history(observations: tuple[ObservedSignalDay, ...], cutoff_day: date) -> SeasonalHistory:
    """Reduce a series' own governed history at or before the cutoff day to the additive-anomaly method's state."""
    kept = eligible_history(observations, cutoff_day)
    if len(kept) < MIN_TRAINING_DAYS:
        raise InsufficientSignalHistoryError(
            "training_days_below_minimum",
            f"{len(kept)} observed days at or before {cutoff_day.isoformat()} below the required {MIN_TRAINING_DAYS}",
        )
    observed_days = tuple(row.observed_day for row in kept)
    values = numpy.asarray([row.value for row in kept], dtype=numpy.float64)
    days_of_year = numpy.asarray([day_of_year_index(day) for day in observed_days], dtype=numpy.int64)
    climatology, sample_counts = _build_climatology(days_of_year, values)
    supported = ~numpy.isnan(climatology[days_of_year - 1])
    if int(supported.sum()) < MIN_TRAINING_DAYS:
        raise InsufficientSignalHistoryError(
            "seasonally_referenced_days_below_minimum",
            f"{int(supported.sum())} of {len(kept)} observed days have a supported seasonal window, "
            f"below the required {MIN_TRAINING_DAYS}",
        )
    supported_days = tuple(day for day, keep in zip(observed_days, supported.tolist(), strict=True) if keep)
    supported_values = values[supported]
    supported_days_of_year = days_of_year[supported]
    anomalies = supported_values - climatology[supported_days_of_year - 1]
    daily_persistence, correlation, pair_count, mean_gap_days = _estimate_daily_persistence(supported_days, anomalies)
    return SeasonalHistory(
        cutoff_day=cutoff_day,
        history_start_day=observed_days[0],
        governed_day_count=len(kept),
        training_day_count=len(supported_days),
        climatology_by_day_of_year=tuple(float(value) for value in climatology),
        climatology_sample_counts=tuple(int(count) for count in sample_counts),
        anomaly_values=tuple(float(value) for value in anomalies),
        anomaly_days_of_year=tuple(int(value) for value in supported_days_of_year),
        latest_observed_day=observed_days[-1],
        latest_observed_value=float(values[-1]),
        anchor_day=supported_days[-1],
        anchor_anomaly=float(anomalies[-1]),
        lag_one_autocorrelation=correlation,
        autocorrelation_pair_count=pair_count,
        mean_observation_gap_days=mean_gap_days,
        daily_persistence=daily_persistence,
    )


def build_seasonal_value_pool(observations: tuple[ObservedSignalDay, ...], cutoff_day: date) -> SeasonalValuePool:
    """Reduce a series' own governed history at or before the cutoff day to the empirical-resample method's state."""
    kept = eligible_history(observations, cutoff_day)
    if len(kept) < MIN_TRAINING_DAYS:
        raise InsufficientSignalHistoryError(
            "training_days_below_minimum",
            f"{len(kept)} observed days at or before {cutoff_day.isoformat()} below the required {MIN_TRAINING_DAYS}",
        )
    observed_days = tuple(row.observed_day for row in kept)
    values = tuple(row.value for row in kept)
    return SeasonalValuePool(
        cutoff_day=cutoff_day,
        history_start_day=observed_days[0],
        governed_day_count=len(kept),
        days_of_year=tuple(day_of_year_index(day) for day in observed_days),
        values=values,
        latest_observed_day=observed_days[-1],
        latest_observed_value=values[-1],
    )


def climatology_baseline(history: SeasonalHistory, valid_day: date) -> float:
    """Return the seasonal-naive baseline: the series' own day-of-year climatology, NaN where unsupported."""
    return history.climatology_by_day_of_year[day_of_year_index(valid_day) - 1]


def history_checksum(observations: tuple[ObservedSignalDay, ...], cutoff_day: date, spec: SignalSeriesSpec) -> str:
    """Return a sha256 digest over the ordered governed history one simulation consumed."""
    parts = [
        "|".join((row.observed_day.isoformat(), format(row.value, FLOAT_FINGERPRINT_FORMAT), row.observation_checksum))
        for row in eligible_history(observations, cutoff_day)
    ]
    canonical = "|".join(("signal_history_v1", spec.signal_name, spec.normalized_unit, spec.support_key, *parts))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_parameter_text(
    *,
    series_key: str,
    spec: SignalSeriesSpec,
    issued_on: date,
    governed_history_checksum: str,
    request: SimulationRequest,
) -> str:
    """Return the pinned canonical parameter text whose digest identifies one simulation exactly."""
    method_name = (
        METHOD_NAME_EMPIRICAL_RESAMPLE
        if spec.bootstrap_kind is BootstrapKind.EMPIRICAL_SEASONAL_RESAMPLE
        else METHOD_NAME_ADDITIVE_ANOMALY
    )
    fields = (
        method_name,
        RNG_FINGERPRINT,
        series_key,
        spec.signal_name,
        spec.normalized_unit,
        spec.support_key,
        governed_history_checksum,
        issued_on.isoformat(),
        str(request.horizon_days),
        str(request.simulation_count),
        str(request.seed),
        str(SEASONAL_WINDOW_DAYS),
        str(MIN_CLIMATOLOGY_SAMPLES),
        str(MIN_INNOVATION_SAMPLES),
        str(MIN_TRAINING_DAYS),
        "p10_p50_p90",
    )
    return "|".join(fields)


def parameter_checksum(canonical_text: str) -> str:
    """Return the sha256 hex digest that identifies one simulation's parameters and its inputs."""
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _innovation_pools(
    history: SeasonalHistory,
    valid_days: tuple[date, ...],
) -> tuple[NDArray[numpy.float64], NDArray[numpy.int64]]:
    anomaly_days_of_year = numpy.asarray(history.anomaly_days_of_year, dtype=numpy.int64)
    anomaly_values = numpy.asarray(history.anomaly_values, dtype=numpy.float64)
    pools: list[NDArray[numpy.float64]] = []
    for valid_day in valid_days:
        pool = anomaly_values[_seasonal_window_mask(anomaly_days_of_year, day_of_year_index(valid_day))]
        if pool.size < MIN_INNOVATION_SAMPLES:
            raise InsufficientSignalHistoryError(
                "innovation_pool_below_minimum",
                f"{pool.size} seasonal anomalies for {valid_day.isoformat()} "
                f"below the required {MIN_INNOVATION_SAMPLES}",
            )
        pools.append(pool)
    pool_sizes = numpy.asarray([pool.size for pool in pools], dtype=numpy.int64)
    padded = numpy.zeros((len(pools), int(pool_sizes.max())), dtype=numpy.float64)
    for index, pool in enumerate(pools):
        padded[index, : pool.size] = pool
    return padded, pool_sizes


def _value_pools(
    pool: SeasonalValuePool,
    valid_days: tuple[date, ...],
) -> tuple[NDArray[numpy.float64], NDArray[numpy.int64]]:
    days_of_year = numpy.asarray(pool.days_of_year, dtype=numpy.int64)
    values = numpy.asarray(pool.values, dtype=numpy.float64)
    windows: list[NDArray[numpy.float64]] = []
    for valid_day in valid_days:
        window = values[_seasonal_window_mask(days_of_year, day_of_year_index(valid_day))]
        if window.size < MIN_INNOVATION_SAMPLES:
            raise InsufficientSignalHistoryError(
                "seasonal_value_pool_below_minimum",
                f"{window.size} seasonal values for {valid_day.isoformat()} "
                f"below the required {MIN_INNOVATION_SAMPLES}",
            )
        windows.append(window)
    pool_sizes = numpy.asarray([window.size for window in windows], dtype=numpy.int64)
    padded = numpy.zeros((len(windows), int(pool_sizes.max())), dtype=numpy.float64)
    for index, window in enumerate(windows):
        padded[index, : window.size] = window
    return padded, pool_sizes


def _quantile_rows(draws: NDArray[numpy.float64], valid_days: tuple[date, ...]) -> tuple[ForecastQuantileRow, ...]:
    quantile_matrix = numpy.percentile(
        draws, [quantile * 100.0 for quantile in PUBLISHED_QUANTILES], axis=0, method="linear"
    )
    rows: list[ForecastQuantileRow] = []
    for step, valid_day in enumerate(valid_days):
        rows.extend(
            ForecastQuantileRow(
                horizon_step=step + 1,
                valid_day=valid_day,
                quantile=quantile,
                value=float(quantile_matrix[quantile_index, step]),
            )
            for quantile_index, quantile in enumerate(PUBLISHED_QUANTILES)
        )
    return tuple(rows)


def simulate_additive_anomaly_quantiles(
    *, history: SeasonalHistory, spec: SignalSeriesSpec, request: SimulationRequest
) -> tuple[ForecastQuantileRow, ...]:
    """Simulate per-horizon quantiles from the series' own seasonally matched anomaly pool, then clip."""
    valid_days = tuple(history.cutoff_day + timedelta(days=step) for step in range(1, request.horizon_days + 1))
    seasonal_levels = numpy.asarray([climatology_baseline(history, day) for day in valid_days], dtype=numpy.float64)
    if bool(numpy.isnan(seasonal_levels).any()):
        raise InsufficientSignalHistoryError(
            "climatology_window_unsupported",
            "at least one horizon day has fewer seasonal neighbours than the climatology minimum",
        )
    padded_pools, pool_sizes = _innovation_pools(history, valid_days)
    anchor_gap_days = (history.cutoff_day - history.anchor_day).days
    horizon_offsets = numpy.arange(1, request.horizon_days + 1, dtype=numpy.float64)
    decay = numpy.power(history.daily_persistence, anchor_gap_days + horizon_offsets)
    innovation_scale = numpy.sqrt(numpy.maximum(1.0 - numpy.square(decay), 0.0))
    rng = Generator(PCG64(request.seed))
    draws = numpy.empty((request.simulation_count, request.horizon_days), dtype=numpy.float64)
    for step in range(request.horizon_days):
        indices = rng.integers(0, int(pool_sizes[step]), size=request.simulation_count)
        draws[:, step] = padded_pools[step, indices]
    paths = seasonal_levels + decay * history.anchor_anomaly + innovation_scale * draws
    lower = spec.lower_bound if spec.lower_bound is not None else -numpy.inf
    upper = spec.upper_bound if spec.upper_bound is not None else numpy.inf
    bounded = numpy.clip(paths, lower, upper)
    return _quantile_rows(bounded, valid_days)


def simulate_empirical_resample_quantiles(
    *, pool: SeasonalValuePool, request: SimulationRequest
) -> tuple[ForecastQuantileRow, ...]:
    """Simulate per-horizon quantiles by resampling seasonally matched REAL historical values.

    No climatology mean, no additive anomaly, no clipping: every draw is a value the record
    actually produced at that time of year, so a physically impossible draw (e.g. negative
    precipitation) cannot occur by construction.
    """
    valid_days = tuple(pool.cutoff_day + timedelta(days=step) for step in range(1, request.horizon_days + 1))
    padded_pools, pool_sizes = _value_pools(pool, valid_days)
    rng = Generator(PCG64(request.seed))
    draws = numpy.empty((request.simulation_count, request.horizon_days), dtype=numpy.float64)
    for step in range(request.horizon_days):
        indices = rng.integers(0, int(pool_sizes[step]), size=request.simulation_count)
        draws[:, step] = padded_pools[step, indices]
    return _quantile_rows(draws, valid_days)


def simulate_signal_forecast(
    *,
    spec: SignalSeriesSpec,
    observations: tuple[ObservedSignalDay, ...],
    issued_on: date,
    request: SimulationRequest,
    series_key: str,
) -> SignalForecastRun:
    """Simulate one cell-signal's 30-day forecast, dispatching on the series' own bootstrap kind.

    `issued_on` doubles as the leakage cutoff -- only observations at or before it are used -- so
    it is simultaneously the forecast's declared issue day and the boundary that makes the run
    time-honest. Every signal is forecast independently: this function never sees another signal's
    series, matching docs/lanes/weather-observations.md section 7's "do not attempt one joint
    model over all 19."
    """
    if request.horizon_days < 1:
        raise ValueError("horizon days must be positive")
    if request.simulation_count < 1:
        raise ValueError("simulation count must be positive")
    if spec.bootstrap_kind is BootstrapKind.EMPIRICAL_SEASONAL_RESAMPLE:
        pool = build_seasonal_value_pool(observations, issued_on)
        rows = simulate_empirical_resample_quantiles(pool=pool, request=request)
        method_name = METHOD_NAME_EMPIRICAL_RESAMPLE
    else:
        history = build_seasonal_history(observations, issued_on)
        rows = simulate_additive_anomaly_quantiles(history=history, spec=spec, request=request)
        method_name = METHOD_NAME_ADDITIVE_ANOMALY
    checksum = history_checksum(observations, issued_on, spec)
    canonical_text = canonical_parameter_text(
        series_key=series_key, spec=spec, issued_on=issued_on, governed_history_checksum=checksum, request=request
    )
    return SignalForecastRun(
        forecast_run_id=parameter_checksum(canonical_text),
        random_seed=request.seed,
        ensemble_size=request.simulation_count,
        horizon_days=request.horizon_days,
        issued_on=issued_on,
        signal_name=spec.signal_name,
        normalized_unit=spec.normalized_unit,
        support_key=spec.support_key,
        method_name=method_name,
        rows=rows,
    )
