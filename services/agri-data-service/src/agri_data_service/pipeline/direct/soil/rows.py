"""Assemble one product-day into its registered base-rung Arrow table, in any of three row shapes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.soil.products import (
    ERA5_LAND_SOURCE_KEY,
    ERA5_LAND_SUPPORT_KEY,
    SOIL_DIRECT_PRECEDENCE_CONTRACT,
)

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct
    from agri_data_service.pipeline.direct.soil.source import SoilCellValue, SoilSourceReceipt

#: ERA5-Land is a public open dataset redistributed under an attribution licence and every
#: historical row of these eight streams is already exposed, so a direct row inherits the same gate.
SOIL_ALLOWED_CLIENT_EXPOSURE: Final = True

#: One archive release contributed this cell-day. The column counts republications, never readings.
SOIL_OBSERVATION_COUNT: Final = 1

#: How many physical candidates the direct fetch chose between: one, always. The historical rows
#: carry the real count of superseded PostgreSQL releases; a direct row has no release ledger.
SOIL_PHYSICAL_CANDIDATE_COUNT: Final = 1


class SoilRowError(RuntimeError):
    """Raised when a product-day cannot be assembled into its registered contract."""


def soil_day_table(
    product: SoilFieldProduct,
    *,
    day: date,
    values: tuple[SoilCellValue, ...],
    receipt: SoilSourceReceipt,
) -> pa.Table:
    """Build the base-rung table for one product-day in the row shape that product declares."""
    if not values:
        raise SoilRowError(
            f"{product.stream} {day.isoformat()} has no values; a day with none is a governed absence, "
            "and building a zero-row table would let it read as a published day"
        )
    observed_at = datetime(day.year, day.month, day.day, tzinfo=UTC)
    builders = {
        "signal_plane": _plane_row,
        "snapshot_lineage": _lineage_row,
        "soil_temperature": _lane_row,
    }
    build = builders[product.row_shape]
    rows = [build(product, day=day, observed_at=observed_at, value=value, receipt=receipt) for value in values]
    return pa.Table.from_pylist(rows, schema=product.stream_schema.arrow_schema)


def _plane_row(
    product: SoilFieldProduct,
    *,
    day: date,
    observed_at: datetime,
    value: SoilCellValue,
    receipt: SoilSourceReceipt,  # noqa: ARG001 - uniform builder shape; the plane carries no lineage
) -> dict[str, object]:
    """The frozen twelve-column signal-plane row, identical in shape to every historical plane row."""
    return {
        "support_key": ERA5_LAND_SUPPORT_KEY,
        "signal_name": product.signal_name,
        "normalized_unit": product.normalized_unit,
        "cell_id": value.cell.cell_id,
        "observed_day": day,
        "normalized_value": value.value,
        "observation_count": SOIL_OBSERVATION_COUNT,
        "newest_observed_at": observed_at,
        "coverage_fraction": value.cell.coverage_fraction,
        "allowed_client_exposure": SOIL_ALLOWED_CLIENT_EXPOSURE,
        "cell_longitude": value.cell.cell_longitude,
        "cell_latitude": value.cell.cell_latitude,
    }


def _lineage_row(
    product: SoilFieldProduct,
    *,
    day: date,
    observed_at: datetime,
    value: SoilCellValue,
    receipt: SoilSourceReceipt,
) -> dict[str, object]:
    """The thirty-three-column snapshot-breakdown row the three moisture streams are written in.

    EVERY LINEAGE COLUMN HERE IS SCOPED BY `source_snapshot_id`, which on a direct row is
    `direct:<response sha256>`. A reader that joins `selected_source_row_id` to
    `agri.signal_observation.id` without checking that discriminator is reading the wrong namespace:
    a direct row was never selected out of a canonical PostgreSQL population, so its "row" is the
    support cell it was read out of and its "part" is the chunk request that returned it. See
    `pipeline/direct/AGENTS.md`, "Direct lineage namespace".
    """
    row_sha256 = _direct_row_sha256(product, day=day, value=value)
    return {
        **_plane_row(product, day=day, observed_at=observed_at, value=value, receipt=receipt),
        "source_key": ERA5_LAND_SOURCE_KEY,
        "source_parameter": product.source_parameter,
        "source_snapshot_id": receipt.snapshot_id,
        "source_manifest_sha256": receipt.response_sha256,
        "precedence_contract": SOIL_DIRECT_PRECEDENCE_CONTRACT,
        "selected_source_row_id": value.support_ordinal,
        "selected_source_row_sha256": row_sha256,
        "selected_source_release_id": receipt.snapshot_id,
        "selected_source_release_retrieved_at": receipt.retrieved_at,
        "selected_source_release_payload_checksum": receipt.response_sha256,
        "selected_source_part_key": value.request_url,
        "selected_source_part_sha256": value.response_sha256,
        "selected_source_row_ordinal": value.support_ordinal,
        "input_source_row_count": SOIL_OBSERVATION_COUNT,
        "input_source_row_digest": row_sha256,
        "input_source_row_ids": [value.support_ordinal],
        "input_source_row_sha256s": [row_sha256],
        "input_source_release_ids": [receipt.snapshot_id],
        "input_source_part_keys": [value.request_url],
        "input_source_part_sha256s": [value.response_sha256],
        "input_source_row_ordinals": [value.support_ordinal],
    }


def _lane_row(
    product: SoilFieldProduct,
    *,
    day: date,
    observed_at: datetime,
    value: SoilCellValue,
    receipt: SoilSourceReceipt,
) -> dict[str, object]:
    """The twenty-one-column lane row the four soil-temperature streams are written in.

    A DIFFERENT LINEAGE VOCABULARY FROM `_lineage_row`, not a subset of it: this shape leads with
    `data_source_key`/`source_parameter` and names its selection `selected_observation_id` /
    `selected_canonical_row_sha256`, because it was frozen by a different snapshot breakdown
    (`scripts/soil_temperature_snapshot_breakdown.py`). Its `direct:` discriminator therefore rides
    `selected_source_release_id`, which is the only column in this shape a namespace can be read off.
    `selected_observation_id` is a SUPPORT ORDINAL here, never an `agri.signal_observation.id`.
    """
    row_sha256 = _direct_row_sha256(product, day=day, value=value)
    return {
        "data_source_key": ERA5_LAND_SOURCE_KEY,
        "source_parameter": product.source_parameter,
        **_plane_row(product, day=day, observed_at=observed_at, value=value, receipt=receipt),
        "selected_observation_id": value.support_ordinal,
        "selected_canonical_row_sha256": row_sha256,
        "selected_source_release_id": receipt.snapshot_id,
        "selected_release_retrieved_at": receipt.retrieved_at,
        "physical_candidate_count": SOIL_PHYSICAL_CANDIDATE_COUNT,
        "lineage_sha256": row_sha256,
        "input_manifest_sha256": receipt.response_sha256,
    }


def _direct_row_sha256(
    product: SoilFieldProduct,
    *,
    day: date,
    value: SoilCellValue,
) -> str:
    """Fingerprint one direct cell-day over pinned, sorted, UTC-rendered inputs so the digest reproduces."""
    payload = json.dumps(
        {
            "cell_key": value.cell.cell_key,
            "normalized_unit": product.normalized_unit,
            "normalized_value": repr(value.value),
            "observed_day": day.isoformat(),
            "response_sha256": value.response_sha256,
            "signal_name": product.signal_name,
            "source_key": ERA5_LAND_SOURCE_KEY,
            "source_parameter": product.source_parameter,
            "support_key": ERA5_LAND_SUPPORT_KEY,
            "support_ordinal": value.support_ordinal,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "SOIL_ALLOWED_CLIENT_EXPOSURE",
    "SOIL_OBSERVATION_COUNT",
    "SOIL_PHYSICAL_CANDIDATE_COUNT",
    "SoilRowError",
    "soil_day_table",
]
