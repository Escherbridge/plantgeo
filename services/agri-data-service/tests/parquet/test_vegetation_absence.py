"""Vegetation absence evidence is propagated safely across every coarse rung."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.vegetation_absence import (
    propagate_vegetation_absence_ladders,
    retract_unsettled_vegetation_absences,
)
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 8, 1)
VEGETATION_ZOOMS = (13, 9, 5, 0)
EVIDENCE = GovernedAbsence(
    reason="zero rows",
    upstream_response="governed query returned no rows",
    recorded_at=datetime(2026, 8, 2, tzinfo=UTC),
    run_id="test-absence",
)


class _Result:
    def __init__(self, rows: tuple[dict[str, object], ...] = (), scalar: bool = True) -> None:
        self._rows = rows
        self._scalar = scalar

    def mappings(self) -> tuple[dict[str, object], ...]:
        return self._rows

    def scalar(self) -> bool:
        return self._scalar


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, _statement: Any, params: dict[str, object] | None = None) -> _Result:
        return _Result() if params is not None else _Result(scalar=True)

    async def rollback(self) -> None:
        self.rollbacks += 1


class _SourceAppearsSession(_Session):
    def __init__(self, *, appears_on_read: int = 2) -> None:
        super().__init__()
        self.appears_on_read = appears_on_read
        self.source_reads = 0

    async def execute(self, _statement: Any, params: dict[str, object] | None = None) -> _Result:
        if params is None:
            return _Result(scalar=True)
        self.source_reads += 1
        if self.source_reads < self.appears_on_read:
            return _Result()
        return _Result(
            (
                {
                    "cell_id": str(params["cell_ids"][0]),  # type: ignore[index]
                    "observed_day": DAY,
                    "source_release_count": 1,
                },
            )
        )


class _ContendedSession(_Session):
    async def execute(self, _statement: Any, params: dict[str, object] | None = None) -> _Result:
        return _Result() if params is not None else _Result(scalar=False)


class _FlakyGetBackend(RecordingBackend):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def get(self, key: str) -> bytes | None:
        if self.failures:
            self.failures -= 1
            raise OSError("transient read")
        return super().get(key)


@pytest.mark.asyncio
async def test_settled_base_absence_is_written_to_every_coarse_rung_and_resumes() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=DAY)
    session = _Session()

    first = await propagate_vegetation_absence_ladders(
        session,  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=DAY,
        dry_run=False,
        sleeper=lambda _seconds: None,
    )
    second = await propagate_vegetation_absence_ladders(
        session,  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=DAY,
        dry_run=False,
        sleeper=lambda _seconds: None,
    )

    assert first.is_clean
    assert first.written_markers == len(DERIVED_ZOOM_TIERS)
    assert second.is_clean
    assert second.eligible_days == 0
    for zoom in DERIVED_ZOOM_TIERS:
        assert store.read_absence("vegetation", "observed", zoom, DAY) == EVIDENCE


@pytest.mark.asyncio
async def test_unsettled_absence_retraction_is_dry_by_default_and_resumable() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for zoom in VEGETATION_ZOOMS:
        store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=zoom, day=DAY)
    session = _Session()

    dry = await retract_unsettled_vegetation_absences(
        session,  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        days=(DAY,),
        coverage_last_day=date(2026, 7, 31),
        sleeper=lambda _seconds: None,
    )
    applied = await retract_unsettled_vegetation_absences(
        session,  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        days=(DAY,),
        coverage_last_day=date(2026, 7, 31),
        dry_run=False,
        sleeper=lambda _seconds: None,
    )
    resumed = await retract_unsettled_vegetation_absences(
        session,  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        days=(DAY,),
        coverage_last_day=date(2026, 7, 31),
        dry_run=False,
        sleeper=lambda _seconds: None,
    )

    assert not dry.is_clean
    assert dry.would_remove_markers == len(VEGETATION_ZOOMS)
    assert applied.is_clean
    assert applied.removed_markers == len(VEGETATION_ZOOMS)
    assert resumed.is_clean
    assert resumed.already_missing_days == 1
    for zoom in VEGETATION_ZOOMS:
        assert store.read_absence("vegetation", "observed", zoom, DAY) is None


@pytest.mark.asyncio
async def test_unsettled_absence_retraction_refuses_different_evidence() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=DAY)
    store.write_absence(
        GovernedAbsence(
            reason="different",
            upstream_response="different",
            recorded_at=EVIDENCE.recorded_at,
            run_id="different",
        ),
        layer="vegetation",
        kind="observed",
        zoom=9,
        day=DAY,
    )

    report = await retract_unsettled_vegetation_absences(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        days=(DAY,),
        coverage_last_day=date(2026, 7, 31),
        dry_run=False,
        sleeper=lambda _seconds: None,
    )

    assert not report.is_clean
    assert report.removed_markers == 0
    assert "differs" in report.failures[0]


@pytest.mark.asyncio
async def test_source_day_is_never_propagated_as_absent() -> None:
    class SourceSession(_Session):
        async def execute(self, _statement: Any, params: dict[str, object] | None = None) -> _Result:
            if params is not None:
                return _Result(
                    (
                        {
                            "cell_id": str(params["cell_ids"][0]),  # type: ignore[index]
                            "observed_day": DAY,
                            "source_release_count": 1,
                        },
                    )
                )
            return _Result(scalar=True)

    store = ObjectStore(RecordingBackend())
    store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=DAY)

    report = await propagate_vegetation_absence_ladders(
        SourceSession(),  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=DAY,
        sleeper=lambda _seconds: None,
    )

    assert report.eligible_days == 0
    for zoom in DERIVED_ZOOM_TIERS:
        assert store.read_absence("vegetation", "observed", zoom, DAY) is None


@pytest.mark.asyncio
async def test_source_is_rechecked_under_lock_before_any_absence_write() -> None:
    store = ObjectStore(RecordingBackend())
    store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=DAY)

    report = await propagate_vegetation_absence_ladders(
        _SourceAppearsSession(),  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=DAY,
        dry_run=False,
        sleeper=lambda _seconds: None,
    )

    assert not report.is_clean
    assert report.written_markers == 0
    assert any("gained rows" in failure for failure in report.failures)


@pytest.mark.asyncio
async def test_closing_census_detects_source_change_after_marker_writes() -> None:
    store = ObjectStore(RecordingBackend())
    store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=DAY)

    report = await propagate_vegetation_absence_ladders(
        _SourceAppearsSession(appears_on_read=3),  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=DAY,
        dry_run=False,
        sleeper=lambda _seconds: None,
    )

    assert report.written_markers == len(DERIVED_ZOOM_TIERS)
    assert not report.is_clean
    assert any("opening and closing census" in failure for failure in report.failures)


@pytest.mark.asyncio
async def test_partial_ladder_revalidates_existing_absence_evidence() -> None:
    store = ObjectStore(RecordingBackend())
    store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=DAY)
    different = GovernedAbsence(
        reason="different",
        upstream_response=EVIDENCE.upstream_response,
        recorded_at=EVIDENCE.recorded_at,
        run_id=EVIDENCE.run_id,
    )
    store.write_absence(
        different,
        layer="vegetation",
        kind="observed",
        zoom=DERIVED_ZOOM_TIERS[0],
        day=DAY,
    )

    report = await propagate_vegetation_absence_ladders(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=DAY,
        dry_run=False,
        sleeper=lambda _seconds: None,
    )

    assert not report.is_clean
    assert any("differs from z13" in failure for failure in report.failures)


@pytest.mark.asyncio
async def test_max_days_reports_unvisited_candidate_days_as_remaining() -> None:
    next_day = date(2026, 8, 2)
    store = ObjectStore(RecordingBackend())
    for day in (DAY, next_day):
        store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=day)

    report = await propagate_vegetation_absence_ladders(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=next_day,
        max_days=1,
        dry_run=False,
        sleeper=lambda _seconds: None,
    )

    assert report.remaining_days == 1
    assert not report.is_clean


@pytest.mark.asyncio
async def test_transient_absence_read_uses_bounded_retry_budget() -> None:
    backend = _FlakyGetBackend(failures=2)
    store = ObjectStore(backend)
    store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=DAY)
    sleeps: list[float] = []

    report = await propagate_vegetation_absence_ladders(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=DAY,
        dry_run=False,
        attempts=3,
        sleeper=sleeps.append,
    )

    assert report.is_clean
    assert sleeps == [0.25, 0.5]


@pytest.mark.asyncio
async def test_lock_contention_is_nonclean_and_still_emits_progress() -> None:
    store = ObjectStore(RecordingBackend())
    store.write_absence(EVIDENCE, layer="vegetation", kind="observed", zoom=13, day=DAY)
    progress: list[dict[str, object]] = []

    report = await propagate_vegetation_absence_ladders(
        _ContendedSession(),  # type: ignore[arg-type]
        store,
        cell_ids=(uuid4(),),
        first_day=DAY,
        last_day=DAY,
        dry_run=False,
        progress=progress.append,
        sleeper=lambda _seconds: None,
    )

    assert report.contended_days == 1
    assert not report.is_clean
    assert progress == [
        {
            "completed_days": 0,
            "day": DAY.isoformat(),
            "eligible_days": 1,
            "failure_count": 0,
            "selected_days": 1,
            "visited_days": 1,
            "written_markers": 0,
        }
    ]
