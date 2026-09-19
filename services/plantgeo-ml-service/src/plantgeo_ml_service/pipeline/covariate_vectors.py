"""Standardized Analog Ensemble covariate vectors, built from the signal lane's OBSERVED partitions.

Layer L3, spec FR-6. Re-expresses the sibling's deleted `select_covariate_vectors.sql` semantics
over Parquet: one row per (cell, day, signal), pivoted into one vector per (cell_id, day) in a
PINNED signal order, partial staying partial. Why a day missing one signal is dropped rather than
imputed, and why the standardization moments are recorded rather than recomputed at query time, live
in `AGENTS-forecast-lanes.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, cast

import numpy as np
import polars as pl

from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM, ZoomTier
from plantgeo_ml_service.warehouse.lanes import settled_through
from plantgeo_ml_service.warehouse.streams import SIGNAL_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.pipeline.observed_reader import ObservedReader

#: Bump this when `COVARIATE_SIGNAL_ORDER` changes. The order IS the vector: a matrix built under
#: one order and a query vector built under another are silently different features at one position.
COVARIATE_SCHEMA_VERSION: Final = "plantgeo_signal_covariates_v1"

#: The pinned vector positions, in order. Every one is a declared series of
#: `method/monte_carlo/signal.py`, so a covariate and a forecastable target are the same vocabulary.
COVARIATE_SIGNAL_ORDER: Final[tuple[str, ...]] = (
    "air_temperature_mean",
    "air_temperature_max",
    "air_temperature_min",
    "dew_point_temperature",
    "precipitation",
    "relative_humidity",
    "wind_speed",
    "vapor_pressure_deficit",
)

#: An analog search needs a season's worth of neighbours at every day of year it is asked about.
#: Three years is the floor the AnEn literature treats as usable; the reader's own window budget is
#: the ceiling above it.
MIN_COVARIATE_HISTORY_DAYS: Final = 1_095

#: A feature whose sample standard deviation is at or below this is treated as constant: dividing by
#: it would turn float noise into an enormous standardized coordinate and dominate every distance.
MIN_FEATURE_STANDARD_DEVIATION: Final = 1e-12


class CovariateVectorError(ValueError):
    """Raised when covariate vectors cannot be built honestly from the rows offered."""


@dataclass(frozen=True, slots=True)
class SignalSeriesFacts:
    """The observed attributes one cell-signal carries, for verbatim propagation onto forecast rows.

    Every field here is READ from the observed rows, never invented, and is what
    `layer-lanes.md` section 2 means by identical grain and identical column names.
    """

    signal_name: str
    support_key: str
    normalized_unit: str
    newest_observed_at: object
    allowed_client_exposure: bool | None


@dataclass(frozen=True, slots=True)
class CovariateMatrix:
    """One cell's standardized covariate history, its raw target series, and its pinned moments."""

    cell_id: str
    cell_longitude: float
    cell_latitude: float
    schema_version: str
    feature_names: tuple[str, ...]
    observed_days: tuple[date, ...]
    standardized_values: np.ndarray
    feature_means: tuple[float, ...]
    feature_standard_deviations: tuple[float, ...]
    zero_variance_features: tuple[str, ...]
    raw_series_by_signal: Mapping[str, Mapping[date, float]]
    facts_by_signal: Mapping[str, SignalSeriesFacts]
    dropped_day_count: int

    @property
    def day_count(self) -> int:
        """Return how many complete days the matrix holds."""
        return len(self.observed_days)

    def query_vector(self, day: date) -> np.ndarray:
        """Return the standardized vector of one day, or refuse by naming the day."""
        try:
            position = self.observed_days.index(day)
        except ValueError as error:
            raise CovariateVectorError(
                f"cell {self.cell_id!r} has no complete covariate vector for {day.isoformat()}; a day "
                "missing any pinned signal is dropped rather than imputed"
            ) from error
        # numpy's stubs resolve unparameterized ndarray indexing to `Any`; the runtime type is a row vector.
        return cast("np.ndarray", self.standardized_values[position])


def covariate_window_bounds(*, issued_on: date, history_days: int) -> tuple[date, date]:
    """Return the inclusive observed window an issue day may read, bounded by the signal lane's clock.

    The upper bound is the lane's own `settled_through`, never `issued_on` and never today: a
    covariate read past the producer's frontier is leakage whatever the bucket happens to hold.
    """
    if history_days < MIN_COVARIATE_HISTORY_DAYS:
        raise CovariateVectorError(
            f"an analog search needs at least {MIN_COVARIATE_HISTORY_DAYS} days of signal history, got {history_days}"
        )
    last_day = settled_through(SIGNAL_STREAM, issued_on)
    return last_day - timedelta(days=history_days - 1), last_day


def read_covariate_window(
    reader: ObservedReader,
    *,
    issued_on: date,
    history_days: int,
    zoom: ZoomTier = BASE_PARTITION_ZOOM,
) -> pl.DataFrame:
    """Read the signal lane's observed rows for one issue day's covariate window."""
    first_day, last_day = covariate_window_bounds(issued_on=issued_on, history_days=history_days)
    return reader.read_lane_window(SIGNAL_STREAM, zoom, first_day, last_day, as_of=issued_on)


def refuse_unsettled_rows(frame: pl.DataFrame, *, issued_on: date) -> pl.DataFrame:
    """Return `frame` unchanged, or refuse by naming a row dated past the lane's settled frontier.

    The reader already bounds its window; this is the second, independent check the tripwire asks
    for, so a frame assembled any other way cannot carry a day the producer had not published.
    """
    ceiling = settled_through(SIGNAL_STREAM, issued_on)
    if frame.height == 0:
        return frame
    newest = frame.get_column("observed_day").max()
    if isinstance(newest, date) and newest > ceiling:
        raise CovariateVectorError(
            f"the signal lane settles through {ceiling.isoformat()} for an issue day of "
            f"{issued_on.isoformat()}, but the rows offered reach {newest.isoformat()}"
        )
    return frame


def build_covariate_matrices(
    frame: pl.DataFrame,
    *,
    issued_on: date,
    signal_order: Sequence[str] = COVARIATE_SIGNAL_ORDER,
) -> tuple[CovariateMatrix, ...]:
    """Pivot observed signal rows into one standardized vector per (cell_id, day), in pinned order.

    Cells are returned in `cell_id` order and days in calendar order, so the matrix a run searches
    is a function of its inputs alone and two runs over the same window build the same one.
    """
    ordered_signals = tuple(signal_order)
    _refuse_unknown_signals(ordered_signals)
    refuse_unsettled_rows(frame, issued_on=issued_on)
    if frame.height == 0:
        return ()
    relevant = frame.filter(pl.col("signal_name").is_in(list(ordered_signals)))
    if relevant.height == 0:
        return ()
    matrices = [
        _matrix_for_cell(cell_rows, signals=ordered_signals)
        for _cell_id, cell_rows in sorted(
            relevant.partition_by("cell_id", as_dict=True).items(),
            key=lambda entry: str(entry[0]),
        )
    ]
    return tuple(matrix for matrix in matrices if matrix is not None)


def _refuse_unknown_signals(signals: tuple[str, ...]) -> None:
    """Refuse a vector definition that repeats a position or names nothing."""
    if not signals:
        raise CovariateVectorError("a covariate vector needs at least one pinned signal position")
    if len(set(signals)) != len(signals):
        raise CovariateVectorError(f"the pinned signal order repeats a position: {signals}")


def _matrix_for_cell(rows: pl.DataFrame, *, signals: tuple[str, ...]) -> CovariateMatrix | None:
    """Build one cell's matrix, or `None` when no day of it carries every pinned signal."""
    cell_id = _single_cell_identity(rows, "cell_id")
    wide = rows.pivot(
        on="signal_name",
        index="observed_day",
        values="normalized_value",
        aggregate_function="first",
    ).sort("observed_day")
    present = tuple(name for name in signals if name in wide.columns)
    if len(present) != len(signals):
        return None
    offered_day_count = wide.height
    complete = wide.drop_nulls(subset=list(signals))
    if complete.height == 0:
        return None
    observed_days = tuple(complete.get_column("observed_day").to_list())
    raw_values = complete.select(list(signals)).to_numpy().astype(np.float64)
    means, deviations, zero_variance = _standardization_moments(raw_values, signals=signals)
    standardized = (raw_values - np.asarray(means)) / np.asarray(deviations)
    return CovariateMatrix(
        cell_id=str(cell_id),
        # `_single_cell_identity` is typed `object` so it can also return the `cell_id` string; here
        # the column is float64.
        cell_longitude=float(_single_cell_identity(rows, "cell_longitude")),  # type: ignore[arg-type]
        cell_latitude=float(_single_cell_identity(rows, "cell_latitude")),  # type: ignore[arg-type]
        schema_version=COVARIATE_SCHEMA_VERSION,
        feature_names=signals,
        observed_days=observed_days,
        standardized_values=standardized,
        feature_means=means,
        feature_standard_deviations=deviations,
        zero_variance_features=zero_variance,
        raw_series_by_signal=_raw_series_by_signal(complete, signals=signals, days=observed_days),
        facts_by_signal=_facts_by_signal(rows, signals=signals),
        dropped_day_count=offered_day_count - complete.height,
    )


def _standardization_moments(
    values: np.ndarray, *, signals: tuple[str, ...]
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[str, ...]]:
    """Return per-feature means, the scales to divide by, and which features carry no variance."""
    means = values.mean(axis=0)
    raw_deviations = values.std(axis=0)
    constant = raw_deviations <= MIN_FEATURE_STANDARD_DEVIATION
    scales = np.where(constant, 1.0, raw_deviations)
    zero_variance = tuple(name for name, is_constant in zip(signals, constant.tolist(), strict=True) if is_constant)
    return tuple(means.tolist()), tuple(scales.tolist()), zero_variance


def _raw_series_by_signal(
    complete: pl.DataFrame, *, signals: tuple[str, ...], days: tuple[date, ...]
) -> dict[str, dict[date, float]]:
    """Return each pinned signal's RAW day-to-value series, which is what a forecast reports in."""
    return {
        name: dict(zip(days, (float(value) for value in complete.get_column(name).to_list()), strict=True))
        for name in signals
    }


def _facts_by_signal(rows: pl.DataFrame, *, signals: tuple[str, ...]) -> dict[str, SignalSeriesFacts]:
    """Read the observed attributes each pinned signal carries, for verbatim propagation."""
    facts: dict[str, SignalSeriesFacts] = {}
    for name in signals:
        series_rows = rows.filter(pl.col("signal_name") == name).sort("observed_day")
        if series_rows.height == 0:
            continue
        latest = series_rows.tail(1)
        facts[name] = SignalSeriesFacts(
            signal_name=name,
            support_key=str(latest.get_column("support_key").item()),
            normalized_unit=str(latest.get_column("normalized_unit").item()),
            newest_observed_at=series_rows.get_column("newest_observed_at").max(),
            allowed_client_exposure=_optional_flag(series_rows.get_column("allowed_client_exposure").to_list()),
        )
    return facts


def _optional_flag(values: list[object]) -> bool | None:
    """Return the exposure gate the merged series carries: exposed only when every row was."""
    stated = [value for value in values if value is not None]
    if not stated:
        return None
    return all(bool(value) for value in stated)


def _single_cell_identity(rows: pl.DataFrame, column: str) -> object:
    """Return the one value a per-cell frame carries in `column`, refusing a frame that mixes cells."""
    distinct = rows.get_column(column).unique().to_list()
    if len(distinct) != 1 or distinct[0] is None:
        raise CovariateVectorError(
            f"a per-cell covariate frame carries exactly one {column}, got {len(distinct)}; "
            "cell identity is propagated verbatim and may not be merged here"
        )
    return distinct[0]
