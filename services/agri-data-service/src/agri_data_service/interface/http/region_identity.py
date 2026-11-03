"""Actual-exchange region identity checks shared by environmental HTTP blueprints."""

from __future__ import annotations

import asyncio

from botocore.config import Config  # type: ignore[import-untyped]
from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse  # noqa: TC002 - Sanic evaluates middleware annotations at runtime.

from agri_data_service.config import settings
from agri_data_service.foundation.region.identity import definition_digest, is_custom_region
from agri_data_service.foundation.region.manifest import load_region
from agri_data_service.parquet_ops.duckdb_session import run_bounded_read
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend
from agri_data_service.pipeline.parquet.region_admission import RegionAdmissionError, guard_custom_operation

IDENTITY_HEADER = "X-PlantGeo-Region-Definition"
EXPECTED_IDENTITY_HEADER = "X-PlantGeo-Expected-Region-Definition"
_NAMESPACE_DEADLINE_SECONDS = 10


def install_region_identity(blueprint: Blueprint) -> None:
    """Guard before cached responses and attach the identity captured for this request."""

    async def check_region(request: Request) -> HTTPResponse | None:
        region = load_region()
        digest = definition_digest(region)
        request.ctx.region_definition_sha256 = digest
        expected = request.headers.get(EXPECTED_IDENTITY_HEADER)
        if expected != digest and (expected is not None or is_custom_region(region)):
            return json({"error": "region_definition_mismatch"}, status=409)
        if is_custom_region(region):
            try:

                def check() -> None:
                    backend = BotoObjectStoreBackend.from_credentials(
                        settings.require_object_store(),
                        client_config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 0}),
                    )
                    guard_custom_operation(
                        backend.client, backend.bucket, settings.object_store_prefix.rstrip("/") + "/_read"
                    )

                async with asyncio.timeout(_NAMESPACE_DEADLINE_SECONDS):
                    await run_bounded_read(check, operation="region-namespace", slot_wait_seconds=0.0)
            except ServingRefusalError:
                return json({"error": "region_namespace_at_capacity"}, status=409)
            except RegionAdmissionError as error:
                return json({"error": str(error)}, status=409)
            except (TimeoutError, ValueError):
                return json({"error": "region_namespace_unavailable"}, status=409)
        return None

    async def state_region(request: Request, response: HTTPResponse) -> None:
        digest = getattr(request.ctx, "region_definition_sha256", None)
        if digest is not None:
            response.headers[IDENTITY_HEADER] = digest

    blueprint.middleware(check_region, "request")
    blueprint.middleware(state_region, "response")
