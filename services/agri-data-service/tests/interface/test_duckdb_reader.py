"""The serving DuckDB session: the memory guard is real, and the reads it runs are honest.

The session addresses a local directory here instead of the bucket -- `read_parquet` does not care
which -- so the projection, the viewport predicate, the clip and the truncation are all exercised
against real Parquet with no network and no credentials.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.interface.http.duckdb_session import (
    SERVING_MEMORY_LIMIT,
    SERVING_THREAD_COUNT,
    SPILLING_DISABLED,
    ServingSession,
    open_serving_session,
)
from agri_data_service.interface.http.faults import ServingRefusalError
from agri_data_service.interface.http.request_params import BoundingBox, ReadScope
from agri_data_service.interface.http.warehouse_reader import (
    DuckDbRowReader,
    GeometrySupport,
    NoSpatialSupport,
    PointSupport,
    RowRead,
    spatial_support,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CREDENTIALS = ObjectStoreCredentials(
    endpoint_url="https://example.invalid",
    region="auto",
    bucket="not-reached-by-a-local-read",
    access_key_id=SecretStr("key"),
    secret_access_key=SecretStr("secret"),
)

PNW = BoundingBox(west=-124.9, south=42.1, east=-111.1, north=48.9)

#: A budget small enough that a ten-row day provably overruns it.
TINY_ROW_BUDGET = 3


@pytest.fixture
def session() -> Iterator[ServingSession]:
    """A guarded session, closed on teardown; the caller re-points it at a local directory."""
    opened = open_serving_session(CREDENTIALS)
    try:
        yield opened
    finally:
        opened.close()


def local_session(session: ServingSession, root: Path) -> ServingSession:
    """The same guarded connection, addressing a local directory instead of the bucket."""
    return ServingSession(connection=session.connection, bucket_uri=root.as_posix())


def write_parquet(session: ServingSession, root: Path, key: str, select: str) -> str:
    """Materialise one part file at its layout key from a SELECT, and return the key."""
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    session.connection.execute(f"COPY ({select}) TO '{target.as_posix()}' (FORMAT PARQUET)")
    return key


def test_the_serving_session_caps_memory_threads_and_disables_spilling(session: ServingSession) -> None:
    """Not tuning: with spilling enabled, the 2026-08-24 read consumed the host instead of erroring."""
    held = dict(
        session.connection.execute(
            "SELECT name, value FROM duckdb_settings() "
            "WHERE name IN ('memory_limit', 'threads', 'max_temp_directory_size')"
        ).fetchall()
    )

    # DuckDB normalises the setting (`0GiB` reads back as `0 bytes`), so the assertion is on the
    # QUANTITY: zero temp space is spilling off, not spilling made small.
    assert _bytes_of(held["max_temp_directory_size"]) == 0, "spilling must be OFF, not merely small"
    assert _bytes_of(SPILLING_DISABLED) == 0, "the constant this session sets must itself mean zero"
    assert held["threads"] == str(SERVING_THREAD_COUNT)
    assert _bytes_of(held["memory_limit"]) <= _bytes_of(SERVING_MEMORY_LIMIT)


def test_the_serving_session_opens_no_local_database_file(session: ServingSession) -> None:
    """`:memory:` deliberately: a serving read must leave nothing on the host's disk."""
    attached = session.connection.execute("SELECT database_name, path FROM duckdb_databases()").fetchall()

    assert [path for _, path in attached if path] == []


def test_hive_columns_never_reach_a_served_row(session: ServingSession, tmp_path: Path) -> None:
    """DuckDB injects `layer`, `zoom`, `year`, `month` and `day` from the path unless told not to."""
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("signal", "observed", 13, date(2026, 8, 1)),
        "SELECT 'c1' AS cell_id, -116.2 AS cell_longitude, 43.6 AS cell_latitude, 0.4 AS normalized_value",
    )
    scope = ReadScope(layer="signal", kind="observed", tier=13, bbox=None)

    result = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=10))

    assert list(result.rows[0][1]) == ["cell_id", "cell_longitude", "cell_latitude", "normalized_value"]
    assert result.rows[0][0] == key, "a row must be attributed to the RELATIVE key its day is parsed from"


def test_a_viewport_narrows_a_point_lane_to_the_rows_inside_it(session: ServingSession, tmp_path: Path) -> None:
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("signal", "observed", 13, date(2026, 8, 1)),
        "SELECT * FROM (VALUES ('inside', -116.2, 43.6, 0.4), ('outside', -80.0, 25.0, 0.9)) "
        "AS t(cell_id, cell_longitude, cell_latitude, normalized_value)",
    )
    scope = ReadScope(layer="signal", kind="observed", tier=13, bbox=PNW)

    result = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=10))

    assert [row["cell_id"] for _, row in result.rows] == ["inside"]


def test_a_geometry_lane_is_served_as_geojson_clipped_to_the_viewport(
    session: ServingSession, tmp_path: Path
) -> None:
    """Clip before probing: the lever that took the largest USDM polygon from 124,676 to 6,151 vertices."""
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("drought", "observed", 13, date(2026, 8, 18)),
        "SELECT 3311 AS area_id, 'D2' AS dm_category, "
        "ST_AsWKB(ST_MakeEnvelope(-130.0, 30.0, -100.0, 50.0)) AS geom",
    )
    scope = ReadScope(layer="drought", kind="observed", tier=13, bbox=PNW)

    result = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=10))
    served = result.rows[0][1]["geom"]

    assert isinstance(served, str), "a served geometry is GeoJSON text, never raw WKB bytes"
    assert '"Polygon"' in served
    clipped = session.connection.execute(
        "SELECT ST_XMin(ST_GeomFromGeoJSON(?)), ST_XMax(ST_GeomFromGeoJSON(?))", [served, served]
    ).fetchone()
    assert clipped is not None
    assert clipped[0] == pytest.approx(PNW.west)
    assert clipped[1] == pytest.approx(PNW.east)


def test_a_read_over_its_budget_reports_itself_exhausted_rather_than_answering_short(
    session: ServingSession, tmp_path: Path
) -> None:
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("signal", "observed", 13, date(2026, 8, 1)),
        "SELECT range::VARCHAR AS cell_id, -116.2 AS cell_longitude, 43.6 AS cell_latitude, "
        "0.4 AS normalized_value FROM range(10)",
    )
    scope = ReadScope(layer="signal", kind="observed", tier=13, bbox=None)

    result = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=TINY_ROW_BUDGET))

    assert len(result.rows) == TINY_ROW_BUDGET
    assert result.budget_exhausted is True


def test_a_viewport_is_refused_when_the_written_objects_lack_the_position_columns(
    session: ServingSession, tmp_path: Path
) -> None:
    """The `signal` base rung was in exactly this state on 2026-08-25, mid re-export."""
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("signal", "observed", 13, date(2026, 8, 1)),
        "SELECT 'c1' AS cell_id, 0.4 AS normalized_value",
    )
    scope = ReadScope(layer="signal", kind="observed", tier=13, bbox=PNW)

    with pytest.raises(ServingRefusalError) as raised:
        reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=10))

    assert raised.value.code == "bbox_columns_absent"


def test_a_viewport_is_refused_on_a_lane_with_no_spatial_extent(session: ServingSession, tmp_path: Path) -> None:
    """`calendar` has no geometry at all; a bbox against it must not silently answer the whole world."""
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("calendar", "observed", 13, date(2026, 8, 1)),
        "SELECT DATE '2026-08-01' AS day_date",
    )
    scope = ReadScope(layer="calendar", kind="observed", tier=13, bbox=PNW)

    with pytest.raises(ServingRefusalError) as raised:
        reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=10))

    assert raised.value.code == "bbox_unsupported"


@pytest.mark.parametrize(
    ("layer", "expected"),
    [
        ("signal", PointSupport(longitude_column="cell_longitude", latitude_column="cell_latitude", nullable=False)),
        ("drought", GeometrySupport(geometry_column="geom")),
        ("soil-survey", GeometrySupport(geometry_column="geometry_wkb")),
        (
            "water-gauges",
            PointSupport(longitude_column="longitude", latitude_column="latitude", nullable=True),
        ),
    ],
)
def test_spatial_support_is_read_off_the_registered_schema_and_never_guessed(
    layer: str, expected: PointSupport | GeometrySupport
) -> None:
    assert spatial_support(layer, "observed") == expected


def test_a_lane_with_no_coordinates_and_no_geometry_says_so_rather_than_defaulting() -> None:
    support = spatial_support("calendar", "observed")

    assert isinstance(support, NoSpatialSupport)


def test_an_empty_key_list_never_reaches_duckdb(session: ServingSession) -> None:
    """A published day always has parts; a read with none is a caller bug, not an empty scan."""
    reader = DuckDbRowReader(session=session)
    scope = ReadScope(layer="signal", kind="observed", tier=13, bbox=None)

    result = reader.read_rows(RowRead(scope=scope, keys=(), row_budget=10))

    assert result == type(result)(rows=(), budget_exhausted=False, unpositioned_rows=0)


def _bytes_of(setting: str) -> float:
    """Parse a DuckDB byte setting (`1200MB`, `1.1 GiB`) into bytes so two spellings compare."""
    matched = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]?i?B|bytes)?\s*", setting, re.IGNORECASE)
    assert matched, setting
    factors = {"": 1, "bytes": 1, "b": 1, "kb": 10**3, "mb": 10**6, "gb": 10**9, "tb": 10**12}
    factors |= {"kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}
    return float(matched.group(1)) * factors[(matched.group(2) or "").lower()]
