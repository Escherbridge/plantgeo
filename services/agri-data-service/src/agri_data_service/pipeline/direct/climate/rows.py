"""Assemble one product-day into its registered base-rung Arrow table, in either declared row shape."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_DIRECT_PRECEDENCE_CONTRACT,
    NASA_POWER_SOURCE_KEY,
    NASA_POWER_SUPPORT_KEY,
)

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.climate.source import ClimateCellValue, ClimateSourceReceipt

#: NASA POWER is a public open dataset and every historical climate-field row is already exposed, so
#: a direct row inherits the same gate rather than inventing a narrower one.
CLIMATE_ALLOWED_CLIENT_EXPOSURE: Final = True

#: One POWER release contributed this cell-day. The column counts republications, never readings --
#: `sql/pipeline/signal_plane_day_export.sql` says so explicitly, and a direct fetch has exactly one.
CLIMATE_OBSERVATION_COUNT: Final = 1


class ClimateRowError(RuntimeError):
    """Raised when a product-day cannot be assembled into its registered contract."""


def climate_day_table(
    product: ClimateFieldProduct,
    *,
    day: date,
    values: tuple[ClimateCellValue, ...],
    receipt: ClimateSourceReceipt,
) -> pa.Table:
    """Build the base-rung table for one product-day in the row shape that product declares."""
    if not values:
        raise ClimateRowError(
            f"{product.stream} {day.isoformat()} has no values; a day with none is a governed absence, "
            "and building a zero-row table would let it read as a published day"
        )
    observed_at = datetime(day.year, day.month, day.day, tzinfo=UTC)
    builders = {
        "signal_plane": _plane_row,
        "snapshot_lineage": _lineage_row,
        "snapshot_lane": _lane_row,
    }
    build = builders[product.row_shape]
    rows = [build(product, day=day, observed_at=observed_at, value=value, receipt=receipt) for value in values]
    return pa.Table.from_pylist(rows, schema=product.stream_schema.arrow_schema)


def _plane_row(
    product: ClimateFieldProduct,
    *,
    day: date,
    observed_at: datetime,
    value: ClimateCellValue,
    receipt: ClimateSourceReceipt,  # noqa: ARG001 - uniform builder shape; the plane carries no lineage
) -> dict[str, object]:
    """The frozen twelve-column signal-plane row, identical in shape to every historical plane row."""
    return {
        "support_key": NASA_POWER_SUPPORT_KEY,
        "signal_name": product.signal_name,
        "normalized_unit": product.normalized_unit,
        "cell_id": value.cell.cell_id,
        "observed_day": day,
        "normalized_value": value.value,
        "observation_count": CLIMATE_OBSERVATION_COUNT,
        "newest_observed_at": observed_at,
        "coverage_fraction": value.cell.coverage_fraction,
        "allowed_client_exposure": CLIMATE_ALLOWED_CLIENT_EXPOSURE,
        "cell_longitude": value.cell.cell_longitude,
        "cell_latitude": value.cell.cell_latitude,
    }


def _lineage_row(
    product: ClimateFieldProduct,
    *,
    day: date,
    observed_at: datetime,
    value: ClimateCellValue,
    receipt: ClimateSourceReceipt,
) -> dict[str, object]:
    """The thirty-three-column snapshot-breakdown row, with every lineage column scoped to one response.

    EVERY LINEAGE COLUMN HERE IS SCOPED BY `source_snapshot_id`, which on a direct row is
    `direct:<response sha256>`. A reader that joins `selected_source_row_id` to
    `agri.signal_observation.id` without checking that discriminator is reading the wrong namespace:
    a direct row was never selected out of a canonical PostgreSQL population, so its "row" is the
    feature it was read out of and its "part" is the request that returned it. See
    `pipeline/direct/AGENTS.md`, "Direct lineage namespace".
    """
    row_sha256 = _direct_row_sha256(product, day=day, value=value)
    return {
        **_plane_row(product, day=day, observed_at=observed_at, value=value, receipt=receipt),
        "source_key": NASA_POWER_SOURCE_KEY,
        "source_parameter": product.source_parameter,
        "source_snapshot_id": receipt.snapshot_id,
        "source_manifest_sha256": receipt.response_sha256,
        "precedence_contract": CLIMATE_DIRECT_PRECEDENCE_CONTRACT,
        "selected_source_row_id": value.response_ordinal,
        "selected_source_row_sha256": row_sha256,
        "selected_source_release_id": receipt.snapshot_id,
        "selected_source_release_retrieved_at": receipt.retrieved_at,
        "selected_source_release_payload_checksum": receipt.response_sha256,
        "selected_source_part_key": value.request_url,
        "selected_source_part_sha256": value.response_sha256,
        "selected_source_row_ordinal": value.response_ordinal,
        "input_source_row_count": CLIMATE_OBSERVATION_COUNT,
        "input_source_row_digest": row_sha256,
        "input_source_row_ids": [value.response_ordinal],
        "input_source_row_sha256s": [row_sha256],
        "input_source_release_ids": [receipt.snapshot_id],
        "input_source_part_keys": [value.request_url],
        "input_source_part_sha256s": [value.response_sha256],
        "input_source_row_ordinals": [value.response_ordinal],
    }


def _lane_row(
    product: ClimateFieldProduct,
    *,
    day: date,
    observed_at: datetime,
    value: ClimateCellValue,
    receipt: ClimateSourceReceipt,
) -> dict[str, object]:
    """The nineteen-column lane row the three soil-wetness streams are written in.

    A DIFFERENT LINEAGE VOCABULARY FROM `_lineage_row`, not a subset of it: this shape was frozen by
    `scripts/soil_wetness_snapshot_breakdown.py` LANE_SCHEMA, which names its selection
    `selected_observation_id` / `selected_canonical_row_sha256` and carries no `source_snapshot_id`
    at all. The `direct:` discriminator therefore rides `selected_source_release_id`, which is the
    only column in this shape a namespace can be read off, and `selected_observation_id` is a
    RESPONSE ORDINAL here, never an `agri.signal_observation.id`.
    """
    row_sha256 = _direct_row_sha256(product, day=day, value=value)
    return {
        **_plane_row(product, day=day, observed_at=observed_at, value=value, receipt=receipt),
        "selected_observation_id": value.response_ordinal,
        "selected_canonical_row_sha256": row_sha256,
        "selected_source_release_id": receipt.snapshot_id,
        "selected_release_retrieved_at": receipt.retrieved_at,
        "physical_candidate_count": CLIMATE_OBSERVATION_COUNT,
        "lineage_sha256": row_sha256,
        "input_manifest_sha256": receipt.response_sha256,
    }


def _direct_row_sha256(
    product: ClimateFieldProduct,
    *,
    day: date,
    value: ClimateCellValue,
) -> str:
    """Fingerprint one direct cell-day over pinned, sorted, UTC-rendered inputs so the digest reproduces."""
    payload = json.dumps(
        {
            "cell_key": value.cell.cell_key,
            "normalized_unit": product.normalized_unit,
            "observed_day": day.isoformat(),
            "normalized_value": repr(value.value),
            "response_ordinal": value.response_ordinal,
            "response_sha256": value.response_sha256,
            "signal_name": product.signal_name,
            "source_key": NASA_POWER_SOURCE_KEY,
            "source_parameter": product.source_parameter,
            "support_key": NASA_POWER_SUPPORT_KEY,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "CLIMATE_ALLOWED_CLIENT_EXPOSURE",
    "CLIMATE_OBSERVATION_COUNT",
    "ClimateRowError",
    "climate_day_table",
]
