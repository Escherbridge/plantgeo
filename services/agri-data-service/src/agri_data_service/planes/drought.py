"""The DuckDB/Polars serving read for `drought`: a weekly USDM cadence answering a daily slider.

Layer L3 (planes): may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import
`interface`. `horizon: none` (warehouse/schemas/drought.py) -- only `kind=observed` is ever
written, so `DROUGHT_KIND` is hardcoded everywhere below rather than accepted as a parameter: there
is no code path that could silently fall through to a `kind=forecast` this lane never writes
(`conductor/code_styleguides/layer-lanes.md` section 2). This path is synchronous by the same
reasoning as `pipeline/parquet/AGENTS.md` ("this path is synchronous, deliberately"): boto3
listing, the Polars scan, and DuckDB's spatial query are all blocking calls; a route that needs
this should run it in an executor rather than this module pretending to be async.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import duckdb
import polars as pl

from agri_data_service.config import settings as _default_settings
from agri_data_service.foundation.parquet.paths import day_prefix, try_parse_partition_path
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema, polars_storage_options
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA, DROUGHT_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.config import ObjectStoreCredentials, Settings
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# The only stream this lane ever writes (docs/lanes/drought.md section 5); never `"forecast"`.
DROUGHT_KIND: Final[PartitionKind] = "observed"


class DroughtServingError(RuntimeError):
    """Raised when a resolved release cannot be read back as one honest, complete table."""


@dataclass(frozen=True, slots=True)
class DroughtReleaseAnswer:
    """One USDM release's rows, tagged with the `valid_date` that actually answered the request."""

    requested_day: date
    valid_date: date
    areas: pa.Table


@dataclass(frozen=True, slots=True)
class DroughtCoverageAnswer:
    """The most severe drought class covering one point, or the honest absence of an answer.

    `valid_date` is `None` only when no release exists at or before `requested_day` at all;
    `dm_category` is `None` whenever a release was found but no class polygon covers the point
    (USDM's D0 boundary does not cover the whole map).
    """

    requested_day: date
    valid_date: date | None
    dm_category: int | None


def drought_object_store_root(credentials: ObjectStoreCredentials, *, prefix: str = "") -> str:
    """Return the `s3://` root Polars/DuckDB scan through, matching `ObjectStore`'s own key layout."""
    trimmed = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
    return f"s3://{credentials.bucket}/{trimmed}".rstrip("/")


def drought_release_source(source: Settings | None = None) -> tuple[str, dict[str, str]]:
    """Resolve the `(root, storage_options)` pair a real caller scans releases through."""
    resolved = _default_settings if source is None else source
    credentials = resolved.require_object_store()
    root = drought_object_store_root(credentials, prefix=resolved.object_store_prefix)
    return root, polars_storage_options(credentials)


def list_observed_drought_release_days(store: ObjectStore) -> tuple[date, ...]:
    """Every distinct `valid_date` carrying at least one observed part file, sorted ascending.

    A release spilling across several part files collapses to one entry here, and a governed
    absence marker never appears: it fails `try_parse_partition_path`, so a Tuesday USDM did not
    publish is silently excluded rather than resolving as though it answered anything.
    """
    keys = store.list_partition_keys(DROUGHT_STREAM, DROUGHT_KIND)
    days = {parsed.day for parsed in (try_parse_partition_path(key) for key in keys) if parsed is not None}
    return tuple(sorted(days))


def resolve_drought_release_day(store: ObjectStore, *, on_or_before: date, now: date) -> date | None:
    """Return the newest USDM release `valid_date` at or before `on_or_before`, or `None`.

    Refuses outright when `on_or_before` is later than `now`: this lane ships no `kind=forecast`
    sibling, so a genuinely future day must answer empty rather than silently reusing the newest
    past release as if it already covered a day USDM has not assessed yet (layer-lanes.md
    section 2: "never let a reader silently fall through"). This deliberately differs from
    `planes/watersheds.py`'s `resolve_latest_observed_release_day`, which reuses its newest release
    for a future date because a HUC12 boundary barely changes -- a USDM drought class is
    weather-driven and can shift day to day, so treating last week's release as valid for a day
    that has not happened yet would misrepresent a measurement as a forecast this lane refuses to
    make. Discovery is a plain key listing; zero bytes of any release are read to answer this.
    """
    if on_or_before > now:
        return None
    eligible = tuple(day for day in list_observed_drought_release_days(store) if day <= on_or_before)
    return eligible[-1] if eligible else None


def read_drought_release(
    root: str,
    day: date,
    *,
    storage_options: Mapping[str, str] | None = None,
) -> pa.Table:
    """Read exactly one release day's part files as one table, sorted to the grain.

    `root` is the URI every part file of this stream lives under, already forward-slashed: an
    `s3://bucket/prefix` root in production (`drought_release_source`), or a local directory's
    `Path.as_posix()` in tests. The glob is scoped to `day`'s own partition folder, so this scan
    never opens a byte belonging to any other release -- the Hive layout's own directory striation
    is what prunes it, not a predicate applied after a wider read.
    """
    glob = f"{root.rstrip('/')}/{day_prefix(DROUGHT_STREAM, DROUGHT_KIND, day)}*.parquet"
    try:
        collected = pl.scan_parquet(
            glob,
            hive_partitioning=False,
            storage_options=dict(storage_options) if storage_options is not None else None,
        ).collect()
    except pl.exceptions.ComputeError as error:
        raise DroughtServingError(f"no drought release partition found at {glob!r} for {day}") from error
    if collected.height == 0:
        raise DroughtServingError(f"drought release partition for {day} read back empty; the writer refuses that")
    table = conform_to_stream_schema(collected.to_arrow(), DROUGHT_SCHEMA)
    if set(table.column("valid_date").to_pylist()) != {day}:
        raise DroughtServingError(f"drought release partition at {glob!r} does not carry valid_date {day}")
    return table


def resolve_drought_release(
    store: ObjectStore,
    root: str,
    *,
    on_or_before: date,
    now: date,
    storage_options: Mapping[str, str] | None = None,
) -> DroughtReleaseAnswer | None:
    """Answer a daily-slider request from the newest weekly release at or before it.

    Returns `None` when no release covers `on_or_before` at all (before the archive floor, or the
    request is itself in the future); the caller must render that as an honest empty answer, never
    interpolate between two weekly releases to manufacture one.
    """
    valid_date = resolve_drought_release_day(store, on_or_before=on_or_before, now=now)
    if valid_date is None:
        return None
    areas = read_drought_release(root, valid_date, storage_options=storage_options)
    return DroughtReleaseAnswer(requested_day=on_or_before, valid_date=valid_date, areas=areas)


def most_severe_class_at_point(  # noqa: PLR0913 - one parameter per point/window/credential input, none foldable
    store: ObjectStore,
    root: str,
    *,
    longitude: float,
    latitude: float,
    on_or_before: date,
    now: date,
    storage_options: Mapping[str, str] | None = None,
) -> DroughtCoverageAnswer:
    """Return the MOST SEVERE USDM class covering one WGS-84 point, from the resolved release.

    The five USDM classes are nested severity boundaries, not a spatial partition: D4 is drawn as
    a subset of D3, ... D0 as the broadest boundary, so a point inside D4 is typically inside every
    lower class too. That is a fact about USDM's own cartography stated by the task that requested
    this module, consistent with USDM's published GIS convention; it was NOT independently
    re-derived from a live geometry query here, since this session may not run one. The query below
    does not assume it: every polygon covering the point is tested and the HIGHEST `dm_category`
    among the matches wins, which is the correct answer whether or not the nesting invariant holds
    exactly for a given release.
    """
    answer = resolve_drought_release(store, root, on_or_before=on_or_before, now=now, storage_options=storage_options)
    if answer is None:
        return DroughtCoverageAnswer(requested_day=on_or_before, valid_date=None, dm_category=None)
    dm_category = _most_severe_covering_class(answer.areas, longitude=longitude, latitude=latitude)
    return DroughtCoverageAnswer(requested_day=on_or_before, valid_date=answer.valid_date, dm_category=dm_category)


def _most_severe_covering_class(areas: pa.Table, *, longitude: float, latitude: float) -> int | None:
    """Run a point-in-polygon test per class in an in-memory DuckDB session; SRID is applied out of band.

    `geom` is raw WKB with no coordinate-system header (warehouse/schemas/drought.py); every row is
    WGS 84 (EPSG 4326) by the source column's own declared type, so the point below must already be
    (longitude, latitude) in that reference system. `INSTALL`/`LOAD spatial` need network access
    only once per machine, ever -- DuckDB caches the extension binary locally after the first fetch.
    """
    connection = duckdb.connect(database=":memory:")
    try:
        try:
            connection.execute("LOAD spatial")
        except duckdb.Error:
            connection.execute("INSTALL spatial")
            connection.execute("LOAD spatial")
        connection.register("drought_release_areas", areas)
        row = connection.execute(
            "SELECT max(dm_category) FROM drought_release_areas "
            "WHERE ST_Contains(ST_GeomFromWKB(geom), ST_Point(?, ?))",
            [longitude, latitude],
        ).fetchone()
    finally:
        connection.close()
    if row is None or row[0] is None:
        return None
    return int(row[0])


__all__ = [
    "DroughtCoverageAnswer",
    "DroughtReleaseAnswer",
    "DroughtServingError",
    "drought_object_store_root",
    "drought_release_source",
    "list_observed_drought_release_days",
    "most_severe_class_at_point",
    "read_drought_release",
    "resolve_drought_release",
    "resolve_drought_release_day",
]
