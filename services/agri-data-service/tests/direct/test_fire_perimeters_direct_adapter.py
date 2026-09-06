"""What the direct fire-perimeters adapter publishes, what it retracts, and what it refuses outright.

The full-ladder test NEEDS DuckDB's `spatial` extension: `fill_one_lane_day` derives z9/z5/z0 from
the base rung through `warehouse/parquet/tiers.py`. The refusal and retraction tests need none.

Rows are built as dicts rather than fetched -- `rows.py`'s WFIGS reduction is
`test_fire_perimeters_direct_rows.py`'s subject, not this file's -- but the DEFAULT `geometry_wkb`
is REAL WKB, produced once by `support.py`'s own converter. A placeholder like `b"\\x01wkb-OR-A"`
is not a parseable geometry, and the moment a row reaches `fill_one_lane_day` its
`GeometrySimplification` derivation asks DuckDB to read those bytes and the whole ladder comes back
`raised` instead of `written`. Hand-rolling a valid polygon's bytes here would be a second,
untested WKB writer; calling the converter the publisher itself calls cannot drift from it.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from functools import cache
from typing import Any

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.fire_perimeters import adapter as adapter_module
from agri_data_service.pipeline.direct.fire_perimeters.adapter import (
    DirectFirePerimetersAdapter,
    DirectFirePerimetersError,
)
from agri_data_service.pipeline.direct.fire_perimeters.forward import MemoizedDirectWatermark
from agri_data_service.pipeline.direct.fire_perimeters.products import FIRE_PERIMETERS_DIRECT_KIND
from agri_data_service.pipeline.direct.fire_perimeters.rows import FirePerimeterPopulation
from agri_data_service.pipeline.direct.fire_perimeters.support import (
    fire_perimeter_geometry_session,
    perimeter_geometries_to_wkb,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, unlocked_lane_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

VERSION_DAY = date(2026, 9, 6)
FETCHED_AT = datetime(2026, 9, 6, 14, 30, tzinfo=UTC)
#: One square degree of eastern Oregon, the same shape `test_fire_perimeters_direct_support.py` and
#: `test_burn_severity_direct_adapter.py` both use. Valid and non-empty, so `support.py` converts it
#: rather than refusing the snapshot.
VALID_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}


@cache
def valid_square_wkb() -> bytes:
    """Real WKB for `VALID_SQUARE`, built by the one converter this lane publishes through.

    Cached because the answer is a constant and the DuckDB `LOAD spatial` behind it is not free:
    every row-building test in this module shares one session for the whole run.
    """
    with fire_perimeter_geometry_session() as session:
        return perimeter_geometries_to_wkb(session, [VALID_SQUARE], ["OR-SQUARE"])[0]


class SessionDouble:
    """Answers the statement-timeout pin and counts rollbacks; executes no real SQL."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:  # noqa: ARG002
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


def _row(identifier: str, *, geometry: bytes | None = None, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "feature_id": f"direct:{identifier}",
        "unique_fire_identifier": identifier,
        "observed_day": date(2026, 8, 30),
        "incident_name": f"{identifier} Fire",
        "irwin_id": None,
        "fire_discovery_at": datetime(2026, 7, 16, 1, 7, tzinfo=UTC),
        "polygon_at": datetime(2026, 8, 30, 18, 45, tzinfo=UTC),
        "gis_acres": 1234.5,
        "fire_cause": "Natural",
        "incident_type_category": "WF",
        "poo_state": "US-OR",
        "percent_contained": 30.0,
        "severity": "moderate",
        "status": "published",
        "data_available_at": None,
        "updated_at": FETCHED_AT,
        "geometry_wkb": geometry if geometry is not None else valid_square_wkb(),
    }
    row.update(overrides)
    return row


def _population(*rows: dict[str, Any]) -> FirePerimeterPopulation:
    return FirePerimeterPopulation(rows=tuple(rows), fetched_at=FETCHED_AT, rejected=0, collapsed=0)


def _direct_lane(population: FirePerimeterPopulation) -> Any:
    """The registered lane with BOTH its PostgreSQL adapter and its PostgreSQL watermark substituted."""
    return replace(
        LANE_REGISTRY[FIRE_PERIMETERS_STREAM],
        adapter=DirectFirePerimetersAdapter(population=population),
        watermark=MemoizedDirectWatermark(
            watermark=SourceWatermark(day=VERSION_DAY, instant=FETCHED_AT, basis="test watermark")
        ),
    )


@pytest.mark.asyncio
async def test_an_empty_population_is_refused_by_name_and_never_governed_absent() -> None:
    """`resolve_static_lane`: an empty read of a version stamp is a FAILED READ, never settled coverage."""
    store = ObjectStore(RecordingBackend())
    session = SessionDouble()

    with pytest.raises(DirectFirePerimetersError, match="contradicts the watermark"):
        await DirectFirePerimetersAdapter(population=_population())(
            session,  # type: ignore[arg-type]
            store,
            day=VERSION_DAY,
            run_id="test-run",
        )

    kind, tier = FIRE_PERIMETERS_DIRECT_KIND, LANE_BASE_ZOOM_TIER
    assert not store.partition_exists(FIRE_PERIMETERS_STREAM, kind, tier, VERSION_DAY)
    assert not store.absence_exists(FIRE_PERIMETERS_STREAM, kind, tier, VERSION_DAY)


@pytest.mark.asyncio
async def test_the_adapter_rolls_back_before_touching_the_object_store() -> None:
    """The session lock survives the rollback; holding a snapshot across an upload pins an xmin horizon."""
    store = ObjectStore(RecordingBackend())
    session = SessionDouble()

    await DirectFirePerimetersAdapter(population=_population(_row("OR-A")))(
        session,  # type: ignore[arg-type]
        store,
        day=VERSION_DAY,
        run_id="test-run",
    )

    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_the_version_day_is_stamped_on_every_row_rather_than_any_rows_own_date() -> None:
    store = ObjectStore(RecordingBackend())

    result = await DirectFirePerimetersAdapter(population=_population(_row("OR-A"), _row("OR-B")))(
        SessionDouble(),  # type: ignore[arg-type]
        store,
        day=VERSION_DAY,
        run_id="test-run",
    )

    assert result.row_count == 2
    assert result.absence_recorded is False
    table = store.read_partition(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, VERSION_DAY)
    assert {row["snapshot_day"] for row in table.to_pylist()} == {VERSION_DAY}
    assert [row["unique_fire_identifier"] for row in table.to_pylist()] == ["OR-A", "OR-B"]


@pytest.mark.asyncio
async def test_a_governed_absence_over_this_version_day_is_retracted_at_every_tier_first() -> None:
    """`write_partition` refuses a day a marker covers, so without this the day could never publish again."""
    store = ObjectStore(RecordingBackend())
    for tier in ZOOM_TIERS:
        store.write_absence(
            GovernedAbsence(
                reason="left by the retired daily_series shape",
                upstream_response="{}",
                recorded_at=FETCHED_AT,
                run_id="old-run",
            ),
            layer=FIRE_PERIMETERS_STREAM,
            kind=FIRE_PERIMETERS_DIRECT_KIND,
            zoom=tier,
            day=VERSION_DAY,
        )

    await DirectFirePerimetersAdapter(population=_population(_row("OR-A")))(
        SessionDouble(),  # type: ignore[arg-type]
        store,
        day=VERSION_DAY,
        run_id="test-run",
    )

    for tier in ZOOM_TIERS:
        assert not store.absence_exists(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, tier, VERSION_DAY)
    assert store.partition_exists(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, VERSION_DAY)


@pytest.mark.asyncio
async def test_the_snapshot_spills_into_parts_by_geometry_bytes_in_one_global_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never sized by row count: one perimeter averages 130,583 B and every row lands in every version.

    The six placeholder bytes per row are the arithmetic under test -- a 6-byte payload against an
    8-byte ceiling is what forces one part per row -- and nothing here parses them: this calls the
    adapter directly, so no tier derivation ever reads the geometry. Substituting real WKB would
    replace a controlled length with a ~100-byte one and silently stop testing the spill boundary.
    """
    monkeypatch.setattr(adapter_module, "MAX_PART_PAYLOAD_BYTES", 8)
    store = ObjectStore(RecordingBackend())
    population = _population(
        _row("OR-A", geometry=b"\x01" * 6),
        _row("OR-B", geometry=b"\x01" * 6),
        _row("OR-C", geometry=b"\x01" * 6),
    )
    written = DirectFirePerimetersAdapter(population=population)

    result = await written(SessionDouble(), store, day=VERSION_DAY, run_id="test-run")  # type: ignore[arg-type]

    assert result.part_count == 3
    assert written.parts_written == 3
    table = store.read_partition(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, VERSION_DAY)
    assert [row["unique_fire_identifier"] for row in table.to_pylist()] == ["OR-A", "OR-B", "OR-C"]


@pytest.mark.asyncio
async def test_one_version_publishes_a_complete_four_rung_ladder_without_re_exporting() -> None:
    """The memoized watermark makes `_fill_static_day`'s race bracket a one-attempt no-op, by design."""
    store = ObjectStore(RecordingBackend())
    lane = _direct_lane(_population(_row("OR-A"), _row("OR-B")))

    outcome, parts, rows, _, _ = await fill_one_lane_day(
        SessionDouble(),  # type: ignore[arg-type]
        store,
        lane,
        day=VERSION_DAY,
        run_id="test-run",
        now=lambda: FETCHED_AT,
        today=VERSION_DAY,
        lane_day_lock=unlocked_lane_day,
        availability_storage=None,
    )

    assert outcome == "written"
    assert parts == 1
    assert rows == 2
    assert lane.watermark.reads == 2
    for tier in (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS):
        assert (
            store.read_completion_marker(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, tier, VERSION_DAY)
            is not None
        )
