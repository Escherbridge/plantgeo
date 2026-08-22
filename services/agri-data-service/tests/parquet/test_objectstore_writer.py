"""The object store and partition writer, exercised end to end against an in-memory backend."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.config import ObjectStoreCredentials, Settings
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import missing_partition_days, partition_path
from agri_data_service.pipeline.parquet.objectstore import (
    PARQUET_CONTENT_TYPE,
    EmptyPartitionError,
    ObjectStore,
    ParquetSchemaMismatchError,
    conform_to_stream_schema,
    polars_storage_options,
)
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_SCHEMA, SIGNAL_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Iterator

OBJECT_STORE_ENV_NAMES = (
    "OBJECT_STORE_ENDPOINT_URL",
    "OBJECT_STORE_BUCKET",
    "OBJECT_STORE_ACCESS_KEY_ID",
    "OBJECT_STORE_SECRET_ACCESS_KEY",
    "OBJECT_STORE_PREFIX",
    "OBJECT_STORE_REGION",
)

JULY_FOURTH = date(2026, 7, 4)
EXPECTED_ROW_COUNT = 3
EXPECTED_YEAR_KEY_COUNT = 2


class RecordingBackend:
    """In-memory `ObjectStoreBackend`: the whole writer runs with no network and no credentials."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        self.objects[key] = payload
        self.content_types[key] = content_type

    def list_keys(self, prefix: str) -> Iterator[str]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield key

    def size_of(self, key: str) -> int | None:
        payload = self.objects.get(key)
        return None if payload is None else len(payload)


def signal_rows(*, cell_ids: tuple[str, ...] = ("c3", "c1", "c2")) -> pa.Table:
    """Build a signal-plane table in deliberately non-schema column order and non-grain row order."""
    count = len(cell_ids)
    return pa.table(
        {
            "normalized_value": pa.array([2.5, 0.5, 1.5][:count], pa.float64()),
            "cell_id": pa.array(list(cell_ids), pa.large_string()),
            "observed_day": pa.array([JULY_FOURTH] * count, pa.date32()),
            "support_key": pa.array(["surface"] * count, pa.string()),
            "signal_name": pa.array(["precipitation"] * count, pa.string()),
            "normalized_unit": pa.array(["mm/day"] * count, pa.string()),
            "observation_count": pa.array([1] * count, pa.int64()),
            "newest_observed_at": pa.array(
                [datetime(2026, 7, 4, 12, tzinfo=UTC)] * count, pa.timestamp("us", tz="UTC")
            ),
            "coverage_fraction": pa.array([1.0] * count, pa.float64()),
            "allowed_client_exposure": pa.array([False] * count, pa.bool_()),
        }
    )


def test_write_partition_lands_at_the_frozen_key_and_returns_a_receipt() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=JULY_FOURTH)

    expected_key = partition_path(SIGNAL_PLANE_STREAM, "observed", JULY_FOURTH)
    assert receipt.key == expected_key
    assert receipt.relative_path == expected_key
    assert list(backend.objects) == [expected_key]
    assert backend.content_types[expected_key] == PARQUET_CONTENT_TYPE
    assert receipt.stream == SIGNAL_PLANE_STREAM
    assert receipt.kind == "observed"
    assert receipt.day == JULY_FOURTH
    assert receipt.row_count == EXPECTED_ROW_COUNT
    assert receipt.byte_count == len(backend.objects[expected_key])
    assert receipt.sha256 == sha256_digest(backend.objects[expected_key])


def test_written_bytes_carry_the_registered_schema_sorted_to_the_grain() -> None:
    """Column order, types and clustering are what the compression and every reader depend on."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=JULY_FOURTH)
    table = pq.read_table(io.BytesIO(backend.objects[receipt.key]))

    assert table.schema.equals(SIGNAL_PLANE_SCHEMA.arrow_schema)
    assert table.column("cell_id").to_pylist() == ["c1", "c2", "c3"]
    assert table.column("normalized_value").to_pylist() == [0.5, 1.5, 2.5]


def test_written_bytes_use_zstd() -> None:
    """Measured at 695,338 B against snappy's 874,945 B on one real month."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=JULY_FOURTH)
    metadata = pq.ParquetFile(io.BytesIO(backend.objects[receipt.key])).metadata

    assert metadata.row_group(0).column(0).compression == "ZSTD"


def test_the_store_prefix_wraps_the_layout_without_disturbing_it() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")

    receipt = store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=JULY_FOURTH)

    assert store.prefix == "sandbox/"
    assert receipt.key == f"sandbox/{receipt.relative_path}"
    assert store.relative_key(receipt.key) == receipt.relative_path
    with pytest.raises(ValueError, match="prefix"):
        store.relative_key("elsewhere/part-0.parquet")


def test_listing_returns_relative_paths_that_feed_gap_detection() -> None:
    """This is the whole point of the layout: a missing day is a missing path, never a scan."""
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")
    for day in (date(2026, 7, 1), date(2026, 7, 3)):
        store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=day)
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="forecast", day=date(2026, 7, 2))
    backend.put(
        "sandbox/layer=signal/kind=observed/year=2026/month=07/day=02/manifest.json",
        b"{}",
        content_type="application/json",
    )

    keys = store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed")

    assert keys == (
        partition_path(SIGNAL_PLANE_STREAM, "observed", date(2026, 7, 1)),
        partition_path(SIGNAL_PLANE_STREAM, "observed", date(2026, 7, 3)),
    )
    assert missing_partition_days(
        layer=SIGNAL_PLANE_STREAM,
        kind="observed",
        first_day=date(2026, 7, 1),
        last_day=date(2026, 7, 3),
        keys=keys,
    ) == (date(2026, 7, 2),)


def test_listing_narrows_by_year_and_month() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=date(2026, 6, 30))
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=JULY_FOURTH)

    assert len(store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", year=2026)) == EXPECTED_YEAR_KEY_COUNT
    assert store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", year=2026, month=7) == (
        partition_path(SIGNAL_PLANE_STREAM, "observed", JULY_FOURTH),
    )
    with pytest.raises(ValueError, match="requires the year"):
        store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", month=7)


def test_partition_exists_answers_without_downloading() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)

    assert not store.partition_exists(SIGNAL_PLANE_STREAM, "observed", JULY_FOURTH)
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=JULY_FOURTH)
    assert store.partition_exists(SIGNAL_PLANE_STREAM, "observed", JULY_FOURTH)
    assert not store.partition_exists(SIGNAL_PLANE_STREAM, "forecast", JULY_FOURTH)


def test_a_missing_column_is_refused_rather_than_written_thin() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    thin = signal_rows().drop_columns(["coverage_fraction"])

    with pytest.raises(ParquetSchemaMismatchError):
        store.write_partition(thin, layer=SIGNAL_PLANE_STREAM, kind="observed", day=JULY_FOURTH)
    assert backend.objects == {}


def test_a_null_in_a_non_nullable_column_is_refused() -> None:
    """The schema is the null gate: a null normalized_value would be a fabricated measurement."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    holed = signal_rows().set_column(
        signal_rows().schema.get_field_index("normalized_value"),
        "normalized_value",
        pa.array([1.0, None, 2.0], pa.float64()),
    )

    with pytest.raises(ParquetSchemaMismatchError):
        store.write_partition(holed, layer=SIGNAL_PLANE_STREAM, kind="observed", day=JULY_FOURTH)
    assert backend.objects == {}


def test_a_zero_row_partition_is_refused() -> None:
    """An empty file reads to gap detection as a present day, turning a real hole into coverage."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(EmptyPartitionError):
        store.write_partition(
            SIGNAL_PLANE_SCHEMA.arrow_schema.empty_table(),
            layer=SIGNAL_PLANE_STREAM,
            kind="observed",
            day=JULY_FOURTH,
        )
    assert backend.objects == {}


def test_an_unregistered_layer_cannot_be_written() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(LookupError):
        store.write_partition(signal_rows(), layer="no-such-lane", kind="observed", day=JULY_FOURTH)
    assert backend.objects == {}


def test_conform_absorbs_column_order_and_large_string_from_polars() -> None:
    """Polars hands back `large_string`; absorbing it here stops eleven lanes each discovering it."""
    conformed = conform_to_stream_schema(signal_rows(), SIGNAL_PLANE_SCHEMA)

    assert conformed.schema.equals(SIGNAL_PLANE_SCHEMA.arrow_schema)
    assert conformed.column_names == list(SIGNAL_PLANE_SCHEMA.column_names)


def test_from_settings_fails_closed_and_names_every_missing_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing may reach the network to discover that credentials are absent."""
    for name in OBJECT_STORE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    unconfigured = Settings(_env_file=None)

    with pytest.raises(ValueError, match="object storage is not configured") as caught:
        ObjectStore.from_settings(unconfigured)

    message = str(caught.value)
    assert "OBJECT_STORE_ENDPOINT_URL" in message
    assert "OBJECT_STORE_BUCKET" in message
    assert "OBJECT_STORE_ACCESS_KEY_ID" in message
    assert "OBJECT_STORE_SECRET_ACCESS_KEY" in message


def test_settings_validate_object_store_shape_at_ingress(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OBJECT_STORE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="credential-free HTTPS"):
        Settings(_env_file=None, object_store_endpoint_url="http://storage.example.com")
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        Settings(_env_file=None, object_store_endpoint_url="https://key:secret@storage.example.com")
    with pytest.raises(ValueError, match="bucket name"):
        Settings(_env_file=None, object_store_bucket="Not A Bucket")
    with pytest.raises(ValueError, match="parent traversal"):
        Settings(_env_file=None, object_store_prefix="../escape")

    normalized = Settings(_env_file=None, object_store_prefix="/sandbox/")
    assert normalized.object_store_prefix == "sandbox/"


def test_require_object_store_returns_complete_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OBJECT_STORE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    configured = Settings(
        _env_file=None,
        object_store_endpoint_url="https://storage.example.com/",
        object_store_bucket="plantgeo-warehouse",
        object_store_access_key_id="access-key-value",
        object_store_secret_access_key="secret-key-value",
        object_store_region="sjc",
    )

    credentials = configured.require_object_store()

    assert credentials.endpoint_url == "https://storage.example.com"
    assert credentials.bucket == "plantgeo-warehouse"
    assert credentials.region == "sjc"
    assert credentials.access_key_id.get_secret_value() == "access-key-value"
    assert "secret-key-value" not in repr(credentials)


def test_polars_storage_options_shape() -> None:
    credentials = ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="sjc",
        bucket="plantgeo-warehouse",
        access_key_id="access-key-value",
        secret_access_key="secret-key-value",
    )

    assert polars_storage_options(credentials) == {
        "aws_endpoint_url": "https://storage.example.com",
        "aws_region": "sjc",
        "aws_access_key_id": "access-key-value",
        "aws_secret_access_key": "secret-key-value",
    }
