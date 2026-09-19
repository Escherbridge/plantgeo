"""Bounded partition reads: one lane, one kind, one rung, ONE day, an explicit key list, a row cap.

Layer L4. Why every read names its keys instead of globbing a prefix, why a point read costs two
queries rather than one, and why a valid-day filter converts to UTC explicitly, live in `AGENTS.md`
in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from plantgeo_ml_service.foundation.lattice import from_micro_degrees, to_micro_degrees
from plantgeo_ml_service.foundation.parquet_paths import (
    classify_partition_day,
    day_prefix,
    tier_day_objects,
    try_parse_partition_path,
)
from plantgeo_ml_service.planes import refusals

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.foundation.parquet_paths import PartitionKind, ZoomTier
    from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
    from plantgeo_ml_service.pipeline.object_store import ReadOnlyObjectStore

#: How many objects one day's prefix may hold before the read refuses. A day is written by one run
#: at one rung, so a prefix this wide is a fault, not a large day.
MAX_DAY_KEYS: Final = 256

#: How many rows one point answers with. A point read returns the quantile and horizon rows of ONE
#: cell, so this is generous by two orders of magnitude and exists to bound a schema surprise.
MAX_POINT_ROWS: Final = 500

#: How many rows one cell's whole forecast series may answer with: 30 horizons x 3 quantiles x a
#: handful of signals, bounded well above the AnEn lane's own shape.
MAX_SERIES_ROWS: Final = 2_000

#: How far from the requested point a cell may sit and still be the answer. The coarsest base grain
#: this service reads is the 0.25-degree signal lattice, so a wider radius would answer a viewport
#: from a cell the caller never asked about.
NEAREST_CELL_SEARCH_DEGREES: Final = 0.25

#: How many DISTINCT cell origins the search box may name before the read refuses. The box is a
#: quarter-degree square, so a lane at any grain this service reads offers a handful; a population
#: this size means the box is not selecting, and sorting it is not a point read.
MAX_NEAREST_CANDIDATES: Final = 4_096


@dataclass(frozen=True, slots=True)
class PositionColumns:
    """Which two columns one stream records a row's position in."""

    longitude: str
    latitude: str


#: Every `kind=forecast` stream this service reads. Tripwire: the `signal` lane's `cell_id` resolves
#: through a dimension this service does not have, so a position read never goes through it.
CELL_POSITION_COLUMNS: Final = PositionColumns(longitude="cell_longitude", latitude="cell_latitude")

#: The provider-run streams (`weather-forecast`), which record a station-or-point position instead.
POINT_POSITION_COLUMNS: Final = PositionColumns(longitude="longitude", latitude="latitude")


@dataclass(frozen=True, slots=True)
class CellPosition:
    """One row's position, as the partition itself records it."""

    longitude: float
    latitude: float


@dataclass(frozen=True, slots=True)
class BoundedRows:
    """Rows a bounded read returned, and whether the cap cut the answer short."""

    rows: tuple[Mapping[str, object], ...]
    truncated: bool


def day_part_keys(
    store: ReadOnlyObjectStore,
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
    day: date,
) -> tuple[str, ...]:
    """Return the part keys of ONE written day, or refuse by naming what that day actually holds."""
    prefix = day_prefix(layer, kind, zoom, day)
    keys = store.list_relative_paths(prefix, max_keys=MAX_DAY_KEYS + 1)
    if len(keys) > MAX_DAY_KEYS:
        raise refusals.read_over_budget(
            operation="partition_day",
            detail=f"the day lists more than {MAX_DAY_KEYS} objects, which no single run writes",
        )
    objects = tier_day_objects(keys, layer=layer, kind=kind, zoom=zoom)
    status = classify_partition_day(day, objects, zoom=zoom)
    rendered_day = day.isoformat()
    if status == "absent":
        raise refusals.day_governed_absence(layer=layer, day=rendered_day)
    if status == "incomplete":
        raise refusals.day_incomplete(layer=layer, day=rendered_day)
    if status == "conflict":
        raise refusals.day_conflict(layer=layer, day=rendered_day)
    if status == "missing":
        raise refusals.day_not_written(layer=layer, day=rendered_day)
    return tuple(key for key in keys if _names_a_part_of(key, layer=layer, kind=kind, zoom=zoom, day=day))


def nearest_cell_position(  # noqa: PLR0913 - one keyword per search-box bound is the contract
    session: DuckDbSession,
    keys: Sequence[str],
    *,
    longitude: float,
    latitude: float,
    columns: PositionColumns = CELL_POSITION_COLUMNS,
    search_radius_degrees: float = NEAREST_CELL_SEARCH_DEGREES,
) -> CellPosition | None:
    """Return the position of the closest cell ORIGIN inside the search box, or `None` when it is empty.

    Two steps, and the split is the point. SQL supplies the CANDIDATE SET only: a bounded box over
    the day, `DISTINCT` so the scan returns origins rather than every row that sits on one. The
    choice among those candidates is then made in whole micro-degrees through `foundation/lattice`,
    the one flooring rule the warehouse writes its origins with, so a point read and a partition
    write agree about which cell a coordinate falls in. An `ORDER BY` on float subtraction inside
    the engine is a second, differently-rounded opinion about the same distance.
    """
    longitude_column = _quoted(columns.longitude)
    latitude_column = _quoted(columns.latitude)
    statement = (
        f"SELECT DISTINCT {longitude_column} AS longitude, {latitude_column} AS latitude "
        "FROM read_parquet(?) "
        f"WHERE {longitude_column} BETWEEN ? AND ? AND {latitude_column} BETWEEN ? AND ? "
        "LIMIT ?"
    )
    parameters = [
        _object_uris(session, keys),
        longitude - search_radius_degrees,
        longitude + search_radius_degrees,
        latitude - search_radius_degrees,
        latitude + search_radius_degrees,
        MAX_NEAREST_CANDIDATES + 1,
    ]
    candidates = session.connection.execute(statement, parameters).arrow().read_all().to_pylist()
    if len(candidates) > MAX_NEAREST_CANDIDATES:
        raise refusals.read_over_budget(
            operation="nearest_cell",
            detail=f"the search box names more than {MAX_NEAREST_CANDIDATES} distinct cells, so it selects nothing",
        )
    return _closest_candidate(candidates, longitude=longitude, latitude=latitude)


def _closest_candidate(
    candidates: Sequence[Mapping[str, object]], *, longitude: float, latitude: float
) -> CellPosition | None:
    """Return the candidate nearest the point, comparing squared distance in whole micro-degrees.

    Integers, so the comparison cannot depend on how the floats were produced, and a deterministic
    tie-break on the origin itself, so two equidistant cells always resolve the same way.
    """
    target_longitude = to_micro_degrees(longitude)
    target_latitude = to_micro_degrees(latitude)
    placed = [
        (to_micro_degrees(float(row["longitude"])), to_micro_degrees(float(row["latitude"])))  # type: ignore[arg-type]
        for row in candidates
    ]
    if not placed:
        return None
    nearest_longitude, nearest_latitude = min(
        placed,
        key=lambda origin: (
            (origin[0] - target_longitude) ** 2 + (origin[1] - target_latitude) ** 2,
            origin[0],
            origin[1],
        ),
    )
    return CellPosition(longitude=from_micro_degrees(nearest_longitude), latitude=from_micro_degrees(nearest_latitude))


def rows_at_cell_position(  # noqa: PLR0913 - one keyword per bound the read is narrowed by
    session: DuckDbSession,
    keys: Sequence[str],
    *,
    position: CellPosition,
    columns: PositionColumns = CELL_POSITION_COLUMNS,
    valid_day: date | None = None,
    valid_time_column: str | None = None,
    row_limit: int = MAX_POINT_ROWS,
) -> BoundedRows:
    """Return every row of ONE position, optionally narrowed to one UTC valid day.

    The valid-day filter converts to UTC EXPLICITLY (`AT TIME ZONE 'UTC'`) rather than casting the
    instant to a date: a session zone is one edit away from moving 6,279 of 16,743 rows onto the
    neighbouring calendar day, which is what the named-day rule exists to prevent.
    """
    predicates = [f"{_quoted(columns.longitude)} = ?", f"{_quoted(columns.latitude)} = ?"]
    parameters: list[object] = [_object_uris(session, keys), position.longitude, position.latitude]
    if valid_day is not None:
        if valid_time_column is None:
            raise refusals.serving_fault(
                operation="partition_read", fault="valid-day filter with no valid-time column named"
            )
        predicates.append(f"CAST({_quoted(valid_time_column)} AT TIME ZONE 'UTC' AS DATE) = ?")
        parameters.append(valid_day)
    parameters.append(row_limit + 1)
    statement = f"SELECT * FROM read_parquet(?) WHERE {' AND '.join(predicates)} LIMIT ?"
    return _bounded(
        session.connection.execute(statement, parameters).arrow().read_all().to_pylist(), row_limit=row_limit
    )


def newest_valid_day(session: DuckDbSession, keys: Sequence[str], *, valid_time_column: str) -> date | None:
    """Return the newest UTC valid day the named keys carry, or `None` when they carry no rows."""
    statement = (
        f"SELECT max(CAST({_quoted(valid_time_column)} AT TIME ZONE 'UTC' AS DATE)) AS newest_day FROM read_parquet(?)"
    )
    rows = session.connection.execute(statement, [_object_uris(session, keys)]).arrow().read_all().to_pylist()
    newest = rows[0]["newest_day"] if rows else None
    return newest if isinstance(newest, date) else None


def rows_for_cell_identifier(
    session: DuckDbSession,
    keys: Sequence[str],
    *,
    cell_id: str,
    issued_on: date,
    row_limit: int = MAX_SERIES_ROWS,
) -> BoundedRows:
    """Return every row one cell carries for one ISSUE day, across whatever days the keys span."""
    statement = 'SELECT * FROM read_parquet(?) WHERE "cell_id" = ? AND "issued_on" = ? LIMIT ?'
    parameters = [_object_uris(session, keys), cell_id, issued_on, row_limit + 1]
    return _bounded(
        session.connection.execute(statement, parameters).arrow().read_all().to_pylist(), row_limit=row_limit
    )


def _bounded(rows: list[dict[str, object]], *, row_limit: int) -> BoundedRows:
    """Cut one over-cap answer to its cap and say so, rather than serving an unbounded body."""
    return BoundedRows(rows=tuple(rows[:row_limit]), truncated=len(rows) > row_limit)


def _object_uris(session: DuckDbSession, keys: Sequence[str]) -> list[str]:
    """Return the `s3://` URI of every key, refusing an empty list rather than reading the world."""
    if not keys:
        raise refusals.read_over_budget(
            operation="partition_read",
            detail="a read was assembled with no object keys, and an empty key list globs the bucket",
        )
    return [session.object_uri(key) for key in keys]


def _quoted(column: str) -> str:
    """Render one SQL identifier, doubling embedded quotes so a column name cannot end the statement."""
    return '"' + column.replace('"', '""') + '"'


def _names_a_part_of(key: str, *, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> bool:
    """Return whether one listed key is a part file of exactly this lane, stream, rung and day."""
    parsed = try_parse_partition_path(key)
    if parsed is None:
        return False
    return (parsed.layer, parsed.kind, parsed.zoom, parsed.day) == (layer, kind, zoom, day)


__all__ = [
    "CELL_POSITION_COLUMNS",
    "MAX_DAY_KEYS",
    "MAX_NEAREST_CANDIDATES",
    "MAX_POINT_ROWS",
    "MAX_SERIES_ROWS",
    "NEAREST_CELL_SEARCH_DEGREES",
    "POINT_POSITION_COLUMNS",
    "BoundedRows",
    "CellPosition",
    "PositionColumns",
    "day_part_keys",
    "nearest_cell_position",
    "newest_valid_day",
    "rows_at_cell_position",
    "rows_for_cell_identifier",
]
