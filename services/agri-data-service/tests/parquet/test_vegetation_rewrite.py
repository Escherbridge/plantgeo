"""The pinned, schema-guarded vegetation rewrite and its operator-facing dry-run contract."""

from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from click.testing import CliRunner

from agri_data_service.cli import cli
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    partition_path,
)
from agri_data_service.pipeline.parquet.gap_fill import _lane_day_lock_key
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.vegetation_rewrite import (
    LEGACY_VEGETATION_BASE_SCHEMA,
    VEGETATION_REWRITE_LAYER,
    VEGETATION_REWRITE_ZOOM_TIERS,
    VegetationRewriteManifest,
    VegetationRewriteSummary,
    load_vegetation_rewrite_manifest,
    rewrite_vegetation_manifest,
)
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.zoom import ZoomTier

DAY: Final = date(2026, 8, 1)
OTHER_DAY: Final = date(2026, 8, 2)
RUN_ID: Final = "test-vegetation-rewrite"
FROZEN_NOW: Final = datetime(2026, 8, 1, 12, tzinfo=UTC)


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


def _session() -> AsyncSession:
    return _Session()  # type: ignore[return-value]


def _manifest(*days: date) -> VegetationRewriteManifest:
    return VegetationRewriteManifest(days=days, sha256="a" * 64, byte_count=1)


def _legacy_table(day: date) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "cell_id": "00000000-0000-0000-0000-000000000001",
                "grid_name": "sentinel2-ndvi-0p25deg",
                "metric_name": "ndvi",
                "metric_unit": "unitless",
                "observed_day": day,
                "metric_value": 0.42,
                "observation_checksum": "checksum",
                "data_available_at": FROZEN_NOW,
                "release_count": 1,
                "allowed_client_exposure": True,
            }
        ],
        schema=LEGACY_VEGETATION_BASE_SCHEMA,
    )


def _current_table(day: date) -> pa.Table:
    row = _legacy_table(day).to_pylist()[0]
    row.update({"cell_longitude": -116.0, "cell_latitude": 43.0})
    return pa.Table.from_pylist([row], schema=observed_stream_schema(VEGETATION_REWRITE_LAYER).arrow_schema)


def _put_legacy_base(store: ObjectStore, backend: RecordingBackend, day: date, *, complete: bool = True) -> None:
    payload = io.BytesIO()
    pq.write_table(_legacy_table(day), payload)
    backend.put(
        store.key_for(partition_path(VEGETATION_REWRITE_LAYER, "observed", BASE_ZOOM_TIER, day, 0)),
        payload.getvalue(),
        content_type="application/vnd.apache.parquet",
    )
    if complete:
        store.write_completion_marker(
            PartitionCompletion(part_count=1, row_count=1, completed_at=FROZEN_NOW, run_id=RUN_ID),
            layer=VEGETATION_REWRITE_LAYER,
            kind="observed",
            zoom=BASE_ZOOM_TIER,
            day=day,
        )


def _put_current_rung(store: ObjectStore, day: date, tier: ZoomTier) -> None:
    store.write_partition(
        _current_table(day),
        layer=VEGETATION_REWRITE_LAYER,
        kind="observed",
        zoom=tier,
        day=day,
    )
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=1, completed_at=FROZEN_NOW, run_id=RUN_ID),
        layer=VEGETATION_REWRITE_LAYER,
        kind="observed",
        zoom=tier,
        day=day,
    )


def _store() -> tuple[ObjectStore, RecordingBackend]:
    backend = RecordingBackend()
    return ObjectStore(backend), backend


def _all_day_keys(store: ObjectStore) -> tuple[str, ...]:
    return tuple(
        key
        for tier in VEGETATION_REWRITE_ZOOM_TIERS
        for key in store.list_partition_keys(VEGETATION_REWRITE_LAYER, "observed", tier, year=DAY.year, month=DAY.month)
    )


def _write_manifest(path: Path, days: list[str] | None = None) -> tuple[bytes, str]:
    payload = json.dumps(
        {
            "schema_version": 1,
            "layer": "vegetation",
            "kind": "observed",
            "days": days or [DAY.isoformat()],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    path.write_bytes(payload)
    return payload, sha256_digest(payload)


def test_manifest_is_pinned_by_raw_sha_count_and_exact_scope(tmp_path: Path) -> None:
    path = tmp_path / "vegetation-rewrite.json"
    payload, digest = _write_manifest(path)

    manifest = load_vegetation_rewrite_manifest(path, expected_day_count=1, expected_sha256=digest)

    assert manifest.days == (DAY,)
    assert manifest.byte_count == len(payload)
    with pytest.raises(ValueError, match="holds 1 day"):
        load_vegetation_rewrite_manifest(path, expected_day_count=2, expected_sha256=digest)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_vegetation_rewrite_manifest(path, expected_day_count=1, expected_sha256="0" * 64)

    foreign = payload.replace(b'"vegetation"', b'"signal"')
    path.write_bytes(foreign)
    with pytest.raises(ValueError, match="restricted to vegetation/observed"):
        load_vegetation_rewrite_manifest(
            path,
            expected_day_count=1,
            expected_sha256=sha256_digest(foreign),
        )


@pytest.mark.asyncio
async def test_dry_run_takes_the_existing_lane_day_lock_and_deletes_nothing() -> None:
    store, backend = _store()
    _put_legacy_base(store, backend, DAY)
    for tier in VEGETATION_REWRITE_ZOOM_TIERS[1:]:
        _put_current_rung(store, DAY, tier)
    before = dict(backend.objects)
    lock_keys: list[str] = []

    @asynccontextmanager
    async def record_lock(_session: AsyncSession, key: str) -> AsyncIterator[bool]:
        lock_keys.append(key)
        yield True

    summary = await rewrite_vegetation_manifest(
        _session(),
        store,
        manifest=_manifest(DAY),
        run_id=RUN_ID,
        lane_day_lock=record_lock,
        retry_base_seconds=0,
    )

    assert summary.days[0].outcome == "would_retract"
    assert backend.objects == before
    assert lock_keys == [_lane_day_lock_key(LANE_REGISTRY[VEGETATION_REWRITE_LAYER], DAY)]


@pytest.mark.asyncio
async def test_apply_retracts_the_complete_13_9_5_0_ladder() -> None:
    store, backend = _store()
    _put_legacy_base(store, backend, DAY)
    for tier in VEGETATION_REWRITE_ZOOM_TIERS[1:]:
        _put_current_rung(store, DAY, tier)

    summary = await rewrite_vegetation_manifest(
        _session(),
        store,
        manifest=_manifest(DAY),
        run_id=RUN_ID,
        dry_run=False,
        retry_base_seconds=0,
        lane_day_lock=_granted_lock,
    )

    result = summary.days[0]
    assert result.outcome == "retracted"
    assert len(result.removed_keys) == len(VEGETATION_REWRITE_ZOOM_TIERS)
    assert _all_day_keys(store) == ()
    assert set(VEGETATION_REWRITE_ZOOM_TIERS) == {13, 9, 5, 0}


@pytest.mark.asyncio
async def test_an_already_missing_base_resumes_and_retracts_remaining_coarse_rungs() -> None:
    store, _ = _store()
    for tier in VEGETATION_REWRITE_ZOOM_TIERS[1:]:
        _put_current_rung(store, DAY, tier)

    summary = await rewrite_vegetation_manifest(
        _session(),
        store,
        manifest=_manifest(DAY),
        run_id=RUN_ID,
        dry_run=False,
        retry_base_seconds=0,
        lane_day_lock=_granted_lock,
    )

    assert summary.days[0].outcome == "retracted"
    assert summary.days[0].preflight is not None
    assert summary.days[0].preflight.base_state == "missing"
    assert _all_day_keys(store) == ()


@pytest.mark.asyncio
async def test_every_unapproved_base_state_is_refused() -> None:
    current_store, current_backend = _store()
    _put_current_rung(current_store, DAY, BASE_ZOOM_TIER)
    current_before = dict(current_backend.objects)

    absent_store, absent_backend = _store()
    absent_backend.put(
        absent_store.key_for(absence_marker_path(VEGETATION_REWRITE_LAYER, "observed", BASE_ZOOM_TIER, DAY)),
        b"{}",
        content_type="application/json",
    )

    incomplete_store, incomplete_backend = _store()
    _put_legacy_base(incomplete_store, incomplete_backend, DAY, complete=False)

    conflict_store, conflict_backend = _store()
    _put_legacy_base(conflict_store, conflict_backend, DAY)
    conflict_backend.put(
        conflict_store.key_for(absence_marker_path(VEGETATION_REWRITE_LAYER, "observed", BASE_ZOOM_TIER, DAY)),
        b"{}",
        content_type="application/json",
    )

    marker_store, _ = _store()
    marker_store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=1, completed_at=FROZEN_NOW, run_id=RUN_ID),
        layer=VEGETATION_REWRITE_LAYER,
        kind="observed",
        zoom=BASE_ZOOM_TIER,
        day=DAY,
    )

    for store, expected in (
        (current_store, "current coordinate-bearing schema"),
        (absent_store, "state is absent"),
        (incomplete_store, "state is incomplete"),
        (conflict_store, "state is conflict"),
        (marker_store, "marker-only residue"),
    ):
        summary = await rewrite_vegetation_manifest(
            _session(),
            store,
            manifest=_manifest(DAY),
            run_id=RUN_ID,
            dry_run=False,
            retry_base_seconds=0,
            lane_day_lock=_granted_lock,
        )
        assert summary.days[0].outcome == "rejected"
        assert expected in (summary.days[0].detail or "")

    assert current_backend.objects == current_before


@pytest.mark.asyncio
async def test_a_transient_part_delete_is_retried_inside_the_same_lock() -> None:
    class FlakyBackend(RecordingBackend):
        def __init__(self) -> None:
            super().__init__()
            self.failed_once: set[str] = set()
            self.fail_once_for: set[str] = set()

        def delete(self, key: str) -> None:
            if key in self.fail_once_for and key not in self.failed_once:
                self.failed_once.add(key)
                raise OSError("transient delete failure")
            super().delete(key)

    backend = FlakyBackend()
    store = ObjectStore(backend)
    _put_legacy_base(store, backend, DAY)
    base_part = store.key_for(partition_path(VEGETATION_REWRITE_LAYER, "observed", BASE_ZOOM_TIER, DAY, 0))
    backend.fail_once_for.add(base_part)

    summary = await rewrite_vegetation_manifest(
        _session(),
        store,
        manifest=_manifest(DAY),
        run_id=RUN_ID,
        dry_run=False,
        max_attempts=2,
        retry_base_seconds=0,
        lane_day_lock=_granted_lock,
    )

    assert summary.days[0].outcome == "retracted"
    assert summary.days[0].retry_count == 1
    assert base_part not in backend.objects


@pytest.mark.asyncio
async def test_a_cleanly_empty_manifest_day_is_an_idempotent_success() -> None:
    store, _ = _store()

    summary = await rewrite_vegetation_manifest(
        _session(),
        store,
        manifest=_manifest(DAY),
        run_id=RUN_ID,
        dry_run=False,
        retry_base_seconds=0,
        lane_day_lock=_granted_lock,
    )

    assert summary.days[0].outcome == "already_retracted"
    assert summary.failed is False


@pytest.mark.asyncio
async def test_a_missing_checkpoint_ignores_other_days_in_the_same_month_listing() -> None:
    store, _ = _store()
    _put_current_rung(store, OTHER_DAY, BASE_ZOOM_TIER)

    summary = await rewrite_vegetation_manifest(
        _session(),
        store,
        manifest=_manifest(DAY),
        run_id=RUN_ID,
        dry_run=False,
        retry_base_seconds=0,
        lane_day_lock=_granted_lock,
    )

    assert summary.days[0].outcome == "already_retracted"


def test_cli_is_dry_run_by_default_and_requires_the_external_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "vegetation-rewrite.json"
    _, digest = _write_manifest(path)
    seen: dict[str, object] = {}

    async def record(manifest: VegetationRewriteManifest, **options: object) -> VegetationRewriteSummary:
        seen.update(options)
        return VegetationRewriteSummary(
            run_id=str(options["run_id"]), manifest=manifest, dry_run=bool(options["dry_run"]), days=()
        )

    monkeypatch.setattr("agri_data_service.cli._parquet_rewrite_vegetation", record)
    result = CliRunner().invoke(
        cli,
        [
            "parquet-rewrite-vegetation",
            "--manifest",
            str(path),
            "--expected-day-count",
            "1",
            "--manifest-sha256",
            digest,
            "--no-progress",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["dry_run"] is True
    assert json.loads(result.output)["dry_run"] is True


@asynccontextmanager
async def _granted_lock(_session: AsyncSession, _key: str) -> AsyncIterator[bool]:
    yield True
