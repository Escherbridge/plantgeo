"""Bounded reads over manifest-closed immutable snapshot product prefixes."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.serving import WINDOW_ROW_BUDGET
from agri_data_service.parquet_ops.wire import (
    DayNotWritten,
    DayRange,
    LaneCoverage,
    LaneNeverWritten,
    PublishedDay,
    contiguous_ranges,
)
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.parquet_ops.wire import DayEnvelope, ServedRow
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend

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


SNAPSHOT_PRODUCTS: Final[tuple[SnapshotProduct, ...]] = (
    SnapshotProduct(
        "climate-field-air-temperature-mean",
        "monthly",
        _layer_root("climate-field-air-temperature-mean"),
        _layer_root("climate-field-air-temperature-mean"),
        schema_columns=SIGNAL_PRODUCT_COLUMNS,
        contract_version="plantgeo.air-temperature.snapshot-product.v1",
        coverage_cell_grid_name="nasa-power-0.5-degree",
        coverage_cells_per_day=397,
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
    ),
    SnapshotProduct(
        "climate-field-relative-humidity",
        "daily",
        _layer_root("climate-field-relative-humidity"),
        f"{_layer_root('climate-field-relative-humidity')}/_breakdown",
        contract_version="climate-field-relative-humidity.snapshot-breakdown.v1",
    ),
    SnapshotProduct(
        "climate-field-dew-point",
        "monthly",
        _layer_root("climate-field-dew-point"),
        _layer_root("climate-field-dew-point"),
        expected_manifest_sha256="c2972ea61ebfb66a86fa1e834625fae163e5d0a0abfd39f8c701edca3e59b71a",
        schema_columns=SIGNAL_PRODUCT_COLUMNS,
        contract_version="plantgeo.dew-point.snapshot-product.v1",
        coverage_cell_grid_name="nasa-power-0.5-degree",
        coverage_cells_per_day=397,
    ),
    SnapshotProduct(
        "climate-field-wind-speed",
        "daily",
        _layer_root("climate-field-wind-speed"),
        _layer_root("climate-field-wind-speed"),
        expected_manifest_sha256="7dced7e273ed8357cafc8388742b892074061dcd59cd9ffeff086f0cb95da13f",
        schema_columns=SIGNAL_PRODUCT_COLUMNS,
        contract_version="plantgeo.climate-field-wind-speed.snapshot.v1",
    ),
    SnapshotProduct(
        "soil-field-vpd",
        "monthly",
        _layer_root("soil-field-vpd"),
        _layer_root("soil-field-vpd"),
        schema_columns=SIGNAL_PRODUCT_COLUMNS,
        contract_version="plantgeo.vpd.snapshot-product.v1",
    ),
    SnapshotProduct(
        "soil-wetness-surface",
        "daily",
        _derived_lane_root("soil-wetness-surface"),
        _derived_lane_root("soil-wetness-surface"),
        expected_manifest_sha256="92f4486e5054495fc46ffc15cac558b03916c60f436c8eac0afcbdf0200d6565",
        schema_columns=SOIL_WETNESS_COLUMNS,
        contract_version="plantgeo.signal-product-breakdown.v1",
    ),
    SnapshotProduct(
        "soil-wetness-root-zone",
        "daily",
        _derived_lane_root("soil-wetness-root-zone"),
        _derived_lane_root("soil-wetness-root-zone"),
        expected_manifest_sha256="0cffc4832438a228d23e6de6fdd16ac038243ed302809b8143f939378ffbe948",
        schema_columns=SOIL_WETNESS_COLUMNS,
        contract_version="plantgeo.signal-product-breakdown.v1",
    ),
    SnapshotProduct(
        "soil-wetness-profile",
        "daily",
        _derived_lane_root("soil-wetness-profile"),
        _derived_lane_root("soil-wetness-profile"),
        expected_manifest_sha256="8f3a10450716b0fa71548721f245591d5001de35fc2a0a4a7b3d5ea3262f1347",
        schema_columns=SOIL_WETNESS_COLUMNS,
        contract_version="plantgeo.signal-product-breakdown.v1",
    ),
    SnapshotProduct(
        "soil-temperature-0-to-7cm",
        "monthly",
        _derived_lane_root("soil-temperature-0-to-7cm"),
        _derived_lane_root("soil-temperature-0-to-7cm"),
        expected_manifest_sha256="67216660bd64f938e883dd51eba0fc9c28afbdd3eaa79351862eb96f4d4e480f",
        schema_columns=SOIL_TEMPERATURE_COLUMNS,
        contract_version="plantgeo.signal-product-breakdown.v1",
    ),
    SnapshotProduct(
        "soil-temperature-7-to-28cm",
        "monthly",
        _derived_lane_root("soil-temperature-7-to-28cm"),
        _derived_lane_root("soil-temperature-7-to-28cm"),
        expected_manifest_sha256="0120ae2a9d6922b67861bf257b8c1b354a97e6cc0e889e146305ccd4e4a835d1",
        schema_columns=SOIL_TEMPERATURE_COLUMNS,
        contract_version="plantgeo.signal-product-breakdown.v1",
    ),
    SnapshotProduct(
        "soil-temperature-28-to-100cm",
        "monthly",
        _derived_lane_root("soil-temperature-28-to-100cm"),
        _derived_lane_root("soil-temperature-28-to-100cm"),
        expected_manifest_sha256="3cacd5856630dc252f4f71d6b7156ec98cb8125270743420c5d7fa692ca2ce34",
        schema_columns=SOIL_TEMPERATURE_COLUMNS,
        contract_version="plantgeo.signal-product-breakdown.v1",
    ),
    SnapshotProduct(
        "soil-temperature-100-to-255cm",
        "monthly",
        _derived_lane_root("soil-temperature-100-to-255cm"),
        _derived_lane_root("soil-temperature-100-to-255cm"),
        expected_manifest_sha256="d40aa7851877f2ab4f85b71b7e8a9ed7bb6c5c4d754e5f54ee8ec6b4237cb82f",
        schema_columns=SOIL_TEMPERATURE_COLUMNS,
        contract_version="plantgeo.signal-product-breakdown.v1",
    ),
)

PRODUCT_BY_LAYER: Final = {product.layer: product for product in SNAPSHOT_PRODUCTS}


class SnapshotStore(Protocol):
    """The bounded object operations an immutable snapshot read needs."""

    def cache_identity(self) -> tuple[object, ...]: ...

    def iter_keys(self, relative_prefix: str) -> Iterator[str]: ...

    def read_object(self, relative_key: str) -> bytes | None: ...

    def relative_key(self, persisted_key: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ObjectStoreSnapshotStore:
    """A relative-key snapshot store over the configured object-store backend."""

    backend: ObjectStoreBackend
    prefix: str = ""

    def cache_identity(self) -> tuple[object, ...]:
        """Identify one process-held backend namespace across per-request wrappers."""
        return ("object-store", id(self.backend), getattr(self.backend, "bucket", None), self.prefix)

    def key_for(self, relative_key: str) -> str:
        return f"{self.prefix}{relative_key}"

    def iter_keys(self, relative_prefix: str) -> Iterator[str]:
        found = 0
        absolute_prefix = self.key_for(relative_prefix)
        for listed in self.backend.list_objects(absolute_prefix):
            if not listed.key.startswith(self.prefix):
                continue
            relative = listed.key[len(self.prefix) :]
            if not relative.startswith(relative_prefix):
                continue
            found += 1
            if found > MAX_SNAPSHOT_KEYS:
                raise faults.census_budget_exhausted(listed_keys=MAX_SNAPSHOT_KEYS)
            yield relative

    def read_object(self, relative_key: str) -> bytes | None:
        return self.backend.get(self.key_for(relative_key))

    def relative_key(self, persisted_key: str) -> str:
        if self.prefix and persisted_key.startswith(self.prefix):
            return persisted_key[len(self.prefix) :]
        return persisted_key


@dataclass(frozen=True, slots=True)
class SnapshotEvidence:
    """A closed snapshot plus the exact serving objects found under its allowlisted root."""

    product: SnapshotProduct
    manifest: Mapping[str, object]
    keys: tuple[str, ...]
    parts_by_tier: Mapping[int, tuple[str, ...]]
    part_receipts: Mapping[str, SnapshotObjectReceipt]


@dataclass(frozen=True, slots=True)
class SnapshotObjectReceipt:
    """One serving object whose identity is transitively bound to the closed manifest."""

    key: str
    byte_count: int
    sha256: str
    row_count: int | None = None


@dataclass(frozen=True, slots=True)
class SnapshotCoverageWithholding:
    """Why one product is absent from coverage without being called never-written."""

    layer: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SnapshotCoverageCensus:
    """Healthy rung evidence plus exact product-local reasons withheld from the frozen wire."""

    lanes: tuple[LaneCoverage, ...]
    withheld: tuple[SnapshotCoverageWithholding, ...]


type EvidenceCacheKey = tuple[tuple[object, ...], SnapshotProduct, str]

_EVIDENCE_CACHE: dict[EvidenceCacheKey, SnapshotEvidence] = {}
_EVIDENCE_LOCKS: dict[EvidenceCacheKey, threading.Lock] = {}
_EVIDENCE_CACHE_LOCK = threading.Lock()


def product_for_layer(layer: str) -> SnapshotProduct:
    """Resolve only a server-side allowlisted layer; callers never select an arbitrary prefix."""
    product = PRODUCT_BY_LAYER.get(layer)
    if product is None:
        raise faults.snapshot_unpublished(layer=layer, snapshot_id=SNAPSHOT_ID, detail="layer is not allowlisted")
    return product


def load_snapshot_evidence(store: SnapshotStore, product: SnapshotProduct) -> SnapshotEvidence:
    """Bind manifest bytes to `_COMPLETE`, then retain only exact serving-part paths."""
    manifest_key = f"{product.metadata_root}/manifest.json"
    complete_key = f"{product.metadata_root}/_COMPLETE"
    manifest_payload = _required_json_bytes(store, manifest_key, product)
    complete = _json_object(_required_json_bytes(store, complete_key, product), key=complete_key, product=product)
    digest = hashlib.sha256(manifest_payload).hexdigest()
    bound_manifest_key = complete.get("manifest_key")
    if (
        not isinstance(bound_manifest_key, str)
        or store.relative_key(bound_manifest_key) != manifest_key
        or complete.get("manifest_sha256") != digest
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="_COMPLETE does not bind the exact allowlisted manifest",
        )
    if product.expected_manifest_sha256 is not None and digest != product.expected_manifest_sha256:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest checksum differs from the pinned production receipt",
        )
    cache_key = (store.cache_identity(), product, digest)
    with _EVIDENCE_CACHE_LOCK:
        held = _EVIDENCE_CACHE.get(cache_key)
        lock = _EVIDENCE_LOCKS.setdefault(cache_key, threading.Lock())
    if held is not None:
        return held
    with lock:
        with _EVIDENCE_CACHE_LOCK:
            held = _EVIDENCE_CACHE.get(cache_key)
        if held is not None:
            return held
        evidence = _load_snapshot_evidence_uncached(store, product, manifest_payload, manifest_key=manifest_key)
        with _EVIDENCE_CACHE_LOCK:
            if len(_EVIDENCE_CACHE) >= MAX_EVIDENCE_CACHE_ENTRIES:
                _EVIDENCE_CACHE.clear()
                _EVIDENCE_LOCKS.clear()
                _EVIDENCE_LOCKS[cache_key] = lock
            _EVIDENCE_CACHE[cache_key] = evidence
        return evidence


def _load_snapshot_evidence_uncached(
    store: SnapshotStore,
    product: SnapshotProduct,
    manifest_payload: bytes,
    *,
    manifest_key: str,
) -> SnapshotEvidence:
    manifest = _json_object(manifest_payload, key=manifest_key, product=product)
    _verify_manifest_identity(manifest, product)
    part_receipts: Mapping[str, SnapshotObjectReceipt]
    if product.layout == "monthly":
        part_receipts = _monthly_serving_receipts(store, manifest, product)
    else:
        part_receipts = _daily_serving_receipts(store, manifest, product)
    keys = tuple(sorted(part_receipts))
    matcher = _DAILY_PART if product.layout == "daily" else _MONTHLY_PART
    parts: dict[int, list[str]] = {tier: [] for tier in ZOOM_TIERS}
    for key in keys:
        matched = matcher.search(key)
        if matched is None or not key.startswith(f"{product.data_root}/"):
            continue
        parts[int(matched.group("zoom"))].append(key)
    if not all(parts.values()):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest evidence does not bind one serving population at every required tier",
        )
    return SnapshotEvidence(
        product=product,
        manifest=manifest,
        keys=keys,
        parts_by_tier={tier: tuple(values) for tier, values in parts.items()},
        part_receipts=part_receipts,
    )


def clear_snapshot_evidence_cache() -> None:
    """Clear process-lifetime immutable evidence, used by tests and explicit lifecycle resets."""
    with _EVIDENCE_CACHE_LOCK:
        _EVIDENCE_CACHE.clear()
        _EVIDENCE_LOCKS.clear()


def resolve_snapshot_product(
    store: SnapshotStore,
    session: ServingSession,
    *,
    scope: ReadScope,
    day: date,
) -> DayEnvelope:
    """Serve one exact observed day from a closed daily-series snapshot product."""
    evidence = load_snapshot_scope_evidence(store, scope)
    return resolve_snapshot_evidence_day(store, session, evidence=evidence, scope=scope, day=day)


def load_snapshot_scope_evidence(store: SnapshotStore, scope: ReadScope) -> SnapshotEvidence:
    """Resolve one observed scope's immutable evidence without admitting a DuckDB session."""
    _require_observed_scope(scope)
    return load_snapshot_evidence(store, product_for_layer(scope.layer))


def resolve_snapshot_evidence_day(
    store: SnapshotStore,
    session: ServingSession,
    *,
    evidence: SnapshotEvidence,
    scope: ReadScope,
    day: date,
) -> DayEnvelope:
    """Serve an exact day after immutable evidence was resolved before session admission."""
    _require_matching_observed_scope(evidence, scope)
    return _resolve_snapshot_evidence(store, session, evidence=evidence, scope=scope, day=day)


def _require_matching_observed_scope(evidence: SnapshotEvidence, scope: ReadScope) -> None:
    """Keep pre-admitted immutable evidence bound to the exact requested product."""
    _require_observed_scope(scope)
    if scope.layer != evidence.product.layer:
        raise faults.snapshot_unpublished(
            layer=scope.layer,
            snapshot_id=evidence.product.snapshot_id,
            detail="pre-admitted snapshot evidence belongs to a different allowlisted layer",
        )


def _require_observed_scope(scope: ReadScope) -> None:
    """Reject unsupported snapshot streams before any immutable evidence read."""
    if scope.kind != "observed":
        raise faults.snapshot_unpublished(
            layer=scope.layer,
            snapshot_id=SNAPSHOT_ID,
            detail="immutable product snapshots publish observed rows only",
        )


def resolve_snapshot_window(
    store: SnapshotStore,
    session: ServingSession,
    *,
    scope: ReadScope,
    first_day: date,
    last_day: date,
) -> tuple[DayEnvelope, ...]:
    """Resolve a bounded closed range while binding the immutable manifest only once."""
    evidence = load_snapshot_scope_evidence(store, scope)
    return resolve_snapshot_evidence_window(
        store,
        session,
        evidence=evidence,
        scope=scope,
        first_day=first_day,
        last_day=last_day,
    )


def resolve_snapshot_evidence_window(  # noqa: PLR0913 - evidence and closed-range bounds are distinct
    store: SnapshotStore,
    session: ServingSession,
    *,
    evidence: SnapshotEvidence,
    scope: ReadScope,
    first_day: date,
    last_day: date,
) -> tuple[DayEnvelope, ...]:
    """Resolve a range after immutable evidence was resolved before session admission."""
    _require_matching_observed_scope(evidence, scope)
    verified_parts: set[str] = set()
    envelopes: list[DayEnvelope] = []
    remaining_rows = WINDOW_ROW_BUDGET
    truncated_from: date | None = None
    for offset in range((last_day - first_day).days + 1):
        day = first_day + timedelta(days=offset)
        budget_was_exhausted = remaining_rows == 0
        envelope = _resolve_snapshot_evidence(
            store,
            session,
            evidence=evidence,
            scope=scope,
            day=day,
            verified_parts=verified_parts,
            row_budget=min(SNAPSHOT_ROW_BUDGET, remaining_rows),
        )
        if isinstance(envelope, PublishedDay):
            remaining_rows -= len(envelope.rows)
            if envelope.truncated and truncated_from is None:
                truncated_from = day
            if budget_was_exhausted and truncated_from is None:
                truncated_from = day
            if truncated_from is not None and day >= truncated_from and not envelope.truncated:
                envelope = PublishedDay(
                    requested_day=envelope.requested_day,
                    served_day=envelope.served_day,
                    rows=envelope.rows,
                    truncated=True,
                )
        envelopes.append(envelope)
    return tuple(envelopes)


def _resolve_snapshot_evidence(  # noqa: PLR0913 - exact evidence, request, and shared-window budget are distinct
    store: SnapshotStore,
    session: ServingSession,
    *,
    evidence: SnapshotEvidence,
    scope: ReadScope,
    day: date,
    verified_parts: set[str] | None = None,
    row_budget: int = SNAPSHOT_ROW_BUDGET,
) -> DayEnvelope:
    tier_parts = evidence.parts_by_tier[int(scope.tier)]
    if not tier_parts:
        return LaneNeverWritten(requested_day=day)
    if evidence.product.layout == "daily":
        selected_parts = _daily_parts(tier_parts, day=day, product=evidence.product)
        _verify_bound_parts(store, evidence, selected_parts, verified=verified_parts)
    else:
        selected_parts = _parts_for_month(tier_parts, day.replace(day=1))
        _verify_bound_parts(store, evidence, selected_parts, verified=verified_parts)
        if selected_parts and not _month_has_day(session, selected_parts, day=day):
            selected_parts = ()
    if not selected_parts:
        return DayNotWritten(requested_day=day)
    if len(selected_parts) > MAX_SNAPSHOT_READ_PARTS:
        raise faults.snapshot_unpublished(
            layer=evidence.product.layer,
            snapshot_id=evidence.product.snapshot_id,
            detail=f"exact-day read exceeds the {MAX_SNAPSHOT_READ_PARTS}-part serving limit",
        )
    _verify_exact_schemas(session, evidence, selected_parts)
    rows, truncated = _read_observed_day(
        session,
        keys=selected_parts,
        observed_day=day,
        scope=scope,
        row_budget=row_budget,
    )
    return PublishedDay(
        requested_day=day,
        served_day=day,
        rows=rows,
        truncated=truncated,
    )


def build_snapshot_coverage(
    store: SnapshotStore,
) -> SnapshotCoverageCensus:
    """Prove each product independently through bounded metadata-only evidence."""
    rows: list[LaneCoverage] = []
    withheld: list[SnapshotCoverageWithholding] = []
    worker_count = min(SNAPSHOT_COVERAGE_PRODUCT_WORKERS, len(SNAPSHOT_PRODUCTS))
    if worker_count == 0:
        return SnapshotCoverageCensus(lanes=(), withheld=())
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        evidence_results = executor.map(
            lambda product: _coverage_evidence_or_withholding(store, product),
            SNAPSHOT_PRODUCTS,
        )
        ordered_results = tuple(evidence_results)
    for product, loaded in zip(SNAPSHOT_PRODUCTS, ordered_results, strict=True):
        if isinstance(loaded, SnapshotCoverageWithholding):
            withheld.append(loaded)
            continue
        try:
            rows.extend(_build_product_coverage(loaded))
        except faults.ServingRefusalError as exc:
            withheld.append(SnapshotCoverageWithholding(layer=product.layer, code=exc.code, message=exc.message))
    return SnapshotCoverageCensus(lanes=tuple(rows), withheld=tuple(withheld))


def _coverage_evidence_or_withholding(
    store: SnapshotStore,
    product: SnapshotProduct,
) -> SnapshotEvidence | SnapshotCoverageWithholding:
    try:
        return load_snapshot_evidence(store, product)
    except faults.ServingRefusalError as exc:
        return SnapshotCoverageWithholding(layer=product.layer, code=exc.code, message=exc.message)


def _build_product_coverage(
    evidence: SnapshotEvidence,
) -> tuple[LaneCoverage, ...]:
    """Build all four rungs for one product or raise one product-local typed refusal."""
    rows: list[LaneCoverage] = []
    product = evidence.product
    declared_days = _declared_contiguous_days(evidence)
    if product.layout == "monthly":
        _require_monthly_tier_parity(evidence)
        if declared_days is None:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly manifest evidence does not prove an exact daily coverage range",
            )
        shared_days = declared_days
    else:
        shared_days = set()
    for tier in ZOOM_TIERS:
        tier_parts = evidence.parts_by_tier[tier]
        days = _daily_days(tier_parts, product=product) if product.layout == "daily" else shared_days
        if product.layout == "daily" and declared_days is not None and days != declared_days:
            raise faults.snapshot_schema_mismatch(
                layer=product.layer,
                key=product.data_root,
                detail=f"tier z{tier:02d} day paths do not equal the manifest's closed day range",
            )
        ranges = contiguous_ranges(days)
        rows.append(
            LaneCoverage(
                layer=product.layer,
                nature="daily_series",
                kind="observed",
                zoom=tier,
                earliest_day=min(days) if days else None,
                latest_day=max(days) if days else None,
                published_ranges=ranges,
                gap_ranges=_gap_ranges(days),
                governed_absence_ranges=(),
            )
        )
    return tuple(rows)


class SnapshotCoverageCache:
    """One single-flight immutable-product census, separate from direct-lane coverage."""

    def __init__(self, ttl_seconds: int = SNAPSHOT_COVERAGE_CACHE_SECONDS) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._held: tuple[datetime, SnapshotCoverageCensus] | None = None
        self._lock = threading.Lock()

    def get(
        self,
        store: SnapshotStore,
        *,
        now: datetime,
    ) -> SnapshotCoverageCensus:
        held = self._fresh(now)
        if held is not None:
            return held
        with self._lock:
            held = self._fresh(now)
            if held is not None:
                return held
            built = build_snapshot_coverage(store)
            self._held = (now, built)
            return built

    def clear(self) -> None:
        self._held = None

    def _fresh(self, now: datetime) -> SnapshotCoverageCensus | None:
        held = self._held
        if held is not None and now - held[0] < self._ttl:
            return held[1]
        return None


def _required_json_bytes(store: SnapshotStore, key: str, product: SnapshotProduct) -> bytes:
    payload = store.read_object(key)
    if payload is None or not payload or len(payload) > MAX_MANIFEST_BYTES:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{key} is absent, empty, or over the metadata byte limit",
        )
    return payload


def _json_object(payload: bytes, *, key: str, product: SnapshotProduct) -> Mapping[str, object]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{key} is not valid JSON",
        ) from exc
    if not isinstance(value, dict):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{key} is not a JSON object",
        )
    return value


def _verify_manifest_identity(manifest: Mapping[str, object], product: SnapshotProduct) -> None:
    if product.contract_version is not None and manifest.get("contract_version") != product.contract_version:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest contract version is not the allowlisted builder contract",
        )
    snapshot_values = tuple(
        manifest.get(name) for name in ("snapshot_id", "source_snapshot_id", "input_snapshot_id") if name in manifest
    )
    if not snapshot_values or any(value != product.snapshot_id for value in snapshot_values):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest does not bind the allowlisted snapshot id",
        )
    identity_values: list[str] = []
    for name in ("lane", "destination_prefix", "destination_root", "lane_prefix"):
        value = manifest.get(name)
        if isinstance(value, str):
            identity_values.append(value)
    product_block = manifest.get("product")
    if isinstance(product_block, dict):
        identity_values.extend(str(product_block[name]) for name in ("stream", "lane") if name in product_block)
    if not any(value == product.layer or value.rstrip("/") == product.data_root for value in identity_values):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest does not bind the allowlisted layer and data root",
        )


def _daily_serving_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    """Resolve each supported daily builder through its persisted cryptographic receipt family."""
    if isinstance(manifest.get("serving_parts"), list):
        return _direct_daily_receipts(store, manifest, product)
    if isinstance(manifest.get("month_checkpoints"), list):
        return _relative_humidity_receipts(store, manifest, product)
    if "verification_marker_digest" in manifest:
        return _verified_lane_daily_receipts(store, manifest, product)
    raise faults.snapshot_unpublished(
        layer=product.layer,
        snapshot_id=product.snapshot_id,
        detail="daily manifest has no supported serving receipt inventory",
    )


def _direct_daily_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_parts = manifest.get("serving_parts")
    if not isinstance(raw_parts, list) or not raw_parts or len(raw_parts) > MAX_SNAPSHOT_KEYS:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily serving receipt inventory is absent or over the object limit",
        )
    expected_count = manifest.get("serving_part_count")
    if not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count != len(raw_parts):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily serving receipt count differs from the closed manifest",
        )
    result: dict[str, SnapshotObjectReceipt] = {}
    for raw in raw_parts:
        receipt = _object_receipt(store, raw, product, context="daily manifest serving part")
        _verify_daily_part_identity(receipt, product)
        if receipt.key in result:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"daily manifest repeats serving part {receipt.key}",
            )
        result[receipt.key] = receipt
    return result


def _relative_humidity_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_checkpoints = manifest.get("month_checkpoints")
    if not isinstance(raw_checkpoints, list) or not raw_checkpoints:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="relative-humidity manifest has no monthly checkpoint receipts",
        )
    jobs: list[tuple[str, str, str]] = []
    months: set[str] = set()
    for raw in raw_checkpoints:
        if not isinstance(raw, Mapping):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="relative-humidity checkpoint summary is not an object",
            )
        raw_key = raw.get("key")
        raw_sha256 = raw.get("sha256")
        month = raw.get("month")
        if (
            not isinstance(raw_key, str)
            or not isinstance(raw_sha256, str)
            or _SHA256.fullmatch(raw_sha256) is None
            or not isinstance(month, str)
            or re.fullmatch(r"\d{4}-\d{2}", month) is None
            or month in months
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="relative-humidity checkpoint summary has an invalid key, month, or SHA-256",
            )
        key = store.relative_key(raw_key)
        expected_key = f"{product.metadata_root}/_checkpoints/month={month}.json"
        if key != expected_key:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"relative-humidity checkpoint escaped its allowlisted month: {key}",
            )
        months.add(month)
        jobs.append((month, key, raw_sha256))

    def verify_checkpoint(job: tuple[str, str, str]) -> tuple[str, tuple[SnapshotObjectReceipt, ...]]:
        month, key, raw_sha256 = job
        payload = store.read_object(key)
        if (
            payload is None
            or not payload
            or len(payload) > MAX_MANIFEST_BYTES
            or hashlib.sha256(payload).hexdigest() != raw_sha256
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"relative-humidity checkpoint receipt no longer matches {key}",
            )
        checkpoint = _json_object(payload, key=key, product=product)
        if (
            checkpoint.get("contract_version") != product.contract_version
            or checkpoint.get("source_snapshot_id") != product.snapshot_id
            or checkpoint.get("lane") != product.layer
            or checkpoint.get("month") != month
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"relative-humidity checkpoint identity differs for {month}",
            )
        days = checkpoint.get("days")
        if not isinstance(days, list):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"relative-humidity checkpoint {month} has no day inventory",
            )
        checkpoint_parts: list[SnapshotObjectReceipt] = []
        for raw_day in days:
            if not isinstance(raw_day, Mapping) or not isinstance(raw_day.get("objects"), list):
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"relative-humidity checkpoint {month} has an invalid day receipt",
                )
            for raw_part in raw_day["objects"]:
                if not isinstance(raw_part, Mapping) or raw_part.get("kind") != "part":
                    continue
                receipt = _object_receipt(store, raw_part, product, context="relative-humidity day part")
                _verify_daily_part_identity(receipt, product, expected_day=str(raw_day.get("day")))
                checkpoint_parts.append(receipt)
        return month, tuple(checkpoint_parts)

    result: dict[str, SnapshotObjectReceipt] = {}
    with ThreadPoolExecutor(max_workers=min(METADATA_VERIFY_WORKERS, len(jobs))) as executor:
        verified = executor.map(verify_checkpoint, jobs)
        for _month, receipts in verified:
            for receipt in receipts:
                if receipt.key in result:
                    raise faults.snapshot_unpublished(
                        layer=product.layer,
                        snapshot_id=product.snapshot_id,
                        detail=f"relative-humidity checkpoints repeat serving part {receipt.key}",
                    )
                result[receipt.key] = receipt
    return result


def _verified_lane_daily_receipts(  # noqa: PLR0912, PLR0915 - fail-closed receipt graph validation
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_count = manifest.get("verification_marker_count")
    raw_digest = manifest.get("verification_marker_digest")
    if (
        not isinstance(raw_count, int)
        or isinstance(raw_count, bool)
        or raw_count <= 0
        or raw_count > MAX_MONTHLY_RECEIPT_OBJECTS
        or not isinstance(raw_digest, str)
        or _SHA256.fullmatch(raw_digest) is None
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily lane manifest has an invalid verification-marker census",
        )
    marker_keys = tuple(sorted(store.iter_keys(f"{product.data_root}/_verification/")))
    if len(marker_keys) != raw_count:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily lane verification-marker inventory differs from its manifest",
        )

    def verify_marker(
        marker_key: str,
    ) -> tuple[str, tuple[str, str], Mapping[str, object]]:
        matched = _VERIFICATION_MARKER.search(marker_key)
        if matched is None or not marker_key.startswith(f"{product.data_root}/_verification/"):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verification marker escaped the allowlisted lane: {marker_key}",
            )
        marker_payload = store.read_object(marker_key)
        if marker_payload is None or not marker_payload or len(marker_payload) > MAX_MANIFEST_BYTES:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verification marker is absent or over budget: {marker_key}",
            )
        marker_sha256 = hashlib.sha256(marker_payload).hexdigest()
        marker_line = f"{marker_key}:{len(marker_payload)}:{marker_sha256}"
        marker = _json_object(marker_payload, key=marker_key, product=product)
        phase = matched.group("phase")
        month = f"{matched.group('year')}-{matched.group('month')}"
        if (
            marker.get("contract_version") != product.contract_version
            or marker.get("lane") != product.layer
            or marker.get("phase") != phase
            or marker.get("observation_month") != month
            or store.relative_key(str(marker.get("marker_key"))) != marker_key
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verification marker identity differs from its path: {marker_key}",
            )
        checkpoint_receipt = _checkpoint_receipt_from_marker(store, marker, product)
        checkpoint_payload = _read_bound_receipt(store, checkpoint_receipt, product, metadata=True)
        checkpoint = _json_object(checkpoint_payload, key=checkpoint_receipt.key, product=product)
        _verify_checkpoint_identity(checkpoint, product, month=month)
        if _checkpoint_output_digest(checkpoint, product) != marker.get("output_receipt_digest"):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verification marker does not bind checkpoint outputs: {marker_key}",
            )
        identity = (phase, month)
        return marker_line, identity, checkpoint

    marker_lines: list[str] = []
    checkpoints: dict[tuple[str, str], Mapping[str, object]] = {}
    with ThreadPoolExecutor(max_workers=min(METADATA_VERIFY_WORKERS, len(marker_keys))) as executor:
        verified = executor.map(verify_marker, marker_keys)
        for marker_line, identity, checkpoint in verified:
            marker_lines.append(marker_line)
            if identity in checkpoints:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"daily lane repeats verification phase/month {identity}",
                )
            checkpoints[identity] = checkpoint
    if _lineage_digest(marker_lines) != raw_digest:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily lane verification-marker digest differs from its manifest",
        )
    months = {month for phase, month in checkpoints if phase == "base"}
    if not months or months != {month for phase, month in checkpoints if phase == "tiers"}:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily lane base and tier verification months differ",
        )
    result: dict[str, SnapshotObjectReceipt] = {}
    for month in sorted(months):
        base = checkpoints[("base", month)]
        tiers = checkpoints[("tiers", month)]
        base_parts: dict[str, SnapshotObjectReceipt] = {}
        raw_base_parts = base.get("day_parts")
        if not isinstance(raw_base_parts, list):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"daily lane base checkpoint {month} has no day parts",
            )
        base_outputs = _checkpoint_output_receipts(store, base, product)
        for raw_part in raw_base_parts:
            receipt = _object_receipt(store, raw_part, product, context="daily lane base part")
            day = str(raw_part.get("day")) if isinstance(raw_part, Mapping) else ""
            _verify_daily_part_identity(receipt, product, expected_day=day, expected_tier=BASE_ZOOM_TIER)
            if base_outputs.get(receipt.key) != receipt or day in base_parts:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"daily lane base receipt differs from checkpoint output inventory: {receipt.key}",
                )
            base_parts[day] = receipt
        tier_outputs = _checkpoint_output_receipts(store, tiers, product)
        raw_days = tiers.get("days")
        if not isinstance(raw_days, list):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"daily lane tier checkpoint {month} has no day inventory",
            )
        seen_days: set[str] = set()
        for raw_day in raw_days:
            if not isinstance(raw_day, Mapping) or not isinstance(raw_day.get("tiers"), Mapping):
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"daily lane tier checkpoint {month} has an invalid day",
                )
            day = str(raw_day.get("day"))
            raw_tiers = raw_day["tiers"]
            if day in seen_days or set(raw_tiers) != {str(tier) for tier in ZOOM_TIERS}:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"daily lane tier checkpoint repeats a day or omits a rung: {day}",
                )
            seen_days.add(day)
            for tier in ZOOM_TIERS:
                receipt = _object_receipt(store, raw_tiers[str(tier)], product, context="daily lane tier part")
                _verify_daily_part_identity(receipt, product, expected_day=day, expected_tier=tier)
                bound = base_parts.get(day) if tier == BASE_ZOOM_TIER else tier_outputs.get(receipt.key)
                if bound != receipt or receipt.key in result:
                    raise faults.snapshot_unpublished(
                        layer=product.layer,
                        snapshot_id=product.snapshot_id,
                        detail=f"daily lane tier receipt differs from its verified checkpoint: {receipt.key}",
                    )
                result[receipt.key] = receipt
        if seen_days != set(base_parts):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"daily lane base and tier day inventories differ for {month}",
            )
    return result


def _checkpoint_receipt_from_marker(
    store: SnapshotStore,
    marker: Mapping[str, object],
    product: SnapshotProduct,
) -> SnapshotObjectReceipt:
    value = {
        "key": marker.get("checkpoint_key"),
        "byte_count": marker.get("checkpoint_byte_count"),
        "sha256": marker.get("checkpoint_sha256"),
    }
    return _object_receipt(store, value, product, context="verification marker checkpoint")


def _checkpoint_output_receipts(
    store: SnapshotStore,
    checkpoint: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_outputs = checkpoint.get("output_objects")
    if not isinstance(raw_outputs, list):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="verified checkpoint has no output receipt inventory",
        )
    result: dict[str, SnapshotObjectReceipt] = {}
    for raw in raw_outputs:
        receipt = _object_receipt(store, raw, product, context="checkpoint output")
        if receipt.key in result:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verified checkpoint repeats output receipt {receipt.key}",
            )
        result[receipt.key] = receipt
    return result


def _checkpoint_output_digest(checkpoint: Mapping[str, object], product: SnapshotProduct) -> str:
    raw_outputs = checkpoint.get("output_objects")
    if not isinstance(raw_outputs, list):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="verified checkpoint has no output receipt inventory",
        )
    lines: list[str] = []
    for raw in raw_outputs:
        receipt = _object_receipt(_IdentityStore(), raw, product, context="checkpoint output")
        raw_rows = raw.get("row_count") if isinstance(raw, Mapping) else None
        if not isinstance(raw_rows, int) or isinstance(raw_rows, bool) or raw_rows < 0:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"checkpoint output has an invalid row count: {receipt.key}",
            )
        lines.append(f"{receipt.key}:{raw_rows}:{receipt.byte_count}:{receipt.sha256}")
    return _lineage_digest(lines)


class _IdentityStore:
    """Normalize already-relative checkpoint receipts without object access."""

    def cache_identity(self) -> tuple[object, ...]:
        return ("identity-only",)

    def iter_keys(self, relative_prefix: str) -> Iterator[str]:
        del relative_prefix
        return iter(())

    def read_object(self, relative_key: str) -> bytes | None:
        del relative_key
        return None

    def relative_key(self, persisted_key: str) -> str:
        return persisted_key


def _lineage_digest(values: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _verify_daily_part_identity(
    receipt: SnapshotObjectReceipt,
    product: SnapshotProduct,
    *,
    expected_day: str | None = None,
    expected_tier: int | None = None,
) -> None:
    matched = _DAILY_PART.search(receipt.key)
    if matched is None or not receipt.key.startswith(f"{product.data_root}/kind=observed/"):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"daily serving receipt escaped its allowlisted product path: {receipt.key}",
        )
    day = f"{matched.group('year')}-{matched.group('month')}-{matched.group('day')}"
    tier = int(matched.group("zoom"))
    if (expected_day is not None and day != expected_day) or (expected_tier is not None and tier != expected_tier):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"daily serving receipt differs from its checkpoint day/tier: {receipt.key}",
        )


def _monthly_serving_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    """Resolve monthly parts only through the exact checkpoint family bound by the manifest."""
    if isinstance(manifest.get("checkpoints"), list):
        return _product_checkpoint_receipts(store, manifest, product)
    if isinstance(manifest.get("object_receipts"), list):
        return _lane_checkpoint_receipts(store, manifest, product)
    raise faults.snapshot_unpublished(
        layer=product.layer,
        snapshot_id=product.snapshot_id,
        detail="monthly manifest has no supported checkpoint receipt inventory",
    )


def _product_checkpoint_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_checkpoints = manifest.get("checkpoints")
    if not isinstance(raw_checkpoints, list) or not raw_checkpoints:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly product manifest has no checkpoint receipts",
        )
    if len(raw_checkpoints) > MAX_MONTHLY_RECEIPT_OBJECTS:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly checkpoint inventory exceeds the serving receipt limit",
        )
    expected_tiers = {str(tier) for tier in ZOOM_TIERS}

    def load_checkpoint(raw_checkpoint: object) -> tuple[str, str, tuple[SnapshotObjectReceipt, ...]]:
        checkpoint_receipt = _object_receipt(store, raw_checkpoint, product, context="manifest checkpoint")
        matched = _PRODUCT_CHECKPOINT.search(checkpoint_receipt.key)
        month = f"{matched.group('year')}-{matched.group('month')}" if matched is not None else ""
        if (
            matched is None
            or not checkpoint_receipt.key.startswith(f"{product.data_root}/_checkpoints/")
            or not isinstance(raw_checkpoint, Mapping)
            or raw_checkpoint.get("month") != month
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly checkpoint receipt has an invalid product/month identity",
            )
        checkpoint_payload = _read_bound_receipt(store, checkpoint_receipt, product, metadata=True)
        checkpoint = _json_object(checkpoint_payload, key=checkpoint_receipt.key, product=product)
        _verify_checkpoint_identity(checkpoint, product, month=month)
        raw_rungs = checkpoint.get("rungs")
        if not isinstance(raw_rungs, Mapping) or set(raw_rungs) != expected_tiers:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"checkpoint {checkpoint_receipt.key} does not bind exactly four serving rungs",
            )
        parts: list[SnapshotObjectReceipt] = []
        for tier in ZOOM_TIERS:
            part = _object_receipt(store, raw_rungs[str(tier)], product, context="checkpoint rung")
            _verify_monthly_part_identity(part, product, month=month, tier=tier)
            parts.append(part)
        return (month, checkpoint_receipt.key, tuple(parts))

    with ThreadPoolExecutor(max_workers=min(METADATA_VERIFY_WORKERS, len(raw_checkpoints))) as executor:
        loaded = tuple(executor.map(load_checkpoint, raw_checkpoints))
    serving: dict[str, SnapshotObjectReceipt] = {}
    months: set[str] = set()
    checkpoint_keys: set[str] = set()
    for month, checkpoint_key, parts in loaded:
        if month in months or checkpoint_key in checkpoint_keys:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly checkpoint receipt repeats a product/month identity",
            )
        months.add(month)
        checkpoint_keys.add(checkpoint_key)
        for part in parts:
            if part.key in serving:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"checkpoint inventory repeats serving part {part.key}",
                )
            serving[part.key] = part
    return serving


def _lane_checkpoint_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_inventory = manifest.get("object_receipts")
    if not isinstance(raw_inventory, list) or not raw_inventory:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly lane manifest has no object receipt inventory",
        )
    if len(raw_inventory) > MAX_MONTHLY_RECEIPT_OBJECTS:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly lane object inventory exceeds the serving receipt limit",
        )
    inventory: dict[str, tuple[SnapshotObjectReceipt, str]] = {}
    for raw in raw_inventory:
        receipt = _object_receipt(store, raw, product, context="manifest object")
        kind = raw.get("kind") if isinstance(raw, Mapping) else None
        if not isinstance(kind, str) or receipt.key in inventory:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly lane manifest has an invalid or duplicate object receipt",
            )
        inventory[receipt.key] = (receipt, kind)
    base_by_month = _checkpoint_receipts_by_month(inventory, product, kind="base_checkpoint")
    tier_by_month = _checkpoint_receipts_by_month(inventory, product, kind="tier_checkpoint")
    if not base_by_month or set(base_by_month) != set(tier_by_month):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly lane base and tier checkpoint month inventories differ",
        )
    expected_tiers = {str(tier) for tier in ZOOM_TIERS}

    def load_month(month: str) -> tuple[SnapshotObjectReceipt, ...]:
        base_receipt = base_by_month[month]
        tier_receipt = tier_by_month[month]
        base_payload = _read_bound_receipt(store, base_receipt, product, metadata=True)
        tier_payload = _read_bound_receipt(store, tier_receipt, product, metadata=True)
        base = _json_object(base_payload, key=base_receipt.key, product=product)
        tiers = _json_object(tier_payload, key=tier_receipt.key, product=product)
        _verify_checkpoint_identity(base, product, month=month)
        _verify_checkpoint_identity(tiers, product, month=month)
        bound_base_key = tiers.get("base_checkpoint_key")
        if (
            not isinstance(bound_base_key, str)
            or store.relative_key(bound_base_key) != base_receipt.key
            or tiers.get("base_checkpoint_sha256") != base_receipt.sha256
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"tier checkpoint {tier_receipt.key} does not bind its exact base checkpoint",
            )
        raw_tiers = tiers.get("tiers")
        if not isinstance(raw_tiers, Mapping) or set(raw_tiers) != expected_tiers:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"tier checkpoint {tier_receipt.key} does not bind exactly four serving rungs",
            )
        base_part = _object_receipt(store, base.get("base_part"), product, context="base checkpoint rung")
        parts: list[SnapshotObjectReceipt] = []
        for tier in ZOOM_TIERS:
            part = _object_receipt(store, raw_tiers[str(tier)], product, context="tier checkpoint rung")
            _verify_monthly_part_identity(part, product, month=month, tier=tier)
            manifest_part = inventory.get(part.key)
            expected_kind = "z13_data" if tier == BASE_ZOOM_TIER else "coarse_data"
            if (
                manifest_part is None
                or manifest_part[1] != expected_kind
                or manifest_part[0] != part
                or (tier == BASE_ZOOM_TIER and base_part != part)
            ):
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"checkpoint rung {part.key} differs from the exact manifest receipt chain",
                )
            parts.append(part)
        return tuple(parts)

    months = tuple(sorted(base_by_month))
    with ThreadPoolExecutor(max_workers=min(METADATA_VERIFY_WORKERS, len(months))) as executor:
        loaded = tuple(executor.map(load_month, months))
    serving: dict[str, SnapshotObjectReceipt] = {}
    for parts in loaded:
        for part in parts:
            if part.key in serving:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"monthly lane repeats serving part {part.key}",
                )
            serving[part.key] = part
    return serving


def _checkpoint_receipts_by_month(
    inventory: Mapping[str, tuple[SnapshotObjectReceipt, str]],
    product: SnapshotProduct,
    *,
    kind: Literal["base_checkpoint", "tier_checkpoint"],
) -> Mapping[str, SnapshotObjectReceipt]:
    matcher = _LANE_BASE_CHECKPOINT if kind == "base_checkpoint" else _LANE_TIER_CHECKPOINT
    result: dict[str, SnapshotObjectReceipt] = {}
    for receipt, receipt_kind in inventory.values():
        if receipt_kind != kind:
            continue
        matched = matcher.search(receipt.key)
        if matched is None or not receipt.key.startswith(f"{product.data_root}/_checkpoints/"):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"{kind} receipt escaped the allowlisted lane checkpoint path",
            )
        month = f"{matched.group('year')}-{matched.group('month')}"
        if month in result:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"monthly lane repeats the {kind} month {month}",
            )
        result[month] = receipt
    return result


def _verify_checkpoint_identity(
    checkpoint: Mapping[str, object],
    product: SnapshotProduct,
    *,
    month: str,
) -> None:
    if checkpoint.get("contract_version") != product.contract_version or checkpoint.get("observation_month") != month:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"monthly checkpoint identity differs from the allowlisted contract for {month}",
        )
    snapshot_values = tuple(checkpoint.get(name) for name in ("snapshot_id", "input_snapshot_id") if name in checkpoint)
    if not snapshot_values or any(value != product.snapshot_id for value in snapshot_values):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"monthly checkpoint does not bind snapshot {product.snapshot_id}",
        )
    identities: list[str] = []
    lane = checkpoint.get("lane")
    if isinstance(lane, str):
        identities.append(lane)
    product_block = checkpoint.get("product")
    if isinstance(product_block, Mapping):
        identities.extend(
            str(product_block[name]) for name in ("stream", "lane") if isinstance(product_block.get(name), str)
        )
    if not identities or product.layer not in identities:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"monthly checkpoint does not bind allowlisted product {product.layer}",
        )


def _object_receipt(
    store: SnapshotStore,
    value: object,
    product: SnapshotProduct,
    *,
    context: str,
) -> SnapshotObjectReceipt:
    if not isinstance(value, Mapping):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{context} receipt is not an object",
        )
    raw_key = value.get("key")
    raw_sha256 = value.get("sha256")
    byte_values = [value[name] for name in ("bytes", "byte_count") if name in value]
    row_values = [value[name] for name in ("rows", "row_count") if name in value and value[name] is not None]
    if (
        not isinstance(raw_key, str)
        or not isinstance(raw_sha256, str)
        or _SHA256.fullmatch(raw_sha256) is None
        or len(byte_values) != 1
        or not isinstance(byte_values[0], int)
        or isinstance(byte_values[0], bool)
        or byte_values[0] <= 0
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{context} receipt has an invalid key, byte count, or SHA-256",
        )
    if len(row_values) > 1 or (
        row_values and (not isinstance(row_values[0], int) or isinstance(row_values[0], bool) or row_values[0] < 0)
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{context} receipt has an invalid row count",
        )
    row_count = row_values[0] if row_values else None
    assert row_count is None or isinstance(row_count, int)
    return SnapshotObjectReceipt(
        key=store.relative_key(raw_key),
        byte_count=byte_values[0],
        sha256=raw_sha256,
        row_count=row_count,
    )


def _verify_monthly_part_identity(
    receipt: SnapshotObjectReceipt,
    product: SnapshotProduct,
    *,
    month: str,
    tier: int,
) -> None:
    matched = _MONTHLY_PART.search(receipt.key)
    if (
        matched is None
        or not receipt.key.startswith(f"{product.data_root}/kind=observed/")
        or int(matched.group("zoom")) != tier
        or f"{matched.group('year')}-{matched.group('month')}" != month
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"monthly serving receipt escaped its allowlisted product/month/tier path: {receipt.key}",
        )


def _read_bound_receipt(
    store: SnapshotStore,
    receipt: SnapshotObjectReceipt,
    product: SnapshotProduct,
    *,
    metadata: bool,
) -> bytes:
    payload = store.read_object(receipt.key)
    if (
        payload is None
        or (metadata and len(payload) > MAX_MANIFEST_BYTES)
        or len(payload) != receipt.byte_count
        or hashlib.sha256(payload).hexdigest() != receipt.sha256
    ):
        if metadata:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"checkpoint receipt no longer matches {receipt.key}",
            )
        raise faults.snapshot_schema_mismatch(
            layer=product.layer,
            key=receipt.key,
            detail="serving Parquet bytes do not match the closed receipt chain",
        )
    return payload


def _verify_bound_parts(
    store: SnapshotStore,
    evidence: SnapshotEvidence,
    keys: Sequence[str],
    *,
    verified: set[str] | None = None,
) -> None:
    pending = tuple(dict.fromkeys(key for key in keys if verified is None or key not in verified))
    if len(pending) > MAX_MONTHLY_RECEIPT_OBJECTS:
        raise faults.snapshot_unpublished(
            layer=evidence.product.layer,
            snapshot_id=evidence.product.snapshot_id,
            detail="monthly serving receipt verification exceeds the object limit",
        )

    def verify(key: str) -> None:
        receipt = evidence.part_receipts.get(key)
        if receipt is None:
            raise faults.snapshot_unpublished(
                layer=evidence.product.layer,
                snapshot_id=evidence.product.snapshot_id,
                detail=f"serving key is not bound through a verified checkpoint receipt: {key}",
            )
        _read_bound_receipt(store, receipt, evidence.product, metadata=False)

    for key in pending:
        verify(key)
    if verified is not None:
        verified.update(pending)


def _declared_contiguous_days(evidence: SnapshotEvidence) -> set[date] | None:
    declared = _declared_contiguous_days_from_manifest(evidence.manifest, evidence.product)
    if declared is not None:
        return declared
    if evidence.product.coverage_cells_per_day is not None:
        return _complete_grid_contiguous_days(evidence)
    return None


def _complete_grid_contiguous_days(evidence: SnapshotEvidence) -> set[date]:
    """Prove a closed monthly day range from unique cell-day rows and a fixed complete lattice."""
    product = evidence.product
    cells_per_day = product.coverage_cells_per_day
    grid_name = product.coverage_cell_grid_name
    product_block = evidence.manifest.get("product")
    expected_grain = ["support_key", "signal_name", "normalized_unit", "cell_id", "observation_day"]
    if (
        not isinstance(cells_per_day, int)
        or isinstance(cells_per_day, bool)
        or cells_per_day <= 0
        or not isinstance(grid_name, str)
        or not isinstance(product_block, Mapping)
        or product_block.get("cell_grid_name") != grid_name
        or product_block.get("observed_grain") != expected_grain
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly coverage lacks its exact fixed-lattice unique cell-day contract",
        )
    raw_first = evidence.manifest.get("source_observation_day_min")
    raw_last = evidence.manifest.get("source_observation_day_max")
    try:
        if not isinstance(raw_first, str) or not isinstance(raw_last, str):
            raise TypeError
        first = date.fromisoformat(raw_first)
        last = date.fromisoformat(raw_last)
    except (TypeError, ValueError) as exc:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly fixed-lattice coverage has malformed source day bounds",
        ) from exc
    if last < first:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly fixed-lattice coverage has an inverted source day range",
        )

    expected_days = {first + timedelta(days=offset) for offset in range((last - first).days + 1)}
    proven_days: set[date] = set()
    total_rows = 0
    seen_months: set[tuple[int, int]] = set()
    for key in evidence.parts_by_tier[BASE_ZOOM_TIER]:
        matched = _MONTHLY_PART.search(key)
        receipt = evidence.part_receipts.get(key)
        if matched is None or receipt is None or receipt.row_count is None:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly fixed-lattice coverage lacks a bound z13 row-count receipt",
            )
        month = (int(matched.group("year")), int(matched.group("month")))
        if month in seen_months:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly fixed-lattice coverage has more than one z13 part for a month",
            )
        seen_months.add(month)
        month_days = {day for day in expected_days if (day.year, day.month) == month}
        if not month_days or receipt.row_count != len(month_days) * cells_per_day:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"monthly fixed-lattice z13 rows do not prove every {month[0]:04d}-{month[1]:02d} day",
            )
        proven_days.update(month_days)
        total_rows += receipt.row_count
    if proven_days != expected_days:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly fixed-lattice z13 parts do not cover the contiguous source day range",
        )

    totals = evidence.manifest.get("totals")
    rung_totals = totals.get("rungs") if isinstance(totals, Mapping) else None
    z13_totals = rung_totals.get(str(BASE_ZOOM_TIER)) if isinstance(rung_totals, Mapping) else None
    if (
        not isinstance(totals, Mapping)
        or not isinstance(z13_totals, Mapping)
        or z13_totals.get("rows") != total_rows
        or totals.get("release_winner_rows") != total_rows
        or totals.get("excluded_rows") != 0
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly fixed-lattice coverage totals do not bind the exact z13 winner population",
        )
    return proven_days


def _declared_contiguous_days_from_manifest(
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> set[date] | None:
    candidates: list[tuple[object, object, object]] = [
        (manifest.get("day_count"), manifest.get("observation_day_min"), manifest.get("observation_day_max")),
        (manifest.get("day_count"), manifest.get("first_day"), manifest.get("last_day")),
        (
            manifest.get("data_day_count"),
            manifest.get("observation_day_min"),
            manifest.get("observation_day_max"),
        ),
    ]
    totals = manifest.get("totals")
    if isinstance(totals, Mapping):
        candidates.append((totals.get("winner_day_count"), totals.get("winner_day_min"), totals.get("winner_day_max")))
    for raw_count, raw_first, raw_last in candidates:
        if raw_count is None or not isinstance(raw_first, str) or not isinstance(raw_last, str):
            continue
        try:
            first = date.fromisoformat(raw_first)
            last = date.fromisoformat(raw_last)
            if not isinstance(raw_count, int) or isinstance(raw_count, bool):
                raise TypeError
            count = raw_count
        except (TypeError, ValueError) as exc:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="manifest day range is malformed",
            ) from exc
        span = (last - first).days + 1
        if span <= 0 or count != span:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="manifest day count does not prove a contiguous closed range",
            )
        return {first + timedelta(days=offset) for offset in range(span)}
    return None


def _require_monthly_tier_parity(evidence: SnapshotEvidence) -> None:
    month_sets = {
        tier: {
            (matched.group("year"), matched.group("month"))
            for key in parts
            if (matched := _MONTHLY_PART.search(key)) is not None
        }
        for tier, parts in evidence.parts_by_tier.items()
    }
    if not month_sets[13] or any(months != month_sets[13] for months in month_sets.values()):
        raise faults.snapshot_schema_mismatch(
            layer=evidence.product.layer,
            key=evidence.product.data_root,
            detail="manifest-bound monthly rungs do not have identical month membership",
        )
    expected_parts = len(month_sets[13])
    rung_totals = evidence.manifest.get("rungs")
    if rung_totals is None:
        totals = evidence.manifest.get("totals")
        rung_totals = totals.get("rungs") if isinstance(totals, Mapping) else None
    if rung_totals is None:
        rung_totals = evidence.manifest.get("tiers")
    if not isinstance(rung_totals, Mapping):
        raise faults.snapshot_unpublished(
            layer=evidence.product.layer,
            snapshot_id=evidence.product.snapshot_id,
            detail="monthly manifest has no persisted tier population proof",
        )
    for tier in ZOOM_TIERS:
        record = rung_totals.get(str(tier))
        if not isinstance(record, Mapping):
            raise faults.snapshot_unpublished(
                layer=evidence.product.layer,
                snapshot_id=evidence.product.snapshot_id,
                detail=f"monthly manifest has no z{tier:02d} population proof",
            )
        part_count = record.get("parts", record.get("part_count"))
        if not isinstance(part_count, int) or isinstance(part_count, bool) or part_count != expected_parts:
            raise faults.snapshot_unpublished(
                layer=evidence.product.layer,
                snapshot_id=evidence.product.snapshot_id,
                detail=f"monthly manifest z{tier:02d} part count differs from its month population",
            )


def _gap_ranges(days: set[date]) -> tuple[DayRange, ...]:
    if not days:
        return ()
    first, last = min(days), max(days)
    expected = {first + timedelta(days=offset) for offset in range((last - first).days + 1)}
    return contiguous_ranges(expected - days)


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
    if product.schema_columns is not None:
        return frozenset(product.schema_columns)
    schema_layer = product.schema_layer or product.layer
    return frozenset(get_stream_schema(schema_layer, "observed").arrow_schema.names)


def _verify_exact_schemas(
    session: ServingSession,
    evidence: SnapshotEvidence,
    keys: Sequence[str],
) -> None:
    expected = snapshot_product_columns(evidence.product)
    for key in keys:
        uri = session.object_uri(key)
        cursor = session.connection.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=false) LIMIT 0",
            [[uri]],
        )
        actual = frozenset(str(column[0]) for column in cursor.description or ())
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise faults.snapshot_schema_mismatch(
                layer=evidence.product.layer,
                key=key,
                detail=f"missing={missing[:5]}, extra={extra[:5]}",
            )


def _read_observed_day(
    session: ServingSession,
    *,
    keys: Sequence[str],
    observed_day: date,
    scope: ReadScope,
    row_budget: int,
) -> tuple[tuple[ServedRow, ...], bool]:
    uris = [session.object_uri(key) for key in keys]
    predicates = ["observed_day = ?"]
    parameters: list[object] = [uris, observed_day]
    if scope.bbox is not None:
        predicates.extend(("cell_longitude BETWEEN ? AND ?", "cell_latitude BETWEEN ? AND ?"))
        parameters.extend(
            (
                scope.bbox.west,
                scope.bbox.east,
                scope.bbox.south,
                scope.bbox.north,
            )
        )
    statement = (
        "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=false) WHERE "
        + " AND ".join(predicates)
        + " ORDER BY cell_longitude, cell_latitude LIMIT ?"
    )
    parameters.append(row_budget + 1)
    cursor = session.connection.execute(statement, parameters)
    columns = tuple(description[0] for description in cursor.description or ())
    values = cursor.fetchall()
    truncated = len(values) > row_budget
    rows = tuple(dict(zip(columns, row, strict=True)) for row in values[:row_budget])
    return (rows, truncated)


__all__ = [
    "MAX_SNAPSHOT_READ_PARTS",
    "PRODUCT_BY_LAYER",
    "SIGNAL_PRODUCT_COLUMNS",
    "SNAPSHOT_ID",
    "SNAPSHOT_PRODUCTS",
    "SOIL_TEMPERATURE_COLUMNS",
    "SOIL_WETNESS_COLUMNS",
    "ObjectStoreSnapshotStore",
    "SnapshotCoverageCache",
    "SnapshotCoverageCensus",
    "SnapshotCoverageWithholding",
    "SnapshotEvidence",
    "SnapshotProduct",
    "SnapshotStore",
    "build_snapshot_coverage",
    "load_snapshot_evidence",
    "load_snapshot_scope_evidence",
    "product_for_layer",
    "resolve_snapshot_evidence_day",
    "resolve_snapshot_evidence_window",
    "resolve_snapshot_product",
    "resolve_snapshot_window",
    "snapshot_product_columns",
]
