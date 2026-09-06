"""Read-only parity receipt: does the direct-written Parquet snapshot agree with `geo.features`?

Compares WRITTEN Parquet state (this object store's newest complete base-rung version) against
`geo.features`'s currently-published `watersheds` rows -- never the other direction, never a write.

WHY THIS IS A THINNER CHECK THAN DROUGHT'S OR VEGETATION'S PARITY RECEIPT, SAID PLAINLY RATHER THAN
DRESSED UP. Those lanes walk a calendar of many days or releases and compare row counts day by day.
Watersheds is a `static_lookup` with, per `pipeline/parquet/lane_registry.py`'s own registration
comment, exactly ONE measured historical load day (2026-08-07, 9,396 rows) and no daily series to
walk at all -- so there is only ever ONE Postgres population and (at most) ONE Parquet version to
compare, and the "day-by-day" shape those other receipts have collapses here to a SINGLE row-count
comparison. This receipt proves the two CURRENT snapshots agree on count; it does not diff the huc12
identity sets row by row (a full identity diff is future work, not built here), and it says nothing
about whether either side is itself current against NHDPlus_HR -- `forward.py`'s own watermark is
the only thing in this package that asks that question. Calling this receipt as rigorous as
drought's would misstate what it actually checks.
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
from agri_data_service.foundation.parquet.lane_contract import newest_data_day
from agri_data_service.pipeline.direct.watersheds.adapter import WATERSHEDS_DIRECT_KIND
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: Transcribed from `sql/pipeline/watersheds_day_export.sql`'s own WHERE clause -- the predicates
#: MUST stay identical, or this receipt compares two differently-scoped populations and calls the
#: mismatch a defect that isn't one. One table, one predicate, no join -- the same convention
#: `pipeline/direct/drought/parity.py`'s module docstring cites for `climate/support.py`'s equally
#: small query.
_POSTGRES_WATERSHEDS_COUNT_SQL: Final = text(
    "SELECT count(*) AS row_count FROM geo.features AS f JOIN geo.layers AS l ON l.id = f.layer_id "
    "WHERE l.name = 'watersheds' AND f.status = 'published' AND f.geom IS NOT NULL "
    "AND f.properties ->> 'huc12' IS NOT NULL"
)


class WatershedsParityError(RuntimeError):
    """Raised when either side of the comparison cannot be READ."""


@dataclass(frozen=True, slots=True)
class WatershedsParityReceipt:
    """A counted comparison of what Postgres holds against what Parquet has most recently written."""

    postgres_rows: int
    parquet_version_day: str | None
    parquet_rows: int
    parquet_complete: bool
    #: True only when the newest Parquet version is COMPLETE and its row count equals Postgres's.
    #: An incomplete write is never counted towards parity, matching `pipeline/direct/drought/parity.py`'s
    #: identical refusal to trust a half-finished export as evidence of anything.
    row_count_match: bool
    parity_achieved: bool

    def to_json_dict(self) -> dict[str, object]:
        """Render the receipt exactly as `main()` prints it, so a test can assert against this shape."""
        return {
            "postgres_rows": self.postgres_rows,
            "parquet_version_day": self.parquet_version_day,
            "parquet_rows": self.parquet_rows,
            "parquet_complete": self.parquet_complete,
            "row_count_match": self.row_count_match,
            "parity_achieved": self.parity_achieved,
        }


async def postgres_watersheds_row_count(session: AsyncSession) -> int:
    """Read-only: how many `watersheds` rows `geo.features` currently publishes.

    Never opens a write: `main()` rolls the session back explicitly, and no other function in this
    module touches Postgres at all -- `forward.py` and `adapter.py` never read it either.
    """
    try:
        result = await session.execute(_POSTGRES_WATERSHEDS_COUNT_SQL)
    except Exception as error:  # reraised as this module's own typed read failure
        raise WatershedsParityError(
            f"could not read geo.features watersheds row count: {type(error).__name__}: {error}"
        ) from error
    return int(result.scalar_one())


def parquet_watersheds_state(store: ObjectStore) -> tuple[str | None, int, bool]:
    """Read-only: the newest COMPLETE base-rung version day (ISO text or `None`), its row count, and
    whether that version is complete.

    Whole-stream listing, not month-scoped: a `static_lookup` holds versions, not a calendar window
    to narrow by -- the identical reasoning `pipeline/parquet/gap_fill.py::_static_lane_census` gives
    for its own unbounded `list_partition_objects` call.
    """
    try:
        listed = store.list_partition_objects(WATERSHEDS_STREAM, WATERSHEDS_DIRECT_KIND, LANE_BASE_ZOOM_TIER)
        keys = tuple(entry.relative_path for entry in listed)
        newest_day = newest_data_day(
            layer=WATERSHEDS_STREAM, kind=WATERSHEDS_DIRECT_KIND, zoom=LANE_BASE_ZOOM_TIER, keys=keys
        )
        if newest_day is None:
            return None, 0, False
        marker = store.read_completion_marker(
            WATERSHEDS_STREAM, WATERSHEDS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, newest_day
        )
        if marker is None:
            return newest_day.isoformat(), 0, False
        return newest_day.isoformat(), marker.row_count, True
    except Exception as error:  # reraised as this module's own typed read failure
        raise WatershedsParityError(
            f"could not read the Parquet watersheds ladder: {type(error).__name__}: {error}"
        ) from error


async def build_watersheds_parity_receipt(session: AsyncSession, store: ObjectStore) -> WatershedsParityReceipt:
    """Build the counted Postgres-vs-Parquet comparison. Reads both sides; writes neither.

    UNLIKE `drought/parity.py`, a zero-row Postgres count is NOT refused here. Watersheds' own
    `postgres-watersheds` lane is the thing this whole package exists to make deletable
    (`pipeline/direct/watersheds/__init__.py`), so a future run of this receipt against a Postgres
    database that legitimately holds nothing for this layer any more is an EXPECTED end state, not a
    misconfiguration -- the receipt reports it (`parity_achieved=False` if Parquet still holds rows
    Postgres no longer does) rather than treating zero as a probable wrong-database mistake.
    """
    postgres_rows = await postgres_watersheds_row_count(session)
    version_day, parquet_rows, complete = parquet_watersheds_state(store)
    row_count_match = complete and parquet_rows == postgres_rows
    return WatershedsParityReceipt(
        postgres_rows=postgres_rows,
        parquet_version_day=version_day,
        parquet_rows=parquet_rows,
        parquet_complete=complete,
        row_count_match=row_count_match,
        parity_achieved=row_count_match,
    )


def parser() -> argparse.ArgumentParser:
    """No operator knobs -- this is a fixed, unconditional whole-lane comparison."""
    return argparse.ArgumentParser(description=__doc__)


async def main(argv: Sequence[str] | None = None) -> int:
    """Print the parity receipt as one JSON object on stdout; exit 1 when the counts disagree.

    FIRE NO PRODUCTION ACTION: opens exactly one read-only Postgres session (rolled back, never
    committed) and one read-only object-store listing. An operator runs this to decide whether the
    forward writer's snapshot is trustworthy before retiring `postgres-watersheds`, never as part of
    the write path itself.
    """
    parser().parse_args(argv)
    loader_database_url = settings.require_local_source_loader_database_url()
    store = ObjectStore.from_settings()
    async with local_source_loader_session(loader_database_url) as session:
        receipt = await build_watersheds_parity_receipt(session, store)
        await session.rollback()
    print(json.dumps(receipt.to_json_dict(), sort_keys=True))
    return 0 if receipt.parity_achieved else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "WatershedsParityError",
    "WatershedsParityReceipt",
    "build_watersheds_parity_receipt",
    "main",
    "parquet_watersheds_state",
    "parser",
    "postgres_watersheds_row_count",
]
