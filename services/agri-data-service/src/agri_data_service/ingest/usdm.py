"""US Drought Monitor ingestion: the dated release adapter, its PostGIS store, and the weekly retention prune."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Protocol

import structlog
from sqlalchemy import text

from agri_data_service.ingest.http import (
    HTTP_NOT_FOUND,
    UpstreamBounds,
    UpstreamHttpError,
    UpstreamPayloadError,
    fetch_bounded_json,
    upstream_client,
)
from agri_data_service.ingest.identity import build_drought_area_identity
from agri_data_service.ingest.policy import javascript_number
from agri_data_service.ingest.results import IngestionJobResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

USDM_SOURCE: Final = "usdm-drought"
USDM_BASE_URL: Final = "https://droughtmonitor.unl.edu/data/json"

# The national collection is ~19 MB of full-resolution MultiPolygon rings.
USDM_BOUNDS: Final = UpstreamBounds(max_bytes=48 * 1024 * 1024, timeout_seconds=60.0)

# USDM publishes Thursdays for the preceding Tuesday; two misses is a real outage.
DEFAULT_CANDIDATE_WEEKS: Final = 3
MAX_CANDIDATE_WEEKS: Final = 8
TUESDAY: Final = 1  # date.weekday(): Monday is 0, so Tuesday is 1.

RETAINED_RELEASES_VARIABLE: Final = "DROUGHT_RETAINED_RELEASES"
DEFAULT_RETAINED_RELEASES: Final = 8
MIN_RETAINED_RELEASES: Final = 2
MAX_RETAINED_RELEASES: Final = 52

MIN_DROUGHT_CLASS: Final = 0
MAX_DROUGHT_CLASS: Final = 4
DAYS_PER_WEEK: Final = 7

UNPUBLISHED_RELEASE_REASON: Final = "USDM has not published a release for the requested dates"


@dataclass(frozen=True, slots=True)
class DroughtArea:
    """One drought class of one release, with the MultiPolygon PostGIS repairs at write time."""

    drought_monitor_category: int
    geometry: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DroughtRelease:
    """One indivisible weekly USDM release, dated by the request parameter rather than by its payload."""

    valid_date: str
    source_url: str
    areas: tuple[DroughtArea, ...]


class DroughtStore(Protocol):
    """The persistence seam the drought job writes through, so its rules are testable without PostGIS."""

    async def store_release(self, release: DroughtRelease, *, replace: bool) -> int:
        """Write one release's areas, returning the number of rows actually written."""
        ...

    async def prune_releases(self, retain: int) -> int:
        """Drop all but the newest retained releases, returning the release count removed."""
        ...


def resolve_retained_releases() -> int:
    """Read DROUGHT_RETAINED_RELEASES at call time, clamped to a sane retention window."""
    raw = os.environ.get(RETAINED_RELEASES_VARIABLE, "").strip()
    if not raw:
        return DEFAULT_RETAINED_RELEASES
    parsed = javascript_number(raw)
    if not math.isfinite(parsed):
        return DEFAULT_RETAINED_RELEASES
    return min(MAX_RETAINED_RELEASES, max(MIN_RETAINED_RELEASES, math.trunc(parsed)))


def usdm_valid_date_candidates(now: datetime | None = None, weeks: int = DEFAULT_CANDIDATE_WEEKS) -> list[str]:
    """Return the most recent USDM valid dates (Tuesdays), newest first."""
    bounded = min(MAX_CANDIDATE_WEEKS, max(1, weeks))
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        # A JavaScript Date is always an absolute instant; a naive one would walk local-zone Tuesdays.
        raise ValueError("now must include a timezone")
    cursor = moment.astimezone(UTC).date()
    # Step back to the most recent Tuesday; today counts when today is a Tuesday.
    cursor -= timedelta(days=(cursor.weekday() - TUESDAY + DAYS_PER_WEEK) % DAYS_PER_WEEK)
    return [(cursor - timedelta(days=DAYS_PER_WEEK * week)).isoformat() for week in range(bounded)]


def usdm_source_url(valid_date: str) -> str:
    """Return the exact upstream file a release is read from, which is stored as its provenance."""
    return f"{USDM_BASE_URL}/usdm_{valid_date.replace('-', '')}.json"


def _require_tuesday(valid_date: str) -> date:
    """Reject any date that is not a real USDM Tuesday before a request is ever made."""
    try:
        release_date = date.fromisoformat(valid_date)
    except ValueError as error:
        raise ValueError("USDM valid date must be an ISO YYYY-MM-DD string") from error
    if release_date.isoformat() != valid_date:
        raise ValueError("USDM valid date must be an ISO YYYY-MM-DD string")
    if release_date.weekday() != TUESDAY:
        raise ValueError("USDM releases are only valid for Tuesdays")
    return release_date


def parse_drought_release(valid_date: str, payload: object) -> DroughtRelease:
    """Parse one dated USDM collection, rejecting a release that repeats or omits a drought class."""
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    features = payload.get("features")
    if not isinstance(features, list):
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")

    areas: dict[int, DroughtArea] = {}
    for feature in features:
        drought_class, geometry = _parse_drought_feature(feature)
        # A duplicated class makes the release ambiguous rather than mergeable, so the whole
        # release is rejected instead of silently picking one.
        if drought_class in areas:
            raise UpstreamPayloadError(f"USDM release {valid_date} repeats drought class D{drought_class}")
        areas[drought_class] = DroughtArea(drought_monitor_category=drought_class, geometry=geometry)

    if not areas:
        raise UpstreamPayloadError(f"USDM release {valid_date} contained no drought classes")

    return DroughtRelease(
        valid_date=valid_date,
        source_url=usdm_source_url(valid_date),
        areas=tuple(areas[drought_class] for drought_class in sorted(areas)),
    )


def _parse_drought_feature(feature: object) -> tuple[int, Mapping[str, object]]:
    """Validate one USDM feature into its drought class and MultiPolygon geometry."""
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    if geometry.get("type") != "MultiPolygon" or not isinstance(geometry.get("coordinates"), list):
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    drought_class = properties.get("DM")
    if (
        isinstance(drought_class, bool)
        or not isinstance(drought_class, int)
        or not MIN_DROUGHT_CLASS <= drought_class <= MAX_DROUGHT_CLASS
    ):
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    return drought_class, geometry


async def fetch_drought_release(
    client: httpx.AsyncClient,
    valid_date: str,
) -> DroughtRelease | None:
    """Fetch one dated release; a 404 is USDM's documented "not published yet", not a failure."""
    _require_tuesday(valid_date)
    try:
        payload = await fetch_bounded_json(
            client,
            usdm_source_url(valid_date),
            USDM_BOUNDS,
            {"Accept": "application/json"},
        )
    except UpstreamHttpError as error:
        if error.status == HTTP_NOT_FOUND:
            return None
        raise
    return parse_drought_release(valid_date, payload)


async def fetch_latest_drought_release(
    client: httpx.AsyncClient,
    now: datetime | None = None,
    weeks: int = DEFAULT_CANDIDATE_WEEKS,
) -> DroughtRelease | None:
    """Walk back over recent USDM Tuesdays and return the newest published release."""
    for valid_date in usdm_valid_date_candidates(now, weeks):
        release = await fetch_drought_release(client, valid_date)
        if release is not None:
            return release
    return None


_STORE_DROUGHT_AREA_TEMPLATE: Final = """
    INSERT INTO geo.drought_areas (valid_date, dm_category, geom, source_url)
    VALUES (
        :valid_date,
        :dm_category,
        ST_Multi(
            ST_CollectionExtract(
                ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)),
                3
            )
        ),
        :source_url
    )
    ON CONFLICT (valid_date, dm_category) DO UPDATE
        SET geom = EXCLUDED.geom,
            source_url = EXCLUDED.source_url,
            ingested_at = now()
        WHERE {replace_predicate}
    RETURNING id
"""

_PRUNE_DROUGHT_RELEASES = text(
    """
    DELETE FROM geo.drought_areas
    WHERE valid_date NOT IN (
        SELECT valid_date
        FROM geo.drought_areas
        GROUP BY valid_date
        ORDER BY valid_date DESC
        LIMIT :retain
    )
    RETURNING valid_date
    """
)


class PostgresDroughtStore:
    """Stores releases as PostGIS geometry; rings are repaired in the database, never in Shapely."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the store to one ingestion session."""
        self._session = session

    async def store_release(self, release: DroughtRelease, *, replace: bool) -> int:
        """Write one release atomically, re-running as a no-op unless the operator asked to replace it."""
        statement = text(_STORE_DROUGHT_AREA_TEMPLATE.format(replace_predicate="true" if replace else "false"))
        written = 0
        try:
            for area in release.areas:
                # Built for its validation and for the Type-2 dimension's key; geo.drought_areas
                # has no properties->>'id' of its own. See ingest/AGENTS.md "usdm.py".
                identity = build_drought_area_identity(release.valid_date, area.drought_monitor_category)
                rows = await self._session.execute(
                    statement,
                    {
                        "valid_date": release.valid_date,
                        "dm_category": area.drought_monitor_category,
                        "geometry": json.dumps(area.geometry, allow_nan=False, separators=(",", ":")),
                        "source_url": release.source_url,
                    },
                )
                stored = len(rows.all())
                written += stored
                if stored:
                    logger.info("drought_area_stored", natural_key=identity.natural_key)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        return written

    async def prune_releases(self, retain: int) -> int:
        """Drop all but the newest retained releases, returning the number of releases removed."""
        try:
            rows = await self._session.execute(_PRUNE_DROUGHT_RELEASES, {"retain": retain})
            pruned = {row.valid_date for row in rows.all()}
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        return len(pruned)


async def run_drought_ingestion_job(
    store: DroughtStore,
    *,
    valid_date: str | None = None,
    replace: bool = False,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> IngestionJobResult:
    """Fetch the newest published USDM release, store it as PostGIS geometry, then prune old releases."""
    if client is None:
        async with upstream_client(USDM_BOUNDS) as owned_client:
            release = await _resolve_release(owned_client, valid_date, now)
    else:
        release = await _resolve_release(client, valid_date, now)

    if release is None:
        return IngestionJobResult(
            source=USDM_SOURCE,
            status="skipped",
            records_seen=0,
            records_written=0,
            reason=UNPUBLISHED_RELEASE_REASON,
        )

    areas_written = await store.store_release(release, replace=replace)
    releases_pruned = await store.prune_releases(resolve_retained_releases())
    return IngestionJobResult(
        source=USDM_SOURCE,
        status="ingested",
        records_seen=len(release.areas),
        records_written=areas_written,
        details={"releases_pruned": releases_pruned},
    )


async def _resolve_release(
    client: httpx.AsyncClient,
    valid_date: str | None,
    now: datetime | None,
) -> DroughtRelease | None:
    """Fetch either the operator's explicit release date or the newest published one."""
    if valid_date is not None:
        return await fetch_drought_release(client, valid_date)
    return await fetch_latest_drought_release(client, now)
