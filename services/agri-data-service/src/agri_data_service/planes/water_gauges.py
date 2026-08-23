"""DuckDB/Polars serving read for the water-gauges lane: observed and forecast, kept strictly apart.

Layer `planes` (L3): may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import
`interface` (`planes/AGENTS.md`). Reads Parquet directly from the object store through Polars using
`polars_storage_options` (`pipeline/parquet/objectstore.py`) -- the seam named in this lane's brief
-- rather than through DuckDB's separate httpfs/S3 wiring, which would duplicate that seam for no
benefit. The Hive `layer=/kind=/year=/month=/day=` layout prunes automatically: `hive_partitioning`
lets Polars skip files by path, and every written file also carries parquet row-group statistics
(`write_statistics=True`, `pipeline/parquet/objectstore.py`) that let a later `observed_day` filter
prune by content too.

`kind` is a partition, never a column branch (`conductor/code_styleguides/layer-lanes.md` section
2): every function below scopes to exactly one `kind` and stamps it explicitly on the result, so a
caller that later combines two calls' output can still trace every row back to where it came from.
No function here unions `kind=observed` with `kind=forecast`, and none falls back from one to the
other.

`zoom` is a partition on the same terms, and the glob starts inside `zoom=NN/` rather than inside
`kind=<kind>/` for a sharper reason than tidiness: this lane's grain is (site, instant), so a scan
spanning the ladder returns the SAME gauge tick once per published rung. Nothing about that frame
looks malformed -- the site numbers are real, the flows are real -- and a mean discharge computed
over it is simply wrong by a factor of however many tiers happen to exist. Every public function
therefore takes a `requested_zoom` and resolves it once through `serving_zoom_tier`.

`docs/lanes/water-gauges.md` section 4 measured 37% of one day's rows geometry-orphaned. The
exporter keeps those rows rather than filtering them (`pipeline/lanes/water_gauges.py`), and nothing
below re-introduces that filter -- `geometry_linked` is carried through as an ordinary column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.paths import zoom_prefix
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_SCHEMA, WATER_GAUGES_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

_empty_observed_frame = pl.from_arrow(WATER_GAUGES_SCHEMA.arrow_schema.empty_table())
# `pl.from_arrow` returns `DataFrame | Series`; a `pa.Table` input always yields a `DataFrame`. This
# narrows that union once, at import, rather than at every call site below.
assert isinstance(_empty_observed_frame, pl.DataFrame)
# Always supplied to `scan_parquet`'s `schema=`: it both validates that real files carry exactly
# this lane's registered contract and lets a `root` with zero partitions written yet resolve to a
# correctly typed, zero-row frame instead of raising `pl.exceptions.ComputeError` ("expanded paths
# were empty") -- verified directly against Polars 1.43.2 before relying on it; see AGENTS.md.
_OBSERVED_POLARS_SCHEMA: Final[dict[str, pl.DataType]] = dict(_empty_observed_frame.schema)


def water_gauges_partition_root(credentials: ObjectStoreCredentials, *, prefix: str = "") -> str:
    """Return the s3:// root URI this lane's partitions live under, honoring the store's own key prefix.

    `ObjectStore` (`pipeline/parquet/objectstore.py`) has no accessor for a bare root URI -- only
    for resolved object keys -- and exposes `put`/`list_keys`/`size_of` but no content read, so this
    plane reads through Polars/`polars_storage_options` directly rather than through that class.
    """
    trimmed = prefix.strip("/")
    root_prefix = f"{trimmed}/" if trimmed else ""
    return f"s3://{credentials.bucket}/{root_prefix}"


def _partition_glob(root: str, kind: PartitionKind, zoom: ZoomTier) -> str:
    """Return the glob covering one kind at ONE tier under `root`, never the other kind and never a second rung."""
    normalized_root = root if root.endswith("/") else f"{root}/"
    return f"{normalized_root}{zoom_prefix(WATER_GAUGES_STREAM, kind, zoom)}**/*.parquet"


def scan_water_gauges_observed(
    root: str,
    *,
    requested_zoom: int,
    storage_options: Mapping[str, str] | None = None,
) -> pl.LazyFrame:
    """Return a lazy frame over one tier's `kind=observed` part files under `root`, stamped `kind="observed"`.

    Keeps the exporter's true (site, instant) grain -- no aggregation, no `geometry_linked` filter.
    Hive-inferred `layer=`/`kind=`/`zoom=`/`year=`/`month=`/`day=` path columns are dropped in favour
    of the file's own registered columns plus an explicitly stamped `kind`, rather than trusting the
    inferred path column's name to keep meaning "which kind this is" under a naming change upstream.
    """
    frame = pl.scan_parquet(
        _partition_glob(root, "observed", serving_zoom_tier(requested_zoom)),
        hive_partitioning=True,
        schema=_OBSERVED_POLARS_SCHEMA,
        missing_columns="insert",
        storage_options=dict(storage_options) if storage_options else None,
    )
    return frame.select(list(WATER_GAUGES_SCHEMA.column_names)).with_columns(pl.lit("observed").alias("kind"))


def scan_water_gauges_forecast(
    root: str,
    *,
    requested_zoom: int,
    storage_options: Mapping[str, str] | None = None,
    empty_schema: Mapping[str, pl.DataType] | None = None,
) -> pl.LazyFrame:
    """Return a lazy frame over one tier's `kind=forecast` part files under `root`, stamped `kind="forecast"`.

    No forecast Parquet schema is registered for this lane yet -- no `pipeline/lanes/water_gauges.py`
    forecast exporter exists at time of writing, confirmed by grep, not assumed -- so this function
    cannot default to a known empty shape the way `scan_water_gauges_observed` does. Pass
    `empty_schema` explicitly if the caller needs a `root` with zero forecast partitions written yet
    to resolve rather than raise; leaving it unset means a genuinely empty `root` surfaces Polars'
    own `pl.exceptions.ComputeError` rather than a fabricated empty success.
    """
    glob = _partition_glob(root, "forecast", serving_zoom_tier(requested_zoom))
    options = dict(storage_options) if storage_options else None
    if empty_schema is not None:
        frame = pl.scan_parquet(
            glob,
            hive_partitioning=True,
            schema=dict(empty_schema),
            missing_columns="insert",
            storage_options=options,
        )
    else:
        frame = pl.scan_parquet(glob, hive_partitioning=True, storage_options=options)
    return frame.with_columns(pl.lit("forecast").alias("kind"))


def read_water_gauges_observed(  # noqa: PLR0913 -- every argument is an independent, documented filter axis
    root: str,
    *,
    requested_zoom: int,
    first_day: date,
    last_day: date,
    site_numbers: Sequence[str] | None = None,
    storage_options: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Collect one inclusive day range of `kind=observed` rows, at the lane's true (site, instant) grain.

    The `observed_day` filter is what predicate pushdown prunes on: every written partition file
    holds exactly one day's rows (`pipeline/lanes/water_gauges.py`), sorted and stats-carrying, so
    Polars can skip a whole file by its row-group min/max without opening it.
    """
    if last_day < first_day:
        raise ValueError(f"day range {first_day}..{last_day} runs backwards")
    frame = scan_water_gauges_observed(root, requested_zoom=requested_zoom, storage_options=storage_options).filter(
        pl.col("observed_day").is_between(first_day, last_day)
    )
    if site_numbers is not None:
        frame = frame.filter(pl.col("site_number").is_in(list(site_numbers)))
    return frame.collect()


def read_water_gauges_forecast(  # noqa: PLR0913 -- every argument is an independent, documented filter axis
    root: str,
    *,
    day_column: str,
    requested_zoom: int,
    first_day: date,
    last_day: date,
    site_numbers: Sequence[str] | None = None,
    storage_options: Mapping[str, str] | None = None,
    empty_schema: Mapping[str, pl.DataType] | None = None,
) -> pl.DataFrame:
    """Collect one inclusive day range of `kind=forecast` rows, filtered on a caller-named day column.

    No day-column name is assumed: this lane has no forecast schema yet (layer-lanes.md section 3's
    provenance columns are declared, but no forecast exporter has landed to fix a column name), so
    hardcoding `observed_day` or `valid_day` here would bake in a guess this module cannot verify
    against a real written file. The caller names the column its own forecast writer actually used.
    """
    if last_day < first_day:
        raise ValueError(f"day range {first_day}..{last_day} runs backwards")
    frame = scan_water_gauges_forecast(
        root, requested_zoom=requested_zoom, storage_options=storage_options, empty_schema=empty_schema
    ).filter(pl.col(day_column).is_between(first_day, last_day))
    if site_numbers is not None:
        frame = frame.filter(pl.col("site_number").is_in(list(site_numbers)))
    return frame.collect()
