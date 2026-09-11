"""The retained PostgreSQL reconciliation read preserves the registered schema and day scope."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.pipeline.lanes.burn_severity import read_burn_severity_release_day
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Sequence

# The fire-year-2022 cohort's real, governed release date (ingest/mtbs.py:140,
# MTBS_ANNUAL_RELEASE_DATES) -- one of the five days this lane's whole history spans.
RELEASE_DAY = date(2024, 8, 22)


def burn_severity_row(fire_id: str) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "feature_id": f"feature-{fire_id}",
        "fire_id": fire_id,
        "natural_key": f"mtbs:{fire_id}",
        "release_identifier": "mtbs-2022-release-2024-08-22",
        "mapping_revision": "mtbs-2022-release-2024-08-22|m1|Initial|p1|po1",
        "fire_year": 2022,
        "ignition_date": date(2022, 7, 4),
        "observed_day": RELEASE_DAY,
        "data_available_at": datetime(2024, 8, 22, tzinfo=UTC),
        "fire_name": "TEST FIRE",
        "fire_type": "Wildfire",
        "assessment_type": "Initial",
        "acres": 1234.5,
        # Null on every published row today, by design -- MTBS has no polygon-level class.
        "severity_class": None,
        "dnbr_offset": 100,
        "dnbr_standard_deviation": 50,
        "nodata_threshold": -970,
        "greenness_threshold": 100,
        "low_threshold": 76,
        "moderate_threshold": 306,
        "high_threshold": 615,
        "allowed_client_exposure": False,
        "geom": b"\x01\x03\x00\x00\x00",  # a WKB stub; its bytes are opaque to this lane
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures the single day-scoped query's bound parameters and answers with fixed rows."""

    def __init__(self, *, rows: Sequence[dict[str, object]] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rows = rows

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(params)
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    rows = [burn_severity_row("2022PNW00001"), burn_severity_row("2022PNW00002")]
    session = RecordingSession(rows=rows)

    table = await read_burn_severity_release_day(session, release_day=RELEASE_DAY)  # type: ignore[arg-type]

    assert table.schema.equals(BURN_SEVERITY_SCHEMA.arrow_schema)
    assert table.num_rows == len(rows)
    assert session.calls == [{"release_day": RELEASE_DAY}]
