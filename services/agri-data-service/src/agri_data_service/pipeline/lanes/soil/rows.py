"""One soil product-day as its registered base-rung table, in the three historical row shapes.

Byte-for-byte restatement of `pipeline/direct/soil/rows.py` (not importable from a strategy); the
lineage namespace notes live in `pipeline/direct/AGENTS.md` "Direct lineage namespace".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.lanes._shared.digest import COMPACT_SEPARATORS, canonical_json_bytes, sha256_hex
from agri_data_service.warehouse.field_products.soil import (
    ERA5_LAND_SOURCE_KEY,
    ERA5_LAND_SUPPORT_KEY,
    SOIL_DIRECT_PRECEDENCE_CONTRACT,
    SOIL_DIRECT_SNAPSHOT_PREFIX,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.pipeline.lanes.soil.support import SupportCellIdentity
    from agri_data_service.warehouse.field_products.soil import SoilFieldProduct

#: `direct/soil/rows.py` SOIL_ALLOWED_CLIENT_EXPOSURE: ERA5-Land is redistributed under attribution.
ALLOWED_CLIENT_EXPOSURE: Final = True
#: `direct/soil/rows.py` SOIL_OBSERVATION_COUNT: one archive release per cell-day.
OBSERVATION_COUNT: Final = 1
#: `direct/soil/rows.py` SOIL_PHYSICAL_CANDIDATE_COUNT: a direct fetch chooses between one candidate.
PHYSICAL_CANDIDATE_COUNT: Final = 1


@dataclass(frozen=True, slots=True)
class CellValue:
    """One cell's value for one product-day, and the part (chunk request) it was read out of."""

    cell: SupportCellIdentity
    value: float
    part_key: str
    part_sha256: str


@dataclass(frozen=True, slots=True)
class DayReceipt:
    """The day's source identity: the `direct:` snapshot digest and the newest chunk retrieval instant."""

    response_sha256: str
    retrieved_at: datetime

    @property
    def snapshot_id(self) -> str:
        """The `direct:` token every lineage column is scoped by."""
        return f"{SOIL_DIRECT_SNAPSHOT_PREFIX}{self.response_sha256}"


def product_day_table(
    product: SoilFieldProduct, *, day: date, values: Sequence[CellValue], receipt: DayReceipt
) -> pa.Table:
    """The base-rung table for one product-day in the row shape the product declares, in support order."""
    observed_at = datetime(day.year, day.month, day.day, tzinfo=UTC)
    builders = {"signal_plane": _plane_row, "snapshot_lineage": _lineage_row, "soil_temperature": _lane_row}
    build = builders[product.row_shape]
    ordered = sorted(values, key=lambda value: value.cell.support_ordinal)
    rows = [build(product, day=day, observed_at=observed_at, value=value, receipt=receipt) for value in ordered]
    return pa.Table.from_pylist(rows, schema=product.stream_schema.arrow_schema)


def _plane_row(
    product: SoilFieldProduct,
    *,
    day: date,
    observed_at: datetime,
    value: CellValue,
    receipt: DayReceipt,  # noqa: ARG001 - uniform builder shape; the plane carries no lineage
) -> dict[str, object]:
    """The twelve-column signal-plane row (`direct/soil/rows.py::_plane_row`)."""
    return {
        "support_key": ERA5_LAND_SUPPORT_KEY,
        "signal_name": product.signal_name,
        "normalized_unit": product.normalized_unit,
        "cell_id": value.cell.cell_id,
        "observed_day": day,
        "normalized_value": value.value,
        "observation_count": OBSERVATION_COUNT,
        "newest_observed_at": observed_at,
        "coverage_fraction": value.cell.coverage_fraction,
        "allowed_client_exposure": ALLOWED_CLIENT_EXPOSURE,
        "cell_longitude": value.cell.cell_longitude,
        "cell_latitude": value.cell.cell_latitude,
    }


def _lineage_row(
    product: SoilFieldProduct, *, day: date, observed_at: datetime, value: CellValue, receipt: DayReceipt
) -> dict[str, object]:
    """The thirty-three-column moisture row (`direct/soil/rows.py::_lineage_row`); `*_retrieved_at` is volatile."""
    row_sha256 = _direct_row_sha256(product, day=day, value=value)
    ordinal = value.cell.support_ordinal
    return {
        **_plane_row(product, day=day, observed_at=observed_at, value=value, receipt=receipt),
        "source_key": ERA5_LAND_SOURCE_KEY,
        "source_parameter": product.source_parameter,
        "source_snapshot_id": receipt.snapshot_id,
        "source_manifest_sha256": receipt.response_sha256,
        "precedence_contract": SOIL_DIRECT_PRECEDENCE_CONTRACT,
        "selected_source_row_id": ordinal,
        "selected_source_row_sha256": row_sha256,
        "selected_source_release_id": receipt.snapshot_id,
        "selected_source_release_retrieved_at": receipt.retrieved_at,
        "selected_source_release_payload_checksum": receipt.response_sha256,
        "selected_source_part_key": value.part_key,
        "selected_source_part_sha256": value.part_sha256,
        "selected_source_row_ordinal": ordinal,
        "input_source_row_count": OBSERVATION_COUNT,
        "input_source_row_digest": row_sha256,
        "input_source_row_ids": [ordinal],
        "input_source_row_sha256s": [row_sha256],
        "input_source_release_ids": [receipt.snapshot_id],
        "input_source_part_keys": [value.part_key],
        "input_source_part_sha256s": [value.part_sha256],
        "input_source_row_ordinals": [ordinal],
    }


def _lane_row(
    product: SoilFieldProduct, *, day: date, observed_at: datetime, value: CellValue, receipt: DayReceipt
) -> dict[str, object]:
    """The twenty-one-column temperature row (`direct/soil/rows.py::_lane_row`); `*_retrieved_at` is volatile."""
    row_sha256 = _direct_row_sha256(product, day=day, value=value)
    return {
        "data_source_key": ERA5_LAND_SOURCE_KEY,
        "source_parameter": product.source_parameter,
        **_plane_row(product, day=day, observed_at=observed_at, value=value, receipt=receipt),
        "selected_observation_id": value.cell.support_ordinal,
        "selected_canonical_row_sha256": row_sha256,
        "selected_source_release_id": receipt.snapshot_id,
        "selected_release_retrieved_at": receipt.retrieved_at,
        "physical_candidate_count": PHYSICAL_CANDIDATE_COUNT,
        "lineage_sha256": row_sha256,
        "input_manifest_sha256": receipt.response_sha256,
    }


def _direct_row_sha256(product: SoilFieldProduct, *, day: date, value: CellValue) -> str:
    """The cell-day fingerprint over pinned inputs (`direct/soil/rows.py::_direct_row_sha256`, same kwargs)."""
    payload = {
        "cell_key": value.cell.cell_key,
        "normalized_unit": product.normalized_unit,
        "normalized_value": repr(value.value),
        "observed_day": day.isoformat(),
        "response_sha256": value.part_sha256,
        "signal_name": product.signal_name,
        "source_key": ERA5_LAND_SOURCE_KEY,
        "source_parameter": product.source_parameter,
        "support_key": ERA5_LAND_SUPPORT_KEY,
        "support_ordinal": value.cell.support_ordinal,
    }
    return sha256_hex(canonical_json_bytes(payload, sort_keys=True, separators=COMPACT_SEPARATORS, allow_nan=True))


__all__ = [
    "ALLOWED_CLIENT_EXPOSURE",
    "OBSERVATION_COUNT",
    "PHYSICAL_CANDIDATE_COUNT",
    "CellValue",
    "DayReceipt",
    "product_day_table",
]
