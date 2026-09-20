"""Real DuckDB evidence for governed product-lane geometry, bounds, and read-only queries."""

# ruff: noqa: PLR2004 - the literals here are fixture cell counts and offsets the assertion checks directly.

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agri_data_service.agent import parquet_reads
from agri_data_service.agent.tools import _bbox_bounds
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


def product_rows(day: date) -> list[dict[str, Any]]:
    """One day of the dedicated VPD product at three source-cell coordinates."""
    rows: list[dict[str, Any]] = []
    for index, (cell_id, (longitude, latitude)) in enumerate(CELL_POSITIONS.items()):
        for signal, unit, base in (("vapor_pressure_deficit", "kPa", 1.0),):
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


# --- The signal plane --------------------------------------------------------------


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
    key, path = write_lane_day(tmp_path, "soil-field-vpd", DAY, product_rows(DAY))
    session = LocalSession(connection=connection, files={key: path})
    support = spatial_support("soil-field-vpd", "observed")
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


# --- Read-only and layout tripwires ------------------------------------------------


def _statement_id(value: object) -> str:
    """Name a parametrised case by its statement's short label, never by the whole SQL text."""
    return value if isinstance(value, str) and "\n" not in value else ""


def all_statements() -> list[tuple[str, str]]:
    """Every DuckDB statement the agent can issue, named by its line-one marker."""
    point = spatial_support("soil-field-vpd", "observed")
    geometry = spatial_support("watersheds", "observed")
    return [
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
