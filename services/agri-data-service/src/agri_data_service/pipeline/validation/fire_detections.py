"""Reconcile the fire-detections lane against NASA FIRMS itself -- never local intermediate state.

Layer L2 (pipeline/validation): needs the network, so it cannot live in `method`
(layer-lanes.md section 4). See `docs/lanes/fire-detections.md` sections 5-6 for the incident this
module exists to catch and the reconciliation approach it implements.

THE HEADLINE TRAP: `INGEST_MAX_SOURCE_RECORDS` (default 10,000) silently drops the oldest detections
in an over-large ingest tick, and `records_written == 0` is a CORRECT, by-design idempotency result
that looks identical to a capped tick unless `IngestionJobResult.details["dropped"]` /
`.truncated` are read (docs/lanes/fire-detections.md section 5.1). `detect_capped_ingest` is the
primary, network-free check for exactly that signal; everything else here additionally reconciles
against FIRMS' own live API, which no amount of local bookkeeping can substitute for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.ingest.firms import (
    FIRMS_API_KEY_VARIABLE,
    fetch_active_fires,
    fetch_product_availability,
)
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_STREAM

if TYPE_CHECKING:
    from datetime import date

    import httpx

    from agri_data_service.ingest.results import IngestionJobResult

DROPPED_DETAIL_KEY: Final = "dropped"
REJECTED_DETAIL_KEY: Final = "rejected"

# FIRMS' dated-area endpoint answers `day_range` days starting at `start_date`; 1 scopes a spot-check
# recount to exactly the one day being reconciled, never a wider window that would double count.
_SINGLE_DAY_RANGE: Final = 1

ReconciliationStatus = Literal["reconciled", "source_uncovered_day", "unaccounted_gap"]


class FireDetectionsValidationError(RuntimeError):
    """Base error for a fire-detections validation pass that cannot proceed."""


class MissingFirmsCredentialError(FireDetectionsValidationError):
    """Raised when `NASA_FIRMS_KEY` is unset. Validation fails loudly here rather than skipping silently."""


def _require_firms_api_key() -> None:
    """Fail by name before any network call; a missing key must never read as a quiet, healthy skip."""
    if not os.environ.get(FIRMS_API_KEY_VARIABLE, "").strip():
        raise MissingFirmsCredentialError(
            f"{FIRMS_API_KEY_VARIABLE} is not set; fire-detections validation cannot reconcile against "
            "FIRMS without it, and skipping the check silently would hide a real validation gap"
        )


@dataclass(frozen=True, slots=True)
class CappedIngestFinding:
    """Evidence that one ingest tick silently dropped detections at the `INGEST_MAX_SOURCE_RECORDS` cap."""

    lane: str
    day: date
    dropped_record_count: int
    rejected_record_count: int
    records_written: int
    detail: str


def detect_capped_ingest(
    result: IngestionJobResult, *, day: date, lane: str = FIRE_DETECTIONS_STREAM
) -> CappedIngestFinding | None:
    """Read `result.truncated` / `details["dropped"]`, never `records_written` alone (section 5.1).

    `records_written == 0` is correct idempotency (the rows already landed on a prior tick) and is
    NEVER, by itself, evidence of a problem. A truncated tick or a nonzero `dropped` count is the
    real, silent loss this check exists to surface -- independent of `records_written` in both
    directions: a tick can write zero rows and still have dropped a real batch of new ones, or write
    thousands and drop nothing.
    """
    dropped = int(result.details.get(DROPPED_DETAIL_KEY, 0))
    if not result.truncated and dropped <= 0:
        return None
    return CappedIngestFinding(
        lane=lane,
        day=day,
        dropped_record_count=dropped,
        rejected_record_count=int(result.details.get(REJECTED_DETAIL_KEY, 0)),
        records_written=result.records_written,
        detail=(
            f"{lane} {day.isoformat()}: ingest reported truncated={result.truncated}, dropped={dropped} "
            f"detections at the INGEST_MAX_SOURCE_RECORDS cap (records_written={result.records_written} "
            "looked healthy in isolation)"
        ),
    )


@dataclass(frozen=True, slots=True)
class SourceDayCoverage:
    """Which of the products asked actually cover one acquisition day, per FIRMS' own live table."""

    day: date
    products_checked: tuple[str, ...]
    covering_products: tuple[str, ...]

    @property
    def is_source_uncovered(self) -> bool:
        """True when NO product publishes this day: a governed absence, never inferred from zero rows."""
        return not self.covering_products


async def check_source_day_coverage(
    client: httpx.AsyncClient, *, day: date, products: tuple[str, ...]
) -> SourceDayCoverage:
    """Cross-check one day against FIRMS' live availability table before treating zero rows as a gap.

    A day no product's [min_date, max_date] window covers is a GOVERNED ABSENCE -- "no product
    publishes this day" -- never inferred from a zero-row write (docs/lanes/fire-detections.md
    section 6, matching the archive walker's own `firms_history_spans_uncovered` event).
    """
    _require_firms_api_key()
    availability = await fetch_product_availability(client)
    covering = tuple(
        product for product in products if (window := availability.get(product)) is not None and window.covers(day)
    )
    return SourceDayCoverage(day=day, products_checked=products, covering_products=covering)


@dataclass(frozen=True, slots=True)
class SourceDayRecount:
    """One product's raw, undeduplicated FIRMS row count for one acquisition day."""

    product: str
    row_count: int


async def recount_source_day(
    client: httpx.AsyncClient, *, day: date, bbox: str, products: tuple[str, ...]
) -> tuple[SourceDayRecount, ...]:
    """Re-fetch the SAME dated-area CSV FIRMS itself would answer with, one product at a time.

    Never deduplicated across products here: SP-supersedes-NRT precedence and cross-satellite
    identity merging are the INGESTER's logic (`ingest/firms.py`), not something a spot-check should
    quietly re-derive. Reporting one row count per product, named, is what lets a validation report
    state which products were asked and what each answered (layer-lanes.md section 4: "N rows
    mismatched" is not actionable).
    """
    _require_firms_api_key()
    counts: list[SourceDayRecount] = []
    for product in products:
        features = await fetch_active_fires(client, bbox, _SINGLE_DAY_RANGE, product, start_date=day)
        counts.append(SourceDayRecount(product=product, row_count=len(features)))
    return tuple(counts)


@dataclass(frozen=True, slots=True)
class FireDetectionsDayValidation:
    """One day's full reconciliation: capped-ingest evidence, live source coverage, and gap accounting."""

    day: date
    lane: str
    capped_ingest: CappedIngestFinding | None
    coverage: SourceDayCoverage
    recount: tuple[SourceDayRecount, ...]
    written_detection_count: int
    status: ReconciliationStatus
    detail: str


async def validate_fire_detections_day(  # noqa: PLR0913 - one caller-supplied reconciliation input per arg, none foldable
    client: httpx.AsyncClient,
    *,
    day: date,
    bbox: str,
    products: tuple[str, ...],
    ingest_result: IngestionJobResult,
    written_detection_count: int,
    lane: str = FIRE_DETECTIONS_STREAM,
) -> FireDetectionsDayValidation:
    """Reconcile one exported day: the job's own signal first, then FIRMS' live API, never local state.

    `written_detection_count` is the sum of `detection_count` across every cell-day row this lane
    actually exported for `day` -- the aggregate grain's honest stand-in for "rows landed", since the
    stream never carries one row per raw hotspot. The gap between what FIRMS answers with and what
    landed is expected to equal `rejected + dropped`, both already itemized on `ingest_result`
    (docs/lanes/fire-detections.md section 6.3); anything left over is unaccounted and named as such.
    """
    capped = detect_capped_ingest(ingest_result, day=day, lane=lane)
    coverage = await check_source_day_coverage(client, day=day, products=products)
    if coverage.is_source_uncovered:
        return FireDetectionsDayValidation(
            day=day,
            lane=lane,
            capped_ingest=capped,
            coverage=coverage,
            recount=(),
            written_detection_count=written_detection_count,
            status="source_uncovered_day",
            detail=(
                f"{lane} {day.isoformat()}: no FIRMS product in {products} covers this day per the live "
                "availability table; a zero-row export here is a governed absence, not a gap"
            ),
        )
    recount = await recount_source_day(client, day=day, bbox=bbox, products=products)
    source_row_count = sum(entry.row_count for entry in recount)
    rejected = int(ingest_result.details.get(REJECTED_DETAIL_KEY, 0))
    dropped = int(ingest_result.details.get(DROPPED_DETAIL_KEY, 0))
    expected_gap = rejected + dropped
    actual_gap = source_row_count - written_detection_count
    if actual_gap == expected_gap:
        status: ReconciliationStatus = "reconciled"
        detail = (
            f"{lane} {day.isoformat()}: FIRMS answered {source_row_count} rows across {products}; "
            f"{written_detection_count} landed, {rejected} rejected and {dropped} dropped -- the gap "
            "accounts exactly"
        )
    else:
        status = "unaccounted_gap"
        detail = (
            f"{lane} {day.isoformat()}: FIRMS answered {source_row_count} rows across {products}; "
            f"{written_detection_count} landed but rejected({rejected}) + dropped({dropped}) = "
            f"{expected_gap} does not explain the observed gap of {actual_gap} -- "
            f"{actual_gap - expected_gap} rows are unaccounted for"
        )
    return FireDetectionsDayValidation(
        day=day,
        lane=lane,
        capped_ingest=capped,
        coverage=coverage,
        recount=recount,
        written_detection_count=written_detection_count,
        status=status,
        detail=detail,
    )
