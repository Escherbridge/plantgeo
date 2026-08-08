"""Freeze the readiness contract: the extensions, privileges, roles and revision this build requires.

Every name here is a governance assertion, not a default. ``tests/test_health_readiness.py``
reads several of them back and compares them to the live database, and
``queries.py`` renders the tuples into the SQL row-literals the probe statements
compare against -- so an edit here changes what /ready refuses to serve on. See
``routes/AGENTS.md`` for why the probe SQL lives in ``sql/routes/health_*.sql``.
"""

EXPECTED_ALEMBIC_REVISION = "20260803_0018"
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
        )
    ),
    *(
        ("plantgeo_forecast_publisher", signature, "EXECUTE")
        for signature in (
            "agri.validate_forecast_feature_snapshot(uuid)",
            "agri.validate_forecast_training_run(uuid,character varying,character varying,jsonb)",
            "agri.validate_forecast_run(uuid)",
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
        "plantgeo_forecast_mv_refresher",
        "agri.refresh_forecast_ml_daily_serving()",
        "EXECUTE",
    ),
)
SEQUENCE_PRIVILEGE_UNIVERSE = ("USAGE", "SELECT", "UPDATE")
