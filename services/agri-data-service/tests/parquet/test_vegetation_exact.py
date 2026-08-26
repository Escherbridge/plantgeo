"""Exact vegetation reconciliation compares values, markers, schema, and all zoom tiers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.vegetation_exact import reconcile_exact_vegetation
from agri_data_service.pipeline.vegetation_source import SourceCellDay
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 8, 6)
NOW = datetime(2026, 8, 7, tzinfo=UTC)
CELL_ID = UUID("00000000-0000-4000-8000-000000000001")
BASE_ZOOM = ZOOM_TIERS[-1]


def _table(*, day: date = DAY, **overrides: object) -> pa.Table:
    row: dict[str, object] = {
        "cell_id": str(CELL_ID),
        "grid_name": "sentinel2-ndvi-0p25deg",
        "metric_name": "ndvi",
        "metric_unit": "ndvi_index",
        "observed_day": day,
        "metric_value": 0.62,
        "observation_checksum": "a" * 64,
        "data_available_at": NOW,
        "release_count": 2,
        "allowed_client_exposure": True,
        "cell_longitude": -116.2,
        "cell_latitude": 43.6,
    }
    row.update(overrides)
    return pa.Table.from_pylist(
        [row],
        schema=VEGETATION_PLANE_SCHEMA.arrow_schema,
    )


def _complete_store(base: pa.Table, *, day: date = DAY) -> ObjectStore:
    store = ObjectStore(RecordingBackend())
    receipt = store.write_partition(base, layer="vegetation", kind="observed", zoom=BASE_ZOOM, day=day)
    derive_and_write_day_tiers(store, layer="vegetation", kind="observed", day=day, run_id="test", now=lambda: NOW)
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=receipt.row_count, completed_at=NOW, run_id="test"),
        layer="vegetation",
        kind="observed",
        zoom=BASE_ZOOM,
        day=day,
    )
    return store


async def _source_rows(*_args: Any, **_kwargs: Any) -> tuple[SourceCellDay, ...]:
    return (SourceCellDay(cell_id=str(CELL_ID), observed_day=DAY, source_release_count=2),)


async def _no_source_rows(*_args: Any, **_kwargs: Any) -> tuple[SourceCellDay, ...]:
    return ()


class _Session:
    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_exact_reconciliation_is_clean_only_when_every_tier_and_marker_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _table()
    store = _complete_store(expected)

    async def read_source(*_args: Any, **_kwargs: Any) -> pa.Table:
        return expected

    monkeypatch.setattr("agri_data_service.pipeline.validation.vegetation_exact.fetch_source_cell_days", _source_rows)
    monkeypatch.setattr("agri_data_service.pipeline.validation.vegetation_exact.read_vegetation_day", read_source)

    report = await reconcile_exact_vegetation(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(CELL_ID,),
        first_day=DAY,
        last_day=DAY,
        coverage_last_day=DAY,
        sleeper=lambda _seconds: None,
    )

    assert report.is_clean
    assert report.source_row_count == 1
    assert report.parquet_base_row_count == 1
    assert report.source_sha256 == report.parquet_base_sha256


@pytest.mark.asyncio
async def test_exact_reconciliation_names_value_level_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _table()
    store = _complete_store(_table(metric_value=0.1))

    async def read_source(*_args: Any, **_kwargs: Any) -> pa.Table:
        return expected

    monkeypatch.setattr("agri_data_service.pipeline.validation.vegetation_exact.fetch_source_cell_days", _source_rows)
    monkeypatch.setattr("agri_data_service.pipeline.validation.vegetation_exact.read_vegetation_day", read_source)

    report = await reconcile_exact_vegetation(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(CELL_ID,),
        first_day=DAY,
        last_day=DAY,
        coverage_last_day=DAY,
        sleeper=lambda _seconds: None,
    )

    assert not report.is_clean
    assert any(
        finding.zoom == BASE_ZOOM and finding.kind == "row_mismatch" and "metric_value" in finding.detail
        for finding in report.findings
    )


@pytest.mark.parametrize(
    ("column", "different_value"),
    [
        ("cell_id", "00000000-0000-4000-8000-000000000002"),
        ("grid_name", "different-grid"),
        ("metric_name", "different-metric"),
        ("metric_unit", "different-unit"),
        ("observed_day", date(2026, 8, 5)),
        ("metric_value", 0.9),
        ("observation_checksum", "b" * 64),
        ("data_available_at", NOW + timedelta(hours=1)),
        ("release_count", 3),
        ("allowed_client_exposure", False),
        ("cell_longitude", -115.0),
        ("cell_latitude", 44.0),
    ],
)
@pytest.mark.asyncio
async def test_exact_reconciliation_compares_each_of_the_12_columns(
    monkeypatch: pytest.MonkeyPatch,
    column: str,
    different_value: object,
) -> None:
    expected = _table(**{column: different_value})
    store = _complete_store(_table())

    async def read_source(*_args: Any, **_kwargs: Any) -> pa.Table:
        return expected

    monkeypatch.setattr("agri_data_service.pipeline.validation.vegetation_exact.fetch_source_cell_days", _source_rows)
    monkeypatch.setattr("agri_data_service.pipeline.validation.vegetation_exact.read_vegetation_day", read_source)

    report = await reconcile_exact_vegetation(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(CELL_ID,),
        first_day=DAY,
        last_day=DAY,
        coverage_last_day=DAY,
        sleeper=lambda _seconds: None,
    )

    assert any(
        finding.zoom == BASE_ZOOM and finding.kind == "row_mismatch" and column in finding.detail
        for finding in report.findings
    )


@pytest.mark.asyncio
async def test_exact_reconciliation_rejects_extra_data_in_unsettled_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    tail_day = date(2026, 8, 7)
    store = _complete_store(_table(day=tail_day), day=tail_day)
    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.vegetation_exact.fetch_source_cell_days",
        _no_source_rows,
    )

    report = await reconcile_exact_vegetation(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(CELL_ID,),
        first_day=DAY,
        last_day=tail_day,
        coverage_last_day=DAY,
        sleeper=lambda _seconds: None,
    )

    assert not report.is_clean
    assert all(
        any(
            finding.day == tail_day and finding.zoom == zoom and "expected missing" in finding.detail
            for finding in report.findings
        )
        for zoom in (BASE_ZOOM, *DERIVED_ZOOM_TIERS)
    )


@pytest.mark.asyncio
async def test_exact_reconciliation_decodes_and_matches_absence_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    base_evidence = GovernedAbsence(
        reason="zero rows",
        upstream_response="governed query returned no rows",
        recorded_at=NOW,
        run_id="absence-base",
    )
    different_evidence = GovernedAbsence(
        reason="different",
        upstream_response=base_evidence.upstream_response,
        recorded_at=NOW,
        run_id="absence-coarse",
    )
    store = ObjectStore(RecordingBackend())
    store.write_absence(base_evidence, layer="vegetation", kind="observed", zoom=BASE_ZOOM, day=DAY)
    for zoom in DERIVED_ZOOM_TIERS:
        store.write_absence(
            different_evidence if zoom == DERIVED_ZOOM_TIERS[0] else base_evidence,
            layer="vegetation",
            kind="observed",
            zoom=zoom,
            day=DAY,
        )
    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.vegetation_exact.fetch_source_cell_days",
        _no_source_rows,
    )

    report = await reconcile_exact_vegetation(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(CELL_ID,),
        first_day=DAY,
        last_day=DAY,
        coverage_last_day=DAY,
        sleeper=lambda _seconds: None,
    )

    assert not report.is_clean
    assert any(
        finding.kind == "absence_evidence" and "differs from z13" in finding.detail for finding in report.findings
    )


@pytest.mark.asyncio
async def test_exact_reconciliation_rejects_malformed_absence_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = GovernedAbsence(
        reason="zero rows",
        upstream_response="governed query returned no rows",
        recorded_at=NOW,
        run_id="absence",
    )
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for zoom in (BASE_ZOOM, *DERIVED_ZOOM_TIERS):
        store.write_absence(evidence, layer="vegetation", kind="observed", zoom=zoom, day=DAY)
    corrupt_key = store.key_for(absence_marker_path("vegetation", "observed", DERIVED_ZOOM_TIERS[0], DAY))
    backend.objects[corrupt_key] = b"{not-json"
    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.vegetation_exact.fetch_source_cell_days",
        _no_source_rows,
    )

    report = await reconcile_exact_vegetation(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(CELL_ID,),
        first_day=DAY,
        last_day=DAY,
        coverage_last_day=DAY,
        sleeper=lambda _seconds: None,
    )

    assert not report.is_clean
    assert any(
        finding.kind == "absence_evidence" and "GovernedAbsenceError" in finding.detail for finding in report.findings
    )


@pytest.mark.asyncio
async def test_exact_reconciliation_detects_full_source_value_change(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _table()
    changed = _table(metric_value=0.9)
    store = _complete_store(expected)
    reads = 0

    async def changing_source(*_args: Any, **_kwargs: Any) -> pa.Table:
        nonlocal reads
        reads += 1
        return expected if reads == 1 else changed

    monkeypatch.setattr("agri_data_service.pipeline.validation.vegetation_exact.fetch_source_cell_days", _source_rows)
    monkeypatch.setattr("agri_data_service.pipeline.validation.vegetation_exact.read_vegetation_day", changing_source)

    report = await reconcile_exact_vegetation(
        _Session(),  # type: ignore[arg-type]
        store,
        cell_ids=(CELL_ID,),
        first_day=DAY,
        last_day=DAY,
        coverage_last_day=DAY,
        sleeper=lambda _seconds: None,
    )

    assert not report.is_clean
    assert any(finding.kind == "source_values_changed_during_reconciliation" for finding in report.findings)
