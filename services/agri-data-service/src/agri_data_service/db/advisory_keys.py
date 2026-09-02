"""Stable PostgreSQL advisory-lock identities shared across operational layers."""

from __future__ import annotations

import re
from datetime import date
from typing import Final

from agri_data_service.foundation.parquet.paths import validate_layer_slug, validate_partition_kind

VEGETATION_PUBLICATION_BARRIER_KEY: Final = "vegetation-governed-publication-v1"
_PARQUET_LANE_PUBLICATION_PREFIX: Final = "parquet-lane-publication"
_PARQUET_DAY_LOCK_PATTERN: Final = re.compile(
    r"^parquet-gap-fill:(?P<layer>[a-z0-9]+(?:-[a-z0-9]+)*):"
    r"(?P<kind>observed|forecast):z13:(?P<day>\d{4}-\d{2}-\d{2})$"
)


def parquet_lane_publication_barrier_key(layer: str, kind: str) -> str:
    """Return the frozen shared/exclusive publication-barrier identity for one lane."""
    validated_layer = validate_layer_slug(layer)
    validated_kind = validate_partition_kind(kind)
    return f"{_PARQUET_LANE_PUBLICATION_PREFIX}:{validated_layer}:{validated_kind}:v1"


def parquet_lane_publication_barrier_from_day_lock_key(day_lock_key: str) -> str:
    """Derive the lane barrier from the frozen historical lane-day lock spelling."""
    matched = _PARQUET_DAY_LOCK_PATTERN.fullmatch(day_lock_key)
    if matched is None:
        raise ValueError("lane-day lock key does not match the frozen Parquet writer contract")
    try:
        parsed_day = date.fromisoformat(matched.group("day"))
    except ValueError as exc:
        raise ValueError("lane-day lock key contains an invalid ISO day") from exc
    if parsed_day.isoformat() != matched.group("day"):
        raise ValueError("lane-day lock key contains a non-canonical ISO day")
    return parquet_lane_publication_barrier_key(matched.group("layer"), matched.group("kind"))


__all__ = [
    "VEGETATION_PUBLICATION_BARRIER_KEY",
    "parquet_lane_publication_barrier_from_day_lock_key",
    "parquet_lane_publication_barrier_key",
]
