"""Read-only parity receipt: does the Parquet evacuation-zones lane cover every zone PostgreSQL holds?

Compares WRITTEN Parquet state (this object store's listing plus one partition read at the written
base rung) against `geo.features` -- never the other direction, and never a write. This is the D1
parity receipt `conductor/tracks/environmental_postgres_retirement_20260904/spec.md` requires before
ANY relation may be dropped: "a counted comparison showing the Parquet twin covers at least every day
and row the PostgreSQL relation holds. Under-coverage is a blocker, not a note."

WHY THIS RECEIPT COUNTS KEYS AND NOT DAYS, unlike `drought/parity.py`'s. Drought is a
`release_series`: Postgres holds 209 dated releases and Parquet must hold all 209. This lane is a
`static_lookup` -- Postgres holds ONE population, refreshed in place, with no day dimension at all
(`sql/pipeline/evacuation_zones_day_export.sql`'s header: "Postgres holds no record of what was
published on any day but today"). "Every day Postgres holds" is therefore one day, and the whole
comparison is a set comparison over `natural_key`.

THE JOIN THIS QUERY DOES NOT MAKE. The export SQL LEFT JOINs `geo.geometry`; this one does not, and
does not need to. That join contributed three provenance columns and no geometry (see
`rows.py`'s module docstring), none of which participates in "which zones exist". Reading it here
would tie the parity gate to a table the same track is deleting.

TWO INNOCENT REASONS A ZONE CAN BE IN POSTGRES AND NOT IN PARQUET, and this receipt names them
rather than passing them:

1. Oregon RETIRED the area. `Fire_Evacuation_Areas_Public`'s definition query drops an area once the
   upstream integration stops re-confirming it (`ingest/evacuation_zones.py:68-72`), so a direct
   capture correctly no longer holds it -- while nothing in the repo ever unpublished it from
   `geo.features` (`docs/lanes/evacuation-zones.md` section 5: "a zone Oregon has quietly dropped
   will keep rendering on the PlantGeo map indefinitely").
2. `postgres-evacuation-zones` is STOPPED (owner decision 2026-09-04), so the Postgres side is frozen
   at whatever it last ingested while the direct side keeps moving.

Neither is auto-forgiven. `parity_achieved` is false whenever Postgres holds a key Parquet does not,
`main()` exits 1, and an operator reads `missing_from_parquet_sample` to decide which of the two it
is looking at. A receipt that waved a shortfall through on a life-safety layer because it MIGHT be
retirement would be exactly the "wrong-but-plausible" answer `layer-lanes.md` section 2 forbids.
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
from agri_data_service.foundation.parquet.paths import (
    completed_partition_days,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
)
from agri_data_service.pipeline.direct.evacuation_zones.rows import natural_key_for
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: How many missing keys the receipt names before it stops listing them. The whole set is COUNTED
#: either way; a sample is what makes a shortfall diagnosable without printing 651 keys.
MISSING_SAMPLE_SIZE: Final = 25

#: THE PREDICATES ARE TRANSCRIBED from `sql/pipeline/evacuation_zones_day_export.sql:79-82`, exactly
#: as `lane_watermark_evacuation_zones.sql` transcribes them, and must stay transcribed: a parity
#: query over a different population than the export wrote would either invent a shortfall or hide
#: one. The `geo.layers` join is not optional -- `layer.name` and `layer.is_public` are two of the
#: four predicates -- so this is two tables rather than the one `drought/parity.py` needed.
_POSTGRES_PUBLISHED_ZONES_SQL: Final = text(
    "SELECT feature.properties ->> 'globalId' AS global_id "
    "FROM geo.features AS feature "
    "JOIN geo.layers AS layer ON layer.id = feature.layer_id "
    "WHERE layer.name = 'evacuation-zones' "
    "AND layer.is_public IS TRUE "
    "AND feature.status = 'published' "
    "AND feature.geom IS NOT NULL"
)


class EvacuationZonesParityError(RuntimeError):
    """Raised when either side of the comparison cannot be READ, or Postgres reports zero zones.

    Under-coverage -- Postgres holds a zone Parquet does not -- is never raised here; it is a counted
    finding in the receipt, so the receipt always finishes and reports it. Zero Postgres rows is a
    different kind of failure: `geo.features` held 651 published evacuation areas at the last census
    (`docs/lanes/evacuation-zones.md` section 5), so an empty read is a near-certain sign this run
    pointed at the wrong database -- a mistargeted `LOCAL_SOURCE_LOADER_DATABASE_URL` -- rather than
    a genuinely empty relation. Refusing here is what stops that misconfiguration from producing a
    GREEN receipt for a table the run never actually read.
    """


@dataclass(frozen=True, slots=True)
class EvacuationZonesParityReceipt:
    """A counted, key-by-key comparison of what Postgres holds against the newest published version."""

    postgres_zones: int
    parquet_version_day: str | None
    parquet_zones: int
    parquet_version_is_absence: bool
    parquet_incomplete_versions: tuple[str, ...]
    missing_from_parquet: int
    missing_from_parquet_sample: tuple[str, ...]
    only_in_parquet: int
    #: True only when EVERY natural key Postgres publishes is present in the newest COMPLETED Parquet
    #: version. Keys only Parquet holds are reported but never fail the receipt: those are areas
    #: Oregon published after Postgres ingestion stopped, which is over-coverage, and the D1 bar is
    #: "covers at least every row the PostgreSQL relation holds".
    parity_achieved: bool

    def to_json_dict(self) -> dict[str, object]:
        """Render the receipt exactly as `main()` prints it, so a test can assert against this shape."""
        return {
            "postgres_zones": self.postgres_zones,
            "parquet_version_day": self.parquet_version_day,
            "parquet_zones": self.parquet_zones,
            "parquet_version_is_absence": self.parquet_version_is_absence,
            "parquet_incomplete_versions": list(self.parquet_incomplete_versions),
            "missing_from_parquet": self.missing_from_parquet,
            "missing_from_parquet_sample": list(self.missing_from_parquet_sample),
            "only_in_parquet": self.only_in_parquet,
            "parity_achieved": self.parity_achieved,
        }


async def postgres_published_natural_keys(session: AsyncSession) -> set[str]:
    """Read-only: every published evacuation area's natural key, as `geo.features` holds it today.

    Neither `forward.py` nor anything it imports ever opens a read against Postgres for content --
    this is the one module in the package that does, and it never writes (`main()` rolls the session
    back explicitly). That `forward.py` never imports this module is the proof the dependency
    direction holds: the write path must keep working the day `geo.features` is dropped.
    """
    try:
        result = await session.execute(_POSTGRES_PUBLISHED_ZONES_SQL)
    except Exception as error:  # reraised as this module's own typed read failure
        raise EvacuationZonesParityError(
            f"could not read geo.features evacuation zones: {type(error).__name__}: {error}"
        ) from error
    keys: set[str] = set()
    for row in result:
        if row.global_id is None:
            # `build_evacuation_zone_write` refuses a blank GlobalID before a write is attempted, so
            # a NULL here is a row no forward path could have produced. Counted as a mismatch by
            # being skipped rather than silently mapped onto some other key.
            continue
        keys.add(natural_key_for(str(row.global_id)))
    return keys


def parquet_published_version(store: ObjectStore) -> tuple[str | None, set[str], bool, tuple[str, ...]]:
    """Read-only: the newest COMPLETED version's day, its natural keys, whether it is an absence, and strays.

    Only completed versions count -- a day with parts but no completion marker is reported separately
    as `parquet_incomplete_versions`, never silently folded into either bucket, matching
    `gap_fill.py::_static_lane_census`'s identical refusal to trust a half-finished export.
    """
    try:
        listed = store.list_partition_objects(
            EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER
        )
        part_days = {
            parsed.day
            for entry in listed
            if (parsed := try_parse_partition_path(entry.relative_path)) is not None
            and parsed.layer == EVACUATION_ZONES_STREAM
            and parsed.kind == EVACUATION_ZONES_DIRECT_KIND
            and parsed.zoom == LANE_BASE_ZOOM_TIER
        }
        marker_days = {
            marker.day
            for entry in listed
            if (marker := try_parse_absence_marker_path(entry.relative_path)) is not None
            and marker.layer == EVACUATION_ZONES_STREAM
            and marker.kind == EVACUATION_ZONES_DIRECT_KIND
            and marker.zoom == LANE_BASE_ZOOM_TIER
        }
        complete_days = part_days & completed_partition_days(
            (entry.relative_path for entry in listed),
            layer=EVACUATION_ZONES_STREAM,
            kind=EVACUATION_ZONES_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
        )
        incomplete = tuple(sorted(day.isoformat() for day in part_days - complete_days - marker_days))
        newest = max(complete_days | marker_days, default=None)
        if newest is None:
            return None, set(), False, incomplete
        if newest in marker_days and newest not in complete_days:
            return newest.isoformat(), set(), True, incomplete
        table = store.read_partition(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER, newest)
        keys = {str(key) for key in table.column("natural_key").to_pylist()}
    except Exception as error:  # reraised as this module's own typed read failure
        raise EvacuationZonesParityError(
            f"could not read the Parquet evacuation-zones ladder: {type(error).__name__}: {error}"
        ) from error
    return newest.isoformat(), keys, False, incomplete


async def build_evacuation_zones_parity_receipt(
    session: AsyncSession, store: ObjectStore
) -> EvacuationZonesParityReceipt:
    """Build the counted Postgres-vs-Parquet comparison. Reads both sides; writes neither."""
    postgres_keys = await postgres_published_natural_keys(session)
    if not postgres_keys:
        raise EvacuationZonesParityError(
            "geo.features returned zero published evacuation zones; refusing rather than reporting a "
            "trivial, unearned parity_achieved=True for a table this run may never have actually read"
        )
    version_day, parquet_keys, is_absence, incomplete = parquet_published_version(store)
    missing = sorted(postgres_keys - parquet_keys)
    return EvacuationZonesParityReceipt(
        postgres_zones=len(postgres_keys),
        parquet_version_day=version_day,
        parquet_zones=len(parquet_keys),
        parquet_version_is_absence=is_absence,
        parquet_incomplete_versions=incomplete,
        missing_from_parquet=len(missing),
        missing_from_parquet_sample=tuple(missing[:MISSING_SAMPLE_SIZE]),
        only_in_parquet=len(parquet_keys - postgres_keys),
        parity_achieved=not missing,
    )


def parser() -> argparse.ArgumentParser:
    """No operator knobs today -- this is a fixed, unconditional whole-lane comparison."""
    return argparse.ArgumentParser(description=__doc__)


async def main(argv: Sequence[str] | None = None) -> int:
    """Print the parity receipt as one JSON object on stdout; exit 1 when coverage is incomplete.

    FIRE NO PRODUCTION ACTION: opens exactly one read-only Postgres session (rolled back, never
    committed) and one read-only object-store listing plus at most one partition read. An operator
    runs this to DECIDE whether the Postgres relation may be dropped, never as part of the write path.
    """
    parser().parse_args(argv)
    loader_database_url = settings.require_local_source_loader_database_url()
    store = ObjectStore.from_settings()
    async with local_source_loader_session(loader_database_url) as session:
        receipt = await build_evacuation_zones_parity_receipt(session, store)
        await session.rollback()
    print(json.dumps(receipt.to_json_dict(), sort_keys=True))
    return 0 if receipt.parity_achieved else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "MISSING_SAMPLE_SIZE",
    "EvacuationZonesParityError",
    "EvacuationZonesParityReceipt",
    "build_evacuation_zones_parity_receipt",
    "main",
    "parquet_published_version",
    "parser",
    "postgres_published_natural_keys",
]
