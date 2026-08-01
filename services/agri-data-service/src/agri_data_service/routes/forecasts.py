"""Bounded read-only access to published metric forecasts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict
from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse  # noqa: TC002 - sanic-ext evaluates handler annotations at runtime.
from sqlalchemy import text

from agri_data_service.db.engine import published_reader_session

forecasts_bp = Blueprint("forecasts", url_prefix="/forecasts")

_MAX_SERIES_KEY_LENGTH = 255
_MAX_OFFSET = 10_000
_MAX_PAGE_SIZE = 250
_MAX_GEOJSON_PAGE_SIZE = 25
_MAX_CELL_GEOJSON_BYTES = 65_536
_MAX_CELL_GEOMETRY_STORAGE_BYTES = 131_072
_MAX_CELL_GEOMETRY_POINTS = 2_048


class GeoJSONPoint(BaseModel):
    """A WGS84 point emitted by PostGIS."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Point"]
    coordinates: tuple[float, float]


class GeoJSONPolygon(BaseModel):
    """A bounded WGS84 polygon emitted by PostGIS."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon"]
    coordinates: list[list[tuple[float, float]]]


class ForecastPublicationRef(BaseModel):
    """Immutable publication and receipt identity for one served value."""

    publication_id: UUID
    publication_key: str
    scope_key: str
    publication_manifest_checksum: str
    published_at: datetime
    forecast_receipt_id: UUID
    receipt_checksum: str


class ForecastSeriesRef(BaseModel):
    """Typed metric and entity identity for one forecast series."""

    series_id: UUID
    series_key: str
    source_variant_key: str
    entity_type: str
    entity_key: str
    metric_name: str
    metric_unit: str


class ForecastSupport(BaseModel):
    """Native and output support retained from the governed series."""

    representation_kind: str
    spatial_support_kind: str
    source_spatial_resolution_m: float | None
    output_spatial_resolution_m: float | None
    source_temporal_support: timedelta
    output_temporal_support: timedelta
    aggregation_method: str | None


class ForecastLineage(BaseModel):
    """Checksums and run identities needed to trace a served value."""

    release_set_id: UUID
    forecast_run_id: UUID
    forecast_method: Literal["sql_linear", "ml"]
    input_release_checksum: str
    feature_checksum: str
    model_checksum: str
    parameter_checksum: str
    training_run_id: UUID | None
    training_code_checksum: str | None
    validation_checksum: str | None


class ForecastSpatial(BaseModel):
    """Optional bounded spatial context for chart and map consumers."""

    spatial_cell_id: UUID
    centroid: GeoJSONPoint | None = None
    geometry: GeoJSONPolygon | None = None
    geometry_omitted: bool = False


class ForecastPoint(BaseModel):
    """One published point forecast and its empirical uncertainty band."""

    issue_time: datetime
    valid_time: datetime
    horizon_step: int
    point_value: float
    p10_value: float | None
    p50_value: float | None
    p90_value: float | None


class ForecastServingRecord(BaseModel):
    """Typed serving record assembled only from the published forecast view."""

    publication: ForecastPublicationRef
    series: ForecastSeriesRef
    support: ForecastSupport
    lineage: ForecastLineage
    point: ForecastPoint
    spatial: ForecastSpatial | None


_FORECAST_SERVING_SQL = text(
    f"""
    WITH serving_page AS MATERIALIZED (
        SELECT
            serving.publication_id,
            serving.publication_key,
            serving.scope_key,
            serving.publication_manifest_checksum,
            serving.published_at,
            serving.forecast_receipt_id,
            serving.receipt_checksum,
            serving.series_id,
            serving.series_key,
            serving.source_variant_key,
            serving.entity_type,
            serving.entity_key,
            serving.metric_name,
            serving.metric_unit,
            serving.representation_kind,
            serving.spatial_support_kind,
            serving.source_spatial_resolution_m,
            serving.output_spatial_resolution_m,
            serving.source_temporal_support,
            serving.output_temporal_support,
            serving.aggregation_method,
            serving.release_set_id,
            serving.forecast_run_id,
            serving.forecast_method,
            serving.input_release_checksum,
            serving.feature_checksum,
            serving.model_checksum,
            serving.parameter_checksum,
            serving.training_run_id,
            serving.training_code_checksum,
            serving.validation_checksum,
            serving.issue_time,
            serving.valid_time,
            serving.horizon_step,
            serving.point_value,
            serving.p10_value,
            serving.p50_value,
            serving.p90_value,
            serving.spatial_cell_id,
            cell.id AS joined_spatial_cell_id,
            cell.centroid AS cell_centroid,
            cell.geometry AS cell_geometry
        FROM agri.v_forecast_series_serving AS serving
        LEFT JOIN agri.spatial_cell AS cell ON cell.id = serving.spatial_cell_id
        WHERE serving.series_key = :series_key
          AND (CAST(:valid_from AS timestamptz) IS NULL OR serving.valid_time >= CAST(:valid_from AS timestamptz))
          AND (CAST(:valid_to AS timestamptz) IS NULL OR serving.valid_time < CAST(:valid_to AS timestamptz))
        ORDER BY
            serving.valid_time,
            serving.forecast_receipt_id,
            serving.horizon_step
        LIMIT :fetch_limit
        OFFSET :offset
    ),
    spatial_page AS MATERIALIZED (
        SELECT
            serving_page.*,
            CASE
                WHEN :spatial_mode IN ('centroid', 'geojson')
                    THEN ST_AsGeoJSON(serving_page.cell_centroid, 6, 0)
                ELSE NULL
            END AS centroid_candidate,
            CASE
                WHEN :spatial_mode = 'geojson' THEN
                    CASE
                        WHEN pg_column_size(serving_page.cell_geometry) <= {_MAX_CELL_GEOMETRY_STORAGE_BYTES} THEN
                            CASE
                                WHEN ST_NPoints(serving_page.cell_geometry) <= {_MAX_CELL_GEOMETRY_POINTS}
                                    THEN ST_AsGeoJSON(serving_page.cell_geometry, 6, 0)
                                ELSE NULL
                            END
                        ELSE NULL
                    END
                ELSE NULL
            END AS cell_geojson_candidate
        FROM serving_page
    )
    SELECT
        spatial_page.publication_id,
        spatial_page.publication_key,
        spatial_page.scope_key,
        spatial_page.publication_manifest_checksum,
        spatial_page.published_at,
        spatial_page.forecast_receipt_id,
        spatial_page.receipt_checksum,
        spatial_page.series_id,
        spatial_page.series_key,
        spatial_page.source_variant_key,
        spatial_page.entity_type,
        spatial_page.entity_key,
        spatial_page.metric_name,
        spatial_page.metric_unit,
        spatial_page.representation_kind,
        spatial_page.spatial_support_kind,
        spatial_page.source_spatial_resolution_m,
        spatial_page.output_spatial_resolution_m,
        spatial_page.source_temporal_support,
        spatial_page.output_temporal_support,
        spatial_page.aggregation_method,
        spatial_page.release_set_id,
        spatial_page.forecast_run_id,
        spatial_page.forecast_method,
        spatial_page.input_release_checksum,
        spatial_page.feature_checksum,
        spatial_page.model_checksum,
        spatial_page.parameter_checksum,
        spatial_page.training_run_id,
        spatial_page.training_code_checksum,
        spatial_page.validation_checksum,
        spatial_page.issue_time,
        spatial_page.valid_time,
        spatial_page.horizon_step,
        spatial_page.point_value,
        spatial_page.p10_value,
        spatial_page.p50_value,
        spatial_page.p90_value,
        spatial_page.spatial_cell_id,
        spatial_page.centroid_candidate::jsonb AS centroid_geojson,
        CASE
            WHEN octet_length(spatial_page.cell_geojson_candidate) <= {_MAX_CELL_GEOJSON_BYTES}
                THEN spatial_page.cell_geojson_candidate::jsonb
            ELSE NULL
        END AS cell_geojson,
        CASE
            WHEN :spatial_mode = 'geojson'
             AND spatial_page.joined_spatial_cell_id IS NOT NULL
             AND (
                 spatial_page.cell_geojson_candidate IS NULL
                 OR octet_length(spatial_page.cell_geojson_candidate) > {_MAX_CELL_GEOJSON_BYTES}
             )
                THEN true
            ELSE false
        END AS geometry_omitted
    FROM spatial_page
    ORDER BY
        spatial_page.valid_time,
        spatial_page.forecast_receipt_id,
        spatial_page.horizon_step
    """
)


@forecasts_bp.get("/")
async def list_forecast_series(request: Request) -> HTTPResponse:
    """Return a bounded page from the current published forecast pointer."""
    try:
        query = _parse_forecast_query(request)
    except ValueError as exc:
        return json({"error": str(exc)}, status=400)

    async with published_reader_session() as session:
        result = await session.execute(_FORECAST_SERVING_SQL, query)
        rows = [dict(row) for row in result.mappings().all()]

    limit = cast("int", query["fetch_limit"]) - 1
    has_more = len(rows) > limit
    records = [_record_from_row(row) for row in rows[:limit]]
    return json(
        {
            "data": [record.model_dump(mode="json") for record in records],
            "limit": limit,
            "offset": query["offset"],
            "hasMore": has_more,
        },
        headers={"Cache-Control": "no-store"},
    )


def _parse_forecast_query(request: Request) -> dict[str, object]:
    series_key = (request.args.get("series_key") or "").strip()
    if not series_key or len(series_key) > _MAX_SERIES_KEY_LENGTH:
        raise ValueError("series_key must contain 1 to 255 characters")

    spatial_mode = request.args.get("spatial", "none")
    if spatial_mode not in {"none", "centroid", "geojson"}:
        raise ValueError("spatial must be one of none, centroid, or geojson")

    maximum_limit = _MAX_GEOJSON_PAGE_SIZE if spatial_mode == "geojson" else _MAX_PAGE_SIZE
    default_limit = min(100, maximum_limit)
    limit = _bounded_query_int(request.args.get("limit"), default_limit, 1, maximum_limit, "limit")
    offset = _bounded_query_int(request.args.get("offset"), 0, 0, _MAX_OFFSET, "offset")
    valid_from = _parse_query_datetime(request.args.get("valid_from"), "valid_from")
    valid_to = _parse_query_datetime(request.args.get("valid_to"), "valid_to")
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise ValueError("valid_to must be later than valid_from")

    return {
        "series_key": series_key,
        "spatial_mode": spatial_mode,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "fetch_limit": limit + 1,
        "offset": offset,
    }


def _bounded_query_int(
    value: str | None,
    default: int,
    minimum: int,
    maximum: int,
    field_name: str,
) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return parsed


def _parse_query_datetime(value: str | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _record_from_row(row: dict[str, Any]) -> ForecastServingRecord:
    spatial = None
    if row["spatial_cell_id"] is not None:
        spatial = ForecastSpatial(
            spatial_cell_id=row["spatial_cell_id"],
            centroid=row["centroid_geojson"],
            geometry=row["cell_geojson"],
            geometry_omitted=bool(row["geometry_omitted"]),
        )
    return ForecastServingRecord(
        publication=ForecastPublicationRef(**_take(row, ForecastPublicationRef)),
        series=ForecastSeriesRef(**_take(row, ForecastSeriesRef)),
        support=ForecastSupport(**_take(row, ForecastSupport)),
        lineage=ForecastLineage(**_take(row, ForecastLineage)),
        point=ForecastPoint(**_take(row, ForecastPoint)),
        spatial=spatial,
    )


def _take(row: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    return {field_name: row[field_name] for field_name in model.model_fields}
