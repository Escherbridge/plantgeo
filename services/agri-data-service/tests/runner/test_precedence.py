"""The generic precedence transform (S7): stream routing and the settled ▷ provisional merge, table-driven."""

from __future__ import annotations

from datetime import date

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.lanes.transforms.precedence import (
    PRECEDENCE_SOURCE_COLUMN,
    PrecedenceRouteError,
    RankedInput,
    merge_by_precedence,
    route_input_stream,
)

DAY = date(2026, 9, 1)


@pytest.mark.parametrize(
    ("output", "candidates", "routed"),
    [
        (
            "meteorology-dew-point",
            ("meteorology-era5-dew-point", "meteorology-era5-precipitation"),
            "meteorology-era5-dew-point",
        ),
        ("meteorology-dew-point", ("meteorology-ifs-dew-point", "shortwave-ifs"), "meteorology-ifs-dew-point"),
        ("shortwave-radiation", ("shortwave-power",), "shortwave-power"),
        ("shortwave-radiation", ("meteorology-ifs-dew-point", "shortwave-ifs"), "shortwave-ifs"),
        ("shortwave-radiation", ("meteorology-era5-dew-point",), None),
    ],
)
def test_each_output_stream_routes_to_one_stream_of_each_input_lane(
    output: str, candidates: tuple[str, ...], routed: str | None
) -> None:
    """The climate shape of spec §6.1: one hyphen token inserted, else the one stream of the output's family."""
    assert route_input_stream(output, candidates) == routed


def test_an_ambiguous_route_is_refused_rather_than_guessed() -> None:
    with pytest.raises(PrecedenceRouteError):
        route_input_stream("shortwave-radiation", ("shortwave-power", "shortwave-ifs"))


def _cells(values: dict[str, float]) -> pa.Table:
    return pa.table(
        {
            "cell_id": pa.array(list(values), pa.string()),
            "observed_day": pa.array([DAY] * len(values), pa.date32()),
            "value": pa.array(list(values.values()), pa.float64()),
        }
    )


def test_each_key_takes_the_highest_ranked_row_and_says_which_lane_it_came_from() -> None:
    """Settled wins where it has a cell; provisional fills the rest; a fully covered input is superseded."""
    settled = RankedInput("settled", "s-era5-v", _cells({"c1": 1.0, "c2": 2.0}))
    provisional = RankedInput("provisional", "s-ifs-v", _cells({"c2": 20.0, "c3": 30.0}))
    covered = RankedInput("provisional", "s-ifs-v", _cells({"c1": 10.0}))

    merged, superseded = merge_by_precedence([settled, provisional])
    _, fully_superseded = merge_by_precedence([settled, covered])

    assert merged is not None
    rows = {
        cell: (value, source)
        for cell, value, source in zip(
            merged.column("cell_id").to_pylist(),
            merged.column("value").to_pylist(),
            merged.column(PRECEDENCE_SOURCE_COLUMN).to_pylist(),
            strict=True,
        )
    }
    assert rows == {"c1": (1.0, "settled"), "c2": (2.0, "settled"), "c3": (30.0, "provisional")}
    assert superseded == ()
    assert fully_superseded == ("s-ifs-v",)


def test_inputs_with_no_rows_derive_nothing() -> None:
    merged, superseded = merge_by_precedence([RankedInput("settled", "s-era5-v", _cells({}))])

    assert (merged, superseded) == (None, ())
