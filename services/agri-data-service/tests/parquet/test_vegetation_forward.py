"""Forward vegetation promotion stays day-bounded and resumes from four-tier completion markers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest

from agri_data_service.execution.vegetation_ndvi_plane import (
    CELL_BATCH_SIZE,
    RegistrationSummary,
)
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
from agri_data_service.ingest.vegetation import build_ndvi_write
from agri_data_service.pipeline.parquet import vegetation_forward as forward_module
from agri_data_service.pipeline.parquet.vegetation_forward import (
    VegetationForwardDayResult,
    VegetationForwardError,
    VegetationForwardIncompleteError,
    VegetationForwardScope,
    VegetationForwardSummary,
    bind_vegetation_forward_writer,
    changed_vegetation_forward_scope,
    forward_persisted_vegetation,
    vegetation_forward_scope,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.ingest.writer import FeatureWrite
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

_CHECKPOINT_DAY = date(2026, 8, 25)
_EXPECTED_AFFECTED_DAY_COUNT = 3
_MAX_ATTEMPTS = 3
_SOURCE_REVISION = 9


def _write(cell_key: str, observed_at: str) -> FeatureWrite:
    latitude, longitude = (float(part) for part in cell_key.split(":"))
    write = build_ndvi_write(
        {
            "cellKey": cell_key,
            "gridName": "sentinel2-ndvi-0p25deg",
            "centerLatitude": latitude,
            "centerLongitude": longitude,
            "west": longitude - 0.125,
            "south": latitude - 0.125,
            "east": longitude + 0.125,
            "north": latitude + 0.125,
            "ndvi": 0.48,
            "observedAt": observed_at,
            "sceneId": f"scene-{cell_key}-{observed_at}",
            "cloudCover": 4.2,
            "sampleCount": 25,
        },
        "vegetation",
    )
    assert write is not None
    return write


def test_scope_uses_unique_cells_and_exact_touched_publisher_days() -> None:
    scope = vegetation_forward_scope(
        (
            _write("45.1250:-122.6250", "2026-08-24T18:00:00Z"),
            _write("45.1250:-122.6250", "2026-08-25T18:00:00Z"),
            _write("45.3750:-122.3750", "2026-08-25T19:00:00Z"),
        )
    )
    assert scope.cell_keys == ("45.1250:-122.6250", "45.3750:-122.3750")
    assert scope.observed_days == (date(2026, 8, 24), date(2026, 8, 25))
    assert scope.cell_days == (
        ("45.1250:-122.6250", date(2026, 8, 24)),
        ("45.1250:-122.6250", date(2026, 8, 25)),
        ("45.3750:-122.3750", date(2026, 8, 25)),
    )
    assert scope.cutoff_day == date(2026, 8, 25)


def test_scope_refuses_a_raw_layer_override_the_governed_sql_cannot_follow() -> None:
    write = _write("45.1250:-122.6250", "2026-08-25T18:00:00Z")
    with pytest.raises(VegetationForwardError, match="requires raw layer 'vegetation'"):
        vegetation_forward_scope((replace(write, layer_reference="different-vegetation-layer"),))


class _Rows:
    def __init__(self, rows: list[Mapping[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> list[Mapping[str, object]]:
        return self._rows


class _AffectedDaySession:
    def __init__(self) -> None:
        self.parameters: list[Mapping[str, object]] = []

    async def execute(self, _statement: object, parameters: Mapping[str, object]) -> _Rows:
        self.parameters.append(parameters)
        cell_keys = cast("list[str]", parameters["prefixed_cell_keys"])
        observed_days = cast("list[date]", parameters["observed_days"])
        return _Rows(
            [
                {"cell_key": cell_key, "observed_day": observed_day}
                for cell_key, observed_day in zip(cell_keys, observed_days, strict=True)
            ]
        )

    async def rollback(self) -> None:
        return None


class _CheckpointStore:
    def __init__(self, *, marker_part_count: int, physical_part_count: int) -> None:
        self.marker = PartitionCompletion(
            part_count=marker_part_count,
            row_count=7,
            completed_at=datetime(2026, 8, 26, tzinfo=UTC),
            run_id="vegetation-forward-v1:9",
        )
        self.physical_part_count = physical_part_count

    def list_partition_keys(
        self,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        assert year == _CHECKPOINT_DAY.year
        assert month == _CHECKPOINT_DAY.month
        day = _CHECKPOINT_DAY
        return (
            completion_marker_path(layer, kind, zoom, day),
            *(partition_path(layer, kind, zoom, day, index) for index in range(self.physical_part_count)),
        )

    def read_completion_marker(
        self,
        _layer: str,
        _kind: PartitionKind,
        _zoom: ZoomTier,
        _day: date,
    ) -> PartitionCompletion:
        return self.marker


@pytest.mark.parametrize(
    ("marker_part_count", "physical_part_count"),
    [(1, 0), (2, 1), (1, 2)],
)
def test_checkpoint_refuses_marker_only_truncated_and_surplus_tiers(
    marker_part_count: int,
    physical_part_count: int,
) -> None:
    store = _CheckpointStore(
        marker_part_count=marker_part_count,
        physical_part_count=physical_part_count,
    )
    assert not forward_module._ladder_checkpoint_is_current(
        cast("ObjectStore", store),
        day=date(2026, 8, 25),
        source_revision=_SOURCE_REVISION,
    )


async def test_checkpoint_is_verified_while_the_lane_day_lock_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_held = False

    @asynccontextmanager
    async def recording_lock(_session: AsyncSession, _key: str) -> AsyncIterator[bool]:
        nonlocal lock_held
        lock_held = True
        try:
            yield True
        finally:
            lock_held = False

    original_check = forward_module._ladder_checkpoint_is_current

    def checked(store: ObjectStore, *, day: date, source_revision: int) -> bool:
        assert lock_held
        return original_check(store, day=day, source_revision=source_revision)

    async def forbidden_fill(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a current physical ladder must not be rewritten")

    monkeypatch.setattr(forward_module, "_ladder_checkpoint_is_current", checked)
    monkeypatch.setattr(forward_module, "fill_one_lane_day", forbidden_fill)
    result = await forward_module._write_day_once(
        cast("AsyncSession", _AffectedDaySession()),
        cast("ObjectStore", _CheckpointStore(marker_part_count=1, physical_part_count=1)),
        day=date(2026, 8, 25),
        source_revision=_SOURCE_REVISION,
        lane_day_lock=recording_lock,
    )

    assert result.outcome == "checkpointed"
    assert lock_held is False


async def test_affected_day_lookup_bounds_cell_batches_and_restricts_to_touched_days() -> None:
    session = _AffectedDaySession()
    first_day = date(2026, 7, 25)
    observed_days = tuple(first_day + timedelta(days=offset) for offset in range(32))
    cell_days = tuple(
        (f"cell-{index}", observed_days[index % len(observed_days)]) for index in range(CELL_BATCH_SIZE + 1)
    )
    scope = VegetationForwardScope(
        cell_keys=tuple(f"cell-{index}" for index in range(CELL_BATCH_SIZE + 1)),
        cutoff_day=date(2026, 8, 25),
        observed_days=observed_days,
        cell_days=cell_days,
    )

    days = await forward_module._affected_days(
        cast("AsyncSession", session),
        scope=scope,
        source_release_id=UUID(int=1),
    )

    assert days == tuple(reversed(observed_days))
    assert [len(cast("list[str]", parameters["prefixed_cell_keys"])) for parameters in session.parameters] == [
        CELL_BATCH_SIZE,
        1,
    ]
    assert [len(cast("list[date]", parameters["observed_days"])) for parameters in session.parameters] == [
        CELL_BATCH_SIZE,
        1,
    ]
    assert all(parameters["source_release_id"] == UUID(int=1) for parameters in session.parameters)


async def test_changed_scope_is_exact_deduplicated_and_operator_bounded() -> None:
    class _ChangedScopeSession:
        def __init__(self) -> None:
            self.parameters: Mapping[str, object] | None = None

        async def execute(self, _statement: object, parameters: Mapping[str, object]) -> _Rows:
            self.parameters = parameters
            return _Rows(
                [
                    {"cell_key": "45.1250:-122.6250", "observed_day": date(2026, 8, 24)},
                    {"cell_key": "45.1250:-122.6250", "observed_day": date(2026, 8, 24)},
                    {"cell_key": "45.3750:-122.3750", "observed_day": date(2026, 8, 25)},
                ]
            )

    session = _ChangedScopeSession()
    since = datetime(2026, 8, 26, tzinfo=UTC)
    scope = await changed_vegetation_forward_scope(
        cast("AsyncSession", session),
        since=since,
        through_day=date(2026, 8, 25),
    )

    assert session.parameters == {"since": since, "through_day": date(2026, 8, 25)}
    assert scope.cell_keys == ("45.1250:-122.6250", "45.3750:-122.3750")
    assert scope.observed_days == (date(2026, 8, 24), date(2026, 8, 25))
    assert scope.cell_days == (
        ("45.1250:-122.6250", date(2026, 8, 24)),
        ("45.3750:-122.3750", date(2026, 8, 25)),
    )


async def test_changed_scope_refuses_an_empty_window() -> None:
    class _EmptyChangedScopeSession:
        async def execute(self, _statement: object, _parameters: Mapping[str, object]) -> _Rows:
            return _Rows([])

    with pytest.raises(VegetationForwardError, match="no valid raw vegetation cell-day changed"):
        await changed_vegetation_forward_scope(
            cast("AsyncSession", _EmptyChangedScopeSession()),
            since=datetime(2026, 8, 26, tzinfo=UTC),
            through_day=date(2026, 8, 25),
        )


async def test_forward_run_skips_current_ladders_and_bounds_noncheckpointed_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = date(2026, 8, 25)
    middle = date(2026, 8, 24)
    oldest = date(2026, 8, 23)
    registration = cast("RegistrationSummary", object())
    writes = (_write("45.1250:-122.6250", "2026-08-25T18:00:00Z"),)
    written: list[date] = []

    async def fake_prepare(
        _session: AsyncSession,
        *,
        scope: VegetationForwardScope,
        max_attempts: int,
        retry_base_seconds: float,
        sleep: object,
    ) -> tuple[RegistrationSummary, int, tuple[date, ...]]:
        assert scope.observed_days == (newest,)
        assert max_attempts == _MAX_ATTEMPTS
        assert retry_base_seconds == 1.0
        assert sleep is not None
        return registration, 185_230, (newest, middle, oldest)

    async def fake_write_day(
        _session: AsyncSession,
        _store: ObjectStore,
        *,
        day: date,
        **_kwargs: object,
    ) -> VegetationForwardDayResult:
        if day == newest:
            return VegetationForwardDayResult(day=day, outcome="checkpointed", attempt_count=1)
        written.append(day)
        return VegetationForwardDayResult(day=day, outcome="written", attempt_count=1, row_count=9)

    monkeypatch.setattr(forward_module, "_prepare_forward", fake_prepare)
    monkeypatch.setattr(forward_module, "_write_day_with_retry", fake_write_day)

    summary = await forward_persisted_vegetation(
        cast("AsyncSession", object()),
        cast("ObjectStore", object()),
        writes,
        max_days_per_run=1,
        monotonic=lambda: 0.0,
    )

    assert written == [middle]
    assert summary.stop_reason == "day_limit"
    assert summary.checkpointed_day_count == 1
    assert summary.written_day_count == 1
    assert summary.affected_day_count == _EXPECTED_AFFECTED_DAY_COUNT
    assert summary.to_details()["pending_days"] == 1
    assert summary.to_details()["forward_complete"] == 0
    assert all(isinstance(value, int) for value in summary.to_details().values())


async def test_day_writer_retries_with_bounded_backoff_and_then_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    delays: list[float] = []

    async def flaky_write(
        _session: AsyncSession,
        _store: ObjectStore,
        *,
        day: date,
        source_revision: int,
        lane_day_lock: object,
    ) -> VegetationForwardDayResult:
        nonlocal attempts
        attempts += 1
        assert source_revision == _SOURCE_REVISION
        assert lane_day_lock is not None
        if attempts < _MAX_ATTEMPTS:
            raise OSError("transient object-store failure")
        return VegetationForwardDayResult(day=day, outcome="written", attempt_count=1)

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(forward_module, "_write_day_once", flaky_write)
    session = _AffectedDaySession()
    result = await forward_module._write_day_with_retry(
        cast("AsyncSession", session),
        cast("ObjectStore", object()),
        day=date(2026, 8, 25),
        source_revision=_SOURCE_REVISION,
        max_attempts=_MAX_ATTEMPTS,
        retry_base_seconds=1.0,
        lane_day_lock=cast("forward_module.VegetationLaneDayLock", object()),
        sleep=record_delay,
    )

    assert result.outcome == "written"
    assert result.attempt_count == _MAX_ATTEMPTS
    assert delays == [1.0, 2.0]


async def test_bound_callback_raises_when_a_bounded_run_leaves_pending_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes = (_write("45.1250:-122.6250", "2026-08-25T18:00:00Z"),)
    scope = vegetation_forward_scope(writes)
    summary = VegetationForwardSummary(
        scope=scope,
        registration=cast("RegistrationSummary", object()),
        source_revision=9,
        affected_day_count=2,
        examined_day_count=1,
        stop_reason="time_budget",
        days=(VegetationForwardDayResult(day=date(2026, 8, 25), outcome="written", attempt_count=1),),
    )

    async def incomplete(*_args: object, **_kwargs: object) -> VegetationForwardSummary:
        return summary

    monkeypatch.setattr(forward_module, "forward_persisted_vegetation", incomplete)
    callback = bind_vegetation_forward_writer(
        cast("AsyncSession", object()),
        store=cast("ObjectStore", object()),
    )
    with pytest.raises(VegetationForwardIncompleteError, match="1 pending day"):
        await callback(writes)
