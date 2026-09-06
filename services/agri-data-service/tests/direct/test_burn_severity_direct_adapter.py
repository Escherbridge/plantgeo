"""What the direct burn-severity adapter publishes, what it governs absent, and what it refuses outright.

The full-ladder test NEEDS DuckDB's `spatial` extension twice over: once for the base-rung repair
(`support.py`) and once for `fill_one_lane_day`'s own z9/z5/z0 derivation
(`warehouse/parquet/tiers.py`) -- matching `pipeline/direct/drought/adapter.py`'s equivalent test
(`pipeline/direct/AGENTS.md`, "Drought").
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.mtbs import MtbsBurnSeverityRecord, MtbsSeverityThresholds
from agri_data_service.pipeline.direct.burn_severity.adapter import (
    BURN_SEVERITY_DIRECT_KIND,
    MAX_ROWS_PER_PART,
    DirectBurnSeverityAdapter,
    DirectBurnSeverityError,
)
from agri_data_service.pipeline.direct.burn_severity.source import BurnSeverityDaySource
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, unlocked_lane_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2020, 11, 24)
VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
FETCHED_AT = datetime(2020, 11, 24, 6, 0, tzinfo=UTC)


class SessionDouble:
    """Answers the statement-timeout pin and counts rollbacks; executes no real SQL."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:  # noqa: ARG002
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


def _record(*, fire_id: str) -> MtbsBurnSeverityRecord:
    return MtbsBurnSeverityRecord(
        natural_key=f"mtbs:{fire_id}",
        producer="mtbs",
        producer_local_id=fire_id,
        geometry=VALID_SQUARE,
        release_identifier="mtbs-2018-release-2020-11-24",
        mapping_revision=f"mtbs-2018-release-2020-11-24|{fire_id}||pre1|post1|perim1",
        data_available_at=FETCHED_AT,
        ignition_date=date(2018, 8, 1),
        ignition_year=2018,
        fire_name="Test Fire",
        fire_type="Wildfire",
        assessment_type="Initial",
        acres=1234.5,
        severity_class=None,
        severity_thresholds=MtbsSeverityThresholds(),
    )


def published_source(day: date = DAY, *, fire_count: int = 1) -> BurnSeverityDaySource:
    records = tuple(_record(fire_id=f"FIRE_{index:03d}") for index in range(fire_count))
    return BurnSeverityDaySource(day=day, ignition_years=(2018,), records=records, fetched_at=FETCHED_AT)


def empty_source(day: date = DAY) -> BurnSeverityDaySource:
    return BurnSeverityDaySource(day=day, ignition_years=(2018,), records=(), fetched_at=FETCHED_AT)


def adapter_for(source: BurnSeverityDaySource) -> DirectBurnSeverityAdapter:
    """Bind a pre-built source into the adapter so no test opens a socket."""

    async def fetch() -> BurnSeverityDaySource:
        return source

    return DirectBurnSeverityAdapter(fetch_source=fetch)


@pytest.mark.asyncio
async def test_a_published_release_day_writes_every_rung_and_marks_the_base_last() -> None:
    """The shared finalizer must produce all four rungs, and the base marker must land after them."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    adapter = adapter_for(published_source())

    outcome, parts, rows, _written_bytes, detail = await fill_one_lane_day(
        SessionDouble(),
        store,
        replace(LANE_REGISTRY[BURN_SEVERITY_STREAM], adapter=adapter),
        day=DAY,
        run_id="burn-severity-test",
        now=lambda: FETCHED_AT,
        today=date(2020, 11, 26),
        lane_day_lock=unlocked_lane_day,
    )

    assert outcome == "written"
    assert detail is not None
    assert [f"z{tier}" in detail for tier in DERIVED_ZOOM_TIERS] == [True] * len(DERIVED_ZOOM_TIERS), detail
    assert parts == 1
    assert rows == 1
    for tier in ZOOM_TIERS:
        assert store.partition_exists(BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, tier, DAY), tier
        assert store.read_completion_marker(BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, tier, DAY) is not None, (
            tier
        )
    written = [key for key in backend.objects if f"layer={BURN_SEVERITY_STREAM}/" in key]
    base_marker = next(key for key in written if "_complete.json" in key and f"zoom={LANE_BASE_ZOOM_TIER}" in key)
    assert written.index(base_marker) == max(written.index(key) for key in written if "_complete.json" in key), (
        "the base completion marker must be the last claim written for the day"
    )


@pytest.mark.asyncio
async def test_a_zero_fire_release_day_is_a_governed_absence() -> None:
    """A real fire year whose cohort falls entirely outside the bbox is an honest zero, never a refusal."""
    store = ObjectStore(RecordingBackend())
    adapter = adapter_for(empty_source())

    result = await adapter(SessionDouble(), store, day=DAY, run_id="absence-run")

    assert result.absence_recorded is True
    assert result.row_count == 0
    absence = store.read_absence(BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
    assert absence is not None
    assert "2018" in absence.upstream_response


@pytest.mark.asyncio
async def test_a_disproven_absence_is_retracted_inside_the_lock_before_the_first_write() -> None:
    """MTBS may reissue a release, adding fires to a cohort a prior fetch found empty in-bbox."""
    store = ObjectStore(RecordingBackend())
    store.write_absence(
        GovernedAbsence(
            reason=(
                "no MTBS fire from any ignition-year cohort resolving to this release day fell "
                "inside this deployment's bounding box"
            ),
            upstream_response="{}",
            recorded_at=FETCHED_AT,
            run_id="initial-empty",
        ),
        layer=BURN_SEVERITY_STREAM,
        kind=BURN_SEVERITY_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=DAY,
    )

    result = await adapter_for(published_source())(SessionDouble(), store, day=DAY, run_id="revision-run")

    assert result.row_count == 1
    assert store.absence_exists(BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is False
    assert store.partition_exists(BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is True


@pytest.mark.asyncio
async def test_a_fetch_returning_a_different_day_than_requested_is_refused() -> None:
    """The lock and the fetch must agree on which release day is being published."""

    async def fetch() -> BurnSeverityDaySource:
        return published_source(date(2021, 9, 27))

    adapter = DirectBurnSeverityAdapter(fetch_source=fetch)

    with pytest.raises(DirectBurnSeverityError, match="returned source day"):
        await adapter(SessionDouble(), ObjectStore(RecordingBackend()), day=DAY, run_id="mismatch-run")


@pytest.mark.asyncio
async def test_a_cohort_over_the_row_ceiling_splits_across_multiple_parts() -> None:
    """DO NOT DELETE. At 2.3 million vertices across a real layer, one release day WILL exceed one
    part; this proves the chunking loop, not just its constant."""
    store = ObjectStore(RecordingBackend())
    fire_count = MAX_ROWS_PER_PART + 1
    adapter = adapter_for(published_source(fire_count=fire_count))

    result = await adapter(SessionDouble(), store, day=DAY, run_id="multi-part-run")

    assert result.row_count == fire_count
    assert result.part_count == 2  # noqa: PLR2004 - one row past MAX_ROWS_PER_PART is exactly two parts
