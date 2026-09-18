"""The frozen snapshot products: their roots, columns, pinned Arrow schemas and object-key grammar.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, Literal

import pyarrow as pa  # type: ignore[import-untyped]
import structlog

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import faults
from agri_data_service.pipeline.direct.climate.products import CLIMATE_DIRECT_WRITER_START_DAY
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_SCHEMA, get_stream_schema
from agri_data_service.warehouse.parquet.snapshot_signal_product import SOIL_TEMPERATURE_FIELDS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.parquet_ops.duckdb_session import ServingSession


logger = structlog.get_logger()

SNAPSHOT_ID: Final = "prod-20260826-full-signal-v1"
MAX_SNAPSHOT_KEYS: Final = 30_000
MAX_MANIFEST_BYTES: Final = 16_000_000
SNAPSHOT_ROW_BUDGET: Final = 20_000
SNAPSHOT_COVERAGE_CACHE_SECONDS: Final = 120
MAX_SNAPSHOT_READ_PARTS: Final = 32
MAX_MONTHLY_RECEIPT_OBJECTS: Final = 4_096
METADATA_VERIFY_WORKERS: Final = 16
SNAPSHOT_COVERAGE_PRODUCT_WORKERS: Final = 4
MAX_EVIDENCE_CACHE_ENTRIES: Final = 64
BASE_ZOOM_TIER: Final = ZOOM_TIERS[-1]
#: The one stream a snapshot product publishes, and therefore the one its forward edge is listed
#: under. `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`.
FORWARD_PARTITION_KIND: Final = "observed"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DAILY_PART = re.compile(
    r"/kind=observed/zoom=(?P<zoom>00|05|09|13)/year=(?P<year>\d{4})/month=(?P<month>\d{2})/"
    r"day=(?P<day>\d{2})/part-(?:\d+|\d{5})\.parquet$"
)
_MONTHLY_PART = re.compile(
    r"/kind=observed/zoom=(?P<zoom>00|05|09|13)/year=(?P<year>\d{4})/month=(?P<month>\d{2})/"
    r"part-(?:\d+|\d{5})\.parquet$"
)
_PRODUCT_CHECKPOINT = re.compile(r"/_checkpoints/year=(?P<year>\d{4})/month=(?P<month>\d{2})\.json$")
_LANE_BASE_CHECKPOINT = re.compile(r"/_checkpoints/base/year=(?P<year>\d{4})/month=(?P<month>\d{2})\.json$")
_LANE_TIER_CHECKPOINT = re.compile(r"/_checkpoints/tiers/year=(?P<year>\d{4})/month=(?P<month>\d{2})\.json$")
_VERIFICATION_MARKER = re.compile(
    r"/_verification/phase=(?P<phase>base|tiers)/year=(?P<year>\d{4})/month=(?P<month>\d{2})\.json$"
)

type SnapshotLayout = Literal["daily", "monthly"]


@dataclass(frozen=True, slots=True)
class SnapshotProduct:
    """One immutable product the private plane is allowed to expose."""

    layer: str
    layout: SnapshotLayout
    data_root: str
    metadata_root: str
    snapshot_id: str = SNAPSHOT_ID
    expected_manifest_sha256: str | None = None
    schema_layer: str | None = None
    schema_columns: tuple[str, ...] | None = None
    contract_version: str | None = None
    coverage_cell_grid_name: str | None = None
    coverage_cells_per_day: int | None = None
    #: First day a LIVE writer owns, beyond the closed snapshot's reach. `None` means the product is
    #: frozen end to end and every one of its days is proven by the manifest.
    forward_first_day: date | None = None


def _layer_root(layer: str) -> str:
    return f"layer={layer}/snapshot={SNAPSHOT_ID}"


def _derived_lane_root(layer: str) -> str:
    return f"derived-canonical/signal-observation/lane={layer}/snapshot={SNAPSHOT_ID}"


SIGNAL_PRODUCT_COLUMNS: Final = (
    "support_key",
    "signal_name",
    "normalized_unit",
    "cell_id",
    "observed_day",
    "normalized_value",
    "observation_count",
    "newest_observed_at",
    "coverage_fraction",
    "allowed_client_exposure",
    "cell_longitude",
    "cell_latitude",
)

SOIL_WETNESS_COLUMNS: Final = (
    *SIGNAL_PRODUCT_COLUMNS,
    "selected_observation_id",
    "selected_canonical_row_sha256",
    "selected_source_release_id",
    "selected_release_retrieved_at",
    "physical_candidate_count",
    "lineage_sha256",
    "input_manifest_sha256",
)

SOIL_TEMPERATURE_COLUMNS: Final = (
    "data_source_key",
    "source_parameter",
    *SOIL_WETNESS_COLUMNS,
)

_PINNED_ARROW_SCHEMAS: Final[dict[tuple[str, ...], pa.Schema]] = {
    SIGNAL_PRODUCT_COLUMNS: SIGNAL_PLANE_SCHEMA.arrow_schema,
    SOIL_WETNESS_COLUMNS: pa.schema(SOIL_TEMPERATURE_FIELDS[2:]),
    SOIL_TEMPERATURE_COLUMNS: pa.schema(SOIL_TEMPERATURE_FIELDS),
}


#: Frozen provenance descriptors; completed serving-cutover history is recorded in AGENTS.md.
FROZEN_SNAPSHOT_PRODUCTS: Final[tuple[SnapshotProduct, ...]] = (
    SnapshotProduct(
        "climate-field-air-temperature-mean",
        "monthly",
        _layer_root("climate-field-air-temperature-mean"),
        _layer_root("climate-field-air-temperature-mean"),
        schema_columns=SIGNAL_PRODUCT_COLUMNS,
        contract_version="plantgeo.air-temperature.snapshot-product.v1",
        coverage_cell_grid_name="nasa-power-0.5-degree",
        coverage_cells_per_day=397,
        forward_first_day=CLIMATE_DIRECT_WRITER_START_DAY,
    ),
    SnapshotProduct(
        "climate-field-air-temperature-max",
        "monthly",
        _layer_root("climate-field-air-temperature-max"),
        _layer_root("climate-field-air-temperature-max"),
        schema_columns=SIGNAL_PRODUCT_COLUMNS,
        contract_version="plantgeo.air-temperature.snapshot-product.v1",
        coverage_cell_grid_name="nasa-power-0.5-degree",
        coverage_cells_per_day=397,
        forward_first_day=CLIMATE_DIRECT_WRITER_START_DAY,
    ),
    SnapshotProduct(
        "climate-field-air-temperature-min",
        "monthly",
        _layer_root("climate-field-air-temperature-min"),
        _layer_root("climate-field-air-temperature-min"),
        schema_columns=SIGNAL_PRODUCT_COLUMNS,
        contract_version="plantgeo.air-temperature.snapshot-product.v1",
        coverage_cell_grid_name="nasa-power-0.5-degree",
        coverage_cells_per_day=397,
        forward_first_day=CLIMATE_DIRECT_WRITER_START_DAY,
    ),
)

# Serving graduation and frozen provenance are separate; see AGENTS.md, "Air-temperature graduation".
SNAPSHOT_PRODUCTS: Final[tuple[SnapshotProduct, ...]] = ()
PRODUCT_BY_LAYER: Final[dict[str, SnapshotProduct]] = {product.layer: product for product in SNAPSHOT_PRODUCTS}


def product_for_layer(layer: str) -> SnapshotProduct:
    """Resolve only a server-side allowlisted layer; callers never select an arbitrary prefix."""
    product = PRODUCT_BY_LAYER.get(layer)
    if product is None:
        raise faults.snapshot_unpublished(layer=layer, snapshot_id=SNAPSHOT_ID, detail="layer is not allowlisted")
    return product


def serves_from_snapshot(layer: str, day: date) -> bool:
    """Decide which of the two paths owns ONE requested day of ONE layer.

    Every route adapter asks this instead of testing `layer in PRODUCT_BY_LAYER`, because that test
    is day-BLIND: it sent a day the direct writer owns to a manifest that has never heard of it, and
    the frozen product answered `day_not_written` for a day sitting in the bucket.
    """
    product = PRODUCT_BY_LAYER.get(layer)
    if product is None:
        return False
    return product.forward_first_day is None or day < product.forward_first_day


def _daily_days(keys: Sequence[str], *, product: SnapshotProduct) -> set[date]:
    days: set[date] = set()
    for key in keys:
        parsed = _daily_part_day(key, product=product)
        if parsed is not None:
            days.add(parsed)
    return days


def _daily_parts(keys: Sequence[str], *, day: date, product: SnapshotProduct) -> tuple[str, ...]:
    selected: list[str] = []
    for key in keys:
        parsed = _daily_part_day(key, product=product)
        if parsed == day:
            selected.append(key)
    return tuple(sorted(selected))


def _daily_part_day(key: str, *, product: SnapshotProduct) -> date | None:
    matched = _DAILY_PART.search(key)
    if matched is None:
        return None
    try:
        return date(int(matched.group("year")), int(matched.group("month")), int(matched.group("day")))
    except ValueError as exc:
        raise faults.snapshot_schema_mismatch(
            layer=product.layer,
            key=key,
            detail="manifest-bound daily key contains an impossible calendar day",
        ) from exc


def _parts_for_month(keys: Sequence[str], month: date) -> tuple[str, ...]:
    token = f"/year={month.year:04d}/month={month.month:02d}/"
    return tuple(sorted(key for key in keys if token in key))


def _month_has_day(session: ServingSession, keys: Sequence[str], *, day: date) -> bool:
    uris = [session.object_uri(key) for key in keys]
    row = session.connection.execute(
        "SELECT 1 FROM read_parquet(?, hive_partitioning=false, union_by_name=false) WHERE observed_day = ? LIMIT 1",
        [uris, day],
    ).fetchone()
    return row is not None


def snapshot_product_columns(product: SnapshotProduct) -> frozenset[str]:
    """Return the exact allowlisted top-level serving schema for one snapshot product."""
    return frozenset(_snapshot_product_arrow_schema(product).names)


def _snapshot_product_arrow_schema(product: SnapshotProduct) -> pa.Schema:
    """Resolve the complete registered Arrow contract, narrowed only by pinned column names."""
    if product.schema_columns in _PINNED_ARROW_SCHEMAS:
        return _PINNED_ARROW_SCHEMAS[product.schema_columns]
    schema_layer = product.schema_layer or product.layer
    registered = get_stream_schema(schema_layer, "observed").arrow_schema
    if product.schema_columns is None:
        return registered
    fields_by_name = {field.name: field for field in registered}
    missing = tuple(name for name in product.schema_columns if name not in fields_by_name)
    if missing:
        raise ValueError(f"{product.layer} pins columns absent from registered schema {schema_layer}: {missing}")
    return pa.schema([fields_by_name[name] for name in product.schema_columns])


def _snapshot_declared_list_columns(product: SnapshotProduct) -> frozenset[str]:
    """Return only list cells declared by the registered physical snapshot schema."""
    schema = _snapshot_product_arrow_schema(product)
    return frozenset(
        field.name
        for field in schema
        if pa.types.is_list(field.type) or pa.types.is_large_list(field.type) or pa.types.is_fixed_size_list(field.type)
    )


def _duckdb_type_for_arrow(data_type: pa.DataType) -> str:
    """Translate the snapshot registry's supported Arrow scalars to DuckDB logical types."""
    if pa.types.is_string(data_type) or pa.types.is_large_string(data_type):
        resolved = "VARCHAR"
    elif pa.types.is_int64(data_type):
        resolved = "BIGINT"
    elif pa.types.is_float64(data_type):
        resolved = "DOUBLE"
    elif pa.types.is_boolean(data_type):
        resolved = "BOOLEAN"
    elif pa.types.is_date32(data_type):
        resolved = "DATE"
    elif pa.types.is_timestamp(data_type):
        resolved = "TIMESTAMP WITH TIME ZONE" if data_type.tz is not None else "TIMESTAMP"
    elif pa.types.is_fixed_size_list(data_type):
        resolved = f"{_duckdb_type_for_arrow(data_type.value_type)}[{data_type.list_size}]"
    elif pa.types.is_list(data_type) or pa.types.is_large_list(data_type):
        resolved = f"{_duckdb_type_for_arrow(data_type.value_type)}[]"
    else:
        raise ValueError(f"snapshot serving has no DuckDB type binding for registered Arrow type {data_type}")
    return resolved
