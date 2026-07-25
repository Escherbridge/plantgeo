"""Published forecast reads stay typed, lineage-complete, and bounded."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from agri_data_service.routes import forecasts as forecast_route

_HTTP_OK = 200
_HTTP_BAD_REQUEST = 400
_DEFAULT_TEST_LIMIT = 5


class _Mappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, object]]:
        return self.rows


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self.rows)


class _Session:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.parameters: dict[str, object] | None = None

    async def execute(self, statement: object, parameters: dict[str, object]) -> _Result:
        assert "agri.v_forecast_series_serving" in str(statement)
        assert "agri.forecast_value" not in str(statement)
        self.parameters = parameters
        return _Result(self.rows)


def _session_factory(session: _Session) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[_Session]:
        yield session

    return factory


def _request(**query: str) -> Any:
    return SimpleNamespace(args=query)


def _row(*, valid_time: datetime, geometry_omitted: bool = False) -> dict[str, object]:
    publication_id = uuid4()
    receipt_id = uuid4()
    series_id = uuid4()
    spatial_cell_id = uuid4()
    return {
        "publication_id": publication_id,
        "publication_key": "nasa-t2m-sql-linear-v1",
        "scope_key": "nasa-power-colorado-t2m",
        "publication_manifest_checksum": "a" * 64,
        "published_at": datetime(2026, 7, 22, tzinfo=UTC),
        "forecast_receipt_id": receipt_id,
        "receipt_checksum": "b" * 64,
        "series_id": series_id,
        "series_key": "nasa-power:point-001:t2m-c",
        "source_variant_key": "nasa-power:mERRA2:daily-point",
        "entity_type": "spatial_cell",
        "entity_key": "point-001",
        "metric_name": "temperature_2m_mean",
        "metric_unit": "degC",
        "representation_kind": "raw_native",
        "spatial_support_kind": "point_sample",
        "source_spatial_resolution_m": 50_000,
        "output_spatial_resolution_m": None,
        "source_temporal_support": timedelta(days=1),
        "output_temporal_support": timedelta(days=1),
        "aggregation_method": None,
        "release_set_id": uuid4(),
        "forecast_run_id": uuid4(),
        "forecast_method": "sql_linear",
        "input_release_checksum": "c" * 64,
        "feature_checksum": "d" * 64,
        "model_checksum": "e" * 64,
        "parameter_checksum": "f" * 64,
        "training_run_id": None,
        "training_code_checksum": None,
        "validation_checksum": None,
        "issue_time": datetime(2026, 7, 21, tzinfo=UTC),
        "valid_time": valid_time,
        "horizon_step": 1,
        "point_value": 21.25,
        "p10_value": 18.5,
        "p50_value": 21.25,
        "p90_value": 24.0,
        "spatial_cell_id": spatial_cell_id,
        "centroid_geojson": {"type": "Point", "coordinates": [-105.5, 39.0]},
        "cell_geojson": (
            None
            if geometry_omitted
            else {
                "type": "Polygon",
                "coordinates": [[[-106.0, 38.5], [-105.0, 38.5], [-105.0, 39.5], [-106.0, 38.5]]],
            }
        ),
        "geometry_omitted": geometry_omitted,
    }


def test_forecast_geojson_query_materializes_one_point_guarded_candidate() -> None:
    sql = str(forecast_route._FORECAST_SERVING_SQL)

    assert "spatial_page AS MATERIALIZED" in sql
    assert sql.count("ST_AsGeoJSON(serving_page.cell_geometry, 6, 0)") == 1
    storage_guard = sql.index("pg_column_size(serving_page.cell_geometry)")
    point_guard = sql.index("ST_NPoints(serving_page.cell_geometry)")
    serialization = sql.index("ST_AsGeoJSON(serving_page.cell_geometry, 6, 0)")
    assert storage_guard < point_guard < serialization


@pytest.mark.asyncio
async def test_forecast_route_reads_only_published_view_with_lineage_and_safe_geojson(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(valid_time=datetime(2026, 7, 23, tzinfo=UTC))
    session = _Session([row])
    monkeypatch.setattr(forecast_route, "published_reader_session", _session_factory(session))

    response = await forecast_route.list_forecast_series(
        _request(
            series_key="nasa-power:point-001:t2m-c",
            spatial="geojson",
            limit="5",
            valid_from="2026-07-23T00:00:00Z",
            valid_to="2026-07-24T00:00:00Z",
        )
    )
    payload = json.loads(response.body)

    assert response.status == _HTTP_OK
    assert payload["limit"] == _DEFAULT_TEST_LIMIT
    assert payload["hasMore"] is False
    assert payload["data"][0]["publication"]["receipt_checksum"] == "b" * 64
    assert payload["data"][0]["series"]["source_variant_key"] == "nasa-power:mERRA2:daily-point"
    assert payload["data"][0]["support"] == {
        "representation_kind": "raw_native",
        "spatial_support_kind": "point_sample",
        "source_spatial_resolution_m": 50_000.0,
        "output_spatial_resolution_m": None,
        "source_temporal_support": "P1D",
        "output_temporal_support": "P1D",
        "aggregation_method": None,
    }
    assert payload["data"][0]["lineage"]["forecast_method"] == "sql_linear"
    assert payload["data"][0]["spatial"]["geometry"]["type"] == "Polygon"
    assert session.parameters == {
        "series_key": "nasa-power:point-001:t2m-c",
        "spatial_mode": "geojson",
        "valid_from": datetime(2026, 7, 23, tzinfo=UTC),
        "valid_to": datetime(2026, 7, 24, tzinfo=UTC),
        "fetch_limit": 6,
        "offset": 0,
    }


@pytest.mark.asyncio
async def test_forecast_route_signals_oversized_geometry_and_bounded_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _row(valid_time=datetime(2026, 7, 23, tzinfo=UTC), geometry_omitted=True)
    second = _row(valid_time=datetime(2026, 7, 24, tzinfo=UTC), geometry_omitted=True)
    session = _Session([first, second])
    monkeypatch.setattr(forecast_route, "published_reader_session", _session_factory(session))

    response = await forecast_route.list_forecast_series(
        _request(series_key="nasa-power:point-001:t2m-c", spatial="geojson", limit="1")
    )
    payload = json.loads(response.body)

    assert payload["hasMore"] is True
    assert len(payload["data"]) == 1
    assert payload["data"][0]["spatial"]["geometry"] is None
    assert payload["data"][0]["spatial"]["geometry_omitted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({}, "series_key must contain"),
        ({"series_key": "metric", "spatial": "full"}, "spatial must be"),
        ({"series_key": "metric", "spatial": "geojson", "limit": "26"}, "limit must be between 1 and 25"),
        ({"series_key": "metric", "valid_from": "2026-07-23"}, "must include a timezone"),
        (
            {
                "series_key": "metric",
                "valid_from": "2026-07-24T00:00:00Z",
                "valid_to": "2026-07-23T00:00:00Z",
            },
            "valid_to must be later",
        ),
    ],
)
async def test_forecast_route_rejects_unbounded_or_ambiguous_queries(
    query: dict[str, str],
    message: str,
) -> None:
    response = await forecast_route.list_forecast_series(_request(**query))

    assert response.status == _HTTP_BAD_REQUEST
    assert message in json.loads(response.body)["error"]
