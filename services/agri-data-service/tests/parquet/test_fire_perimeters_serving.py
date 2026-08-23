"""The fire-perimeters planes serving read: multi-part days as one, honest empty answers, no fall-through.

Exercised entirely against a local temp directory, never a real bucket: the writer runs against
`RecordingBackend` exactly as `test_fire_perimeters_lane.py` does, then its recorded bytes are
materialized onto disk at their own relative keys so `pl.scan_parquet` -- the real production read
path, minus `storage_options` -- reads genuine Parquet bytes rather than a hand-rolled fixture.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.planes.fire_perimeters import fire_perimeters_base_uri, read_fire_perimeters_day
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_SCHEMA, FIRE_PERIMETERS_STREAM
from tests.parquet.test_objectstore_writer import (
    BASE_TIER,
    DETAIL_TIER,
    UNPUBLISHED_ZOOM,
    RecordingBackend,
)

if TYPE_CHECKING:
    from pathlib import Path

# The rung a lane export lands on, and the zoom a viewport asks for to be served it.
BASE_TIER_REQUEST = BASE_TIER

AUGUST_SIXTH = date(2026, 8, 6)
AUGUST_SEVENTH = date(2026, 8, 7)
FAR_FUTURE_DAY = date(2099, 1, 1)
EXPECTED_MULTI_PART_ROW_COUNT = 3


def fire_perimeter_row(
    *,
    unique_fire_identifier: str,
    observed_day: date = AUGUST_SIXTH,
    geometry_wkb: bytes = b"\x01\x02\x03",
) -> dict[str, object]:
    """One row shaped exactly as `FIRE_PERIMETERS_SCHEMA` expects it, mirroring `test_fire_perimeters_lane.py`."""
    return {
        "feature_id": "11111111-1111-1111-1111-111111111111",
        "unique_fire_identifier": unique_fire_identifier,
        "observed_day": observed_day,
        "incident_name": "Example Fire",
        "irwin_id": "irwin-1",
        "fire_discovery_at": datetime(2026, 8, 6, 10, tzinfo=UTC),
        "polygon_at": datetime(2026, 8, 6, 12, tzinfo=UTC),
        "gis_acres": 1234.5,
        "fire_cause": "Lightning",
        "incident_type_category": "WF",
        "poo_state": "US-CA",
        "percent_contained": 45.0,
        "severity": "high",
        "status": "published",
        # 100% NULL in production today; never fabricated here either.
        "data_available_at": None,
        "updated_at": datetime(2026, 8, 6, 13, tzinfo=UTC),
        "geometry_wkb": geometry_wkb,
    }


def fire_perimeters_table(rows: list[dict[str, object]]) -> pa.Table:
    """Build a conformant table the way the exporter's own `write_partition` call expects one."""
    return pa.Table.from_pylist(rows).cast(FIRE_PERIMETERS_SCHEMA.arrow_schema)


def materialize_backend(backend: RecordingBackend, root: Path) -> None:
    """Write every object the writer recorded onto local disk at its own relative key.

    This is the seam between the network-free `RecordingBackend` the writer already runs against
    and the polars/`object_store` read path `planes/fire_perimeters.py` uses in production: never a
    second, hand-rolled Parquet writer, and never a real bucket.
    """
    for key, payload in backend.objects.items():
        destination = root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def test_a_day_with_no_part_files_reads_as_an_honest_empty_typed_frame(tmp_path: Path) -> None:
    store = ObjectStore(RecordingBackend())

    frame = read_fire_perimeters_day(store, requested_zoom=BASE_TIER_REQUEST, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert frame.height == 0
    assert frame.columns == list(FIRE_PERIMETERS_SCHEMA.column_names)


def test_a_future_date_answers_empty_rather_than_falling_through_to_the_newest_observed_day(tmp_path: Path) -> None:
    """`kind` is a partition, not a column branch: a date nothing was written for gets its own empty answer."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001")]),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    materialize_backend(backend, tmp_path)

    frame = read_fire_perimeters_day(
        store, requested_zoom=BASE_TIER_REQUEST, day=FAR_FUTURE_DAY, base_uri=str(tmp_path)
    )

    assert frame.height == 0
    assert frame.columns == list(FIRE_PERIMETERS_SCHEMA.column_names)


def test_a_governed_absence_marker_reads_as_zero_rows_not_an_error(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(
        GovernedAbsence(
            reason="no incident's geo.feature_observation_day fell on this UTC day",
            upstream_response="0 rows",
            recorded_at=datetime.now(UTC),
            run_id="run-1",
        ),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    materialize_backend(backend, tmp_path)

    frame = read_fire_perimeters_day(store, requested_zoom=BASE_TIER_REQUEST, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert frame.height == 0
    assert frame.columns == list(FIRE_PERIMETERS_SCHEMA.column_names)


def test_a_day_split_across_several_part_files_reads_as_one_grain_sorted_table(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for part_index, fire_id in enumerate(("2026-CA-000003", "2026-CA-000001", "2026-CA-000002")):
        store.write_partition(
            fire_perimeters_table([fire_perimeter_row(unique_fire_identifier=fire_id)]),
            layer=FIRE_PERIMETERS_STREAM,
            kind="observed",
            zoom=BASE_TIER,
            day=AUGUST_SIXTH,
            part_index=part_index,
        )
    materialize_backend(backend, tmp_path)

    frame = read_fire_perimeters_day(store, requested_zoom=BASE_TIER_REQUEST, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert frame.height == EXPECTED_MULTI_PART_ROW_COUNT
    assert frame["unique_fire_identifier"].to_list() == [
        "2026-CA-000001",
        "2026-CA-000002",
        "2026-CA-000003",
    ]


def test_geometry_survives_as_binary_wkb_with_no_srid_header(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    wkb = b"\x01\x03\x00\x00\x00"
    store.write_partition(
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001", geometry_wkb=wkb)]),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    materialize_backend(backend, tmp_path)

    frame = read_fire_perimeters_day(store, requested_zoom=BASE_TIER_REQUEST, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert frame["geometry_wkb"].to_list() == [wkb]
    assert frame.schema["geometry_wkb"] == pl.Binary


def test_a_different_day_never_leaks_into_the_answer(tmp_path: Path) -> None:
    """A day only ever reads its own directory -- another day's rows never appear beside it."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001", observed_day=AUGUST_SIXTH)]),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    store.write_partition(
        fire_perimeters_table(
            [fire_perimeter_row(unique_fire_identifier="2026-CA-000099", observed_day=AUGUST_SEVENTH)]
        ),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SEVENTH,
    )
    materialize_backend(backend, tmp_path)

    frame = read_fire_perimeters_day(store, requested_zoom=BASE_TIER_REQUEST, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert frame["unique_fire_identifier"].to_list() == ["2026-CA-000001"]


def test_fire_perimeters_base_uri_composes_bucket_and_prefix() -> None:
    credentials = ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="sjc",
        bucket="plantgeo-warehouse",
        access_key_id="access-key-value",
        secret_access_key="secret-key-value",
    )
    store = ObjectStore(RecordingBackend(), prefix="sandbox")

    assert fire_perimeters_base_uri(credentials, store) == "s3://plantgeo-warehouse/sandbox/"


# --- the zoom axis: one rung per read, and a blend that is not expressible ------------------------


def test_two_tiers_of_one_day_never_stack_into_one_incident_list(tmp_path: Path) -> None:
    """One incident published at two rungs sorts to adjacent rows and reads as a re-report, not a re-generalisation."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001")]),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    store.write_partition(
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000009")]),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=DETAIL_TIER,
        day=AUGUST_SIXTH,
    )
    materialize_backend(backend, tmp_path)

    at_base = read_fire_perimeters_day(store, requested_zoom=BASE_TIER, day=AUGUST_SIXTH, base_uri=str(tmp_path))
    at_detail = read_fire_perimeters_day(store, requested_zoom=DETAIL_TIER, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert at_base["unique_fire_identifier"].to_list() == ["2026-CA-000001"]
    assert at_detail["unique_fire_identifier"].to_list() == ["2026-CA-000009"]


def test_a_request_between_two_rungs_is_served_by_the_rung_below_it(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000009")]),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=DETAIL_TIER,
        day=AUGUST_SIXTH,
    )
    store.write_partition(
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001")]),
        layer=FIRE_PERIMETERS_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    materialize_backend(backend, tmp_path)

    served = read_fire_perimeters_day(store, requested_zoom=UNPUBLISHED_ZOOM, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert served["unique_fire_identifier"].to_list() == ["2026-CA-000009"]
