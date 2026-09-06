"""Read-only parity receipt: does the Parquet fire-perimeters lane cover every perimeter Postgres holds?

Compares the NEWEST COMPLETE published version against `geo.features` -- never the other direction,
and never a write. This is the D1 parity receipt
`conductor/tracks/environmental_postgres_retirement_20260904/spec.md` requires before ANY relation
may be dropped: "a counted comparison showing the Parquet twin covers at least every day and row the
PostgreSQL relation holds. Under-coverage is a blocker, not a note."

IT IS A POPULATION COMPARISON, NOT A DAY-BY-DAY ONE, and the lane's nature is why.
`drought/parity.py` compares per `valid_date` because a `release_series` owes one partition per
release. This lane is a `static_lookup`: `geo.features` holds ONE ROW PER WFIGS INCIDENT refreshed in
place and keeps no record of what it said yesterday, so the only honest question is whether the
newest published VERSION reproduces the population PostgreSQL is serving right now. Comparing the 45
partition days the retired `daily_series` shape left behind against anything would compare a
snapshot sliced along an axis the source does not have.

THREE THINGS ARE COUNTED THAT A NAIVE ROW COUNT WOULD MISS:

* THE UNDATED PERIMETERS. `geo.feature_observation_day` returns NULL for a row it cannot date, and
  such a row must show at EVERY slider date -- the client keeps it via
  `src/lib/map/tile-layer-date-filter.ts`'s `["!", ["has", "observed_day"]]`. The retired day export
  DELETED those rows (its `= :observed_day` equality can never match NULL), so this receipt counts
  the NULL bucket on both sides explicitly rather than letting it hide inside a total.
* THE LAYER GATE. The export and the tile function both require `layers.is_public IS TRUE`, and this
  writer -- having no `geo.layers` to consult -- cannot see a layer withdrawn from publication. That
  is a real, named divergence rather than an oversight: this receipt is the one place that can still
  observe it, so it reports the flag rather than assuming it.
* THE GEOMETRY, by digest rather than by bytes on the wire. Pulling 23 MB of WKB to compare it would
  make this receipt cost more than a publication; `md5` on both sides costs a scan.

`layers.is_public`, `features.status = 'published'` and `features.geom IS NOT NULL` in
`sql/pipeline/direct/fire_perimeters/postgres_population.sql` are a TRANSCRIPTION of
`sql/pipeline/fire_perimeters_day_export.sql`'s own WHERE clause, which is itself a transcription of
`geo.fire_risk_tiles`. `features.geometry_id IS NOT NULL` is deliberately ABSENT from both: the tile
function has no such predicate, an unlinked feature is still drawn, and orphans regrow because the
forward path does not maintain the dimension -- so that gate could silently drop a served perimeter
from either side of this comparison. That query file carries the same reasoning clause by clause.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.direct.fire_perimeters.products import FIRE_PERIMETERS_DIRECT_KIND
from agri_data_service.pipeline.direct.fire_perimeters.watermark import read_published_ladder
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: How many identifiers a receipt NAMES before it stops listing and only counts. The whole set is
#: derivable by re-running; this bound is on the single JSON object an operator reads first.
MAX_NAMED_IDENTIFIERS: Final = 25

#: Transcribes `sql/pipeline/fire_perimeters_day_export.sql`'s WHERE clause -- see the module
#: docstring for every predicate, and the query file's own header for the one predicate that is
#: deliberately absent. Extracted per `sql/AGENTS.md` and matching `sensors/parity.py` /
#: `weather_observations/parity.py`: a multi-line statement with a join and four predicates is not
#: typed inline, whatever its expected lifetime. (`drought/parity.py` keeps its statement inline
#: legitimately -- one table, one predicate, no join, which is that guide's own carve-out.) When
#: `geo.features` is finally dropped, this file and its caller are deleted together;
#: `test_sql_tree_conventions.py`'s LOADED rule is what guarantees neither can outlive the other.
_POSTGRES_POPULATION_SQL: Final = text(load_query_sql("pipeline/direct/fire_perimeters/postgres_population.sql"))

#: Asked separately because the population query CANNOT report it: `is_public IS FALSE` makes that
#: query return nothing at all, which would read as "the layer is empty" rather than "the layer is
#: withdrawn". Two very different findings that must not share one shape.
_POSTGRES_LAYER_GATE_SQL: Final = text("SELECT is_public FROM geo.layers WHERE name = 'fire-perimeters' LIMIT 1")


class FirePerimetersParityError(RuntimeError):
    """Raised when either side cannot be READ, or when PostgreSQL reports zero published perimeters.

    Under-coverage -- PostgreSQL holds a perimeter Parquet does not -- is never raised here; it is a
    counted finding in the receipt, so the receipt always finishes and reports it. Zero PostgreSQL
    rows is a different kind of failure and gets a refusal, because it has two very different causes
    that a green receipt would conflate: a mistargeted `LOCAL_SOURCE_LOADER_DATABASE_URL` (this
    project has an executor pointed at an EMPTY database right now), or a retirement that already
    dropped the relation this receipt exists to gate. Neither is "the two sides agree", which is what
    an unrefused zero-versus-zero comparison would print.
    """


@dataclass(frozen=True, slots=True)
class FirePerimetersParityReceipt:
    """A counted, perimeter-by-perimeter comparison of PostgreSQL against the newest Parquet version."""

    postgres_rows: int
    postgres_identifiers: int
    postgres_layer_is_public: bool | None
    postgres_undated_rows: int
    parquet_version_day: str | None
    parquet_rows: int
    parquet_identifiers: int
    parquet_undated_rows: int
    missing_from_parquet: tuple[str, ...]
    extra_in_parquet: tuple[str, ...]
    observed_day_mismatches: tuple[dict[str, object], ...]
    #: Counted and reported, NEVER folded into `parity_achieved`. PostGIS `ST_AsBinary` and DuckDB
    #: `ST_AsWKB` both emit standard WKB with no SRID header from the same GeoJSON, so equal digests
    #: are the expected result -- but neither library promises byte-identical serialisation of an
    #: identical geometry, so a mismatch here is evidence to LOOK at, not proof of data loss. A
    #: perimeter genuinely missing or mis-shaped shows up in `missing_from_parquet` or in a visual
    #: check; gating D1 on a serialisation detail would block a correct cutover.
    geometry_digest_mismatches: int
    #: True only when every PostgreSQL perimeter is present in Parquet, with no extras, with the same
    #: `observed_day` (NULL included), and with the same undated count on both sides.
    parity_achieved: bool

    def to_json_dict(self) -> dict[str, object]:
        """Render the receipt exactly as `main()` prints it, so a test can assert against this shape."""
        return {
            "postgres_rows": self.postgres_rows,
            "postgres_identifiers": self.postgres_identifiers,
            "postgres_layer_is_public": self.postgres_layer_is_public,
            "postgres_undated_rows": self.postgres_undated_rows,
            "parquet_version_day": self.parquet_version_day,
            "parquet_rows": self.parquet_rows,
            "parquet_identifiers": self.parquet_identifiers,
            "parquet_undated_rows": self.parquet_undated_rows,
            "missing_from_parquet": list(self.missing_from_parquet[:MAX_NAMED_IDENTIFIERS]),
            "missing_from_parquet_count": len(self.missing_from_parquet),
            "extra_in_parquet": list(self.extra_in_parquet[:MAX_NAMED_IDENTIFIERS]),
            "extra_in_parquet_count": len(self.extra_in_parquet),
            "observed_day_mismatches": list(self.observed_day_mismatches[:MAX_NAMED_IDENTIFIERS]),
            "observed_day_mismatch_count": len(self.observed_day_mismatches),
            "geometry_digest_mismatches": self.geometry_digest_mismatches,
            "parity_achieved": self.parity_achieved,
        }


@dataclass(frozen=True, slots=True)
class _PerimeterFacts:
    """One perimeter reduced to the three facts this receipt compares."""

    observed_day: str | None
    geometry_md5: str


async def postgres_layer_is_public(session: AsyncSession) -> bool | None:
    """Read-only: whether `geo.layers` still publishes this layer, or `None` when the row is gone."""
    try:
        result = await session.execute(_POSTGRES_LAYER_GATE_SQL)
    except Exception as error:  # reraised as this module's own typed read failure
        raise FirePerimetersParityError(
            f"could not read the fire-perimeters layer gate: {type(error).__name__}: {error}"
        ) from error
    row = result.first()
    return None if row is None else bool(row.is_public)


async def postgres_fire_perimeter_facts(session: AsyncSession) -> tuple[dict[str, _PerimeterFacts], int]:
    """Read-only: identifier -> facts, plus the raw row count before duplicates collapse.

    The row count is carried separately because the mapping collapses a repeated
    `uniqueFireIdentifier` and a silent collapse is exactly what a parity receipt may not do.
    `geo.features` keys one row per `external_id`, so the two numbers agree in practice; a
    disagreement is a finding about PostgreSQL, surfaced rather than absorbed.
    """
    try:
        result = await session.execute(_POSTGRES_POPULATION_SQL)
    except Exception as error:  # reraised as this module's own typed read failure
        raise FirePerimetersParityError(
            f"could not read the published geo.features fire perimeters: {type(error).__name__}: {error}"
        ) from error
    facts: dict[str, _PerimeterFacts] = {}
    rows = 0
    for row in result:
        rows += 1
        facts[row.unique_fire_identifier] = _PerimeterFacts(
            observed_day=row.observed_day,
            geometry_md5=row.geometry_md5,
        )
    return facts, rows


def parquet_fire_perimeter_facts(store: ObjectStore) -> tuple[str | None, dict[str, _PerimeterFacts], int]:
    """Read-only: the newest COMPLETE version's day, its identifier -> facts, and its raw row count.

    Only a COMPLETED version counts, for the reason `gap_fill._static_lane_census` gives: a version
    killed part-way through uploading would otherwise be compared as though it were the published
    one, and a receipt is exactly where that half-snapshot must not pass for coverage. When no
    complete version exists the day is `None` and every PostgreSQL perimeter reports missing, which
    is the correct D1 answer -- a blocker, not a read failure.
    """
    try:
        ladder = read_published_ladder(store)
        if ladder.newest_data_day is None:
            return None, {}, 0
        table = store.read_partition(
            FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, ladder.newest_data_day
        )
    except Exception as error:  # reraised as this module's own typed read failure
        raise FirePerimetersParityError(
            f"could not read the Parquet fire-perimeters ladder: {type(error).__name__}: {error}"
        ) from error
    facts: dict[str, _PerimeterFacts] = {}
    for row in table.select(["unique_fire_identifier", "observed_day", "geometry_wkb"]).to_pylist():
        observed = row["observed_day"]
        facts[row["unique_fire_identifier"]] = _PerimeterFacts(
            observed_day=None if observed is None else observed.isoformat(),
            # `usedforsecurity=False` because this is a content comparison against PostgreSQL's own
            # `md5(bytea)`, not an integrity claim; the digest is chosen to MATCH that function.
            geometry_md5=hashlib.md5(row["geometry_wkb"], usedforsecurity=False).hexdigest(),
        )
    return ladder.newest_data_day.isoformat(), facts, table.num_rows


async def build_fire_perimeters_parity_receipt(
    session: AsyncSession, store: ObjectStore
) -> FirePerimetersParityReceipt:
    """Build the counted PostgreSQL-vs-Parquet comparison. Reads both sides; writes neither."""
    is_public = await postgres_layer_is_public(session)
    postgres_facts, postgres_rows = await postgres_fire_perimeter_facts(session)
    if not postgres_facts:
        raise FirePerimetersParityError(
            "the published geo.features fire-perimeters population is empty"
            + (
                " and geo.layers reports is_public=false, so the layer is WITHDRAWN rather than gone"
                if is_public is False
                else " and no geo.layers row named 'fire-perimeters' exists at all"
                if is_public is None
                else ""
            )
            + "; refusing rather than reporting a trivial, unearned parity_achieved=True. Either this run's "
            "LOCAL_SOURCE_LOADER_DATABASE_URL points at a database that never held this layer, or the "
            "retirement this receipt gates has already dropped it -- and those are opposite conclusions"
        )
    version_day, parquet_facts, parquet_rows = parquet_fire_perimeter_facts(store)
    missing = tuple(sorted(identity for identity in postgres_facts if identity not in parquet_facts))
    extra = tuple(sorted(identity for identity in parquet_facts if identity not in postgres_facts))
    shared = sorted(set(postgres_facts) & set(parquet_facts))
    # Annotated rather than inferred, for the same reason `rows.py` annotates its population: the
    # literals below infer `dict[str, str | None]`, `dict` is invariant in its value type, and the
    # receipt field is declared `tuple[dict[str, object], ...]`.
    observed_day_mismatches: tuple[dict[str, object], ...] = tuple(
        {
            "unique_fire_identifier": identity,
            "postgres_observed_day": postgres_facts[identity].observed_day,
            "parquet_observed_day": parquet_facts[identity].observed_day,
        }
        for identity in shared
        if postgres_facts[identity].observed_day != parquet_facts[identity].observed_day
    )
    geometry_mismatches = sum(
        1 for identity in shared if postgres_facts[identity].geometry_md5 != parquet_facts[identity].geometry_md5
    )
    postgres_undated = sum(1 for facts in postgres_facts.values() if facts.observed_day is None)
    parquet_undated = sum(1 for facts in parquet_facts.values() if facts.observed_day is None)
    return FirePerimetersParityReceipt(
        postgres_rows=postgres_rows,
        postgres_identifiers=len(postgres_facts),
        postgres_layer_is_public=is_public,
        postgres_undated_rows=postgres_undated,
        parquet_version_day=version_day,
        parquet_rows=parquet_rows,
        parquet_identifiers=len(parquet_facts),
        parquet_undated_rows=parquet_undated,
        missing_from_parquet=missing,
        extra_in_parquet=extra,
        observed_day_mismatches=observed_day_mismatches,
        geometry_digest_mismatches=geometry_mismatches,
        parity_achieved=(
            not missing and not extra and not observed_day_mismatches and postgres_undated == parquet_undated
        ),
    )


def parser() -> argparse.ArgumentParser:
    """No operator knobs today -- this is a fixed, unconditional whole-population comparison."""
    return argparse.ArgumentParser(description=__doc__)


async def main(argv: Sequence[str] | None = None) -> int:
    """Print the parity receipt as one JSON object on stdout; exit 1 when coverage is incomplete.

    FIRE NO PRODUCTION ACTION: opens exactly one read-only PostgreSQL session (rolled back, never
    committed) and one read-only object-store read. An operator runs this to DECIDE whether the
    cutover may proceed, never as part of the write path itself.
    """
    parser().parse_args(argv)
    loader_database_url = settings.require_local_source_loader_database_url()
    store = ObjectStore.from_settings()
    async with local_source_loader_session(loader_database_url) as session:
        receipt = await build_fire_perimeters_parity_receipt(session, store)
        await session.rollback()
    print(json.dumps(receipt.to_json_dict(), sort_keys=True))
    return 0 if receipt.parity_achieved else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "MAX_NAMED_IDENTIFIERS",
    "FirePerimetersParityError",
    "FirePerimetersParityReceipt",
    "build_fire_perimeters_parity_receipt",
    "main",
    "parquet_fire_perimeter_facts",
    "parser",
    "postgres_fire_perimeter_facts",
    "postgres_layer_is_public",
]
