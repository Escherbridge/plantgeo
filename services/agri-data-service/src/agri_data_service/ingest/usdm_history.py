"""USDM weekly history: walk past release Tuesdays through the dated archive, recording every week that is absent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol

import structlog
from sqlalchemy import text

from agri_data_service.ingest.backfill import DEFAULT_HISTORY_YEARS, subtract_years
from agri_data_service.ingest.http import UpstreamError, UpstreamPayloadError, upstream_client
from agri_data_service.ingest.identity import build_drought_area_identity
from agri_data_service.ingest.policy import resolve_max_source_records
from agri_data_service.ingest.results import IngestionJobResult, failure_reason, skipped_result
from agri_data_service.ingest.usdm import (
    DAYS_PER_WEEK,
    TUESDAY,
    USDM_BOUNDS,
    fetch_drought_release,
    resolve_retained_releases,
    usdm_source_url,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.identity import FeatureIdentity
    from agri_data_service.ingest.results import JobStatus
    from agri_data_service.ingest.usdm import DroughtRelease, DroughtStore

logger = structlog.get_logger()

USDM_HISTORY_SOURCE: Final = "usdm-drought-history"

# USDM's first published release. Earlier Tuesdays are not a gap in our history, they are before the record.
USDM_ARCHIVE_START: Final = date(2000, 1, 4)

# The walk is sequential by design and pauses between files: a two-year window is ~105 files of roughly 19 MB.
DEFAULT_PAUSE_SECONDS: Final = 1.0

NOT_PUBLISHED_DETAIL: Final = "USDM has not published a release for this week"
ALREADY_STORED_DETAIL: Final = "geo.drought_areas already holds this release week"
RECORD_CAP_DETAIL: Final = "the bounded-ingestion record cap stopped the walk before this week"
NO_WEEKS_REASON: Final = "the history window contained no release weeks"
NOTHING_PUBLISHED_REASON: Final = "no requested release week was published"
ALL_ALREADY_STORED_REASON: Final = "every requested release week was already stored"

WeekSkipReason = Literal[
    "not_published",
    "already_stored",
    "unreadable_release",
    "upstream_unavailable",
    "record_cap_reached",
]


class UsdmHistoryContractError(ValueError):
    """Raised when a history walk is asked for a week or a bound that cannot honestly be walked."""


@dataclass(frozen=True, slots=True)
class ReleaseWeek:
    """One USDM release week: the Tuesday it is valid for and the dated archive file it is read from."""

    release_date: date

    def __post_init__(self) -> None:
        """Refuse any weekday but Tuesday, the only day a USDM release is ever valid for."""
        # Held as a `date` rather than text so the stored `valid_date` spelling is canonical by construction
        # and `usdm._require_tuesday`'s ISO round-trip guard has nothing left to catch.
        if self.release_date.weekday() != TUESDAY:
            raise UsdmHistoryContractError("a USDM release week must be a Tuesday")

    @property
    def valid_date(self) -> str:
        """The canonical `YYYY-MM-DD` spelling that `geo.drought_areas.valid_date` stores."""
        return self.release_date.isoformat()

    @property
    def source_url(self) -> str:
        """The exact archive file this week is read from, which is stored as the row's provenance."""
        return usdm_source_url(self.valid_date)


@dataclass(frozen=True, slots=True)
class WeekOutcome:
    """One ledger row per requested week: what it contributed, or the typed reason it contributed nothing."""

    valid_date: str
    status: JobStatus
    areas_seen: int = 0
    areas_written: int = 0
    natural_keys: tuple[str, ...] = ()
    observed_at: datetime | None = None
    skip_reason: WeekSkipReason | None = None
    detail: str | None = None

    @property
    def is_gap(self) -> bool:
        """True when no release covers this week after the run; a gap is a fact about USDM, never to be filled."""
        return self.status != "ingested" and self.skip_reason != "already_stored"


class StoredReleaseIndex(Protocol):
    """The read-only seam that tells a walk which release weeks the warehouse already holds."""

    async def stored_release_dates(self) -> frozenset[str]:
        """Every `valid_date` that `geo.drought_areas` already holds at least one area for."""
        ...


def usdm_release_weeks(start: date, end: date) -> tuple[ReleaseWeek, ...]:
    """Every USDM release Tuesday from `start` through `end` inclusive, oldest first, clamped to the archive."""
    first = max(start, USDM_ARCHIVE_START)
    if start < USDM_ARCHIVE_START:
        logger.info(
            "usdm_history_window_clamped",
            requested_start=start.isoformat(),
            effective_start=first.isoformat(),
        )
    if first > end:
        return ()
    cursor = first + timedelta(days=(TUESDAY - first.weekday() + DAYS_PER_WEEK) % DAYS_PER_WEEK)
    weeks: list[ReleaseWeek] = []
    while cursor <= end:
        weeks.append(ReleaseWeek(release_date=cursor))
        cursor += timedelta(days=DAYS_PER_WEEK)
    return tuple(weeks)


def two_year_release_weeks(now: datetime | None = None, years: int = DEFAULT_HISTORY_YEARS) -> tuple[ReleaseWeek, ...]:
    """The default history span: whole calendar years back from the run date, enumerated as release Tuesdays."""
    if years < 0:
        raise UsdmHistoryContractError("the history window must span a non-negative number of years")
    end = now if now is not None else datetime.now(UTC)
    if end.tzinfo is None or end.utcoffset() is None:
        # A naive instant resolves against the container's local zone and walks a different set of Tuesdays.
        raise UsdmHistoryContractError("now must include a timezone")
    return usdm_release_weeks(subtract_years(end, years).astimezone(UTC).date(), end.astimezone(UTC).date())


@dataclass(frozen=True, slots=True)
class HistoryBackfillPlan:
    """Which release weeks a walk covers and the bounds it runs under."""

    weeks: tuple[ReleaseWeek, ...]
    replace: bool = False
    max_areas: int | None = None
    pause_seconds: float = DEFAULT_PAUSE_SECONDS
    client: httpx.AsyncClient | None = None

    def __post_init__(self) -> None:
        """Refuse bounds that would either hammer the archive or walk nothing."""
        if self.pause_seconds < 0:
            raise UsdmHistoryContractError("pause_seconds must not be negative")
        if self.max_areas is not None and self.max_areas <= 0:
            raise UsdmHistoryContractError("max_areas must be a positive number of drought areas")


def default_history_plan(
    now: datetime | None = None,
    years: int = DEFAULT_HISTORY_YEARS,
    replace: bool = False,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
) -> HistoryBackfillPlan:
    """The default plan: two years of release Tuesdays, walked oldest first, one paced request per week."""
    return HistoryBackfillPlan(
        weeks=two_year_release_weeks(now, years),
        replace=replace,
        pause_seconds=pause_seconds,
    )


_SELECT_STORED_RELEASE_DATES = text("SELECT DISTINCT valid_date FROM geo.drought_areas")


class PostgresStoredReleaseIndex:
    """Reads the release weeks already stored, so a resumed walk re-downloads none of them."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the index to one ingestion session."""
        self._session = session

    async def stored_release_dates(self) -> frozenset[str]:
        """Every stored `valid_date`; one area proves the whole week, because a release is written atomically."""
        # `PostgresDroughtStore.store_release` writes every area of a release and commits once, so a week is
        # never half-stored and the presence of one row is a sound "this week is complete" signal.
        rows = await self._session.execute(_SELECT_STORED_RELEASE_DATES)
        return frozenset(str(row.valid_date) for row in rows.all())


async def fetch_release_week(client: httpx.AsyncClient, week: ReleaseWeek) -> DroughtRelease | None:
    """Fetch one archived week through the shipped dated-release adapter; None is USDM's "not published" 404."""
    return await fetch_drought_release(client, week.valid_date)


def release_identities(week: ReleaseWeek, release: DroughtRelease) -> tuple[FeatureIdentity, ...]:
    """Mint every area's identity through identity.py, dated only by the release week and never by a clock."""
    identities = tuple(
        build_drought_area_identity(week.release_date, area.drought_monitor_category) for area in release.areas
    )
    for identity in identities:
        # Tripwire, not decoration: the day anything dates a drought area from a run clock instead of its
        # release week, this walk stops rather than backfilling two years of plausible-looking wrong dates.
        if identity.observed_at is None or identity.observed_at.date() != week.release_date:
            raise UsdmHistoryContractError(
                f"USDM area identity {identity.natural_key} is not dated by release week {week.valid_date}"
            )
    return identities


async def ingest_release_week(
    client: httpx.AsyncClient,
    store: DroughtStore,
    week: ReleaseWeek,
    *,
    replace: bool = False,
) -> WeekOutcome:
    """Fetch and store one archived week, turning an unavailable archive into a typed outcome rather than a stop."""
    try:
        release = await fetch_release_week(client, week)
    except UpstreamPayloadError as error:
        # A garbled or unexpected body is "we could not read this week", which is not "USDM published none".
        return WeekOutcome(
            valid_date=week.valid_date,
            status="failed",
            skip_reason="unreadable_release",
            detail=failure_reason(error),
        )
    except UpstreamError as error:
        return WeekOutcome(
            valid_date=week.valid_date,
            status="failed",
            skip_reason="upstream_unavailable",
            detail=failure_reason(error),
        )

    if release is None:
        return WeekOutcome(
            valid_date=week.valid_date,
            status="skipped",
            skip_reason="not_published",
            detail=NOT_PUBLISHED_DETAIL,
        )

    identities = release_identities(week, release)
    written = await store.store_release(release, replace=replace)
    return WeekOutcome(
        valid_date=week.valid_date,
        status="ingested",
        areas_seen=len(release.areas),
        areas_written=written,
        natural_keys=tuple(identity.natural_key for identity in identities),
        observed_at=identities[0].observed_at if identities else None,
    )


async def _walk_release_weeks(
    client: httpx.AsyncClient,
    store: DroughtStore,
    plan: HistoryBackfillPlan,
    already_stored: frozenset[str],
    max_areas: int,
) -> list[WeekOutcome]:
    """Walk every requested week in order, emitting exactly one ledger row per week whatever happened to it."""
    outcomes: list[WeekOutcome] = []
    areas_seen = 0
    fetched = 0
    capped = False
    for week in plan.weeks:
        if capped:
            outcomes.append(
                WeekOutcome(
                    valid_date=week.valid_date,
                    status="skipped",
                    skip_reason="record_cap_reached",
                    detail=RECORD_CAP_DETAIL,
                )
            )
            continue
        if not plan.replace and week.valid_date in already_stored:
            outcomes.append(
                WeekOutcome(
                    valid_date=week.valid_date,
                    status="skipped",
                    skip_reason="already_stored",
                    detail=ALREADY_STORED_DETAIL,
                )
            )
            continue
        if fetched and plan.pause_seconds:
            await asyncio.sleep(plan.pause_seconds)
        outcome = await ingest_release_week(client, store, week, replace=plan.replace)
        fetched += 1
        areas_seen += outcome.areas_seen
        outcomes.append(outcome)
        logger.info(
            "usdm_history_week",
            valid_date=outcome.valid_date,
            status=outcome.status,
            areas_written=outcome.areas_written,
            skip_reason=outcome.skip_reason,
        )
        # The cap is checked after a week completes, so a release is never half-written to meet it.
        capped = areas_seen >= max_areas
    return outcomes


async def run_usdm_history_backfill(
    store: DroughtStore,
    plan: HistoryBackfillPlan | None = None,
    stored: StoredReleaseIndex | None = None,
) -> list[WeekOutcome]:
    """Walk a window of USDM release weeks into geo.drought_areas, one ledger row per week, never inventing one."""
    resolved_plan = plan if plan is not None else default_history_plan()
    already_stored = await stored.stored_release_dates() if stored is not None else frozenset()
    max_areas = resolved_plan.max_areas if resolved_plan.max_areas is not None else resolve_max_source_records()
    if resolved_plan.client is None:
        async with upstream_client(USDM_BOUNDS) as owned_client:
            return await _walk_release_weeks(owned_client, store, resolved_plan, already_stored, max_areas)
    return await _walk_release_weeks(resolved_plan.client, store, resolved_plan, already_stored, max_areas)


def history_gap_weeks(outcomes: Sequence[WeekOutcome]) -> tuple[str, ...]:
    """The release weeks no stored release covers after the run, in order; the slider must render these empty."""
    return tuple(outcome.valid_date for outcome in outcomes if outcome.is_gap)


def _count_skips(outcomes: Sequence[WeekOutcome], reason: WeekSkipReason) -> int:
    """Count the weeks that ended on one typed skip reason."""
    return sum(1 for outcome in outcomes if outcome.skip_reason == reason)


def _walk_status(failed: int, ingested: int, already_stored: int) -> tuple[JobStatus, str | None]:
    """Decide the walk's verdict, refusing to report a walk that wrote nothing as a successful backfill."""
    if failed:
        return "failed", None
    if ingested:
        return "ingested", None
    # A window that stored nothing is reported as a skip with its reason, because an "ingested" summary over
    # zero writes is how an unwalked gap gets certified as complete. See ingest/source.py's history contract.
    return "skipped", ALL_ALREADY_STORED_REASON if already_stored else NOTHING_PUBLISHED_REASON


def merge_week_outcomes(outcomes: Sequence[WeekOutcome]) -> IngestionJobResult:
    """Fold the per-week ledger into the operator-facing summary, counting every gap rather than smoothing it."""
    if not outcomes:
        return skipped_result(USDM_HISTORY_SOURCE, NO_WEEKS_REASON)

    failures = [outcome for outcome in outcomes if outcome.status == "failed"]
    ingested = sum(1 for outcome in outcomes if outcome.status == "ingested")
    already_stored = _count_skips(outcomes, "already_stored")
    status, skip_reason = _walk_status(len(failures), ingested, already_stored)
    retained_releases = resolve_retained_releases()
    beyond_retention = max(0, ingested + already_stored - retained_releases)
    if beyond_retention:
        # The weekly job prunes to DROUGHT_RETAINED_RELEASES after every run, so the history written here is
        # deleted by the next ordinary cron tick unless that retention is raised first.
        logger.warning(
            "usdm_history_beyond_retention",
            releases_beyond_retention=beyond_retention,
            retained_releases=retained_releases,
        )
    return IngestionJobResult(
        source=USDM_HISTORY_SOURCE,
        status=status,
        records_seen=sum(outcome.areas_seen for outcome in outcomes),
        records_written=sum(outcome.areas_written for outcome in outcomes),
        truncated=any(outcome.skip_reason == "record_cap_reached" for outcome in outcomes),
        reason=failures[0].detail if failures else skip_reason,
        details={
            "weeks_requested": len(outcomes),
            "weeks_ingested": ingested,
            "weeks_failed": len(failures),
            "weeks_already_stored": already_stored,
            "weeks_not_published": _count_skips(outcomes, "not_published"),
            "weeks_unreadable": _count_skips(outcomes, "unreadable_release"),
            "weeks_upstream_unavailable": _count_skips(outcomes, "upstream_unavailable"),
            "weeks_beyond_record_cap": _count_skips(outcomes, "record_cap_reached"),
            "weeks_missing": len(history_gap_weeks(outcomes)),
            "releases_beyond_retention": beyond_retention,
        },
    )
