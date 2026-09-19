"""The pinned stream contracts: the forecast derivation, the fire-risk stream, and the lane clocks."""

from __future__ import annotations

from datetime import date

import pyarrow as pa
import pytest

from plantgeo_ml_service.warehouse.lanes import (
    LANE_CONTRACTS,
    PUBLICATION_LAG_DAYS,
    LaneContractError,
    lane_contract,
    settled_through,
)
from plantgeo_ml_service.warehouse.streams import (
    FIRE_RISK_SCHEMA,
    FORECAST_PROVENANCE_COLUMNS,
    FORECAST_PROVENANCE_GRAIN,
    SIGNAL_SCHEMA,
    ParquetStreamSchema,
    StreamSchemaConflictError,
    StreamSchemaError,
    forecast_schema_for,
    observed_stream_schema,
    registered_stream_names,
    stream_schema,
)


def test_a_forecast_schema_is_the_observed_columns_then_the_six_provenance_columns() -> None:
    forecast = stream_schema("signal", "forecast")

    assert forecast.column_names[: len(SIGNAL_SCHEMA.column_names)] == SIGNAL_SCHEMA.column_names
    assert forecast.column_names[len(SIGNAL_SCHEMA.column_names) :] == FORECAST_PROVENANCE_COLUMNS


def test_the_forecast_sort_key_is_total_so_a_write_is_reproducible() -> None:
    """An ordering that leaves ties is not reproducible evidence."""
    forecast = stream_schema("signal", "forecast")

    assert forecast.sort_columns == SIGNAL_SCHEMA.sort_columns + FORECAST_PROVENANCE_GRAIN


def test_the_six_provenance_columns_are_all_non_null() -> None:
    forecast = stream_schema("vegetation", "forecast")

    assert all(not forecast.arrow_schema.field(name).nullable for name in FORECAST_PROVENANCE_COLUMNS)


def test_a_stream_that_already_declares_a_provenance_column_is_refused() -> None:
    colliding = ParquetStreamSchema(
        name="colliding",
        arrow_schema=pa.schema([pa.field("issued_on", pa.date32(), nullable=False)]),
        sort_columns=("issued_on",),
    )

    with pytest.raises(StreamSchemaConflictError, match="issued_on"):
        forecast_schema_for(colliding)


def test_a_stream_sorting_on_a_column_it_lacks_is_refused() -> None:
    with pytest.raises(StreamSchemaConflictError, match="absent from its schema"):
        ParquetStreamSchema(
            name="broken",
            arrow_schema=pa.schema([pa.field("a", pa.string(), nullable=False)]),
            sort_columns=("missing",),
        )


def test_an_unpinned_stream_refuses_by_naming_what_is_pinned() -> None:
    with pytest.raises(StreamSchemaError, match="soil-survey"):
        observed_stream_schema("soil-survey")


def test_fire_risk_is_forecast_only_and_has_no_observed_side_to_read() -> None:
    """There is no observed fire risk: a risk score is always a claim about a day not yet lived."""
    with pytest.raises(StreamSchemaError, match="forecast-only"):
        stream_schema("fire-risk", "observed")

    assert stream_schema("fire-risk", "forecast") is FIRE_RISK_SCHEMA


def test_fire_risk_carries_the_grain_the_detections_lane_uses_and_the_six_provenance_columns() -> None:
    assert FIRE_RISK_SCHEMA.column_names[:3] == ("cell_longitude", "cell_latitude", "valid_day")
    assert set(FORECAST_PROVENANCE_COLUMNS) <= set(FIRE_RISK_SCHEMA.column_names)
    assert "model_artifact_sha256" in FIRE_RISK_SCHEMA.column_names


def test_a_refused_fire_risk_cell_nulls_its_score_rather_than_scoring_zero() -> None:
    """A fabricated zero reads as "no risk here", which is the claim the FR-5 gate exists to prevent."""
    assert FIRE_RISK_SCHEMA.arrow_schema.field("probability").nullable
    assert FIRE_RISK_SCHEMA.arrow_schema.field("risk_score").nullable
    assert FIRE_RISK_SCHEMA.arrow_schema.field("refused_reason").nullable
    assert not FIRE_RISK_SCHEMA.arrow_schema.field("stratum").nullable


def test_every_pinned_stream_is_named_once() -> None:
    assert registered_stream_names() == (
        "burn-severity",
        "drought",
        "fire-detections",
        "fire-risk",
        "signal",
        "vegetation",
        "weather-forecast",
        "weather-observations",
    )


@pytest.mark.parametrize(("slug", "lag"), [("signal", 9), ("fire-detections", 2), ("vegetation", 7), ("drought", 4)])
def test_a_lane_settles_exactly_its_publication_lag_behind_the_issue_day(slug: str, lag: int) -> None:
    as_of = date(2026, 9, 19)

    assert (as_of - settled_through(slug, as_of)).days == lag
    assert PUBLICATION_LAG_DAYS[slug] == lag


def test_every_copied_lane_has_a_flat_lag_entry() -> None:
    assert set(PUBLICATION_LAG_DAYS) == set(LANE_CONTRACTS)


def test_a_lane_this_service_does_not_consume_refuses_by_naming_the_ones_it_does() -> None:
    with pytest.raises(LaneContractError, match="signal"):
        lane_contract("soil-survey")


def test_a_negative_publication_lag_is_refused() -> None:
    from plantgeo_ml_service.warehouse.lanes import LaneContract  # noqa: PLC0415

    with pytest.raises(LaneContractError, match="negative publication lag"):
        LaneContract(slug="broken", history_floor=date(2020, 1, 1), publication_lag_days=-1, nature="daily_series")
