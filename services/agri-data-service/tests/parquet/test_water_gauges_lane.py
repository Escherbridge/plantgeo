"""The water-gauges day exporter: no cell batching, the row cap, and the empty-day refusal.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python -- that a day is read in one statement (unlike the signal plane,
which must batch by cell), that the result conforms to the registered schema, that a day
reaching the row cap is refused rather than silently truncated, and that an empty day is refused
rather than silently written as a present, empty partition.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

import agri_data_service.pipeline.lanes.water_gauges as water_gauges_lane
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.water_gauges import (
    MAX_ROWS_PER_DAY,
    WaterGaugesExportError,
    export_water_gauges_day,
    read_water_gauges_day,
)
from agri_data_service.pipeline.parquet.objectstore import EmptyPartitionError, ObjectStore
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_SCHEMA, WATER_GAUGES_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)


def water_gauge_row(site_number: str, observed_at: datetime) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "site_number": site_number,
        "observed_at": observed_at,
        "observed_day": AUGUST_SIXTH,
        "site_name": f"Gauge {site_number}",
        "latitude": 47.5,
        "longitude": -113.5,
        "flow_cfs": 123.0,
        "percentile": None,
        "condition": "unknown",
        "trend": "stable",
        "source": "USGS NWIS",
        "geometry_linked": True,
        "data_available_at": None,
        "ingested_at": datetime(2026, 8, 6, 12, 30, tzinfo=UTC),
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound day and row cap, and answers with a fixed row set."""

    def __init__(self, rows: Sequence[dict[str, object]] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rows = list(rows)

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(params)
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_a_day_is_read_in_one_statement_with_no_cell_batching() -> None:
    """geo.features already carries the (layer, day) index the signal plane lacks; no chunking."""
    rows = [water_gauge_row(f"0{index}", datetime(2026, 8, 6, index, tzinfo=UTC)) for index in range(5)]
    session = RecordingSession(rows)

    await read_water_gauges_day(session, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert len(session.calls) == 1
    assert session.calls[0]["observed_day"] == AUGUST_SIXTH
    assert session.calls[0]["row_limit"] == MAX_ROWS_PER_DAY
    assert "cell_ids" not in session.calls[0]


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    rows = [
        water_gauge_row("05014500", datetime(2026, 8, 6, 6, tzinfo=UTC)),
        water_gauge_row("05014500", datetime(2026, 8, 6, 18, tzinfo=UTC)),
        water_gauge_row("12345678", datetime(2026, 8, 6, 12, tzinfo=UTC)),
    ]
    session = RecordingSession(rows)

    table = await read_water_gauges_day(session, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    expected_rows = 3
    assert table.schema.equals(WATER_GAUGES_SCHEMA.arrow_schema)
    assert table.num_rows == expected_rows


@pytest.mark.asyncio
async def test_a_day_that_reaches_the_row_cap_is_refused_rather_than_silently_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(water_gauges_lane, "MAX_ROWS_PER_DAY", 2)
    rows = [water_gauge_row(f"0{index}", datetime(2026, 8, 6, index, tzinfo=UTC)) for index in range(2)]
    session = RecordingSession(rows)

    with pytest.raises(WaterGaugesExportError, match="at least 2"):
        await water_gauges_lane.read_water_gauges_day(session, day=AUGUST_SIXTH)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_an_empty_day_is_refused_rather_than_written_as_a_present_partition() -> None:
    """A day the source genuinely cannot serve is a governed absence, never worked around here."""
    session = RecordingSession(())
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(EmptyPartitionError):
        await export_water_gauges_day(session, store, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert backend.objects == {}


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    rows = [
        water_gauge_row("99999999", datetime(2026, 8, 6, 3, tzinfo=UTC)),
        water_gauge_row("05014500", datetime(2026, 8, 6, 18, tzinfo=UTC)),
        water_gauge_row("05014500", datetime(2026, 8, 6, 6, tzinfo=UTC)),
    ]
    session = RecordingSession(rows)
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = await export_water_gauges_day(session, store, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    expected_rows = 3
    assert receipt.key == partition_path(WATER_GAUGES_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows
