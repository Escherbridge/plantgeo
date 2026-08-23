"""Deterministic Monte Carlo forecast for the sensors lane's day-grain NWS readings.

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
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import numpy
from numpy.random import PCG64

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

METHOD_NAME: Final = "sensors_ar1_residual_bootstrap_v1"

# Tied directly to the 30-day horizon this lane is chartered to forecast (docs/lanes/sensors.md
# section 7): refusing to extrapolate further into the future than the lane has ever observed
# into the past. As of this module's writing the warehouse holds roughly three weeks of history
# (docs/lanes/sensors.md section 3), so this gate is EXPECTED to refuse for virtually every
# station-measurement pair today; it starts passing once the producer (running since 2026-08-04)
# has accumulated 30 observed days for a given station-measurement, on its own, with no backfill
# possible past NWS's ~6-day retention.
MIN_HISTORY_DAYS: Final = 30
MAX_HORIZON_DAYS: Final = 30
MIN_ENSEMBLE_SIZE: Final = 100
MIN_RESIDUAL_PAIRS: Final = 20
MAX_AUTOCORRELATION_GAP_DAYS: Final = 5
MAX_DAILY_PERSISTENCE: Final = 0.98
MIN_WET_DAY_SAMPLES: Final = 5
LOW_QUANTILE: Final = 0.1
MEDIAN_QUANTILE: Final = 0.5
HIGH_QUANTILE: Final = 0.9
QUANTILES: Final[tuple[float, ...]] = (LOW_QUANTILE, MEDIAN_QUANTILE, HIGH_QUANTILE)
FLOAT_FINGERPRINT_FORMAT: Final = ".17g"
_SEED_MASK: Final = (1 << 63) - 1
_UINT64_SPAN: Final = float(1 << 64)


class MeasurementFamily(StrEnum):
    """The four treatments the sixteen NWS measurement fields are dispatched across."""

    TEMPERATURE_LIKE = "temperature_like"
    BOUNDED_NONNEGATIVE = "bounded_nonnegative"
    ZERO_INFLATED_NONNEGATIVE = "zero_inflated_nonnegative"
    CIRCULAR_UNSUPPORTED = "circular_unsupported"


# Every one of `OBSERVATION_MEASUREMENTS` (ingest/sensors.py:104-121), classified once. Not
# imported from `ingest.sensors` -- that module is not part of this lane's `method`/`pipeline`
# lattice slice, and this classification is this lane's own modelling decision, not a fact the
# ingest puller asserts.
_MEASUREMENT_FAMILIES: Final[dict[str, MeasurementFamily]] = {
    "temperature": MeasurementFamily.TEMPERATURE_LIKE,
    "dewpoint": MeasurementFamily.TEMPERATURE_LIKE,
    "windChill": MeasurementFamily.TEMPERATURE_LIKE,
    "heatIndex": MeasurementFamily.TEMPERATURE_LIKE,
    "maxTemperatureLast24Hours": MeasurementFamily.TEMPERATURE_LIKE,
    "minTemperatureLast24Hours": MeasurementFamily.TEMPERATURE_LIKE,
    "relativeHumidity": MeasurementFamily.BOUNDED_NONNEGATIVE,
    "windSpeed": MeasurementFamily.BOUNDED_NONNEGATIVE,
    "windGust": MeasurementFamily.BOUNDED_NONNEGATIVE,
    "barometricPressure": MeasurementFamily.BOUNDED_NONNEGATIVE,
    "seaLevelPressure": MeasurementFamily.BOUNDED_NONNEGATIVE,
    "visibility": MeasurementFamily.BOUNDED_NONNEGATIVE,
    "precipitationLastHour": MeasurementFamily.ZERO_INFLATED_NONNEGATIVE,
    "precipitationLast3Hours": MeasurementFamily.ZERO_INFLATED_NONNEGATIVE,
    "precipitationLast6Hours": MeasurementFamily.ZERO_INFLATED_NONNEGATIVE,
    "windDirection": MeasurementFamily.CIRCULAR_UNSUPPORTED,
}

# Physical floors/ceilings for BOUNDED_NONNEGATIVE fields. Absent from this map means "floor at
# 0.0, no ceiling" -- the common case for pressures, speeds and visibility.
_UPPER_BOUNDS: Final[dict[str, float]] = {"relativeHumidity": 100.0}


class SensorForecastRefusalReason(StrEnum):
    """Every way this module refuses to fabricate a forecast, named so a caller can branch on it."""

    UNSUPPORTED_MEASUREMENT_FAMILY = "unsupported_measurement_family"
    HISTORY_BELOW_MINIMUM = "history_below_minimum"
    DUPLICATE_OBSERVED_DAY = "duplicate_observed_day"
    RESIDUAL_PAIRS_BELOW_MINIMUM = "residual_pairs_below_minimum"
    DEGENERATE_RESIDUAL_POOL = "degenerate_residual_pool"
    WET_DAY_SAMPLES_BELOW_MINIMUM = "wet_day_samples_below_minimum"
    INVALID_REQUEST = "invalid_request"


class InsufficientSensorHistoryError(ValueError):
    """The typed refusal: raised instead of returning an empty forecast array.

    A caller catching this gets `.reason` (a closed `SensorForecastRefusalReason`), `.sensor_id`
    and `.measurement_name` -- enough to log or surface "no forecast for KMSO/temperature: only
    12 of 30 required days observed" rather than silently receiving zero rows indistinguishable
    from "this ran and found nothing to say."
    """

    def __init__(
        self,
        reason: SensorForecastRefusalReason,
        detail: str,
        *,
        sensor_id: str,
        measurement_name: str,
    ) -> None:
        super().__init__(f"{sensor_id}/{measurement_name}: {reason.value}: {detail}")
        self.reason = reason
        self.detail = detail
        self.sensor_id = sensor_id
        self.measurement_name = measurement_name


@dataclass(frozen=True, slots=True)
class ObservedReading:
    """One publisher-named day's reading for one (sensor_id, measurement_name) series."""

    observed_day: date
    value: float
    unit_code: str | None


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """Bounded simulation parameters. The seed is never taken from here -- see `derive_seed`."""

    horizon_days: int
    ensemble_size: int


@dataclass(frozen=True, slots=True)
class ContinuousSensorHistory:
    """Reduced history for a TEMPERATURE_LIKE or BOUNDED_NONNEGATIVE series."""

    sensor_id: str
    measurement_name: str
    family: MeasurementFamily
    unit_code: str | None
    cutoff_day: date
    history_start_day: date
    observed_day_count: int
    mean_level: float
    residual_pool: tuple[float, ...]
    anchor_day: date
    anchor_residual: float
    daily_persistence: float
    lag_one_autocorrelation: float
    residual_pair_count: int
    mean_observation_gap_days: float


@dataclass(frozen=True, slots=True)
class ZeroInflatedSensorHistory:
    """Reduced history for a ZERO_INFLATED_NONNEGATIVE series (the precipitation windows)."""

    sensor_id: str
    measurement_name: str
    family: MeasurementFamily
    unit_code: str | None
    cutoff_day: date
    history_start_day: date
    observed_day_count: int
    wet_day_count: int
    wet_probability: float
    positive_value_pool: tuple[float, ...]


SensorHistory = ContinuousSensorHistory | ZeroInflatedSensorHistory


@dataclass(frozen=True, slots=True)
class SensorForecastRow:
    """One quantile of one horizon step of one (sensor_id, measurement_name) forecast run.

    Carries every provenance column `conductor/code_styleguides/layer-lanes.md` section 3
    requires of a `kind=forecast` row: `forecast_run_id`, `random_seed`, `ensemble_size`,
    `horizon_days`, `issued_on`, and `quantile`. `valid_day` is the day this row projects, i.e.
    the `observed_day` a `kind=forecast` partition would file this row under.
    """

    sensor_id: str
    measurement_name: str
    measurement_family: MeasurementFamily
    unit_code: str | None
    valid_day: date
    horizon_step: int
    quantile: float
    value: float
    forecast_run_id: str
    random_seed: int
    ensemble_size: int
    horizon_days: int
    issued_on: date


def measurement_family(measurement_name: str) -> MeasurementFamily:
    """Return the treatment family for one of the sixteen captured NWS measurement fields, or raise."""
    try:
        return _MEASUREMENT_FAMILIES[measurement_name]
    except KeyError as exc:
        raise ValueError(
            f"{measurement_name!r} is not one of the sixteen captured NWS measurement fields"
        ) from exc


def eligible_history(
    observations: tuple[ObservedReading, ...],
    cutoff_day: date,
) -> tuple[ObservedReading, ...]:
    """Return the leakage-free history: publisher-named days at or before the cutoff, in day order."""
    kept = [row for row in observations if row.observed_day <= cutoff_day]
    kept.sort(key=lambda row: row.observed_day)
    return tuple(kept)


def history_checksum(observations: tuple[ObservedReading, ...], cutoff_day: date) -> str:
    """Return a sha256 digest over the ordered governed history one build/simulate call consumed."""
    parts = [
        "|".join((row.observed_day.isoformat(), format(row.value, FLOAT_FINGERPRINT_FORMAT), row.unit_code or ""))
        for row in eligible_history(observations, cutoff_day)
    ]
    canonical = "|".join((f"{METHOD_NAME}_history_v1", *parts))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def derive_seed(canonical_text: str) -> int:
    """Return a reproducible, schema-friendly (non-negative 63-bit) seed from one run's own fingerprint.

    Deliberately not caller-supplied: the seed is a pure function of the observations, the
    cutoff, and the request, so re-running the same inputs always reproduces the same seed with
    no external nonce to lose track of.
    """
    digest = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) & _SEED_MASK


def _validate_request(request: SimulationRequest) -> None:
    if not (1 <= request.horizon_days <= MAX_HORIZON_DAYS):
        raise ValueError(f"horizon_days must be between 1 and {MAX_HORIZON_DAYS}, got {request.horizon_days}")
    if request.ensemble_size < MIN_ENSEMBLE_SIZE:
        raise ValueError(f"ensemble_size must be at least {MIN_ENSEMBLE_SIZE}, got {request.ensemble_size}")


def _require_unique_days(
    kept: tuple[ObservedReading, ...],
    *,
    sensor_id: str,
    measurement_name: str,
) -> None:
    observed_days = [row.observed_day for row in kept]
    if len(set(observed_days)) != len(observed_days):
        raise InsufficientSensorHistoryError(
            SensorForecastRefusalReason.DUPLICATE_OBSERVED_DAY,
            "the governed history carries more than one row for a publisher-named day",
            sensor_id=sensor_id,
            measurement_name=measurement_name,
        )


def _consecutive_day_pairs(
    kept: tuple[ObservedReading, ...],
    residuals: NDArray[numpy.float64],
) -> tuple[NDArray[numpy.float64], NDArray[numpy.float64], NDArray[numpy.float64]]:
    """Return (leading, trailing, gap_days) over consecutive-enough observed-day pairs."""
    leading: list[float] = []
    trailing: list[float] = []
    gaps: list[float] = []
    for index in range(1, len(kept)):
        gap_days = (kept[index].observed_day - kept[index - 1].observed_day).days
        if 1 <= gap_days <= MAX_AUTOCORRELATION_GAP_DAYS:
            leading.append(float(residuals[index - 1]))
            trailing.append(float(residuals[index]))
            gaps.append(float(gap_days))
    return (
        numpy.asarray(leading, dtype=numpy.float64),
        numpy.asarray(trailing, dtype=numpy.float64),
        numpy.asarray(gaps, dtype=numpy.float64),
    )


def build_continuous_history(
    *,
    sensor_id: str,
    measurement_name: str,
    family: MeasurementFamily,
    observations: tuple[ObservedReading, ...],
    cutoff_day: date,
) -> ContinuousSensorHistory:
    """Reduce a TEMPERATURE_LIKE/BOUNDED_NONNEGATIVE series to a level, a residual pool and a persistence anchor."""
    kept = eligible_history(observations, cutoff_day)
    if len(kept) < MIN_HISTORY_DAYS:
        raise InsufficientSensorHistoryError(
            SensorForecastRefusalReason.HISTORY_BELOW_MINIMUM,
            f"{len(kept)} observed days at or before {cutoff_day.isoformat()}, "
            f"below the required {MIN_HISTORY_DAYS}",
            sensor_id=sensor_id,
            measurement_name=measurement_name,
        )
    _require_unique_days(kept, sensor_id=sensor_id, measurement_name=measurement_name)
    values = numpy.asarray([row.value for row in kept], dtype=numpy.float64)
    mean_level = float(values.mean())
    residuals = values - mean_level
    leading, trailing, gaps = _consecutive_day_pairs(kept, residuals)
    if leading.size < MIN_RESIDUAL_PAIRS:
        raise InsufficientSensorHistoryError(
            SensorForecastRefusalReason.RESIDUAL_PAIRS_BELOW_MINIMUM,
            f"{leading.size} usable day-over-day pairs below the required {MIN_RESIDUAL_PAIRS}",
            sensor_id=sensor_id,
            measurement_name=measurement_name,
        )
    if float(leading.std()) == 0.0 or float(trailing.std()) == 0.0:
        raise InsufficientSensorHistoryError(
            SensorForecastRefusalReason.DEGENERATE_RESIDUAL_POOL,
            "the series' own residual pool is degenerate, so no honest band can be simulated",
            sensor_id=sensor_id,
            measurement_name=measurement_name,
        )
    correlation = float(numpy.corrcoef(leading, trailing)[0, 1])
    if not numpy.isfinite(correlation):
        raise InsufficientSensorHistoryError(
            SensorForecastRefusalReason.DEGENERATE_RESIDUAL_POOL,
            "the series' own residual autocorrelation is not finite",
            sensor_id=sensor_id,
            measurement_name=measurement_name,
        )
    mean_gap_days = float(gaps.mean())
    clamped_correlation = min(max(correlation, 0.0), MAX_DAILY_PERSISTENCE)
    daily_persistence = clamped_correlation ** (1.0 / mean_gap_days)
    unit_code = next((row.unit_code for row in reversed(kept) if row.unit_code is not None), None)
    return ContinuousSensorHistory(
        sensor_id=sensor_id,
        measurement_name=measurement_name,
        family=family,
        unit_code=unit_code,
        cutoff_day=cutoff_day,
        history_start_day=kept[0].observed_day,
        observed_day_count=len(kept),
        mean_level=mean_level,
        residual_pool=tuple(float(value) for value in residuals),
        anchor_day=kept[-1].observed_day,
        anchor_residual=float(residuals[-1]),
        daily_persistence=daily_persistence,
        lag_one_autocorrelation=correlation,
        residual_pair_count=int(leading.size),
        mean_observation_gap_days=mean_gap_days,
    )


def build_zero_inflated_history(
    *,
    sensor_id: str,
    measurement_name: str,
    observations: tuple[ObservedReading, ...],
    cutoff_day: date,
) -> ZeroInflatedSensorHistory:
    """Reduce a precipitation-window series to a wet-day probability and a positive-amount pool."""
    kept = eligible_history(observations, cutoff_day)
    if len(kept) < MIN_HISTORY_DAYS:
        raise InsufficientSensorHistoryError(
            SensorForecastRefusalReason.HISTORY_BELOW_MINIMUM,
            f"{len(kept)} observed days at or before {cutoff_day.isoformat()}, "
            f"below the required {MIN_HISTORY_DAYS}",
            sensor_id=sensor_id,
            measurement_name=measurement_name,
        )
    _require_unique_days(kept, sensor_id=sensor_id, measurement_name=measurement_name)
    values = numpy.asarray([row.value for row in kept], dtype=numpy.float64)
    wet_mask = values > 0.0
    wet_count = int(wet_mask.sum())
    if wet_count < MIN_WET_DAY_SAMPLES:
        raise InsufficientSensorHistoryError(
            SensorForecastRefusalReason.WET_DAY_SAMPLES_BELOW_MINIMUM,
            f"{wet_count} positive-reading days below the required {MIN_WET_DAY_SAMPLES}; "
            "cannot bootstrap a positive-amount pool without fabricating one",
            sensor_id=sensor_id,
            measurement_name=measurement_name,
        )
    unit_code = next((row.unit_code for row in reversed(kept) if row.unit_code is not None), None)
    return ZeroInflatedSensorHistory(
        sensor_id=sensor_id,
        measurement_name=measurement_name,
        family=MeasurementFamily.ZERO_INFLATED_NONNEGATIVE,
        unit_code=unit_code,
        cutoff_day=cutoff_day,
        history_start_day=kept[0].observed_day,
        observed_day_count=len(kept),
        wet_day_count=wet_count,
        wet_probability=wet_count / len(kept),
        positive_value_pool=tuple(float(value) for value in values[wet_mask]),
    )


def build_sensor_history(
    *,
    sensor_id: str,
    measurement_name: str,
    observations: tuple[ObservedReading, ...],
    cutoff_day: date,
) -> SensorHistory:
    """Dispatch to the right history reduction by measurement family, or refuse outright.

    `windDirection` (CIRCULAR_UNSUPPORTED) is refused before any history is even inspected: a
    compass bearing's wraparound makes both the additive-bootstrap and the non-negative-floor
    shapes this module knows meaningless, independent of how much history exists.
    """
    family = measurement_family(measurement_name)
    if family is MeasurementFamily.CIRCULAR_UNSUPPORTED:
        raise InsufficientSensorHistoryError(
            SensorForecastRefusalReason.UNSUPPORTED_MEASUREMENT_FAMILY,
            "windDirection is a circular quantity (wraps at 360); neither this module's additive "
            "bootstrap nor its non-negative floor is a meaningful treatment for it",
            sensor_id=sensor_id,
            measurement_name=measurement_name,
        )
    if family is MeasurementFamily.ZERO_INFLATED_NONNEGATIVE:
        return build_zero_inflated_history(
            sensor_id=sensor_id,
            measurement_name=measurement_name,
            observations=observations,
            cutoff_day=cutoff_day,
        )
    return build_continuous_history(
        sensor_id=sensor_id,
        measurement_name=measurement_name,
        family=family,
        observations=observations,
        cutoff_day=cutoff_day,
    )


def _apply_family_bounds(paths: NDArray[numpy.float64], history: ContinuousSensorHistory) -> NDArray[numpy.float64]:
    if history.family is MeasurementFamily.TEMPERATURE_LIKE:
        return paths
    upper = _UPPER_BOUNDS.get(history.measurement_name)
    return numpy.clip(paths, 0.0, upper if upper is not None else numpy.inf)


def _continuous_canonical_text(history: ContinuousSensorHistory, request: SimulationRequest) -> str:
    fields = (
        METHOD_NAME,
        history.sensor_id,
        history.measurement_name,
        history.family.value,
        history.cutoff_day.isoformat(),
        history.history_start_day.isoformat(),
        str(request.horizon_days),
        str(request.ensemble_size),
        format(history.mean_level, FLOAT_FINGERPRINT_FORMAT),
        format(history.daily_persistence, FLOAT_FINGERPRINT_FORMAT),
        format(history.anchor_residual, FLOAT_FINGERPRINT_FORMAT),
        history.anchor_day.isoformat(),
        str(history.observed_day_count),
        str(history.residual_pair_count),
        "pcg64_random_raw",
    )
    return "|".join(fields)


def simulate_continuous_forecast(
    history: ContinuousSensorHistory,
    request: SimulationRequest,
) -> tuple[SensorForecastRow, ...]:
    """Simulate p10/p50/p90 per horizon day for a TEMPERATURE_LIKE/BOUNDED_NONNEGATIVE series."""
    _validate_request(request)
    valid_days = tuple(history.cutoff_day + timedelta(days=step) for step in range(1, request.horizon_days + 1))
    residual_pool = numpy.asarray(history.residual_pool, dtype=numpy.float64)
    anchor_gap_days = float((history.cutoff_day - history.anchor_day).days)
    horizon_offsets = numpy.arange(1, request.horizon_days + 1, dtype=numpy.float64)
    decay = numpy.power(history.daily_persistence, anchor_gap_days + horizon_offsets)
    innovation_scale = numpy.sqrt(numpy.maximum(1.0 - numpy.square(decay), 0.0))

    canonical_text = _continuous_canonical_text(history, request)
    forecast_run_id = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    seed = derive_seed(canonical_text)
    raw_draws = PCG64(seed).random_raw(request.ensemble_size * request.horizon_days)
    draw_matrix = numpy.asarray(raw_draws, dtype=numpy.uint64).reshape(request.ensemble_size, request.horizon_days)
    pool_indices = numpy.mod(draw_matrix, residual_pool.size).astype(numpy.intp)
    innovations = residual_pool[pool_indices]

    paths = history.mean_level + decay * history.anchor_residual + innovation_scale * innovations
    bounded = _apply_family_bounds(paths, history)
    quantile_matrix = numpy.percentile(bounded, [quantile * 100.0 for quantile in QUANTILES], axis=0, method="linear")
    return _rows_from_quantile_matrix(
        sensor_id=history.sensor_id,
        measurement_name=history.measurement_name,
        family=history.family,
        unit_code=history.unit_code,
        issued_on=history.cutoff_day,
        valid_days=valid_days,
        quantile_matrix=quantile_matrix,
        forecast_run_id=forecast_run_id,
        random_seed=seed,
        request=request,
    )


def _zero_inflated_canonical_text(history: ZeroInflatedSensorHistory, request: SimulationRequest) -> str:
    fields = (
        METHOD_NAME,
        "zero_inflated_v1",
        history.sensor_id,
        history.measurement_name,
        history.cutoff_day.isoformat(),
        history.history_start_day.isoformat(),
        str(request.horizon_days),
        str(request.ensemble_size),
        format(history.wet_probability, FLOAT_FINGERPRINT_FORMAT),
        str(history.wet_day_count),
        str(history.observed_day_count),
        "pcg64_random_raw",
    )
    return "|".join(fields)


def simulate_zero_inflated_forecast(
    history: ZeroInflatedSensorHistory,
    request: SimulationRequest,
) -> tuple[SensorForecastRow, ...]:
    """Simulate p10/p50/p90 per horizon day for a precipitation-window series.

    Each horizon day is drawn independently: a Bernoulli "did it rain" at the history's own wet
    probability, then a bootstrap amount from the history's own positive-reading pool on a wet
    draw, else exactly 0.0. No day-to-day wet/dry persistence is modelled -- see the module
    docstring for why fitting one from three weeks of history would be a fabricated default.
    """
    _validate_request(request)
    valid_days = tuple(history.cutoff_day + timedelta(days=step) for step in range(1, request.horizon_days + 1))
    amount_pool = numpy.asarray(history.positive_value_pool, dtype=numpy.float64)
    draw_count = request.ensemble_size * request.horizon_days

    canonical_text = _zero_inflated_canonical_text(history, request)
    forecast_run_id = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    seed = derive_seed(canonical_text)
    # Two independent PCG64 streams derived from the one recorded, reproducible `seed` -- the
    # occurrence draw and the amount draw must not share a bitstream, or a wet day would always
    # pair with the same rank-ordered amount index.
    wet_raw = numpy.asarray(PCG64(seed).random_raw(draw_count), dtype=numpy.uint64)
    amount_raw = numpy.asarray(PCG64(seed + 1).random_raw(draw_count), dtype=numpy.uint64)
    wet_uniform = wet_raw.astype(numpy.float64) / _UINT64_SPAN
    is_wet = wet_uniform.reshape(request.ensemble_size, request.horizon_days) < history.wet_probability
    amount_indices = numpy.mod(amount_raw, amount_pool.size).astype(numpy.intp)
    amounts = amount_pool[amount_indices].reshape(request.ensemble_size, request.horizon_days)

    paths = numpy.where(is_wet, amounts, 0.0)
    quantile_matrix = numpy.percentile(paths, [quantile * 100.0 for quantile in QUANTILES], axis=0, method="linear")
    return _rows_from_quantile_matrix(
        sensor_id=history.sensor_id,
        measurement_name=history.measurement_name,
        family=history.family,
        unit_code=history.unit_code,
        issued_on=history.cutoff_day,
        valid_days=valid_days,
        quantile_matrix=quantile_matrix,
        forecast_run_id=forecast_run_id,
        random_seed=seed,
        request=request,
    )


def _rows_from_quantile_matrix(  # noqa: PLR0913
    *,
    sensor_id: str,
    measurement_name: str,
    family: MeasurementFamily,
    unit_code: str | None,
    issued_on: date,
    valid_days: tuple[date, ...],
    quantile_matrix: NDArray[numpy.float64],
    forecast_run_id: str,
    random_seed: int,
    request: SimulationRequest,
) -> tuple[SensorForecastRow, ...]:
    rows: list[SensorForecastRow] = []
    for step_index, valid_day in enumerate(valid_days):
        for quantile_index, quantile in enumerate(QUANTILES):
            rows.append(
                SensorForecastRow(
                    sensor_id=sensor_id,
                    measurement_name=measurement_name,
                    measurement_family=family,
                    unit_code=unit_code,
                    valid_day=valid_day,
                    horizon_step=step_index + 1,
                    quantile=quantile,
                    value=float(quantile_matrix[quantile_index, step_index]),
                    forecast_run_id=forecast_run_id,
                    random_seed=random_seed,
                    ensemble_size=request.ensemble_size,
                    horizon_days=request.horizon_days,
                    issued_on=issued_on,
                )
            )
    return tuple(rows)


def simulate_sensor_forecast(history: SensorHistory, request: SimulationRequest) -> tuple[SensorForecastRow, ...]:
    """Dispatch to the right simulator by history type; the single entry point a caller needs."""
    if isinstance(history, ZeroInflatedSensorHistory):
        return simulate_zero_inflated_forecast(history, request)
    return simulate_continuous_forecast(history, request)


def forecast_sensor_measurement(
    *,
    sensor_id: str,
    measurement_name: str,
    observations: tuple[ObservedReading, ...],
    cutoff_day: date,
    request: SimulationRequest,
) -> tuple[SensorForecastRow, ...]:
    """Build history and simulate in one call -- the convenience entry point for one series.

    Raises `InsufficientSensorHistoryError` (never returns an empty tuple) when this
    sensor/measurement's own history cannot honestly support a forecast. `observations` not
    already restricted to `<= cutoff_day` is fine: `eligible_history` enforces the leakage
    boundary internally.
    """
    history = build_sensor_history(
        sensor_id=sensor_id,
        measurement_name=measurement_name,
        observations=observations,
        cutoff_day=cutoff_day,
    )
    return simulate_sensor_forecast(history, request)


def required_measurement_names() -> Sequence[str]:
    """Return every measurement name this module has a family classification for, sorted."""
    return tuple(sorted(_MEASUREMENT_FAMILIES))
