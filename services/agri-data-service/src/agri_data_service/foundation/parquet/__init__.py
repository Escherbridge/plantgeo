"""Frozen object-key layout for the day-partitioned Parquet warehouse (L0).

Pure computation: no I/O, no clients, no first-party imports outside `foundation`.
"""

from agri_data_service.foundation.parquet.paths import (
    LAYER_SLUG_PATTERN,
    MAX_GAP_WINDOW_DAYS,
    MAX_PART_INDEX,
    PARQUET_SUFFIX,
    PARTITION_KINDS,
    PartitionKind,
    PartitionPath,
    PartitionPathError,
    day_prefix,
    layer_prefix,
    missing_partition_days,
    month_prefix,
    parse_partition_path,
    partition_path,
    stream_prefix,
    try_parse_partition_path,
    validate_layer_slug,
    validate_partition_kind,
    year_prefix,
)

__all__ = [
    "LAYER_SLUG_PATTERN",
    "MAX_GAP_WINDOW_DAYS",
    "MAX_PART_INDEX",
    "PARQUET_SUFFIX",
    "PARTITION_KINDS",
    "PartitionKind",
    "PartitionPath",
    "PartitionPathError",
    "day_prefix",
    "layer_prefix",
    "missing_partition_days",
    "month_prefix",
    "parse_partition_path",
    "partition_path",
    "stream_prefix",
    "try_parse_partition_path",
    "validate_layer_slug",
    "validate_partition_kind",
    "year_prefix",
]
