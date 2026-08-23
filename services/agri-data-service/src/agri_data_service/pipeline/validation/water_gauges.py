"""Reconcile one written water-gauges observed-day Parquet partition against USGS NWIS itself.

Layer `pipeline` (L2): may use httpx and the object store; may NOT import `method`, `planes`, or
`interface`. Compares WRITTEN Parquet against a fresh USGS NWIS pull for the same day and bbox --
never local intermediate state, per `conductor/code_styleguides/layer-lanes.md` section 4. See
`docs/lanes/water-gauges.md` section 6 for why this validator does not exist there yet, and what it
must get right:

* A silent gauge (`flow_cfs` null) is a real observation, not a defect -- reported on its own axis,
  never folded into a mismatch count.
* The day axis is the PUBLISHER-NAMED day, `geo.feature_observation_day`'s own rule
  (`drizzle/0015_tile_observation_day.sql`): the first ten characters of the raw timestamp text,
  never a `datetime.fromisoformat(...).date()` conversion, which applies whatever UTC offset the
  string carries before truncating and moved 6,279 of 16,743 production rows onto the day after the
  one they name. `publisher_named_day` below replicates that exact rule.
* Failures name the day, the lane, and the source response (never "N rows mismatched").
* `ObjectStore` (`pipeline/parquet/objectstore.py`, out of this lane's ownership) exposes
  `put`/`list_keys`/`size_of` but no content `get`. This module uses `store` only for the
  listing/existence checks layer-lanes.md section 4 asks gap detection to prefer
  (`partition_exists`, `absence_exists`), and takes an injected `PartitionContentReader` for the
  partition's actual bytes -- a real implementation is the caller's to wire (e.g. one boto3
  `get_object`), so this module carries no network client of its own beyond the USGS fetch below.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Protocol

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.source import HistoryWindow
from agri_data_service.ingest.usgs_nwis import NWIS_ARCHIVE_BOUNDS, fetch_streamflow_history
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    import pyarrow as pa

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

    SourceDayFetcher = Callable[[date, str], Awaitable["Sequence[Mapping[str, object]]"]]


class WaterGaugesValidationError(RuntimeError):
    """Raised when this lane's own bookkeeping is internally inconsistent -- never for a source gap."""


class PartitionContentReader(Protocol):
    """Reads one written partition's raw bytes by its absolute object key (`ObjectStore.key_for`
    output), returning `None` when nothing is stored there.
    """

    def __call__(self, key: str) -> bytes | None: ...


def publisher_named_day(raw_timestamp: str) -> date:
    """Return the first ten characters of a raw ISO-8601 string as a date.

    Replicates `geo.feature_observation_day` exactly (`drizzle/0015_tile_observation_day.sql`) so
    this validator's day axis can never disagree with the one the written Parquet's `observed_day`
    column was computed with. NEVER `datetime.fromisoformat(raw_timestamp).date()`: parsing first
    would apply whatever offset the string carries before truncating to a day, and for a
    non-UTC-midnight timestamp that lands one day later than the publisher named -- exactly the
    conversion that moved 6,279 of 16,743 production water-gauges rows onto the wrong day.
    """
    return date.fromisoformat(raw_timestamp[:10])


async def fetch_source_day_from_nwis(day: date, bbox: str) -> Sequence[Mapping[str, object]]:
    """Fetch USGS NWIS's own daily-values holdings for one calendar day across a bbox.

    Reuses the archive lane's own tested tiling, sentinel-guard and offset-stamping
    (`ingest.usgs_nwis.fetch_streamflow_history`) rather than re-deriving them a second time. The
    window is the half-open UTC span covering exactly the one calendar day named by `day`.
    """
    window_start = datetime.combine(day, time.min, tzinfo=UTC)
    window = HistoryWindow(start=window_start, end=window_start + timedelta(days=1))
    async with upstream_client(NWIS_ARCHIVE_BOUNDS) as client:
        return await fetch_streamflow_history(client, bbox, window)


def _site_numbers(records: Sequence[Mapping[str, object]]) -> frozenset[str]:
    """Return the distinct, non-blank site numbers named by USGS NWIS's own records."""
    return frozenset(
        str(record["siteNo"]) for record in records if isinstance(record.get("siteNo"), str) and record["siteNo"]
    )


def _written_site_flow_status(table: pa.Table) -> tuple[frozenset[str], frozenset[str]]:
    """Split a written partition's site numbers into those with a real flow reading and those without.

    A site whose every written row carries a null `flow_cfs` for the day is a silent gauge -- a real
    observation, per docs/lanes/water-gauges.md section 5 -- so it is reported on its own axis
    (`silent_written_sites`) rather than folded into `missing_sites`.
    """
    site_numbers = table.column("site_number").to_pylist()
    flow_values = table.column("flow_cfs").to_pylist()
    with_flow: set[str] = set()
    without_flow: set[str] = set()
    for site_number, flow_cfs in zip(site_numbers, flow_values, strict=True):
        if flow_cfs is None:
            without_flow.add(site_number)
        else:
            with_flow.add(site_number)
    return frozenset(with_flow), frozenset(without_flow - with_flow)


@dataclass(frozen=True, slots=True)
class WaterGaugesValidationReport:
    """One day's reconciliation of the written `kind=observed` partition against USGS NWIS itself.

    Compares SITE COVERAGE, never individual flow values: NWIS `/nwis/dv/` answers one daily
    aggregate per site while a settled day's written partition may hold many sub-daily `/nwis/iv/`
    ticks for the same site, so a value-for-value diff would be a false, wrong-but-plausible
    mismatch rather than a real one. A site NWIS reports for this day that the written partition has
    no row for at all (`missing_sites`) is the one honest gap this report can prove.
    """

    lane: str
    day: date
    bbox: str
    source_response_summary: str
    source_site_count: int
    written_row_count: int
    missing_sites: tuple[str, ...]
    silent_written_sites: tuple[str, ...]
    is_governed_absence: bool
    ok: bool


async def validate_water_gauges_day(
    *,
    store: ObjectStore,
    read_partition: PartitionContentReader,
    day: date,
    bbox: str,
    fetch_source_day: SourceDayFetcher = fetch_source_day_from_nwis,
) -> WaterGaugesValidationReport:
    """Reconcile one day's written `kind=observed` partition against USGS NWIS's own holdings.

    Reports an honest gap rather than a filled one (layer-lanes.md section 4): a day the source
    genuinely holds nothing for is recorded as such, never interpolated. Three shapes, in order:
    (1) a governed absence marker exists -- honest only if NWIS agrees nothing landed that day;
    (2) no partition was ever written -- every NWIS site for that day is a `missing_sites` gap;
    (3) a partition exists -- its site coverage is diffed against NWIS's own site set.
    """
    source_records = await fetch_source_day(day, bbox)
    source_sites = _site_numbers(source_records)
    response_summary = (
        f"{len(source_records)} NWIS daily-value records across {len(source_sites)} sites for {day.isoformat()}"
    )

    if store.absence_exists(WATER_GAUGES_STREAM, "observed", day):
        return WaterGaugesValidationReport(
            lane=WATER_GAUGES_STREAM,
            day=day,
            bbox=bbox,
            source_response_summary=response_summary,
            source_site_count=len(source_sites),
            written_row_count=0,
            missing_sites=tuple(sorted(source_sites)),
            silent_written_sites=(),
            is_governed_absence=True,
            ok=not source_sites,
        )

    if not store.partition_exists(WATER_GAUGES_STREAM, "observed", day):
        return WaterGaugesValidationReport(
            lane=WATER_GAUGES_STREAM,
            day=day,
            bbox=bbox,
            source_response_summary=response_summary,
            source_site_count=len(source_sites),
            written_row_count=0,
            missing_sites=tuple(sorted(source_sites)),
            silent_written_sites=(),
            is_governed_absence=False,
            ok=not source_sites,
        )

    key = store.key_for(partition_path(WATER_GAUGES_STREAM, "observed", day))
    payload = read_partition(key)
    if payload is None:
        raise WaterGaugesValidationError(
            f"{WATER_GAUGES_STREAM} observed {day.isoformat()}: object store reports the partition "
            f"exists at {key!r} but the content reader returned nothing"
        )
    table = pq.read_table(io.BytesIO(payload), columns=["site_number", "flow_cfs"])
    written_with_flow, silent_sites = _written_site_flow_status(table)
    written_all_sites = written_with_flow | silent_sites
    missing = source_sites - written_all_sites

    return WaterGaugesValidationReport(
        lane=WATER_GAUGES_STREAM,
        day=day,
        bbox=bbox,
        source_response_summary=response_summary,
        source_site_count=len(source_sites),
        written_row_count=table.num_rows,
        missing_sites=tuple(sorted(missing)),
        silent_written_sites=tuple(sorted(silent_sites)),
        is_governed_absence=False,
        ok=not missing,
    )
