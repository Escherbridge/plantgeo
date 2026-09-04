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

# --- The signal plane --------------------------------------------------------------
#
# One scope, shared by the four signal statements so they cannot disagree about which cells are
# "near the point". Parameters after the part list, in bind order:
#
#   1 west, 2 east, 3 south, 4 north   the degree box that certainly contains the radius
#   5 LATITUDE, 6 LONGITUDE            the probe point -- latitude FIRST, see the note above
#   7 radius_meters                    the exact test the box only approximates
#   8 cell_limit                       MAX_CELL_FANOUT, applied to CELLS and never to rows
#
# The box runs first because a degree comparison is a range scan DuckDB can push into the Parquet
# row groups, while the geodesic distance is a per-row computation. The box is a strict superset of
# the circle, so it changes how many rows are measured and never which rows survive.
_SIGNAL_SCOPE: Final = f"""
WITH lane_rows AS (
    SELECT
        support_key,
        signal_name,
        normalized_unit,
        cell_id,
        observed_day,
        normalized_value,
        observation_count,
        newest_observed_at,
        coverage_fraction,
        allowed_client_exposure,
        cell_longitude,
        cell_latitude
    FROM {PARQUET_SOURCE}
    WHERE cell_longitude BETWEEN ? AND ?
      AND cell_latitude BETWEEN ? AND ?
),
measured AS (
    SELECT
        lane_rows.*,
        ST_Distance_Spheroid(
            ST_Point(lane_rows.cell_latitude, lane_rows.cell_longitude),
            {PROBE_DISTANCE_POINT}
        ) AS distance_m
    FROM lane_rows
),
in_radius AS (
    SELECT * FROM measured WHERE distance_m <= ?
),
admitted_cells AS (
    SELECT
        cell_id,
        min(distance_m) AS cell_distance_m,
        row_number() OVER (ORDER BY min(distance_m), cell_id) AS cell_rank
    FROM in_radius
    GROUP BY cell_id
),
scoped AS (
    SELECT in_radius.*
    FROM in_radius
    INNER JOIN admitted_cells ON admitted_cells.cell_id = in_radius.cell_id
    WHERE admitted_cells.cell_rank <= ?
)
"""

#: Window summary. No parameters beyond the shared scope.
#:
#: `minimum_value` / `maximum_value` / `mean_value` are taken from `normalized_value` rather than
#: from the `min_value` / `max_value` / `avg_value` columns the dropped matview carried. That is an
#: EXACT reproduction, not an approximation: those three equalled `normalized_value` on 100% of
#: 701,257 measured rows, which is why RUNBOOK section 0.22.4 left them out of the Parquet schema at
#: a 3.81x file-size saving. Do not re-derive them from anything else.
SIGNAL_WINDOW_SUMMARY: Final = f"""-- agent_signal_window_summary{_SIGNAL_SCOPE}
SELECT
    signal_name,
    support_key,
    normalized_unit,
    sum(observation_count) AS observation_count,
    count(DISTINCT cell_id) AS cell_count,
    count(DISTINCT observed_day) AS day_count,
    min(observed_day) AS first_observed_day,
    max(observed_day) AS last_observed_day,
    max(newest_observed_at) AS last_observed_at,
    min(normalized_value) AS minimum_value,
    max(normalized_value) AS maximum_value,
    sum(normalized_value * observation_count) / nullif(sum(observation_count), 0) AS mean_value,
    min(distance_m) AS nearest_cell_distance_m
FROM scoped
GROUP BY signal_name, support_key, normalized_unit
ORDER BY sum(observation_count) DESC, signal_name
"""

#: One day's values. No parameters beyond the shared scope.
#:
#: THE DAY IS THE PARTITION, not a column predicate. `warehouse.scan` is handed exactly the part
#: files of the requested day, so no row from a neighbouring day can reach this statement and no
#: timestamp is ever cast to a date to keep that true -- which is the whole of the named-day rule
#: that once moved 6,279 of 16,743 water-gauge rows onto the following day.
#:
#: `QUALIFY` filters on a window function after it is computed; it is how DuckDB spells the
#: `DISTINCT ON (...)` the PostgreSQL statement used to pick one nearest cell per signal group.
SIGNAL_DAY_VALUES: Final = f"""-- agent_signal_day_values{_SIGNAL_SCOPE},
nearest AS (
    SELECT
        signal_name,
        support_key,
        normalized_unit,
        observed_day,
        normalized_value,
        newest_observed_at,
        coverage_fraction,
        allowed_client_exposure,
        cell_id,
        distance_m
    FROM scoped
    QUALIFY row_number() OVER (
        PARTITION BY signal_name, support_key, normalized_unit
        ORDER BY distance_m, cell_id
    ) = 1
),
spread AS (
    SELECT
        signal_name,
        support_key,
        normalized_unit,
        sum(observation_count) AS observation_count,
        count(DISTINCT cell_id) AS cell_count,
        min(normalized_value) AS minimum_value,
        max(normalized_value) AS maximum_value,
        sum(normalized_value * observation_count) / nullif(sum(observation_count), 0) AS mean_value,
        max(newest_observed_at) AS last_observed_at
    FROM scoped
    GROUP BY signal_name, support_key, normalized_unit
)
SELECT
    nearest.signal_name,
    nearest.support_key,
    nearest.normalized_unit,
    nearest.observed_day,
    spread.observation_count,
    spread.cell_count,
    nearest.distance_m AS nearest_cell_distance_m,
    nearest.cell_id AS nearest_cell_id,
    nearest.normalized_value AS nearest_cell_value,
    nearest.newest_observed_at AS nearest_cell_observed_at,
    nearest.coverage_fraction AS nearest_cell_coverage_fraction,
    nearest.allowed_client_exposure AS nearest_cell_allowed_client_exposure,
    spread.minimum_value,
    spread.maximum_value,
    spread.mean_value,
    spread.last_observed_at
FROM nearest
LEFT JOIN spread
    ON spread.signal_name = nearest.signal_name
   AND spread.support_key = nearest.support_key
   AND spread.normalized_unit = nearest.normalized_unit
ORDER BY nearest.distance_m, nearest.signal_name
"""

#: The cells one signal read admitted, nearest first. No parameters beyond the shared scope.
#:
#: This is the scope `signal_coverage_on_day.sql` reads the ingest lane's absence ledger over. That
#: ledger is keyed by spatial-cell id and lives in PostgreSQL -- it is a governance record of what
#: an upstream was asked for and what it answered, not environmental data -- so the two halves of
#: `signal_value_on_day` are joined by this cell list rather than by a relation both can read.
SIGNAL_ADMITTED_CELLS: Final = f"""-- agent_signal_admitted_cells{_SIGNAL_SCOPE}
SELECT
    cell_id,
    min(distance_m) AS distance_meters
FROM scoped
GROUP BY cell_id
ORDER BY distance_meters, cell_id
"""

#: Nearest reading each side of one day. Parameters after the shared scope, in bind order:
#:
#:   9 requested_day   the day the BEFORE arm must fall strictly before
#:  10 requested_day   the day the AFTER arm must fall strictly after
#:  11 requested_day   the origin `day_offset` is signed from
#:  12 requested_day   the same origin, for the unsigned `distance_days`
#:
#: The day is bound four times because DuckDB's positional parameters are counted by appearance;
#: it is ONE value and the caller passes it four times rather than four values that could drift.
SIGNAL_TIME_NEIGHBORS: Final = f"""-- agent_signal_time_neighbors{_SIGNAL_SCOPE},
before_day AS (
    SELECT
        'before' AS side,
        signal_name,
        support_key,
        normalized_unit,
        observed_day,
        newest_observed_at,
        normalized_value,
        cell_id,
        distance_m
    FROM scoped
    WHERE observed_day < ?
    QUALIFY row_number() OVER (
        PARTITION BY signal_name, support_key, normalized_unit
        ORDER BY observed_day DESC, distance_m, cell_id
    ) = 1
),
after_day AS (
    SELECT
        'after' AS side,
        signal_name,
        support_key,
        normalized_unit,
        observed_day,
        newest_observed_at,
        normalized_value,
        cell_id,
        distance_m
    FROM scoped
    WHERE observed_day > ?
    QUALIFY row_number() OVER (
        PARTITION BY signal_name, support_key, normalized_unit
        ORDER BY observed_day ASC, distance_m, cell_id
    ) = 1
),
neighbours AS (
    SELECT * FROM before_day
    UNION ALL
    SELECT * FROM after_day
)
SELECT
    side,
    signal_name,
    support_key,
    normalized_unit,
    observed_day,
    newest_observed_at AS nearest_cell_observed_at,
    date_diff('day', ?, observed_day) AS day_offset,
    abs(date_diff('day', ?, observed_day)) AS distance_days,
    normalized_value AS nearest_cell_value,
    cell_id AS nearest_cell_id,
    distance_m AS nearest_cell_distance_m
FROM neighbours
ORDER BY signal_name, side, distance_days
"""

#: The cells near a point and what each holds on one day. Parameters after the shared scope:
#:
#:   9 requested_day   the day the counts are taken on
#:  10 row_limit       how many cells come back, nearest first
#:
#: THE LEFT JOIN IS THE POINT. A cell that reported inside the scan window but holds nothing on the
#: requested day comes back with a count of 0 rather than being dropped, so "the nearest cells" can
#: never quietly mean "the nearest cells that had data".
SIGNAL_CELL_DAY_COUNTS: Final = f"""-- agent_signal_cell_day_counts{_SIGNAL_SCOPE},
cells AS (
    SELECT
        cell_id,
        min(distance_m) AS distance_meters,
        any_value(cell_longitude) AS centroid_longitude,
        any_value(cell_latitude) AS centroid_latitude
    FROM scoped
    GROUP BY cell_id
),
on_day AS (
    SELECT
        cell_id,
        sum(observation_count) AS observation_count_on_day,
        count(DISTINCT signal_name) AS signal_count_on_day,
        max(newest_observed_at) AS last_observed_at
    FROM scoped
    WHERE observed_day = ?
    GROUP BY cell_id
)
SELECT
    cells.cell_id,
    cells.centroid_longitude,
    cells.centroid_latitude,
    cells.distance_meters,
    coalesce(on_day.observation_count_on_day, 0) AS observation_count_on_day,
    coalesce(on_day.signal_count_on_day, 0) AS signal_count_on_day,
    on_day.last_observed_at
FROM cells
LEFT JOIN on_day ON on_day.cell_id = cells.cell_id
ORDER BY cells.distance_meters, cells.cell_id
LIMIT ?
"""

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
