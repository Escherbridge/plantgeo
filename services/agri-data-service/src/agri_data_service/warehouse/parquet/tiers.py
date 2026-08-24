"""The tier derivation: how one lane's base z13 Parquet becomes its z9, z5 and z0 rungs.

Layer L1: may import `foundation` (L0). May NOT import method, pipeline, planes, or interface.

PURE. A table goes in and a table comes out. Nothing here opens an object store, a database, a
socket or a clock -- RUNBOOK section 0.34.2 fuses the derivation into the drain's single pass and
puts the fusion IN THE DRIVER for exactly this reason: "a derivation bug now fails the drain too",
so the derivation has to be testable on its own, against a literal table, with no fixture behind it.

WHY THE COARSE RUNGS ARE DERIVED FROM THE BASE PARQUET AND NEVER FROM POSTGRES
(RUNBOOK section 0.32.2 decision 2). `geo.mv_soil_survey_grid`, `geo.watershed_rollup` and the
soil-field lattice are the PostGIS era's own per-layer tiers. Reading them would make the warehouse
depend on the database it is replacing, and would let a coarse rung describe a different population
than the base rung under it -- the two would drift the first time one was refreshed and the other
was not. Derived-from-base cannot drift: a coarse tier is a function of the bytes below it.

THE OUTPUT SCHEMA IS THE INPUT SCHEMA. Every rung of a lane carries the same arrow schema, so no
reader branches on zoom and `serving_zoom_tier` stays a path template rather than a lookup. Two
consequences a later reader must not be surprised by:

  * A DISSOLVED KEY COLUMN HOLDS A SHORTER CODE, NOT A DIFFERENT COLUMN. At z5 the watersheds
    `huc12` column holds an eight-digit HUC8. The column keeps its name because renaming it per
    rung would be the schema branch this design exists to avoid; the value stays self-describing
    because a HUC's level IS its length.
  * A COLUMN A DISSOLVE CANNOT HONESTLY CARRY BECOMES NULL, and the lane's schema must already
    permit that. `derive_tier` refuses rather than writing a fabricated value -- there is no single
    `feature_id` for a basin that is the union of nine others, and inventing one would put a
    warehouse surrogate key on a row no warehouse row corresponds to.

THREE STRATEGIES, ONE PER SHAPE THE THIRTEEN LANES ACTUALLY HAVE:

  * `GridAggregation` -- the lane carries longitude/latitude columns, so a coarser rung is the same
    measurements re-floored onto a coarser grid and re-aggregated. `warehouse/schemas/
    fire_detections.py` already anticipated this in its own grain argument: "Coarser aggregates are
    always derivable from a finer one by re-flooring; the reverse is not true."
  * `GeometrySimplification` -- the lane carries WKB, so a coarser rung is the same features with
    fewer vertices, optionally dissolved up a hierarchy the lane genuinely has.
  * `TierPassthrough` -- the lane has no spatial extent at all, so every rung is the same bytes.
    `calendar` is the whole of this case and RUNBOOK section 0.33.4 warns why it must be handled
    rather than assumed away: "a `zoom=13` prefix therefore does not imply geometry -- the
    derivation step must not assume it does."

WHY PASSTHROUGH RATHER THAN PUBLISHING ONLY THE BASE RUNG for a lane with no geometry: every plane
resolves a request through `serving_zoom_tier(requested_zoom)`, which returns z0 for a whole-world
request REGARDLESS of lane. A lane that published only z13 would answer such a request from a
prefix that does not exist -- an empty result indistinguishable from a genuinely empty day. Paying
four copies of a lane that has no resolution axis is the cheaper of the two mistakes, and for the
one lane in this case the object is a single day-row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import duckdb
import polars as pl

from agri_data_service.foundation.parquet.paths import validate_layer_slug
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, ZoomTier, validate_zoom_tier

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from duckdb import DuckDBPyConnection

# The base rung: the one a lane's own exporter writes, and the only one that is not derived.
BASE_ZOOM_TIER: Final[ZoomTier] = ZOOM_TIERS[-1]

# Every rung below the base, in the order a drain should derive them (finest first).
DERIVED_ZOOM_TIERS: Final[tuple[ZoomTier, ...]] = tuple(
    tier for tier in reversed(ZOOM_TIERS) if tier != BASE_ZOOM_TIER
)

# THE RESOLUTION LADDER, in degrees, and the arithmetic behind each number.
#
# A web-map pixel at zoom z spans 360 / (256 * 2**z) degrees of longitude. Each tier answers a SPAN
# of requested zooms (`zoom_tier_span`), and the number below is sized for the WIDEST viewport in
# that span -- the tier's own zoom -- because that is the viewport where byte count decides whether
# the map draws at all. Concretely, four pixels at the tier's own zoom, rounded to a clean decimal:
#
#   z9  ->  4 * 360/(256*512)  = 0.0110 deg  ->  0.01
#   z5  ->  4 * 360/(256*32)   = 0.1758 deg  ->  0.2
#   z0  ->  4 * 360/256        = 5.6250 deg  ->  5.0
#
# The cost is stated rather than hidden: a request at the TOP of a span -- z12 served by the z9
# tier -- is over-generalised relative to what it could show. That is the price of a four-rung
# uniform ladder, and it is the trade RUNBOOK section 0.32.2 decision 3 took deliberately, so
# nothing here may quietly re-introduce per-layer breakpoints to escape it.
TIER_RESOLUTION_DEGREES: Final[Mapping[ZoomTier, float]] = {9: 0.01, 5: 0.2, 0: 5.0}

# How many rows a single derivation may hold at once before the caller must batch. The drain walks
# one lane-day at a time and the largest measured day is soil-survey's ~1.5M delineations (RUNBOOK
# section 0.32.2 decision 4), which fits; this ceiling exists so a future lane that does NOT fit
# says so instead of exhausting the machine.
MAX_DERIVATION_ROWS: Final = 5_000_000

# The aggregate a column carries when many base rows collapse into one coarse row. Deliberately a
# closed vocabulary rather than a caller-supplied callable: a lane's coarse rung is a published
# contract, and "whatever this lambda did" is not a contract anyone can review.
Aggregation = Literal[
    "sum",  # additive counts and totals: detections, areas, observation counts
    "mean",  # intensive measurements: temperature, NDVI, a normalized value
    "min",
    "max",  # recency columns: the newest instant among the rows that merged
    "first",  # a column constant across the group
    "all",  # boolean gates: a coarse cell is exposable only if EVERY row in it was
    "any",
    "null",  # a column no coarse row can honestly carry; the schema must permit null
]


class TierDerivationError(ValueError):
    """Raised when a tier cannot be derived from the table it was handed, or from the spec it was given."""


class TierDerivationConflictError(ValueError):
    """Raised when a stream is registered twice with different derivations."""


@dataclass(frozen=True, slots=True)
class ColumnAggregation:
    """One non-key column's fate when base rows merge: which column, and by what aggregate."""

    column: str
    how: Aggregation


@dataclass(frozen=True, slots=True)
class GridAggregation:
    """Coarsen a lane that carries coordinates: re-floor the grid, then re-aggregate onto it.

    `key_columns` are the lane's NON-SPATIAL grain -- the columns that must stay distinct after
    merging, such as `signal_name` or `measurement_name`. The coarsened longitude and latitude join
    them to form the coarse rung's grain, so a lane that omitted one of its own grain columns here
    would silently average two different signals together.

    FLOORING, NOT ROUNDING, and the coordinate written back is the floored cell ORIGIN rather than
    its centre. It matches how the base grid was built in the first place
    (`sql/pipeline/fire_detections_day_export.sql` snaps with the same arithmetic), so a coarse cell
    contains exactly the base cells whose own origin floors into it -- an invariant a reader can
    check, which rounding to centres would break at every tier boundary.
    """

    longitude_column: str
    latitude_column: str
    key_columns: tuple[str, ...]
    aggregations: tuple[ColumnAggregation, ...]


@dataclass(frozen=True, slots=True)
class HierarchicalDissolve:
    """A lane whose identity code genuinely nests, so a coarse rung is a real parent feature.

    Only `watersheds` has this today: a HUC12's first ten digits ARE its HUC10, its first eight ARE
    its HUC8 (RUNBOOK section 0.32.2 decision 3 -- "Watersheds' HUC12/10/8/6/4 is mapped ONTO the
    ladder"). The parent therefore needs no lookup table and no second source: it is a prefix.

    `agri.spatial_cell` looks like a second candidate and is NOT one -- its `parent_cell_id` column
    exists but is populated on 0 of 1,965 production rows (measured 2026-08-23), so the cell lanes
    re-floor through `GridAggregation` instead. Do not "restore" a dissolve there on the strength of
    that column's mere existence.
    """

    code_column: str
    code_length_by_tier: Mapping[ZoomTier, int]


@dataclass(frozen=True, slots=True)
class GeometrySimplification:
    """Coarsen a lane that carries WKB: fewer vertices, and optionally fewer features.

    SIMPLIFICATION IS TOPOLOGY-PRESERVING (`ST_SimplifyPreserveTopology`). Plain `ST_Simplify` is
    free to produce a self-intersecting or empty ring, which a renderer draws as a bow tie and a
    point-in-polygon reader answers wrongly -- `planes/drought.py` runs exactly that test against
    this geometry, so an invalid coarse ring would not merely look wrong, it would report the wrong
    drought class for a location.

    `min_area_tier_squares` drops a feature the tier cannot render: below roughly one pixel there is
    nothing to draw, and carrying it costs bytes at precisely the rung where bytes are scarcest.

    IT IS A MULTIPLE OF THE TIER'S OWN RESOLUTION, NOT AN ABSOLUTE AREA, and that is the whole
    point: one fixed threshold cannot be right at 0.01, 0.2 and 5.0 degrees at once -- it would drop
    nothing at z9 and everything at z0, or the reverse. `1.0` means "smaller than one square of this
    tier's grid". The unit underneath is squared degrees rather than a projected area because the
    geometry is EPSG:4326 with no projection applied anywhere in this warehouse, so a square-metre
    threshold would be a fabricated conversion that varies with latitude.
    """

    geometry_column: str
    dissolve: HierarchicalDissolve | None = None
    aggregations: tuple[ColumnAggregation, ...] = ()
    min_area_tier_squares: float | None = None


@dataclass(frozen=True, slots=True)
class TierPassthrough:
    """A lane with no spatial extent: every rung is byte-identical to the base."""


TierStrategy = GridAggregation | GeometrySimplification | TierPassthrough


@dataclass(frozen=True, slots=True)
class TierDerivation:
    """One lane's whole answer to "what does a coarser zoom mean for me"."""

    stream: str
    strategy: TierStrategy


_DERIVATIONS: Final[dict[str, TierDerivation]] = {}


def register_tier_derivation(derivation: TierDerivation) -> TierDerivation:
    """Register `derivation` under its stream; re-registering an identical one is a no-op."""
    validate_layer_slug(derivation.stream)
    existing = _DERIVATIONS.get(derivation.stream)
    if existing is not None and existing != derivation:
        raise TierDerivationConflictError(
            f"stream {derivation.stream!r} is already registered with a different tier derivation"
        )
    _DERIVATIONS[derivation.stream] = derivation
    return derivation


def tier_derivation(stream: str) -> TierDerivation:
    """Return the registered derivation for `stream`, autoloading the lane's schema module if needed."""
    registered = _DERIVATIONS.get(stream)
    if registered is not None:
        return registered
    # Same autoload seam as `observed_stream_schema`: a lane declares its derivation beside its
    # schema, so importing one registers the other. Imported here rather than at module scope
    # because `schema.py`'s own lane modules import THIS module to declare their derivation.
    from agri_data_service.warehouse.parquet.schema import observed_stream_schema  # noqa: PLC0415 - cycle

    observed_stream_schema(stream)
    autoloaded = _DERIVATIONS.get(stream)
    if autoloaded is None:
        raise TierDerivationError(
            f"stream {stream!r} registered a Parquet schema but no tier derivation; every lane must say what a "
            f"coarser zoom means for it, because {tuple(DERIVED_ZOOM_TIERS)} are written for every lane and a "
            f"lane that declares nothing would publish a rung nobody decided the contents of"
        )
    return autoloaded


def registered_tier_derivations() -> tuple[str, ...]:
    """Return every stream that has declared a derivation so far, sorted."""
    return tuple(sorted(_DERIVATIONS))


def tier_resolution_degrees(tier: ZoomTier) -> float:
    """Return the grid size / simplification tolerance for `tier`, refusing the base rung.

    The base rung has no resolution of its own -- it is whatever the lane's exporter wrote -- so
    asking for one is a caller that has confused "derive every tier" with "derive the derived tiers".
    """
    validated = validate_zoom_tier(tier)
    resolution = TIER_RESOLUTION_DEGREES.get(validated)
    if resolution is None:
        raise TierDerivationError(
            f"z{validated} has no derivation resolution: it is the base rung, written by the lane's own exporter "
            f"at whatever grain its source has. Derive only {tuple(DERIVED_ZOOM_TIERS)}"
        )
    return resolution


def floor_to_resolution(values: pl.Expr, resolution: float) -> pl.Expr:
    """Floor a coordinate onto the `resolution`-degree grid, returning the cell ORIGIN.

    Written as `floor(v / r) * r` rather than `v - v % r` because Python's and Polars' modulo of a
    negative number is not the C modulo a reader might expect, and every longitude in this
    warehouse's PNW universe is negative -- the two forms disagree on exactly the data this repo has.
    """
    return (values / resolution).floor() * resolution


def _require_columns(frame: pl.DataFrame, columns: Sequence[str], *, role: str, stream: str) -> None:
    """Refuse a spec naming a column the table does not carry, rather than failing later and vaguely."""
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise TierDerivationError(
            f"{stream}: the tier derivation names {role} column(s) {missing} that the base table does not carry; "
            f"it has {sorted(frame.columns)}. A derivation and a schema that disagree publish a coarse rung whose "
            f"columns are not the base rung's"
        )


def _require_total_coverage(
    frame: pl.DataFrame, *, keyed: Sequence[str], aggregated: Sequence[str], stream: str
) -> None:
    """Every column must be keyed or aggregated -- an unmentioned one is a silently dropped column.

    THIS IS THE SAFETY PROPERTY OF THE WHOLE MODULE. Without it, adding a column to a lane's schema
    quietly produces a coarse rung missing that column, and the failure surfaces months later as a
    schema mismatch at serving time, on a tier nobody was looking at.
    """
    unmentioned = sorted(set(frame.columns) - {*keyed, *aggregated})
    if unmentioned:
        raise TierDerivationError(
            f"{stream}: column(s) {unmentioned} are neither part of the coarse grain nor given an aggregate, so the "
            f"derived tier would silently drop them. Add each to the grain or to `aggregations` -- 'null' is a "
            f"valid, explicit answer for a column no coarse row can honestly carry"
        )


# The two aggregate vocabularies, kept as parallel dispatch tables so that adding a member to
# `Aggregation` without teaching BOTH engines fails loudly at the lookup rather than quietly in one
# of the two paths -- a grid lane and a geometry lane would otherwise disagree about what the same
# declared aggregate means.
_POLARS_AGGREGATES: Final[Mapping[str, Callable[[pl.Expr], pl.Expr]]] = {
    # AN ALL-NULL GROUP SUMS TO NULL, NOT TO ZERO, and the guard is not decoration. Polars folds
    # `sum()` over an all-null group to 0 while SQL -- and therefore the DuckDB table below --
    # returns NULL, so the two engines disagree on exactly the case that matters. `frp_sum` is the
    # lane that proves it: `warehouse/schemas/fire_detections.py` requires "NULL, never 0, when
    # none did", because a fabricated 0 MW of fire-radiative power reads as a measured absence of
    # fire rather than as an absent measurement. `count()` counts non-null values, so `== 0` is
    # precisely "every row in this group was null".
    "sum": lambda column: pl.when(column.count() == 0).then(None).otherwise(column.sum()),
    "mean": lambda column: column.mean(),
    "min": lambda column: column.min(),
    "max": lambda column: column.max(),
    "all": lambda column: column.all(),
    "any": lambda column: column.any(),
    "first": lambda column: column.first(),
    # A TYPED null, produced by a never-taken branch off the column itself. `pl.lit(None)` would
    # land as Null dtype and the write would then be refused by `conform_to_stream_schema` for the
    # wrong reason -- a type error, not the honest "this coarse row has no such value" declared.
    "null": lambda column: pl.when(pl.lit(value=False)).then(column.first()).otherwise(None),
}

_DUCKDB_AGGREGATES: Final[Mapping[str, str]] = {
    "sum": "sum({column})",
    "mean": "avg({column})",
    "min": "min({column})",
    "max": "max({column})",
    "all": "bool_and({column})",
    "any": "bool_or({column})",
    "first": "any_value({column})",
    # A FILTER admitting no rows yields a null OF THE COLUMN'S OWN TYPE, for the same reason the
    # Polars table above routes its null through the column rather than through a bare literal.
    "null": "first({column}) FILTER (WHERE FALSE)",
}


def _aggregate_expression(spec: ColumnAggregation, *, stream: str) -> pl.Expr:
    """Return the Polars aggregate for one column."""
    build = _POLARS_AGGREGATES.get(spec.how)
    if build is None:
        raise TierDerivationError(f"{stream}: unknown aggregation {spec.how!r} for column {spec.column!r}")
    return build(pl.col(spec.column)).alias(spec.column)


def _derive_grid_tier(
    frame: pl.DataFrame, strategy: GridAggregation, *, tier: ZoomTier, stream: str
) -> pl.DataFrame:
    """Re-floor a coordinate lane onto `tier`'s grid and re-aggregate onto the coarser cells."""
    resolution = tier_resolution_degrees(tier)
    coordinates = (strategy.longitude_column, strategy.latitude_column)
    _require_columns(frame, coordinates, role="coordinate", stream=stream)
    _require_columns(frame, strategy.key_columns, role="key", stream=stream)
    _require_columns(frame, [spec.column for spec in strategy.aggregations], role="aggregated", stream=stream)
    grain = (*coordinates, *strategy.key_columns)
    _require_total_coverage(
        frame, keyed=grain, aggregated=[spec.column for spec in strategy.aggregations], stream=stream
    )
    # A ROW WITH NO POSITION HAS NO RUNG. `water-gauges` carries nullable latitude/longitude (its
    # `geometry_linked` column records which rows lack a location at all), and a null coordinate
    # cannot be floored onto a grid. Polars would otherwise group every such row together into a
    # single null cell -- a "null island" row that draws at no place and aggregates gauges from
    # everywhere into one number. They are dropped from DERIVED tiers only; the base rung, which is
    # the record rather than the spatial index, keeps them.
    located = frame.drop_nulls(list(coordinates))
    coarsened = located.with_columns(
        floor_to_resolution(pl.col(strategy.longitude_column), resolution).alias(strategy.longitude_column),
        floor_to_resolution(pl.col(strategy.latitude_column), resolution).alias(strategy.latitude_column),
    )
    aggregated = coarsened.group_by(grain).agg(
        *(_aggregate_expression(spec, stream=stream) for spec in strategy.aggregations)
    )
    # Re-select in the base table's own column order. `group_by` returns keys first, and a table
    # whose columns are correct but reordered is refused by `conform_to_stream_schema` -- a failure
    # that reads as a schema regression rather than as the column shuffle it actually is.
    return aggregated.select(frame.columns).sort(grain)


def _derive_geometry_tier(
    frame: pl.DataFrame,
    strategy: GeometrySimplification,
    *,
    tier: ZoomTier,
    stream: str,
    connection: DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Simplify (and optionally dissolve) a WKB lane onto `tier`, in DuckDB's spatial extension.

    THE ORDER IS DISSOLVE, THEN SIMPLIFY, THEN DROP, and it is not interchangeable. Simplifying
    first would generalise interior boundaries that the dissolve is about to erase anyway, spending
    the vertex budget on lines nobody will see and leaving slivers where two independently-simplified
    neighbours no longer share an edge. Dropping first would delete a feature that is below the
    threshold alone but well above it once merged with its siblings.

    `INSTALL spatial` needs the network once per machine, ever; `LOAD` is local afterwards. Measured
    available on the drain host 2026-08-23. Note that `planes/soil_survey.py`'s header claims the
    opposite ("not installable offline in this environment") -- that claim is FALSE here and was
    tested; it is why that lane hand-rolls a WKB reader, which is a defensible choice for a serving
    path that must not depend on an extension, but not a reason for this batch path to avoid one.
    """
    geometry = strategy.geometry_column
    _require_columns(frame, [geometry], role="geometry", stream=stream)
    tolerance = tier_resolution_degrees(tier)
    session = connection if connection is not None else duckdb.connect(database=":memory:")
    try:
        try:
            session.execute("LOAD spatial")
        except duckdb.Error:
            session.execute("INSTALL spatial")
            session.execute("LOAD spatial")
        session.register("base_tier", frame.to_arrow())
        if strategy.dissolve is None:
            _require_total_coverage(frame, keyed=frame.columns, aggregated=(), stream=stream)
            carried = ", ".join(column for column in frame.columns if column != geometry)
            generalised = _simplify_sql(f"ST_GeomFromWKB({geometry})", tolerance)
            query = f"SELECT {carried}, {generalised} AS {geometry} FROM base_tier"
        else:
            query = _dissolve_query(frame, strategy, tier=tier, stream=stream, tolerance=tolerance)
        wrapped = (
            f"WITH generalised AS ({query}) "
            f"SELECT * REPLACE (ST_AsWKB({geometry}) AS {geometry}) FROM generalised "
            f"WHERE NOT ST_IsEmpty({geometry})"
        )
        if strategy.min_area_tier_squares is not None:
            # An area test, not a bounding-box one: a long thin river reach has a wide envelope and
            # nothing to draw, and dropping by envelope would keep exactly the features that cost
            # the most bytes for the least ink.
            minimum_area = strategy.min_area_tier_squares * tolerance * tolerance
            wrapped += f" AND ST_Area({geometry}) >= {minimum_area}"
        derived = session.execute(wrapped).arrow()
    finally:
        if connection is None:
            session.close()
    return pl.from_arrow(derived).select(frame.columns)  # type: ignore[union-attr]


def _simplify_sql(inner_geometry: str, tolerance: float) -> str:
    """Wrap a DuckDB geometry expression in the topology-preserving simplifier at `tolerance` degrees."""
    return f"ST_SimplifyPreserveTopology({inner_geometry}, {tolerance})"


def _dissolve_query(
    frame: pl.DataFrame, strategy: GeometrySimplification, *, tier: ZoomTier, stream: str, tolerance: float
) -> str:
    """Return the SELECT that unions a hierarchy's children into their parent for `tier`."""
    dissolve = strategy.dissolve
    if dissolve is None:  # pragma: no cover - guarded by the caller
        raise TierDerivationError(f"{stream}: _dissolve_query called without a dissolve")
    _require_columns(frame, [dissolve.code_column], role="dissolve code", stream=stream)
    code_length = dissolve.code_length_by_tier.get(validate_zoom_tier(tier))
    if code_length is None:
        raise TierDerivationError(
            f"{stream}: the dissolve declares no code length for z{tier}, so the rung has no parent level to roll "
            f"up to. It names {sorted(dissolve.code_length_by_tier)}"
        )
    geometry = strategy.geometry_column
    aggregated = {spec.column: spec for spec in strategy.aggregations}
    _require_total_coverage(
        frame,
        keyed=(dissolve.code_column, geometry),
        aggregated=tuple(aggregated),
        stream=stream,
    )
    projections = [f"substr({dissolve.code_column}, 1, {code_length}) AS {dissolve.code_column}"]
    for column in frame.columns:
        if column in (dissolve.code_column, geometry):
            continue
        projections.append(_dissolve_aggregate_sql(aggregated[column], stream=stream))
    # `ST_Union_Agg` INSIDE the simplifier: the parent's boundary is the union's OUTER edge, and
    # simplifying it once is both cheaper and more faithful than simplifying every child first.
    projections.append(f"{_simplify_sql(f'ST_Union_Agg(ST_GeomFromWKB({geometry}))', tolerance)} AS {geometry}")
    columns = ", ".join(projections)
    return (
        f"SELECT {columns} FROM base_tier GROUP BY substr({dissolve.code_column}, 1, {code_length})"
    )


def _dissolve_aggregate_sql(spec: ColumnAggregation, *, stream: str) -> str:
    """Return the SQL aggregate for one dissolved column, mirroring `_aggregate_expression`'s vocabulary."""
    template = _DUCKDB_AGGREGATES.get(spec.how)
    if template is None:
        raise TierDerivationError(f"{stream}: unknown aggregation {spec.how!r} for column {spec.column!r}")
    return f"{template.format(column=spec.column)} AS {spec.column}"


def derive_tier(
    table: pl.DataFrame,
    *,
    stream: str,
    tier: ZoomTier,
    connection: DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Derive one coarse rung of `stream` from its base table, using the lane's declared strategy.

    The base rung is refused rather than returned unchanged: a caller asking to "derive z13" has
    confused the rung its exporter already wrote with the rungs this module makes, and silently
    handing back the input would let that confusion write the base tier twice.
    """
    validated = validate_zoom_tier(tier)
    if validated == BASE_ZOOM_TIER:
        raise TierDerivationError(
            f"z{validated} is the base rung and is written by {stream}'s own exporter, not derived. Derive only "
            f"{tuple(DERIVED_ZOOM_TIERS)}"
        )
    if table.height > MAX_DERIVATION_ROWS:
        raise TierDerivationError(
            f"{stream}: {table.height:,} rows exceeds MAX_DERIVATION_ROWS ({MAX_DERIVATION_ROWS:,}); derive this "
            f"lane-day in batches rather than letting one table exhaust the machine"
        )
    strategy = tier_derivation(stream).strategy
    if isinstance(strategy, TierPassthrough):
        return table
    if isinstance(strategy, GridAggregation):
        return _derive_grid_tier(table, strategy, tier=validated, stream=stream)
    return _derive_geometry_tier(table, strategy, tier=validated, stream=stream, connection=connection)


__all__ = [
    "BASE_ZOOM_TIER",
    "DERIVED_ZOOM_TIERS",
    "MAX_DERIVATION_ROWS",
    "TIER_RESOLUTION_DEGREES",
    "Aggregation",
    "ColumnAggregation",
    "GeometrySimplification",
    "GridAggregation",
    "HierarchicalDissolve",
    "TierDerivation",
    "TierDerivationConflictError",
    "TierDerivationError",
    "TierPassthrough",
    "TierStrategy",
    "ZoomTier",
    "derive_tier",
    "floor_to_resolution",
    "register_tier_derivation",
    "registered_tier_derivations",
    "tier_derivation",
    "tier_resolution_degrees",
]
