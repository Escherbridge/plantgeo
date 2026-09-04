"""The fire-perimeters snapshot exporter: the GRAIN, WKB conformance, the empty refusal, byte spillover.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python. The first block is the important one and is new as of 2026-09-04:
this lane's grain is ONE ROW PER WFIGS INCIDENT PER SNAPSHOT, never one row per (incident, day),
and until that date the claim lived only in a docstring while the exporter did the opposite --
filtering `geo.features` by `geo.feature_observation_day` and scattering 177 published perimeters
across 45 near-empty partition days. The tests below assert it instead of describing it: incidents
carrying three DIFFERENT observed days land in ONE partition, the population does not move when the
snapshot day does, and the grain tuple names no observation column at all.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.fire_perimeters import (
    _chunk_row_indices_by_geometry_bytes,
    export_fire_perimeters_day,
    read_fire_perimeters_snapshot,
)
from agri_data_service.pipeline.parquet.objectstore import EmptyPartitionError, ObjectStore
from agri_data_service.warehouse.schemas.fire_perimeters import (
    FIRE_PERIMETERS_GRAIN,
    FIRE_PERIMETERS_SCHEMA,
    FIRE_PERIMETERS_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)
AUGUST_SEVENTH = date(2026, 8, 7)
JULY_FIRST = date(2026, 7, 1)


def pq_buffer(backend: RecordingBackend, key: str) -> io.BytesIO:
    """Wrap one recorded object's bytes in a seekable buffer `pyarrow.parquet.read_table` can read."""
    return io.BytesIO(backend.objects[key])


def fire_perimeter_row(
    *,
    feature_id: str = "11111111-1111-1111-1111-111111111111",
    unique_fire_identifier: str = "2026-CA-000123",
    snapshot_day: date = AUGUST_SIXTH,
    observed_day: date | None = AUGUST_SIXTH,
    geometry_wkb: bytes = b"\x01\x02\x03",
) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "feature_id": feature_id,
        "unique_fire_identifier": unique_fire_identifier,
        "snapshot_day": snapshot_day,
        "observed_day": observed_day,
        "incident_name": "Example Fire",
        "irwin_id": "irwin-1",
        "fire_discovery_at": datetime(2026, 8, 6, 10, tzinfo=UTC),
        "polygon_at": datetime(2026, 8, 6, 12, tzinfo=UTC),
        "gis_acres": 1234.5,
        "fire_cause": "Lightning",
        "incident_type_category": "WF",
        "poo_state": "US-CA",
        "percent_contained": 45.0,
        "severity": "high",
        "status": "published",
        # 100% NULL in production today (conductor/RUNBOOK.md); never fabricated here either.
        "data_available_at": None,
        "updated_at": datetime(2026, 8, 6, 13, tzinfo=UTC),
        "geometry_wkb": geometry_wkb,
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Answers with one fixed population and records the `snapshot_day` each call bound.

    It answers the SAME rows for every bound day ON PURPOSE -- that is what the real query does now
    that `snapshot_day` is stamped rather than filtered on. A fixture keyed by day would let a
    re-introduced date predicate pass unnoticed, which is the regression these tests exist to catch.
    """

    def __init__(self, rows: Sequence[dict[str, object]] = ()) -> None:
        self.snapshot_days: list[date] = []
        self._rows = list(rows)

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.snapshot_days.append(params["snapshot_day"])
        return _Result([dict(row, snapshot_day=params["snapshot_day"]) for row in self._rows])


# --- the grain: one row per incident per snapshot, never one row per (incident, day) --------------


def test_the_registered_grain_is_per_incident_and_names_no_observation_column() -> None:
    """`observed_day` is a per-row attribute of the INCIDENT, never part of this lane's key.

    `geo.features` holds one row per WFIGS incident refreshed in place
    (`docs/lanes/fire-perimeters.md` #4), so the only key a snapshot has is the incident's own
    identifier plus the version stamp the whole partition shares.
    """
    assert FIRE_PERIMETERS_GRAIN == ("snapshot_day", "unique_fire_identifier")
    assert "observed_day" not in FIRE_PERIMETERS_GRAIN


@pytest.mark.asyncio
async def test_incidents_with_different_observed_days_all_land_in_one_snapshot() -> None:
    """The exact inversion of the shape this lane carried until 2026-09-04.

    Three incidents whose own publisher timestamps name three different days are ONE standing set,
    and the old `daily_series` export put them in three separate partitions -- each near-empty, and
    none of them answering "what is burning now" without unioning the whole history window.
    """
    rows = [
        fire_perimeter_row(unique_fire_identifier="2026-CA-000001", observed_day=JULY_FIRST),
        fire_perimeter_row(unique_fire_identifier="2026-CA-000002", observed_day=AUGUST_SIXTH),
        fire_perimeter_row(unique_fire_identifier="2026-CA-000003", observed_day=AUGUST_SEVENTH),
    ]
    session = RecordingSession(rows)
    backend = RecordingBackend()
    store = ObjectStore(backend)

    parts = await export_fire_perimeters_day(session, store, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert len(parts) == 1
    written = pq.read_table(pq_buffer(backend, parts[0].key))
    assert written.column("observed_day").to_pylist() == [JULY_FIRST, AUGUST_SIXTH, AUGUST_SEVENTH]
    # One partition, one version stamp, and one row per incident -- no key repeats.
    assert written.column("snapshot_day").to_pylist() == [AUGUST_SIXTH] * len(rows)
    identifiers = written.column("unique_fire_identifier").to_pylist()
    assert len(set(identifiers)) == len(identifiers)


@pytest.mark.asyncio
async def test_the_snapshot_day_is_stamped_not_filtered_on() -> None:
    """Two different version days return the SAME population, each stamped with its own day.

    A date predicate re-introduced into the export SQL would show up here as a shrinking or shifting
    population, which is what made 177 perimeters look like 45 sparse days.
    """
    rows = [fire_perimeter_row(unique_fire_identifier=f"2026-CA-{index:06d}") for index in range(3)]
    session = RecordingSession(rows)

    first = await read_fire_perimeters_snapshot(session, snapshot_day=AUGUST_SIXTH)  # type: ignore[arg-type]
    second = await read_fire_perimeters_snapshot(session, snapshot_day=AUGUST_SEVENTH)  # type: ignore[arg-type]

    assert session.snapshot_days == [AUGUST_SIXTH, AUGUST_SEVENTH]
    assert first.column("unique_fire_identifier").to_pylist() == second.column("unique_fire_identifier").to_pylist()
    assert first.column("snapshot_day").to_pylist() == [AUGUST_SIXTH] * len(rows)
    assert second.column("snapshot_day").to_pylist() == [AUGUST_SEVENTH] * len(rows)


@pytest.mark.asyncio
async def test_an_undated_incident_survives_the_export() -> None:
    """`geo.feature_observation_day` returns NULL for a row it cannot date, and the map keeps it.

    `drizzle/0018_fire_discovery_observation_day.sql:39-40`: such a row "is treated as undated by
    the client filter, which shows it at every date rather than hiding it." The old `= <one day>`
    export could never match NULL, so it dropped a perimeter Martin was drawing.
    """
    session = RecordingSession([fire_perimeter_row(unique_fire_identifier="2026-CA-000404", observed_day=None)])

    table = await read_fire_perimeters_snapshot(session, snapshot_day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert table.num_rows == 1
    assert table.column("observed_day").to_pylist() == [None]
    assert FIRE_PERIMETERS_SCHEMA.arrow_schema.field("observed_day").nullable


# --- conformance, refusal, and byte spillover ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    rows = [fire_perimeter_row(unique_fire_identifier=f"2026-CA-{index:06d}") for index in range(3)]
    session = RecordingSession(rows)

    table = await read_fire_perimeters_snapshot(session, snapshot_day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert table.schema.equals(FIRE_PERIMETERS_SCHEMA.arrow_schema)
    assert table.num_rows == len(rows)
    assert table.column("geometry_wkb").type.equals(FIRE_PERIMETERS_SCHEMA.arrow_schema.field("geometry_wkb").type)


@pytest.mark.asyncio
async def test_an_empty_population_is_refused_rather_than_written_or_recorded_as_an_absence() -> None:
    """A static lane is only asked for a version its own watermark counted rows for.

    So an empty read contradicts the watermark that scheduled it -- a failed read for the gap-fill
    driver to govern, never a settled fact this module records for itself. The refusal comes from
    `write_partition`, which is reached because an empty chunk list still produces one write call.
    """
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(EmptyPartitionError):
        await export_fire_perimeters_day(session, store, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    # Nothing at all was written -- in particular no absence marker, which the `daily_series` shape
    # recorded here and which `resolve_static_lane` would now read as a FAILED read to retry.
    assert list(backend.objects) == []
    assert not store.absence_exists(FIRE_PERIMETERS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH)


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    rows = [
        fire_perimeter_row(unique_fire_identifier="2026-CA-000003"),
        fire_perimeter_row(unique_fire_identifier="2026-CA-000001"),
        fire_perimeter_row(unique_fire_identifier="2026-CA-000002"),
    ]
    session = RecordingSession(rows)
    backend = RecordingBackend()
    store = ObjectStore(backend)

    parts = await export_fire_perimeters_day(session, store, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    expected_rows = 3
    assert len(parts) == 1
    receipt = parts[0]
    assert receipt.key == partition_path(FIRE_PERIMETERS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH, 0)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows

    written = pq.read_table(pq_buffer(backend, receipt.key))
    assert written.column("unique_fire_identifier").to_pylist() == [
        "2026-CA-000001",
        "2026-CA-000002",
        "2026-CA-000003",
    ]


@pytest.mark.asyncio
async def test_a_snapshot_whose_geometry_bytes_exceed_the_part_budget_spills_into_multiple_parts() -> None:
    heavy_geometry = b"\x00" * (5 * 1024 * 1024)  # 5 MiB; two of these exceed the 8 MiB part budget.
    rows = [
        fire_perimeter_row(unique_fire_identifier="2026-CA-000002", geometry_wkb=heavy_geometry),
        fire_perimeter_row(unique_fire_identifier="2026-CA-000001", geometry_wkb=heavy_geometry),
    ]
    session = RecordingSession(rows)
    backend = RecordingBackend()
    store = ObjectStore(backend)

    parts = await export_fire_perimeters_day(session, store, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    expected_part_count = 2
    assert len(parts) == expected_part_count
    for part_index, receipt in enumerate(parts):
        assert receipt.key == partition_path(
            FIRE_PERIMETERS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH, part_index
        )
        assert receipt.row_count == 1
    # Grain-sorted before chunking: part-0 carries the lexicographically smaller natural key.
    first_part = pq.read_table(pq_buffer(backend, parts[0].key))
    second_part = pq.read_table(pq_buffer(backend, parts[1].key))
    assert first_part.column("unique_fire_identifier").to_pylist() == ["2026-CA-000001"]
    assert second_part.column("unique_fire_identifier").to_pylist() == ["2026-CA-000002"]


def test_chunk_row_indices_keeps_contiguous_runs_under_the_byte_budget() -> None:
    assert _chunk_row_indices_by_geometry_bytes([3, 3, 3], max_bytes=5) == [[0], [1], [2]]
    assert _chunk_row_indices_by_geometry_bytes([2, 2, 2, 2], max_bytes=5) == [[0, 1], [2, 3]]


def test_chunk_row_indices_never_splits_a_single_oversized_row() -> None:
    """A perimeter whose own WKB already exceeds the budget still lands in one, unsplit chunk."""
    assert _chunk_row_indices_by_geometry_bytes([9], max_bytes=5) == [[0]]


def test_chunk_row_indices_yields_one_empty_chunk_for_an_empty_population() -> None:
    """Load-bearing, not incidental: it is what forces one `write_partition` call, and so one refusal."""
    assert _chunk_row_indices_by_geometry_bytes([], max_bytes=5) == [[]]
