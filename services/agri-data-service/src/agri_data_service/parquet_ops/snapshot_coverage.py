"""The per-product coverage census: the closed manifest half, the live forward half, and their join.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.snapshot_contiguity import (
    _declared_contiguous_days,
    _gap_ranges,
    _require_monthly_tier_parity,
)
from agri_data_service.parquet_ops.snapshot_evidence import load_snapshot_evidence
from agri_data_service.parquet_ops.snapshot_forward_edge import (
    _forward_half,
    _ForwardHalf,
    _product_source_ceiling,
)
from agri_data_service.parquet_ops.snapshot_product_catalog import (
    SNAPSHOT_COVERAGE_CACHE_SECONDS,
    SNAPSHOT_COVERAGE_PRODUCT_WORKERS,
    SnapshotProduct,
    _daily_days,
)
from agri_data_service.parquet_ops.snapshot_store import (
    ForwardAvailabilityPort,
    SnapshotCoverageCensus,
    SnapshotCoverageWithholding,
    SnapshotEvidence,
    SnapshotStore,
)
from agri_data_service.parquet_ops.wire import (
    LaneCoverage,
    contiguous_ranges,
)

if TYPE_CHECKING:
    from agri_data_service.config import CoverageAuthorityPolicy
    from agri_data_service.foundation.parquet.zoom import ZoomTier


def build_snapshot_coverage(
    store: SnapshotStore,
    *,
    policy: CoverageAuthorityPolicy = "census_until_bootstrap",
    forward_availability: ForwardAvailabilityPort | None = None,
) -> SnapshotCoverageCensus:
    """Prove each product independently through bounded metadata-only evidence.

    THE FORWARD HALF IS AUTHORITY-AWARE, and it has to be: every product carries a live edge, and
    listing it is a `layer=<slug>/kind=observed/` prefix walk on every cold `GET /coverage`. Under
    `availability` that walk is exactly the cost the index exists to retire, so the forward half is
    proven from the product's OWN availability index or withheld -- never from a LIST. Under
    `census_until_bootstrap` the listing is the declared transitional cost, labelled `census` and
    logged once per cache TTL, because this function runs only on a `SnapshotCoverageCache` miss.

    Reads `SNAPSHOT_PRODUCTS` off the `snapshot_products` module (not `snapshot_product_catalog`
    directly) via a call-time import, not a module-level one: `snapshot_products` is what
    `tests/parquet_ops/test_snapshot_products.py` monkeypatches, and a `from x import NAME` binds a
    copy of the reference at import time that a later `monkeypatch.setattr(x, "NAME", ...)` on a
    different module's namespace would silently miss. See execution/AGENTS.md's identical note on
    `job_executor_service.LANE_SPECS` for the general shape of this trap.
    """
    from agri_data_service.parquet_ops.snapshot_products import SNAPSHOT_PRODUCTS  # noqa: PLC0415

    rows: list[LaneCoverage] = []
    withheld: list[SnapshotCoverageWithholding] = []
    worker_count = min(SNAPSHOT_COVERAGE_PRODUCT_WORKERS, len(SNAPSHOT_PRODUCTS))
    if worker_count == 0:
        return SnapshotCoverageCensus(lanes=(), withheld=())
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        evidence_results = executor.map(
            lambda product: _coverage_inputs(
                store,
                product,
                policy=policy,
                forward_availability=forward_availability,
            ),
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


@dataclass(frozen=True, slots=True)
class _ProductCoverageInputs:
    """One product's closed evidence plus the forward half its live days are proven from."""

    evidence: SnapshotEvidence
    forward: _ForwardHalf


def _coverage_inputs(
    store: SnapshotStore,
    product: SnapshotProduct,
    *,
    policy: CoverageAuthorityPolicy,
    forward_availability: ForwardAvailabilityPort | None,
) -> _ProductCoverageInputs | SnapshotCoverageWithholding:
    """Gather everything one product's rungs are built from, inside the census's own worker thread."""
    loaded = _coverage_evidence_or_withholding(store, product)
    if isinstance(loaded, SnapshotCoverageWithholding):
        return loaded
    try:
        forward = _forward_half(store, product, policy=policy, forward_availability=forward_availability)
    except faults.ServingRefusalError as exc:
        return SnapshotCoverageWithholding(layer=product.layer, code=exc.code, message=exc.message)
    return _ProductCoverageInputs(evidence=loaded, forward=forward)


def _coverage_evidence_or_withholding(
    store: SnapshotStore,
    product: SnapshotProduct,
) -> SnapshotEvidence | SnapshotCoverageWithholding:
    try:
        return load_snapshot_evidence(store, product)
    except faults.ServingRefusalError as exc:
        return SnapshotCoverageWithholding(layer=product.layer, code=exc.code, message=exc.message)


def _build_product_coverage(
    inputs: _ProductCoverageInputs,
) -> tuple[LaneCoverage, ...]:
    """Build all four rungs for one product or raise one product-local typed refusal.

    An immutable product owns no availability index yet, so it stays `census` authority and states
    its own last day as its source ceiling. That ceiling is what stops the census's
    `evaluated_through_day` from reading as a claim that the frozen snapshot is current through it.

    A PRODUCT WITH A FORWARD EDGE REPORTS BOTH HALVES. Days below `forward_first_day` come from the
    closed manifest; days at or above it come from the live lane, proven by whichever authority the
    coverage policy allows. The manifest-equality check therefore holds only over the closed half --
    above the boundary the manifest is silent BY CONSTRUCTION, and asking it to agree there would
    refuse the whole product the moment the writer wrote a day.

    A MANIFEST DAY AT OR ABOVE THE BOUNDARY REFUSES THE PRODUCT. The frozen snapshot cannot
    legitimately declare a day it was closed before; a day excluded from the equality check and then
    unioned into the answer anyway is a manifest claim nothing verified, published as if it were.

    `coverage_authority` on a forward product names WHAT PROVED ITS LIVE EDGE, because that is the
    only half whose evidence can change: the closed half is manifest-bound under either policy, and
    a frozen product stays `census` because it has no live edge to prove.

    A WITHHELD FORWARD HALF WITHHOLDS THE WHOLE PRODUCT, null bounds and empty ranges, in exactly the
    shape `availability_coverage.withheld_lane_coverage` uses. The manifest did not stop being
    evidence, but the CLIENT cannot gate on half a lane: a non-null `withheld_reason` withholds the
    whole capability there, so shipping the closed half's bounds beside a reason publishes days
    nothing on the wire will ever draw -- and `tests/contract/test_wire_contract.py`'s "a withheld
    lane publishes no selectable days" is a contract this row would break. The manifest's evidence
    is not lost; it is simply not published until the forward half can prove itself.
    """
    rows: list[LaneCoverage] = []
    evidence = inputs.evidence
    forward = inputs.forward
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
    _require_manifest_below_forward_boundary(declared_days, product=product)
    for tier in ZOOM_TIERS:
        tier_parts = evidence.parts_by_tier[tier]
        closed_days = _daily_days(tier_parts, product=product) if product.layout == "daily" else shared_days
        _require_manifest_below_forward_boundary(closed_days, product=product, tier=tier)
        if (
            product.layout == "daily"
            and declared_days is not None
            and _closed_half(closed_days, product) != _closed_half(declared_days, product)
        ):
            raise faults.snapshot_schema_mismatch(
                layer=product.layer,
                key=product.data_root,
                detail=f"tier z{tier:02d} day paths do not equal the manifest's closed day range",
            )
        if forward.withheld_reason is not None:
            rows.append(
                LaneCoverage(
                    layer=product.layer,
                    nature="daily_series",
                    kind="observed",
                    zoom=tier,
                    earliest_day=None,
                    latest_day=None,
                    latest_recorded_day=None,
                    published_ranges=(),
                    gap_ranges=(),
                    governed_absence_ranges=(),
                    coverage_authority=forward.authority,
                    availability_generation_sha256=forward.generation_sha256,
                    availability_pointer_key=forward.pointer_key,
                    withheld_reason=forward.withheld_reason,
                )
            )
            continue
        published_days = closed_days | forward.published_for(tier)
        # A FORWARD GOVERNED ABSENCE IS A GOVERNED ABSENCE, not a gap. The lane looked at the day and
        # the source deliberately had nothing; reporting it as a hole tells a client to keep asking.
        absence_days = forward.absent_for(tier) - published_days
        accounted = published_days | absence_days
        rows.append(
            LaneCoverage(
                layer=product.layer,
                nature="daily_series",
                kind="observed",
                zoom=tier,
                earliest_day=min(published_days) if published_days else None,
                latest_day=max(published_days) if published_days else None,
                # A snapshot product is `daily_series` and carries nothing: every published day here
                # is a day some half of the product actually wrote, so held and recorded coincide.
                latest_recorded_day=max(published_days) if published_days else None,
                published_ranges=contiguous_ranges(published_days),
                gap_ranges=_gap_ranges(accounted) if accounted else (),
                governed_absence_ranges=contiguous_ranges(absence_days),
                coverage_authority=forward.authority,
                availability_generation_sha256=forward.generation_sha256,
                availability_pointer_key=forward.pointer_key,
                source_ceiling_day=_product_source_ceiling(declared_days, published_days, forward),
                withheld_reason=None,
            )
        )
    return tuple(rows)


def _require_manifest_below_forward_boundary(
    days: set[date] | None,
    *,
    product: SnapshotProduct,
    tier: ZoomTier | None = None,
) -> None:
    """Refuse a closed product that claims a day the LIVE writer owns."""
    first_day = product.forward_first_day
    if first_day is None or not days:
        return
    trespassing = sorted(day for day in days if day >= first_day)
    if not trespassing:
        return
    scope = "manifest" if tier is None else f"tier z{tier:02d}"
    raise faults.snapshot_manifest_conflict(
        layer=product.layer,
        snapshot_id=product.snapshot_id,
        detail=(
            f"the {scope} declares {trespassing[0].isoformat()} at or after the forward boundary "
            f"{first_day.isoformat()}, which only the live writer may own"
        ),
    )


def _closed_half(days: set[date], product: SnapshotProduct) -> set[date]:
    """Narrow a day set to the half the frozen manifest is allowed to speak for."""
    if product.forward_first_day is None:
        return days
    return {day for day in days if day < product.forward_first_day}


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
        policy: CoverageAuthorityPolicy = "census_until_bootstrap",
        forward_availability: ForwardAvailabilityPort | None = None,
    ) -> SnapshotCoverageCensus:
        """Return the held census, rebuilding it under the caller's authority policy on a miss."""
        held = self._fresh(now)
        if held is not None:
            return held
        with self._lock:
            held = self._fresh(now)
            if held is not None:
                return held
            built = build_snapshot_coverage(store, policy=policy, forward_availability=forward_availability)
            self._held = (now, built)
            return built

    def clear(self) -> None:
        self._held = None

    def _fresh(self, now: datetime) -> SnapshotCoverageCensus | None:
        held = self._held
        if held is not None and now - held[0] < self._ttl:
            return held[1]
        return None
