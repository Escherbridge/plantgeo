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
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.planes.burn_severity import (
    BURN_SEVERITY_EXPOSURE_CONTRADICTION,
    burn_severity_base_uri,
    read_burn_severity_release_day,
    resolve_burn_severity_as_of,
)
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA, BURN_SEVERITY_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from pathlib import Path

# The real governed release dates for fire years 2020 and 2022 (ingest/mtbs.py MTBS_ANNUAL_RELEASE_DATES) --
# two of this lane's whole-history five, used here to exercise "as of" resolution honestly rather than
# against invented dates.
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


def test_a_day_with_no_part_files_reads_as_an_honest_empty_typed_frame(tmp_path: Path) -> None:
    store = ObjectStore(RecordingBackend())

    frame = read_burn_severity_release_day(store, day=EARLIER_RELEASE, base_uri=str(tmp_path))

    assert frame.height == 0
    assert frame.columns == list(BURN_SEVERITY_SCHEMA.column_names)


def test_a_future_date_answers_empty_rather_than_falling_through_to_the_newest_observed_release(
    tmp_path: Path,
) -> None:
    """`kind` is a partition, not a column branch: a date nothing was written for gets its own empty answer."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=EARLIER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(store, day=FAR_FUTURE_DAY, base_uri=str(tmp_path))

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
        day=EARLIER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(store, day=EARLIER_RELEASE, base_uri=str(tmp_path))

    assert frame.height == 0
    assert frame.columns == list(BURN_SEVERITY_SCHEMA.column_names)


def test_a_release_split_across_several_part_files_reads_as_one_grain_sorted_table(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for part_index, fire_id in enumerate(("2022PNW00003", "2022PNW00001", "2022PNW00002")):
        store.write_partition(
            burn_severity_table([burn_severity_row(fire_id=fire_id, observed_day=LATER_RELEASE)]),
            layer=BURN_SEVERITY_STREAM,
            kind="observed",
            day=LATER_RELEASE,
            part_index=part_index,
        )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(store, day=LATER_RELEASE, base_uri=str(tmp_path))

    assert frame.height == EXPECTED_MULTI_PART_ROW_COUNT
    assert frame["fire_id"].to_list() == ["2022PNW00001", "2022PNW00002", "2022PNW00003"]


def test_geometry_survives_as_binary_wkb_with_no_srid_header(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    wkb = b"\x01\x03\x00\x00\x00"
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2022PNW00001", observed_day=LATER_RELEASE, geom=wkb)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(store, day=LATER_RELEASE, base_uri=str(tmp_path))

    assert frame["geom"].to_list() == [wkb]
    assert frame.schema["geom"] == pl.Binary


def test_a_different_release_never_leaks_into_an_exact_day_answer(tmp_path: Path) -> None:
    """A day only ever reads its own directory -- another release's rows never appear beside it."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=EARLIER_RELEASE,
    )
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2022PNW00099", observed_day=LATER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    frame = read_burn_severity_release_day(store, day=EARLIER_RELEASE, base_uri=str(tmp_path))

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
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=EARLIER_RELEASE,
    )
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2022PNW00001", observed_day=LATER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(store, requested_day=BETWEEN_RELEASES, base_uri=str(tmp_path))

    assert answer.release_day == EARLIER_RELEASE
    assert answer.is_governed_absence is False
    assert answer.rows["fire_id"].to_list() == ["2020PNW00001"]
    assert answer.exposure_contradiction == BURN_SEVERITY_EXPOSURE_CONTRADICTION


def test_as_of_past_the_newest_release_answers_the_newest_release_not_a_projection(tmp_path: Path) -> None:
    """This lane ships no `kind=forecast` stream -- a future date resolves to the newest past release."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=EARLIER_RELEASE,
    )
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2022PNW00001", observed_day=LATER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(store, requested_day=FAR_FUTURE_DAY, base_uri=str(tmp_path))

    assert answer.release_day == LATER_RELEASE
    assert answer.rows["fire_id"].to_list() == ["2022PNW00001"]


def test_as_of_before_the_earliest_release_is_an_honest_absence_not_a_zero_row_release(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=EARLIER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(store, requested_day=BEFORE_ANY_RELEASE, base_uri=str(tmp_path))

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
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(store, requested_day=FAR_FUTURE_DAY, base_uri=str(tmp_path))

    assert answer.release_day == LATER_RELEASE
    assert answer.is_governed_absence is True
    assert answer.rows.height == 0


def test_as_of_never_blends_two_releases_into_one_answer(tmp_path: Path) -> None:
    """The as-of answer contains exactly one release's rows, never a union of the two nearest ones."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2020PNW00001", observed_day=EARLIER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=EARLIER_RELEASE,
    )
    store.write_partition(
        burn_severity_table([burn_severity_row(fire_id="2022PNW00001", observed_day=LATER_RELEASE)]),
        layer=BURN_SEVERITY_STREAM,
        kind="observed",
        day=LATER_RELEASE,
    )
    materialize_backend(backend, tmp_path)

    answer = resolve_burn_severity_as_of(store, requested_day=LATER_RELEASE, base_uri=str(tmp_path))

    assert answer.release_day == LATER_RELEASE
    assert answer.rows.height == 1
    assert answer.rows["fire_id"].to_list() == ["2022PNW00001"]
