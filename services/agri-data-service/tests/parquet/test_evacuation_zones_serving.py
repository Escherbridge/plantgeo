"""Serving reads for the evacuation-zones lane, against local Parquet files and an in-memory
object-store backend -- no network, no real bucket, matching `RecordingBackend`'s established use
in `tests/parquet/test_objectstore_writer.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path, partition_path
from agri_data_service.pipeline.parquet.objectstore import ABSENCE_CONTENT_TYPE, PARQUET_CONTENT_TYPE, ObjectStore
from agri_data_service.planes.evacuation_zones import (
    EvacuationZonesServingError,
    bucket_object_root,
    classify_evacuation_zones_coverage,
    evacuation_zones_scan_pattern,
    read_evacuation_zones_forecast,
    resolve_evacuation_zones_as_of,
)
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_SCHEMA, EVACUATION_ZONES_STREAM
from tests.parquet.test_evacuation_zones_lane import zone_row
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from pathlib import Path

HISTORY_FLOOR = date(2026, 7, 1)
AUGUST_FIRST = date(2026, 8, 1)
AUGUST_SIXTH = date(2026, 8, 6)
AUGUST_TENTH = date(2026, 8, 10)
TWO_ZONES = 2


def _fake_credentials() -> ObjectStoreCredentials:
    return ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="auto",
        bucket="plantgeo-warehouse",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
    )


def _write_local_snapshot(
    tmp_path: Path, store: ObjectStore, backend: RecordingBackend, *, day: date, zone_count: int = 1
) -> None:
    """Write one day's snapshot through the real writer, then mirror the bytes onto local disk so
    `pl.scan_parquet` can read them back without touching any bucket.
    """
    table = pa.Table.from_pylist([zone_row(index) for index in range(zone_count)])
    table = table.set_column(
        table.schema.get_field_index("snapshot_day"), "snapshot_day", pa.array([day] * zone_count, pa.date32())
    )
    receipt = store.write_partition(table, layer=EVACUATION_ZONES_STREAM, kind="observed", day=day)
    target = tmp_path / receipt.relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(backend.objects[receipt.key])


def test_classify_coverage_is_oregon_only_and_case_insensitive() -> None:
    assert classify_evacuation_zones_coverage("Oregon") == "covered"
    assert classify_evacuation_zones_coverage("OREGON") == "covered"
    assert classify_evacuation_zones_coverage(" oregon ") == "covered"
    assert classify_evacuation_zones_coverage("Washington") == "no_coverage"
    assert classify_evacuation_zones_coverage("Idaho") == "no_coverage"
    assert classify_evacuation_zones_coverage("Montana") == "no_coverage"


def test_bucket_object_root_and_scan_pattern_shape() -> None:
    credentials = _fake_credentials()
    store = ObjectStore(RecordingBackend(), prefix="sandbox")

    root = bucket_object_root(credentials=credentials, store=store)
    pattern = evacuation_zones_scan_pattern(root=root, kind="observed")

    assert root == "s3://plantgeo-warehouse/sandbox/"
    assert pattern == "s3://plantgeo-warehouse/sandbox/layer=evacuation-zones/kind=observed/**/*.parquet"


def test_no_coverage_is_distinct_from_a_quiet_in_coverage_day(tmp_path: Path) -> None:
    """The single most dangerous ambiguity this lane can ship: these must never be conflated."""
    store = ObjectStore(RecordingBackend())

    answer = resolve_evacuation_zones_as_of(
        store, root=tmp_path.as_posix(), as_of=AUGUST_SIXTH, state="Washington", history_floor=HISTORY_FLOOR
    )

    assert answer.status == "no_coverage"
    assert answer.answered_by_snapshot_day is None
    assert answer.zone_count == 0
    assert "structural" in answer.note


def test_not_yet_observed_when_nothing_exists_before_as_of(tmp_path: Path) -> None:
    store = ObjectStore(RecordingBackend())

    answer = resolve_evacuation_zones_as_of(
        store, root=tmp_path.as_posix(), as_of=AUGUST_SIXTH, state="Oregon", history_floor=HISTORY_FLOOR
    )

    assert answer.status == "not_yet_observed"
    assert answer.answered_by_snapshot_day is None
    assert answer.zone_count == 0


def test_resolves_to_the_newest_snapshot_at_or_before_as_of_and_names_it(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_snapshot(tmp_path, store, backend, day=AUGUST_FIRST, zone_count=TWO_ZONES)

    answer = resolve_evacuation_zones_as_of(
        store, root=tmp_path.as_posix(), as_of=AUGUST_TENTH, state="Oregon", history_floor=HISTORY_FLOOR
    )

    assert answer.status == "observed"
    assert answer.answered_by_snapshot_day == AUGUST_FIRST
    assert answer.zone_count == TWO_ZONES
    assert str(AUGUST_TENTH) in answer.note


def test_an_absence_marker_reads_as_a_quiet_day_not_a_defect(tmp_path: Path) -> None:
    store = ObjectStore(RecordingBackend())
    absence = GovernedAbsence(
        reason="Oregon OEM reported zero currently-published zones",
        upstream_response="0 features",
        recorded_at=datetime(2026, 8, 6, 12, tzinfo=UTC),
        run_id="test-run",
    )
    store.write_absence(absence, layer=EVACUATION_ZONES_STREAM, kind="observed", day=AUGUST_SIXTH)

    answer = resolve_evacuation_zones_as_of(
        store, root=tmp_path.as_posix(), as_of=AUGUST_SIXTH, state="Oregon", history_floor=HISTORY_FLOOR
    )

    assert answer.status == "observed"
    assert answer.answered_by_snapshot_day == AUGUST_SIXTH
    assert answer.zone_count == 0
    assert "quiet-season" in answer.note


def test_a_conflict_day_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    """Only a manual admin action can create this state (layer-lanes.md section 4); the reader
    must still handle it without silently picking a side.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    data_key = store.key_for(partition_path(EVACUATION_ZONES_STREAM, "observed", AUGUST_SIXTH))
    absence_key = store.key_for(absence_marker_path(EVACUATION_ZONES_STREAM, "observed", AUGUST_SIXTH))
    backend.put(data_key, b"not-real-parquet-bytes", content_type=PARQUET_CONTENT_TYPE)
    absence = GovernedAbsence(
        reason="manufactured for the conflict test",
        upstream_response="n/a",
        recorded_at=datetime(2026, 8, 6, 12, tzinfo=UTC),
        run_id="test-run",
    )
    backend.put(absence_key, absence.to_json_bytes(), content_type=ABSENCE_CONTENT_TYPE)

    answer = resolve_evacuation_zones_as_of(
        store, root=tmp_path.as_posix(), as_of=AUGUST_SIXTH, state="Oregon", history_floor=HISTORY_FLOOR
    )

    assert answer.status == "conflicted"
    assert answer.answered_by_snapshot_day == AUGUST_SIXTH
    assert answer.zone_count == 0


def test_history_floor_after_as_of_is_rejected() -> None:
    store = ObjectStore(RecordingBackend())

    with pytest.raises(EvacuationZonesServingError, match="history_floor"):
        resolve_evacuation_zones_as_of(
            store, root="unused", as_of=HISTORY_FLOOR, state="Oregon", history_floor=AUGUST_SIXTH
        )


def test_forecast_stream_is_always_honestly_empty(tmp_path: Path) -> None:
    """`horizon: none`: this lane never wrote a `kind=forecast` file, so the scan is genuinely empty."""
    forecast = read_evacuation_zones_forecast(root=tmp_path.as_posix())

    assert forecast.num_rows == 0
    assert forecast.schema.equals(EVACUATION_ZONES_SCHEMA.arrow_schema)
