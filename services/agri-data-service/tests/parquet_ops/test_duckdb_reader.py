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
from agri_data_service.parquet_ops.duckdb_session import (
    SERVING_MAX_CONCURRENT_READS,
    SERVING_MEMORY_LIMIT,
    SERVING_PROCESS_MEMORY_CEILING_BYTES,
    SERVING_THREAD_COUNT,
    SERVING_TIME_ZONE,
    SPILLING_DISABLED,
    ServingSession,
    open_guarded_connection,
)
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.request_params import BoundingBox, ReadScope
from agri_data_service.parquet_ops.serving import resolve_window
from agri_data_service.parquet_ops.warehouse_reader import (
    DuckDbRowReader,
    GeometrySupport,
    NoSpatialSupport,
    PointSupport,
    RowRead,
    spatial_support,
)
from tests.parquet_ops.fakes import FakeListing

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

#: One `signal` row, at the types the REGISTERED schema declares. The casts matter: a bare `-116.2`
#: is a DECIMAL to DuckDB and no registered schema carries one, so a fixture without them exercises
#: a shape the warehouse cannot produce and `wire.render_scalar` correctly refuses.
POSITIONED_SIGNAL_ROW = (
    "SELECT '{cell_id}' AS cell_id, CAST(-116.2 AS DOUBLE) AS cell_longitude, "
    "CAST(43.6 AS DOUBLE) AS cell_latitude, CAST(0.4 AS DOUBLE) AS normalized_value"
)


@pytest.fixture
def session() -> Iterator[ServingSession]:
    """A guarded session, closed on teardown; the caller re-points it at a local directory."""
    opened = ServingSession(connection=open_guarded_connection(), bucket_uri=f"s3://{CREDENTIALS.bucket}")
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


def test_the_serving_session_pins_utc_rather_than_inheriting_the_hosts_zone(session: ServingSession) -> None:
    """It read back `America/Denver` on the review machine. A session zone is one edit from a day shift."""
    held = session.connection.execute("SELECT current_setting('TimeZone')").fetchone()

    assert held is not None
    assert held[0] == SERVING_TIME_ZONE


def test_the_process_memory_ceiling_bounds_concurrency_times_the_per_read_limit() -> None:
    """`duckdb.connect()` makes a NEW instance, so the limit is per connection and the pool is the ceiling."""
    assert SERVING_MAX_CONCURRENT_READS >= 1
    assert SERVING_MAX_CONCURRENT_READS * _bytes_of(SERVING_MEMORY_LIMIT) <= SERVING_PROCESS_MEMORY_CEILING_BYTES, (
        "raising either the slot count or the per-read limit without the other overcommits the container"
    )


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


def test_a_geometry_lane_is_served_as_geojson_clipped_to_the_viewport(session: ServingSession, tmp_path: Path) -> None:
    """Clip before probing: the lever that took the largest USDM polygon from 124,676 to 6,151 vertices."""
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("drought", "observed", 13, date(2026, 8, 18)),
        "SELECT 3311 AS area_id, 'D2' AS dm_category, ST_AsWKB(ST_MakeEnvelope(-130.0, 30.0, -100.0, 50.0)) AS geom",
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


def test_a_mixed_key_set_is_refused_rather_than_dropping_the_days_that_lack_the_columns(
    session: ServingSession, tmp_path: Path
) -> None:
    """The re-export boundary: one day re-exported with positions, the day beside it not yet.

    `union_by_name=true` makes a probe's column set the UNION over the read, so a probe of the whole
    key set PASSES here -- asserted below -- and the bbox predicate then evaluates NULL for the older
    object and drops its rows. The window would answer `published, rows: [], truncated: false` for a
    day that holds rows, which is the false-content-claim the four states exist to prevent.
    """
    listing = FakeListing()
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    re_exported = listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    write_parquet(session, tmp_path, re_exported, POSITIONED_SIGNAL_ROW.format(cell_id="positioned"))
    not_yet = listing.write_day("signal", "observed", 13, date(2026, 8, 2))
    write_parquet(
        session,
        tmp_path,
        not_yet,
        "SELECT 'unpositioned' AS cell_id, CAST(0.9 AS DOUBLE) AS normalized_value",
    )
    scope = ReadScope(layer="signal", kind="observed", tier=13, bbox=PNW)

    union_probe = {
        description[0]
        for description in session.connection.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=true) LIMIT 0",
            [[(tmp_path / key).as_posix() for key in (re_exported, not_yet)]],
        ).description
        or ()
    }
    with pytest.raises(ServingRefusalError) as raised:
        resolve_window(listing, reader, scope=scope, first_day=date(2026, 8, 1), last_day=date(2026, 8, 2))

    assert "cell_longitude" in union_probe, (
        "the union probe this refusal replaced sees the column and passes; that is the whole defect"
    )
    assert raised.value.code == "bbox_columns_absent"
    assert not_yet in raised.value.message, "the refusal must name the RELATIVE key that cannot answer"
    assert re_exported not in raised.value.message


def test_a_window_over_objects_that_all_carry_the_columns_is_still_served(
    session: ServingSession, tmp_path: Path
) -> None:
    """The control for the refusal above: a uniform key set must not be refused."""
    listing = FakeListing()
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    for index, day in enumerate((date(2026, 8, 1), date(2026, 8, 2))):
        key = listing.write_day("signal", "observed", 13, day)
        write_parquet(session, tmp_path, key, POSITIONED_SIGNAL_ROW.format(cell_id=f"c{index}"))
    scope = ReadScope(layer="signal", kind="observed", tier=13, bbox=PNW)

    days = resolve_window(listing, reader, scope=scope, first_day=date(2026, 8, 1), last_day=date(2026, 8, 2))
    wire = [day.to_wire() for day in days]

    assert [day["state"] for day in wire] == ["published", "published"]
    assert [len(day["rows"]) for day in wire if isinstance(day["rows"], list)] == [1, 1]


@pytest.mark.parametrize(
    ("case", "geometry", "served"),
    [
        ("straddling", "ST_MakeEnvelope(-130.0, 30.0, -120.0, 50.0)", True),
        ("edge-touching", "ST_MakeEnvelope(-140.0, 42.1, -124.9, 48.9)", False),
        ("corner-touching", "ST_MakeEnvelope(-140.0, 30.0, -124.9, 42.1)", False),
    ],
)
def test_a_clip_that_collapses_to_a_lower_dimension_is_dropped_rather_than_served(
    session: ServingSession, tmp_path: Path, case: str, geometry: str, served: bool
) -> None:
    """`ST_Intersects` is true for boundary contact, so an edge-touching polygon clips to a LINESTRING.

    Serving that under a schema promising a Polygon hands a fill renderer a row it cannot draw.
    """
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("drought", "observed", 13, date(2026, 8, 18)),
        f"SELECT 3311 AS area_id, 'D2' AS dm_category, ST_AsWKB({geometry}) AS geom",
    )
    scope = ReadScope(layer="drought", kind="observed", tier=13, bbox=PNW)

    result = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=10))

    assert (len(result.rows) == 1) is served, case
    for _, row in result.rows:
        assert '"Polygon"' in str(row["geom"]), "a served clip keeps the source geometry's own dimension"


def test_a_point_lane_geometry_on_the_envelope_boundary_is_still_served(
    session: ServingSession, tmp_path: Path
) -> None:
    """Only a DIMENSION COLLAPSE is wrong: a point that clips to a point has lost nothing."""
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("drought", "observed", 13, date(2026, 8, 18)),
        f"SELECT 3311 AS area_id, 'D2' AS dm_category, ST_AsWKB(ST_Point({PNW.west}, 43.6)) AS geom",
    )
    scope = ReadScope(layer="drought", kind="observed", tier=13, bbox=PNW)

    result = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=10))

    assert '"Point"' in str(result.rows[0][1]["geom"])


def test_the_unpositioned_probe_is_skipped_once_truncation_is_already_forced(
    session: ServingSession, tmp_path: Path
) -> None:
    """A whole-read flag cannot change once the budget is exhausted, so a second scan is pure cost."""
    reader = DuckDbRowReader(session=local_session(session, tmp_path))
    key = write_parquet(
        session,
        tmp_path,
        partition_path("water-gauges", "observed", 13, date(2026, 8, 1)),
        "SELECT range::VARCHAR AS gauge_id, -116.2 AS longitude, 43.6 AS latitude FROM range(10) "
        "UNION ALL SELECT 'nowhere', NULL, NULL",
    )
    scope = ReadScope(layer="water-gauges", kind="observed", tier=13, bbox=PNW)

    exhausted = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=TINY_ROW_BUDGET))
    per_day = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=TINY_ROW_BUDGET, per_day_truncation=True))
    unexhausted = reader.read_rows(RowRead(scope=scope, keys=(key,), row_budget=100))

    assert exhausted.budget_exhausted is True
    assert exhausted.unpositioned_rows == 0, "not counted: `truncated` is already true for the whole read"
    assert per_day.unpositioned_rows > 0, "a window attributes truncation per day, so it still has to look"
    assert unexhausted.unpositioned_rows > 0


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
