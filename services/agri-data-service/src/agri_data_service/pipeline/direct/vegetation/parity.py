"""Count Postgres's governed vegetation plane against Parquet's published ladder. READS ONLY.

D1's parity receipt and D2's backfill bar (`conductor/tracks/environmental_postgres_retirement_20260904/spec.md`)
both need one counted comparison, not an assertion. This module never writes Postgres and never
writes Parquet -- it lists and, optionally, reads published partitions -- so it is safe to run
against production at any time, including before `backfill.py` or `forward.py` have ever run.

DAYS ARE COMPARED BY MEMBERSHIP AND ROWS PER DAY, NEVER BY TOTALS. Two totals agreeing is not
coverage: an earlier revision asked `days_with_data >= distinct_days` and `row_count >= cell_days`,
so ONE Parquet-only day inside the window cancelled one uncovered Postgres day, and ONE day carrying
surplus rows (a stale `part-1` from a retry, a double publish) paid for a day that was entirely
empty. Both read `parity_ok` over a lane that was genuinely short, on the one receipt an irreversible
DROP is gated on. `pipeline/direct/drought/parity.py` already compared per day; this module now does
too, and `main()` exits NON-ZERO when it finds under-coverage, because a tripwire that says
"under-coverage is a blocker, never waived" and then exits 0 is defeated by its own exit code.

ONLY THE DAYS POSTGRES ACTUALLY HOLDS ARE HELD AGAINST PARQUET. This lane is sparse by construction
-- a MEASURED median 7-day gap (`pipeline/parquet/lane_registry.py:880-881`), 1,195 base days over
roughly four years -- so demanding a written day for EVERY calendar day between Postgres's own first
and last would report hundreds of days as gaps that Postgres never held either, and the verdict would
read `under_covered` forever no matter how complete the backfill was. Calendar days Postgres never
held are reported as a count, never as a finding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.pipeline.direct.vegetation.backfill import backfill_ceiling
from agri_data_service.pipeline.direct.vegetation.forward import VEGETATION_DIRECT_ALL_TIERS, _tier_status_window
from agri_data_service.pipeline.direct.vegetation.products import (
    VEGETATION_DIRECT_KIND,
    VEGETATION_METRIC_NAME,
    VEGETATION_SOURCE_KEY,
    VEGETATION_TRANSFORM_VERSION,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: How many example days a receipt names before it just states the count. A JSON receipt with 1,026
#: dates inlined is unreadable; the count is the number an operator acts on, the sample is what they
#: spot-check.
_MAX_LISTED_GAPS: Final = 25

# The identical literal predicate `sql/pipeline/vegetation_day_export.sql:69-71,79-80,85` reads,
# grouped BY DAY rather than folded into one scalar, because the receipt compares per day.
#
# THE COUNTED GRAIN IS THE EXPORTER'S, NOT THE SCHEMA'S. `sql/pipeline/vegetation_day_export.sql:158`
# groups by `(cell_id, grid_name, metric_name, metric_unit, observed_day)`, so one cell carrying two
# series that differ in `grid_name` or `metric_unit` exports TWO rows for that day. Counting
# `DISTINCT (spatial_cell_id, day)` instead counted ONE, which under-counts Postgres -- and an
# under-counted baseline makes a SHORT Parquet ladder pass. Low probability, dangerous direction, so
# this counts exactly what the exporter would write.
_GOVERNED_PLANE_DAY_COUNTS_SQL: Final = text(
    "SELECT"
    "  (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day,"
    "  COUNT(DISTINCT (cell.id, cell.grid_name, series.metric_name, series.metric_unit)) AS cell_days"
    " FROM agri.forecast_series AS series"
    " INNER JOIN agri.spatial_cell AS cell ON cell.id = series.spatial_cell_id"
    " INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id"
    " INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id"
    " INNER JOIN agri.data_source AS source ON source.id = release.data_source_id"
    " WHERE series.metric_name = :metric_name"
    "   AND series.source_transform_version = :transform_version"
    "   AND source.key = :source_key"
    "   AND observation.quality_flag = 'accepted'"
    " GROUP BY (observation.observed_at AT TIME ZONE 'UTC')::date"
)


class VegetationParityError(RuntimeError):
    """Raised when either side of the comparison cannot be READ.

    Under-coverage is never raised -- it is a counted finding in the receipt, so the receipt always
    finishes and reports it, and only the process exit code turns it into a gate.
    """


@dataclass(frozen=True, slots=True)
class ParquetLadderCensus:
    """What the published ladder holds over one window, as SETS of days rather than as totals."""

    #: Days whose BASE rung reads `data`: parts plus the completion marker that asserts they finished.
    data_days: frozenset[date]
    #: Days governed as deliberately empty at the base rung.
    absent_days: frozenset[date]
    #: Days the base rung holds neither parts nor a marker for.
    missing_days: frozenset[date]
    #: Days that are covered at the base rung but do NOT read `data` or `absent` at all four rungs.
    ladder_incomplete_days: frozenset[date]
    #: Base-rung row counts, for the days a caller asked to measure. `None` means no row-level verdict
    #: can be rendered at all -- distinct from an empty mapping, which means nothing needed measuring.
    row_counts: Mapping[date, int] | None


@dataclass(frozen=True, slots=True)
class VegetationParityReceipt:
    """A day-by-day and row-by-row comparison of what Postgres holds against what Parquet has written."""

    postgres_days: int
    postgres_rows: int
    postgres_first_day: date | None
    postgres_last_day: date | None
    parquet_data_days: int
    parquet_absent_days: int
    parquet_rows_measured: int | None
    #: Days Postgres holds that Parquet's base rung does NOT read `data` for. THE blocking finding.
    missing_from_parquet: tuple[date, ...]
    #: Days Postgres holds whose four rungs are not all settled. Also blocking: a day visible only
    #: above z13, or written but never marked, is not a day this lane can be dropped in favour of.
    ladder_incomplete: tuple[date, ...]
    #: Per day, where Parquet holds FEWER rows than Postgres. Blocking, and the reason the comparison
    #: may never be one global sum: a surplus day would otherwise pay for an empty one.
    row_shortfalls: tuple[dict[str, object], ...]
    #: Per day, where Parquet holds MORE rows than Postgres. REPORTED, NOT BLOCKING -- coverage is not
    #: at risk, but a stale part file or a double publish is worth an operator's eyes.
    row_surpluses: tuple[dict[str, object], ...]
    #: Calendar days in the censused window that NEITHER side holds. A count only: this lane is sparse
    #: by construction, so these are the ordinary shape of its history, not gaps.
    days_neither_side_holds: int

    @property
    def day_coverage(self) -> str:
        """`parity_ok` only when every Postgres day is `data` in Parquet at all four rungs."""
        return "parity_ok" if not self.missing_from_parquet and not self.ladder_incomplete else "under_covered"

    @property
    def row_coverage(self) -> str:
        """`not_measured` unless rows were read; otherwise `under_covered` on any per-day shortfall."""
        if self.parquet_rows_measured is None:
            return "not_measured"
        return "parity_ok" if not self.row_shortfalls else "under_covered"

    @property
    def parity_achieved(self) -> bool:
        """True only when no finding is under-coverage. A surplus is reported and does not gate."""
        return self.day_coverage == "parity_ok" and self.row_coverage != "under_covered"

    def to_json_dict(self) -> dict[str, object]:
        """Render the receipt exactly as `main()` prints it, so a test can assert against this shape."""
        return {
            "layer": VEGETATION_PLANE_STREAM,
            "postgres": {
                "days": self.postgres_days,
                "cell_day_rows": self.postgres_rows,
                "first_day": _isoformat(self.postgres_first_day),
                "last_day": _isoformat(self.postgres_last_day),
            },
            "parquet": {
                "days_with_data": self.parquet_data_days,
                "days_absent": self.parquet_absent_days,
                "rows_measured": self.parquet_rows_measured,
                "days_neither_side_holds": self.days_neither_side_holds,
            },
            "findings": {
                "missing_from_parquet_count": len(self.missing_from_parquet),
                "missing_from_parquet_sample": _day_sample(self.missing_from_parquet),
                "ladder_incomplete_count": len(self.ladder_incomplete),
                "ladder_incomplete_sample": _day_sample(self.ladder_incomplete),
                "row_shortfall_count": len(self.row_shortfalls),
                "row_shortfall_sample": list(self.row_shortfalls[:_MAX_LISTED_GAPS]),
                "row_surplus_count": len(self.row_surpluses),
                "row_surplus_sample": list(self.row_surpluses[:_MAX_LISTED_GAPS]),
            },
            "verdict": {
                "day_coverage": self.day_coverage,
                "row_coverage": self.row_coverage,
                "parity_achieved": self.parity_achieved,
            },
        }


def _isoformat(day: date | None) -> str | None:
    """Render one optional day as ISO text, keeping `None` distinguishable from a real date."""
    return None if day is None else day.isoformat()


def _day_sample(days: tuple[date, ...]) -> list[str]:
    """Render the first few days of a finding, so a 1,026-entry receipt stays something a human reads."""
    return [day.isoformat() for day in days[:_MAX_LISTED_GAPS]]


async def postgres_vegetation_day_counts(session: AsyncSession) -> dict[date, int]:
    """Read-only: `observed_day` -> the row count the registered exporter would write for that day.

    Never writes; the caller rolls the session back explicitly. Fails as this module's own typed read
    error rather than as a bare SQLAlchemy exception, matching `drought/parity.py`.
    """
    try:
        result = await session.execute(
            _GOVERNED_PLANE_DAY_COUNTS_SQL,
            {
                "metric_name": VEGETATION_METRIC_NAME,
                "transform_version": VEGETATION_TRANSFORM_VERSION,
                "source_key": VEGETATION_SOURCE_KEY,
            },
        )
    except Exception as error:  # reraised as this module's own typed read failure
        raise VegetationParityError(
            f"could not read the governed vegetation plane's day counts: {type(error).__name__}: {error}"
        ) from error
    return {row.observed_day: int(row.cell_days) for row in result}


def census_parquet_ladder(
    store: ObjectStore,
    *,
    first_day: date,
    last_day: date,
    count_rows_for: Collection[date] | None,
) -> ParquetLadderCensus:
    """List (and, for the days a caller names, read) the published ladder over `[first_day, last_day]`.

    Never writes. `count_rows_for` is the set of days worth measuring -- in practice the days Postgres
    holds -- so the expensive read is bounded by the comparison it feeds rather than by the whole
    published history. `None` skips the read entirely and leaves `row_counts` unmeasured.
    """
    statuses = _tier_status_window(store, first_day, last_day)
    base = statuses[LANE_BASE_ZOOM_TIER]
    data_days = frozenset(day for day, status in base.items() if status == "data")
    absent_days = frozenset(day for day, status in base.items() if status == "absent")
    missing_days = frozenset(day for day, status in base.items() if status == "missing")
    ladder_incomplete_days = frozenset(
        day
        for day in base
        if day not in missing_days
        and any(statuses[tier][day] not in {"data", "absent"} for tier in VEGETATION_DIRECT_ALL_TIERS)
    )
    row_counts: dict[date, int] | None = None
    if count_rows_for is not None:
        row_counts = {
            day: store.read_partition(
                VEGETATION_PLANE_STREAM, VEGETATION_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day
            ).num_rows
            for day in sorted(set(count_rows_for) & data_days)
        }
    return ParquetLadderCensus(
        data_days=data_days,
        absent_days=absent_days,
        missing_days=missing_days,
        ladder_incomplete_days=ladder_incomplete_days,
        row_counts=row_counts,
    )


def build_vegetation_parity_receipt(
    postgres_counts: Mapping[date, int], ladder: ParquetLadderCensus
) -> VegetationParityReceipt:
    """Compare the two sides day by day. PURE: no session, no bucket, so a test drives it with fakes."""
    postgres_days = sorted(postgres_counts)
    missing = tuple(day for day in postgres_days if day not in ladder.data_days)
    incomplete = tuple(day for day in postgres_days if day in ladder.ladder_incomplete_days)
    measured = ladder.row_counts
    shortfalls: tuple[dict[str, object], ...] = ()
    surpluses: tuple[dict[str, object], ...] = ()
    if measured is not None:
        shortfalls = tuple(
            _row_finding(day, postgres_rows=postgres_counts[day], parquet_rows=measured[day])
            for day in postgres_days
            if day in measured and measured[day] < postgres_counts[day]
        )
        surpluses = tuple(
            _row_finding(day, postgres_rows=postgres_counts[day], parquet_rows=measured[day])
            for day in postgres_days
            if day in measured and measured[day] > postgres_counts[day]
        )
    return VegetationParityReceipt(
        postgres_days=len(postgres_counts),
        postgres_rows=sum(postgres_counts.values()),
        postgres_first_day=postgres_days[0] if postgres_days else None,
        postgres_last_day=postgres_days[-1] if postgres_days else None,
        parquet_data_days=len(ladder.data_days),
        parquet_absent_days=len(ladder.absent_days),
        parquet_rows_measured=None if measured is None else sum(measured.values()),
        missing_from_parquet=missing,
        ladder_incomplete=incomplete,
        row_shortfalls=shortfalls,
        row_surpluses=surpluses,
        days_neither_side_holds=len(ladder.missing_days - set(postgres_counts)),
    )


def _row_finding(day: date, *, postgres_rows: int, parquet_rows: int) -> dict[str, object]:
    """Render one day's row disagreement, naming both sides so neither has to be inferred."""
    return {"observed_day": day.isoformat(), "postgres_rows": postgres_rows, "parquet_rows": parquet_rows}


async def run_parity(*, count_rows: bool) -> VegetationParityReceipt:
    """Produce the counted receipt an operator gates a drop decision on. Reads only; writes nothing."""
    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        postgres_counts = await postgres_vegetation_day_counts(session)
        await session.rollback()
    store = ObjectStore.from_settings()
    first_day, last_day = _census_window(postgres_counts)
    ladder = await asyncio.to_thread(
        census_parquet_ladder,
        store,
        first_day=first_day,
        last_day=last_day,
        count_rows_for=frozenset(postgres_counts) if count_rows else None,
    )
    return build_vegetation_parity_receipt(postgres_counts, ladder)


def _census_window(postgres_counts: Mapping[date, int]) -> tuple[date, date]:
    """Return the window to list, WIDENED to the whole range the two writers are responsible for.

    Bounding the window by Postgres's own MIN/MAX alone is how the ownership-boundary day stayed
    invisible: with `postgres-vegetation` stopped, that day fell outside the window and the receipt
    read clean whether or not anything had ever written it. The floor is the lane's registered
    `history_floor` and the ceiling is `backfill.py::backfill_ceiling()`, so the boundary day is
    always censused -- extended further in either direction if Postgres somehow holds a day outside
    that range, which is itself worth seeing.
    """
    lane = LANE_REGISTRY[VEGETATION_PLANE_STREAM]
    days = sorted(postgres_counts)
    first_day = min([lane.history_floor, *days])
    last_day = max([backfill_ceiling(), *days])
    return first_day, last_day


def parser() -> argparse.ArgumentParser:
    """Build the read-only vegetation parity operator."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument(
        "--count-rows",
        action="store_true",
        help=(
            "also read the base-rung row count of every day Postgres holds, and compare them per day; "
            "slow on a multi-year history, and required before the row verdict is anything but not_measured"
        ),
    )
    return built


async def main(argv: Sequence[str] | None = None) -> int:
    """Print one JSON receipt on stdout; exit NON-ZERO when the comparison found under-coverage.

    THE EXIT CODE IS THE GATE. D1 states "under-coverage is a blocker, not a note", and a receipt
    printed beside a zero exit is exactly a note: a CI step or an `&&` chain reads the code, not the
    JSON. `drought/parity.py:163` and `weather_observations/parity.py:162` already exit non-zero on
    their own under-coverage; this now matches them.
    """
    arguments = parser().parse_args(argv)
    try:
        receipt = await run_parity(count_rows=arguments.count_rows)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(receipt.to_json_dict(), sort_keys=True))
    return 0 if receipt.parity_achieved else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "ParquetLadderCensus",
    "VegetationParityError",
    "VegetationParityReceipt",
    "build_vegetation_parity_receipt",
    "census_parquet_ladder",
    "main",
    "parser",
    "postgres_vegetation_day_counts",
    "run_parity",
]
