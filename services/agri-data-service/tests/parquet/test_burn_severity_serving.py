"""The burn-severity planes serving read: as-of resolution across five discrete releases, never blended.

Exercised entirely against a local temp directory, never a real bucket: the writer runs against
`RecordingBackend` exactly as `test_burn_severity_lane.py` does, then its recorded bytes are
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
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.planes.burn_severity import (
    BURN_SEVERITY_EXPOSURE_CONTRADICTION,
    burn_severity_base_uri,
    read_burn_severity_release_day,
    resolve_burn_severity_as_of,
)
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA, BURN_SEVERITY_STREAM
from tests.parquet.test_objectstore_writer import (
    BASE_TIER,
    DETAIL_TIER,
    UNPUBLISHED_ZOOM,
    RecordingBackend,
)

if TYPE_CHECKING:
    from pathlib import Path

# The real governed release dates for fire years 2020 and 2022 (ingest/mtbs.py MTBS_ANNUAL_RELEASE_DATES) --
# two of this lane's whole-history five, used here to exercise "as of" resolution honestly rather than
# against invented dates.
# The rung a lane export lands on, and the zoom a viewport asks for to be served it.
BASE_TIER_REQUEST = BASE_TIER

EARLIER_RELEASE = date(2022, 4, 28)
LATER_RELEASE = date(2024, 8, 22)
BETWEEN_RELEASES = date(2023, 6, 1)
BEFORE_ANY_RELEASE = date(2021, 1, 1)
FAR_FUTURE_DAY = date(2099, 1, 1)
EXPECTED_MULTI_PART_ROW_COUNT = 3


def burn_severity_row(
    *,
    fire_id: str,
    observed_day: date,
    acres: float | None = 1234.5,
    geom: bytes = b"\x01\x03\x00\x00\x00",
) -> dict[str, object]:
    """One row shaped exactly as `BURN_SEVERITY_SCHEMA` expects it, mirroring `test_burn_severity_lane.py`."""
    return {
        "feature_id": f"feature-{fire_id}",
        "fire_id": fire_id,
        "natural_key": f"mtbs:{fire_id}",
        "release_identifier": f"mtbs-release-{observed_day.isoformat()}",
        "mapping_revision": f"mtbs-release-{observed_day.isoformat()}|m1|Initial|p1|po1",
        "fire_year": observed_day.year,
        "ignition_date": date(observed_day.year - 2, 7, 4),
        "observed_day": observed_day,
        "data_available_at": datetime(observed_day.year, observed_day.month, observed_day.day, tzinfo=UTC),
        "fire_name": "TEST FIRE",
        "fire_type": "Wildfire",
        "assessment_type": "Initial",
        "acres": acres,
        # Null on every published row today, by design -- MTBS has no polygon-level class.
        "severity_class": None,
        "dnbr_offset": 100,
        "dnbr_standard_deviation": 50,
        "nodata_threshold": -970,
        "greenness_threshold": 100,
        "low_threshold": 76,
        "moderate_threshold": 306,
        "high_threshold": 615,
        "allowed_client_exposure": False,
        "geom": geom,
    }


def burn_severity_table(rows: list[dict[str, object]]) -> pa.Table:
    """Build a conformant table the way the exporter's own `write_partition` call expects one."""
    return pa.Table.from_pylist(rows).cast(BURN_SEVERITY_SCHEMA.arrow_schema)


def materialize_backend(backend: RecordingBackend, root: Path) -> None:
    """Write every object the writer recorded onto local disk at its own relative key.

    This is the seam between the network-free `RecordingBackend` the writer already runs against and
    the polars/`object_store` read path `planes/burn_severity.py` uses in production: never a second,
    hand-rolled Parquet writer, and never a real bucket.
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
        table, layer=BURN_SEVERITY_STREAM, kind=kind, zoom=zoom, day=day, part_index=part_index
    )
    store.write_completion_marker(
        PartitionCompletion(
            part_count=part_count,
            row_count=receipt.row_count,
            completed_at=datetime(2026, 8, 22, tzinfo=UTC),
            run_id="test",
        ),
        layer=BURN_SEVERITY_STREAM,
        kind=kind,
        zoom=zoom,
        day=day,
    )


def test_a_day_with_no_part_files_reads_as_an_honest_empty_typed_frame(tmp_path: Path) -> None:
    store = ObjectStore(RecordingBackend())

    frame = read_burn_severity_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, day=EARLIER_RELEASE, base_uri=str(tmp_path)
    )

    assert frame.height == 0
    assert frame.columns == list(BURN_SEVERITY_SCHEMA.column_names)


def test_a_future_date_answers_empty_rather_than_falling_through_to_the_newest_observed_release(
    tmp_path: Path,
) -> None:
    """`kind` is a partition, not a column branch: a date nothing was written for gets its own empty answer."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, day=FAR_FUTURE_DAY, base_uri=str(tmp_path)
    )

    assert frame.height == 0
    assert frame.columns == list(BURN_SEVERITY_SCHEMA.column_names)


def test_a_governed_absence_marker_reads_as_zero_rows_not_an_error(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(
        GovernedAbsence(
            reason="no MTBS fire in geo.features is dated to this release day",
            upstream_response="geo.features query returned 0 rows",
            recorded_at=datetime.now(UTC),
            run_id="run-1",
        ),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, day=EARLIER_RELEASE, base_uri=str(tmp_path)
    )

    assert frame.height == 0
    assert frame.columns == list(BURN_SEVERITY_SCHEMA.column_names)


def test_a_release_split_across_several_part_files_reads_as_one_grain_sorted_table(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for part_index, fire_id in enumerate(("2022PNW00003", "2022PNW00001", "2022PNW00002")):
        _write_complete_partition(
            store,
            burn_severity_table([burn_severity_row(fire_id=fire_id, observed_day=LATER_RELEASE)]),
            kind="observed",
            zoom=BASE_TIER,
            day=LATER_RELEASE,
            part_index=part_index,
        )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, day=LATER_RELEASE, base_uri=str(tmp_path)
    )

    assert frame.height == EXPECTED_MULTI_PART_ROW_COUNT
    assert frame["fire_id"].to_list() == ["2022PNW00001", "2022PNW00002", "2022PNW00003"]


def test_geometry_survives_as_binary_wkb_with_no_srid_header(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    wkb = b"\x01\x03\x00\x00\x00"
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2022PNW00001", observed_day=LATER_RELEASE, geom=wkb)]),
        kind="observed",
        zoom=BASE_TIER,
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, day=LATER_RELEASE, base_uri=str(tmp_path)
    )

    assert frame["geom"].to_list() == [wkb]
    assert frame.schema["geom"] == pl.Binary


def test_a_different_release_never_leaks_into_an_exact_day_answer(tmp_path: Path) -> None:
    """A day only ever reads its own directory -- another release's rows never appear beside it."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2022PNW00099", observed_day=LATER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, day=EARLIER_RELEASE, base_uri=str(tmp_path)
    )

    assert frame["fire_id"].to_list() == ["2020PNW00001"]


def test_burn_severity_base_uri_composes_bucket_and_prefix() -> None:
    credentials = ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="sjc",
        bucket="plantgeo-warehouse",
        access_key_id="access-key-value",
        secret_access_key="secret-key-value",
    )
    store = ObjectStore(RecordingBackend(), prefix="sandbox")

    assert burn_severity_base_uri(credentials, store) == "s3://plantgeo-warehouse/sandbox/"


# --- "as of" resolution: five discrete releases across all of history, never a daily series -------


def test_as_of_between_two_releases_resolves_to_the_older_one_and_names_it(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2022PNW00001", observed_day=LATER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(
        store, requested_zoom=BASE_TIER_REQUEST, requested_day=BETWEEN_RELEASES, base_uri=str(tmp_path)
    )

    assert answer.release_day == EARLIER_RELEASE
    assert answer.is_governed_absence is False
    assert answer.rows["fire_id"].to_list() == ["2020PNW00001"]
    assert answer.exposure_contradiction == BURN_SEVERITY_EXPOSURE_CONTRADICTION


def test_as_of_past_the_newest_release_answers_the_newest_release_not_a_projection(tmp_path: Path) -> None:
    """This lane ships no `kind=forecast` stream -- a future date resolves to the newest past release."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2022PNW00001", observed_day=LATER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(
        store, requested_zoom=BASE_TIER_REQUEST, requested_day=FAR_FUTURE_DAY, base_uri=str(tmp_path)
    )

    assert answer.release_day == LATER_RELEASE
    assert answer.rows["fire_id"].to_list() == ["2022PNW00001"]


def test_as_of_before_the_earliest_release_is_an_honest_absence_not_a_zero_row_release(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(
        store, requested_zoom=BASE_TIER_REQUEST, requested_day=BEFORE_ANY_RELEASE, base_uri=str(tmp_path)
    )

    assert answer.release_day is None
    assert answer.is_governed_absence is False
    assert answer.rows.height == 0
    assert answer.rows.columns == list(BURN_SEVERITY_SCHEMA.column_names)


def test_as_of_a_governed_absence_release_reports_the_absence_rather_than_a_data_gap(tmp_path: Path) -> None:
    """A governed absence names the release it belongs to -- it is not indistinguishable from "no release"."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(
        GovernedAbsence(
            reason="no MTBS fire in geo.features is dated to this release day",
            upstream_response="geo.features query returned 0 rows",
            recorded_at=datetime.now(UTC),
            run_id="run-1",
        ),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(
        store, requested_zoom=BASE_TIER_REQUEST, requested_day=FAR_FUTURE_DAY, base_uri=str(tmp_path)
    )

    assert answer.release_day == LATER_RELEASE
    assert answer.is_governed_absence is True
    assert answer.rows.height == 0


def test_as_of_never_blends_two_releases_into_one_answer(tmp_path: Path) -> None:
    """The as-of answer contains exactly one release's rows, never a union of the two nearest ones."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2022PNW00001", observed_day=LATER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(
        store, requested_zoom=BASE_TIER_REQUEST, requested_day=LATER_RELEASE, base_uri=str(tmp_path)
    )

    assert answer.release_day == LATER_RELEASE
    assert answer.rows.height == 1
    assert answer.rows["fire_id"].to_list() == ["2022PNW00001"]


# --- the zoom axis: one rung per read, and a blend that is not expressible ------------------------


def test_two_tiers_of_one_release_never_stack_into_one_as_of_answer(tmp_path: Path) -> None:
    """One MTBS cohort at two rungs is one release generalised twice, not two releases."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00009", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=DETAIL_TIER,
        day=EARLIER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    at_base = resolve_burn_severity_as_of(
        store, requested_zoom=BASE_TIER, requested_day=EARLIER_RELEASE, base_uri=str(tmp_path)
    )
    at_detail = resolve_burn_severity_as_of(
        store, requested_zoom=DETAIL_TIER, requested_day=EARLIER_RELEASE, base_uri=str(tmp_path)
    )

    assert at_base.rows["fire_id"].to_list() == ["2020PNW00001"]
    assert at_detail.rows["fire_id"].to_list() == ["2020PNW00009"]
    assert at_base.zoom == BASE_TIER
    assert at_detail.zoom == DETAIL_TIER


def test_a_governed_absence_at_one_rung_does_not_mark_the_release_absent_at_another(tmp_path: Path) -> None:
    """`_known_release_days` maps day -> "is absent"; unscoped, the verdict would depend on listing order."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(
        GovernedAbsence(
            reason="this cohort held no fire inside the deployment bounding box at this resolution",
            upstream_response="geo.features query returned 0 rows",
            recorded_at=datetime.now(UTC),
            run_id="run-1",
        ),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        zoom=DETAIL_TIER,
        day=EARLIER_RELEASE,
    )
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    at_base = resolve_burn_severity_as_of(
        store, requested_zoom=BASE_TIER, requested_day=EARLIER_RELEASE, base_uri=str(tmp_path)
    )
    at_detail = resolve_burn_severity_as_of(
        store, requested_zoom=DETAIL_TIER, requested_day=EARLIER_RELEASE, base_uri=str(tmp_path)
    )

    assert at_base.is_governed_absence is False
    assert at_base.rows.height == 1
    assert at_detail.is_governed_absence is True
    assert at_detail.rows.height == 0


def test_a_request_between_two_rungs_is_answered_by_the_rung_below_it(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00009", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=DETAIL_TIER,
        day=EARLIER_RELEASE,
    )
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=LATER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(
        store, requested_zoom=UNPUBLISHED_ZOOM, requested_day=FAR_FUTURE_DAY, base_uri=str(tmp_path)
    )

    assert answer.zoom == DETAIL_TIER
    assert answer.release_day == EARLIER_RELEASE, "z9's newest release, not z13's"
    assert answer.rows["fire_id"].to_list() == ["2020PNW00009"]


# --- incomplete releases: parts without a completion marker serve zero rows ----------------------


def test_a_day_with_parts_but_no_completion_marker_reads_as_zero_rows(tmp_path: Path) -> None:
    """An upload killed part-way through left parts behind, but they are a prefix, not the release."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    # Remove the completion marker: the parts survive, so the day is a killed upload.
    del backend.objects[completion_marker_path(BURN_SEVERITY_STREAM, "observed", BASE_TIER, EARLIER_RELEASE)]
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, day=EARLIER_RELEASE, base_uri=str(tmp_path)
    )

    assert frame.height == 0
    assert frame.columns == list(BURN_SEVERITY_SCHEMA.column_names)


def test_as_of_never_selects_an_incomplete_release_as_the_answer(tmp_path: Path) -> None:
    """An incomplete release is not counted as a published release for as-of resolution."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    # Write a complete earlier release
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=EARLIER_RELEASE,
    )
    # Write an incomplete later release
    _write_complete_partition(
        store,
        burn_severity_table([burn_severity_row(fire_id="2022PNW00099", observed_day=LATER_RELEASE)]),
        kind="observed",
        zoom=BASE_TIER,
        day=LATER_RELEASE,
    )
    # Remove the marker: the parts survive, so the later release is a killed upload.
    del backend.objects[completion_marker_path(BURN_SEVERITY_STREAM, "observed", BASE_TIER, LATER_RELEASE)]
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(
        store, requested_zoom=BASE_TIER_REQUEST, requested_day=FAR_FUTURE_DAY, base_uri=str(tmp_path)
    )

    assert answer.release_day == EARLIER_RELEASE, "incomplete later release is not selected"
    assert answer.rows["fire_id"].to_list() == ["2020PNW00001"]
