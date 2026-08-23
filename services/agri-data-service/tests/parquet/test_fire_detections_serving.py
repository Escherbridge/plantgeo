"""The fire-detections serving read: kind isolation, the empty-scan escape hatch, and the as-of split.

No network and no real bucket: every partition is written through the same `ObjectStore` every lane
writes through, backed by `RecordingBackend`'s in-memory dict, then persisted to a local Hive-layout
directory tree so `polars.scan_parquet` can read it exactly as it would read the real bucket
(`aws_endpoint_url` et al. are irrelevant to a `file://`-shaped path; `storage_options={}` is used).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.planes.fire_detections import (
    KIND_COLUMN,
    FireDetectionsServingError,
    FireDetectionsWindowRequest,
    read_fire_detections_kind_window,
    read_fire_detections_window,
    scan_fire_detections_kind,
)
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend, with_forecast_provenance

if TYPE_CHECKING:
    from pathlib import Path

AUGUST_SIXTH = date(2026, 8, 6)
AUGUST_SEVENTH = date(2026, 8, 7)
AUGUST_EIGHTH = date(2026, 8, 8)


def _cell_day_table(*, observed_day: date, cell_longitude: float = -116.2, frp_sum: float | None = 4.27) -> pa.Table:
    """One cell-day row shaped exactly as the registered fire-detections schema expects."""
    return pa.table(
        {
            "cell_longitude": pa.array([cell_longitude], pa.float64()),
            "cell_latitude": pa.array([43.615], pa.float64()),
            "observed_day": pa.array([observed_day], pa.date32()),
            "detection_count": pa.array([2], pa.int64()),
            "frp_sum": pa.array([frp_sum], pa.float64()),
            "frp_observation_count": pa.array([1 if frp_sum is not None else 0], pa.int64()),
            "high_confidence_detection_count": pa.array([1], pa.int64()),
            "newest_observed_at": pa.array(
                [datetime(observed_day.year, observed_day.month, observed_day.day, 12, tzinfo=UTC)],
                pa.timestamp("us", tz="UTC"),
            ),
        }
    )


def _persist_to_disk(backend: RecordingBackend, base_dir: Path) -> None:
    """Flush every object the in-memory backend recorded to the real local Hive-layout tree."""
    for key, payload in backend.objects.items():
        target = base_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _write_and_persist(base_dir: Path, *, kind: str, day: date, **table_kwargs: object) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    table = _cell_day_table(observed_day=day, **table_kwargs)  # type: ignore[arg-type]
    if kind == "forecast":
        # A forecast partition carries the six provenance columns on top of the observed grain.
        table = with_forecast_provenance(table, issued_on=day)
    store.write_partition(table, layer=FIRE_DETECTIONS_STREAM, kind=kind, day=day)  # type: ignore[arg-type]
    _persist_to_disk(backend, base_dir)


def test_scan_one_kind_returns_only_that_kind_stamped(tmp_path: Path) -> None:
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="observed", day=AUGUST_SIXTH)

    table = scan_fire_detections_kind(base_uri, kind="observed").collect()

    expected_row_count = 1
    assert table.height == expected_row_count
    assert table[KIND_COLUMN].to_list() == ["observed"]
    assert table["observed_day"].to_list() == [AUGUST_SIXTH]
    assert table["frp_sum"].to_list() == [4.27]


def test_a_null_frp_sum_is_never_coerced_to_zero_on_read(tmp_path: Path) -> None:
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="observed", day=AUGUST_SIXTH, frp_sum=None)

    table = scan_fire_detections_kind(base_uri, kind="observed").collect()

    assert table["frp_sum"].to_list() == [None]
    assert table["frp_observation_count"].to_list() == [0]


def test_missing_kind_returns_empty_typed_frame_not_an_error(tmp_path: Path) -> None:
    """`kind=forecast` never having been written is exactly the framework's current, honest state."""
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="observed", day=AUGUST_SIXTH)

    table = scan_fire_detections_kind(base_uri, kind="forecast").collect()

    assert table.height == 0
    assert table[KIND_COLUMN].to_list() == []
    assert set(table.columns) == {
        "cell_longitude",
        "cell_latitude",
        "observed_day",
        "detection_count",
        "frp_sum",
        "frp_observation_count",
        "high_confidence_detection_count",
        "newest_observed_at",
        KIND_COLUMN,
    }


def test_read_fire_detections_kind_window_rejects_backwards_range(tmp_path: Path) -> None:
    with pytest.raises(FireDetectionsServingError, match="backwards"):
        read_fire_detections_kind_window(
            tmp_path.as_posix(), kind="observed", first_day=AUGUST_EIGHTH, last_day=AUGUST_SIXTH
        )


def test_window_request_rejects_a_backwards_range() -> None:
    with pytest.raises(FireDetectionsServingError, match="backwards"):
        FireDetectionsWindowRequest(first_day=AUGUST_EIGHTH, last_day=AUGUST_SIXTH, as_of_day=AUGUST_SIXTH)


def test_window_splits_by_as_of_day_never_blending_kinds(tmp_path: Path) -> None:
    """A settled day and a future day land in the SAME window answer, each traceable to its own kind."""
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="observed", day=AUGUST_SIXTH, cell_longitude=-116.2)
    _write_and_persist(tmp_path, kind="forecast", day=AUGUST_EIGHTH, cell_longitude=-116.195)

    request = FireDetectionsWindowRequest(first_day=AUGUST_SIXTH, last_day=AUGUST_EIGHTH, as_of_day=AUGUST_SIXTH)
    result = read_fire_detections_window(base_uri, request)

    expected_row_count = 2
    assert result.height == expected_row_count
    by_day = dict(zip(result["observed_day"].to_list(), result[KIND_COLUMN].to_list(), strict=True))
    assert by_day[AUGUST_SIXTH] == "observed"
    assert by_day[AUGUST_EIGHTH] == "forecast"


def test_window_never_reads_observed_data_that_falls_after_as_of_day(tmp_path: Path) -> None:
    """An observed row published for a day after `as_of_day` must not leak into the observed side."""
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="observed", day=AUGUST_SIXTH, cell_longitude=-116.2)
    _write_and_persist(tmp_path, kind="observed", day=AUGUST_SEVENTH, cell_longitude=-116.195)

    request = FireDetectionsWindowRequest(first_day=AUGUST_SIXTH, last_day=AUGUST_SEVENTH, as_of_day=AUGUST_SIXTH)
    result = read_fire_detections_window(base_uri, request)

    expected_row_count = 1
    assert result.height == expected_row_count
    assert result["observed_day"].to_list() == [AUGUST_SIXTH]
    assert result[KIND_COLUMN].to_list() == ["observed"]


def test_window_entirely_in_the_future_reads_only_forecast(tmp_path: Path) -> None:
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="forecast", day=AUGUST_EIGHTH)

    request = FireDetectionsWindowRequest(first_day=AUGUST_SEVENTH, last_day=AUGUST_EIGHTH, as_of_day=AUGUST_SIXTH)
    result = read_fire_detections_window(base_uri, request)

    expected_row_count = 1
    assert result.height == expected_row_count
    assert result[KIND_COLUMN].to_list() == ["forecast"]
    assert result["observed_day"].to_list() == [AUGUST_EIGHTH]


def test_window_entirely_settled_reads_only_observed_even_with_no_forecast_partition(tmp_path: Path) -> None:
    """No `kind=forecast` prefix exists at all yet; a fully-settled window must not need one to succeed."""
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="observed", day=AUGUST_SIXTH)

    request = FireDetectionsWindowRequest(first_day=AUGUST_SIXTH, last_day=AUGUST_SIXTH, as_of_day=AUGUST_SEVENTH)
    result = read_fire_detections_window(base_uri, request)

    expected_row_count = 1
    assert result.height == expected_row_count
    assert result[KIND_COLUMN].to_list() == ["observed"]


def test_the_as_of_day_itself_is_served_from_observed_not_forecast(tmp_path: Path) -> None:
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="observed", day=AUGUST_SIXTH)

    request = FireDetectionsWindowRequest(first_day=AUGUST_SIXTH, last_day=AUGUST_SIXTH, as_of_day=AUGUST_SIXTH)
    result = read_fire_detections_window(base_uri, request)

    assert result[KIND_COLUMN].to_list() == ["observed"]


def test_window_of_a_single_day_before_epoch_start_still_bounds_correctly(tmp_path: Path) -> None:
    """Sanity check that `timedelta`-based boundary math in the forecast branch does not off-by-one."""
    base_uri = tmp_path.as_posix()
    _write_and_persist(tmp_path, kind="forecast", day=AUGUST_SEVENTH)
    request = FireDetectionsWindowRequest(
        first_day=AUGUST_SIXTH + timedelta(days=1), last_day=AUGUST_SEVENTH, as_of_day=AUGUST_SIXTH
    )

    result = read_fire_detections_window(base_uri, request)

    assert result["observed_day"].to_list() == [AUGUST_SEVENTH]
