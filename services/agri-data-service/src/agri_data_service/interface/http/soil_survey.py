"""Thin HTTP adapter for the admitted-release SSURGO native-geometry read (port slice S3).

Checks run in one fixed order, cheapest and most storage-free first (`AGENTS.md` in this
directory): the region binding, then the admission pin, then the zoom gate -- so an unbound region
or a below-z13 request never opens object storage to say so. Everything else parses, gathers,
queries and renders through `planes.soil_survey`; this module owns only request parsing, ordering
the gates, and mapping faults to a transport status. See that module's own "Admitted release read
path" banner for the wire contract this route serves.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

import duckdb
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from pydantic import ValidationError
from sanic import Blueprint, Request, json

from agri_data_service.config import settings
from agri_data_service.foundation.observability.logging import get_logger
from agri_data_service.foundation.region import UNBOUND_REASON_NO_SOURCE, is_layer_bound, load_region
from agri_data_service.foundation.soil_survey.receipts import SoilSurveyError
from agri_data_service.parquet_ops.duckdb_session import (
    SERVING_MAX_CONCURRENT_READS,
    ServingSession,
    run_serving_read,
)
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.pipeline.parquet.availability_primitives import AvailabilityError
from agri_data_service.pipeline.parquet.availability_storage import BotoAvailabilityStorage
from agri_data_service.planes.soil_survey import (
    SoilSurveyViewport,
    gather_admitted_soil_survey_viewport,
    load_admitted_release,
    render_served_soil_survey,
    run_admitted_soil_survey_query,
    soil_survey_unavailable,
)

if TYPE_CHECKING:
    from sanic.response import HTTPResponse

logger = get_logger(__name__)

soil_survey_bp = Blueprint("soil_survey", url_prefix="/soil-survey")

#: This platform layer's slug in `foundation/region/layer_availability.py`'s catalogue.
_LAYER_SLUG: Final = "soil-survey"
_MAX_HTTP_BYTES: Final = 16 * 1024 * 1024
_BBOX_COORDINATE_COUNT: Final = 4
_READ_DEADLINE_SECONDS: Final = 14

#: Bounds how many requests can run the object-store gather phase (release index, shard manifests,
#: part bytes -- all outside any DuckDB slot, F10) at once. Sized to `SERVING_MAX_CONCURRENT_READS`
#: for consistency with the DuckDB slot this phase feeds (review finding 5): that phase is already
#: bounded, and without this semaphore the heavier gather phase in front of it would not be.
_GATHER_SLOT: Final = asyncio.Semaphore(SERVING_MAX_CONCURRENT_READS)

#: Faults a read can raise once storage is open. None of these says anything about what the
#: release holds -- they are transport/serving state, mapped uniformly to 503 `soil_survey_read_
#: refused` (`interface/http/AGENTS.md`: "a refusal is serving/transport state, never warehouse
#: content"). `SoilSurveyError` also covers the bounded-cap and digest-mismatch refusals raised in
#: `planes.soil_survey`; plain `ValueError` covers `settings.require_object_store()` naming an
#: unset variable (F12).
_READ_FAULTS: Final = (
    SoilSurveyError,
    ValueError,
    ValidationError,
    ServingRefusalError,
    AvailabilityError,
    BotoCoreError,
    ClientError,
    duckdb.Error,
    TimeoutError,
)


def _parse(request: Request, *, point: bool) -> SoilSurveyViewport:
    """Parse and shape-validate one request; raises `ValueError`/`KeyError`/`TypeError` on a malformed one."""
    allowed = {"lon", "lat", "zoom"} if point else {"bbox", "zoom"}
    if any(key not in allowed or len(request.args.getlist(key)) != 1 for key in request.args):
        raise ValueError("unknown or repeated SSURGO query parameter")
    zoom = int(request.args.get("zoom", "13"))
    if point:
        longitude = float(request.args["lon"][0])
        latitude = float(request.args["lat"][0])
        return SoilSurveyViewport((longitude, latitude, longitude, latitude), zoom, point=(longitude, latitude))
    raw_bbox = request.args.get("bbox", "").split(",")
    if len(raw_bbox) != _BBOX_COORDINATE_COUNT:
        raise ValueError("bbox must contain west,south,east,north")
    west, south, east, north = (float(value) for value in raw_bbox)
    return SoilSurveyViewport((west, south, east, north), zoom)


async def _answer(request: Request, *, point: bool) -> HTTPResponse:
    try:
        parsed = _parse(request, point=point)
    except (KeyError, TypeError, ValueError):
        return json({"error": "soil_survey_invalid_request"}, status=400)

    # Gate order (plan §7 S3 row): region binding, then the admission pin, then zoom -- each one
    # cheaper and more storage-free than the read it would otherwise trigger.
    if not is_layer_bound(load_region(), _LAYER_SLUG):
        return json(soil_survey_unavailable(UNBOUND_REASON_NO_SOURCE, requested_zoom=parsed.requested_zoom))
    admitted = settings.ssurgo_admitted_release_sha256
    if not admitted:
        return json(soil_survey_unavailable("soil_survey_release_not_admitted", requested_zoom=parsed.requested_zoom))
    if not parsed.at_native_rung:
        return json(soil_survey_unavailable("soil_survey_zoom_in", requested_zoom=parsed.requested_zoom))

    try:
        credentials = settings.require_object_store()
        storage = BotoAvailabilityStorage.from_credentials(credentials, prefix=settings.object_store_prefix)
        async with asyncio.timeout(_READ_DEADLINE_SECONDS):
            # Object-store I/O (release index, shard manifests, part bytes) runs OFF the event
            # loop and OUTSIDE any DuckDB slot (F10); only the registration + SQL below is
            # admitted through `run_serving_read`. `_GATHER_SLOT` bounds how many requests run
            # this phase concurrently (review finding 5), matching the DuckDB slot's own bound.
            async with _GATHER_SLOT:
                release = await asyncio.to_thread(load_admitted_release, storage, admitted)
                gathered = await asyncio.to_thread(
                    gather_admitted_soil_survey_viewport, parsed, storage=storage, release=release
                )
            rows: list[dict[str, object]] = []
            if gathered.table is not None:
                gathered_table = gathered.table

                def work(session: ServingSession) -> list[dict[str, object]]:
                    return run_admitted_soil_survey_query(session.connection, gathered_table, parsed)

                rows = await run_serving_read(
                    credentials, work, prefix=settings.object_store_prefix, operation="soil-survey"
                )
            result = render_served_soil_survey(
                rows,
                release=release,
                request=parsed,
                touched_areas=gathered.touched_areas,
                admitted_sha256=admitted,
            )
        response = json(result, headers={"Cache-Control": "no-store"})
        if response.body is None or len(response.body) > _MAX_HTTP_BYTES:
            raise SoilSurveyError("SSURGO geometry response exceeds its byte budget")
    except _READ_FAULTS as error:
        logger.warning("soil_survey_read_refused", point=point, error_type=type(error).__name__)
        return json({"error": "soil_survey_read_refused"}, status=503, headers={"Cache-Control": "no-store"})
    return response


@soil_survey_bp.get("/query")
async def query_soil_survey(request: Request) -> HTTPResponse:
    """Bounded native-geometry GeoJSON for one viewport, at the admitted release's z13 rung."""
    return await _answer(request, point=False)


@soil_survey_bp.get("/point")
async def point_soil_survey(request: Request) -> HTTPResponse:
    """Every delineation whose bbox contains one exact point (see `planes` module, "ssurgo_point.sql")."""
    return await _answer(request, point=True)
