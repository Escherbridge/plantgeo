"""Deterministic seasonal-hurdle Monte Carlo for the fire-detections cell-day aggregate.

Layer L1 (method/monte_carlo): pure domain computation, no I/O, no SQLAlchemy, no httpx. May NOT
import `agri_data_service.warehouse` (or `method.ml`) -- see `tests/test_layer_import_contract.py`.

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
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import numpy
from numpy.random import PCG64

if TYPE_CHECKING:
    from numpy.typing import NDArray

METHOD_NAME: Final = "fire_detections_seasonal_hurdle_bootstrap_v1"
PURPOSE_FORWARD_SIMULATION: Final = "forward_simulation"
PURPOSE_HOLDOUT_EVALUATION: Final = "holdout_evaluation"
GAP_POLICY: Final = "strict"

DAYS_PER_SEASONAL_CYCLE: Final = 366
SEASONAL_WINDOW_DAYS: Final = 15

# A seasonal ignition-probability estimate from under a year of monitoring is not a seasonal estimate
# at all -- it is one pass through the calendar with no repetition. 365 is the floor at which the
# hurdle stage's denominator can start to mean something.
MIN_MONITORED_DAY_COUNT: Final = 365
# The floor on how many MONITORED calendar days (not detection days) fall inside one horizon day's
# +/-15-day-of-year window, summed across every year of history. Below this the ratio is noise.
MIN_SEASONAL_WINDOW_MONITORED_DAYS: Final = 30
MIN_SIMULATION_COUNT: Final = 200

LOW_QUANTILE: Final = 0.1
MEDIAN_QUANTILE: Final = 0.5
HIGH_QUANTILE: Final = 0.9
_REPORTED_QUANTILES: Final[tuple[float, ...]] = (LOW_QUANTILE, MEDIAN_QUANTILE, HIGH_QUANTILE)

FLOAT_FINGERPRINT_FORMAT: Final = ".17g"
_UNIT_INTERVAL_DIVISOR: Final = float(2**64)


class InsufficientFireHistoryError(ValueError):
    """Raised when one cell's own governed history cannot support the method without fabrication."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ObservedCellDay:
    """One publisher-named cell-day this method may consume -- the exported grain, verbatim.

    `detection_count` is always >= 1: the exported stream never materializes a zero-detection row
    (`warehouse/schemas/fire_detections.py`), so every instance of this type IS a positive day, and
    the hurdle stage below is what stands in for the days this type structurally cannot represent.
    """

    observed_day: date
    detection_count: int
    frp_sum: float | None
    frp_observation_count: int
    high_confidence_detection_count: int
    observation_checksum: str

    def __post_init__(self) -> None:
        if self.detection_count < 1:
            raise ValueError("an exported fire-detections cell-day always carries at least one detection")
        if not 0 <= self.frp_observation_count <= self.detection_count:
            raise ValueError("frp_observation_count must be between 0 and detection_count")
        if not 0 <= self.high_confidence_detection_count <= self.detection_count:
            raise ValueError("high_confidence_detection_count must be between 0 and detection_count")
        if self.frp_sum is None and self.frp_observation_count != 0:
            raise ValueError("frp_observation_count without frp_sum would misrepresent an absent measurement")


@dataclass(frozen=True, slots=True)
class CellIgnitionHistory:
    """One cell's history reduced to a monitored-day calendar and its own positive-day pool."""

    cutoff_day: date
    history_start_day: date
    monitored_day_count: int
    positive_days: tuple[ObservedCellDay, ...]
    monitored_days_of_year: tuple[int, ...]
    positive_days_of_year: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CellDayForecastQuantile:
    """One quantile of one simulated horizon day for one cell -- the observed grain plus its provenance.

    Columns through `high_confidence_detection_count` share name, unit and meaning with
    `warehouse/schemas/fire_detections.py`'s observed row (layer-lanes.md section 2); the six
    provenance columns are the ones section 3 requires on every forecast row and that the observed
    schema does not carry (RUNBOOK: "a column that is unconditionally NULL is not provenance").
    """

    cell_longitude: float
    cell_latitude: float
    observed_day: date
    detection_count: int
    frp_sum: float | None
    frp_observation_count: int
    high_confidence_detection_count: int
    forecast_run_id: str
    random_seed: int
    ensemble_size: int
    horizon_days: int
    issued_on: date
    quantile: float


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """Bounded, reproducible simulation parameters, plus the run identity provenance must not borrow."""

    forecast_run_id: str
    horizon_days: int
    simulation_count: int
    seed: int

    def __post_init__(self) -> None:
        if not self.forecast_run_id.strip():
            raise ValueError("a forecast run must carry a non-blank forecast_run_id")
        if self.horizon_days < 1:
            raise ValueError("horizon days must be positive")
        if self.simulation_count < MIN_SIMULATION_COUNT:
            raise ValueError(
                f"simulation_count {self.simulation_count} is below the {MIN_SIMULATION_COUNT}-draw floor "
                f"{_REPORTED_QUANTILES} needs to mean anything; publish fewer quantiles before publishing "
                "from a smaller ensemble"
            )


def day_of_year_index(value: date) -> int:
    """Return the 1-based day of year used as the seasonal phase coordinate."""
    return value.timetuple().tm_yday


def _circular_day_distance(first_day_of_year: int, second_day_of_year: int) -> int:
    direct = abs(first_day_of_year - second_day_of_year)
    return min(direct, DAYS_PER_SEASONAL_CYCLE - direct)


def _seasonal_window_mask(days_of_year: NDArray[numpy.int64], target_day_of_year: int) -> NDArray[numpy.bool_]:
    direct = numpy.abs(days_of_year - target_day_of_year)
    circular = numpy.minimum(direct, DAYS_PER_SEASONAL_CYCLE - direct)
    return numpy.asarray(circular <= SEASONAL_WINDOW_DAYS, dtype=numpy.bool_)


def eligible_positive_days(
    observations: tuple[ObservedCellDay, ...], *, history_start_day: date, cutoff_day: date
) -> tuple[ObservedCellDay, ...]:
    """Return the leakage-free positive-day history: no day after `cutoff_day` survives, in day order.

    A day BEFORE `history_start_day` is a caller data inconsistency, not a normal filter -- silently
    dropping it would hide a wrong monitoring floor rather than surface it.
    """
    stale = [row for row in observations if row.observed_day < history_start_day]
    if stale:
        raise InsufficientFireHistoryError(
            "observation_before_monitored_window",
            f"{len(stale)} observed day(s) predate the declared history_start_day {history_start_day.isoformat()}",
        )
    kept = sorted((row for row in observations if row.observed_day <= cutoff_day), key=lambda row: row.observed_day)
    days = [row.observed_day for row in kept]
    if len(set(days)) != len(days):
        raise InsufficientFireHistoryError(
            "duplicate_observed_day", "the governed history carries more than one row for a publisher-named day"
        )
    return tuple(kept)


def build_cell_ignition_history(
    observations: tuple[ObservedCellDay, ...], *, history_start_day: date, cutoff_day: date
) -> CellIgnitionHistory:
    """Reduce one cell's own governed history to a monitored calendar and its positive-day pool.

    `history_start_day` MUST be the lane's own monitoring floor (e.g. the fire-detections lane's
    registered `history_floor`), never inferred from this cell's earliest detection -- a cell has no
    persistent identity before its first fire (`warehouse/schemas/fire_detections.py`'s "no such
    dimension exists" note), so the first detection day is not evidence monitoring began there; it is
    survivorship bias that would inflate every ignition-probability estimate computed from it.
    """
    if cutoff_day < history_start_day:
        raise InsufficientFireHistoryError(
            "cutoff_before_history_start", f"cutoff {cutoff_day.isoformat()} precedes {history_start_day.isoformat()}"
        )
    monitored_day_count = (cutoff_day - history_start_day).days + 1
    if monitored_day_count < MIN_MONITORED_DAY_COUNT:
        raise InsufficientFireHistoryError(
            "monitored_days_below_minimum",
            f"{monitored_day_count} monitored days below the required {MIN_MONITORED_DAY_COUNT}",
        )
    positive_days = eligible_positive_days(observations, history_start_day=history_start_day, cutoff_day=cutoff_day)
    monitored_days_of_year = tuple(
        day_of_year_index(history_start_day + timedelta(days=offset)) for offset in range(monitored_day_count)
    )
    positive_days_of_year = tuple(day_of_year_index(row.observed_day) for row in positive_days)
    return CellIgnitionHistory(
        cutoff_day=cutoff_day,
        history_start_day=history_start_day,
        monitored_day_count=monitored_day_count,
        positive_days=positive_days,
        monitored_days_of_year=monitored_days_of_year,
        positive_days_of_year=positive_days_of_year,
    )


def seasonal_ignition_probability(history: CellIgnitionHistory, valid_day: date) -> float:
    """Return P(>=1 detection) for `valid_day`'s seasonal window: positive days over monitored days.

    Both counts are taken from the SAME +/-15-day-of-year window, so the estimate is self-normalizing
    regardless of how many calendar years the history spans.
    """
    target = day_of_year_index(valid_day)
    monitored_mask = _seasonal_window_mask(numpy.asarray(history.monitored_days_of_year, dtype=numpy.int64), target)
    monitored_count = int(monitored_mask.sum())
    if monitored_count < MIN_SEASONAL_WINDOW_MONITORED_DAYS:
        raise InsufficientFireHistoryError(
            "seasonal_window_monitored_days_below_minimum",
            f"{monitored_count} monitored days near day-of-year {target} below the required "
            f"{MIN_SEASONAL_WINDOW_MONITORED_DAYS}",
        )
    if not history.positive_days_of_year:
        return 0.0
    positive_mask = _seasonal_window_mask(numpy.asarray(history.positive_days_of_year, dtype=numpy.int64), target)
    return float(positive_mask.sum()) / monitored_count


def seasonal_positive_pool(history: CellIgnitionHistory, valid_day: date) -> tuple[ObservedCellDay, ...]:
    """Return the historical positive days whose day-of-year falls in `valid_day`'s seasonal window."""
    if not history.positive_days_of_year:
        return ()
    target = day_of_year_index(valid_day)
    mask = _seasonal_window_mask(numpy.asarray(history.positive_days_of_year, dtype=numpy.int64), target)
    return tuple(row for row, keep in zip(history.positive_days, mask.tolist(), strict=True) if keep)


def history_checksum(observations: tuple[ObservedCellDay, ...], *, history_start_day: date, cutoff_day: date) -> str:
    """Return a sha256 digest over the ordered governed history that one simulation consumed."""
    parts = [
        "|".join(
            (
                row.observed_day.isoformat(),
                str(row.detection_count),
                "" if row.frp_sum is None else format(row.frp_sum, FLOAT_FINGERPRINT_FORMAT),
                str(row.frp_observation_count),
                str(row.high_confidence_detection_count),
                row.observation_checksum,
            )
        )
        for row in eligible_positive_days(observations, history_start_day=history_start_day, cutoff_day=cutoff_day)
    ]
    canonical = "|".join((f"{METHOD_NAME}_history_v1", history_start_day.isoformat(), cutoff_day.isoformat(), *parts))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_parameter_text(
    *,
    cell_longitude: float,
    cell_latitude: float,
    governed_history_checksum: str,
    history: CellIgnitionHistory,
    request: SimulationRequest,
) -> str:
    """Return the pinned canonical parameter text whose digest reproduces one simulation exactly."""
    fields = (
        METHOD_NAME,
        format(cell_longitude, FLOAT_FINGERPRINT_FORMAT),
        format(cell_latitude, FLOAT_FINGERPRINT_FORMAT),
        governed_history_checksum,
        history.cutoff_day.isoformat(),
        history.history_start_day.isoformat(),
        str(history.monitored_day_count),
        str(len(history.positive_days)),
        str(request.horizon_days),
        str(request.simulation_count),
        str(request.seed),
        request.forecast_run_id,
        GAP_POLICY,
        str(SEASONAL_WINDOW_DAYS),
        str(MIN_SEASONAL_WINDOW_MONITORED_DAYS),
        "pcg64_random_raw",
        "hurdle_then_joint_row_bootstrap",
        "nearest_rank_p10_p50_p90",
    )
    return "|".join(fields)


def parameter_checksum(canonical_text: str) -> str:
    """Return the sha256 hex digest that binds one simulation's parameters and its random stream."""
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _order_statistic_rank(quantile: float, simulation_count: int) -> int:
    """Nearest-rank index into a stably-sorted ensemble; never an interpolated position between two."""
    return min(simulation_count - 1, max(0, round(quantile * (simulation_count - 1))))


def simulate_cell_day_quantiles(
    *,
    cell_longitude: float,
    cell_latitude: float,
    history: CellIgnitionHistory,
    request: SimulationRequest,
    checksum: str,
) -> tuple[CellDayForecastQuantile, ...]:
    """Simulate the hurdle-then-bootstrap ensemble and reduce it to nearest-rank quantiles per horizon day.

    Each simulated cell-day is a whole draw: a seeded Bernoulli decides whether anything ignites, and
    only when it does is a whole historical positive-day row copied in -- never four independently
    sampled columns. `checksum` seeds a single `PCG64` stream for the whole run, so re-running with the
    same governed history and parameters reproduces byte-identical output.
    """
    valid_days = tuple(history.cutoff_day + timedelta(days=step) for step in range(1, request.horizon_days + 1))
    raw_draws = PCG64(int(checksum, 16)).random_raw(request.simulation_count * request.horizon_days * 2)
    draw_matrix = numpy.asarray(raw_draws, dtype=numpy.uint64).reshape(
        request.horizon_days, request.simulation_count, 2
    )

    results: list[CellDayForecastQuantile] = []
    for step_index, valid_day in enumerate(valid_days):
        ignition_probability = seasonal_ignition_probability(history, valid_day)
        pool = seasonal_positive_pool(history, valid_day)
        ignition_unit_draws = draw_matrix[step_index, :, 0].astype(numpy.float64) / _UNIT_INTERVAL_DIVISOR
        ignited = ignition_unit_draws < ignition_probability

        detection_counts = numpy.zeros(request.simulation_count, dtype=numpy.int64)
        frp_sums = numpy.full(request.simulation_count, numpy.nan, dtype=numpy.float64)
        frp_observation_counts = numpy.zeros(request.simulation_count, dtype=numpy.int64)
        high_confidence_counts = numpy.zeros(request.simulation_count, dtype=numpy.int64)
        if pool:
            pool_detection = numpy.asarray([row.detection_count for row in pool], dtype=numpy.int64)
            pool_frp = numpy.asarray([numpy.nan if row.frp_sum is None else row.frp_sum for row in pool])
            pool_frp_observations = numpy.asarray([row.frp_observation_count for row in pool], dtype=numpy.int64)
            pool_high_confidence = numpy.asarray(
                [row.high_confidence_detection_count for row in pool], dtype=numpy.int64
            )
            pool_indices = (draw_matrix[step_index, :, 1] % numpy.uint64(len(pool))).astype(numpy.intp)
            detection_counts = numpy.where(ignited, pool_detection[pool_indices], 0)
            frp_sums = numpy.where(ignited, pool_frp[pool_indices], numpy.nan)
            frp_observation_counts = numpy.where(ignited, pool_frp_observations[pool_indices], 0)
            high_confidence_counts = numpy.where(ignited, pool_high_confidence[pool_indices], 0)

        order = numpy.argsort(detection_counts, kind="stable")
        for quantile in _REPORTED_QUANTILES:
            selected = int(order[_order_statistic_rank(quantile, request.simulation_count)])
            frp_value = float(frp_sums[selected])
            results.append(
                CellDayForecastQuantile(
                    cell_longitude=cell_longitude,
                    cell_latitude=cell_latitude,
                    observed_day=valid_day,
                    detection_count=int(detection_counts[selected]),
                    frp_sum=None if math.isnan(frp_value) else frp_value,
                    frp_observation_count=int(frp_observation_counts[selected]),
                    high_confidence_detection_count=int(high_confidence_counts[selected]),
                    forecast_run_id=request.forecast_run_id,
                    random_seed=request.seed,
                    ensemble_size=request.simulation_count,
                    horizon_days=step_index + 1,
                    issued_on=history.cutoff_day,
                    quantile=quantile,
                )
            )
    return tuple(results)
