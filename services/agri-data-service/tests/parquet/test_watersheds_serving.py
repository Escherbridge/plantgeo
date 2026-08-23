"""The watersheds serving read and its source-system reconciliation, proven without network or a bucket.

Every fixture here writes through the ALREADY-PROVEN `ObjectStore` writer (`RecordingBackend`,
byte-identical to a real bucket write) and, where content must actually be scanned back, persists
those exact bytes to a local temp directory so Polars reads them the same way it would read
`s3://...` in production. No test calls USGS or a real bucket (`httpx.MockTransport` stands in for
NHDPlus_HR, matching the convention `tests/test_ingest_watersheds.py` already established).
"""

from __future__ import annotations

import struct
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.watersheds import (
    WrittenWatershedRow,
    check_tohuc_integrity,
    compare_against_source,
    fetch_source_huc12_vintages,
    read_written_watersheds,
    validate_watersheds_release,
)
from agri_data_service.planes.watersheds import (
    WatershedGeometryError,
    decode_polygon_rings,
    find_containing_watersheds,
    list_observed_release_days,
    lookup_watershed_by_huc12,
    point_is_within_watershed_geometry,
    resolve_latest_observed_release_day,
    watersheds_object_store_root,
)
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA, WATERSHEDS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from pathlib import Path

RELEASE_DAY = date(2026, 8, 7)
# 2013-01-18, the same fixture value `tests/test_ingest_watersheds.py` uses to lock down
# `parse_load_date`'s millisecond parsing.
SANDY_RIVER_LOAD_DATE_MS = 1358492970000
SANDY_RIVER_LOAD_DATE = datetime(2013, 1, 18, 7, 9, 30, tzinfo=UTC)

SQUARE_A = [[(-122.0, 45.0), (-121.0, 45.0), (-121.0, 46.0), (-122.0, 46.0), (-122.0, 45.0)]]
SQUARE_B = [[(-120.0, 44.0), (-119.0, 44.0), (-119.0, 45.0), (-120.0, 45.0), (-120.0, 44.0)]]
# A donut: an exterior ring plus one hole, to prove holes are excluded from containment.
DONUT = [
    [(-118.0, 40.0), (-116.0, 40.0), (-116.0, 42.0), (-118.0, 42.0), (-118.0, 40.0)],
    [(-117.6, 40.4), (-116.4, 40.4), (-116.4, 41.6), (-117.6, 41.6), (-117.6, 40.4)],
]


def _polygon_wkb(rings: list[list[tuple[float, float]]]) -> bytes:
    """Encode standard OGC WKB (little-endian, no SRID header) -- the inverse of the module under test."""
    parts = [struct.pack("<BI", 1, 3), struct.pack("<I", len(rings))]
    for ring in rings:
        parts.append(struct.pack("<I", len(ring)))
        parts.extend(struct.pack("<dd", x, y) for x, y in ring)
    return b"".join(parts)


def _multipolygon_wkb(polygons: list[list[list[tuple[float, float]]]]) -> bytes:
    parts = [struct.pack("<BI", 1, 6), struct.pack("<I", len(polygons))]
    parts.extend(_polygon_wkb(rings) for rings in polygons)
    return b"".join(parts)


def _watershed_row(
    huc12: str,
    *,
    tohuc: str | None = None,
    geom: bytes = b"",
    observed_at: datetime | None = None,
    release_day: date = RELEASE_DAY,
) -> dict[str, object]:
    return {
        "huc12": huc12,
        "name": f"Basin {huc12}",
        "areasqkm": 12.5,
        "tohuc": tohuc,
        "states": "OR,WA",
        "hutype": "S",
        "source": "USGS NHDPlus HR WBDHU12",
        "observed_at": observed_at,
        "data_available_at": None,
        "release_day": release_day,
        "feature_id": f"feature-{huc12}",
        "geom": geom or _polygon_wkb(SQUARE_A),
    }


def _table(rows: list[dict[str, object]]) -> pa.Table:
    columns: dict[str, list[object]] = {name: [] for name in WATERSHEDS_SCHEMA.column_names}
    for row in rows:
        for name, values in columns.items():
            values.append(row[name])
    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(WATERSHEDS_SCHEMA.arrow_schema)


def _write_local_release(tmp_path: Path, parts: list[list[dict[str, object]]], *, day: date = RELEASE_DAY) -> str:
    """Write real Parquet bytes through the proven writer, then land them on local disk for Polars.

    `RecordingBackend` needs no network or credentials; persisting its bytes to `tmp_path` lets
    `scan_watersheds_release`/`read_written_watersheds` scan a local glob exactly as they would
    scan `s3://...` in production -- the code path under test never learns it is not a bucket.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for part_index, rows in enumerate(parts):
        receipt = store.write_partition(
            _table(rows), layer=WATERSHEDS_STREAM, kind="observed", day=day, part_index=part_index
        )
        target = tmp_path / receipt.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(backend.objects[receipt.key])
    return tmp_path.as_posix()


# --- planes.watersheds: day discovery ------------------------------------------------------------


def test_ten_parts_read_as_one_release_day_matching_partition_day_statuses() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for part_index in range(10):
        store.write_partition(
            _table([_watershed_row(f"17080001010{part_index}")]),
            layer=WATERSHEDS_STREAM,
            kind="observed",
            day=RELEASE_DAY,
            part_index=part_index,
        )

    assert list_observed_release_days(store) == (RELEASE_DAY,)
    keys = store.list_partition_keys(WATERSHEDS_STREAM, "observed")
    assert partition_day_statuses(
        layer=WATERSHEDS_STREAM, kind="observed", first_day=RELEASE_DAY, last_day=RELEASE_DAY, keys=keys
    ) == {RELEASE_DAY: "data"}


def test_resolve_latest_observed_release_day_never_borrows_from_the_future() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        _table([_watershed_row("170800010101")]), layer=WATERSHEDS_STREAM, kind="observed", day=RELEASE_DAY
    )

    before_first_release = date(2020, 1, 1)
    far_future = date(2030, 1, 1)

    assert resolve_latest_observed_release_day(store, as_of=before_first_release) is None
    assert resolve_latest_observed_release_day(store, as_of=RELEASE_DAY) == RELEASE_DAY
    # Nothing has been observed to have changed since the only release -- forward-filled, not empty.
    assert resolve_latest_observed_release_day(store, as_of=far_future) == RELEASE_DAY


def test_watersheds_object_store_root_matches_the_frozen_layout() -> None:
    credentials = ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="sjc",
        bucket="plantgeo-warehouse",
        access_key_id="access-key-value",
        secret_access_key="secret-key-value",
    )

    assert watersheds_object_store_root(credentials) == "s3://plantgeo-warehouse"
    assert watersheds_object_store_root(credentials, prefix="/sandbox/") == "s3://plantgeo-warehouse/sandbox"


# --- planes.watersheds: WKB decoding --------------------------------------------------------------


def test_point_in_polygon_and_hole_exclusion() -> None:
    payload = _polygon_wkb(DONUT)

    # Inside the donut's ring (between exterior and hole).
    assert point_is_within_watershed_geometry(payload, longitude=-117.9, latitude=41.9)
    # Inside the hole -- excluded even though it is inside the exterior ring.
    assert not point_is_within_watershed_geometry(payload, longitude=-117.0, latitude=41.0)
    # Entirely outside the exterior ring.
    assert not point_is_within_watershed_geometry(payload, longitude=0.0, latitude=0.0)


def test_multipolygon_decodes_to_one_entry_per_member() -> None:
    payload = _multipolygon_wkb([SQUARE_A, SQUARE_B])

    polygons = decode_polygon_rings(payload)

    expected_member_count = 2
    assert len(polygons) == expected_member_count
    assert point_is_within_watershed_geometry(payload, longitude=-121.5, latitude=45.5)  # inside square A
    assert point_is_within_watershed_geometry(payload, longitude=-119.5, latitude=44.5)  # inside square B
    assert not point_is_within_watershed_geometry(payload, longitude=0.0, latitude=0.0)


def test_a_truncated_or_unsupported_geometry_is_refused_rather_than_silently_misread() -> None:
    with pytest.raises(WatershedGeometryError, match="truncated or malformed"):
        decode_polygon_rings(b"\x01\x03\x00\x00\x00")  # header only, no ring data

    point_type = struct.pack("<BI", 1, 1)  # WKB Point -- never valid for a HUC12 boundary
    with pytest.raises(WatershedGeometryError, match="unsupported watersheds geometry type"):
        decode_polygon_rings(point_type)


# --- planes.watersheds: huc12 lookup and containment, against a real local release -----------------


def _two_part_release(tmp_path: Path) -> str:
    part_zero = [
        _watershed_row("170800010100", tohuc="170800010200", geom=_polygon_wkb(SQUARE_A)),
        _watershed_row("170800010101", tohuc="170800010200", geom=_polygon_wkb(SQUARE_A)),
    ]
    part_one = [
        _watershed_row("170800010200", tohuc=None, geom=_polygon_wkb(SQUARE_B)),
        _watershed_row("170800010201", tohuc="170800010200", geom=_polygon_wkb(SQUARE_B)),
    ]
    return _write_local_release(tmp_path, [part_zero, part_one])


def test_lookup_by_huc12_prunes_by_file_range_and_returns_the_right_basin(tmp_path: Path) -> None:
    root = _two_part_release(tmp_path)

    found = lookup_watershed_by_huc12(root, RELEASE_DAY, "170800010201")

    assert found is not None
    assert found.huc12 == "170800010201"
    assert found.tohuc == "170800010200"
    assert found.geom == _polygon_wkb(SQUARE_B)
    assert found.release_day == RELEASE_DAY


def test_lookup_by_huc12_is_an_honest_empty_for_an_unknown_code(tmp_path: Path) -> None:
    root = _two_part_release(tmp_path)

    assert lookup_watershed_by_huc12(root, RELEASE_DAY, "999999999999") is None


def test_lookup_by_huc12_refuses_a_non_unique_match(tmp_path: Path) -> None:
    duplicated = [
        _watershed_row("170800010100"),
        _watershed_row("170800010100"),
    ]
    root = _write_local_release(tmp_path, [duplicated])

    with pytest.raises(ValueError, match="not unique"):
        lookup_watershed_by_huc12(root, RELEASE_DAY, "170800010100")


def test_find_containing_watersheds_returns_only_the_basin_that_contains_the_point(tmp_path: Path) -> None:
    root = _two_part_release(tmp_path)

    inside_a = find_containing_watersheds(root, RELEASE_DAY, longitude=-121.5, latitude=45.5)
    inside_neither = find_containing_watersheds(root, RELEASE_DAY, longitude=0.0, latitude=0.0)

    assert {boundary.huc12 for boundary in inside_a} == {"170800010100", "170800010101"}
    assert inside_neither == ()


# --- pipeline.validation.watersheds: within-release integrity --------------------------------------


def test_tohuc_integrity_flags_only_a_reference_outside_the_release() -> None:
    written = (
        WrittenWatershedRow(huc12="170800010100", tohuc="170800010200", observed_at=None),
        WrittenWatershedRow(huc12="170800010200", tohuc=None, observed_at=None),  # terminal basin, null tohuc
        WrittenWatershedRow(huc12="170800010300", tohuc="999999999999", observed_at=None),  # broken
    )

    failures = check_tohuc_integrity(written)

    assert len(failures) == 1
    assert failures[0].huc12 == "170800010300"
    assert failures[0].lane == WATERSHEDS_STREAM
    assert "999999999999" in failures[0].reason


def test_read_written_watersheds_projects_to_the_reconciliation_columns_only(tmp_path: Path) -> None:
    root = _two_part_release(tmp_path)

    written = read_written_watersheds(root, RELEASE_DAY)

    assert {row.huc12 for row in written} == {
        "170800010100",
        "170800010101",
        "170800010200",
        "170800010201",
    }
    by_huc12 = {row.huc12: row for row in written}
    assert by_huc12["170800010201"].tohuc == "170800010200"
    assert by_huc12["170800010200"].tohuc is None


# --- pipeline.validation.watersheds: source reconciliation, via httpx.MockTransport -----------------

SOURCE_OBJECT_IDS = [1, 2, 3]
SOURCE_ID_HUC12 = {1: "170800010100", 2: "170800010200", 3: "170800010300"}


def _source_transport(loaddates: dict[str, object]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "returnIdsOnly=true" in url:
            return httpx.Response(200, json={"objectIds": SOURCE_OBJECT_IDS})
        assert "returnGeometry=false" in url
        assert "outFields=huc12" in url
        features = [
            {"attributes": {"huc12": SOURCE_ID_HUC12[object_id], "loaddate": loaddates.get(SOURCE_ID_HUC12[object_id])}}
            for object_id in SOURCE_OBJECT_IDS
        ]
        return httpx.Response(200, json={"features": features})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_source_huc12_vintages_is_geometry_free_and_parses_the_load_date() -> None:
    transport = _source_transport(
        {
            "170800010100": SANDY_RIVER_LOAD_DATE_MS,
            "170800010200": None,
            "170800010300": "not-a-number",
        }
    )

    async with httpx.AsyncClient(transport=transport) as client:
        vintages = await fetch_source_huc12_vintages(client, "-125,42,-111,49")

    assert vintages["170800010100"] == SANDY_RIVER_LOAD_DATE
    assert vintages["170800010200"] is None
    # A non-numeric loaddate parses to an honest None, never a fabricated instant.
    assert vintages["170800010300"] is None


@pytest.mark.asyncio
async def test_fetch_source_huc12_vintages_refuses_the_arcgis_fault_behind_http_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "returnIdsOnly=true" in str(request.url):
            return httpx.Response(200, json={"objectIds": [1]})
        return httpx.Response(200, json={"error": {"code": 500, "message": "Unable to complete operation"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamPayloadError, match="error object"):
            await fetch_source_huc12_vintages(client, "-125,42,-111,49")


def test_compare_against_source_detects_republish_retirement_and_addition() -> None:
    written = (
        WrittenWatershedRow(huc12="republished", tohuc=None, observed_at=SANDY_RIVER_LOAD_DATE),
        WrittenWatershedRow(huc12="retired", tohuc=None, observed_at=SANDY_RIVER_LOAD_DATE),
        WrittenWatershedRow(huc12="unchanged", tohuc=None, observed_at=SANDY_RIVER_LOAD_DATE),
    )
    newer = datetime(2026, 8, 7, tzinfo=UTC)
    source_vintages = {
        "republished": newer,
        "unchanged": SANDY_RIVER_LOAD_DATE,
        "added": SANDY_RIVER_LOAD_DATE,
        # "retired" is absent -- WBD no longer returns it.
    }

    failures = compare_against_source(written, source_vintages)

    by_huc12 = {failure.huc12: failure for failure in failures}
    expected_failure_count = 3
    assert len(failures) == expected_failure_count
    assert "republished upstream" in by_huc12["republished"].reason
    assert "retired upstream" in by_huc12["retired"].reason
    assert "added upstream" in by_huc12["added"].reason
    assert "unchanged" not in by_huc12


def test_compare_against_source_never_flags_an_unparseable_source_date() -> None:
    written = (WrittenWatershedRow(huc12="170800010100", tohuc=None, observed_at=SANDY_RIVER_LOAD_DATE),)

    failures = compare_against_source(written, {"170800010100": None})

    assert failures == ()


@pytest.mark.asyncio
async def test_validate_watersheds_release_composes_both_checks(tmp_path: Path) -> None:
    parts = [
        [
            # Undated in the warehouse; the source below now carries a real loaddate for it.
            _watershed_row("170800010100", tohuc="999999999999"),  # also a broken tohuc
            _watershed_row("170800010200", tohuc=None, observed_at=SANDY_RIVER_LOAD_DATE),
        ]
    ]
    root = _write_local_release(tmp_path, parts)
    transport = _source_transport(
        {
            "170800010100": SANDY_RIVER_LOAD_DATE_MS,  # republished: warehouse holds this basin undated
            "170800010200": SANDY_RIVER_LOAD_DATE_MS,  # unchanged
            "170800010300": SANDY_RIVER_LOAD_DATE_MS,  # added upstream, not yet exported
        }
    )

    async with httpx.AsyncClient(transport=transport) as client:
        report = await validate_watersheds_release(client, root=root, day=RELEASE_DAY, bbox="-125,42,-111,49")

    assert report.release_day == RELEASE_DAY
    assert not report.is_clean
    assert len(report.tohuc_failures) == 1
    assert report.tohuc_failures[0].huc12 == "170800010100"
    by_huc12 = {failure.huc12: failure for failure in report.source_failures}
    expected_source_failure_count = 2
    assert len(report.source_failures) == expected_source_failure_count
    assert "republished upstream" in by_huc12["170800010100"].reason
    assert "added upstream" in by_huc12["170800010300"].reason
