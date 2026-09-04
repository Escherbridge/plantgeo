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
import pytest

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path, completion_marker_path, partition_path
from agri_data_service.pipeline.parquet.objectstore import ABSENCE_CONTENT_TYPE, PARQUET_CONTENT_TYPE, ObjectStore
from agri_data_service.planes.fire_perimeters import (
    FirePerimetersServingError,
    fire_perimeters_base_uri,
    read_fire_perimeters_day,
    resolve_fire_perimeters_as_of,
)
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

HISTORY_FLOOR = date(2026, 7, 1)
AUGUST_FIRST = date(2026, 8, 1)
AUGUST_SIXTH = date(2026, 8, 6)
AUGUST_SEVENTH = date(2026, 8, 7)
AUGUST_TENTH = date(2026, 8, 10)
FAR_FUTURE_DAY = date(2099, 1, 1)
EXPECTED_MULTI_PART_ROW_COUNT = 3


def fire_perimeter_row(
    *,
    unique_fire_identifier: str,
    snapshot_day: date = AUGUST_SIXTH,
    observed_day: date | None = AUGUST_SIXTH,
    geometry_wkb: bytes = b"\x01\x02\x03",
) -> dict[str, object]:
    """One row shaped exactly as `FIRE_PERIMETERS_SCHEMA` expects it, mirroring `test_fire_perimeters_lane.py`."""
    return {
        "feature_id": "11111111-1111-1111-1111-111111111111",
        "unique_fire_identifier": unique_fire_identifier,
        # The version stamp the partition path carries; `observed_day` beside it is the INCIDENT's
        # own date, which the map's slider filters on and which may legitimately be null.
        "snapshot_day": snapshot_day,
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


def _write_complete_partition(  # noqa: PLR0913 - one partition coordinate per arg, none foldable
    store: ObjectStore,
    table: pa.Table,
    *,
    kind: str = "observed",
    zoom: int,
    day: date,
    part_index: int = 0,
    part_count: int = 1,
) -> None:
    """Write a part AND the completion marker that makes its day a published one.

    `write_partition` alone leaves an unfinished upload -- only the gap-fill driver marks a day
    complete -- so a fixture that stops there builds a day every reader correctly refuses to serve.
    A test whose subject IS the unfinished case writes the part and then removes the marker, which
    is why this stays a helper rather than moving into the writer.
    """
    receipt = store.write_partition(
        table, layer=FIRE_PERIMETERS_STREAM, kind=kind, zoom=zoom, day=day, part_index=part_index
    )
    store.write_completion_marker(
        PartitionCompletion(
            part_count=part_count,
            row_count=receipt.row_count,
            completed_at=datetime(2026, 8, 22, tzinfo=UTC),
            run_id="test",
        ),
        layer=FIRE_PERIMETERS_STREAM,
        kind=kind,
        zoom=zoom,
        day=day,
    )


def test_a_day_with_no_part_files_reads_as_an_honest_empty_typed_frame(tmp_path: Path) -> None:
    store = ObjectStore(RecordingBackend())

    frame = read_fire_perimeters_day(store, requested_zoom=BASE_TIER_REQUEST, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert frame.height == 0
    assert frame.columns == list(FIRE_PERIMETERS_SCHEMA.column_names)


def test_a_future_date_answers_empty_rather_than_falling_through_to_the_newest_observed_day(tmp_path: Path) -> None:
    """`kind` is a partition, not a column branch: a date nothing was written for gets its own empty answer."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001")]),
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
        _write_complete_partition(
            store,
            fire_perimeters_table([fire_perimeter_row(unique_fire_identifier=fire_id)]),
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
    _write_complete_partition(
        store,
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001", geometry_wkb=wkb)]),
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
    _write_complete_partition(
        store,
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001", snapshot_day=AUGUST_SIXTH)]),
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    _write_complete_partition(
        store,
        fire_perimeters_table(
            [fire_perimeter_row(unique_fire_identifier="2026-CA-000099", snapshot_day=AUGUST_SEVENTH)]
        ),
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
    _write_complete_partition(
        store,
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001")]),
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    _write_complete_partition(
        store,
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000009")]),
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
    _write_complete_partition(
        store,
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000009")]),
        kind="observed",
        zoom=DETAIL_TIER,
        day=AUGUST_SIXTH,
    )
    _write_complete_partition(
        store,
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001")]),
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    materialize_backend(backend, tmp_path)

    served = read_fire_perimeters_day(store, requested_zoom=UNPUBLISHED_ZOOM, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert served["unique_fire_identifier"].to_list() == ["2026-CA-000009"]


# --- incomplete days: parts without a completion marker serve zero rows --------------------------


def test_a_day_with_parts_but_no_completion_marker_reads_as_zero_rows(tmp_path: Path) -> None:
    """An upload killed part-way through left parts behind, but they are a prefix, not the day.

    A day with no listed parts answers zero rows (tested above as `test_a_day_with_no_part_files...`),
    and an unfinished day answers the same -- there is no nearest-day fallback, and the incomplete
    parts are not served.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        fire_perimeters_table([fire_perimeter_row(unique_fire_identifier="2026-CA-000001")]),
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_SIXTH,
    )
    # Remove the completion marker: the parts survive, so the day is a killed upload.
    del backend.objects[completion_marker_path(FIRE_PERIMETERS_STREAM, "observed", BASE_TIER, AUGUST_SIXTH)]
    materialize_backend(backend, tmp_path)

    frame = read_fire_perimeters_day(store, requested_zoom=BASE_TIER_REQUEST, day=AUGUST_SIXTH, base_uri=str(tmp_path))

    assert frame.height == 0
    assert frame.columns == list(FIRE_PERIMETERS_SCHEMA.column_names)


# --- `resolve_fire_perimeters_as_of`: the newest-at-or-before-D fallback and the observed_day in-frame filter -----


def test_not_yet_observed_when_nothing_exists_before_as_of(tmp_path: Path) -> None:
    store = ObjectStore(RecordingBackend())

    answer = resolve_fire_perimeters_as_of(
        store,
        base_uri=str(tmp_path),
        requested_zoom=BASE_TIER_REQUEST,
        as_of=AUGUST_SIXTH,
        history_floor=HISTORY_FLOOR,
    )

    assert answer.status == "not_yet_observed"
    assert answer.answered_by_snapshot_day is None
    assert answer.perimeter_count == 0


def test_resolves_to_the_newest_snapshot_at_or_before_as_of_and_names_it(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        fire_perimeters_table(
            [
                fire_perimeter_row(
                    unique_fire_identifier="2026-CA-000001", snapshot_day=AUGUST_FIRST, observed_day=AUGUST_FIRST
                )
            ]
        ),
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_fire_perimeters_as_of(
        store,
        base_uri=str(tmp_path),
        requested_zoom=BASE_TIER_REQUEST,
        as_of=AUGUST_TENTH,
        history_floor=HISTORY_FLOOR,
    )

    assert answer.status == "observed"
    assert answer.answered_by_snapshot_day == AUGUST_FIRST
    assert answer.perimeter_count == 1
    assert str(AUGUST_TENTH) in answer.note


def test_an_absence_marker_reads_as_a_quiet_day_not_a_defect(tmp_path: Path) -> None:
    store = ObjectStore(RecordingBackend())
    absence = GovernedAbsence(
        reason="no incident's geo.feature_observation_day fell on this UTC day",
        upstream_response="0 rows",
        recorded_at=datetime(2026, 8, 6, 12, tzinfo=UTC),
        run_id="test-run",
    )
    store.write_absence(absence, layer=FIRE_PERIMETERS_STREAM, kind="observed", zoom=BASE_TIER, day=AUGUST_SIXTH)

    answer = resolve_fire_perimeters_as_of(
        store,
        base_uri=str(tmp_path),
        requested_zoom=BASE_TIER_REQUEST,
        as_of=AUGUST_SIXTH,
        history_floor=HISTORY_FLOOR,
    )

    assert answer.status == "observed"
    assert answer.answered_by_snapshot_day == AUGUST_SIXTH
    assert answer.perimeter_count == 0
    assert "quiet-fire-season" in answer.note


def test_a_conflict_day_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    """Only a manual admin action can create this state (layer-lanes.md section 4); the reader
    must still handle it without silently picking a side.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    data_key = store.key_for(partition_path(FIRE_PERIMETERS_STREAM, "observed", BASE_TIER, AUGUST_SIXTH))
    absence_key = store.key_for(absence_marker_path(FIRE_PERIMETERS_STREAM, "observed", BASE_TIER, AUGUST_SIXTH))
    backend.put(data_key, b"not-real-parquet-bytes", content_type=PARQUET_CONTENT_TYPE)
    absence = GovernedAbsence(
        reason="manufactured for the conflict test",
        upstream_response="n/a",
        recorded_at=datetime(2026, 8, 6, 12, tzinfo=UTC),
        run_id="test-run",
    )
    backend.put(absence_key, absence.to_json_bytes(), content_type=ABSENCE_CONTENT_TYPE)

    answer = resolve_fire_perimeters_as_of(
        store,
        base_uri=str(tmp_path),
        requested_zoom=BASE_TIER_REQUEST,
        as_of=AUGUST_SIXTH,
        history_floor=HISTORY_FLOOR,
    )

    assert answer.status == "conflicted"
    assert answer.answered_by_snapshot_day == AUGUST_SIXTH
    assert answer.perimeter_count == 0


def test_history_floor_after_as_of_is_rejected() -> None:
    store = ObjectStore(RecordingBackend())

    with pytest.raises(FirePerimetersServingError, match="history_floor"):
        resolve_fire_perimeters_as_of(
            store,
            base_uri="unused",
            requested_zoom=BASE_TIER_REQUEST,
            as_of=HISTORY_FLOOR,
            history_floor=AUGUST_SIXTH,
        )


def test_an_incomplete_snapshot_is_not_the_newest_answerable_day(tmp_path: Path) -> None:
    """A day holding part files without a completion marker was killed mid-upload; its parts are a
    prefix of the day, not the day, and must never be counted as the newest answerable snapshot.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        fire_perimeters_table(
            [
                fire_perimeter_row(
                    unique_fire_identifier="2026-CA-000001", snapshot_day=AUGUST_FIRST, observed_day=AUGUST_FIRST
                )
            ]
        ),
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    # A later day's part file with no completion marker: a killed upload, not a published snapshot.
    incomplete_table = fire_perimeters_table(
        [
            fire_perimeter_row(
                unique_fire_identifier="2026-CA-000099", snapshot_day=AUGUST_SIXTH, observed_day=AUGUST_SIXTH
            )
        ]
    )
    store.write_partition(
        incomplete_table, layer=FIRE_PERIMETERS_STREAM, kind="observed", zoom=BASE_TIER, day=AUGUST_SIXTH
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_fire_perimeters_as_of(
        store,
        base_uri=str(tmp_path),
        requested_zoom=BASE_TIER_REQUEST,
        as_of=AUGUST_TENTH,
        history_floor=HISTORY_FLOOR,
    )

    assert answer.status == "observed"
    assert answer.answered_by_snapshot_day == AUGUST_FIRST  # not AUGUST_SIXTH


def test_an_undated_incident_is_kept_at_every_date_while_a_future_dated_one_is_hidden(tmp_path: Path) -> None:
    """Reproduces `src/lib/map/tile-layer-date-filter.ts`'s "at or before, plus every undated row" rule
    server-side, which is the whole reason the old `= :observed_day` equality filter was retired.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        fire_perimeters_table(
            [
                fire_perimeter_row(
                    unique_fire_identifier="past-dated", snapshot_day=AUGUST_FIRST, observed_day=AUGUST_FIRST
                ),
                fire_perimeter_row(
                    unique_fire_identifier="future-dated", snapshot_day=AUGUST_FIRST, observed_day=FAR_FUTURE_DAY
                ),
                fire_perimeter_row(unique_fire_identifier="undated", snapshot_day=AUGUST_FIRST, observed_day=None),
            ]
        ),
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_fire_perimeters_as_of(
        store,
        base_uri=str(tmp_path),
        requested_zoom=BASE_TIER_REQUEST,
        as_of=AUGUST_TENTH,
        history_floor=HISTORY_FLOOR,
    )

    assert sorted(answer.perimeters["unique_fire_identifier"].to_list()) == ["past-dated", "undated"]


def test_the_in_frame_filter_uses_the_requested_as_of_not_the_answering_snapshot_day(tmp_path: Path) -> None:
    """A snapshot answering an earlier day than requested still filters against the CALLER's slider
    date, not the day the snapshot happened to be captured on.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        fire_perimeters_table(
            [
                fire_perimeter_row(
                    unique_fire_identifier="2026-CA-000001", snapshot_day=AUGUST_FIRST, observed_day=AUGUST_SIXTH
                )
            ]
        ),
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    materialize_backend(backend, tmp_path)

    before_the_observed_day = resolve_fire_perimeters_as_of(
        store, base_uri=str(tmp_path), requested_zoom=BASE_TIER_REQUEST, as_of=AUGUST_FIRST, history_floor=HISTORY_FLOOR
    )
    at_or_after_the_observed_day = resolve_fire_perimeters_as_of(
        store, base_uri=str(tmp_path), requested_zoom=BASE_TIER_REQUEST, as_of=AUGUST_TENTH, history_floor=HISTORY_FLOOR
    )

    # Both calls resolve to the SAME snapshot (it is the only one written); only `as_of` differs,
    # and that alone is what flips the incident from hidden to visible.
    assert before_the_observed_day.answered_by_snapshot_day == AUGUST_FIRST
    assert before_the_observed_day.perimeter_count == 0
    assert at_or_after_the_observed_day.answered_by_snapshot_day == AUGUST_FIRST
    assert at_or_after_the_observed_day.perimeter_count == 1
