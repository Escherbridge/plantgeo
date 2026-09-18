"""Which manifest days a product may call selectable: declared, grid-complete, and rung-consistent.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import TYPE_CHECKING

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.snapshot_product_catalog import (
    _MONTHLY_PART,
    BASE_ZOOM_TIER,
    SnapshotProduct,
)
from agri_data_service.parquet_ops.wire import (
    DayRange,
    contiguous_ranges,
)

if TYPE_CHECKING:
    from agri_data_service.parquet_ops.snapshot_store import SnapshotEvidence


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
