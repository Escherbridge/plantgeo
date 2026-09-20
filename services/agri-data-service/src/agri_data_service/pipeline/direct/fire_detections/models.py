"""Immutable request and source-result models for fire detections."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from .products import FIRE_DETECTIONS_DIRECT_WRITER_START_DAY

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime

    import pyarrow as pa  # type: ignore[import-untyped]


@dataclass(frozen=True, slots=True)
class FireForwardConfig:
    """Bound every source request, run, retry series, and contention wait."""

    bbox: str
    lookback_days: int
    max_days: int
    max_records_per_day: int
    retry_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    contention_timeout_seconds: float
    forward_start_day: date = FIRE_DETECTIONS_DIRECT_WRITER_START_DAY
    force_day: date | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class FireDaySource:
    """The complete, deduplicated FIRMS answer used to author one UTC day."""

    day: date
    raw_records: int
    deduplicated_records: int
    source_products: tuple[str, ...]
    product_counts: Mapping[str, int]
    table: pa.Table


@dataclass(slots=True)
class FireCellTotals:
    """Typed additive values for one 0.005-degree fire cell."""

    newest_observed_at: datetime | None
    detection_count: int = 0
    frp_sum: Decimal = Decimal("0")
    frp_observation_count: int = 0
    high_confidence_detection_count: int = 0
