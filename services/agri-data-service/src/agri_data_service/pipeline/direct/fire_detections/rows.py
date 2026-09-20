"""Conform complete FIRMS day responses to the registered cell-day schema."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import ROUND_FLOOR, Decimal

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.firms import build_fire_detection_write
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_SCHEMA, FIRE_DETECTIONS_STREAM

from .models import FireCellTotals, FireDaySource
from .products import CELL_SIZE, POINT_COORDINATE_COUNT


def fire_table_from_features(  # noqa: PLR0913
    features: Sequence[Mapping[str, object]],
    *,
    day: date,
    fetched_at: datetime,
    max_records: int,
    raw_record_count: int | None = None,
    source_products: tuple[str, ...] = (),
    product_counts: Mapping[str, int] | None = None,
) -> FireDaySource:
    """Conform one complete source day to the registered 0.005-degree cell-day schema."""
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise PipelineOperationError(
            "the source fetch clock must include a timezone", lane="fire-detections", stage="rows"
        )
    actual_raw_records = len(features) if raw_record_count is None else raw_record_count
    if actual_raw_records > max_records:
        raise PipelineOperationError(
            f"FIRMS returned {actual_raw_records} records for {day}, over the fail-closed {max_records}-record day cap",
            lane="fire-detections",
            stage="rows",
        )
    writes = {}
    rejected = 0
    for feature in features:
        write = build_fire_detection_write(feature, FIRE_DETECTIONS_STREAM, None, fetched_at)
        if write is None:
            rejected += 1
            continue
        observed_at = write.identity.observed_at
        if observed_at is None or observed_at.astimezone(UTC).date() != day:
            raise PipelineOperationError(
                f"the exact-day FIRMS response for {day} contained detection {write.external_id!r} from {observed_at}",
                lane="fire-detections",
                stage="rows",
            )
        writes[write.external_id] = write
    if rejected:
        raise PipelineOperationError(
            f"FIRMS returned {rejected} unkeyable or invalid records for {day}; refusing a partial day",
            lane="fire-detections",
            stage="rows",
        )
    cells: dict[tuple[Decimal, Decimal], FireCellTotals] = {}
    for write in writes.values():
        geometry = write.properties.get("geometry")
        coordinates = geometry.get("coordinates") if isinstance(geometry, Mapping) else None
        if not isinstance(coordinates, list) or len(coordinates) < POINT_COORDINATE_COUNT:
            raise PipelineOperationError(
                f"FIRMS detection {write.external_id!r} has no point coordinates", lane="fire-detections", stage="rows"
            )
        longitude = _finite_decimal(coordinates[0], field="longitude", external_id=write.external_id)
        latitude = _finite_decimal(coordinates[1], field="latitude", external_id=write.external_id)
        cell = (_floor_cell(longitude), _floor_cell(latitude))
        candidate_observed_at = write.identity.observed_at
        if candidate_observed_at is None:
            raise PipelineOperationError(
                f"validated FIRMS detection {write.external_id!r} lost its observation timestamp",
                lane="fire-detections",
                stage="rows",
            )
        held = cells.setdefault(cell, FireCellTotals(newest_observed_at=candidate_observed_at))
        held.detection_count += 1
        frp = write.properties.get("frp")
        if isinstance(frp, int | float) and not isinstance(frp, bool):
            held.frp_sum += _finite_decimal(frp, field="frp", external_id=write.external_id)
            held.frp_observation_count += 1
        if write.properties.get("confidenceNormalized") == "high":
            held.high_confidence_detection_count += 1
        if held.newest_observed_at is None or candidate_observed_at > held.newest_observed_at:
            held.newest_observed_at = candidate_observed_at
    rows = [
        {
            "cell_longitude": float(longitude),
            "cell_latitude": float(latitude),
            "observed_day": day,
            "detection_count": aggregate.detection_count,
            "frp_sum": float(aggregate.frp_sum) if aggregate.frp_observation_count else None,
            "frp_observation_count": aggregate.frp_observation_count,
            "high_confidence_detection_count": aggregate.high_confidence_detection_count,
            "newest_observed_at": aggregate.newest_observed_at,
        }
        for (longitude, latitude), aggregate in sorted(cells.items())
    ]
    return FireDaySource(
        day=day,
        raw_records=actual_raw_records,
        deduplicated_records=len(writes),
        source_products=source_products,
        product_counts={} if product_counts is None else dict(product_counts),
        table=pa.Table.from_pylist(rows, schema=FIRE_DETECTIONS_SCHEMA.arrow_schema),
    )


def _floor_cell(value: Decimal) -> Decimal:
    return (value / CELL_SIZE).to_integral_value(rounding=ROUND_FLOOR) * CELL_SIZE


def _finite_decimal(value: object, *, field: str, external_id: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise PipelineOperationError(
            f"FIRMS detection {external_id!r} has an invalid {field}: {value!r}", lane="fire-detections", stage="rows"
        ) from error
    if not parsed.is_finite():
        raise PipelineOperationError(
            f"FIRMS detection {external_id!r} has a non-finite {field}: {value!r}", lane="fire-detections", stage="rows"
        )
    return parsed
