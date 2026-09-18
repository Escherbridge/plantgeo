"""The live half of a snapshot product: the days written past `forward_first_day`, and what proved them.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.paths import stream_prefix
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops.serving import day_status_sets
from agri_data_service.parquet_ops.snapshot_product_catalog import (
    FORWARD_PARTITION_KIND,
    SnapshotProduct,
    logger,
)
from agri_data_service.parquet_ops.snapshot_store import (
    ForwardAvailability,
    ForwardAvailabilityPort,
    ForwardAvailabilityWithheld,
    SnapshotStore,
)
from agri_data_service.parquet_ops.wire import (
    COVERAGE_AUTHORITY_AVAILABILITY,
    COVERAGE_AUTHORITY_CENSUS,
    WITHHELD_AVAILABILITY_UNPUBLISHED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.config import CoverageAuthorityPolicy
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.wire import (
        CoverageAuthority,
        CoverageWithholding,
    )


@dataclass(frozen=True, slots=True)
class _ForwardHalf:
    """One product's forward days PER RUNG, what proved them, and why they may be missing entirely.

    Per rung because the two authorities prove different things at different grains and neither may
    be flattened into the other: a listing proves each rung on its own, while an availability index
    proves the days the WHOLE required ladder agrees on and therefore hands every rung one set.
    """

    published_days: Mapping[ZoomTier, frozenset[date]]
    absent_days: Mapping[ZoomTier, frozenset[date]]
    authority: CoverageAuthority
    source_ceiling: date | None = None
    generation_sha256: str | None = None
    pointer_key: str | None = None
    withheld_reason: CoverageWithholding | None = None

    def published_for(self, tier: ZoomTier) -> set[date]:
        """Return one rung's forward published days."""
        return set(self.published_days.get(tier, frozenset()))

    def absent_for(self, tier: ZoomTier) -> set[date]:
        """Return one rung's forward governed-absence days."""
        return set(self.absent_days.get(tier, frozenset()))


def _every_rung(days: frozenset[date]) -> Mapping[ZoomTier, frozenset[date]]:
    """Give every authoritative rung the same day set, as a ladder-agreeing proof does."""
    return dict.fromkeys(ZOOM_TIERS, days)


#: A frozen product has no live edge at all, so its forward half is empty and costs nothing to prove.
_NO_FORWARD_EDGE: Final = _ForwardHalf(
    published_days=_every_rung(frozenset()),
    absent_days=_every_rung(frozenset()),
    authority=COVERAGE_AUTHORITY_CENSUS,
)


def _forward_half(
    store: SnapshotStore,
    product: SnapshotProduct,
    *,
    policy: CoverageAuthorityPolicy,
    forward_availability: ForwardAvailabilityPort | None,
) -> _ForwardHalf:
    """Prove one product's forward days from whichever evidence its authority policy allows."""
    if product.forward_first_day is None:
        return _NO_FORWARD_EDGE
    if policy == "availability":
        if forward_availability is None:
            # `availability` promises no request-path LIST. An unwired port is a wiring fault, and
            # listing anyway would break that promise silently instead of stating it on the wire.
            return _ForwardHalf(
                published_days=_every_rung(frozenset()),
                absent_days=_every_rung(frozenset()),
                authority=COVERAGE_AUTHORITY_AVAILABILITY,
                withheld_reason=WITHHELD_AVAILABILITY_UNPUBLISHED,
            )
        return _forward_half_from_index(
            product,
            forward_availability.forward_days(layer=product.layer, first_day=product.forward_first_day),
        )
    return _forward_half_from_listing(store, product)


def _forward_half_from_index(
    product: SnapshotProduct,
    answer: ForwardAvailability | ForwardAvailabilityWithheld,
) -> _ForwardHalf:
    """Turn the index's answer for one product into a forward half, withholding rather than listing."""
    if isinstance(answer, ForwardAvailabilityWithheld):
        logger.warning(
            "snapshot_forward_availability_withheld",
            layer=product.layer,
            code=answer.reason,
            reason=answer.detail,
        )
        return _ForwardHalf(
            published_days=_every_rung(frozenset()),
            absent_days=_every_rung(frozenset()),
            authority=COVERAGE_AUTHORITY_AVAILABILITY,
            withheld_reason=answer.reason,
        )
    return _ForwardHalf(
        published_days=_every_rung(answer.published_days),
        absent_days=_every_rung(answer.absent_days),
        authority=COVERAGE_AUTHORITY_AVAILABILITY,
        source_ceiling=answer.source_ceiling,
        generation_sha256=answer.generation_sha256,
        pointer_key=answer.pointer_key,
    )


def _forward_half_from_listing(store: SnapshotStore, product: SnapshotProduct) -> _ForwardHalf:
    """List the live lane prefix once for all four rungs: the TRANSITIONAL census, labelled as one.

    ONE listing per product, not one per rung: the four tiers share `layer=<slug>/kind=observed/`,
    and `day_status_sets` already ignores keys of another tier. Logged because this runs only on a
    coverage-cache miss, so the log rate is bounded by that TTL and states the bridge's real cost.
    """
    if product.forward_first_day is None:  # pragma: no cover - the caller already returned for these
        return _NO_FORWARD_EDGE
    logger.warning(
        "snapshot_forward_census_listing",
        layer=product.layer,
        prefix=stream_prefix(product.layer, FORWARD_PARTITION_KIND),
        reason="census_until_bootstrap proves this product's forward half by walking its live lane prefix",
    )
    keys = tuple(store.iter_keys(stream_prefix(product.layer, FORWARD_PARTITION_KIND)))
    first_day = product.forward_first_day
    published: dict[ZoomTier, frozenset[date]] = {}
    absent: dict[ZoomTier, frozenset[date]] = {}
    for tier in ZOOM_TIERS:
        statuses = day_status_sets(keys, layer=product.layer, kind=FORWARD_PARTITION_KIND, tier=tier)
        published[tier] = frozenset(day for day in statuses.data if day >= first_day)
        absent[tier] = frozenset(day for day in statuses.absent if day >= first_day)
    return _ForwardHalf(published_days=published, absent_days=absent, authority=COVERAGE_AUTHORITY_CENSUS)


def _product_source_ceiling(
    declared_days: set[date] | None,
    tier_days: set[date],
    forward: _ForwardHalf,
) -> date | None:
    """Return this product's source ceiling: the newest horizon any of its evidence establishes.

    The manifest's last day alone was right while every product was frozen. It stops being right the
    moment a forward writer publishes past it: the row would then carry `latest_day` ABOVE its own
    `source_ceiling_day`, which reads as a lane serving days its source cannot have produced. An
    availability-proven forward half states its OWN ceiling, which is the only one of the three that
    can sit ahead of the newest published day and therefore the only one that can show a gap tail.
    """
    candidates = {max(declared_days)} if declared_days else set()
    if tier_days:
        candidates.add(max(tier_days))
    if forward.source_ceiling is not None:
        candidates.add(forward.source_ceiling)
    return max(candidates) if candidates else None
