"""Read-only parity receipt: does the Parquet drought lane cover every day and row PostgreSQL holds?

Compares WRITTEN Parquet state (this object store's listing, at the written z13 rung) against
`geo.drought_areas` -- never the other direction, and never a write. This is the D1 parity receipt
`conductor/tracks/environmental_postgres_retirement_20260904/spec.md` requires before ANY relation
may be dropped: "a counted comparison showing the Parquet twin covers at least every day and row the
PostgreSQL relation holds. Under-coverage is a blocker, not a note."

Distinct from `pipeline/validation/drought.py::reconcile_drought_releases`, which compares written
Parquet against USDM's OWN archive -- the source system. This module compares against POSTGRES, the
relation D1 is actually gating a drop on; two "does Parquet agree with X" questions answered by
different baselines X, never conflated with one another.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.paths import completed_partition_days, try_parse_partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.drought import DROUGHT_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

DROUGHT_PARITY_KIND: Final = "observed"
#: Restated here rather than imported, matching this package's own per-module duplication of small
#: constants (`products.py::DAYS_PER_WEEK`, `forward.py::MONTHS_PER_YEAR`).
_MONTHS_PER_YEAR: Final = 12

#: One table, one predicate, no join -- inline beside its caller, per the convention
#: `pipeline/direct/AGENTS.md` "The support query is one table, one predicate, no CTE and no join"
#: names for `climate/support.py`'s equally small query.
_POSTGRES_DAY_COUNTS_SQL: Final = text(
    "SELECT valid_date::text AS valid_date, count(*) AS row_count FROM geo.drought_areas GROUP BY valid_date"
)


class DroughtParityError(RuntimeError):
    """Raised when either side of the comparison cannot be READ, or when Postgres reports zero days.

    Under-coverage -- Postgres holds a day Parquet does not -- is never raised here; it is a counted
    finding in the receipt, so the receipt always finishes and reports it. Zero Postgres days is a
    different kind of failure: `geo.drought_areas` holds 209 measured releases since 2022-08-09
    (`pipeline/parquet/lane_registry.py`), so an empty read is a near-certain sign this run pointed
    at the wrong table or database -- e.g. a mistargeted `LOCAL_SOURCE_LOADER_DATABASE_URL` -- not a
    genuinely empty relation. Refusing here is what stops that misconfiguration from producing a
    GREEN receipt for a table the run never actually read.
    """


@dataclass(frozen=True, slots=True)
class DroughtParityReceipt:
    """A counted, day-by-day comparison of what Postgres holds against what Parquet has written."""

    postgres_days: int
    postgres_rows: int
    parquet_days: int
    parquet_rows: int
    parquet_incomplete_days: tuple[str, ...]
    missing_from_parquet: tuple[str, ...]
    row_count_mismatches: tuple[dict[str, object], ...]
    #: True only when EVERY Postgres day is present in Parquet, complete, with an EQUAL row count.
    #: This is the exact D1 bar -- "covers at least every day and row Postgres holds" -- read as
    #: equality per day rather than a looser ">=": a direct-fetched release reproduces every USDM
    #: class or none of them, so a per-day undercount OR overcount is a defect worth surfacing, not
    #: something a one-sided inequality should wave through silently.
    parity_achieved: bool

    def to_json_dict(self) -> dict[str, object]:
        """Render the receipt exactly as `main()` prints it, so a test can assert against this shape."""
        return {
            "postgres_days": self.postgres_days,
            "postgres_rows": self.postgres_rows,
            "parquet_days": self.parquet_days,
            "parquet_rows": self.parquet_rows,
            "parquet_incomplete_days": list(self.parquet_incomplete_days),
            "missing_from_parquet": list(self.missing_from_parquet),
            "row_count_mismatches": list(self.row_count_mismatches),
            "parity_achieved": self.parity_achieved,
        }


async def postgres_drought_day_counts(session: AsyncSession) -> dict[str, int]:
    """Read-only: `valid_date` (ISO text) -> row count, exactly as `geo.drought_areas` holds it today.

    Neither `forward.py` nor `backfill.py` ever opens a read against Postgres -- this is the one
    module in the package that does, and it never writes (`main()` rolls the session back explicitly).
    """
    try:
        result = await session.execute(_POSTGRES_DAY_COUNTS_SQL)
    except Exception as error:  # reraised as this module's own typed read failure
        raise DroughtParityError(
            f"could not read geo.drought_areas day counts: {type(error).__name__}: {error}"
        ) from error
    return {row.valid_date: int(row.row_count) for row in result}


def parquet_drought_day_counts(
    store: ObjectStore, first_day: date, last_day: date
) -> tuple[dict[str, int], tuple[str, ...]]:
    """Read-only: `valid_date` (ISO text) -> row count, from the written z13 rung's completion markers.

    Bounded to the calendar months spanning `[first_day, last_day]` -- the Postgres days this receipt
    is actually comparing against -- one `list_partition_keys` call per month, the identical
    year/month narrowing `forward.py`'s own `_tier_status_for_weeks` cursor walk uses, rather than one
    unbounded whole-stream listing. `weather_observations/parity.py` refuses that same unbounded
    listing on principle (one marker GET per Postgres day); this bounds it instead of refusing it
    outright, since drought's ~209-week history makes one listing per month cheap either way.

    Only COMPLETED days count towards the returned mapping -- a day with parts but no completion
    marker is reported separately as `parquet_incomplete_days`, never silently folded into either
    bucket. Matches `pipeline/validation/drought.py::written_release_span`'s identical refusal to
    trust a half-finished export as evidence of anything.
    """
    try:
        keys: list[str] = []
        cursor = date(first_day.year, first_day.month, 1)
        while cursor <= last_day:
            keys.extend(
                store.list_partition_keys(
                    DROUGHT_STREAM, DROUGHT_PARITY_KIND, LANE_BASE_ZOOM_TIER, year=cursor.year, month=cursor.month
                )
            )
            cursor = date(
                cursor.year + (1 if cursor.month == _MONTHS_PER_YEAR else 0),
                1 if cursor.month == _MONTHS_PER_YEAR else cursor.month + 1,
                1,
            )
        written_days = {parsed.day for parsed in (try_parse_partition_path(key) for key in keys) if parsed is not None}
        completed = completed_partition_days(
            keys, layer=DROUGHT_STREAM, kind=DROUGHT_PARITY_KIND, zoom=LANE_BASE_ZOOM_TIER
        )
        incomplete = tuple(sorted(day.isoformat() for day in written_days - completed))
        counts: dict[str, int] = {}
        for day in sorted(completed):
            marker = store.read_completion_marker(DROUGHT_STREAM, DROUGHT_PARITY_KIND, LANE_BASE_ZOOM_TIER, day)
            if marker is not None:
                counts[day.isoformat()] = marker.row_count
    except Exception as error:  # reraised as this module's own typed read failure
        raise DroughtParityError(
            f"could not read the Parquet drought ladder: {type(error).__name__}: {error}"
        ) from error
    return counts, incomplete


async def build_drought_parity_receipt(session: AsyncSession, store: ObjectStore) -> DroughtParityReceipt:
    """Build the counted Postgres-vs-Parquet comparison. Reads both sides; writes neither.

    Refuses (`DroughtParityError`) rather than reports when Postgres holds zero days -- see that
    error's docstring for why zero is treated as a probable misconfiguration, not a real finding.
    """
    postgres_counts = await postgres_drought_day_counts(session)
    if not postgres_counts:
        raise DroughtParityError(
            "geo.drought_areas returned zero days; refusing rather than reporting a trivial, unearned "
            "parity_achieved=True for a table this run may never have actually read"
        )
    first_day = date.fromisoformat(min(postgres_counts))
    last_day = date.fromisoformat(max(postgres_counts))
    parquet_counts, incomplete = parquet_drought_day_counts(store, first_day, last_day)
    missing = tuple(sorted(day for day in postgres_counts if day not in parquet_counts))
    mismatches = tuple(
        {"valid_date": day, "postgres_rows": postgres_counts[day], "parquet_rows": parquet_counts[day]}
        for day in sorted(postgres_counts)
        if day in parquet_counts and parquet_counts[day] != postgres_counts[day]
    )
    return DroughtParityReceipt(
        postgres_days=len(postgres_counts),
        postgres_rows=sum(postgres_counts.values()),
        parquet_days=len(parquet_counts),
        parquet_rows=sum(parquet_counts.values()),
        parquet_incomplete_days=incomplete,
        missing_from_parquet=missing,
        row_count_mismatches=mismatches,
        parity_achieved=not missing and not mismatches,
    )


def parser() -> argparse.ArgumentParser:
    """No operator knobs today -- this is a fixed, unconditional whole-lane comparison."""
    return argparse.ArgumentParser(description=__doc__)


async def main(argv: Sequence[str] | None = None) -> int:
    """Print the parity receipt as one JSON object on stdout; exit 1 when coverage is incomplete.

    FIRE NO PRODUCTION ACTION: opens exactly one read-only Postgres session (rolled back, never
    committed) and one read-only object-store listing. An operator runs this to DECIDE whether a
    backfill turn is still owed, never as part of the write path itself.
    """
    parser().parse_args(argv)
    loader_database_url = settings.require_local_source_loader_database_url()
    store = ObjectStore.from_settings()
    async with local_source_loader_session(loader_database_url) as session:
        receipt = await build_drought_parity_receipt(session, store)
        await session.rollback()
    print(json.dumps(receipt.to_json_dict(), sort_keys=True))
    return 0 if receipt.parity_achieved else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "DROUGHT_PARITY_KIND",
    "DroughtParityError",
    "DroughtParityReceipt",
    "build_drought_parity_receipt",
    "main",
    "parquet_drought_day_counts",
    "parser",
    "postgres_drought_day_counts",
]
