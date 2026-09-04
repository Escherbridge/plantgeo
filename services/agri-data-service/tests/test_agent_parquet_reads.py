"""Parity evidence for the agent's move off PostgreSQL: real DuckDB, real Parquet, real statements.

Every statement in `agent/parquet_reads.py` is executed here against Parquet files written to a
temporary directory through each lane's REGISTERED Arrow schema, and its answer is compared with a
Python reference that re-expresses the PostgreSQL statement it replaced. The references are written
out clause by clause in their docstrings and were derived from the `.sql` files deleted in the same
change (`sql/agent/signals_near_point.sql`, `signal_value_on_day.sql`, `signal_neighbors_in_time.sql`,
`nearest_signal_cells.sql`, `drought_history_at_point.sql`), so the comparison is against what the
warehouse used to answer and not against what this module happens to compute.

Nothing here touches an object store, a bucket or the network. `open_guarded_connection()` is the
real serving connection -- the same memory cap, the same two extensions, the same UTC session zone --
so a statement that only works because a test relaxed a guard cannot pass.
"""

# ruff: noqa: PLR2004 - the literals here are fixture cell counts and offsets the assertion checks directly.

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agri_data_service.agent import parquet_reads
from agri_data_service.agent.tools import MAX_CELL_FANOUT, _bbox_bounds
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.parquet_ops.duckdb_session import open_guarded_connection
from agri_data_service.parquet_ops.warehouse_reader import spatial_support
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import duckdb

DAY = date(2026, 3, 14)
BOISE_LONGITUDE = -116.2
BOISE_LATITUDE = 43.6
RADIUS_METERS = 50_000.0

# Two cells inside the radius and one 300 km away, so every statement has something to exclude.
NEAR_CELL = "aaaaaaaa-0000-0000-0000-000000000001"
SECOND_CELL = "bbbbbbbb-0000-0000-0000-000000000002"
FAR_CELL = "cccccccc-0000-0000-0000-000000000003"
CELL_POSITIONS: dict[str, tuple[float, float]] = {
    NEAR_CELL: (-116.25, 43.62),
    SECOND_CELL: (-116.05, 43.55),
    FAR_CELL: (-119.90, 45.90),
}

# PostGIS `::geography` distance and DuckDB `ST_Distance_Spheroid` are both WGS84 ELLIPSOIDAL and
# agree to well under a metre. The reference below is the cheaper SPHERICAL haversine, which sits
# about 0.2% away from both at this latitude -- so distances are compared at this tolerance and the
# tolerance is about the reference's own formula, never about disagreement between the two engines.
SPHERICAL_TO_SPHEROIDAL_TOLERANCE = 2.5e-3


@dataclass
class LocalSession:
    """A `ServingSession` whose `object_uri` resolves to a local file instead of an `s3://` key."""

    connection: duckdb.DuckDBPyConnection
    files: dict[str, str] = field(default_factory=dict)

    def object_uri(self, relative_key: str) -> str:
        """Resolve one relative partition key to the temporary file standing in for it."""
        return self.files[relative_key]


@pytest.fixture(name="connection")
def _connection() -> duckdb.DuckDBPyConnection:
    """The real guarded serving connection: capped memory, no spilling, httpfs and spatial loaded."""
    return open_guarded_connection()


def signal_rows(day: date) -> list[dict[str, Any]]:
    """One day of the signal plane: three cells, two signals each, values keyed to the day."""
    rows: list[dict[str, Any]] = []
    for index, (cell_id, (longitude, latitude)) in enumerate(CELL_POSITIONS.items()):
        for signal, unit, base in (("air_temperature", "degC", 4.0), ("precipitation", "mm", 1.0)):
            rows.append(
                {
                    "support_key": "surface",
                    "signal_name": signal,
                    "normalized_unit": unit,
                    "cell_id": cell_id,
                    "observed_day": day,
                    "normalized_value": base + index + day.day / 100.0,
                    "observation_count": 2 + index,
                    "newest_observed_at": datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
                    "coverage_fraction": 0.9,
                    "allowed_client_exposure": True,
                    "cell_longitude": longitude,
                    "cell_latitude": latitude,
                }
            )
    return rows


def write_lane_day(root: Path, layer: str, day: date, rows: Sequence[dict[str, Any]]) -> tuple[str, str]:
    """Write one day's part file through the lane's REGISTERED schema, and return its key and path."""
    schema = get_stream_schema(layer, "observed").arrow_schema
    key = partition_path(layer, "observed", 13, day)
    path = root / f"{layer}-{day.isoformat()}.parquet"
    pq.write_table(pa.Table.from_pylist(list(rows), schema=schema), path)
    return key, str(path)


def scope_parameters(radius_meters: float = RADIUS_METERS) -> list[Any]:
    """The eight shared signal-scope parameters, built exactly as `agent/tools.py` builds them."""
    west, south, east, north = _bbox_bounds(BOISE_LONGITUDE, BOISE_LATITUDE, radius_meters)
    return [west, east, south, north, BOISE_LATITUDE, BOISE_LONGITUDE, radius_meters, MAX_CELL_FANOUT]


def run_statement(
    session: LocalSession,
    statement: str,
    keys: Sequence[str],
    parameters: Sequence[Any],
) -> list[dict[str, Any]]:
    """Execute one agent statement the way `warehouse.scan` does: part list first, then the rest."""
    uris = [session.object_uri(key) for key in keys]
    cursor = session.connection.execute(statement, [uris, *parameters])
    columns = [description[0] for description in cursor.description or ()]
    return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]


def spherical_distance_meters(longitude: float, latitude: float) -> float:
    """Haversine metres from the probe, on the mean earth radius. The reference formula, spherical."""
    earth_radius = 6_371_008.7714
    phi_probe, phi_cell = math.radians(BOISE_LATITUDE), math.radians(latitude)
    delta_phi = phi_cell - phi_probe
    delta_lambda = math.radians(longitude - BOISE_LONGITUDE)
    half_chord = (
        math.sin(delta_phi / 2) ** 2 + math.cos(phi_probe) * math.cos(phi_cell) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * earth_radius * math.asin(math.sqrt(half_chord))


def postgresql_window_summary(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The deleted `sql/agent/signals_near_point.sql`, re-expressed over the same rows.

    That statement joined `geo.mv_signal_cell_daily` to the cells `agri.spatial_cell` reported
    within the radius, grouped by `(signal_name, support_key, normalized_unit)`, and projected
    `min(min_value)`, `max(max_value)` and `sum(avg_value * observation_count) / sum(...)`. The
    three value columns are taken from `normalized_value` here for the reason RUNBOOK section 0.22.4
    records: they equalled it on 100% of 701,257 measured rows, which is why the Parquet schema does
    not carry them. If that ever stops being true this reference stops being the right one.
    """
    scoped = [
        row for row in rows if spherical_distance_meters(row["cell_longitude"], row["cell_latitude"]) <= RADIUS_METERS
    ]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in scoped:
        grouped.setdefault((row["signal_name"], row["support_key"], row["normalized_unit"]), []).append(row)
    summaries = [
        {
            "signal_name": signal,
            "support_key": support,
            "normalized_unit": unit,
            "observation_count": sum(row["observation_count"] for row in members),
            "cell_count": len({row["cell_id"] for row in members}),
            "day_count": len({row["observed_day"] for row in members}),
            "first_observed_day": min(row["observed_day"] for row in members),
            "last_observed_day": max(row["observed_day"] for row in members),
            "last_observed_at": max(row["newest_observed_at"] for row in members),
            "minimum_value": min(row["normalized_value"] for row in members),
            "maximum_value": max(row["normalized_value"] for row in members),
            "mean_value": (
                sum(row["normalized_value"] * row["observation_count"] for row in members)
                / sum(row["observation_count"] for row in members)
            ),
            "nearest_cell_distance_m": min(
                spherical_distance_meters(row["cell_longitude"], row["cell_latitude"]) for row in members
            ),
        }
        for (signal, support, unit), members in grouped.items()
    ]
    summaries.sort(key=lambda entry: (-entry["observation_count"], entry["signal_name"]))
    return summaries


def assert_row_matches(measured: dict[str, Any], expected: dict[str, Any]) -> None:
    """Compare one answered row against the reference, allowing only the stated distance tolerance."""
    for column, want in expected.items():
        got = measured[column]
        if column.endswith(("distance_m", "distance_meters")):
            assert math.isclose(got, want, rel_tol=SPHERICAL_TO_SPHEROIDAL_TOLERANCE), (
                f"{column}: DuckDB answered {got} and the spherical reference {want}; the two engines "
                "are ellipsoidal and spherical respectively and may differ only by the stated tolerance"
            )
            continue
        assert got == want, f"{column}: DuckDB answered {got!r} and the PostgreSQL reference {want!r}"


# --- The signal plane --------------------------------------------------------------


def test_the_window_summary_answers_exactly_what_the_dropped_matview_answered(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """Parity for `signals_near_point` over five days: every column, against the PostgreSQL reference."""
    files: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for offset in range(5):
        day = DAY - timedelta(days=offset)
        day_rows = signal_rows(day)
        rows.extend(day_rows)
        key, path = write_lane_day(tmp_path, "signal", day, day_rows)
        files[key] = path
    session = LocalSession(connection=connection, files=files)

    measured = run_statement(session, parquet_reads.SIGNAL_WINDOW_SUMMARY, sorted(files), scope_parameters())
    expected = postgresql_window_summary(rows)

    assert [row["signal_name"] for row in measured] == [row["signal_name"] for row in expected]
    for got, want in zip(measured, expected, strict=True):
        assert_row_matches(got, want)
    # The far cell contributed to neither engine's answer, which is what makes the radius load-bearing.
    assert all(row["cell_count"] == 2 for row in measured)


def test_the_day_statement_answers_the_partition_day_and_never_a_neighbouring_one(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The named-day rule, structurally: the day is the part file, so no other day can be reached."""
    files: dict[str, str] = {}
    for offset in range(3):
        day = DAY - timedelta(days=offset)
        key, path = write_lane_day(tmp_path, "signal", day, signal_rows(day))
        files[key] = path
    session = LocalSession(connection=connection, files=files)
    day_key = partition_path("signal", "observed", 13, DAY)

    measured = run_statement(session, parquet_reads.SIGNAL_DAY_VALUES, [day_key], scope_parameters())

    assert {row["observed_day"] for row in measured} == {DAY}
    nearest = next(row for row in measured if row["signal_name"] == "air_temperature")
    # DISTINCT ON (signal, support, unit) ORDER BY distance -- the nearest cell wins, and its own
    # value travels beside the spread over every admitted cell.
    assert nearest["nearest_cell_id"] == NEAR_CELL
    assert nearest["cell_count"] == 2
    assert nearest["nearest_cell_value"] == pytest.approx(4.14)
    assert nearest["minimum_value"] == pytest.approx(4.14)
    assert nearest["maximum_value"] == pytest.approx(5.14)


def test_the_neighbour_statement_returns_one_row_per_side_with_its_real_gap(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """A neighbour handed back without its gap is indistinguishable from an exact answer."""
    files: dict[str, str] = {}
    for day in (DAY - timedelta(days=3), DAY - timedelta(days=1), DAY + timedelta(days=2)):
        key, path = write_lane_day(tmp_path, "signal", day, signal_rows(day))
        files[key] = path
    session = LocalSession(connection=connection, files=files)

    measured = run_statement(
        session,
        parquet_reads.SIGNAL_TIME_NEIGHBORS,
        sorted(files),
        [*scope_parameters(), DAY, DAY, DAY, DAY],
    )

    by_side = {(row["signal_name"], row["side"]): row for row in measured}
    before = by_side[("air_temperature", "before")]
    after = by_side[("air_temperature", "after")]
    assert before["observed_day"] == DAY - timedelta(days=1)
    assert before["day_offset"] == -1
    assert before["distance_days"] == 1
    assert after["observed_day"] == DAY + timedelta(days=2)
    assert after["day_offset"] == 2
    assert after["distance_days"] == 2
    # Exactly one row per side per signal group; the closer day on each side wins the tie-break.
    assert len(measured) == 4


def test_the_cell_statement_lists_a_cell_that_holds_nothing_on_the_requested_day(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The LEFT JOIN is the point: "the nearest cells" must never quietly mean "the ones with data"."""
    silent_day = DAY - timedelta(days=4)
    files: dict[str, str] = {}
    key, path = write_lane_day(tmp_path, "signal", silent_day, signal_rows(silent_day))
    files[key] = path
    # The requested day carries the near cell only, so the second cell is known but silent that day.
    day_rows = [row for row in signal_rows(DAY) if row["cell_id"] == NEAR_CELL]
    key, path = write_lane_day(tmp_path, "signal", DAY, day_rows)
    files[key] = path
    session = LocalSession(connection=connection, files=files)

    measured = run_statement(
        session,
        parquet_reads.SIGNAL_CELL_DAY_COUNTS,
        sorted(files),
        [*scope_parameters(), DAY, 8],
    )

    by_cell = {row["cell_id"]: row for row in measured}
    assert by_cell[NEAR_CELL]["observation_count_on_day"] == 4
    assert by_cell[SECOND_CELL]["observation_count_on_day"] == 0
    assert by_cell[SECOND_CELL]["signal_count_on_day"] == 0
    assert by_cell[SECOND_CELL]["last_observed_at"] is None
    assert FAR_CELL not in by_cell
    assert [row["cell_id"] for row in measured] == [NEAR_CELL, SECOND_CELL]


def test_the_admitted_cells_are_the_scope_the_postgresql_audit_is_read_over(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """`signal_coverage_on_day.sql` now takes these two arrays; a wider set would explain the wrong point."""
    key, path = write_lane_day(tmp_path, "signal", DAY, signal_rows(DAY))
    session = LocalSession(connection=connection, files={key: path})

    measured = run_statement(session, parquet_reads.SIGNAL_ADMITTED_CELLS, [key], scope_parameters())

    assert [row["cell_id"] for row in measured] == [NEAR_CELL, SECOND_CELL]
    assert measured[0]["distance_meters"] < measured[1]["distance_meters"]
    assert measured[0]["distance_meters"] == pytest.approx(
        spherical_distance_meters(*CELL_POSITIONS[NEAR_CELL]),
        rel=SPHERICAL_TO_SPHEROIDAL_TOLERANCE,
    )


def test_a_radius_smaller_than_every_cell_answers_nothing_rather_than_widening(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The exact test, not the box: a 100 m radius near Boise admits no cell of either grid."""
    key, path = write_lane_day(tmp_path, "signal", DAY, signal_rows(DAY))
    session = LocalSession(connection=connection, files={key: path})

    measured = run_statement(session, parquet_reads.SIGNAL_DAY_VALUES, [key], scope_parameters(100.0))

    assert measured == []


# --- The drought release set -------------------------------------------------------


def wkb_envelope(
    connection: duckdb.DuckDBPyConnection,
    west: float,
    south: float,
    east: float,
    north: float,
) -> bytes:
    """One rectangular polygon as WKB, written the way an exporter would."""
    return connection.execute("SELECT ST_AsWKB(ST_MakeEnvelope(?, ?, ?, ?))", [west, south, east, north]).fetchone()[0]


def test_a_release_that_published_no_class_over_the_point_is_a_row_and_not_an_absence(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The deleted `drought_history_at_point.sql` LEFT JOIN LATERAL, reproduced by FILTER aggregates.

    `published_class_count` counts every polygon the release published anywhere; the three filtered
    aggregates describe only the polygons over the point. A release whose polygons all fall elsewhere
    keeps its row with `severity_class` null and `covering_class_count` 0 -- a measured "this release
    existed and found no drought here", which an empty result would collapse into "nothing is known".
    """
    release_day = date(2026, 3, 10)
    rows = [
        {
            "area_id": "covering",
            "valid_date": release_day,
            "dm_category": 2,
            "source_url": "https://droughtmonitor.unl.edu/",
            "ingested_at": datetime(2026, 3, 12, 6, tzinfo=UTC),
            "geom": wkb_envelope(connection, -117.0, 43.0, -115.0, 44.0),
        },
        {
            "area_id": "elsewhere",
            "valid_date": release_day,
            "dm_category": 4,
            "source_url": "https://droughtmonitor.unl.edu/",
            "ingested_at": datetime(2026, 3, 12, 6, tzinfo=UTC),
            "geom": wkb_envelope(connection, -100.0, 30.0, -99.0, 31.0),
        },
    ]
    key, path = write_lane_day(tmp_path, "drought", release_day, rows)
    session = LocalSession(connection=connection, files={key: path})

    measured = run_statement(
        session,
        parquet_reads.DROUGHT_RELEASE_SEVERITY,
        [key],
        [BOISE_LONGITUDE, BOISE_LATITUDE],
    )

    assert len(measured) == 1
    only = measured[0]
    assert only["valid_date"] == release_day
    assert only["published_class_count"] == 2, "the release published two polygons, wherever they fell"
    assert only["severity_class"] == 2, "only the covering polygon may set the severity"
    assert only["covering_class_count"] == 1
    assert only["published_at"] == datetime(2026, 3, 12, 6, tzinfo=UTC)


def test_a_release_whose_polygons_all_fall_elsewhere_still_reports_itself(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """severity null and covering 0 is a FACT; an empty list would be a different claim entirely."""
    release_day = date(2026, 3, 3)
    rows = [
        {
            "area_id": "elsewhere",
            "valid_date": release_day,
            "dm_category": 3,
            "source_url": "https://droughtmonitor.unl.edu/",
            "ingested_at": datetime(2026, 3, 5, 6, tzinfo=UTC),
            "geom": wkb_envelope(connection, -100.0, 30.0, -99.0, 31.0),
        }
    ]
    key, path = write_lane_day(tmp_path, "drought", release_day, rows)
    session = LocalSession(connection=connection, files={key: path})

    measured = run_statement(
        session,
        parquet_reads.DROUGHT_RELEASE_SEVERITY,
        [key],
        [BOISE_LONGITUDE, BOISE_LATITUDE],
    )

    assert len(measured) == 1
    assert measured[0]["severity_class"] is None
    assert measured[0]["covering_class_count"] == 0
    assert measured[0]["published_class_count"] == 1


# --- Generic lanes -----------------------------------------------------------------


def watershed_row(connection: duckdb.DuckDBPyConnection, huc12: str, envelope: tuple[float, ...]) -> dict[str, Any]:
    """One watershed polygon, written through the lane's own registered schema."""
    return {
        "huc12": huc12,
        "name": f"basin {huc12}",
        "areasqkm": 120.5,
        "tohuc": "170501120102",
        "states": "ID",
        "hutype": "S",
        "source": "WBD",
        "observed_at": datetime(2026, 3, 10, tzinfo=UTC),
        "data_available_at": datetime(2026, 3, 10, tzinfo=UTC),
        "release_day": date(2026, 3, 10),
        "feature_id": f"wbd-{huc12}",
        "geom": wkb_envelope(connection, *envelope),
    }


def test_a_geometry_lane_reports_containment_exactly_and_distance_to_the_centroid(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """DuckDB has no geodesic distance to a polygon EDGE, so the two facts are reported separately."""
    release_day = date(2026, 3, 10)
    rows = [
        watershed_row(connection, "170501120101", (-116.30, 43.55, -116.10, 43.65)),
        watershed_row(connection, "170501120999", (-121.0, 45.0, -120.8, 45.2)),
    ]
    key, path = write_lane_day(tmp_path, "watersheds", release_day, rows)
    session = LocalSession(connection=connection, files={key: path})
    support = spatial_support("watersheds", "observed")
    west, south, east, north = _bbox_bounds(BOISE_LONGITUDE, BOISE_LATITUDE, RADIUS_METERS)

    measured = run_statement(
        session,
        parquet_reads.geometry_lane_rows(support),
        [key],
        [west, south, east, north, BOISE_LATITUDE, BOISE_LONGITUDE, BOISE_LONGITUDE, BOISE_LATITUDE, 10],
    )

    assert [row["huc12"] for row in measured] == ["170501120101"], "the 400 km basin is outside the box"
    only = measured[0]
    assert only["covers_probe_point"] is True
    assert only["centroid_longitude"] == pytest.approx(BOISE_LONGITUDE)
    assert only["centroid_latitude"] == pytest.approx(BOISE_LATITUDE)
    assert only["centroid_distance_meters"] == pytest.approx(0.0, abs=1.0)
    # The WKB column never rides to the answer under its own name; only the derived facts do.
    assert support.geometry_column not in only


def test_a_point_lane_measures_to_the_rows_own_coordinate(
    tmp_path: Path,
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """A lane declaring a coordinate pair gets the exact geodesic distance, and the far row is dropped."""
    key, path = write_lane_day(tmp_path, "signal", DAY, signal_rows(DAY))
    session = LocalSession(connection=connection, files={key: path})
    support = spatial_support("signal", "observed")
    west, south, east, north = _bbox_bounds(BOISE_LONGITUDE, BOISE_LATITUDE, RADIUS_METERS)

    measured = run_statement(
        session,
        parquet_reads.point_lane_rows(support),
        [key],
        [west, east, south, north, BOISE_LATITUDE, BOISE_LONGITUDE, RADIUS_METERS, 10],
    )

    assert {row["cell_id"] for row in measured} == {NEAR_CELL, SECOND_CELL}
    assert measured[0]["distance_meters"] == pytest.approx(
        spherical_distance_meters(*CELL_POSITIONS[NEAR_CELL]),
        rel=SPHERICAL_TO_SPHEROIDAL_TOLERANCE,
    )
    assert measured == sorted(measured, key=lambda row: row["distance_meters"])


# --- The ordinate trap -------------------------------------------------------------


def test_the_probe_point_is_bound_latitude_first(connection: duckdb.DuckDBPyConnection) -> None:
    """The measurement `parquet_reads` records, pinned, because the wrong order is SILENT.

    DuckDB's geometry functions take `ST_Point(longitude, latitude)`; its geodesic distance
    functions take the ordinates the other way round. `ST_Distance_Spheroid` answers NaN when they
    are swapped, which is why every distance here uses it -- `ST_Distance_Sphere` answers a
    plausible number that is 23% too large, and a wrong distance beside a reading is worse than none.
    """
    correct = connection.execute(
        "SELECT ST_Distance_Spheroid(ST_Point(?, ?), ST_Point(?, ?))",
        [BOISE_LATITUDE, BOISE_LONGITUDE, 43.62, -116.25],
    ).fetchone()[0]
    swapped = connection.execute(
        "SELECT ST_Distance_Spheroid(ST_Point(?, ?), ST_Point(?, ?))",
        [BOISE_LONGITUDE, BOISE_LATITUDE, -116.25, 43.62],
    ).fetchone()[0]
    plausible_but_wrong = connection.execute(
        "SELECT ST_Distance_Sphere(ST_Point(?, ?), ST_Point(?, ?))",
        [BOISE_LONGITUDE, BOISE_LATITUDE, -116.25, 43.62],
    ).fetchone()[0]

    assert correct == pytest.approx(4607.7, abs=1.0)
    assert math.isnan(swapped), "the spheroidal function refuses a longitude in the latitude slot"
    assert plausible_but_wrong == pytest.approx(5645.9, abs=1.0)
    assert not math.isclose(plausible_but_wrong, correct, rel_tol=0.05)


@pytest.mark.parametrize(
    "statement",
    [
        parquet_reads.SIGNAL_WINDOW_SUMMARY,
        parquet_reads.SIGNAL_DAY_VALUES,
        parquet_reads.SIGNAL_ADMITTED_CELLS,
        parquet_reads.SIGNAL_TIME_NEIGHBORS,
        parquet_reads.SIGNAL_CELL_DAY_COUNTS,
    ],
    ids=["window", "day", "cells", "neighbours", "cell-counts"],
)
def test_no_signal_statement_uses_the_spherical_distance_function(statement: str) -> None:
    """`ST_Distance_Sphere` is banned outright here: fed backwards it lies instead of refusing."""
    assert "ST_Distance_Sphere(" not in statement
    assert "ST_Distance_Spheroid(" in statement


# --- Read-only and layout tripwires ------------------------------------------------


def _statement_id(value: object) -> str:
    """Name a parametrised case by its statement's short label, never by the whole SQL text."""
    return value if isinstance(value, str) and "\n" not in value else ""


def all_statements() -> list[tuple[str, str]]:
    """Every DuckDB statement the agent can issue, named by its line-one marker."""
    point = spatial_support("signal", "observed")
    geometry = spatial_support("watersheds", "observed")
    return [
        ("window", parquet_reads.SIGNAL_WINDOW_SUMMARY),
        ("day", parquet_reads.SIGNAL_DAY_VALUES),
        ("cells", parquet_reads.SIGNAL_ADMITTED_CELLS),
        ("neighbours", parquet_reads.SIGNAL_TIME_NEIGHBORS),
        ("cell-counts", parquet_reads.SIGNAL_CELL_DAY_COUNTS),
        ("drought", parquet_reads.DROUGHT_RELEASE_SEVERITY),
        ("point-lane", parquet_reads.point_lane_rows(point)),  # type: ignore[arg-type]
        ("geometry-lane", parquet_reads.geometry_lane_rows(geometry)),  # type: ignore[arg-type]
    ]


@pytest.mark.parametrize(("name", "statement"), all_statements(), ids=_statement_id)
def test_every_agent_duckdb_statement_is_read_only(name: str, statement: str) -> None:
    """No agent-facing statement may mutate anything, in either dialect."""
    del name
    executable = "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--")).upper()
    for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "CREATE ", "DROP", "ALTER", "COPY "):
        assert verb not in executable, f"{verb} must not appear in an agent tool statement"


@pytest.mark.parametrize(("name", "statement"), all_statements(), ids=_statement_id)
def test_every_agent_duckdb_statement_disables_hive_partitioning(name: str, statement: str) -> None:
    """With it on, DuckDB injects `day` from the object path and it rides to the model as a column."""
    del name
    assert "hive_partitioning=false" in statement


@pytest.mark.parametrize(("name", "statement"), all_statements(), ids=_statement_id)
def test_every_agent_duckdb_statement_opens_with_a_line_one_marker(name: str, statement: str) -> None:
    """The marker protocol from `sql/AGENTS.md`, carried into the DuckDB half so a fake can dispatch."""
    del name
    first_line = statement.splitlines()[0]
    assert re.fullmatch(r"--\s+agent_\w+", first_line), first_line
