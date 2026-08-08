"""The date-ranged backfill driver and the geometry repair pass, both writing through the forward write path."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import UUID

import structlog
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.geometry import (
    GEOMETRY_VERSION_ACTIONS,
    FeatureGeometryLink,
    count_geometry_actions,
    feature_geometry_request,
    link_feature_geometry,
    upsert_geometry_versions,
)
from agri_data_service.ingest.identity import (
    FIRMS_PRODUCER,
    OPEN_METEO_PRODUCER,
    PRODUCER_BY_LAYER_NAME,
    USGS_NWIS_PRODUCER,
    WFIGS_PRODUCER,
    FeatureIdentity,
    MissingNativeKeyError,
    build_fire_perimeter_identity,
    build_firms_identity,
    build_streamflow_gauge_identity,
    build_weather_observation_identity,
)
from agri_data_service.ingest.policy import (
    MAX_MAX_SOURCE_RECORDS,
    UNCONFIGURED_BBOX_REASON,
    resolve_bounded_bbox,
    resolve_max_source_records,
)
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.source import (
    FetchRequest,
    HistoryUnavailableError,
    HistoryWindow,
    select_writes,
)
from agri_data_service.ingest.writer import feature_event_key, lock_feature_event_keys

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.geometry import GeometryVersionRequest
    from agri_data_service.ingest.source import IngestionSource, SelectedWrites
    from agri_data_service.ingest.writer import FeatureWriter

logger = structlog.get_logger()

GEOMETRY_REPAIR_SOURCE: Final = "geometry-repair"

# Two years back from the run date, as a default a caller can override, never a constant buried in a loop.
DEFAULT_HISTORY_YEARS: Final = 2
DEFAULT_HISTORY_CHUNK: Final = timedelta(days=7)

DEFAULT_REPAIR_BATCH_SIZE: Final = 200
ZERO_UUID: Final = str(UUID(int=0))

# The narrowest chunk a retry can be advised to use; a suggestion of zero days would not be a plan.
MIN_RETRY_CHUNK_DAYS: Final = 1

LATITUDE_INDEX: Final = 1
LONGITUDE_INDEX: Final = 0
MIN_COORDINATE_COUNT: Final = 2

_UNREPAIRABLE_LAYER_REASON: Final = "layer has no producer in identity.PRODUCER_BY_LAYER_NAME"


class BackfillContractError(ValueError):
    """Raised when a backfill is asked to walk a window or a chunk size that cannot be walked."""


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    """The window a backfill walks, how finely it walks it, and the bounded-ingestion context it walks in."""

    window: HistoryWindow
    chunk: timedelta = DEFAULT_HISTORY_CHUNK
    bbox: str | None = None
    now: datetime | None = None
    client: httpx.AsyncClient | None = None

    def __post_init__(self) -> None:
        """Refuse a non-positive chunk, which would either never terminate or walk nothing."""
        if self.chunk <= timedelta(0):
            raise BackfillContractError("backfill chunk must be a positive interval")


def subtract_years(moment: datetime, years: int) -> datetime:
    """Step a calendar instant back whole years, landing 29 February on 28 February rather than raising."""
    try:
        return moment.replace(year=moment.year - years)
    except ValueError:
        return moment.replace(year=moment.year - years, day=28)


def default_history_window(now: datetime | None = None, years: int = DEFAULT_HISTORY_YEARS) -> HistoryWindow:
    """The default backfill span: whole calendar years back from the run date, ending at the run date."""
    end = now if now is not None else datetime.now(UTC)
    return HistoryWindow(start=subtract_years(end, years), end=end)


def default_backfill_plan(
    now: datetime | None = None,
    years: int = DEFAULT_HISTORY_YEARS,
    chunk: timedelta = DEFAULT_HISTORY_CHUNK,
    bbox: str | None = None,
) -> BackfillPlan:
    """Build the default plan: two years back, walked a week at a time, inside the bounded ingestion bbox."""
    return BackfillPlan(window=default_history_window(now, years), chunk=chunk, bbox=bbox, now=now)


def history_chunks(window: HistoryWindow, chunk: timedelta = DEFAULT_HISTORY_CHUNK) -> list[HistoryWindow]:
    """Cut a window into fixed steps anchored at its start, so the same window always yields the same chunks."""
    if chunk <= timedelta(0):
        raise BackfillContractError("backfill chunk must be a positive interval")
    chunks: list[HistoryWindow] = []
    start = window.start
    while start < window.end:
        end = min(start + chunk, window.end)
        chunks.append(HistoryWindow(start=start, end=end))
        start = end
    return chunks


def _chunk_label(chunk: HistoryWindow) -> str:
    """Name a chunk by its bounds, which is what an operator resumes from after a partial run."""
    return f"{chunk.start.isoformat()}..{chunk.end.isoformat()}"


def _chunk_days(chunk: HistoryWindow) -> int:
    """The chunk's span in whole days, rounded up, so a sub-day chunk still counts as one day."""
    span = chunk.end - chunk.start
    return max(MIN_RETRY_CHUNK_DAYS, -(-span // timedelta(days=1)))


def _retry_chunk_days(chunk: HistoryWindow, selection: SelectedWrites) -> int:
    """The widest whole-day chunk that would have fitted under the cap, and always narrower than this one.

    Capped at `span_days - 1` so a one-record overshoot cannot advise the same size back and loop; a
    single-day chunk that still overshoots floors at one day, and the reason text names raising
    `INGEST_MAX_SOURCE_RECORDS` as the other lever for exactly that case.
    """
    span_days = _chunk_days(chunk)
    accepted = len(selection.writes) + selection.dropped
    fitted = span_days * len(selection.writes) // accepted if accepted else MIN_RETRY_CHUNK_DAYS
    return max(MIN_RETRY_CHUNK_DAYS, min(fitted, span_days - 1))


def _truncated_chunk_reason(chunk: HistoryWindow, request: FetchRequest, selection: SelectedWrites) -> str:
    """Say what the cap deleted and name the narrower walk that would not hit it."""
    return (
        f"{_chunk_label(chunk)} produced {len(selection.writes) + selection.dropped} records against a "
        f"{request.max_records}-record cap, so {selection.dropped} were dropped. The cap keeps the newest "
        f"observations, so what it drops is the OLDEST days of this chunk, whole -- not a thinner sample of "
        f"all of them. Nothing was written for this chunk. Re-walk it with --chunk-days "
        f"{_retry_chunk_days(chunk, selection)}, or raise INGEST_MAX_SOURCE_RECORDS "
        f"(ceiling {MAX_MAX_SOURCE_RECORDS})."
    )


async def _run_backfill_chunk(
    source: IngestionSource,
    write_features: FeatureWriter,
    request: FetchRequest,
    chunk: HistoryWindow,
) -> IngestionJobResult:
    """Fetch, map and write exactly one chunk, holding nothing from any other chunk in memory.

    A bitten record cap fails the chunk and writes nothing. Truncation is not a rejection, so it never
    reaches `details.rejected`, and `_truncation_rank` keeps the newest -- which means a chunk that
    silently reported `ingested` had deleted its oldest days entirely while claiming to have walked
    them. Writing the survivors anyway would be worse than writing nothing: the days that fitted would
    look complete and the operator would have no reason to re-walk. See ingest/AGENTS.md "backfill.py".
    """
    records = await source.fetch_history(request, chunk)
    selection = select_writes(source, records, request)
    if selection.dropped:
        return IngestionJobResult(
            source=source.source_name,
            status="failed",
            records_seen=len(records),
            records_written=0,
            truncated=True,
            reason=_truncated_chunk_reason(chunk, request, selection),
            details={"rejected": selection.rejected, "dropped": selection.dropped},
        )
    return IngestionJobResult(
        source=source.source_name,
        status="ingested",
        records_seen=len(records),
        records_written=await write_features(selection.writes),
        truncated=selection.truncated,
        reason=_chunk_label(chunk),
        details={"rejected": selection.rejected, "dropped": selection.dropped},
    )


async def run_source_job(
    source: IngestionSource,
    write_features: FeatureWriter,
    bbox: str | None = None,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> IngestionJobResult:
    """Fetch a source's current window and write it through the one path history and repair already write through.

    A bitten cap stays `ingested` here where it fails a backfill chunk, and the asymmetry is deliberate:
    a forward window is whatever the producer publishes as "now" and there is no narrower window to
    retry with, so failing would only turn the hourly cron red on a busy day with no action to take.
    `truncated` plus `details.dropped` is the signal instead.
    """
    area = resolve_bounded_bbox(bbox)
    if area is None:
        return skipped_result(source.source_name, UNCONFIGURED_BBOX_REASON)
    request = FetchRequest(bbox=area, max_records=resolve_max_source_records(), now=now, client=client)
    records = await source.fetch_current(request)
    selection = select_writes(source, records, request)
    return IngestionJobResult(
        source=source.source_name,
        status="ingested",
        records_seen=len(records),
        records_written=await write_features(selection.writes),
        truncated=selection.truncated,
        details={"rejected": selection.rejected, "dropped": selection.dropped},
    )


async def run_source_backfill(
    source: IngestionSource,
    write_features: FeatureWriter,
    plan: BackfillPlan | None = None,
) -> list[IngestionJobResult]:
    """Walk a source over a date range in bounded chunks, writing through the same path forward ingestion uses."""
    resolved_plan = plan if plan is not None else default_backfill_plan()
    area = resolve_bounded_bbox(resolved_plan.bbox)
    if area is None:
        return [skipped_result(source.source_name, UNCONFIGURED_BBOX_REASON)]

    request = FetchRequest(
        bbox=area,
        max_records=resolve_max_source_records(),
        now=resolved_plan.now,
        client=resolved_plan.client,
    )
    results: list[IngestionJobResult] = []
    for chunk in history_chunks(resolved_plan.window, resolved_plan.chunk):
        try:
            result = await _run_backfill_chunk(source, write_features, request, chunk)
        except HistoryUnavailableError as refusal:
            # A typed refusal, surfaced as an operator-visible skip rather than swallowed into an empty run.
            return [*results, skipped_result(source.source_name, str(refusal))]
        results.append(result)
        if result.status == "failed":
            # The walk continues -- one over-large chunk must not erase the chunks after it -- but the
            # log line must not call a chunk complete that wrote nothing.
            logger.warning("backfill_chunk_failed", source=source.source_name, chunk=_chunk_label(chunk))
            continue
        logger.info("backfill_chunk_complete", source=source.source_name, chunk=_chunk_label(chunk))
    return results


def merge_backfill_results(source_name: str, results: Sequence[IngestionJobResult]) -> IngestionJobResult:
    """Fold per-chunk progress into one summary without losing the per-chunk results the caller already has."""
    if not results:
        return skipped_result(source_name, "the backfill window contained no chunks")
    failed = [result for result in results if result.status == "failed"]
    ingested = [result for result in results if result.status == "ingested"]
    if not failed and not ingested:
        # Every result was a skip -- an unconfigured bbox, or the source's typed history refusal.
        # Folding that to "ingested" would report a walk that never happened as a completed one.
        return skipped_result(source_name, results[0].reason or "the backfill walked no chunk")
    return IngestionJobResult(
        source=source_name,
        status="failed" if failed else "ingested",
        records_seen=sum(result.records_seen for result in results),
        records_written=sum(result.records_written for result in results),
        truncated=any(result.truncated for result in results),
        reason=failed[0].reason if failed else None,
        details={
            "chunks": len(results),
            "rejected": sum(int(result.details.get("rejected", 0)) for result in results),
            # Folded rather than reduced to the `truncated` flag: a walk that lost 50,779 published
            # records to a cap and one that lost 3 are not the same run to look at.
            "dropped": sum(int(result.details.get("dropped", 0)) for result in results),
        },
    )


_SELECT_UNVERSIONED_FEATURES = text(load_query_sql("ingest/select_unversioned_features.sql"))


def _stored_coordinates(properties: Mapping[str, object]) -> Sequence[object] | None:
    """Return a stored feature's GeoJSON coordinates, which two producers fold into their native key."""
    geometry = properties.get("geometry")
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    return coordinates if isinstance(coordinates, list) else None


def _coordinate(coordinates: Sequence[object] | None, index: int) -> float | None:
    """Return one ordinate of a stored point, or None when the stored shape is not a usable point."""
    if coordinates is None or len(coordinates) < MIN_COORDINATE_COUNT:
        return None
    value = coordinates[index]
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _rebuild_identity(producer: str, properties: Mapping[str, object]) -> FeatureIdentity | None:
    """Re-mint a stored feature's identity through identity.py, the only place allowed to mint one."""
    coordinates = _stored_coordinates(properties)
    if producer == FIRMS_PRODUCER:
        return build_firms_identity(properties, coordinates)
    if producer == USGS_NWIS_PRODUCER:
        return build_streamflow_gauge_identity(properties)
    if producer == WFIGS_PRODUCER:
        return build_fire_perimeter_identity(properties)
    if producer == OPEN_METEO_PRODUCER:
        latitude = _coordinate(coordinates, LATITUDE_INDEX)
        longitude = _coordinate(coordinates, LONGITUDE_INDEX)
        if latitude is None or longitude is None:
            return None
        return build_weather_observation_identity(latitude, longitude, properties)
    return None


def _fallback_entity_local_id(producer: str, stored_id: str) -> str | None:
    """Split a stored observation id back into the enduring place, for the producers that have one.

    The fallback below keeps the stored key verbatim, which is right -- but a bare
    `FeatureIdentity(producer, stored_id)` leaves `entity_local_id` at None, so `entity_key`
    collapses onto `natural_key` and `feature_geometry_request` mints a chain keyed
    `usgs-nwis:11510700:2026-08-03T23:50:00.000-07:00` rather than `usgs-nwis:11510700`. That is
    exactly the dual-key-shape state scripts/rekey-geometry-to-entity.sql was written to remove --
    "the table would hold BOTH key shapes for the same producer, which is the interleaving hazard
    wearing a new costume" -- and it splits one gauge's version chain into one chain per reading.
    The field counts are the same ones that script's `split_part` derivation uses:
      usgs-nwis:<siteNo>:<updatedAt>       -> field 1
      open-meteo:<lat>:<lon>:<observedAt>  -> fields 1..2
    Every other producer is entity-keyed already (entity_local_id is None by construction), so
    returning None for them is correct rather than a gap.
    """
    if producer == USGS_NWIS_PRODUCER:
        site_number = stored_id.split(":", 1)[0]
        return site_number or None
    if producer == OPEN_METEO_PRODUCER:
        fields = stored_id.split(":")
        if len(fields) < MIN_COORDINATE_COUNT or not fields[0] or not fields[1]:
            return None
        return f"{fields[0]}:{fields[1]}"
    return None


def repair_identity(producer: str, properties: Mapping[str, object]) -> FeatureIdentity | None:
    """Rebuild a stored feature's identity, dating it -infinity rather than inventing an observation time.

    The rebuilt producer-local id must equal the id already stored on the row. A disagreement means the
    payload and the identity contract have drifted, so the parsed observation time cannot be trusted to
    belong to this key either: the key is kept verbatim and the version dates to -infinity instead. See
    ingest/AGENTS.md "backfill.py".
    """
    stored_id = properties.get("id")
    if not isinstance(stored_id, str) or not stored_id.strip():
        return None
    try:
        rebuilt = _rebuild_identity(producer, properties)
    except (MissingNativeKeyError, ValueError):
        rebuilt = None
    if rebuilt is not None and rebuilt.producer_local_id == stored_id:
        return rebuilt
    return FeatureIdentity(
        producer=producer,
        producer_local_id=stored_id,
        observed_at=None,
        entity_local_id=_fallback_entity_local_id(producer, stored_id),
    )


@dataclass(frozen=True, slots=True)
class _RepairBatch:
    """One page of unversioned features, split into what can be versioned and what cannot."""

    requests: list[GeometryVersionRequest]
    linked_places: list[tuple[str, str]]
    event_keys: list[str]
    unrepairable: int
    cursor: str
    seen: int


def _plan_repair_batch(rows: Sequence[Mapping[str, object]], cursor: str) -> _RepairBatch:
    """Turn one page of stored features into version requests, counting the rows no producer can claim."""
    requests: list[GeometryVersionRequest] = []
    linked_places: list[tuple[str, str]] = []
    event_keys: list[str] = []
    unrepairable = 0
    for row in rows:
        feature_id = str(row["feature_id"])
        cursor = feature_id
        producer = PRODUCER_BY_LAYER_NAME.get(str(row["layer_name"]))
        properties = json.loads(str(row["properties_json"]))
        identity = repair_identity(producer, properties) if producer is not None else None
        if identity is None:
            unrepairable += 1
            continue
        request = feature_geometry_request(identity, feature_id)
        requests.append(request)
        linked_places.append((feature_id, request.natural_key))
        event_keys.append(feature_event_key(str(row["layer_id"]), identity.producer_local_id))
    return _RepairBatch(
        requests=requests,
        linked_places=linked_places,
        event_keys=event_keys,
        unrepairable=unrepairable,
        cursor=cursor,
        seen=len(rows),
    )


async def run_geometry_repair(
    session: AsyncSession,
    run_clock: datetime | None = None,
    batch_size: int = DEFAULT_REPAIR_BATCH_SIZE,
    max_features: int | None = None,
) -> IngestionJobResult:
    """Version and link every geo.features row left without a geometry_id, re-runnable and never inventing a date."""
    confirmed_at = run_clock if run_clock is not None else datetime.now(UTC)
    cursor = ZERO_UUID
    seen = 0
    linked = 0
    unrepairable = 0
    totals: dict[str, int] = dict.fromkeys(GEOMETRY_VERSION_ACTIONS, 0)

    while max_features is None or seen < max_features:
        page_size = batch_size if max_features is None else min(batch_size, max_features - seen)
        rows = (
            await session.execute(_SELECT_UNVERSIONED_FEATURES, {"cursor": cursor, "batch_size": page_size})
        ).mappings()
        batch = _plan_repair_batch([dict(row) for row in rows], cursor)
        if batch.seen == 0:
            break
        seen += batch.seen
        unrepairable += batch.unrepairable
        cursor = batch.cursor
        try:
            # Feature lock BEFORE the geometry lock, matching writer._ingest_resolved_batch. The
            # writer holds a row's feature key while it waits on that place's geometry key; taking
            # them the other way round here would deadlock a repair against a concurrent
            # `ingest-all` on the same gauge and Postgres would abort one with 40P01.
            await lock_feature_event_keys(session, batch.event_keys)
            outcomes = await upsert_geometry_versions(session, batch.requests, confirmed_at)
            linked += await link_feature_geometry(
                session,
                [
                    FeatureGeometryLink(feature_id=feature_id, geometry_id=outcomes[natural_key].geometry_id)
                    for feature_id, natural_key in batch.linked_places
                    if natural_key in outcomes
                ],
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        for action, count in count_geometry_actions(outcomes).items():
            totals[action] += count

    if unrepairable:
        logger.warning("geometry_repair_unrepairable", count=unrepairable, reason=_UNREPAIRABLE_LAYER_REASON)
    return IngestionJobResult(
        source=GEOMETRY_REPAIR_SOURCE,
        status="ingested",
        records_seen=seen,
        records_written=linked,
        details={**totals, "unrepairable": unrepairable},
    )
