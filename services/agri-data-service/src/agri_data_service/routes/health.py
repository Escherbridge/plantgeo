"""Separate process liveness from publication-plane readiness."""

import asyncio
from typing import Any

import structlog
from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse
from sqlalchemy import text

from agri_data_service.config import settings
from agri_data_service.db.engine import published_reader_session, receiver_writer_session

logger = structlog.get_logger()

health_bp = Blueprint("health", url_prefix="/")

EXPECTED_ALEMBIC_REVISION = "20260801_0014"
REQUIRED_EXTENSIONS = ("postgis", "timescaledb", "vector", "pgcrypto")
PUBLICATION_TABLE_PRIVILEGES = (
    ("agri", "release_set", "SELECT"),
    ("agri", "release_set", "INSERT"),
    ("agri", "release_set", "UPDATE"),
    ("agri", "job_definition", "SELECT"),
    ("agri", "job_definition", "INSERT"),
    ("agri", "job_run", "SELECT"),
    ("agri", "job_run", "INSERT"),
    ("agri", "job_run", "UPDATE"),
    ("agri", "job_output", "SELECT"),
    ("agri", "job_output", "INSERT"),
    ("agri", "job_output", "UPDATE"),
    ("agri", "artifact", "SELECT"),
    ("agri", "artifact", "INSERT"),
    ("agri", "publication_pointer", "SELECT"),
    ("agri", "publication_pointer", "INSERT"),
    ("agri", "publication_pointer", "UPDATE"),
    ("agri", "job_outbox", "SELECT"),
    ("agri", "job_outbox", "INSERT"),
    ("agri", "data_source", "SELECT"),
    ("agri", "data_source", "INSERT"),
    ("agri", "source_release", "SELECT"),
    ("agri", "source_release", "INSERT"),
    ("agri", "release_set_item", "SELECT"),
    ("agri", "release_set_item", "INSERT"),
    ("agri", "spatial_cell", "SELECT"),
    ("agri", "spatial_cell", "INSERT"),
    ("agri", "cell_source_crosswalk", "SELECT"),
    ("agri", "cell_source_crosswalk", "INSERT"),
    ("agri", "signal_observation", "SELECT"),
    ("agri", "signal_observation", "INSERT"),
    ("agri", "signal_coverage_audit", "SELECT"),
    ("agri", "signal_coverage_audit", "INSERT"),
    ("agri", "source_coverage_audit", "SELECT"),
    ("agri", "source_coverage_audit", "INSERT"),
    ("agri", "drought_polygon_snapshot", "SELECT"),
    ("agri", "drought_polygon_snapshot", "INSERT"),
    ("agri", "historical_promotion_bundle", "SELECT"),
    ("agri", "historical_promotion_bundle", "INSERT"),
    ("agri", "historical_promotion_bundle", "UPDATE"),
    ("agri", "historical_promotion_chunk_receipt", "SELECT"),
    ("agri", "historical_promotion_chunk_receipt", "INSERT"),
    ("agri", "historical_promotion_data_source_receipt", "SELECT"),
    ("agri", "historical_promotion_data_source_receipt", "INSERT"),
    ("agri", "historical_promotion_source_release_receipt", "SELECT"),
    ("agri", "historical_promotion_source_release_receipt", "INSERT"),
    ("agri", "historical_promotion_artifact_receipt", "SELECT"),
    ("agri", "historical_promotion_artifact_receipt", "INSERT"),
    ("agri", "historical_promotion_artifact_receipt", "UPDATE"),
)
MIGRATION_TABLE_PRIVILEGES = (("public", "alembic_version", "SELECT"),)
RECEIVER_SEQUENCE_PRIVILEGES = (
    ("agri", "signal_observation_id_seq", "USAGE"),
    ("agri", "signal_observation_id_seq", "SELECT"),
    ("agri", "drought_polygon_snapshot_id_seq", "USAGE"),
    ("agri", "drought_polygon_snapshot_id_seq", "SELECT"),
)
TABLE_PRIVILEGE_UNIVERSE = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
COLUMN_PRIVILEGE_UNIVERSE = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
FORECAST_ROLES = (
    "plantgeo_forecast_writer",
    "plantgeo_forecast_publisher",
    "plantgeo_forecast_reader",
    "plantgeo_forecast_mv_refresher",
)
FORECAST_ROLE_RELATION_PRIVILEGES = (
    *(
        ("plantgeo_forecast_writer", "agri", relation_name, "SELECT")
        for relation_name in (
            "data_source",
            "source_release",
            "release_set",
            "release_set_item",
            "spatial_cell",
            "cell_source_crosswalk",
            "signal_observation",
            "signal_coverage_audit",
            "source_coverage_audit",
            "drought_polygon_snapshot",
            "job_definition",
            "job_run",
            "job_output",
        )
    ),
    *(
        ("plantgeo_forecast_writer", "agri", relation_name, privilege_name)
        for relation_name in (
            "forecast_series",
            "forecast_entity_state",
            "forecast_observation",
            "forecast_feature_snapshot",
            "forecast_model",
            "forecast_training_run",
            "forecast_quality_policy",
            "forecast_run",
            "forecast_backtest_metric",
            "forecast_receipt",
            "forecast_value",
            "forecast_hindcast_run",
            "forecast_hindcast_value",
        )
        for privilege_name in ("SELECT", "INSERT")
    ),
    *(
        ("plantgeo_forecast_writer", "agri", relation_name, "INSERT")
        for relation_name in ("job_definition", "job_run", "job_output")
    ),
    *(
        ("plantgeo_forecast_publisher", "agri", relation_name, "SELECT")
        for relation_name in (
            "artifact",
            "data_source",
            "source_release",
            "release_set",
            "release_set_item",
            "spatial_cell",
            "cell_source_crosswalk",
            "signal_observation",
            "signal_coverage_audit",
            "source_coverage_audit",
            "drought_polygon_snapshot",
            "job_definition",
            "job_run",
            "job_output",
            "forecast_series",
            "forecast_entity_state",
            "forecast_observation",
            "forecast_feature_snapshot",
            "forecast_model",
            "forecast_training_run",
            "forecast_quality_policy",
            "forecast_run",
            "forecast_backtest_metric",
            "forecast_receipt",
            "forecast_value",
            "forecast_hindcast_run",
            "forecast_hindcast_value",
            "forecast_publication",
            "forecast_publication_item",
            "publication_pointer",
        )
    ),
    *(
        ("plantgeo_forecast_publisher", "agri", relation_name, "INSERT")
        for relation_name in (
            "job_output",
            "forecast_publication",
            "forecast_publication_item",
            "publication_pointer",
        )
    ),
    *(
        ("plantgeo_forecast_reader", "agri", relation_name, "SELECT")
        for relation_name in (
            "v_forecast_series_serving",
            "v_forecast_hindcast_outcome",
            "mv_forecast_ml_daily_serving",
            "spatial_cell",
        )
    ),
    (
        "plantgeo_forecast_mv_refresher",
        "agri",
        "mv_forecast_ml_daily_serving",
        "SELECT",
    ),
)
FORECAST_ROLE_COLUMN_PRIVILEGES = (
    ("plantgeo_forecast_publisher", "agri", "job_output", "state", "UPDATE"),
    ("plantgeo_forecast_publisher", "agri", "job_output", "validated_at", "UPDATE"),
    *(
        ("plantgeo_forecast_publisher", "agri", "forecast_feature_snapshot", column_name, "UPDATE")
        for column_name in ("status", "validation_metrics", "validated_at")
    ),
    *(
        ("plantgeo_forecast_publisher", "agri", "forecast_training_run", column_name, "UPDATE")
        for column_name in (
            "status",
            "model_checksum",
            "validation_checksum",
            "validation_metrics",
            "completed_at",
            "validated_at",
        )
    ),
    *(
        ("plantgeo_forecast_publisher", "agri", "forecast_run", column_name, "UPDATE")
        for column_name in ("status", "backtest_passed", "validated_at")
    ),
    (
        "plantgeo_forecast_publisher",
        "agri",
        "forecast_backtest_metric",
        "passed",
        "UPDATE",
    ),
    *(
        ("plantgeo_forecast_publisher", "agri", "forecast_hindcast_run", column_name, "UPDATE")
        for column_name in (
            "status",
            "quality_passed",
            "mae",
            "rmse",
            "naive_rmse",
            "skill_score",
            "bias",
            "mape",
            "coverage_fraction",
            "interval_coverage_fraction",
            "receipt_checksum",
            "recorded_at",
            "finalized_at",
        )
    ),
    *(
        ("plantgeo_forecast_publisher", "agri", "forecast_receipt", column_name, "UPDATE")
        for column_name in ("status", "receipt_checksum", "finalized_at")
    ),
    *(
        ("plantgeo_forecast_publisher", "agri", "forecast_publication", column_name, "UPDATE")
        for column_name in ("state", "manifest_checksum", "published_at")
    ),
)
FORECAST_ROLE_SEQUENCE_PRIVILEGES = (
    ("plantgeo_forecast_writer", "agri", "forecast_observation_id_seq", "USAGE"),
    ("plantgeo_forecast_writer", "agri", "forecast_observation_id_seq", "SELECT"),
    ("plantgeo_forecast_writer", "agri", "forecast_value_id_seq", "USAGE"),
    ("plantgeo_forecast_writer", "agri", "forecast_value_id_seq", "SELECT"),
    ("plantgeo_forecast_writer", "agri", "forecast_hindcast_value_id_seq", "USAGE"),
    ("plantgeo_forecast_writer", "agri", "forecast_hindcast_value_id_seq", "SELECT"),
)
FORECAST_ROLE_FUNCTION_PRIVILEGES = (
    *(
        ("plantgeo_forecast_writer", signature, "EXECUTE")
        for signature in (
            "agri.forecast_quantiles_valid(double precision[])",
            "agri.v_signal_timeseries_contract(timestamp with time zone,uuid)",
            "agri.forecast_timeseries_base(uuid,timestamp with time zone)",
            "agri.forecast_percentile(uuid,uuid,timestamp with time zone,"
            "timestamp with time zone,timestamp with time zone,double precision)",
            "agri.forecast_normalized_series(uuid,uuid,timestamp with time zone,"
            "timestamp with time zone,timestamp with time zone,interval)",
            "agri.forecast_rolling_stats(uuid,uuid,timestamp with time zone,integer)",
            "agri.forecast_linear_regression(uuid,uuid,timestamp with time zone,"
            "timestamp with time zone,integer,interval,integer)",
            "agri.forecast_linear_backtest(uuid,uuid,timestamp with time zone,"
            "timestamp with time zone,integer,interval,integer)",
            "agri.forecast_linear_residual_bands(uuid,uuid,timestamp with time zone,"
            "timestamp with time zone,integer,interval,integer)",
            "agri.forecast_hindcast_value_checksum(timestamp with time zone,integer,"
            "double precision,double precision,double precision,double precision,"
            "double precision,double precision,uuid,character varying)",
            "agri.forecast_hindcast_receipt_checksum(uuid)",
        )
    ),
    *(
        ("plantgeo_forecast_publisher", signature, "EXECUTE")
        for signature in (
            "agri.validate_forecast_feature_snapshot(uuid)",
            "agri.validate_forecast_training_run(uuid,character varying,character varying,jsonb)",
            "agri.validate_forecast_run(uuid)",
            "agri.finalize_forecast_receipt(uuid,character varying)",
            "agri.finalize_forecast_hindcast_run(uuid,character varying)",
            "agri.forecast_hindcast_receipt_checksum(uuid)",
            "agri.v_signal_timeseries_contract(timestamp with time zone,uuid)",
            "agri.forecast_timeseries_base(uuid,timestamp with time zone)",
            "agri.forecast_linear_regression(uuid,uuid,timestamp with time zone,"
            "timestamp with time zone,integer,interval,integer)",
            "agri.forecast_linear_residual_bands(uuid,uuid,timestamp with time zone,"
            "timestamp with time zone,integer,interval,integer)",
            "agri.publish_forecast_publication(uuid,character varying)",
        )
    ),
    (
        "plantgeo_forecast_reader",
        "agri.forecast_hindcast_signal_timeseries(uuid,uuid,character varying,integer,timestamp with time zone)",
        "EXECUTE",
    ),
    (
        "plantgeo_forecast_mv_refresher",
        "agri.refresh_forecast_ml_daily_serving()",
        "EXECUTE",
    ),
)
SEQUENCE_PRIVILEGE_UNIVERSE = ("USAGE", "SELECT", "UPDATE")


def _sql_values(rows: tuple[tuple[str, ...], ...]) -> str:
    return ", ".join("(" + ", ".join(f"'{value}'" for value in row) + ")" for row in rows)


_EXTENSION_VALUES = _sql_values(tuple((name,) for name in REQUIRED_EXTENSIONS))
_PRIVILEGE_VALUES = _sql_values(PUBLICATION_TABLE_PRIVILEGES + MIGRATION_TABLE_PRIVILEGES)
_TABLE_PRIVILEGE_VALUES = _sql_values(tuple((privilege,) for privilege in TABLE_PRIVILEGE_UNIVERSE))
_COLUMN_PRIVILEGE_VALUES = _sql_values(tuple((privilege,) for privilege in COLUMN_PRIVILEGE_UNIVERSE))
_FORECAST_ROLE_VALUES = _sql_values(tuple((role_name,) for role_name in FORECAST_ROLES))
_FORECAST_RELATION_PRIVILEGE_VALUES = _sql_values(FORECAST_ROLE_RELATION_PRIVILEGES)
_FORECAST_COLUMN_PRIVILEGE_VALUES = _sql_values(FORECAST_ROLE_COLUMN_PRIVILEGES)
_FORECAST_SEQUENCE_PRIVILEGE_VALUES = _sql_values(FORECAST_ROLE_SEQUENCE_PRIVILEGES)
_FORECAST_FUNCTION_PRIVILEGE_VALUES = _sql_values(FORECAST_ROLE_FUNCTION_PRIVILEGES)
_SEQUENCE_PRIVILEGE_VALUES = _sql_values(tuple((privilege,) for privilege in SEQUENCE_PRIVILEGE_UNIVERSE))
_RECEIVER_RELATION_PRIVILEGE_VALUES = _sql_values(PUBLICATION_TABLE_PRIVILEGES)
_RECEIVER_SEQUENCE_PRIVILEGE_VALUES = _sql_values(RECEIVER_SEQUENCE_PRIVILEGES)

_READINESS_SQL = f"""
WITH required_extensions(extname) AS (
    VALUES {_EXTENSION_VALUES}
),
required_privileges(schema_name, table_name, privilege_name) AS (
    VALUES {_PRIVILEGE_VALUES}
),
publication_tables AS (
    SELECT DISTINCT schema_name, table_name FROM required_privileges
),
table_objects AS (
    SELECT
        schema_name,
        table_name,
        to_regclass(format('%I.%I', schema_name, table_name)) AS relation_oid
    FROM publication_tables
),
table_privilege_universe(privilege_name) AS (
    VALUES {_TABLE_PRIVILEGE_VALUES}
),
column_privilege_universe(privilege_name) AS (
    VALUES {_COLUMN_PRIVILEGE_VALUES}
),
forecast_role_names(role_name) AS (
    VALUES {_FORECAST_ROLE_VALUES}
),
forecast_roles AS (
    SELECT expected.role_name, role.oid AS role_oid
    FROM forecast_role_names AS expected
    LEFT JOIN pg_roles AS role ON role.rolname = expected.role_name
),
forecast_relation_privileges(role_name, schema_name, relation_name, privilege_name) AS (
    VALUES {_FORECAST_RELATION_PRIVILEGE_VALUES}
),
forecast_column_privileges(role_name, schema_name, relation_name, column_name, privilege_name) AS (
    VALUES {_FORECAST_COLUMN_PRIVILEGE_VALUES}
),
forecast_sequence_privileges(role_name, schema_name, sequence_name, privilege_name) AS (
    VALUES {_FORECAST_SEQUENCE_PRIVILEGE_VALUES}
),
forecast_function_privileges(role_name, function_signature, privilege_name) AS (
    VALUES {_FORECAST_FUNCTION_PRIVILEGE_VALUES}
),
sequence_privilege_universe(privilege_name) AS (
    VALUES {_SEQUENCE_PRIVILEGE_VALUES}
),
agri_relations AS (
    SELECT class.oid, class.relname AS relation_name
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
),
agri_relation_columns AS (
    SELECT relation.oid, relation.relation_name, attribute.attname AS column_name
    FROM agri_relations AS relation
    INNER JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid
    WHERE attribute.attnum > 0
      AND NOT attribute.attisdropped
),
forecast_sequences AS (
    SELECT class.oid, class.relname AS sequence_name
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind = 'S'
),
forecast_functions AS (
    SELECT procedure.oid, procedure.proname, procedure.proacl, procedure.proowner
    FROM pg_proc AS procedure
    INNER JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'agri'
),
agri_sequences AS (
    SELECT class.oid, class.relname AS sequence_name, class.relowner
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri' AND class.relkind = 'S'
),
agri_functions AS (
    SELECT procedure.oid, procedure.proowner
    FROM pg_proc AS procedure
    INNER JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'agri'
)
SELECT
    (
        SELECT count(*) = {len(REQUIRED_EXTENSIONS)}
        FROM pg_extension installed
        JOIN required_extensions required USING (extname)
    ) AS extensions_ready,
    (
        to_regclass('public.alembic_version') IS NOT NULL
        AND coalesce(
            has_table_privilege(
                current_user,
                to_regclass('public.alembic_version'),
                'SELECT'
            ),
            false
        )
    ) AS migration_catalog_ready,
    (SELECT bool_and(relation_oid IS NOT NULL) FROM table_objects) AS tables_ready,
    (
        has_database_privilege(current_user, current_database(), 'CONNECT')
        AND NOT has_database_privilege(current_user, current_database(), 'CONNECT WITH GRANT OPTION')
        AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
        AND has_schema_privilege(current_user, 'agri', 'USAGE')
        AND has_schema_privilege(current_user, 'public', 'USAGE')
        AND NOT has_schema_privilege(current_user, 'agri', 'CREATE')
        AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
        AND NOT has_schema_privilege(current_user, 'agri', 'USAGE WITH GRANT OPTION')
        AND NOT has_schema_privilege(current_user, 'public', 'USAGE WITH GRANT OPTION')
        AND (
            SELECT bool_and(
                coalesce(
                    has_table_privilege(
                        current_user,
                        objects.relation_oid,
                        required.privilege_name
                    ),
                    false
                )
                AND NOT coalesce(
                    has_table_privilege(
                        current_user,
                        objects.relation_oid,
                        required.privilege_name || ' WITH GRANT OPTION'
                    ),
                    false
                )
            )
            FROM required_privileges required
            JOIN table_objects objects USING (schema_name, table_name)
        )
        AND NOT EXISTS (
            SELECT 1
            FROM table_objects objects
            CROSS JOIN table_privilege_universe candidate
            WHERE NOT EXISTS (
                SELECT 1
                FROM required_privileges required
                WHERE required.schema_name = objects.schema_name
                  AND required.table_name = objects.table_name
                  AND required.privilege_name = candidate.privilege_name
            )
            AND coalesce(
                has_table_privilege(
                    current_user,
                    objects.relation_oid,
                    candidate.privilege_name
                ),
                false
            )
        )
        AND EXISTS (
            SELECT 1
            FROM pg_roles role
            WHERE role.rolname = current_user
              AND NOT role.rolsuper
              AND NOT role.rolcreaterole
              AND NOT role.rolcreatedb
              AND NOT role.rolreplication
              AND NOT role.rolbypassrls
        )
    ) AS privileges_ready,
    (
        (SELECT count(role_oid) = {len(FORECAST_ROLES)} FROM forecast_roles)
        AND NOT EXISTS (
            SELECT 1
            FROM pg_roles AS role
            INNER JOIN forecast_roles AS expected ON expected.role_oid = role.oid
            WHERE role.rolcanlogin
               OR role.rolinherit
               OR role.rolsuper
               OR role.rolcreatedb
               OR role.rolcreaterole
               OR role.rolreplication
               OR role.rolbypassrls
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_auth_members AS membership
            INNER JOIN pg_roles AS member_role ON member_role.oid = membership.member
            INNER JOIN forecast_roles AS expected ON expected.role_oid = member_role.oid
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_auth_members AS membership
            INNER JOIN forecast_roles AS capability
                ON capability.role_oid = membership.roleid
            WHERE membership.admin_option
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            WHERE role.role_oid IS NOT NULL
              AND (
                  NOT has_database_privilege(role.role_oid, current_database(), 'CONNECT')
                  OR has_database_privilege(role.role_oid, current_database(), 'CREATE')
                  OR NOT has_schema_privilege(role.role_oid, 'agri', 'USAGE')
                  OR has_schema_privilege(role.role_oid, 'agri', 'CREATE')
                  OR has_schema_privilege(role.role_oid, 'agri', 'USAGE WITH GRANT OPTION')
              )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            CROSS JOIN agri_relations AS relation
            CROSS JOIN table_privilege_universe AS candidate
            WHERE role.role_oid IS NOT NULL
              AND (
                  (
                      coalesce(
                          has_table_privilege(role.role_oid, relation.oid, candidate.privilege_name),
                          false
                      ) IS DISTINCT FROM EXISTS (
                          SELECT 1
                          FROM forecast_relation_privileges AS expected
                          WHERE expected.role_name = role.role_name
                            AND expected.schema_name = 'agri'
                            AND expected.relation_name = relation.relation_name
                            AND expected.privilege_name = candidate.privilege_name
                      )
                  )
                  OR coalesce(
                      has_table_privilege(
                          role.role_oid,
                          relation.oid,
                          candidate.privilege_name || ' WITH GRANT OPTION'
                      ),
                      false
                  )
                )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            CROSS JOIN agri_relation_columns AS column_object
            CROSS JOIN column_privilege_universe AS candidate
            WHERE role.role_oid IS NOT NULL
              AND (
                  (
                      coalesce(
                          has_column_privilege(
                              role.role_oid,
                              column_object.oid,
                              column_object.column_name,
                              candidate.privilege_name
                          ),
                          false
                      ) IS DISTINCT FROM (
                          EXISTS (
                              SELECT 1
                              FROM forecast_relation_privileges AS expected
                              WHERE expected.role_name = role.role_name
                                AND expected.schema_name = 'agri'
                                AND expected.relation_name = column_object.relation_name
                                AND expected.privilege_name = candidate.privilege_name
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM forecast_column_privileges AS expected
                              WHERE expected.role_name = role.role_name
                                AND expected.schema_name = 'agri'
                                AND expected.relation_name = column_object.relation_name
                                AND expected.column_name = column_object.column_name
                                AND expected.privilege_name = candidate.privilege_name
                          )
                      )
                  )
                  OR coalesce(
                      has_column_privilege(
                          role.role_oid,
                          column_object.oid,
                          column_object.column_name,
                          candidate.privilege_name || ' WITH GRANT OPTION'
                      ),
                      false
                  )
                )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            CROSS JOIN forecast_sequences AS sequence_object
            CROSS JOIN sequence_privilege_universe AS candidate
            WHERE role.role_oid IS NOT NULL
              AND (
                  (
                      coalesce(
                          has_sequence_privilege(
                              role.role_oid,
                              sequence_object.oid,
                              candidate.privilege_name
                          ),
                          false
                      ) IS DISTINCT FROM EXISTS (
                          SELECT 1
                          FROM forecast_sequence_privileges AS expected
                          WHERE expected.role_name = role.role_name
                            AND expected.schema_name = 'agri'
                            AND expected.sequence_name = sequence_object.sequence_name
                            AND expected.privilege_name = candidate.privilege_name
                      )
                  )
                  OR coalesce(
                      has_sequence_privilege(
                          role.role_oid,
                          sequence_object.oid,
                          candidate.privilege_name || ' WITH GRANT OPTION'
                      ),
                      false
                  )
                )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            CROSS JOIN forecast_functions AS function_object
            WHERE role.role_oid IS NOT NULL
              AND (
                  (
                      coalesce(
                          has_function_privilege(role.role_oid, function_object.oid, 'EXECUTE'),
                          false
                      ) IS DISTINCT FROM EXISTS (
                          SELECT 1
                          FROM forecast_function_privileges AS expected
                          WHERE expected.role_name = role.role_name
                            AND to_regprocedure(expected.function_signature) = function_object.oid
                            AND expected.privilege_name = 'EXECUTE'
                      )
                  )
                  OR coalesce(
                      has_function_privilege(
                          role.role_oid,
                          function_object.oid,
                          'EXECUTE WITH GRANT OPTION'
                      ),
                      false
                  )
                )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_functions AS function_object
            WHERE EXISTS (
                SELECT 1
                FROM aclexplode(
                    coalesce(
                        function_object.proacl,
                        acldefault('f', function_object.proowner)
                    )
                ) AS access
                WHERE access.grantee = 0
                  AND access.privilege_type = 'EXECUTE'
            )
        )
        AND EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            INNER JOIN pg_auth_members AS membership
                ON membership.roleid = role.role_oid
            INNER JOIN pg_roles AS runtime_role
                ON runtime_role.oid = membership.member
            WHERE role.role_name = 'plantgeo_forecast_reader'
              AND runtime_role.rolname = current_user
              AND NOT membership.admin_option
              AND membership.inherit_option
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            WHERE role.role_name = 'plantgeo_forecast_writer'
              AND role.role_oid IS NOT NULL
              AND pg_has_role(current_user, role.role_oid, 'MEMBER')
        )
        AND EXISTS (
            SELECT 1
            FROM pg_roles AS role
            WHERE role.rolname = current_user
              AND NOT role.rolsuper
              AND NOT role.rolcreaterole
              AND NOT role.rolcreatedb
              AND NOT role.rolreplication
              AND NOT role.rolbypassrls
        )
        AND has_database_privilege(current_user, current_database(), 'CONNECT')
        AND NOT has_database_privilege(current_user, current_database(), 'CONNECT WITH GRANT OPTION')
        AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
        AND has_schema_privilege(current_user, 'agri', 'USAGE')
        AND has_schema_privilege(current_user, 'public', 'USAGE')
        AND NOT has_schema_privilege(current_user, 'agri', 'CREATE')
        AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
        AND NOT has_schema_privilege(current_user, 'agri', 'USAGE WITH GRANT OPTION')
        AND NOT has_schema_privilege(current_user, 'public', 'USAGE WITH GRANT OPTION')
        AND NOT coalesce(
            has_table_privilege(
                current_user,
                to_regclass('public.alembic_version'),
                'SELECT WITH GRANT OPTION'
            ),
            false
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            WHERE role.role_name = 'plantgeo_forecast_publisher'
              AND role.role_oid IS NOT NULL
              AND pg_has_role(current_user, role.role_oid, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            WHERE role.role_name = 'plantgeo_forecast_mv_refresher'
              AND role.role_oid IS NOT NULL
              AND pg_has_role(current_user, role.role_oid, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_auth_members AS membership
            INNER JOIN pg_roles AS member_role ON member_role.oid = membership.member
            INNER JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
            WHERE member_role.rolname = current_user
              AND granted_role.rolname <> 'plantgeo_forecast_reader'
        )
        AND NOT pg_has_role(
            current_user,
            (SELECT database.datdba FROM pg_database AS database
             WHERE database.datname = current_database()),
            'MEMBER'
        )
        AND NOT pg_has_role(
            current_user,
            (SELECT namespace.nspowner FROM pg_namespace AS namespace
             WHERE namespace.nspname = 'agri'),
            'MEMBER'
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_relations AS relation
            INNER JOIN pg_class AS class ON class.oid = relation.oid
            WHERE pg_has_role(current_user, class.relowner, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_functions AS function_object
            WHERE pg_has_role(current_user, function_object.proowner, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_sequences AS sequence_object
            WHERE pg_has_role(current_user, sequence_object.relowner, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_sequences AS sequence_object
            WHERE has_sequence_privilege(current_user, sequence_object.oid, 'USAGE')
               OR has_sequence_privilege(current_user, sequence_object.oid, 'SELECT')
               OR has_sequence_privilege(current_user, sequence_object.oid, 'UPDATE')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_functions AS function_object
            WHERE (
                coalesce(
                    has_function_privilege(current_user, function_object.oid, 'EXECUTE'),
                    false
                ) IS DISTINCT FROM (
                    function_object.oid = to_regprocedure(
                        'agri.forecast_hindcast_signal_timeseries(uuid,uuid,character varying,'
                        'integer,timestamp with time zone)'
                    )
                )
                OR coalesce(
                    has_function_privilege(
                        current_user,
                        function_object.oid,
                        'EXECUTE WITH GRANT OPTION'
                    ),
                    false
                )
            )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_relations AS relation
            CROSS JOIN table_privilege_universe AS candidate
            WHERE (
                coalesce(
                    has_table_privilege(current_user, relation.oid, candidate.privilege_name),
                    false
                ) IS DISTINCT FROM (
                    relation.relation_name IN (
                        'v_forecast_series_serving',
                        'v_forecast_hindcast_outcome',
                        'mv_forecast_ml_daily_serving',
                        'spatial_cell'
                    )
                    AND candidate.privilege_name = 'SELECT'
                  )
                OR coalesce(
                    has_table_privilege(
                        current_user,
                        relation.oid,
                        candidate.privilege_name || ' WITH GRANT OPTION'
                    ),
                    false
                )
            )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_relations AS relation
            CROSS JOIN column_privilege_universe AS candidate
            WHERE (
                has_any_column_privilege(current_user, relation.oid, candidate.privilege_name)
                AND NOT (
                    relation.relation_name IN (
                        'v_forecast_series_serving',
                        'v_forecast_hindcast_outcome',
                        'mv_forecast_ml_daily_serving',
                        'spatial_cell'
                    )
                    AND candidate.privilege_name = 'SELECT'
                  )
            )
            OR has_any_column_privilege(
                current_user,
                relation.oid,
                candidate.privilege_name || ' WITH GRANT OPTION'
            )
        )
        AND NOT EXISTS (
            SELECT 1 FROM forecast_relation_privileges
            WHERE to_regclass(format('%I.%I', schema_name, relation_name)) IS NULL
        )
        AND NOT EXISTS (
            SELECT 1 FROM forecast_sequence_privileges
            WHERE to_regclass(format('%I.%I', schema_name, sequence_name)) IS NULL
        )
        AND NOT EXISTS (
            SELECT 1 FROM forecast_function_privileges
            WHERE to_regprocedure(function_signature) IS NULL
        )
    ) AS forecast_roles_ready
"""

_FORECAST_ROLE_CONTRACT_SQL = f"""
WITH forecast_role_names(role_name) AS (
    VALUES {_FORECAST_ROLE_VALUES}
),
forecast_roles AS (
    SELECT expected.role_name, role.oid AS role_oid
    FROM forecast_role_names AS expected
    LEFT JOIN pg_roles AS role ON role.rolname = expected.role_name
),
forecast_relation_privileges(role_name, schema_name, relation_name, privilege_name) AS (
    VALUES {_FORECAST_RELATION_PRIVILEGE_VALUES}
),
forecast_column_privileges(role_name, schema_name, relation_name, column_name, privilege_name) AS (
    VALUES {_FORECAST_COLUMN_PRIVILEGE_VALUES}
),
forecast_sequence_privileges(role_name, schema_name, sequence_name, privilege_name) AS (
    VALUES {_FORECAST_SEQUENCE_PRIVILEGE_VALUES}
),
forecast_function_privileges(role_name, function_signature, privilege_name) AS (
    VALUES {_FORECAST_FUNCTION_PRIVILEGE_VALUES}
),
table_privilege_universe(privilege_name) AS (
    VALUES {_TABLE_PRIVILEGE_VALUES}
),
column_privilege_universe(privilege_name) AS (
    VALUES {_COLUMN_PRIVILEGE_VALUES}
),
sequence_privilege_universe(privilege_name) AS (
    VALUES {_SEQUENCE_PRIVILEGE_VALUES}
),
agri_relations AS (
    SELECT class.oid, class.relname AS relation_name
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
),
agri_relation_columns AS (
    SELECT relation.oid, relation.relation_name, attribute.attname AS column_name
    FROM agri_relations AS relation
    INNER JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid
    WHERE attribute.attnum > 0 AND NOT attribute.attisdropped
),
forecast_sequences AS (
    SELECT class.oid, class.relname AS sequence_name
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind = 'S'
),
forecast_functions AS (
    SELECT procedure.oid, procedure.proacl, procedure.proowner
    FROM pg_proc AS procedure
    INNER JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'agri'
)
SELECT
    (SELECT count(role_oid) = {len(FORECAST_ROLES)} FROM forecast_roles)
    AND NOT EXISTS (
        SELECT 1
        FROM pg_roles AS role
        INNER JOIN forecast_roles AS expected ON expected.role_oid = role.oid
        WHERE role.rolcanlogin OR role.rolinherit OR role.rolsuper OR role.rolcreatedb
           OR role.rolcreaterole OR role.rolreplication OR role.rolbypassrls
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN pg_roles AS member_role ON member_role.oid = membership.member
        INNER JOIN forecast_roles AS expected ON expected.role_oid = member_role.oid
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN forecast_roles AS capability ON capability.role_oid = membership.roleid
        WHERE membership.admin_option
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        WHERE role.role_oid IS NOT NULL
          AND (
              NOT has_database_privilege(role.role_oid, current_database(), 'CONNECT')
              OR has_database_privilege(role.role_oid, current_database(), 'CREATE')
              OR NOT has_schema_privilege(role.role_oid, 'agri', 'USAGE')
              OR has_schema_privilege(role.role_oid, 'agri', 'CREATE')
              OR has_schema_privilege(role.role_oid, 'agri', 'USAGE WITH GRANT OPTION')
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        CROSS JOIN agri_relations AS relation
        CROSS JOIN table_privilege_universe AS candidate
        WHERE role.role_oid IS NOT NULL
          AND (
              coalesce(
                  has_table_privilege(role.role_oid, relation.oid, candidate.privilege_name),
                  false
              ) IS DISTINCT FROM EXISTS (
                  SELECT 1
                  FROM forecast_relation_privileges AS expected
                  WHERE expected.role_name = role.role_name
                    AND expected.schema_name = 'agri'
                    AND expected.relation_name = relation.relation_name
                    AND expected.privilege_name = candidate.privilege_name
              )
              OR coalesce(
                  has_table_privilege(
                      role.role_oid,
                      relation.oid,
                      candidate.privilege_name || ' WITH GRANT OPTION'
                  ),
                  false
              )
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        CROSS JOIN agri_relation_columns AS column_object
        CROSS JOIN column_privilege_universe AS candidate
        WHERE role.role_oid IS NOT NULL
          AND (
              coalesce(
                  has_column_privilege(
                      role.role_oid,
                      column_object.oid,
                      column_object.column_name,
                      candidate.privilege_name
                  ),
                  false
              ) IS DISTINCT FROM (
                  EXISTS (
                      SELECT 1
                      FROM forecast_relation_privileges AS expected
                      WHERE expected.role_name = role.role_name
                        AND expected.schema_name = 'agri'
                        AND expected.relation_name = column_object.relation_name
                        AND expected.privilege_name = candidate.privilege_name
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM forecast_column_privileges AS expected
                      WHERE expected.role_name = role.role_name
                        AND expected.schema_name = 'agri'
                        AND expected.relation_name = column_object.relation_name
                        AND expected.column_name = column_object.column_name
                        AND expected.privilege_name = candidate.privilege_name
                  )
              )
              OR coalesce(
                  has_column_privilege(
                      role.role_oid,
                      column_object.oid,
                      column_object.column_name,
                      candidate.privilege_name || ' WITH GRANT OPTION'
                  ),
                  false
              )
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        CROSS JOIN forecast_sequences AS sequence_object
        CROSS JOIN sequence_privilege_universe AS candidate
        WHERE role.role_oid IS NOT NULL
          AND (
              coalesce(
                  has_sequence_privilege(role.role_oid, sequence_object.oid, candidate.privilege_name),
                  false
              ) IS DISTINCT FROM EXISTS (
                  SELECT 1
                  FROM forecast_sequence_privileges AS expected
                  WHERE expected.role_name = role.role_name
                    AND expected.schema_name = 'agri'
                    AND expected.sequence_name = sequence_object.sequence_name
                    AND expected.privilege_name = candidate.privilege_name
              )
              OR coalesce(
                  has_sequence_privilege(
                      role.role_oid,
                      sequence_object.oid,
                      candidate.privilege_name || ' WITH GRANT OPTION'
                  ),
                  false
              )
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        CROSS JOIN forecast_functions AS function_object
        WHERE role.role_oid IS NOT NULL
          AND (
              coalesce(
                  has_function_privilege(role.role_oid, function_object.oid, 'EXECUTE'),
                  false
              ) IS DISTINCT FROM EXISTS (
                  SELECT 1
                  FROM forecast_function_privileges AS expected
                  WHERE expected.role_name = role.role_name
                    AND to_regprocedure(expected.function_signature) = function_object.oid
                    AND expected.privilege_name = 'EXECUTE'
              )
              OR coalesce(
                  has_function_privilege(
                      role.role_oid,
                      function_object.oid,
                      'EXECUTE WITH GRANT OPTION'
                  ),
                  false
              )
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_functions AS function_object
        WHERE EXISTS (
            SELECT 1
            FROM aclexplode(coalesce(function_object.proacl, acldefault('f', function_object.proowner))) AS access
            WHERE access.grantee = 0 AND access.privilege_type = 'EXECUTE'
        )
    )
    AND NOT EXISTS (
        SELECT 1 FROM forecast_relation_privileges
        WHERE to_regclass(format('%I.%I', schema_name, relation_name)) IS NULL
    )
    AND NOT EXISTS (
        SELECT 1 FROM forecast_sequence_privileges
        WHERE to_regclass(format('%I.%I', schema_name, sequence_name)) IS NULL
    )
    AND NOT EXISTS (
        SELECT 1 FROM forecast_function_privileges
        WHERE to_regprocedure(function_signature) IS NULL
    ) AS forecast_role_contracts_ready
"""

_RECEIVER_ROLE_BOUNDARY_SQL = f"""
WITH expected_relation_privileges(schema_name, relation_name, privilege_name) AS (
    VALUES {_RECEIVER_RELATION_PRIVILEGE_VALUES}
),
expected_sequence_privileges(schema_name, sequence_name, privilege_name) AS (
    VALUES {_RECEIVER_SEQUENCE_PRIVILEGE_VALUES}
),
table_privilege_universe(privilege_name) AS (
    VALUES {_TABLE_PRIVILEGE_VALUES}
),
column_privilege_universe(privilege_name) AS (
    VALUES {_COLUMN_PRIVILEGE_VALUES}
),
sequence_privilege_universe(privilege_name) AS (
    VALUES {_SEQUENCE_PRIVILEGE_VALUES}
),
agri_relations AS (
    SELECT class.oid, class.relname AS relation_name, class.relowner
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
),
agri_sequences AS (
    SELECT class.oid, class.relname AS sequence_name, class.relowner
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri' AND class.relkind = 'S'
),
agri_functions AS (
    SELECT procedure.oid, procedure.proowner
    FROM pg_proc AS procedure
    INNER JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'agri'
)
SELECT
    NOT EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN pg_roles AS member_role ON member_role.oid = membership.member
        WHERE member_role.rolname = current_user
    )
    AND NOT pg_has_role(
        current_user,
        (SELECT database.datdba FROM pg_database AS database
         WHERE database.datname = current_database()),
        'MEMBER'
    )
    AND NOT pg_has_role(
        current_user,
        (SELECT namespace.nspowner FROM pg_namespace AS namespace
         WHERE namespace.nspname = 'agri'),
        'MEMBER'
    )
    AND NOT EXISTS (
        SELECT 1 FROM agri_relations AS relation
        WHERE pg_has_role(current_user, relation.relowner, 'MEMBER')
    )
    AND NOT EXISTS (
        SELECT 1 FROM agri_sequences AS sequence_object
        WHERE pg_has_role(current_user, sequence_object.relowner, 'MEMBER')
    )
    AND NOT EXISTS (
        SELECT 1 FROM agri_functions AS function_object
        WHERE pg_has_role(current_user, function_object.proowner, 'MEMBER')
    )
    AND NOT EXISTS (
        SELECT 1
        FROM agri_relations AS relation
        CROSS JOIN table_privilege_universe AS candidate
        WHERE (
            coalesce(
                has_table_privilege(current_user, relation.oid, candidate.privilege_name),
                false
            ) IS DISTINCT FROM EXISTS (
                SELECT 1 FROM expected_relation_privileges AS expected
                WHERE expected.schema_name = 'agri'
                  AND expected.relation_name = relation.relation_name
                  AND expected.privilege_name = candidate.privilege_name
            )
            OR coalesce(
                has_table_privilege(
                    current_user,
                    relation.oid,
                    candidate.privilege_name || ' WITH GRANT OPTION'
                ),
                false
            )
        )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM agri_relations AS relation
        CROSS JOIN column_privilege_universe AS candidate
        WHERE (
            has_any_column_privilege(current_user, relation.oid, candidate.privilege_name)
            AND NOT EXISTS (
                SELECT 1 FROM expected_relation_privileges AS expected
                WHERE expected.schema_name = 'agri'
                  AND expected.relation_name = relation.relation_name
                  AND expected.privilege_name = candidate.privilege_name
            )
        )
        OR has_any_column_privilege(
            current_user,
            relation.oid,
            candidate.privilege_name || ' WITH GRANT OPTION'
        )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM agri_sequences AS sequence_object
        CROSS JOIN sequence_privilege_universe AS candidate
        WHERE (
            coalesce(
                has_sequence_privilege(current_user, sequence_object.oid, candidate.privilege_name),
                false
            ) IS DISTINCT FROM EXISTS (
                SELECT 1 FROM expected_sequence_privileges AS expected
                WHERE expected.schema_name = 'agri'
                  AND expected.sequence_name = sequence_object.sequence_name
                  AND expected.privilege_name = candidate.privilege_name
            )
            OR coalesce(
                has_sequence_privilege(
                    current_user,
                    sequence_object.oid,
                    candidate.privilege_name || ' WITH GRANT OPTION'
                ),
                false
            )
        )
    )
    AND NOT EXISTS (
        SELECT 1 FROM agri_functions AS function_object
        WHERE has_function_privilege(current_user, function_object.oid, 'EXECUTE')
    )
    AND NOT EXISTS (
        SELECT 1 FROM expected_relation_privileges AS expected
        WHERE to_regclass(format('%I.%I', expected.schema_name, expected.relation_name)) IS NULL
    )
    AND NOT EXISTS (
        SELECT 1 FROM expected_sequence_privileges AS expected
        WHERE to_regclass(format('%I.%I', expected.schema_name, expected.sequence_name)) IS NULL
    ) AS receiver_role_boundary_ready
"""

_MIGRATION_SQL = """
SELECT count(*) = 1 AND min(version_num) = :expected_revision AS migration_ready
FROM public.alembic_version
"""


@health_bp.get("/health")
async def health_check(_request: Request) -> HTTPResponse:
    """Report process liveness without coupling it to dependencies."""
    return json({"status": "ok"})


@health_bp.get("/ready")
async def readiness_check(_request: Request) -> HTTPResponse:
    """Require one explicit least-privilege production database profile."""
    profile = settings.service_profile
    receiver_identity_ready = profile != "receiver_writer" or bool(
        (
            settings.local_publication_receiver_enabled
            and settings.local_publish_token is not None
            and settings.local_publish_actor is not None
        )
        or (
            settings.historical_promotion_receiver_enabled
            and settings.historical_promotion_token is not None
            and settings.historical_promotion_actor is not None
        )
    )
    checks = {
        "database_profile": profile in {"receiver_writer", "published_reader"},
        "receiver_identity": receiver_identity_ready,
        "extensions": False,
        "migration": False,
        "profile_tables": False,
        "runtime_privileges": False,
        "forecast_role_contracts": False,
    }
    if profile == "combined_local":
        return json({"status": "not_ready", "profile": profile, "checks": checks}, status=503)

    session_factory = receiver_writer_session if profile == "receiver_writer" else published_reader_session
    try:
        async with asyncio.timeout(2):
            async with session_factory() as session:
                result = await session.execute(text(_READINESS_SQL))
                row: dict[str, Any] = dict(result.mappings().one())
                checks["extensions"] = bool(row["extensions_ready"])
                checks["profile_tables"] = bool(row["tables_ready"])
                if profile == "published_reader":
                    checks["runtime_privileges"] = bool(row["forecast_roles_ready"])
                    checks["forecast_role_contracts"] = bool(row["forecast_roles_ready"])
                else:
                    boundary_result = await session.execute(text(_RECEIVER_ROLE_BOUNDARY_SQL))
                    contract_result = await session.execute(text(_FORECAST_ROLE_CONTRACT_SQL))
                    checks["runtime_privileges"] = bool(row["privileges_ready"] and boundary_result.scalar_one())
                    checks["forecast_role_contracts"] = bool(contract_result.scalar_one())
                if bool(row["migration_catalog_ready"]):
                    migration_result = await session.execute(
                        text(_MIGRATION_SQL),
                        {"expected_revision": EXPECTED_ALEMBIC_REVISION},
                    )
                    checks["migration"] = bool(migration_result.scalar_one())
    except Exception:
        logger.warning("readiness_check_failed")

    ready = all(checks.values())
    return json(
        {"status": "ready" if ready else "not_ready", "profile": profile, "checks": checks},
        status=200 if ready else 503,
    )
