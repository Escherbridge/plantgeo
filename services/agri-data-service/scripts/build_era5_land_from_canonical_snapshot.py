"""Re-export the five ERA5-Land lanes from one pinned canonical snapshot at DAY grain.

THE SIBLING OF `scripts/build_soil_moisture_from_canonical_snapshot.py`, and deliberately so: that
script produced `soil-field-moisture-0-7cm`/`-7-28cm`/`-28-100cm`, which serve today, and this one
reads the same pinned snapshot, applies the same precedence, and writes the same four-rung day
ladder for the five products that snapshot still owes. A reader of one should be able to read the
other; where this one departs, the departure is named below and cited at its call site.

WHY A RE-EXPORT AND NOT A COPY. All five lanes are `layout="monthly"` in
`parquet_ops/snapshot_products.py` -- their frozen roots hold `kind=observed/zoom=NN/year/month/`
with NO day segment, so promoting them by server-side copy would land month files where the reader
looks for a day and the lane would advertise nothing. `snapshot_products.py:229-266` records that
exact failure and states the rule: `layout` is the discriminator, and a `monthly` lane needs a real
day-grain re-export. This is that re-export.

THE FAMILY HAS TWO FROZEN-ROOT SHAPES AND NEITHER IS HARDCODED HERE. `soil-field-vpd` sits under
`_layer_root` (`layer=soil-field-vpd/snapshot=<id>`) while the four soil-temperature lanes sit under
`_derived_lane_root` (`derived-canonical/signal-observation/lane=<slug>/snapshot=<id>`) --
`snapshot_products.py:124-131`. A census that assumed one shape reported the other as holding
nothing, which is the mistake recorded at `snapshot_products.py:255`. Every frozen-root read here
goes through `SnapshotProduct.data_root`/`.metadata_root`, never through a rebuilt path. Nothing is
ever WRITTEN into a frozen root: the soil-temperature roots carry a closed 583-object receipt
inventory that `snapshot_products` reconciles, so one extra object there breaks serving.

WHAT THIS WRITES, AND THE TWO OBJECT NAMES IT REFUSES TO CONFUSE. Per day and per rung it writes the
part file and then a REAL `PartitionCompletion` marker at `_complete.json` -- five fields,
`schema_version` 1, exactly what `foundation/parquet/completion.py:176-188` serializes. It never
writes a breakdown sidecar (`contract_version`/`part_key`/`part_sha256`/`tier`) where a marker
belongs: `classify_partition_day` reads parts-without-completion as `incomplete`, never `data`, so a
sidecar produces a lane with every part present that advertises nothing. It also never writes
`kind=physical`: that stream is an audit-only byte-identical copy carrying no zoom segment, and no
reader opens it.

THE PART IS ALWAYS WRITTEN BEFORE ITS MARKER, and the base rung's marker is written LAST of the
whole day (`_write_day`, mirroring `build_soil_moisture_from_canonical_snapshot.py:765-831`). A run
killed part-way therefore leaves a day whose base rung has parts and no completion, which reads as
`incomplete` and is re-exported on the next pass. A marker that outran its part would be a lane
claiming a day it cannot serve.

RESUME IS AGAINST THE LIVE PREFIX, NOT A CHECKPOINT TREE. `soil-field-vpd` already holds 446 correct
contiguous days (2022-04-30..2023-07-19) written by an earlier backfill, and every one of the five
lanes holds 16 forward-writer days at or above `SOIL_DIRECT_WRITER_START_DAY` (2026-08-03) which are
ABOVE this snapshot's window and none of this script's business. So the authority on "already done"
is a listing of `layer=<lane>/kind=observed/`, parsed with the frozen layout's own parsers: a day is
complete when all four rungs hold a part AND a completion marker. A month whose every in-window day
is already complete is never even loaded, which is what makes a resumed run cheap as well as safe.
Days already present are skipped, never overwritten -- and every write goes out under
`If-None-Match: *` so an unexpected collision refuses loudly instead of clobbering.

DETERMINISM IS WHAT MAKES THE RESUME SAFE. `run_id` is derived from the lane and the pinned source
manifest digest, and `completed_at` is the SOURCE snapshot's own completion instant, so re-running
an interrupted day reproduces byte-identical markers and parts rather than colliding with itself.

DEVIATIONS FROM THE REFERENCE, each with its reason:

- ROW SHAPE. The reference writes the 33-column `register_snapshot_lineage_product` schema because
  that is what its three lanes are registered with. These five are registered differently --
  `soil-field-vpd` as `register_signal_plane_product` (12 columns) and the four soil-temperature
  lanes as `register_soil_temperature_product` (21 columns), see
  `warehouse/parquet/snapshot_signal_product.py:194-212` and `:270-296`. This script writes each
  lane's OWN registered schema, because the live prefix already holds forward-writer days in that
  shape and a second shape at one prefix is a lane that cannot be read.
- LINEAGE ALIGNMENT CHECK. The reference's check (`build_soil_moisture_from_canonical_snapshot.py:
  742-764`) asserts six per-row lineage ARRAYS are aligned and that the winner is element zero.
  Neither shape here carries those arrays, so the same assertion is made against the in-memory
  precedence lineage in `_verify_lineage_alignment` before the table is built: the lineage is
  strictly ordered by the precedence contract, element zero IS the row that was written, and every
  derived count and digest on the row is recomputed from that lineage.
- NO DESTINATION MANIFEST, CHECKPOINT OR AUDIT TREE. The reference closes an immutable product and
  proves it with `_breakdown/snapshot=<id>/` metadata plus an exact-inventory check over
  `layer=<stream>/`. That check cannot hold here: these lanes' live prefixes are shared with a
  forward writer and, for VPD, with an earlier backfill. The proof is instead a live-prefix
  re-census after the writes (`_verify_written_lane`).
- `observation_count`/`newest_observed_at`/`physical_candidate_count` are computed from each grain's
  OWN candidate set. `scripts/soil_temperature_snapshot_breakdown.py:1029-1046` computes them from a
  leaked loop variable (`grain_rows` from the loop at `:966`, not the bound `winner_candidates`), so
  the frozen monthly soil-temperature product carries the LAST grain group's values in those three
  columns on every row. This script does not reproduce that defect; the consequence is stated in the
  handover, and it is why a day written here is not byte-identical to its frozen monthly counterpart.

Run from `services/agri-data-service`. A dry run is the default and writes nothing:

    UV_NO_SYNC=1 uv run --no-sync python scripts/build_era5_land_from_canonical_snapshot.py \
        --product vapour_pressure_deficit_max

    UV_NO_SYNC=1 uv run --no-sync python scripts/build_era5_land_from_canonical_snapshot.py \
        --product vapour_pressure_deficit_max --apply
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from calendar import monthrange
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

import boto3  # type: ignore[import-untyped]
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

SERVICE_ROOT = Path(__file__).resolve().parent.parent
CHECKOUT_ENV_FILE: Final = Path.home() / "Programming" / "plantgeo" / "services" / "agri-data-service" / ".env"
DEFAULT_ENV_FILE: Final = SERVICE_ROOT / ".env" if (SERVICE_ROOT / ".env").is_file() else CHECKOUT_ENV_FILE
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.config import ObjectStoreCredentials, Settings  # noqa: E402
from agri_data_service.foundation.parquet.completion import PartitionCompletion  # noqa: E402
from agri_data_service.foundation.parquet.paths import (  # noqa: E402
    completion_marker_path,
    partition_path,
    stream_prefix,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.foundation.parquet.zoom import ZoomTier  # noqa: E402
from agri_data_service.parquet_ops.snapshot_products import PRODUCT_BY_LAYER, SnapshotProduct  # noqa: E402
from agri_data_service.pipeline.direct.soil.products import (  # noqa: E402
    SOIL_DIRECT_WRITER_START_DAY,
    SOIL_FIELD_PRODUCT_BY_STREAM,
)
from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, get_stream_schema  # noqa: E402
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS, derive_tier  # noqa: E402

if TYPE_CHECKING:  # annotation-only: the rung literal that `partition_path` demands
    from agri_data_service.foundation.parquet.zoom import ZoomTier

# --- The pinned canonical snapshot -----------------------------------------------------------
# Identical to `build_soil_moisture_from_canonical_snapshot.py:51-70`: one snapshot feeds every
# ERA5-Land product, and its digest is the run identity every marker written here carries.
SOURCE_SNAPSHOT_ID: Final = "prod-20260826-full-signal-v1"
SOURCE_MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
SOURCE_ROOT: Final = f"raw-canonical/signal-observation/snapshot={SOURCE_SNAPSHOT_ID}"
SOURCE_MANIFEST_KEY: Final = f"{SOURCE_ROOT}/manifest.json"
SOURCE_COMPLETE_KEY: Final = f"{SOURCE_ROOT}/_COMPLETE"
SOURCE_CONTRACT_VERSION: Final = "agri.signal_observation.raw-canonical.v1"
PRECEDENCE_CONTRACT: Final = "newest-release-retrieved-at-then-highest-observation-id-v1"
SOURCE_KEY: Final = "open-meteo-era5-land-archive"
SUPPORT_KEY: Final = "era5-land-0.1deg"
CELL_GRID_NAME: Final = "sentinel2-ndvi-0p25deg"
SNAPSHOT_FIRST_DAY: Final = date(2022, 4, 30)
SNAPSHOT_LAST_DAY: Final = date(2026, 8, 6)
SOURCE_ROW_COUNT: Final = 46_146_568
SOURCE_PARTITION_COUNT: Final = 8_364
SOURCE_BATCH_COUNT: Final = 424

# --- What every one of the five products owes -------------------------------------------------
# Measured 2026-09-08 from each lane's own frozen product manifest, itself pinned by SHA-256 in
# `snapshot_products.py`. All five agree exactly, which is why these are shared constants rather
# than per-product fields: `physical_scope_rows` 4,298,280, `rejected_rows` 0, `selected_rows`
# 2,287,320, `superseded_rows` 2,010,960, `data_day_count` 1,556 over 2022-04-30..2026-08-02, and
# `tiers` row totals of 2,287,320 at z13/z09/z05 and 9,336 at z00.
EXPECTED_SOURCE_PARTS: Final = 424
EXPECTED_MONTHS: Final = 53
EXPECTED_PHYSICAL_ROWS: Final = 4_298_280
EXPECTED_ELIGIBLE_ROWS: Final = 4_298_280
EXPECTED_BASE_ROWS: Final = 2_287_320
EXPECTED_DUPLICATES_COLLAPSED: Final = 2_010_960
EXPECTED_DAYS: Final = 1_556
EXPECTED_CELLS_PER_DAY: Final = 1_470
EXPECTED_FIRST_DAY: Final = date(2022, 4, 30)
EXPECTED_LAST_DAY: Final = date(2026, 8, 2)
#: Rows one day holds at each rung: 2,287,320 / 1,556 at the three fine rungs and 9,336 / 1,556 at
#: z00, straight out of the frozen manifests' `tiers` block. A rung that derives to any other height
#: means the lattice or the derivation moved under this snapshot.
EXPECTED_TIER_ROWS_PER_DAY: Final[Mapping[int, int]] = {13: 1_470, 9: 1_470, 5: 1_470, 0: 6}

DEFAULT_WORKERS: Final = 8
DEFAULT_AUDIT_EXISTING_DAYS: Final = 2
DEFAULT_PREVIEW_DAYS: Final = 3
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE: Final = "application/json"
PRECONDITION_CODES: Final = frozenset({"412", "PreconditionFailed", "ConditionalRequestConflict"})

RowShape = Literal["signal_plane", "soil_temperature"]

#: How wide each row shape is: the frozen twelve-column signal plane
#: (`warehouse/parquet/schema.py:177-208`) and the twenty-one-column lane shape that leads with
#: `data_source_key`/`source_parameter` (`warehouse/parquet/snapshot_signal_product.py:129-151`).
SHAPE_COLUMN_COUNTS: Final[Mapping[RowShape, int]] = {"signal_plane": 12, "soil_temperature": 21}


@dataclass(frozen=True, slots=True)
class ProductSpec:
    """One ERA5-Land product: its canonical partition, its lane, and what its source prefix holds."""

    product: str
    lane: str
    signal_name: str
    normalized_unit: str
    row_shape: RowShape
    expected_source_bytes: int
    frozen_manifest_sha256: str
    #: This lane's frozen root, PINNED rather than read from `PRODUCT_BY_LAYER`. A lane leaves
    #: `SNAPSHOT_PRODUCTS` the moment this builder finishes it -- that is the point of the run -- so a
    #: lookup would make the script unable to re-run the very lanes it just built, which is exactly
    #: what happened on 2026-09-08. Note the two shapes differ WITHIN this one family: vpd is rooted
    #: under `layer=`, the four soil-temperature lanes under `derived-canonical/`.
    frozen_data_root: str


#: The five products this snapshot still owes a day-grain lane. `product` is the canonical
#: `product_key`/`source_parameter`; `signal_name`, `normalized_unit` and `row_shape` are transcribed
#: from `pipeline/direct/soil/products.py:SOIL_FIELD_PRODUCTS` and re-asserted against it at startup
#: by `_verify_product_table`, so a drifted value fails the run rather than writing a second signal
#: under the first one's slug. `expected_source_bytes` and `frozen_manifest_sha256` were measured on
#: 2026-09-08 against the live bucket; the four soil-temperature digests also equal the pins in
#: `snapshot_products.py:317-352`, and `soil-field-vpd` has no pin there so its digest is pinned here.
PRODUCTS: Final = {
    "vapour_pressure_deficit_max": ProductSpec(
        product="vapour_pressure_deficit_max",
        lane="soil-field-vpd",
        signal_name="vapor_pressure_deficit",
        normalized_unit="kPa",
        row_shape="signal_plane",
        expected_source_bytes=186_032_189,
        frozen_manifest_sha256="92846082bbc40b73480264c6b521e2e575139a03d69b72dd67f4a6e95c41c13e",
        frozen_data_root=f"layer=soil-field-vpd/snapshot={SOURCE_SNAPSHOT_ID}",
    ),
    "soil_temperature_0_to_7cm_mean": ProductSpec(
        product="soil_temperature_0_to_7cm_mean",
        lane="soil-temperature-0-to-7cm",
        signal_name="soil_temperature_level_1",
        normalized_unit="C",
        row_shape="soil_temperature",
        expected_source_bytes=185_359_963,
        frozen_data_root=f"derived-canonical/signal-observation/lane=soil-temperature-0-to-7cm/snapshot={SOURCE_SNAPSHOT_ID}",
        frozen_manifest_sha256="67216660bd64f938e883dd51eba0fc9c28afbdd3eaa79351862eb96f4d4e480f",
    ),
    "soil_temperature_7_to_28cm_mean": ProductSpec(
        product="soil_temperature_7_to_28cm_mean",
        lane="soil-temperature-7-to-28cm",
        signal_name="soil_temperature_level_2",
        normalized_unit="C",
        row_shape="soil_temperature",
        expected_source_bytes=184_914_248,
        frozen_data_root=f"derived-canonical/signal-observation/lane=soil-temperature-7-to-28cm/snapshot={SOURCE_SNAPSHOT_ID}",
        frozen_manifest_sha256="0120ae2a9d6922b67861bf257b8c1b354a97e6cc0e889e146305ccd4e4a835d1",
    ),
    "soil_temperature_28_to_100cm_mean": ProductSpec(
        product="soil_temperature_28_to_100cm_mean",
        lane="soil-temperature-28-to-100cm",
        signal_name="soil_temperature_level_3",
        normalized_unit="C",
        row_shape="soil_temperature",
        expected_source_bytes=184_048_677,
        frozen_data_root=f"derived-canonical/signal-observation/lane=soil-temperature-28-to-100cm/snapshot={SOURCE_SNAPSHOT_ID}",
        frozen_manifest_sha256="3cacd5856630dc252f4f71d6b7156ec98cb8125270743420c5d7fa692ca2ce34",
    ),
    "soil_temperature_100_to_255cm_mean": ProductSpec(
        product="soil_temperature_100_to_255cm_mean",
        lane="soil-temperature-100-to-255cm",
        signal_name="soil_temperature_level_4",
        normalized_unit="C",
        row_shape="soil_temperature",
        expected_source_bytes=183_455_305,
        frozen_data_root=f"derived-canonical/signal-observation/lane=soil-temperature-100-to-255cm/snapshot={SOURCE_SNAPSHOT_ID}",
        frozen_manifest_sha256="d40aa7851877f2ab4f85b71b7e8a9ed7bb6c5c4d754e5f54ee8ec6b4237cb82f",
    ),
}

ACTIVE_PRODUCT: ProductSpec = PRODUCTS["vapour_pressure_deficit_max"]
ACTIVE_LANE: str = ACTIVE_PRODUCT.lane
ACTIVE_SCHEMA: ParquetStreamSchema = get_stream_schema(ACTIVE_LANE)
ACTIVE_FROZEN_ROOT: str = ACTIVE_PRODUCT.frozen_data_root
ACTIVE_DESCRIPTOR: SnapshotProduct | None = PRODUCT_BY_LAYER.get(ACTIVE_LANE)
SOURCE_PARAMETER: str = ACTIVE_PRODUCT.product
SIGNAL_NAME: str = ACTIVE_PRODUCT.signal_name
NORMALIZED_UNIT: str = ACTIVE_PRODUCT.normalized_unit
ROW_SHAPE: RowShape = ACTIVE_PRODUCT.row_shape
SOURCE_PART_PREFIX: str = f"{SOURCE_ROOT}/source={SOURCE_KEY}/product={SOURCE_PARAMETER}/support={SUPPORT_KEY}/"
#: The availability index subtree, which shares `kind=observed/` with the day ladder.
AVAILABILITY_SEGMENT: Final = "availability"
LIVE_STREAM_PREFIX: str = stream_prefix(ACTIVE_LANE, "observed")
RUN_ID: str = f"{ACTIVE_LANE}-snapshot-day-grain:{SOURCE_MANIFEST_SHA256}"


def _resolve_frozen_root(product: ProductSpec) -> str:
    """Return the lane's frozen root, cross-checking `SNAPSHOT_PRODUCTS` only while it is still there.

    Membership is TRANSIENT by design: a finished lane leaves the tuple. So the pinned value is the
    source of truth and the descriptor, when present, is a second witness that must agree.
    """
    descriptor = PRODUCT_BY_LAYER.get(product.lane)
    if descriptor is not None and descriptor.data_root != product.frozen_data_root:
        raise BuildError(
            f"{product.lane}: pinned frozen root {product.frozen_data_root!r} disagrees with the live "
            f"descriptor {descriptor.data_root!r}"
        )
    return product.frozen_data_root


def _activate(product: ProductSpec) -> None:
    """Bind every per-product module global for this invocation, as the reference builder does."""
    global ACTIVE_PRODUCT, ACTIVE_LANE, ACTIVE_SCHEMA, ACTIVE_FROZEN_ROOT, ACTIVE_DESCRIPTOR
    global SOURCE_PARAMETER, SIGNAL_NAME, NORMALIZED_UNIT, ROW_SHAPE
    global SOURCE_PART_PREFIX, LIVE_STREAM_PREFIX, RUN_ID

    ACTIVE_PRODUCT = product
    ACTIVE_LANE = product.lane
    ACTIVE_SCHEMA = get_stream_schema(product.lane)
    ACTIVE_FROZEN_ROOT = _resolve_frozen_root(product)
    ACTIVE_DESCRIPTOR = PRODUCT_BY_LAYER.get(product.lane)
    SOURCE_PARAMETER = product.product
    SIGNAL_NAME = product.signal_name
    NORMALIZED_UNIT = product.normalized_unit
    ROW_SHAPE = product.row_shape
    SOURCE_PART_PREFIX = f"{SOURCE_ROOT}/source={SOURCE_KEY}/product={SOURCE_PARAMETER}/support={SUPPORT_KEY}/"
    LIVE_STREAM_PREFIX = stream_prefix(product.lane, "observed")
    RUN_ID = f"{product.lane}-snapshot-day-grain:{SOURCE_MANIFEST_SHA256}"


#: The canonical columns a source part must carry. `build_soil_moisture_from_canonical_snapshot.py:
#: 146-170` plus `original_unit` and `cell_grid_name`, which the two frozen builders for THIS family
#: both test (`soil_temperature_snapshot_breakdown.py:912-914`, `vpd_snapshot_breakdown.py:641-645`)
#: and which are therefore part of this family's eligibility contract.
RAW_REQUIRED_COLUMNS: Final = frozenset(
    {
        "id",
        "source_release_id",
        "cell_id",
        "signal_name",
        "source_parameter",
        "support_key",
        "observed_at",
        "data_available_at",
        "original_unit",
        "normalized_value",
        "normalized_unit",
        "quality_flag",
        "coverage_fraction",
        "is_observed",
        "observation_day",
        "product_key",
        "cell_key",
        "cell_grid_name",
        "cell_centroid_longitude",
        "cell_centroid_latitude",
        "data_source_id",
        "data_source_key",
        "canonical_row_sha256",
    }
)


class BuildError(RuntimeError):
    """Raised when the pinned snapshot cannot produce an exact day-grain ERA5-Land lane."""


class ImmutableObjectConflictError(BuildError):
    """Raised when a destination key already contains different bytes."""


@dataclass(frozen=True, slots=True)
class ObjectReceipt:
    """One object this run measured, and whether it was actually uploaded."""

    key: str
    row_count: int
    byte_count: int
    sha256: str
    kind: str
    zoom: int
    written: bool

    def to_json(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {
            "key": self.key,
            "row_count": self.row_count,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "kind": self.kind,
            "zoom": self.zoom,
            "written": self.written,
        }


@dataclass(frozen=True, slots=True)
class DayBase:
    """One observation day's base rung, already deduplicated and lineage-checked."""

    observed_day: date
    table: pa.Table
    lineage_row_count: int


@dataclass(frozen=True, slots=True)
class SourceMonth:
    """One observation month of the pinned snapshot, reduced to its per-day base rungs."""

    month: str
    physical_rows: int
    eligible_rows: int
    excluded_rows: Mapping[str, int]
    physical_bytes: int
    source_part_keys: tuple[str, ...]
    base_by_day: Mapping[date, DayBase]


#: The published ladder: the base rung this builder serializes plus the three it derives.
PUBLISHED_RUNGS: Final[frozenset[int]] = frozenset({BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS})


@dataclass(frozen=True, slots=True)
class LadderState:
    """What the live prefix already holds for one day: which rungs carry a part and a completion."""

    parts: frozenset[int]
    completions: frozenset[int]
    derived_empties: frozenset[int]

    @property
    def is_complete(self) -> bool:
        """A day is complete when every rung holds a terminal state: a part with its marker, or an honest empty.

        `_complete.empty.json` counts as terminal for a DERIVED rung -- `foundation/parquet/paths.py:
        306-309` gives that claim its own name precisely so no reader has to open a body to tell a
        rung that generalised to nothing from one whose parts vanished under its marker.
        """
        return (self.parts | self.derived_empties) >= PUBLISHED_RUNGS and (
            self.completions | self.derived_empties
        ) >= PUBLISHED_RUNGS


class ImmutableS3:
    """Small conditional-write S3 surface scoped to the configured warehouse prefix."""

    def __init__(self, credentials: ObjectStoreCredentials, object_store_prefix: str) -> None:
        self.bucket = credentials.bucket
        self.object_store_prefix = object_store_prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            region_name=credentials.region,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
            config=Config(retries={"max_attempts": 12, "mode": "adaptive"}),
        )

    def _key(self, relative: str) -> str:
        clean = relative.strip("/")
        return f"{self.object_store_prefix}/{clean}" if self.object_store_prefix else clean

    def get(self, relative: str) -> bytes | None:
        """Read one object, returning `None` for a key that does not exist."""
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(relative))
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        payload = response["Body"].read()
        return payload if isinstance(payload, bytes) else bytes(payload)

    def list_keys(self, relative_prefix: str) -> list[tuple[str, int]]:
        """List every object under one relative prefix as sorted (relative key, byte count) pairs."""
        full_prefix = self._key(relative_prefix)
        token: str | None = None
        found: list[tuple[str, int]] = []
        while True:
            request: dict[str, object] = {"Bucket": self.bucket, "Prefix": full_prefix}
            if token is not None:
                request["ContinuationToken"] = token
            response = self.client.list_objects_v2(**request)
            for item in response.get("Contents", []):
                full_key = str(item["Key"])
                relative = full_key[len(self.object_store_prefix) + 1 :] if self.object_store_prefix else full_key
                found.append((relative, int(item["Size"])))
            token_value = response.get("NextContinuationToken")
            if not isinstance(token_value, str) or not token_value:
                break
            token = token_value
        return sorted(found)

    def put_immutable(self, relative: str, payload: bytes, *, content_type: str) -> str:
        """Upload one object that must not already exist under different bytes; return its digest."""
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._key(relative),
                Body=payload,
                ContentType=content_type,
                IfNoneMatch="*",
            )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in PRECONDITION_CODES:
                raise
            existing = self.get(relative)
            if existing != payload:
                raise ImmutableObjectConflictError(
                    f"immutable key {relative!r} holds sha256={_sha256(existing or b'')}, "
                    f"attempted sha256={_sha256(payload)}"
                ) from error
        durable = self.get(relative)
        if durable != payload:
            raise BuildError(f"durable read-back failed for {relative!r}")
        return _sha256(payload)


def _sha256(payload: bytes) -> str:
    """Return the lowercase hex SHA-256 of one payload."""
    return hashlib.sha256(payload).hexdigest()


def _json_object(payload: bytes, *, key: str) -> dict[str, Any]:
    """Decode one object that must contain a JSON object."""
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildError(f"{key!r} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise BuildError(f"{key!r} must contain a JSON object")
    return value


def _required_object(store: ImmutableS3, key: str) -> bytes:
    """Read one object that the pinned contract requires to exist."""
    payload = store.get(key)
    if payload is None:
        raise BuildError(f"required immutable object {key!r} is missing")
    return payload


def _require_frame(value: pl.DataFrame | pl.Series) -> pl.DataFrame:
    """Narrow `pl.from_arrow`'s declared union: an Arrow Table always yields a DataFrame.

    The union exists because the same call accepts a ChunkedArray and returns a Series for it. Every
    caller here passes a Table, so the Series arm is unreachable.
    """
    if not isinstance(value, pl.DataFrame):
        raise BuildError(f"expected a Polars DataFrame from an Arrow table, got {type(value).__name__}")
    return value


def _as_utc(value: object, *, what: str) -> datetime:
    """Return one zoned timestamp in UTC, refusing a naive or non-timestamp value."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BuildError(f"{what} is not a timezone-aware timestamp: {value!r}")
    return value.astimezone(UTC)


def _row_set_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    """Digest a physical row population by identity, order-independently."""
    digest = hashlib.sha256()
    identities = sorted((int(row["id"]), str(row["canonical_row_sha256"])) for row in rows)
    for row_id, row_hash in identities:
        digest.update(f"{row_id}:{row_hash}\n".encode("ascii"))
    return digest.hexdigest()


def _lineage_digest(values: Sequence[str]) -> str:
    """Digest one grain's candidate hashes exactly as the frozen lane did (sorted, newline-framed)."""
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _render_value(value: object) -> str:
    """Render one cell to a stable string: a digest over these must not depend on Python's repr drift."""
    if value is None:
        return "\x00null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _base_row_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    """Digest one day's base rung over EVERY column, so two builds of it can be compared exactly.

    Deliberately not a grain digest: `_audit_existing_day` uses this to decide whether a day already
    in the lane was built by this same rule, and a digest over a few columns would call two
    populations equal while they disagreed on exposure, coverage or position.
    """
    columns = tuple(ACTIVE_SCHEMA.column_names)
    digest = hashlib.sha256()
    for rendered in sorted("\x1f".join(_render_value(row[column]) for column in columns) for row in rows):
        digest.update(rendered.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _serialize(table: pa.Table) -> bytes:
    """Conform one table to the active stream contract and serialize it as the lane's Parquet."""
    schema = ACTIVE_SCHEMA.arrow_schema
    conformed = table.select(schema.names).cast(schema).replace_schema_metadata(schema.metadata)
    conformed = conformed.sort_by([(column, "ascending") for column in ACTIVE_SCHEMA.sort_columns])
    buffer = io.BytesIO()
    pq.write_table(
        conformed,
        buffer,
        compression=ACTIVE_SCHEMA.compression,
        write_statistics=True,
        row_group_size=64_000,
    )
    return buffer.getvalue()


def _load_parquet(payload: bytes, *, key: str) -> pa.Table:
    """Read one Parquet object, naming the key when it will not decode."""
    try:
        return pq.read_table(io.BytesIO(payload))
    except Exception as error:
        raise BuildError(f"{key!r} is not readable Parquet: {type(error).__name__}: {error}") from error


def _verify_product_table() -> None:
    """Refuse if this script's product table drifted from the two repo tables that own those facts."""
    forward = SOIL_FIELD_PRODUCT_BY_STREAM.get(ACTIVE_LANE)
    if forward is None:
        raise BuildError(f"{ACTIVE_LANE!r} is not one of the eight ERA5-Land soil products")
    declared = {
        "source_parameter": (forward.source_parameter, SOURCE_PARAMETER),
        "signal_name": (forward.signal_name, SIGNAL_NAME),
        "normalized_unit": (forward.normalized_unit, NORMALIZED_UNIT),
        "row_shape": (forward.row_shape, ROW_SHAPE),
    }
    drift = {name: value for name, value in declared.items() if value[0] != value[1]}
    if drift:
        raise BuildError(f"product table drifted from pipeline/direct/soil/products.py: {drift}")
    # These five lanes are month-grain by construction -- that is WHY they need a re-export rather
    # than a copy -- and the window below their forward edge is pinned above. Both facts are checked
    # against the live descriptor WHEN ONE EXISTS, but a graduated lane has none, and the checks must
    # still hold so the builder can re-run.
    if ACTIVE_DESCRIPTOR is not None:
        if ACTIVE_DESCRIPTOR.layout != "monthly":
            raise BuildError(
                f"{ACTIVE_LANE!r} is layout={ACTIVE_DESCRIPTOR.layout!r}; only a monthly frozen root "
                "needs a day-grain re-export, and a daily one is promoted by copy instead"
            )
        if ACTIVE_DESCRIPTOR.forward_first_day is None:
            raise BuildError(f"{ACTIVE_LANE!r} declares no forward edge, so this snapshot window is unbounded")
        if ACTIVE_DESCRIPTOR.forward_first_day <= EXPECTED_LAST_DAY:
            raise BuildError(
                f"this window ends {EXPECTED_LAST_DAY.isoformat()}, at or above {ACTIVE_LANE}'s forward edge "
                f"{ACTIVE_DESCRIPTOR.forward_first_day.isoformat()}; writing there would collide with the "
                "live writer that owns those days"
            )
    elif EXPECTED_LAST_DAY >= SOIL_DIRECT_WRITER_START_DAY:
        raise BuildError(
            f"this window ends {EXPECTED_LAST_DAY.isoformat()}, at or above the ERA5-Land forward edge "
            f"{SOIL_DIRECT_WRITER_START_DAY.isoformat()}; writing there would collide with the live writer"
        )
    expected_columns = SHAPE_COLUMN_COUNTS[ROW_SHAPE]
    if len(ACTIVE_SCHEMA.column_names) != expected_columns:
        raise BuildError(
            f"{ACTIVE_LANE!r} registers {len(ACTIVE_SCHEMA.column_names)} columns; a {ROW_SHAPE!r} lane has "
            f"{expected_columns}, so this script would write a shape its own reader does not expect"
        )


def _load_source_contract(store: ImmutableS3) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read and pin the canonical snapshot manifest and its `_COMPLETE`, refusing any drift."""
    manifest_payload = _required_object(store, SOURCE_MANIFEST_KEY)
    actual_manifest_sha256 = _sha256(manifest_payload)
    if actual_manifest_sha256 != SOURCE_MANIFEST_SHA256:
        raise BuildError(f"source manifest sha256 is {actual_manifest_sha256}, expected {SOURCE_MANIFEST_SHA256}")
    manifest = _json_object(manifest_payload, key=SOURCE_MANIFEST_KEY)
    completion = _json_object(_required_object(store, SOURCE_COMPLETE_KEY), key=SOURCE_COMPLETE_KEY)
    expected_manifest = {
        "contract_version": SOURCE_CONTRACT_VERSION,
        "snapshot_id": SOURCE_SNAPSHOT_ID,
        "row_count": SOURCE_ROW_COUNT,
        "partition_count": SOURCE_PARTITION_COUNT,
        "batch_count": SOURCE_BATCH_COUNT,
        "rejected_rows": 0,
        "observation_day_min": SNAPSHOT_FIRST_DAY.isoformat(),
        "observation_day_max": SNAPSHOT_LAST_DAY.isoformat(),
    }
    drift = {key: (manifest.get(key), value) for key, value in expected_manifest.items() if manifest.get(key) != value}
    if drift:
        raise BuildError(f"pinned source manifest contract drifted: {drift}")
    if completion.get("manifest_sha256") != SOURCE_MANIFEST_SHA256:
        raise BuildError("source _COMPLETE does not pin the required manifest sha256")
    if (
        completion.get("row_count") != manifest["row_count"]
        or completion.get("partition_count") != manifest["partition_count"]
    ):
        raise BuildError("source manifest and _COMPLETE counts disagree")
    return manifest, completion


def _load_frozen_product_contract(store: ImmutableS3) -> dict[str, Any]:
    """Read the lane's own frozen product manifest through `data_root`, never a rebuilt path shape."""
    manifest_key = f"{ACTIVE_FROZEN_ROOT}/manifest.json"
    payload = _required_object(store, manifest_key)
    observed_sha256 = _sha256(payload)
    if observed_sha256 != ACTIVE_PRODUCT.frozen_manifest_sha256:
        raise BuildError(
            f"frozen product manifest {manifest_key!r} is {observed_sha256}, "
            f"expected {ACTIVE_PRODUCT.frozen_manifest_sha256}"
        )
    pinned = ACTIVE_DESCRIPTOR.expected_manifest_sha256 if ACTIVE_DESCRIPTOR is not None else None
    if pinned is not None and pinned != observed_sha256:
        raise BuildError(f"frozen product manifest {manifest_key!r} does not match its snapshot_products.py pin")
    return {
        "key": manifest_key,
        "sha256": observed_sha256,
        "pinned_in_snapshot_products": pinned is not None,
        "data_root": ACTIVE_FROZEN_ROOT,
        "layout": "monthly",
    }


def _load_dimensions(
    store: ImmutableS3, manifest: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load and checksum the frozen data-source and source-release dimensions the precedence needs."""
    dimensions = manifest.get("dimension_objects")
    if not isinstance(dimensions, Mapping):
        raise BuildError("source manifest has no dimension object inventory")

    def table(name: str) -> pa.Table:
        metadata = dimensions.get(name)
        if not isinstance(metadata, Mapping):
            raise BuildError(f"source manifest has no {name} dimension")
        key = str(metadata["key"])
        expected_key = f"{SOURCE_ROOT}/_dimensions/{name}.parquet"
        if key != expected_key:
            raise BuildError(f"source manifest dimension {name!r} escapes the pinned snapshot: {key!r}")
        payload = _required_object(store, key)
        if len(payload) != int(metadata["byte_count"]) or _sha256(payload) != metadata["sha256"]:
            raise BuildError(f"source dimension {name!r} failed checksum reconciliation")
        loaded = _load_parquet(payload, key=key)
        if loaded.num_rows != int(metadata["row_count"]):
            raise BuildError(f"source dimension {name!r} row count drifted")
        return loaded

    data_sources = table("data_source").to_pylist()
    selected_sources = [row for row in data_sources if row["key"] == SOURCE_KEY]
    if len(selected_sources) != 1:
        raise BuildError(f"source snapshot contains {len(selected_sources)} {SOURCE_KEY!r} data-source rows")
    releases = table("source_release").to_pylist()
    by_release = {str(row["id"]): row for row in releases}
    if len(by_release) != len(releases):
        raise BuildError("source-release dimension contains duplicate ids")
    source = selected_sources[0]
    if source.get("allowed_client_exposure") is not False:
        raise BuildError("pinned ERA5-Land source exposure policy is no longer false")
    return source, by_release


def _month_ledgers(manifest: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """Group the manifest's 424 month/cell-batch ledger summaries into its 53 observation months."""
    rows = manifest.get("month_ledgers")
    if not isinstance(rows, list) or len(rows) != EXPECTED_SOURCE_PARTS:
        raise BuildError(f"source manifest does not describe the expected {EXPECTED_SOURCE_PARTS} month/cell batches")
    by_month: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise BuildError("source manifest contains a non-object month ledger summary")
        required = {
            "observation_month",
            "cell_batch_index",
            "row_count",
            "part_count",
            "byte_count",
            "source_row_digest",
        }
        if not required.issubset(row):
            raise BuildError(f"source manifest ledger summary omits {sorted(required.difference(row))}")
        month = str(row["observation_month"])
        batch_index = int(row["cell_batch_index"])
        identity = (month, batch_index)
        if identity in seen:
            raise BuildError(f"source manifest repeats ledger summary {identity}")
        seen.add(identity)
        by_month[month].append(row)
    for month, summaries in by_month.items():
        indices = sorted(int(summary["cell_batch_index"]) for summary in summaries)
        if indices != list(range(len(indices))):
            raise BuildError(f"source manifest cell-batch indices are not contiguous for {month}")
    if len(by_month) != EXPECTED_MONTHS:
        raise BuildError(f"pinned snapshot spans {len(by_month)} months; expected {EXPECTED_MONTHS}")
    return {
        month: tuple(sorted(summaries, key=lambda summary: int(summary["cell_batch_index"])))
        for month, summaries in sorted(by_month.items())
    }


def _verified_ledger(store: ImmutableS3, summary: Mapping[str, Any]) -> dict[str, Any]:
    """Read one cell-batch ledger and bind it to the pinned manifest summary that named it."""
    month = str(summary["observation_month"])
    batch_index = int(summary["cell_batch_index"])
    ledger_key = f"{SOURCE_ROOT}/_ledger/month={month}/cell-batch={batch_index:05d}.json"
    ledger = _json_object(_required_object(store, ledger_key), key=ledger_key)
    identity = {
        "contract_version": SOURCE_CONTRACT_VERSION,
        "snapshot_id": SOURCE_SNAPSHOT_ID,
        "observation_month": month,
        "cell_batch_index": batch_index,
    }
    summary_fields = ("row_count", "part_count", "byte_count", "source_row_digest")
    drift = {
        key: (ledger.get(key), value)
        for key, value in {**identity, **{field: summary[field] for field in summary_fields}}.items()
        if ledger.get(key) != value
    }
    if drift:
        raise BuildError(f"source ledger {ledger_key!r} is not bound to the pinned manifest summary: {drift}")
    parts = ledger.get("parts")
    if not isinstance(parts, list):
        raise BuildError(f"source ledger {ledger_key!r} has no parts list")
    if len(parts) != int(ledger["part_count"]):
        raise BuildError(f"source ledger {ledger_key!r} part count is internally inconsistent")
    if sum(int(part["row_count"]) for part in parts) != int(ledger["row_count"]):
        raise BuildError(f"source ledger {ledger_key!r} row count is internally inconsistent")
    if sum(int(part["byte_count"]) for part in parts) != int(ledger["byte_count"]):
        raise BuildError(f"source ledger {ledger_key!r} byte count is internally inconsistent")
    if int(ledger.get("rejected_rows", -1)) != 0:
        raise BuildError(f"source ledger {ledger_key!r} contains rejected rows")
    return ledger


def _source_part_for_ledger(ledger: Mapping[str, Any], *, year: int, month: int) -> Mapping[str, Any]:
    """Select the one part of one ledger that belongs to the active product's pinned prefix."""
    expected = (
        f"source={SOURCE_KEY}/product={SOURCE_PARAMETER}/support={SUPPORT_KEY}/year={year:04d}/month={month:02d}/"
    )
    parts = ledger.get("parts")
    if not isinstance(parts, list):
        raise BuildError("source ledger has no parts list")
    selected = [
        part for part in parts if isinstance(part, Mapping) and str(part.get("relative_path", "")).startswith(expected)
    ]
    if len(selected) != 1:
        raise BuildError(f"source ledger contains {len(selected)} {ACTIVE_LANE} parts under {expected!r}, expected one")
    part = selected[0]
    key = str(part["key"])
    if key != f"{SOURCE_ROOT}/{part['relative_path']}" or not key.startswith(SOURCE_PART_PREFIX):
        raise BuildError(f"source ledger part escapes the pinned prefix: {key!r}")
    return part


def _classify_raw_row(row: Mapping[str, Any], *, month: str) -> str | None:
    """Return the reason one physical row is ineligible, or `None` when it may compete for its grain."""
    partition_contract = {
        "data_source_key": SOURCE_KEY,
        "product_key": SOURCE_PARAMETER,
        "support_key": SUPPORT_KEY,
    }
    drift = {key: (row.get(key), value) for key, value in partition_contract.items() if row.get(key) != value}
    if drift:
        raise BuildError(f"raw {ACTIVE_LANE} row {row.get('id')} in {month} violates its partition path: {drift}")
    observed_day = row.get("observation_day")
    observed_at = row.get("observed_at")
    if (
        not isinstance(observed_day, date)
        or not isinstance(observed_at, datetime)
        or observed_at.date() != observed_day
    ):
        raise BuildError(f"raw {ACTIVE_LANE} row {row.get('id')} has an inconsistent observation day")
    row_hash = row.get("canonical_row_sha256")
    if not isinstance(row_hash, str) or len(row_hash) != 64:
        raise BuildError(f"raw {ACTIVE_LANE} row {row.get('id')} has no canonical SHA-256")
    if row.get("signal_name") != SIGNAL_NAME:
        return "signal_name_drift"
    if row.get("source_parameter") != SOURCE_PARAMETER:
        return "source_parameter_drift"
    if row.get("cell_grid_name") != CELL_GRID_NAME:
        return "cell_grid_drift"
    if row.get("normalized_unit") is None:
        return "normalized_unit_null"
    if row.get("normalized_unit") != NORMALIZED_UNIT:
        return "normalized_unit_drift"
    if row.get("original_unit") != NORMALIZED_UNIT:
        return "original_unit_drift"
    if row.get("is_observed") is not True:
        return "not_observed"
    if row.get("quality_flag") != "accepted":
        return "quality_not_accepted"
    value = row.get("normalized_value")
    if value is None:
        return "normalized_value_null"
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BuildError(f"raw {ACTIVE_LANE} row {row.get('id')} has a non-numeric normalized value")
    return None


def _output_row(
    selected: Mapping[str, Any],
    *,
    selected_release: Mapping[str, Any],
    lineage: Sequence[Mapping[str, Any]],
    data_source: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one precedence winner into the row shape its own lane is registered with."""
    plane = {
        "support_key": selected["support_key"],
        "signal_name": selected["signal_name"],
        "normalized_unit": selected["normalized_unit"],
        "cell_id": selected["cell_id"],
        "observed_day": selected["observation_day"],
        "normalized_value": selected["normalized_value"],
        "observation_count": len(lineage),
        "newest_observed_at": max(_as_utc(row["observed_at"], what="observed_at") for row in lineage),
        "coverage_fraction": selected["coverage_fraction"],
        "allowed_client_exposure": data_source["allowed_client_exposure"],
        "cell_longitude": selected["cell_centroid_longitude"],
        "cell_latitude": selected["cell_centroid_latitude"],
    }
    if ROW_SHAPE == "signal_plane":
        return plane
    return {
        "data_source_key": SOURCE_KEY,
        "source_parameter": SOURCE_PARAMETER,
        **plane,
        "selected_observation_id": selected["id"],
        "selected_canonical_row_sha256": selected["canonical_row_sha256"],
        "selected_source_release_id": selected["source_release_id"],
        "selected_release_retrieved_at": selected_release["retrieved_at"],
        "physical_candidate_count": len(lineage),
        "lineage_sha256": _lineage_digest([str(row["canonical_row_sha256"]) for row in lineage]),
        "input_manifest_sha256": SOURCE_MANIFEST_SHA256,
    }


def _verify_lineage_alignment(
    observed_day: date,
    *,
    outputs: Sequence[Mapping[str, Any]],
    lineages: Sequence[Sequence[Mapping[str, Any]]],
    releases: Mapping[str, Mapping[str, Any]],
) -> int:
    """Assert every written row is element zero of its own precedence-ordered lineage.

    The purpose of `build_soil_moisture_from_canonical_snapshot.py:742-764`, made against the
    in-memory lineage because neither row shape in this family carries the six lineage ARRAYS that
    check reads. Returns the physical rows the day's lineages account for.
    """
    physical_rows = 0
    for output, lineage in zip(outputs, lineages, strict=True):
        if not lineage:
            raise BuildError(f"{ACTIVE_LANE} {observed_day} has a grain with no candidate rows")
        ranked = [
            (_as_utc(releases[str(row["source_release_id"])]["retrieved_at"], what="retrieved_at"), int(row["id"]))
            for row in lineage
        ]
        if ranked != sorted(ranked, reverse=True):
            raise BuildError(f"{ACTIVE_LANE} {observed_day}/{output['cell_id']} lineage is not precedence-ordered")
        winner = lineage[0]
        aligned = {
            "cell_id": (output["cell_id"], winner["cell_id"]),
            "observed_day": (output["observed_day"], winner["observation_day"]),
            "normalized_value": (output["normalized_value"], winner["normalized_value"]),
            "coverage_fraction": (output["coverage_fraction"], winner["coverage_fraction"]),
            "cell_longitude": (output["cell_longitude"], winner["cell_centroid_longitude"]),
            "cell_latitude": (output["cell_latitude"], winner["cell_centroid_latitude"]),
            "observation_count": (output["observation_count"], len(lineage)),
            "newest_observed_at": (
                output["newest_observed_at"],
                max(_as_utc(row["observed_at"], what="observed_at") for row in lineage),
            ),
        }
        if ROW_SHAPE == "soil_temperature":
            aligned |= {
                "physical_candidate_count": (output["physical_candidate_count"], len(lineage)),
                "selected_observation_id": (output["selected_observation_id"], winner["id"]),
                "selected_canonical_row_sha256": (
                    output["selected_canonical_row_sha256"],
                    winner["canonical_row_sha256"],
                ),
                "selected_source_release_id": (output["selected_source_release_id"], winner["source_release_id"]),
                "selected_release_retrieved_at": (
                    output["selected_release_retrieved_at"],
                    releases[str(winner["source_release_id"])]["retrieved_at"],
                ),
                "lineage_sha256": (
                    output["lineage_sha256"],
                    _lineage_digest([str(row["canonical_row_sha256"]) for row in lineage]),
                ),
                "input_manifest_sha256": (output["input_manifest_sha256"], SOURCE_MANIFEST_SHA256),
            }
        drift = {name: value for name, value in aligned.items() if value[0] != value[1]}
        if drift:
            raise BuildError(
                f"{ACTIVE_LANE} {observed_day}/{output['cell_id']} is not its lineage's element zero: {drift}"
            )
        if observed_day != winner["observation_day"]:
            raise BuildError(f"{ACTIVE_LANE} day {observed_day} holds a row observed on {winner['observation_day']}")
        physical_rows += len(lineage)
    return physical_rows


def _deduplicate_month(
    rows: Sequence[dict[str, Any]],
    *,
    month: str,
    data_source: Mapping[str, Any],
    releases: Mapping[str, Mapping[str, Any]],
) -> dict[date, DayBase]:
    """Collapse one month's eligible rows to one row per grain, newest release first.

    THE GRAIN AND THE PRECEDENCE ARE THE REFERENCE BUILDER'S, unchanged:
    `build_soil_moisture_from_canonical_snapshot.py:561-588` groups on
    (support_key, signal_name, normalized_unit, cell_id, observation_day) and orders each group by
    (source_release.retrieved_at, signal_observation.id) DESCENDING. The frozen soil-temperature
    builder groups on a seven-tuple that adds data_source_key and source_parameter
    (`soil_temperature_snapshot_breakdown.py:952-960`), which partitions identically here because
    `_classify_raw_row` has already refused every row whose partition path disagrees with those two.
    """
    groups: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            (
                row["support_key"],
                row["signal_name"],
                row["normalized_unit"],
                row["cell_id"],
                row["observation_day"],
            )
        ].append(row)

    outputs_by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    lineages_by_day: dict[date, list[list[dict[str, Any]]]] = defaultdict(list)
    for group_rows in groups.values():
        ranked: list[tuple[datetime, int, dict[str, Any], Mapping[str, Any]]] = []
        for row in group_rows:
            release_id = str(row["source_release_id"])
            release = releases.get(release_id)
            if release is None:
                raise BuildError(f"source release {release_id!r} is absent from the frozen dimension")
            if str(release["data_source_id"]) != str(data_source["id"]):
                raise BuildError(f"source release {release_id!r} does not belong to {SOURCE_KEY}")
            ranked.append((_as_utc(release.get("retrieved_at"), what="retrieved_at"), int(row["id"]), row, release))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        lineage = [item[2] for item in ranked]
        selected, selected_release = ranked[0][2], ranked[0][3]
        observed_day = selected["observation_day"]
        if not isinstance(observed_day, date):
            raise BuildError(f"selected {ACTIVE_LANE} row has no date-valued observation_day")
        outputs_by_day[observed_day].append(
            _output_row(selected, selected_release=selected_release, lineage=lineage, data_source=data_source)
        )
        lineages_by_day[observed_day].append(lineage)

    expected_days = _month_window_days(month)
    if set(outputs_by_day) != expected_days:
        missing = sorted(day.isoformat() for day in expected_days - set(outputs_by_day))
        unexpected = sorted(day.isoformat() for day in set(outputs_by_day) - expected_days)
        raise BuildError(f"{ACTIVE_LANE} {month} day set is not the snapshot window: {missing=}, {unexpected=}")

    schema = ACTIVE_SCHEMA.arrow_schema
    bases: dict[date, DayBase] = {}
    for observed_day, day_rows in outputs_by_day.items():
        if len(day_rows) != EXPECTED_CELLS_PER_DAY:
            raise BuildError(
                f"deduplicated {ACTIVE_LANE} {observed_day} has {len(day_rows)} cells, "
                f"expected {EXPECTED_CELLS_PER_DAY}"
            )
        physical_rows = _verify_lineage_alignment(
            observed_day, outputs=day_rows, lineages=lineages_by_day[observed_day], releases=releases
        )
        table = pa.Table.from_pylist(day_rows, schema=schema)
        if table.num_rows != EXPECTED_CELLS_PER_DAY:
            raise BuildError(f"Arrow conversion changed the {ACTIVE_LANE} row count on {observed_day}")
        bases[observed_day] = DayBase(observed_day=observed_day, table=table, lineage_row_count=physical_rows)
    return bases


def _month_window_days(month: str) -> set[date]:
    """Return the days of one observation month that fall inside this snapshot's published window."""
    year, month_number = (int(part) for part in month.split("-"))
    last_day_number = monthrange(year, month_number)[1]
    return {
        day
        for day in (date(year, month_number, number) for number in range(1, last_day_number + 1))
        if EXPECTED_FIRST_DAY <= day <= EXPECTED_LAST_DAY
    }


def _load_source_month(
    store: ImmutableS3,
    *,
    month: str,
    ledger_summaries: Sequence[Mapping[str, Any]],
    data_source: Mapping[str, Any],
    releases: Mapping[str, Mapping[str, Any]],
) -> SourceMonth:
    """Read one month's pinned source parts, reconcile every digest, and reduce it to day tables."""
    year, month_number = (int(part) for part in month.split("-"))
    all_rows: list[dict[str, Any]] = []
    eligible_rows: list[dict[str, Any]] = []
    excluded_rows: dict[str, int] = defaultdict(int)
    part_keys: list[str] = []
    physical_bytes = 0
    seen_ids: set[int] = set()
    for summary in ledger_summaries:
        ledger = _verified_ledger(store, summary)
        part = _source_part_for_ledger(ledger, year=year, month=month_number)
        key = str(part["key"])
        payload = _required_object(store, key)
        if len(payload) != int(part["byte_count"]) or _sha256(payload) != part["sha256"]:
            raise BuildError(f"source part {key!r} failed byte reconciliation")
        table = _load_parquet(payload, key=key)
        if not RAW_REQUIRED_COLUMNS.issubset(table.column_names):
            raise BuildError(f"source part {key!r} omits {sorted(RAW_REQUIRED_COLUMNS.difference(table.column_names))}")
        rows = table.to_pylist()
        if len(rows) != int(part["row_count"]) or _row_set_digest(rows) != part["row_digest"]:
            raise BuildError(f"source part {key!r} failed row-digest reconciliation")
        for row in rows:
            exclusion = _classify_raw_row(row, month=month)
            row_id = int(row["id"])
            if row_id in seen_ids:
                raise BuildError(f"physical {ACTIVE_LANE} row id {row_id} repeats within {month}")
            seen_ids.add(row_id)
            if exclusion is None:
                eligible_rows.append(row)
            else:
                excluded_rows[exclusion] += 1
        all_rows.extend(rows)
        part_keys.append(key)
        physical_bytes += len(payload)
    if excluded_rows:
        raise BuildError(
            f"{ACTIVE_LANE} {month} excluded {sum(excluded_rows.values())} physical rows {dict(excluded_rows)}; "
            "every frozen product manifest of this family records rejected_rows 0, so any exclusion is drift"
        )
    base_by_day = _deduplicate_month(eligible_rows, month=month, data_source=data_source, releases=releases)
    return SourceMonth(
        month=month,
        physical_rows=len(all_rows),
        eligible_rows=len(eligible_rows),
        excluded_rows=dict(sorted(excluded_rows.items())),
        physical_bytes=physical_bytes,
        source_part_keys=tuple(part_keys),
        base_by_day=base_by_day,
    )


def _write_payload(
    store: ImmutableS3,
    *,
    key: str,
    payload: bytes,
    row_count: int,
    kind: str,
    zoom: int,
    content_type: str,
    apply_writes: bool,
) -> ObjectReceipt:
    """Upload one object, or measure it and upload nothing: the ONLY branch in this file that writes."""
    if key.startswith(f"{ACTIVE_FROZEN_ROOT}/") or not key.startswith(LIVE_STREAM_PREFIX):
        raise BuildError(
            f"refusing to write {key!r}: this builder writes only under {LIVE_STREAM_PREFIX!r}, and never into "
            f"the closed frozen root {ACTIVE_FROZEN_ROOT!r}"
        )
    if apply_writes:
        store.put_immutable(key, payload, content_type=content_type)
    return ObjectReceipt(
        key=key,
        row_count=row_count,
        byte_count=len(payload),
        sha256=_sha256(payload),
        kind=kind,
        zoom=zoom,
        written=apply_writes,
    )


def _derived_rungs(base: pa.Table, *, observed_day: date) -> dict[ZoomTier, pa.Table]:
    """Derive the three coarse rungs of one day through the lane's own registered ladder."""
    source_frame = _require_frame(pl.from_arrow(base))
    derived: dict[ZoomTier, pa.Table] = {}
    for zoom in DERIVED_ZOOM_TIERS:
        frame = derive_tier(source_frame, stream=ACTIVE_LANE, tier=zoom)
        if frame.height != EXPECTED_TIER_ROWS_PER_DAY[zoom]:
            raise BuildError(
                f"{ACTIVE_LANE} {observed_day} z{zoom:02d} derived to {frame.height} rows, "
                f"expected {EXPECTED_TIER_ROWS_PER_DAY[zoom]}"
            )
        derived[zoom] = frame.to_arrow()
    return derived


def _write_day(
    store: ImmutableS3,
    *,
    base: DayBase,
    completed_at: datetime,
    apply_writes: bool,
) -> dict[str, Any]:
    """Write one day's four-rung ladder, each part before its own marker and the base marker last.

    THE ORDER IS THE REFERENCE BUILDER'S (`build_soil_moisture_from_canonical_snapshot.py:765-831`)
    and it is load-bearing: a day whose base rung has parts and no completion reads as `incomplete`,
    so a run killed mid-day leaves a day this script will redo rather than a day the lane claims.
    """
    observed_day = base.observed_day
    table = base.table
    if table.num_rows != EXPECTED_TIER_ROWS_PER_DAY[BASE_ZOOM_TIER]:
        raise BuildError(f"base {ACTIVE_LANE} day {observed_day} has {table.num_rows} rows")
    if not EXPECTED_FIRST_DAY <= observed_day <= EXPECTED_LAST_DAY:
        raise BuildError(f"{ACTIVE_LANE} {observed_day} is outside the published window and must not be written")

    objects: list[ObjectReceipt] = []
    base_payload = _serialize(table)
    objects.append(
        _write_payload(
            store,
            key=partition_path(ACTIVE_LANE, "observed", BASE_ZOOM_TIER, observed_day),
            payload=base_payload,
            row_count=table.num_rows,
            kind="part",
            zoom=BASE_ZOOM_TIER,
            content_type=PARQUET_CONTENT_TYPE,
            apply_writes=apply_writes,
        )
    )
    tier_rows: dict[str, int] = {str(BASE_ZOOM_TIER): table.num_rows}
    for zoom, derived_table in _derived_rungs(table, observed_day=observed_day).items():
        objects.append(
            _write_payload(
                store,
                key=partition_path(ACTIVE_LANE, "observed", zoom, observed_day),
                payload=_serialize(derived_table),
                row_count=derived_table.num_rows,
                kind="part",
                zoom=zoom,
                content_type=PARQUET_CONTENT_TYPE,
                apply_writes=apply_writes,
            )
        )
        objects.append(
            _write_payload(
                store,
                key=completion_marker_path(ACTIVE_LANE, "observed", zoom, observed_day),
                payload=_completion_marker(row_count=derived_table.num_rows, completed_at=completed_at),
                row_count=derived_table.num_rows,
                kind="completion",
                zoom=zoom,
                content_type=JSON_CONTENT_TYPE,
                apply_writes=apply_writes,
            )
        )
        tier_rows[str(zoom)] = derived_table.num_rows
    objects.append(
        _write_payload(
            store,
            key=completion_marker_path(ACTIVE_LANE, "observed", BASE_ZOOM_TIER, observed_day),
            payload=_completion_marker(row_count=table.num_rows, completed_at=completed_at),
            row_count=table.num_rows,
            kind="completion",
            zoom=BASE_ZOOM_TIER,
            content_type=JSON_CONTENT_TYPE,
            apply_writes=apply_writes,
        )
    )
    return {
        "day": observed_day.isoformat(),
        "physical_input_rows": base.lineage_row_count,
        "base_rows": table.num_rows,
        "base_digest": _base_row_digest(table.to_pylist()),
        "tier_rows": tier_rows,
        "objects": [receipt.to_json() for receipt in objects],
    }


def _completion_marker(*, row_count: int, completed_at: datetime) -> bytes:
    """Serialize a REAL completion receipt: five fields, schema_version 1, never a breakdown sidecar."""
    return PartitionCompletion(
        part_count=1,
        row_count=row_count,
        completed_at=completed_at,
        run_id=RUN_ID,
    ).to_json_bytes()


def _live_day_ladder(store: ImmutableS3) -> dict[date, LadderState]:
    """Census `layer=<lane>/kind=observed/`: which rungs of which days already hold a part and a marker."""
    parts: dict[date, set[int]] = defaultdict(set)
    completions: dict[date, set[int]] = defaultdict(set)
    derived_empties: dict[date, set[int]] = defaultdict(set)
    for key, _ in store.list_keys(LIVE_STREAM_PREFIX):
        partition = try_parse_partition_path(key)
        if partition is not None:
            if partition.layer != ACTIVE_LANE or partition.kind != "observed":
                raise BuildError(f"live prefix listing returned a foreign part: {key!r}")
            parts[partition.day].add(partition.zoom)
            continue
        completion = try_parse_completion_marker_path(key)
        if completion is not None:
            if completion.derived_empty:
                derived_empties[completion.day].add(completion.zoom)
            else:
                completions[completion.day].add(completion.zoom)
            continue
        if try_parse_absence_marker_path(key) is not None:
            continue
        # The availability index lives UNDER `kind=observed/` too, and it appears the moment a lane
        # is bootstrapped -- which is the step that immediately follows this builder. Refusing it
        # made a published lane uncensusable by the very script that had just built it, so a re-run
        # or a repair was impossible without deleting the index first. It is not part of the day
        # ladder, so skip it rather than refuse it.
        if f"/{AVAILABILITY_SEGMENT}/" in f"{key}/":
            continue
        raise BuildError(f"live prefix holds an object outside the frozen layout: {key!r}")
    return {
        day: LadderState(
            parts=frozenset(parts.get(day, ())),
            completions=frozenset(completions.get(day, ())),
            derived_empties=frozenset(derived_empties.get(day, ())),
        )
        for day in set(parts) | set(completions) | set(derived_empties)
    }


def _window_days() -> list[date]:
    """Return every day of the published snapshot window, in order."""
    return [EXPECTED_FIRST_DAY + timedelta(days=index) for index in range(EXPECTED_DAYS)]


def _plan_days(ladder: Mapping[date, LadderState]) -> dict[str, Any]:
    """Split the published window into the days already complete and the days this run still owes."""
    window = _window_days()
    if len(window) != EXPECTED_DAYS or window[0] != EXPECTED_FIRST_DAY or window[-1] != EXPECTED_LAST_DAY:
        raise BuildError("published window construction drifted from its pinned bounds")
    complete = [day for day in window if day in ladder and ladder[day].is_complete]
    completed = set(complete)
    owed = [day for day in window if day not in completed]
    partial = [
        day.isoformat()
        for day in window
        if day in ladder
        and not ladder[day].is_complete
        and (ladder[day].parts or ladder[day].completions or ladder[day].derived_empties)
    ]
    above_window = sorted(day for day in ladder if day > EXPECTED_LAST_DAY)
    below_window = sorted(day for day in ladder if day < EXPECTED_FIRST_DAY)
    return {
        "complete_days": complete,
        "owed_days": owed,
        "partial_days": partial,
        "days_above_window": [day.isoformat() for day in above_window],
        "days_below_window": [day.isoformat() for day in below_window],
    }


def _sample(days: Sequence[date], count: int) -> list[date]:
    """Pick up to `count` days spread evenly across an ordered day list."""
    if count <= 0 or not days:
        return []
    if count >= len(days):
        return list(days)
    step = (len(days) - 1) / (count - 1) if count > 1 else 0
    return sorted({days[round(index * step)] for index in range(count)})


def _audit_existing_day(store: ImmutableS3, base: DayBase) -> dict[str, Any]:
    """Rebuild one already-complete day and refuse if the stored base rung disagrees with it.

    Only the BASE rung is compared byte-for-value: it is where the grain, the precedence and every
    lineage column are decided, and the three coarse rungs are a pure function of it through the one
    shared `derive_tier`. Their row counts are still checked, by `_derived_rungs`, on every build.
    """
    key = partition_path(ACTIVE_LANE, "observed", BASE_ZOOM_TIER, base.observed_day)
    stored_payload = _required_object(store, key)
    stored = _load_parquet(stored_payload, key=key)
    rebuilt = _load_parquet(_serialize(base.table), key=f"rebuild:{key}")
    if stored.num_rows != rebuilt.num_rows:
        raise BuildError(
            f"existing {ACTIVE_LANE} day {base.observed_day} holds {stored.num_rows} rows, "
            f"this builder rebuilds {rebuilt.num_rows}"
        )
    if not stored.schema.equals(rebuilt.schema, check_metadata=False):
        raise BuildError(f"existing {ACTIVE_LANE} day {base.observed_day} has a different schema than this builder")
    stored_digest = _base_row_digest(stored.to_pylist())
    rebuilt_digest = _base_row_digest(rebuilt.to_pylist())
    if stored_digest != rebuilt_digest:
        raise BuildError(
            f"existing {ACTIVE_LANE} day {base.observed_day} was built to a different rule: "
            f"stored base digest {stored_digest}, rebuilt {rebuilt_digest}. Resuming would leave one lane "
            "holding two populations, so this run refuses rather than continuing it"
        )
    return {
        "day": base.observed_day.isoformat(),
        "key": key,
        "rows": stored.num_rows,
        "base_digest": stored_digest,
        "agrees": True,
    }


def _verify_written_lane(
    store: ImmutableS3,
    *,
    written_days: Sequence[date],
    expected_complete: Sequence[date],
) -> dict[str, Any]:
    """Re-census the live prefix and refuse unless every day this run wrote carries the full ladder."""
    ladder = _live_day_ladder(store)
    incomplete = [
        day.isoformat()
        for day in written_days
        if day not in ladder or ladder[day].parts != PUBLISHED_RUNGS or ladder[day].completions != PUBLISHED_RUNGS
    ]
    if incomplete:
        raise BuildError(f"{ACTIVE_LANE} wrote days that do not carry the four-rung ladder: {incomplete[:10]}")
    window_parts = dict.fromkeys(PUBLISHED_RUNGS, 0)
    window_completions = dict.fromkeys(PUBLISHED_RUNGS, 0)
    for day, state in ladder.items():
        if not EXPECTED_FIRST_DAY <= day <= EXPECTED_LAST_DAY:
            continue
        for zoom in state.parts:
            window_parts[zoom] += 1
        for zoom in state.completions:
            window_completions[zoom] += 1
    mismatched = {
        f"z{zoom:02d}": (window_parts[zoom], window_completions[zoom])
        for zoom in PUBLISHED_RUNGS
        if window_parts[zoom] != window_completions[zoom]
    }
    if mismatched:
        raise BuildError(f"{ACTIVE_LANE} in-window parts and markers disagree per rung: {mismatched}")
    complete_now = sorted(day for day, state in ladder.items() if state.is_complete and day <= EXPECTED_LAST_DAY)
    missing = sorted(set(expected_complete) - set(complete_now))
    if missing:
        raise BuildError(f"{ACTIVE_LANE} expected {len(expected_complete)} complete days, {len(missing)} are not")
    return {
        "window_parts_per_rung": {f"z{zoom:02d}": window_parts[zoom] for zoom in sorted(PUBLISHED_RUNGS)},
        "window_completions_per_rung": {f"z{zoom:02d}": window_completions[zoom] for zoom in sorted(PUBLISHED_RUNGS)},
        "window_complete_days": len(complete_now),
        "window_owed_days": EXPECTED_DAYS - len(complete_now),
    }


def _reconcile_loaded_months(loaded: Mapping[str, int], *, months_loaded: int) -> dict[str, Any]:
    """Reconcile what this run read against the totals every frozen manifest of this family pins.

    Two of the checks hold on ANY run: every physical row is eligible (all five frozen manifests
    record `rejected_rows` 0), and the base population is a whole number of 1,470-cell days. The
    four pinned totals can only be checked when the run happened to load all 53 months, so they are
    checked exactly then and reported as unchecked otherwise rather than quietly skipped.
    """
    physical = int(loaded["physical_rows"])
    eligible = int(loaded["eligible_rows"])
    base = int(loaded["base_rows"])
    if physical != eligible:
        raise BuildError(f"{ACTIVE_LANE} read {physical} physical rows but only {eligible} were eligible")
    if base % EXPECTED_CELLS_PER_DAY:
        raise BuildError(
            f"{ACTIVE_LANE} base population {base} is not a whole number of {EXPECTED_CELLS_PER_DAY}-cell days"
        )
    whole_snapshot = months_loaded == EXPECTED_MONTHS
    if whole_snapshot:
        expected = {
            "physical_rows": (physical, EXPECTED_PHYSICAL_ROWS),
            "eligible_rows": (eligible, EXPECTED_ELIGIBLE_ROWS),
            "base_rows": (base, EXPECTED_BASE_ROWS),
            "duplicates_collapsed": (eligible - base, EXPECTED_DUPLICATES_COLLAPSED),
            "days": (base // EXPECTED_CELLS_PER_DAY, EXPECTED_DAYS),
        }
        drift = {name: value for name, value in expected.items() if value[0] != value[1]}
        if drift:
            raise BuildError(f"{ACTIVE_LANE} snapshot-to-lane reconciliation drifted: {drift}")
    return {
        "months_loaded": months_loaded,
        "whole_snapshot": whole_snapshot,
        "physical_rows": physical,
        "eligible_rows": eligible,
        "base_rows": base,
        "duplicates_collapsed": eligible - base,
        "days": base // EXPECTED_CELLS_PER_DAY,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-export one ERA5-Land lane at day grain from the pinned snapshot.")
    parser.add_argument("--product", choices=tuple(PRODUCTS), required=True)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and measure every owed day, writing nothing. THE DEFAULT.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="the only flag that uploads anything to the object store",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=0,
        help="write at most this many owed days, oldest first; 0 means the whole owed block",
    )
    parser.add_argument(
        "--preview-days",
        type=int,
        default=DEFAULT_PREVIEW_DAYS,
        help="in a dry run, how many owed days to actually build and measure; 0 builds every owed day",
    )
    parser.add_argument(
        "--audit-existing-days",
        type=int,
        default=DEFAULT_AUDIT_EXISTING_DAYS,
        help="rebuild this many already-complete days and refuse if the stored lane disagrees",
    )
    arguments = parser.parse_args()
    if arguments.dry_run and arguments.apply:
        parser.error("--dry-run and --apply are mutually exclusive; a run without --apply is already a dry run")
    if not 1 <= arguments.workers <= 24:
        parser.error("--workers must be between 1 and 24")
    if arguments.max_days < 0 or arguments.preview_days < 0 or arguments.audit_existing_days < 0:
        parser.error("--max-days, --preview-days and --audit-existing-days cannot be negative")
    return arguments


def _selected_months(
    month_ledgers: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    build_days: Sequence[date],
    audit_days: Sequence[date],
) -> dict[str, tuple[set[date], set[date]]]:
    """Return only the months that hold a day this run must build or audit, oldest first."""
    wanted: dict[str, tuple[set[date], set[date]]] = {}
    for month in month_ledgers:
        year, month_number = (int(part) for part in month.split("-"))
        build = {day for day in build_days if (day.year, day.month) == (year, month_number)}
        audit = {day for day in audit_days if (day.year, day.month) == (year, month_number)}
        if build or audit:
            wanted[month] = (build, audit)
    return wanted


def main() -> int:
    arguments = _arguments()
    _activate(PRODUCTS[arguments.product])
    apply_writes = bool(arguments.apply)
    if not arguments.env_file.is_file():
        raise SystemExit(f"settings file does not exist: {arguments.env_file}")
    try:
        configured = Settings(_env_file=arguments.env_file)  # type: ignore[call-arg]
        store = ImmutableS3(configured.require_object_store(), configured.object_store_prefix)
        _verify_product_table()
        source_manifest, source_completion = _load_source_contract(store)
        frozen_product = _load_frozen_product_contract(store)
        source_inventory = store.list_keys(SOURCE_PART_PREFIX)
        source_bytes = sum(size for _, size in source_inventory)
        if (
            len(source_inventory) != EXPECTED_SOURCE_PARTS
            or source_bytes != ACTIVE_PRODUCT.expected_source_bytes
            or any(not key.endswith(".parquet") for key, _ in source_inventory)
        ):
            raise BuildError(
                f"source prefix holds {len(source_inventory)} objects and {source_bytes} bytes; expected "
                f"{EXPECTED_SOURCE_PARTS} parts and {ACTIVE_PRODUCT.expected_source_bytes} bytes"
            )
        data_source, releases = _load_dimensions(store, source_manifest)
        completed_at = _as_utc(
            datetime.fromisoformat(str(source_completion["completed_at"])), what="source _COMPLETE completed_at"
        )
        month_ledgers = _month_ledgers(source_manifest)

        plan = _plan_days(_live_day_ladder(store))
        complete_days: list[date] = plan["complete_days"]
        owed_days: list[date] = plan["owed_days"]
        build_days = owed_days[: arguments.max_days] if arguments.max_days else list(owed_days)
        if not apply_writes and arguments.preview_days:
            build_days = build_days[: arguments.preview_days]
        audit_days = _sample(complete_days, arguments.audit_existing_days)

        wanted = _selected_months(month_ledgers, build_days=build_days, audit_days=audit_days)
        day_results: list[dict[str, Any]] = []
        audit_results: list[dict[str, Any]] = []
        written_days: list[date] = []
        loaded = {"physical_rows": 0, "eligible_rows": 0, "base_rows": 0}
        for index, (month, (month_build, month_audit)) in enumerate(wanted.items(), start=1):
            source_month = _load_source_month(
                store,
                month=month,
                ledger_summaries=month_ledgers[month],
                data_source=data_source,
                releases=releases,
            )
            loaded["physical_rows"] += source_month.physical_rows
            loaded["eligible_rows"] += source_month.eligible_rows
            loaded["base_rows"] += sum(base.table.num_rows for base in source_month.base_by_day.values())
            audit_results.extend(
                _audit_existing_day(store, source_month.base_by_day[day]) for day in sorted(month_audit)
            )
            with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
                pending: dict[Future[dict[str, Any]], date] = {
                    executor.submit(
                        _write_day,
                        store,
                        base=source_month.base_by_day[day],
                        completed_at=completed_at,
                        apply_writes=apply_writes,
                    ): day
                    for day in sorted(month_build)
                }
                for future in as_completed(pending):
                    day_results.append(future.result())
                    written_days.append(pending[future])
            print(
                f"month {index}/{len(wanted)} {month} parts={len(source_month.source_part_keys)} "
                f"bytes={source_month.physical_bytes} physical={source_month.physical_rows} "
                f"eligible={source_month.eligible_rows} days={len(source_month.base_by_day)} "
                f"built={len(month_build)} audited={len(month_audit)} "
                f"{'written' if apply_writes else 'measured'}",
                file=sys.stderr,
                flush=True,
            )
        day_results.sort(key=lambda item: str(item["day"]))
        reconciliation = _reconcile_loaded_months(loaded, months_loaded=len(wanted))

        verification: dict[str, Any] | None = None
        if apply_writes:
            verification = _verify_written_lane(
                store,
                written_days=sorted(written_days),
                expected_complete=sorted({*complete_days, *written_days}),
            )
        report = {
            "status": "complete",
            "mode": "apply" if apply_writes else "dry-run",
            "lane": ACTIVE_LANE,
            "product": SOURCE_PARAMETER,
            "row_shape": ROW_SHAPE,
            "run_id": RUN_ID,
            "completed_at": completed_at.isoformat(),
            "source_manifest_key": SOURCE_MANIFEST_KEY,
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "source_part_count": len(source_inventory),
            "source_byte_count": source_bytes,
            "frozen_product": frozen_product,
            "precedence_contract": PRECEDENCE_CONTRACT,
            "window": {
                "first_day": EXPECTED_FIRST_DAY.isoformat(),
                "last_day": EXPECTED_LAST_DAY.isoformat(),
                "day_count": EXPECTED_DAYS,
                "cells_per_day": EXPECTED_CELLS_PER_DAY,
            },
            "census": {
                "complete_days": len(complete_days),
                "owed_days": len(owed_days),
                "owed_first_day": owed_days[0].isoformat() if owed_days else None,
                "owed_last_day": owed_days[-1].isoformat() if owed_days else None,
                "partial_days": len(plan["partial_days"]),
                "partial_day_sample": plan["partial_days"][:20],
                "days_above_window": len(plan["days_above_window"]),
                "days_below_window": len(plan["days_below_window"]),
            },
            "built_days": len(day_results),
            "months_loaded": len(wanted),
            "reconciliation": reconciliation,
            "objects_measured": sum(len(day["objects"]) for day in day_results),
            "objects_written": sum(1 for day in day_results for receipt in day["objects"] if bool(receipt["written"])),
            "tier_rows": {
                str(zoom): sum(int(day["tier_rows"][str(zoom)]) for day in day_results)
                for zoom in sorted(EXPECTED_TIER_ROWS_PER_DAY, reverse=True)
            },
            "existing_day_audits": audit_results,
            "verification": verification,
            "built_first_day": day_results[0]["day"] if day_results else None,
            "built_last_day": day_results[-1]["day"] if day_results else None,
            "built_day_digests": {str(day["day"]): str(day["base_digest"]) for day in day_results},
            # Per-object receipts for a bounded sample only: a full apply builds 1,110 days x 8
            # objects, and 8,880 receipts on stdout is a log nobody reads. The digest of every day
            # this run built is above it, which is what a later re-run compares against.
            "day_receipt_sample": day_results[:DEFAULT_PREVIEW_DAYS],
        }
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "mode": "apply" if apply_writes else "dry-run",
                    "lane": ACTIVE_LANE,
                    "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
                    "error": f"{type(error).__name__}: {error}",
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
