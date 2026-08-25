"""Leakage-free pre-fire feature plane and the composite fire-risk index built on it.

See `AGENTS.md` in this directory for the measured results, the confound that forced
stratification, and the two claims this module's own evidence refuted.

    python -m analysis.fire_risk_index --cell-dimension cells.tsv --report
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Final

from analysis.warehouse_session import WarehouseSession, open_warehouse_session

# The feature window must CLOSE before the outcome window OPENS. Coleman Creek ignited
# 2026-07-25; a feature window running into August would let a fire's own scar act as a
# "predictor" of that fire. See AGENTS.md "The leakage trap".
FEATURE_WINDOW: Final = (date(2026, 4, 1), date(2026, 6, 30))
OUTCOME_WINDOW_OPENS: Final = date(2026, 7, 1)

# Volumetric soil water at the surface and in the root zone. The `soil_wetness_*` family is a
# different upstream on a different grid and does not co-register with these.
SURFACE_MOISTURE_SIGNAL: Final = "soil_water_content_layer_1"
ROOT_ZONE_MOISTURE_SIGNAL: Final = "soil_water_content_layer_3"

ANALYSIS_GRID: Final = "sentinel2-ndvi-0p25deg"


def build_feature_plane(session: WarehouseSession, cell_dimension_path: str) -> None:
    """Materialise pre-fire covariates and the post-window fire outcome as in-memory tables."""
    connection = session.connection
    first_day, last_day = FEATURE_WINDOW

    connection.execute(
        "CREATE OR REPLACE VIEW analysis_cells AS "
        "SELECT * FROM read_csv(?, delim='\t', header=true) WHERE grid_name = ?",
        [cell_dimension_path, ANALYSIS_GRID],
    )

    connection.execute(f"""
        CREATE OR REPLACE TABLE pre_fire_signal AS
        SELECT cell_id,
          avg(normalized_value) FILTER (WHERE signal_name='{SURFACE_MOISTURE_SIGNAL}')   AS surface_moisture,
          avg(normalized_value) FILTER (WHERE signal_name='{ROOT_ZONE_MOISTURE_SIGNAL}') AS root_zone_moisture,
          avg(normalized_value) FILTER (WHERE signal_name='vapor_pressure_deficit')      AS vapor_pressure_deficit,
          avg(normalized_value) FILTER (WHERE signal_name='soil_temperature_level_1')    AS soil_temperature
        FROM read_parquet('{session.partition_glob("signal", "observed", year="2026")}')
        WHERE cell_id IS NOT NULL AND observed_day BETWEEN DATE '{first_day}' AND DATE '{last_day}'
        GROUP BY 1
    """)

    connection.execute(f"""
        CREATE OR REPLACE TABLE pre_fire_vegetation AS
        SELECT cell_id, count(*) AS observation_count, avg(metric_value) AS greenness
        FROM read_parquet('{session.partition_glob("vegetation", "*", year="2026")}')
        WHERE metric_name='ndvi' AND metric_value BETWEEN -1 AND 1 AND cell_id IS NOT NULL
          AND observed_day BETWEEN DATE '{first_day}' AND DATE '{last_day}'
        GROUP BY 1
    """)

    # Detections arrive at sub-kilometre resolution; bin them onto the 0.25 degree analysis grid
    # arithmetically (centres sit at .125/.375/.625/.875) rather than by spatial join.
    connection.execute(f"""
        CREATE OR REPLACE TABLE fire_outcome AS
        SELECT round(round((cell_longitude-0.125)/0.25)*0.25+0.125, 3) AS binned_longitude,
               round(round((cell_latitude -0.125)/0.25)*0.25+0.125, 3) AS binned_latitude,
               sum(detection_count) AS detection_count, sum(frp_sum) AS radiative_power
        FROM read_parquet('{session.partition_glob("fire-detections", "*", year="2026")}')
        WHERE observed_day >= DATE '{OUTCOME_WINDOW_OPENS}'
        GROUP BY 1,2
    """)

    connection.execute("""
        CREATE OR REPLACE TABLE fire_features AS
        SELECT c.cell_id, c.lon AS longitude, c.lat AS latitude,
               v.greenness, v.observation_count,
               s.surface_moisture, s.root_zone_moisture, s.vapor_pressure_deficit, s.soil_temperature,
               coalesce(f.detection_count, 0) AS detection_count,
               (f.detection_count IS NOT NULL AND f.detection_count > 0) AS burned
        FROM analysis_cells c
        JOIN pre_fire_vegetation v USING (cell_id)
        JOIN pre_fire_signal s USING (cell_id)
        LEFT JOIN fire_outcome f
          ON round(c.lon,3)=round(f.binned_longitude,3) AND round(c.lat,3)=round(f.binned_latitude,3)
    """)


def add_risk_index(session: WarehouseSession) -> None:
    """Score the rangeland strata only -- the index does not transfer to closed forest."""
    session.connection.execute("""
        CREATE OR REPLACE TABLE rangeland_risk AS
        WITH stratified AS (SELECT *, ntile(4) OVER (ORDER BY greenness) AS greenness_quartile FROM fire_features),
             rangeland AS (SELECT * FROM stratified WHERE greenness_quartile IN (1,2)),
             moments AS (
               SELECT avg(vapor_pressure_deficit) vpd_mean, stddev_samp(vapor_pressure_deficit) vpd_sd,
                      avg(soil_temperature) temp_mean,      stddev_samp(soil_temperature) temp_sd,
                      avg(greenness) green_mean,            stddev_samp(greenness) green_sd,
                      avg(surface_moisture) moist_mean,     stddev_samp(surface_moisture) moist_sd
               FROM rangeland)
        SELECT rangeland.*,
               ( (vapor_pressure_deficit-vpd_mean)/vpd_sd
               + (soil_temperature-temp_mean)/temp_sd
               + (greenness-green_mean)/green_sd
               - (surface_moisture-moist_mean)/moist_sd ) / 4.0 AS risk_index
        FROM rangeland, moments
    """)


def area_under_curve(session: WarehouseSession, column: str, table: str = "rangeland_risk") -> float:
    """Rank-based AUC: the probability a burned cell outranks an unburned one."""
    result = session.connection.execute(f"""
        WITH ranked AS (SELECT burned, rank() OVER (ORDER BY {column}) AS rank_position
                        FROM {table} WHERE {column} IS NOT NULL),
             totals AS (SELECT count(*) FILTER (WHERE burned) AS burned_count,
                               count(*) FILTER (WHERE NOT burned) AS unburned_count,
                               sum(rank_position) FILTER (WHERE burned) AS burned_rank_sum
                        FROM ranked)
        SELECT (burned_rank_sum - burned_count*(burned_count+1)/2.0) / (burned_count*unburned_count)
        FROM totals
    """).fetchone()
    return float(result[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the pre-fire feature plane and score it.")
    parser.add_argument("--cell-dimension", required=True, help="TSV of cell_id, cell_key, grid_name, lon, lat")
    parser.add_argument("--report", action="store_true", help="print the discrimination table")
    arguments = parser.parse_args()

    session = open_warehouse_session()
    build_feature_plane(session, arguments.cell_dimension)
    add_risk_index(session)

    if arguments.report:
        for column in ("risk_index", "vapor_pressure_deficit", "soil_temperature", "surface_moisture", "greenness"):
            print(f"{column:<28} AUC {area_under_curve(session, column):.3f}")


if __name__ == "__main__":
    main()
