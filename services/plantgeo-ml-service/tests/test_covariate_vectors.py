"""What the covariate builder promises: pinned order, partial stays partial, nothing past the frontier."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Final

import numpy as np
import polars as pl
import pyarrow as pa
import pytest

from plantgeo_ml_service.pipeline.covariate_vectors import (
    COVARIATE_SCHEMA_VERSION,
    MIN_COVARIATE_HISTORY_DAYS,
    CovariateVectorError,
    build_covariate_matrices,
    covariate_window_bounds,
    refuse_unsettled_rows,
)
from plantgeo_ml_service.warehouse.lanes import settled_through
from plantgeo_ml_service.warehouse.streams import SIGNAL_SCHEMA, SIGNAL_STREAM

MOMENT: Final = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
ISSUED_ON: Final = date(2026, 9, 19)
CEILING: Final = settled_through(SIGNAL_STREAM, ISSUED_ON)

#: A two-position vector keeps the arithmetic in every assertion below readable by hand.
PINNED_SIGNALS: Final[tuple[str, ...]] = ("air_temperature_mean", "relative_humidity")

DEFAULT_CELL_LONGITUDE: Final = -120.25
DEFAULT_CELL_LATITUDE: Final = 46.75

#: How many days `three_complete_days` seeds, so every assertion against it names the same fixture.
THREE_COMPLETE_DAY_COUNT: Final = 3


def observed_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """Build a signal observed frame under the pinned contract, so nothing is typed by inference."""
    return pl.from_arrow(pa.Table.from_pylist(rows, schema=SIGNAL_SCHEMA.arrow_schema))


def observed_row(  # noqa: PLR0913 - one keyword per synthetic-row field, all keyword-only
    *,
    day: date,
    signal_name: str,
    value: float,
    cell_id: str = "cell-a",
    longitude: float = DEFAULT_CELL_LONGITUDE,
    latitude: float = DEFAULT_CELL_LATITUDE,
) -> dict[str, object]:
    """Build one governed signal cell-day row."""
    return {
        "support_key": "surface",
        "signal_name": signal_name,
        "normalized_unit": "C" if signal_name == "air_temperature_mean" else "%",
        "cell_id": cell_id,
        "observed_day": day,
        "normalized_value": value,
        "observation_count": 4,
        "newest_observed_at": MOMENT,
        "coverage_fraction": 1.0,
        "allowed_client_exposure": True,
        "cell_longitude": longitude,
        "cell_latitude": latitude,
    }


def three_complete_days() -> pl.DataFrame:
    """Three consecutive days carrying both pinned signals, with a hand-checkable spread."""
    days = [CEILING - timedelta(days=offset) for offset in (2, 1, 0)]
    temperatures = (10.0, 12.0, 14.0)
    humidities = (40.0, 50.0, 60.0)
    return observed_frame(
        [
            observed_row(day=day, signal_name="air_temperature_mean", value=temperature)
            for day, temperature in zip(days, temperatures, strict=True)
        ]
        + [
            observed_row(day=day, signal_name="relative_humidity", value=humidity)
            for day, humidity in zip(days, humidities, strict=True)
        ]
    )


def test_one_standardized_vector_per_cell_day_in_the_pinned_order() -> None:
    matrices = build_covariate_matrices(three_complete_days(), issued_on=ISSUED_ON, signal_order=PINNED_SIGNALS)

    assert len(matrices) == 1
    matrix = matrices[0]
    assert matrix.feature_names == PINNED_SIGNALS
    assert matrix.schema_version == COVARIATE_SCHEMA_VERSION
    assert matrix.day_count == THREE_COMPLETE_DAY_COUNT
    assert matrix.observed_days == tuple(sorted(matrix.observed_days))
    assert matrix.standardized_values.shape == (3, 2)
    # Mean removed and scale divided out, so each column is centred with unit population spread.
    assert matrix.standardized_values.mean(axis=0) == pytest.approx([0.0, 0.0])
    assert matrix.standardized_values.std(axis=0) == pytest.approx([1.0, 1.0])
    assert matrix.feature_means == pytest.approx((12.0, 50.0))


def test_cell_identity_and_coordinates_are_propagated_verbatim() -> None:
    matrices = build_covariate_matrices(three_complete_days(), issued_on=ISSUED_ON, signal_order=PINNED_SIGNALS)

    matrix = matrices[0]
    assert matrix.cell_id == "cell-a"
    assert matrix.cell_longitude == DEFAULT_CELL_LONGITUDE
    assert matrix.cell_latitude == DEFAULT_CELL_LATITUDE
    assert matrix.facts_by_signal["air_temperature_mean"].normalized_unit == "C"
    assert matrix.facts_by_signal["relative_humidity"].support_key == "surface"


def test_a_day_missing_one_pinned_signal_is_dropped_not_imputed() -> None:
    """Partial stays partial: a mean over the survivors would read as a measured vector."""
    partial_day = CEILING - timedelta(days=3)
    frame = pl.concat(
        [
            three_complete_days(),
            observed_frame([observed_row(day=partial_day, signal_name="air_temperature_mean", value=99.0)]),
        ],
        how="vertical",
    )

    matrix = build_covariate_matrices(frame, issued_on=ISSUED_ON, signal_order=PINNED_SIGNALS)[0]

    assert partial_day not in matrix.observed_days
    assert matrix.dropped_day_count == 1
    assert matrix.day_count == THREE_COMPLETE_DAY_COUNT


def test_a_constant_feature_is_recorded_rather_than_divided_by_its_noise() -> None:
    days = [CEILING - timedelta(days=offset) for offset in (2, 1, 0)]
    frame = observed_frame(
        [
            observed_row(day=day, signal_name="air_temperature_mean", value=10.0 + index)
            for index, day in enumerate(days)
        ]
        + [observed_row(day=day, signal_name="relative_humidity", value=50.0) for day in days]
    )

    matrix = build_covariate_matrices(frame, issued_on=ISSUED_ON, signal_order=PINNED_SIGNALS)[0]

    assert matrix.zero_variance_features == ("relative_humidity",)
    assert matrix.feature_standard_deviations[1] == 1.0
    assert np.isfinite(matrix.standardized_values).all()


def test_a_row_past_the_settled_frontier_is_refused_by_name() -> None:
    frame = pl.concat(
        [
            three_complete_days(),
            observed_frame(
                [observed_row(day=CEILING + timedelta(days=1), signal_name="air_temperature_mean", value=1.0)]
            ),
        ],
        how="vertical",
    )

    with pytest.raises(CovariateVectorError, match="settles through"):
        refuse_unsettled_rows(frame, issued_on=ISSUED_ON)
    with pytest.raises(CovariateVectorError, match="settles through"):
        build_covariate_matrices(frame, issued_on=ISSUED_ON, signal_order=PINNED_SIGNALS)


def test_the_window_ends_at_the_lanes_frontier_and_refuses_a_history_too_short_to_search() -> None:
    first_day, last_day = covariate_window_bounds(issued_on=ISSUED_ON, history_days=MIN_COVARIATE_HISTORY_DAYS)

    assert last_day == CEILING
    assert (last_day - first_day).days + 1 == MIN_COVARIATE_HISTORY_DAYS
    with pytest.raises(CovariateVectorError, match="at least"):
        covariate_window_bounds(issued_on=ISSUED_ON, history_days=30)


def test_a_repeated_vector_position_is_refused() -> None:
    with pytest.raises(CovariateVectorError, match="repeats a position"):
        build_covariate_matrices(
            three_complete_days(),
            issued_on=ISSUED_ON,
            signal_order=("air_temperature_mean", "air_temperature_mean"),
        )


def test_a_day_with_no_complete_vector_yields_no_matrix_rather_than_an_empty_one() -> None:
    frame = observed_frame(
        [observed_row(day=CEILING, signal_name="air_temperature_mean", value=10.0)],
    )

    assert build_covariate_matrices(frame, issued_on=ISSUED_ON, signal_order=PINNED_SIGNALS) == ()


def test_a_query_vector_for_a_day_the_matrix_never_completed_is_refused() -> None:
    matrix = build_covariate_matrices(three_complete_days(), issued_on=ISSUED_ON, signal_order=PINNED_SIGNALS)[0]

    with pytest.raises(CovariateVectorError, match="no complete covariate vector"):
        matrix.query_vector(CEILING - timedelta(days=90))
