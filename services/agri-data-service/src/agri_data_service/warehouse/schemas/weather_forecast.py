"""Canonical deterministic sampled forecast values; see ../weather_forecast/AGENTS.md."""

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

WEATHER_FORECAST_SCHEMA = register_stream_schema(
    ParquetStreamSchema(
        name="weather-forecast",
        arrow_schema=pa.schema(
            [
                pa.field("run_id", pa.string(), nullable=False),
                pa.field("sample_id", pa.string(), nullable=False),
                pa.field("longitude", pa.float64(), nullable=False),
                pa.field("latitude", pa.float64(), nullable=False),
                pa.field("variable", pa.string(), nullable=False),
                pa.field("unit", pa.string(), nullable=False),
                pa.field("valid_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("lead_seconds", pa.int64(), nullable=False),
                pa.field("interval_start", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("interval_end", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("value", pa.float64(), nullable=True),
                pa.field("status", pa.string(), nullable=False),
            ],
            metadata={b"product_kind": b"forecast", b"schema_version": b"weather-forecast/v1"},
        ),
        sort_columns=("run_id", "sample_id", "variable", "valid_at"),
    )
)
