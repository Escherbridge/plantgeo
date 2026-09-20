"""Every DuckDB statement the agent issues against the Parquet warehouse.

DuckDB SQL is built here in Python rather than under `sql/agent/`, following the convention
`parquet_ops/warehouse_reader.py` and `parquet_ops/snapshot_products.py` already set: `sql/AGENTS.md`
describes a PostgreSQL tree loaded through `text()`, and a second dialect in it would be read with
the wrong grammar. The PostgreSQL statements the agent still issues stay there unchanged.

EVERY STATEMENT'S FIRST `?` IS THE PART-FILE LIST, because `warehouse.scan` supplies it. The
parameters after it are listed above each statement in the order DuckDB binds them, which is the
order they appear in the text. See `agent/AGENTS.md`, "Reading the Parquet warehouse".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from agri_data_service.parquet_ops.warehouse_reader import GeometrySupport, PointSupport

#: `hive_partitioning=false` is not optional: with it on, DuckDB injects `layer`, `kind`, `zoom`,
#: `year`, `month` and `day` from the object path, and `day` would ride into an answer as though the
#: lane had published it as a column. `union_by_name` tolerates a lane re-exported with a new column.
PARQUET_SOURCE: Final = "read_parquet(?, hive_partitioning=false, union_by_name=true)"

# --- TWO ORDINATE CONVENTIONS LIVE IN THIS FILE, AND MIXING THEM IS SILENT ---------------
#
# DuckDB's GEOMETRY functions take a point as `ST_Point(longitude, latitude)`: `ST_X` of
# `ST_Point(-116.25, 43.62)` is -116.25, and `ST_MakeEnvelope(xmin, ymin, xmax, ymax)` is
# `west, south, east, north`. That is the convention `parquet_ops/warehouse_reader.py` already uses.
#
# DuckDB's GEODESIC DISTANCE functions take the ordinates the OTHER WAY ROUND -- the first is the
# LATITUDE. Measured against DuckDB 1.5.4 on 2026-09-04:
#
#     ST_Distance_Spheroid(ST_Point(43.6, -116.2), ST_Point(43.62, -116.25)) = 4607.70 m   correct
#     ST_Distance_Spheroid(ST_Point(-116.2, 43.6), ST_Point(-116.25, 43.62)) = NaN         refused
#     ST_Distance_Sphere(  ST_Point(-116.2, 43.6), ST_Point(-116.25, 43.62)) = 5645.93 m   WRONG
#
# The third line is why every distance here uses `ST_Distance_Spheroid` and never
# `ST_Distance_Sphere`: fed the ordinates backwards, the spheroidal function answers NaN because
# -116.2 is not a latitude, while the spherical one answers a plausible number that is 23% too
# large. A distance the model quotes beside a reading has to be wrong LOUDLY or not at all.
# `ST_Distance_Spheroid` is also the exact analogue of the PostgreSQL statements' `::geography`
# distance -- both are WGS84 ellipsoidal -- so this is a reproduction and not an approximation.
#
# EVERY STATEMENT BELOW THEREFORE BINDS ITS PROBE POINT AS `latitude, longitude`, in that order,
# for the distance, and separately as `longitude, latitude` where a GEOMETRY predicate needs it.
# `tests/test_agent_parquet_reads.py::test_the_probe_point_is_bound_latitude_first` pins it.
PROBE_DISTANCE_POINT: Final = "ST_Point(?, ?)"

# --- The drought release set -------------------------------------------------------

#: One row per PUBLISHED release in the scanned window. Parameters after the part list:
#:
#:   1 longitude, 2 latitude   the probe point, in GEOMETRY order -- this is `ST_Intersects`, an
#:                             exact point-in-polygon test and not a distance, so the ordinates go
#:                             the ordinary way round. See the ordinate note at the top of the file.
#:
#: `published_class_count` counts EVERY polygon the release published, anywhere, and the three
#: `FILTER (WHERE covers_probe)` aggregates describe only the polygons over the point. That split is
#: what makes a release with `covering_class_count` 0 a measured "this release existed and found no
#: drought here" rather than an absence -- the distinction the whole tool exists to carry. One pass
#: over the parts answers both, so the geometry column is decoded once.
DROUGHT_RELEASE_SEVERITY: Final = f"""-- agent_drought_release_severity
WITH lane_rows AS (
    SELECT valid_date, dm_category, ingested_at, geom
    FROM {PARQUET_SOURCE}
),
probed AS (
    SELECT
        valid_date,
        dm_category,
        ingested_at,
        ST_Intersects(ST_GeomFromWKB(geom), ST_Point(?, ?)) AS covers_probe
    FROM lane_rows
)
SELECT
    valid_date,
    count(*) AS published_class_count,
    max(dm_category) FILTER (WHERE covers_probe) AS severity_class,
    count(*) FILTER (WHERE covers_probe) AS covering_class_count,
    max(ingested_at) FILTER (WHERE covers_probe) AS published_at
FROM probed
GROUP BY valid_date
ORDER BY valid_date DESC
"""


# --- Generic lane rows -------------------------------------------------------------
#
# Two shapes, because a lane either carries a representative point or carries WKB, and
# `parquet_ops.warehouse_reader.spatial_support` decides which from the lane's REGISTERED schema.
# The column names interpolated below come from that registered schema and can never be
# caller-supplied; `warehouse_reader._projection` interpolates the same identifiers for the same
# reason.


def point_lane_rows(support: PointSupport, *, distance_column: str = "distance_meters") -> str:
    """Nearest rows of a lane that carries a coordinate pair, with an exact geodesic distance.

    Parameters after the part list, in bind order: west, east, south, north, LATITUDE, LONGITUDE,
    radius_meters, row_limit. The probe is latitude-first; see the ordinate note at the top of file.
    """
    longitude = f'"{support.longitude_column}"'
    latitude = f'"{support.latitude_column}"'
    return f"""-- agent_point_lane_rows
WITH lane_rows AS (
    SELECT * FROM {PARQUET_SOURCE}
    WHERE {longitude} BETWEEN ? AND ?
      AND {latitude} BETWEEN ? AND ?
),
measured AS (
    SELECT
        lane_rows.*,
        ST_Distance_Spheroid(ST_Point({latitude}, {longitude}), {PROBE_DISTANCE_POINT}) AS {distance_column}
    FROM lane_rows
)
SELECT * FROM measured
WHERE {distance_column} <= ?
ORDER BY {distance_column}
LIMIT ?
"""


def geometry_lane_rows(support: GeometrySupport) -> str:
    """Nearest rows of a lane that carries WKB, keyed by a metre-accurate box around the probe.

    Parameters after the part list, in bind order: west, south, east, north (GEOMETRY order, for
    `ST_MakeEnvelope`), LATITUDE, LONGITUDE (for the geodesic distance), longitude, latitude
    (GEOMETRY order again, for the exact point-in-polygon test), row_limit. Both conventions appear
    in this one statement because both functions appear in it; see the ordinate note at the top.

    TWO HONEST DEPARTURES FROM THE POSTGRESQL STATEMENT, both visible in the column names.
    DuckDB's geodesic distance functions accept POINTS only, so there is no `ST_DWithin(geography)`
    to reproduce: membership is decided by the metre-accurate BOX rather than the circle inside it,
    which admits a corner feature up to sqrt(2) times the radius away, and the distance reported is
    to the feature's CENTROID rather than to its nearest edge. `covers_probe_point` is exact and
    answers the question a polygon is usually asked.
    """
    geometry = f'"{support.geometry_column}"'
    return f"""-- agent_geometry_lane_rows
WITH lane_rows AS (
    SELECT * EXCLUDE ({geometry}), ST_GeomFromWKB({geometry}) AS agent_geometry
    FROM {PARQUET_SOURCE}
),
in_box AS (
    SELECT * FROM lane_rows
    WHERE ST_Intersects(agent_geometry, ST_MakeEnvelope(?, ?, ?, ?))
),
measured AS (
    SELECT
        * EXCLUDE (agent_geometry),
        ST_X(ST_Centroid(agent_geometry)) AS centroid_longitude,
        ST_Y(ST_Centroid(agent_geometry)) AS centroid_latitude,
        ST_Distance_Spheroid(
            ST_Point(ST_Y(ST_Centroid(agent_geometry)), ST_X(ST_Centroid(agent_geometry))),
            {PROBE_DISTANCE_POINT}
        ) AS centroid_distance_meters,
        ST_Intersects(agent_geometry, ST_Point(?, ?)) AS covers_probe_point
    FROM in_box
)
SELECT * FROM measured
ORDER BY centroid_distance_meters
LIMIT ?
"""
