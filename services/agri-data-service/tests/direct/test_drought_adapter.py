"""What the direct drought adapter publishes, what it governs absent, and what it refuses outright.

The full-ladder test NEEDS DuckDB's `spatial` extension twice over: once for the base-rung repair
(`support.py`) and once for `fill_one_lane_day`'s own z9/z5/z0 derivation
(`warehouse/parquet/tiers.py`) -- unlike the climate/soil adapters this pattern was copied from,
which never open DuckDB at all. See `pipeline/direct/AGENTS.md`, "Drought".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.usdm import DroughtArea, DroughtRelease
from agri_data_service.pipeline.direct.drought.adapter import (
    DROUGHT_DIRECT_KIND,
    DirectDroughtAdapter,
    DirectDroughtError,
    DroughtSourceUnsettledError,
    no_mirrored_past_proof,
)
from agri_data_service.pipeline.direct.drought.source import DroughtDaySource
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, unlocked_lane_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.drought import DROUGHT_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 8, 18)
VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
FETCHED_AT = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
MIRRORED_PAST_PROOF = "drought is published with a release for 2026-08-25, later than 2026-08-18"


class SessionDouble:
    """Answers the statement-timeout pin and counts rollbacks; executes no real SQL."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:  # noqa: ARG002
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


def published_source(day: date = DAY) -> DroughtDaySource:
    release = DroughtRelease(
        valid_date=day.isoformat(),
        source_url=f"https://droughtmonitor.unl.edu/data/json/usdm_{day.isoformat().replace('-', '')}.json",
        areas=(DroughtArea(drought_monitor_category=0, geometry=VALID_SQUARE),),
    )
    return DroughtDaySource(day=day, release=release, fetched_at=FETCHED_AT)


def unpublished_source(day: date = DAY) -> DroughtDaySource:
    return DroughtDaySource(day=day, release=None, fetched_at=FETCHED_AT)


def adapter_for(source: DroughtDaySource, *, mirrored_past: str | None = MIRRORED_PAST_PROOF) -> DirectDroughtAdapter:
    """Bind a pre-built source into the adapter so no test opens a socket."""

    async def fetch() -> DroughtDaySource:
        return source

    return DirectDroughtAdapter(fetch_source=fetch, mirrored_past_proof=lambda: mirrored_past)


@pytest.mark.asyncio
async def test_a_published_release_writes_every_rung_and_marks_the_base_last() -> None:
    """The shared finalizer must produce all four rungs, and the base marker must land after them."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    adapter = adapter_for(published_source())

    outcome, parts, rows, _written_bytes, detail = await fill_one_lane_day(
        SessionDouble(),
        store,
        replace(LANE_REGISTRY[DROUGHT_STREAM], adapter=adapter),
        day=DAY,
        run_id="drought-test",
        now=lambda: FETCHED_AT,
        today=date(2026, 8, 20),
        lane_day_lock=unlocked_lane_day,
    )

    assert outcome == "written"
    assert detail is not None
    assert [f"z{tier}" in detail for tier in DERIVED_ZOOM_TIERS] == [True] * len(DERIVED_ZOOM_TIERS), detail
    assert parts == 1
    assert rows == 1
    for tier in ZOOM_TIERS:
        assert store.partition_exists(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, tier, DAY), tier
        assert store.read_completion_marker(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, tier, DAY) is not None, tier
    written = [key for key in backend.objects if f"layer={DROUGHT_STREAM}/" in key]
    base_marker = next(key for key in written if "_complete.json" in key and f"zoom={LANE_BASE_ZOOM_TIER}" in key)
    assert written.index(base_marker) == max(written.index(key) for key in written if "_complete.json" in key), (
        "the base completion marker must be the last claim written for the day"
    )


@pytest.mark.asyncio
async def test_an_unpublished_tuesday_with_no_later_release_is_refused_not_governed_absent() -> None:
    """DO NOT DELETE. USDM's 404 means "not yet", not "never" -- see `pipeline/direct/AGENTS.md`,
    "An all-null day is a refusal until the mirror is proven past it"."""
    store = ObjectStore(RecordingBackend())
    adapter = adapter_for(unpublished_source(), mirrored_past=None)

    with pytest.raises(DroughtSourceUnsettledError, match="nothing proves the release"):
        await adapter(SessionDouble(), store, day=DAY, run_id="unsettled-run")

    assert adapter.unsettled_refusal is not None, "the forward walk reads this to tell a refusal from a failure"
    for tier in ZOOM_TIERS:
        assert store.absence_exists(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, tier, DAY) is False, tier


@pytest.mark.asyncio
async def test_an_unpublished_tuesday_with_a_later_release_is_a_governed_absence() -> None:
    """A later published Tuesday proves USDM's weekly cadence moved past this one."""
    store = ObjectStore(RecordingBackend())
    adapter = adapter_for(unpublished_source())

    result = await adapter(SessionDouble(), store, day=DAY, run_id="absence-run")

    assert result.absence_recorded is True
    assert result.row_count == 0
    absence = store.read_absence(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
    assert absence is not None
    assert MIRRORED_PAST_PROOF in absence.upstream_response, (
        "the marker must carry WHY the absence was allowed, not only what was fetched"
    )


@pytest.mark.asyncio
async def test_the_proof_defaults_to_absent_so_an_unwired_caller_cannot_fabricate_an_absence() -> None:
    """Fail-closed: `no_mirrored_past_proof` is the default, and it proves nothing."""
    source = unpublished_source()

    async def fetch() -> DroughtDaySource:
        return source

    adapter = DirectDroughtAdapter(fetch_source=fetch)

    assert adapter.mirrored_past_proof is no_mirrored_past_proof
    with pytest.raises(DroughtSourceUnsettledError):
        await adapter(SessionDouble(), ObjectStore(RecordingBackend()), day=DAY, run_id="default-run")


@pytest.mark.asyncio
async def test_a_disproven_absence_is_retracted_inside_the_lock_before_the_first_write() -> None:
    """USDM may backfill a Tuesday it first answered 404 for; a stale absence must not block that."""
    store = ObjectStore(RecordingBackend())
    store.write_absence(
        GovernedAbsence(
            reason="USDM's archive has no release for this Tuesday",
            upstream_response="{}",
            recorded_at=FETCHED_AT,
            run_id="initial-empty",
        ),
        layer=DROUGHT_STREAM,
        kind=DROUGHT_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=DAY,
    )

    result = await adapter_for(published_source())(SessionDouble(), store, day=DAY, run_id="revision-run")

    assert result.row_count == 1
    assert store.absence_exists(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is False
    assert store.partition_exists(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is True


@pytest.mark.asyncio
async def test_a_fetch_returning_a_different_day_than_requested_is_refused() -> None:
    """The lock and the fetch must agree on which day is being published."""

    async def fetch() -> DroughtDaySource:
        return published_source(date(2026, 8, 25))

    adapter = DirectDroughtAdapter(fetch_source=fetch)

    with pytest.raises(DirectDroughtError, match="returned source day"):
        await adapter(SessionDouble(), ObjectStore(RecordingBackend()), day=DAY, run_id="mismatch-run")
