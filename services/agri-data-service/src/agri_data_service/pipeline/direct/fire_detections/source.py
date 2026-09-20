"""Retrieve one complete, settled FIRMS constellation day."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from agri_data_service.ingest.firms import (
    FIRMS_BOUNDS,
    collapse_history_records,
    fetch_active_fires,
    fetch_product_availability,
    products_covering_span,
)
from agri_data_service.ingest.http import upstream_client
from agri_data_service.pipeline.errors import PipelineOperationError

from .rows import fire_table_from_features
from .support import retry_async

if TYPE_CHECKING:
    from .models import FireDaySource


async def fetch_fire_day(  # noqa: PLR0913
    *, day: date, bbox: str, max_records: int, retry_attempts: int, retry_base_seconds: float, retry_max_seconds: float
) -> FireDaySource:
    """Fetch every applicable FIRMS product for one exact settled day as one retry unit."""

    async def fetch_once() -> FireDaySource:
        async with upstream_client(FIRMS_BOUNDS) as client:
            availability = await fetch_product_availability(client)
            products = products_covering_span(availability, day, 1)
            if not products:
                raise PipelineOperationError(
                    f"FIRMS availability lists no product covering settled day {day}",
                    lane="fire-detections",
                    stage="source",
                )
            answers = await asyncio.gather(
                *(fetch_active_fires(client, bbox, 1, product, day) for product in products), return_exceptions=True
            )
        failures: list[str] = []
        successful: list[tuple[str, list[dict[str, object]]]] = []
        for product, answer in zip(products, answers, strict=True):
            if isinstance(answer, BaseException):
                failures.append(f"{product}: {type(answer).__name__}")
            else:
                successful.append((product, answer))
        if failures:
            raise PipelineOperationError(
                f"FIRMS did not return the complete constellation for {day}: {'; '.join(failures)}",
                lane="fire-detections",
                stage="source",
            )
        product_counts = {product: len(answer) for product, answer in successful}
        raw_features = [feature for _product, answer in successful for feature in answer]
        return fire_table_from_features(
            collapse_history_records(raw_features),
            day=day,
            fetched_at=datetime.now(UTC),
            max_records=max_records,
            raw_record_count=len(raw_features),
            source_products=tuple(products),
            product_counts=product_counts,
        )

    return await retry_async(
        f"FIRMS {day.isoformat()} source fetch",
        fetch_once,
        attempts=retry_attempts,
        base_seconds=retry_base_seconds,
        max_seconds=retry_max_seconds,
    )
