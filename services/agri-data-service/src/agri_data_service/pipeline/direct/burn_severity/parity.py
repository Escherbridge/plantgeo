"""Read-only parity receipt: does the Parquet burn-severity lane cover every release day and row PostgreSQL holds?

Compares WRITTEN Parquet state (this object store's listing, at the written z13 rung) against
`geo.features` (layer='burn-severity') -- never the other direction, and never a write. This is the
counted comparison an eventual drop of the Postgres-side `burn-severity` rows would need: "the
Parquet twin covers at least every release day and row the PostgreSQL relation holds", the same bar
`pipeline/direct/drought/parity.py` proves for its own lane.

Distinct from MTBS's own per-cohort reconciliation (`docs/lanes/burn-severity.md` section 6:
`fetch_release_count`'s `returnCountOnly` check, already implemented in `ingest/mtbs.py`), which
compares written state against the SOURCE system. This module compares against POSTGRES, the
relation a drop is actually gating on; two "does Parquet agree with X" questions answered by
different baselines X, never conflated with one another -- the identical distinction
`drought/parity.py`'s own module docstring draws.
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
from agri_data_service.pipeline.direct.burn_severity.adapter import BURN_SEVERITY_DIRECT_KIND
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: Restated here rather than imported, matching this package's own per-module duplication of small
#: constants (`products.py`, `forward.py::_MONTHS_PER_YEAR`).
_MONTHS_PER_YEAR: Final = 12

#: Mirrors `sql/pipeline/burn_severity_day_export.sql`'s FROM/JOIN/WHERE exactly (minus its
#: `:release_day` bind, replaced with a GROUP BY, matching `drought/parity.py`'s identical
#: `valid_date` GROUP BY convention): one join, no CTE, small enough to keep inline beside its
#: caller per `code_styleguides/sql.md`.
_POSTGRES_DAY_COUNTS_SQL: Final = text(
    "SELECT (feature.properties ->> 'observedAt')::timestamptz::date::text AS release_day, count(*) AS row_count "
    "FROM geo.features AS feature "
    "JOIN geo.layers AS layer ON layer.id = feature.layer_id "
    "WHERE layer.name = 'burn-severity' "
    "AND feature.status = 'published' "
    "AND feature.geom IS NOT NULL "
    "AND feature.properties ->> 'observedAt' IS NOT NULL "
    "GROUP BY 1"
)


class BurnSeverityParityError(RuntimeError):
    """Raised when either side of the comparison cannot be READ, or when Postgres reports zero days.

    Under-coverage -- Postgres holds a release day Parquet does not -- is never raised here; it is a
    counted finding in the receipt, so the receipt always finishes and reports it. Zero Postgres
    days is a different kind of failure: `docs/lanes/burn-severity.md` section 5 measures 541
    published rows across five release days, so an empty read is a near-certain sign this run
    pointed at the wrong table or database, not a genuinely empty layer -- the identical refusal
    `drought/parity.py::DroughtParityError` states for the same reason.
    """


@dataclass(frozen=True, slots=True)
class BurnSeverityParityReceipt:
    """A counted, day-by-day comparison of what Postgres holds against what Parquet has written."""

    postgres_days: int
    postgres_rows: int
    parquet_days: int
    parquet_rows: int
    parquet_incomplete_days: tuple[str, ...]
    missing_from_parquet: tuple[str, ...]
    row_count_mismatches: tuple[dict[str, object], ...]
    #: True only when EVERY Postgres release day is present in Parquet, complete, with an EQUAL row
    #: count. A direct-fetched release day reproduces every fire from its ignition-year cohort(s) or
    #: none of them, so a per-day undercount OR overcount is a defect worth surfacing, not something
    #: a looser ">=" comparison should wave through silently.
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


async def postgres_burn_severity_day_counts(session: AsyncSession) -> dict[str, int]:
    """Read-only: release day (ISO text) -> row count, exactly as `geo.features` holds it today.

    Neither `forward.py` nor `backfill.py` ever opens a read against Postgres -- this is the one
    module in the package that does, and it never writes (`main()` rolls the session back explicitly).
    """
    try:
        result = await session.execute(_POSTGRES_DAY_COUNTS_SQL)
    except Exception as error:  # reraised as this module's own typed read failure
        raise BurnSeverityParityError(
            f"could not read geo.features burn-severity day counts: {type(error).__name__}: {error}"
        ) from error
    return {row.release_day: int(row.row_count) for row in result}


def parquet_burn_severity_day_counts(
    store: ObjectStore, first_day: date, last_day: date
) -> tuple[dict[str, int], tuple[str, ...]]:
    """Read-only: release day (ISO text) -> row count, from the written z13 rung's completion markers.

    Bounded to the calendar months spanning `[first_day, last_day]` -- the Postgres days this receipt
    is actually comparing against -- one `list_partition_keys` call per month, the identical
    year/month narrowing `forward.py::_tier_status_for_days` uses, rather than one unbounded
    whole-stream listing.

    Only COMPLETED days count towards the returned mapping -- a day with parts but no completion
    marker is reported separately as `parquet_incomplete_days`, never silently folded into either
    bucket. `pipeline/validation/burn_severity.py::reconcile_burn_severity_release` reconciles
    Postgres against MTBS's OWN live service rather than against Parquet, so it has no equivalent
    check for this receipt to match; the "never trust a half-finished export" discipline here is
    instead restated from `pipeline/direct/drought/parity.py::parquet_drought_day_counts`, which
    guards the identical case.
    """
    try:
        keys: list[str] = []
        cursor = date(first_day.year, first_day.month, 1)
        while cursor <= last_day:
            keys.extend(
                store.list_partition_keys(
                    BURN_SEVERITY_STREAM,
                    BURN_SEVERITY_DIRECT_KIND,
                    LANE_BASE_ZOOM_TIER,
                    year=cursor.year,
                    month=cursor.month,
                )
            )
            cursor = date(
                cursor.year + (1 if cursor.month == _MONTHS_PER_YEAR else 0),
                1 if cursor.month == _MONTHS_PER_YEAR else cursor.month + 1,
                1,
            )
        written_days = {parsed.day for parsed in (try_parse_partition_path(key) for key in keys) if parsed is not None}
        completed = completed_partition_days(
            keys, layer=BURN_SEVERITY_STREAM, kind=BURN_SEVERITY_DIRECT_KIND, zoom=LANE_BASE_ZOOM_TIER
        )
        incomplete = tuple(sorted(day.isoformat() for day in written_days - completed))
        counts: dict[str, int] = {}
        for day in sorted(completed):
            marker = store.read_completion_marker(
                BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day
            )
            if marker is not None:
                counts[day.isoformat()] = marker.row_count
    except Exception as error:  # reraised as this module's own typed read failure
        raise BurnSeverityParityError(
            f"could not read the Parquet burn-severity ladder: {type(error).__name__}: {error}"
        ) from error
    return counts, incomplete


async def build_burn_severity_parity_receipt(session: AsyncSession, store: ObjectStore) -> BurnSeverityParityReceipt:
    """Build the counted Postgres-vs-Parquet comparison. Reads both sides; writes neither.

    Refuses (`BurnSeverityParityError`) rather than reports when Postgres holds zero days -- see
    that error's docstring for why zero is treated as a probable misconfiguration, not a real finding.
    """
    postgres_counts = await postgres_burn_severity_day_counts(session)
    if not postgres_counts:
        raise BurnSeverityParityError(
            "geo.features returned zero burn-severity release days; refusing rather than reporting a "
            "trivial, unearned parity_achieved=True for a table this run may never have actually read"
        )
    first_day = date.fromisoformat(min(postgres_counts))
    last_day = date.fromisoformat(max(postgres_counts))
    parquet_counts, incomplete = parquet_burn_severity_day_counts(store, first_day, last_day)
    missing = tuple(sorted(day for day in postgres_counts if day not in parquet_counts))
    mismatches = tuple(
        {"release_day": day, "postgres_rows": postgres_counts[day], "parquet_rows": parquet_counts[day]}
        for day in sorted(postgres_counts)
        if day in parquet_counts and parquet_counts[day] != postgres_counts[day]
    )
    return BurnSeverityParityReceipt(
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
        receipt = await build_burn_severity_parity_receipt(session, store)
        await session.rollback()
    print(json.dumps(receipt.to_json_dict(), sort_keys=True))
    return 0 if receipt.parity_achieved else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "BurnSeverityParityError",
    "BurnSeverityParityReceipt",
    "build_burn_severity_parity_receipt",
    "main",
    "parquet_burn_severity_day_counts",
    "parser",
    "postgres_burn_severity_day_counts",
]
