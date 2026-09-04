"""D2's counted parity receipt: Parquet's weather-observations coverage against PostgreSQL's.

READ-ONLY. This module opens a PostgreSQL session and a Parquet object store, reads both, and writes
neither -- it is the operator's evidence for a drop packet (track spec, D1 item 1: "a counted
comparison showing the Parquet twin covers at least every day and row the PostgreSQL relation
holds"), not a migration or a backfill. The governance filters in `_POSTGRES_DAY_COUNTS_SQL` mirror
`sql/pipeline/weather_observations_day_export.sql` exactly (published, geometry-linked, all seven
value keys present) so this counts precisely the population the existing Postgres-reading lane adapter
would itself export -- a parity receipt that used a looser predicate could report "matched" against
rows nothing would ever have published.

WHY POSTGRES IS STILL THE GROUND LIST, AND WHY THIS NEVER LISTS THE WHOLE STREAM. This lane's direct
writer can publish a day Postgres never held (any day from its own deployment forward), and that is
not under-coverage -- D2 only requires Parquet to cover what Postgres ALREADY holds. So the walk is
bounded by the days Postgres counts: one `read_completion_marker` GET per Postgres day, never a
`list_partition_keys()` over the whole stream. A parity tool that listed the whole bucket to report
"days Parquet has that Postgres doesn't" would itself be the A4 tripwire (`pipeline/parquet/AGENTS.md`)
this track's acceptance criteria forbid, for a number D2 does not ask for -- so it is not computed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.writer import resolve_layer_id
from agri_data_service.pipeline.direct.weather_observations.adapter import WEATHER_OBSERVATIONS_DIRECT_KIND
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore
from agri_data_service.warehouse.schemas.weather_observations import WEATHER_OBSERVATIONS_STREAM

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Mirrors `sql/pipeline/weather_observations_day_export.sql`'s WHERE clause and key-presence guard,
#: minus the single-day equality predicate: this counts every day, not one. Extracted per
#: `sql/AGENTS.md` -- a multi-line statement with a WHERE, a GROUP BY and a HAVING is not typed
#: inline.
_POSTGRES_DAY_COUNTS_SQL: Final = text(load_query_sql("pipeline/direct/weather_observations/postgres_day_counts.sql"))

_MISMATCH_SAMPLE_LIMIT: Final = 20


class WeatherObservationsParityError(RuntimeError):
    """Raised when the parity comparison cannot be completed against either side."""


@dataclass(frozen=True, slots=True)
class DayCoverage:
    """One day's counted coverage on both sides."""

    day: date
    postgres_rows: int
    parquet_status: str
    parquet_rows: int

    @property
    def under_covered(self) -> bool:
        """True when Parquet holds fewer rows than PostgreSQL for a day PostgreSQL actually has."""
        return self.parquet_rows < self.postgres_rows


@dataclass(frozen=True, slots=True)
class ParityReceipt:
    """The counted comparison D1's drop packet cites."""

    postgres_days: int
    postgres_rows: int
    under_covered: tuple[DayCoverage, ...]
    checked: tuple[DayCoverage, ...]

    @property
    def verdict(self) -> str:
        """`parity_matched` iff every PostgreSQL day is covered by at least as many Parquet rows."""
        return "under_coverage" if self.under_covered else "parity_matched"

    def to_summary(self) -> dict[str, object]:
        """Render the JSON receipt shape an operator or a drop packet can cite directly."""
        return {
            "verdict": self.verdict,
            "postgres_days": self.postgres_days,
            "postgres_rows": self.postgres_rows,
            "under_covered_day_count": len(self.under_covered),
            "under_covered_days": [
                {
                    "day": coverage.day.isoformat(),
                    "postgres_rows": coverage.postgres_rows,
                    "parquet_status": coverage.parquet_status,
                    "parquet_rows": coverage.parquet_rows,
                }
                for coverage in self.under_covered[:_MISMATCH_SAMPLE_LIMIT]
            ],
        }


async def postgres_day_counts(session: AsyncSession, *, layer_id: str) -> dict[date, int]:
    """Return every day PostgreSQL's `geo.features` holds an exportable weather-observations row for."""
    result = await session.execute(_POSTGRES_DAY_COUNTS_SQL, {"layer_id": layer_id})
    counts: dict[date, int] = {}
    for row in result.mappings():
        day = row["observed_day"]
        row_count = row["row_count"]
        if day is None:
            raise WeatherObservationsParityError("Postgres day-count query returned a NULL observed_day")
        counts[day] = int(row_count)
    return counts


def parquet_day_coverage(store: ObjectStore, day: date) -> tuple[str, int]:
    """Return one day's base-rung (z13) status and row count, at the cost of one marker GET, not a download."""
    marker = store.read_completion_marker(
        WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day
    )
    if marker is not None:
        return "data", marker.row_count
    if store.absence_exists(WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day):
        return "absent", 0
    return "missing", 0


async def build_parity_receipt(session: AsyncSession, store: ObjectStore) -> ParityReceipt:
    """Read both sides once and return the counted comparison, writing to neither."""
    layer_id = await resolve_layer_id(session, WEATHER_OBSERVATIONS_STREAM)
    postgres_counts = await postgres_day_counts(session, layer_id=layer_id)
    checked: list[DayCoverage] = []
    for day, postgres_rows in sorted(postgres_counts.items()):
        parquet_status, parquet_rows = parquet_day_coverage(store, day)
        checked.append(
            DayCoverage(day=day, postgres_rows=postgres_rows, parquet_status=parquet_status, parquet_rows=parquet_rows)
        )
    under_covered = tuple(coverage for coverage in checked if coverage.under_covered)
    return ParityReceipt(
        postgres_days=len(postgres_counts),
        postgres_rows=sum(postgres_counts.values()),
        under_covered=under_covered,
        checked=tuple(checked),
    )


async def main() -> int:
    """Print the parity receipt as one JSON line and exit non-zero on `under_coverage`.

    Opens `LOCAL_SOURCE_LOADER_DATABASE_URL` for the read-only Postgres count and the configured
    object store for the read-only Parquet count. Neither is written to; there is no `--apply`
    flag on this module because there is nothing here for one to gate.
    """
    database_url = settings.require_local_source_loader_database_url()
    credentials = settings.require_object_store()
    store = ObjectStore(BotoObjectStoreBackend.from_credentials(credentials), prefix=settings.object_store_prefix)
    async with local_source_loader_session(database_url) as session:
        receipt = await build_parity_receipt(session, store)
    print(json.dumps({"event": "weather_observations_parity", **receipt.to_summary()}, sort_keys=True))
    return 1 if receipt.verdict == "under_coverage" else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "DayCoverage",
    "ParityReceipt",
    "WeatherObservationsParityError",
    "build_parity_receipt",
    "main",
    "parquet_day_coverage",
    "postgres_day_counts",
]
