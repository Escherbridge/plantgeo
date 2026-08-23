"""The `fire-perimeters` lane's Polars serving read: one honest UTC day, however many `part-N` files.

Layer L3: may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import `interface`.
`geometry_wkb` is standard WKB with no SRID header -- every row is SRID 4326 out of band
(`warehouse/schemas/fire_perimeters.py`), never read off the bytes. This lane declares
`horizon: none` (`docs/lanes/fire-perimeters.md` #7) and writes only `kind=observed`, so this
module never accepts a `kind` argument -- there is no `kind=forecast` partition to point one at.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.paths import try_parse_partition_path
from agri_data_service.warehouse.schemas.fire_perimeters import (
    FIRE_PERIMETERS_GRAIN,
    FIRE_PERIMETERS_SCHEMA,
    FIRE_PERIMETERS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# The only stream this lane ever writes (docs/lanes/fire-perimeters.md #7). Kept as a private
# constant rather than a `kind` parameter so a caller cannot even ask for a partition that will
# never exist.
_OBSERVED_KIND: Final = "observed"


def fire_perimeters_base_uri(credentials: ObjectStoreCredentials, store: ObjectStore) -> str:
    """Return the `s3://bucket/prefix` root a production caller passes as `read_fire_perimeters_day`'s `base_uri`.

    Built from the same credentials and the same `ObjectStore` prefix the writer used, so serving
    reads the writer's own bucket rather than a second, hand-typed copy of the endpoint/bucket/prefix.
    """
    return f"s3://{credentials.bucket}/{store.prefix}"


def read_fire_perimeters_day(
    store: ObjectStore,
    *,
    day: date,
    base_uri: str,
    storage_options: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Read every WFIGS incident this lane wrote for one UTC day, sorted to the registered grain.

    `store.list_partition_keys` -- the same listing gap detection already uses -- discovers every
    `part-N.parquet` file for `day`, however many the exporter's geometry-byte spillover produced
    (`pipeline/lanes/fire_perimeters.py`); they are read in one `pl.scan_parquet` call and returned
    as one table, so a caller never has to know a day was ever split. A day with no listed part
    files -- a genuinely quiet day, a governed absence, or any date not yet reached, since this
    lane's horizon is `none` -- answers with zero rows of the canonical schema. There is no
    "nearest available day" fallback anywhere in this function: `kind=observed` is the only stream
    this lane writes, and a request for a day nothing was written for gets that day's honest empty
    answer, never a silent read of whatever the newest observed day happens to hold.

    `storage_options` is the dict `pipeline.parquet.objectstore.polars_storage_options(credentials)`
    returns for a real bucket; pass `None` (or omit it) when `base_uri` is a local path, e.g. in a
    test. `base_uri` for a production caller is `fire_perimeters_base_uri(credentials, store)`.
    """
    part_keys = _observed_day_part_keys(store, day)
    if not part_keys:
        return _empty_fire_perimeters_frame()
    root = base_uri.rstrip("/")
    uris = [f"{root}/{key}" for key in part_keys]
    options = dict(storage_options) if storage_options else None
    frame = pl.scan_parquet(uris, storage_options=options).collect()
    return frame.sort(list(FIRE_PERIMETERS_GRAIN))


def _observed_day_part_keys(store: ObjectStore, day: date) -> tuple[str, ...]:
    """Return every `part-N.parquet` relative key written for exactly this UTC day, in ascending order.

    `list_partition_keys` also returns any `absent.json` governed-absence marker for the month;
    `try_parse_partition_path` returns `None` for one of those, which is what filters it out here
    -- an absence marker is not a source of rows, and its presence or absence changes nothing about
    what this function answers.
    """
    candidates = store.list_partition_keys(FIRE_PERIMETERS_STREAM, _OBSERVED_KIND, year=day.year, month=day.month)
    return tuple(
        sorted(key for key in candidates if (parsed := try_parse_partition_path(key)) is not None and parsed.day == day)
    )


def _empty_fire_perimeters_frame() -> pl.DataFrame:
    """Zero rows, correctly typed: the honest answer for a day with no part files, future dates included."""
    empty = pl.from_arrow(FIRE_PERIMETERS_SCHEMA.arrow_schema.empty_table())
    if not isinstance(empty, pl.DataFrame):  # pragma: no cover - `from_arrow` on a `pa.Table` always returns one.
        raise TypeError("polars.from_arrow of an empty pyarrow Table did not return a DataFrame")
    return empty
