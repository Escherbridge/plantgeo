"""The receipted writer: what it conforms, what it refuses, and what its receipts prove."""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, date, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from plantgeo_ml_service.foundation.parquet_markers import GovernedAbsence, PartitionCompletion
from plantgeo_ml_service.pipeline.object_store import (
    JSON_CONTENT_TYPE,
    PARQUET_CONTENT_TYPE,
    PARQUET_FORMAT_VERSION,
    EmptyPartitionError,
    GovernedAbsenceConflictError,
    ImmutableObjectConflictError,
    InMemoryObjectStoreBackend,
    NullBaseColumnError,
    ObjectKeyError,
    ObjectStore,
    ObjectStoreError,
    ParquetSchemaMismatchError,
    ParquetWriteError,
    PartitionNotWrittenError,
    completed_parts_from,
    conform_to_stream_schema,
)
from plantgeo_ml_service.warehouse.streams import FIRE_DETECTIONS_SCHEMA, VEGETATION_SCHEMA, stream_schema

FORECAST_FIRE_DETECTIONS_SCHEMA = stream_schema("fire-detections", "forecast")

DAY = date(2026, 9, 19)
MOMENT = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)


def _store(prefix: str = "") -> ObjectStore:
    return ObjectStore(backend=InMemoryObjectStoreBackend(), prefix=prefix)


def _fire_detection_rows(count: int = 2) -> pa.Table:
    return pa.table(
        {
            "cell_longitude": [-120.0 + index * 0.005 for index in range(count)],
            "cell_latitude": [46.0 for _ in range(count)],
            "observed_day": [DAY for _ in range(count)],
            "detection_count": [3 for _ in range(count)],
            "frp_sum": [12.5 for _ in range(count)],
            "frp_observation_count": [3 for _ in range(count)],
            "high_confidence_detection_count": [1 for _ in range(count)],
            "newest_observed_at": [MOMENT for _ in range(count)],
        },
        schema=FIRE_DETECTIONS_SCHEMA.arrow_schema,
    )


def _fire_detection_forecast_rows(count: int = 2) -> pa.Table:
    """The same cells on the forecast side: every observed column plus the six provenance columns."""
    observed = _fire_detection_rows(count)
    provenance = {
        "forecast_run_id": ["run-0001" for _ in range(count)],
        "random_seed": [20260919 for _ in range(count)],
        "ensemble_size": [512 for _ in range(count)],
        "horizon_days": [1 for _ in range(count)],
        "issued_on": [DAY for _ in range(count)],
        "quantile": [0.5 for _ in range(count)],
    }
    columns = {name: observed.column(name) for name in observed.column_names} | provenance
    return pa.table(columns, schema=FORECAST_FIRE_DETECTIONS_SCHEMA.arrow_schema)


def test_a_written_partition_lands_at_its_key_with_a_receipt_that_proves_its_bytes() -> None:
    store = _store()

    receipt = store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)

    payload = store.backend.get(receipt.key)
    assert payload is not None
    assert receipt.sha256 == hashlib.sha256(payload).hexdigest()
    assert receipt.row_count == 2  # noqa: PLR2004
    assert receipt.byte_count == len(payload)
    assert (
        receipt.relative_path == "layer=fire-detections/kind=observed/zoom=13/year=2026/month=09/day=19/part-0.parquet"
    )
    assert store.backend.content_types[receipt.key] == PARQUET_CONTENT_TYPE


def test_a_prefix_keeps_a_dry_run_out_of_the_published_warehouse() -> None:
    store = _store(prefix="ml/scratch/2026-09-19/")

    receipt = store.write_partition(
        _fire_detection_forecast_rows(), layer="fire-detections", kind="forecast", zoom=13, day=DAY
    )

    assert receipt.key.startswith("ml/scratch/2026-09-19/layer=fire-detections/")
    assert receipt.relative_path.startswith("layer=fire-detections/")


@pytest.mark.parametrize("relative_path", ["/absolute", "layer=signal/../escape/part-0.parquet", "a\\b", ""])
def test_a_key_that_escapes_the_prefix_is_refused(relative_path: str) -> None:
    with pytest.raises(ObjectKeyError):
        _store(prefix="ml/scratch/").absolute_key(relative_path)


def test_an_empty_table_is_refused_because_emptiness_has_its_own_vocabulary() -> None:
    store = _store()
    empty = FIRE_DETECTIONS_SCHEMA.arrow_schema.empty_table()

    with pytest.raises(EmptyPartitionError):
        store.write_partition(empty, layer="fire-detections", kind="observed", zoom=13, day=DAY)


def test_a_table_missing_a_column_is_refused_rather_than_padded() -> None:
    store = _store()
    truncated = _fire_detection_rows().drop_columns(["frp_sum"])

    with pytest.raises(ParquetSchemaMismatchError):
        store.write_partition(truncated, layer="fire-detections", kind="observed", zoom=13, day=DAY)


def test_conforming_reorders_to_write_order_and_drops_nothing_the_schema_names() -> None:
    reordered = _fire_detection_rows().select(list(reversed(FIRE_DETECTIONS_SCHEMA.column_names)))

    conformed = conform_to_stream_schema(reordered, FIRE_DETECTIONS_SCHEMA)

    assert conformed.column_names == list(FIRE_DETECTIONS_SCHEMA.column_names)


def _vegetation_rows(*, cell_id: str | None) -> pa.Table:
    return pa.table(
        {
            "cell_id": [cell_id],
            "grid_name": ["sentinel2-ndvi-0p25deg"],
            "metric_name": ["ndvi"],
            "metric_unit": ["unitless"],
            "observed_day": [DAY],
            "metric_value": [0.42],
            "observation_checksum": ["c" * 64],
            "data_available_at": [MOMENT],
            "release_count": [1],
            "allowed_client_exposure": [True],
            "cell_longitude": [-120.125],
            "cell_latitude": [46.125],
        },
        schema=VEGETATION_SCHEMA.arrow_schema,
    )


def test_a_null_base_column_fails_the_write_loudly_at_the_base_rung() -> None:
    """`cell_id` is nullable ONLY so the coarse rungs may null it; a NULL base row is a defect."""
    store = _store()

    with pytest.raises(NullBaseColumnError):
        store.write_partition(_vegetation_rows(cell_id=None), layer="vegetation", kind="observed", zoom=13, day=DAY)


def test_the_same_null_is_accepted_above_the_base_rung() -> None:
    store = _store()

    receipt = store.write_partition(
        _vegetation_rows(cell_id=None), layer="vegetation", kind="observed", zoom=5, day=DAY
    )

    assert receipt.zoom == 5  # noqa: PLR2004


def test_a_completion_marker_records_what_the_export_uploaded() -> None:
    store = _store()
    part = store.write_partition(
        _fire_detection_forecast_rows(), layer="fire-detections", kind="forecast", zoom=13, day=DAY
    )
    completion = PartitionCompletion(
        part_count=1,
        row_count=part.row_count,
        completed_at=MOMENT,
        run_id="test-run",
        parts=completed_parts_from((part,)),
    )

    receipt = store.write_completion_marker(completion, layer="fire-detections", kind="forecast", zoom=13, day=DAY)

    assert receipt.relative_path.endswith("/_complete.json")
    assert receipt.part_count == 1
    assert store.backend.content_types[receipt.key] == JSON_CONTENT_TYPE


def test_a_derived_empty_marker_is_refused_at_the_base_rung() -> None:
    """The base rung generalises nothing, so its emptiness is a governed absence with its own name."""
    store = _store()
    completion = PartitionCompletion(
        part_count=0, row_count=0, completed_at=MOMENT, run_id="test-run", derived_empty=True
    )

    with pytest.raises(ParquetWriteError):
        store.write_completion_marker(completion, layer="fire-detections", kind="forecast", zoom=13, day=DAY)


def test_a_derived_empty_marker_above_the_base_takes_the_sibling_name() -> None:
    store = _store()
    completion = PartitionCompletion(
        part_count=0, row_count=0, completed_at=MOMENT, run_id="test-run", derived_empty=True
    )

    receipt = store.write_completion_marker(completion, layer="fire-detections", kind="forecast", zoom=0, day=DAY)

    assert receipt.relative_path.endswith("/_complete.empty.json")
    assert receipt.derived_empty


def test_an_absence_marker_carries_the_upstream_that_justified_it() -> None:
    store = _store()
    absence = GovernedAbsence(
        reason="source_empty",
        upstream_response="HTTP 200, zero features",
        recorded_at=MOMENT,
        run_id="test-run",
    )

    receipt = store.write_absence_marker(absence, layer="fire-detections", kind="observed", zoom=13, day=DAY)

    assert receipt.relative_path.endswith("/absent.json")
    assert b"source_empty" in (store.backend.get(receipt.key) or b"")


def test_a_written_partition_reads_back_with_the_same_digest() -> None:
    store = _store()
    written = store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)

    read = store.read_partition("fire-detections", "observed", 13, DAY)

    assert read.sha256 == written.sha256
    assert read.table.num_rows == 2  # noqa: PLR2004


def test_reading_an_absent_partition_refuses_rather_than_answering_from_elsewhere() -> None:
    with pytest.raises(PartitionNotWrittenError):
        _store().read_partition("fire-detections", "observed", 13, DAY)


def test_a_write_is_byte_identical_for_identical_input() -> None:
    """Determinism is checksummed here: same rows, same schema, same codec, same bytes."""
    first = _store()
    second = _store()

    left = first.write_partition(
        _fire_detection_forecast_rows(), layer="fire-detections", kind="forecast", zoom=13, day=DAY
    )
    right = second.write_partition(
        _fire_detection_forecast_rows(), layer="fire-detections", kind="forecast", zoom=13, day=DAY
    )

    assert left.sha256 == right.sha256


def test_the_written_bytes_are_parquet_under_the_streams_own_codec() -> None:
    store = _store()
    receipt = store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)

    payload = store.backend.get(receipt.key) or b""
    metadata = pq.ParquetFile(io.BytesIO(payload)).metadata

    assert metadata.num_rows == 2  # noqa: PLR2004
    assert metadata.row_group(0).column(0).compression == "ZSTD"


def test_a_listing_refuses_once_it_passes_its_key_budget() -> None:
    backend = InMemoryObjectStoreBackend()
    for index in range(5):
        backend.put(f"layer=signal/kind=observed/object-{index}", b"x", content_type=JSON_CONTENT_TYPE)

    with pytest.raises(ObjectStoreError):
        list(backend.list_objects("layer=signal/", max_keys=3))


def test_a_listing_strips_the_store_prefix_back_off() -> None:
    store = _store(prefix="ml/scratch/")
    store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)

    listed = store.list_relative_paths("layer=fire-detections/")

    assert listed == ("layer=fire-detections/kind=observed/zoom=13/year=2026/month=09/day=19/part-0.parquet",)


def test_two_row_orders_of_one_table_serialize_to_identical_bytes() -> None:
    """B1: without the sort in `conform_to_stream_schema` a partition's bytes are the caller's whim."""
    store = _store()
    rows = _fire_detection_rows(4)
    shuffled = rows.take([3, 1, 0, 2])

    straight = store.write_partition(rows, layer="fire-detections", kind="observed", zoom=13, day=DAY)
    store.clear_completion_marker("fire-detections", "observed", 13, DAY)
    reordered = _store().write_partition(shuffled, layer="fire-detections", kind="observed", zoom=13, day=DAY)

    assert straight.sha256 == reordered.sha256


def test_the_conformed_table_is_sorted_to_the_streams_grain() -> None:
    conformed = conform_to_stream_schema(_fire_detection_rows(4).take([3, 1, 0, 2]), FIRE_DETECTIONS_SCHEMA)

    sorted_columns = [conformed.column(name).to_pylist() for name in FIRE_DETECTIONS_SCHEMA.sort_columns]
    assert sorted_columns == [sorted(values) for values in sorted_columns]


def test_a_partition_over_a_governed_absence_is_refused_rather_than_retracting_it() -> None:
    """B2: retracting a governed absence is a manual admin action, never an implicit consequence."""
    store = _store()
    absence = GovernedAbsence(
        reason="source_empty", upstream_response="HTTP 200, zero features", recorded_at=MOMENT, run_id="test-run"
    )
    store.write_absence_marker(absence, layer="fire-detections", kind="observed", zoom=13, day=DAY)

    with pytest.raises(GovernedAbsenceConflictError):
        store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)


def test_an_absence_over_existing_data_is_refused_rather_than_contradicting_it() -> None:
    store = _store()
    store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)
    absence = GovernedAbsence(
        reason="source_empty", upstream_response="HTTP 200, zero features", recorded_at=MOMENT, run_id="test-run"
    )

    with pytest.raises(GovernedAbsenceConflictError):
        store.write_absence_marker(absence, layer="fire-detections", kind="observed", zoom=13, day=DAY)


def test_part_zero_clears_the_days_completion_marker_at_the_retraction_point() -> None:
    """The previous export's claim stops describing the day the moment its first part is replaced."""
    store = _store()
    part = store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)
    completion = PartitionCompletion(
        part_count=1, row_count=part.row_count, completed_at=MOMENT, run_id="first", parts=completed_parts_from((part,))
    )
    marker = store.write_completion_marker(completion, layer="fire-detections", kind="observed", zoom=13, day=DAY)
    assert store.backend.get(marker.key) is not None

    store.write_partition(_fire_detection_rows(3), layer="fire-detections", kind="observed", zoom=13, day=DAY)

    assert store.backend.get(marker.key) is None


def test_a_later_part_leaves_the_completion_marker_alone() -> None:
    """Only part-0 is the retraction point; a continuing export must not retract its own day."""
    store = _store()
    part = store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)
    completion = PartitionCompletion(
        part_count=1, row_count=part.row_count, completed_at=MOMENT, run_id="first", parts=completed_parts_from((part,))
    )
    marker = store.write_completion_marker(completion, layer="fire-detections", kind="observed", zoom=13, day=DAY)

    store.write_partition(
        _fire_detection_rows(3), layer="fire-detections", kind="observed", zoom=13, day=DAY, part_index=1
    )

    assert store.backend.get(marker.key) is not None


def test_an_absence_retracts_a_completion_marker_left_over_from_a_removed_export() -> None:
    store = _store()
    completion = PartitionCompletion(part_count=1, row_count=7, completed_at=MOMENT, run_id="first")
    marker = store.write_completion_marker(completion, layer="fire-detections", kind="observed", zoom=13, day=DAY)
    absence = GovernedAbsence(
        reason="source_empty", upstream_response="HTTP 200, zero features", recorded_at=MOMENT, run_id="test-run"
    )

    store.write_absence_marker(absence, layer="fire-detections", kind="observed", zoom=13, day=DAY)

    assert store.backend.get(marker.key) is None


def test_the_schema_comes_from_the_registry_and_a_callers_own_is_refused() -> None:
    """M7: a partition written under a caller's schema is unreadable by every registry-trusting reader."""
    store = _store()

    with pytest.raises(ParquetSchemaMismatchError):
        store.write_partition(
            _fire_detection_rows(),
            layer="fire-detections",
            kind="observed",
            zoom=13,
            day=DAY,
            stream=VEGETATION_SCHEMA,
        )


def test_a_forecast_partition_takes_the_forecast_schema_without_being_told() -> None:
    store = _store()

    receipt = store.write_partition(
        _fire_detection_forecast_rows(), layer="fire-detections", kind="forecast", zoom=13, day=DAY
    )

    read = store.read_partition("fire-detections", "forecast", 13, DAY)
    assert receipt.stream == "fire-detections"
    assert read.table.schema.equals(FORECAST_FIRE_DETECTIONS_SCHEMA.arrow_schema)


def test_observed_rows_are_refused_on_the_forecast_side_because_provenance_is_mandatory() -> None:
    store = _store()

    with pytest.raises(ParquetSchemaMismatchError):
        store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="forecast", zoom=13, day=DAY)


def test_an_immutable_object_adopts_an_exact_replay_and_refuses_a_different_body() -> None:
    """M10: the key names its own digest, so identical bytes are the same object, not a collision."""
    store = _store()
    relative_path = "layer=signal/kind=forecast/availability/generation=" + "a" * 64 + "/availability.parquet"

    assert store.put_immutable(relative_path, b"first", content_type=JSON_CONTENT_TYPE) is True
    assert store.put_immutable(relative_path, b"first", content_type=JSON_CONTENT_TYPE) is False
    with pytest.raises(ImmutableObjectConflictError):
        store.put_immutable(relative_path, b"second", content_type=JSON_CONTENT_TYPE)


def test_the_writer_emits_the_parquet_format_version_the_sibling_reads() -> None:
    """M11: the format version is a contract with every reader, so it is asserted, not assumed."""
    store = _store()
    receipt = store.write_partition(_fire_detection_rows(), layer="fire-detections", kind="observed", zoom=13, day=DAY)

    metadata = pq.ParquetFile(io.BytesIO(store.backend.get(receipt.key) or b"")).metadata

    assert metadata.format_version == PARQUET_FORMAT_VERSION


def test_a_retry_claim_is_a_pointer_to_work_and_refuses_a_parked_payload() -> None:
    store = _store()

    relative_path = store.write_availability_retry(b'{"day":"2026-09-19"}', layer="signal", kind="forecast", day=DAY)

    assert relative_path == "layer=signal/kind=forecast/availability/pending/day=2026-09-19.json"
    with pytest.raises(ObjectStoreError):
        store.write_availability_retry(b"", layer="signal", kind="forecast", day=DAY)
