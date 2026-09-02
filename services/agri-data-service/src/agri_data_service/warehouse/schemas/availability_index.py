"""Canonical Arrow schema for immutable lane availability generations."""

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS

AVAILABILITY_SCHEMA_VERSION: Final = "1"
AVAILABILITY_REQUIRED_RUNGS: Final = ZOOM_TIERS

_DATA_RECEIPT_TYPE: Final = pa.struct(
    [
        pa.field("key", pa.string(), nullable=False),
        pa.field("sha256", pa.string(), nullable=False),
    ]
)

AVAILABILITY_INDEX_SCHEMA: Final = pa.schema(
    [
        pa.field("lane", pa.string(), nullable=False),
        pa.field("product", pa.string(), nullable=False),
        pa.field("nature", pa.string(), nullable=False),
        pa.field("day", pa.date32(), nullable=False),
        pa.field("rung", pa.int16(), nullable=False),
        pa.field("terminal_state", pa.string(), nullable=False),
        pa.field("row_count", pa.int64(), nullable=False),
        pa.field("source_receipt_key", pa.string(), nullable=False),
        pa.field("source_receipt_sha256", pa.string(), nullable=False),
        pa.field("terminal_receipt_key", pa.string(), nullable=False),
        pa.field("terminal_receipt_sha256", pa.string(), nullable=False),
        pa.field(
            "data_receipts",
            pa.list_(pa.field("item", _DATA_RECEIPT_TYPE, nullable=False)),
            nullable=False,
        ),
        pa.field("completion_receipt_key", pa.string(), nullable=True),
        pa.field("completion_receipt_sha256", pa.string(), nullable=True),
        pa.field("absence_reason", pa.string(), nullable=True),
        pa.field("source_ceiling", pa.date32(), nullable=False),
        pa.field("published_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

AVAILABILITY_METADATA_KEYS: Final = frozenset(
    {
        b"availability.bootstrap_receipt_key",
        b"availability.bootstrap_receipt_sha256",
        b"availability.created_at",
        b"availability.earliest_terminal_day",
        b"availability.generation_receipt_sha256",
        b"availability.lane",
        b"availability.lane_root",
        b"availability.latest_terminal_day",
        b"availability.nature",
        b"availability.prior_generation_key",
        b"availability.prior_generation_sha256",
        b"availability.product",
        b"availability.required_rungs",
        b"availability.row_count",
        b"availability.schema_version",
        b"availability.source_ceiling",
        b"availability.verified_source_inventory_root",
    }
)
