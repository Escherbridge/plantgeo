"""USDM weekly history: the Tuesday walk, the release-dated identities, and the gaps it refuses to fill."""

# ruff: noqa: PLR2004

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest import usdm_history
from agri_data_service.ingest.identity import USDM_PRODUCER, FeatureIdentity
from agri_data_service.ingest.usdm import DroughtArea, DroughtRelease
from agri_data_service.ingest.usdm_history import (
    DEFAULT_PAUSE_SECONDS,
    USDM_ARCHIVE_START,
    USDM_HISTORY_SOURCE,
    HistoryBackfillPlan,
    ReleaseWeek,
    UsdmHistoryContractError,
    WeekOutcome,
    default_history_plan,
    history_gap_weeks,
    ingest_release_week,
    merge_week_outcomes,
    release_identities,
    run_usdm_history_backfill,
    two_year_release_weeks,
    usdm_release_weeks,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# 2026-08-04 is a Tuesday whose release USDM has not published yet -- it publishes on the Thursday.
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
STORED_WEEK = ReleaseWeek(release_date=date(2026, 7, 28))
EARLIER_WEEK = ReleaseWeek(release_date=date(2026, 7, 21))

SQUARE_RING = [[-113.0, 47.0], [-113.0, 47.1], [-112.9, 47.1], [-112.9, 47.0], [-113.0, 47.0]]


class RecordingStore:
    """A drought store that records what the walk handed it, so the walk's rules need no PostGIS."""

    def __init__(self, written: int = 2) -> None:
        """Configure how many areas a write reports as actually stored."""
        self.written = written
        self.releases: list[tuple[DroughtRelease, bool]] = []
        self.retentions: list[int] = []

    async def store_release(self, release: DroughtRelease, *, replace: bool) -> int:
        """Record the release and report the configured written count."""
        self.releases.append((release, replace))
        return self.written

    async def prune_releases(self, retain: int) -> int:
        """Record any retention request; the history walk must never make one."""
        self.retentions.append(retain)
        return 0


class RecordingIndex:
    """A stored-release index that answers from a fixed set, so a resumed walk needs no database."""

    def __init__(self, *valid_dates: str) -> None:
        """Hold the release weeks this index reports as already stored."""
        self.valid_dates = frozenset(valid_dates)
        self.reads = 0

    async def stored_release_dates(self) -> frozenset[str]:
        """Report the configured weeks and count the read."""
        self.reads += 1
        return self.valid_dates


def _release_body(*drought_classes: int) -> bytes:
    """Render a USDM release payload carrying one dissolved MultiPolygon per drought class."""
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "MultiPolygon", "coordinates": [[SQUARE_RING]]},
                    "properties": {"DM": drought_class},
                }
                for drought_class in drought_classes
            ],
        }
    ).encode()


def _archive(responses: Mapping[str, httpx.Response], requested: list[str] | None = None) -> httpx.MockTransport:
    """Serve one canned response per `usdm_YYYYMMDD.json` file, recording every file the walk asked for."""

    def handler(request: httpx.Request) -> httpx.Response:
        name = str(request.url).rsplit("/", 1)[-1]
        if requested is not None:
            requested.append(name)
        return responses.get(name, httpx.Response(404))

    return httpx.MockTransport(handler)


def _published(*drought_classes: int) -> httpx.Response:
    """A published release response, content-typed the way the archive types it."""
    return httpx.Response(
        200,
        content=_release_body(*drought_classes),
        headers={"content-type": "application/json"},
    )


def _plan(
    weeks: tuple[ReleaseWeek, ...],
    client: httpx.AsyncClient,
    replace: bool = False,
    max_areas: int | None = None,
) -> HistoryBackfillPlan:
    """Build a plan whose pacing is disabled, so a test never sleeps unless pacing is what it pins."""
    return HistoryBackfillPlan(
        weeks=weeks,
        client=client,
        replace=replace,
        max_areas=max_areas,
        pause_seconds=0.0,
    )


def test_only_a_tuesday_is_a_release_week() -> None:
    # USDM publishes on a Thursday for the preceding Tuesday; the Tuesday is what dates the release.
    assert ReleaseWeek(release_date=date(2026, 7, 28)).valid_date == "2026-07-28"
    with pytest.raises(UsdmHistoryContractError, match="must be a Tuesday"):
        ReleaseWeek(release_date=date(2026, 7, 30))


def test_a_week_names_the_exact_archive_file_it_is_read_from() -> None:
    assert STORED_WEEK.source_url == "https://droughtmonitor.unl.edu/data/json/usdm_20260728.json"


def test_the_walk_enumerates_tuesdays_oldest_first() -> None:
    weeks = usdm_release_weeks(date(2026, 7, 1), date(2026, 7, 28))
    assert [week.valid_date for week in weeks] == ["2026-07-07", "2026-07-14", "2026-07-21", "2026-07-28"]
    assert all(week.release_date.weekday() == 1 for week in weeks)


def test_the_default_window_is_two_years_of_release_tuesdays() -> None:
    weeks = two_year_release_weeks(NOW)
    assert len(weeks) == 105
    assert weeks[0].valid_date == "2024-08-06"
    assert weeks[-1].valid_date == "2026-08-04"


def test_the_window_length_is_a_named_parameter_rather_than_a_buried_constant() -> None:
    assert len(two_year_release_weeks(NOW, years=1)) == 53
    assert len(two_year_release_weeks(NOW, years=4)) == 209
    with pytest.raises(UsdmHistoryContractError, match="non-negative"):
        two_year_release_weeks(NOW, years=-1)


def test_a_naive_now_is_refused_rather_than_resolved_against_the_container_local_zone() -> None:
    naive = datetime(2026, 8, 4, 12, 0)  # noqa: DTZ001 - the missing zone is the case under test.
    with pytest.raises(UsdmHistoryContractError, match="must include a timezone"):
        two_year_release_weeks(naive)


def test_a_window_reaching_before_the_first_release_is_clamped_not_invented() -> None:
    weeks = usdm_release_weeks(date(1995, 1, 3), date(2000, 1, 25))
    assert weeks[0].release_date == USDM_ARCHIVE_START
    assert [week.valid_date for week in weeks] == ["2000-01-04", "2000-01-11", "2000-01-18", "2000-01-25"]


def test_a_window_that_ends_before_the_archive_begins_is_empty() -> None:
    assert usdm_release_weeks(date(1990, 1, 1), date(1999, 12, 31)) == ()


def test_the_default_plan_paces_its_requests() -> None:
    plan = default_history_plan(NOW)
    assert plan.pause_seconds == DEFAULT_PAUSE_SECONDS
    assert len(plan.weeks) == 105
    assert plan.replace is False


def test_a_plan_refuses_bounds_that_cannot_be_walked() -> None:
    with pytest.raises(UsdmHistoryContractError, match="pause_seconds"):
        HistoryBackfillPlan(weeks=(STORED_WEEK,), pause_seconds=-1.0)
    with pytest.raises(UsdmHistoryContractError, match="max_areas"):
        HistoryBackfillPlan(weeks=(STORED_WEEK,), max_areas=0)


async def test_every_area_is_dated_by_its_release_week_and_never_by_a_run_clock() -> None:
    store = RecordingStore()
    transport = _archive({"usdm_20260728.json": _published(0, 2)})
    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await ingest_release_week(client, store, STORED_WEEK, replace=False)

    assert outcome.status == "ingested"
    assert outcome.natural_keys == ("usdm:2026-07-28:0", "usdm:2026-07-28:2")
    assert outcome.observed_at == datetime(2026, 7, 28, tzinfo=UTC)


async def test_an_identity_dated_from_a_clock_stops_the_walk_rather_than_backfilling_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The tripwire exists so that the day anything dates a drought area from a run clock, this walk stops
    # instead of writing two years of plausible-looking wrong dates.
    def _clock_dated(valid_date: object, drought_monitor_category: int) -> FeatureIdentity:
        assert valid_date is not None
        return FeatureIdentity(
            producer=USDM_PRODUCER,
            producer_local_id=f"2026-08-04:{drought_monitor_category}",
            observed_at=datetime(2026, 8, 4, 17, 30, tzinfo=UTC),
        )

    monkeypatch.setattr(usdm_history, "build_drought_area_identity", _clock_dated)
    release = DroughtRelease(
        valid_date=EARLIER_WEEK.valid_date,
        source_url=EARLIER_WEEK.source_url,
        areas=(DroughtArea(drought_monitor_category=0, geometry={"type": "MultiPolygon", "coordinates": []}),),
    )
    with pytest.raises(UsdmHistoryContractError, match="not dated by release week"):
        release_identities(EARLIER_WEEK, release)


def test_a_release_with_no_areas_mints_no_identities() -> None:
    release = DroughtRelease(valid_date=EARLIER_WEEK.valid_date, source_url=EARLIER_WEEK.source_url, areas=())
    assert release_identities(EARLIER_WEEK, release) == ()


async def test_a_release_reaches_the_store_with_the_release_date_it_was_requested_for() -> None:
    store = RecordingStore()
    transport = _archive({"usdm_20260721.json": _published(0, 1, 4)})
    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await ingest_release_week(client, store, EARLIER_WEEK, replace=False)

    stored_release, replace = store.releases[0]
    assert stored_release.valid_date == "2026-07-21"
    assert stored_release.source_url.endswith("usdm_20260721.json")
    assert replace is False
    assert outcome.areas_seen == 3


async def test_an_unpublished_week_is_skipped_and_the_walk_carries_on() -> None:
    requested: list[str] = []
    store = RecordingStore()
    transport = _archive({"usdm_20260721.json": _published(0)}, requested)
    weeks = usdm_release_weeks(date(2026, 7, 14), date(2026, 7, 28))
    async with httpx.AsyncClient(transport=transport) as client:
        outcomes = await run_usdm_history_backfill(store, _plan(weeks, client))

    assert [outcome.valid_date for outcome in outcomes] == ["2026-07-14", "2026-07-21", "2026-07-28"]
    assert [outcome.status for outcome in outcomes] == ["skipped", "ingested", "skipped"]
    assert outcomes[0].skip_reason == "not_published"
    assert outcomes[2].skip_reason == "not_published"
    # Every requested week was actually asked for; a missing week is never inferred from its neighbours.
    assert requested == ["usdm_20260714.json", "usdm_20260721.json", "usdm_20260728.json"]


async def test_a_missing_week_is_reported_as_a_gap_and_never_filled_from_its_neighbours() -> None:
    store = RecordingStore()
    transport = _archive({"usdm_20260714.json": _published(0), "usdm_20260728.json": _published(0)})
    weeks = usdm_release_weeks(date(2026, 7, 14), date(2026, 7, 28))
    async with httpx.AsyncClient(transport=transport) as client:
        outcomes = await run_usdm_history_backfill(store, _plan(weeks, client))

    assert history_gap_weeks(outcomes) == ("2026-07-21",)
    # Two weeks were published, so exactly two releases reached the store -- the gap stayed a gap.
    assert [release.valid_date for release, _ in store.releases] == ["2026-07-14", "2026-07-28"]


async def test_a_week_already_in_the_warehouse_is_skipped_without_a_request() -> None:
    requested: list[str] = []
    store = RecordingStore()
    index = RecordingIndex("2026-07-21")
    transport = _archive({"usdm_20260721.json": _published(0), "usdm_20260728.json": _published(0)}, requested)
    weeks = (EARLIER_WEEK, STORED_WEEK)
    async with httpx.AsyncClient(transport=transport) as client:
        outcomes = await run_usdm_history_backfill(store, _plan(weeks, client), index)

    assert index.reads == 1
    assert outcomes[0].skip_reason == "already_stored"
    assert outcomes[0].is_gap is False
    assert requested == ["usdm_20260728.json"]
    assert [release.valid_date for release, _ in store.releases] == ["2026-07-28"]


async def test_replaying_a_stored_week_without_an_index_writes_nothing_and_is_still_ingested() -> None:
    # The durable guarantee is the store's own `ON CONFLICT (valid_date, dm_category) ... WHERE false`,
    # which returns no rows for an already-stored week, so a re-run costs a fetch and writes nothing.
    store = RecordingStore(written=0)
    transport = _archive({"usdm_20260728.json": _published(0, 1)})
    async with httpx.AsyncClient(transport=transport) as client:
        outcomes = await run_usdm_history_backfill(store, _plan((STORED_WEEK,), client))

    assert outcomes[0].status == "ingested"
    assert outcomes[0].areas_seen == 2
    assert outcomes[0].areas_written == 0
    assert history_gap_weeks(outcomes) == ()


async def test_replace_re_requests_a_stored_week_the_index_would_have_skipped() -> None:
    store = RecordingStore()
    index = RecordingIndex("2026-07-28")
    transport = _archive({"usdm_20260728.json": _published(0)})
    async with httpx.AsyncClient(transport=transport) as client:
        outcomes = await run_usdm_history_backfill(store, _plan((STORED_WEEK,), client, replace=True), index)

    assert outcomes[0].status == "ingested"
    assert store.releases[0][1] is True


async def test_an_upstream_failure_fails_that_week_alone() -> None:
    store = RecordingStore()
    transport = _archive({"usdm_20260721.json": httpx.Response(500), "usdm_20260728.json": _published(0)})
    async with httpx.AsyncClient(transport=transport) as client:
        outcomes = await run_usdm_history_backfill(store, _plan((EARLIER_WEEK, STORED_WEEK), client))

    assert outcomes[0].status == "failed"
    assert outcomes[0].skip_reason == "upstream_unavailable"
    assert outcomes[0].is_gap is True
    assert outcomes[1].status == "ingested"


async def test_a_garbled_body_is_unreadable_rather_than_unpublished() -> None:
    garbled = httpx.Response(200, content=b"<html>maintenance</html>", headers={"content-type": "application/json"})
    store = RecordingStore()
    transport = _archive({"usdm_20260728.json": garbled})
    async with httpx.AsyncClient(transport=transport) as client:
        outcomes = await run_usdm_history_backfill(store, _plan((STORED_WEEK,), client))

    assert outcomes[0].status == "failed"
    assert outcomes[0].skip_reason == "unreadable_release"
    assert store.releases == []


async def test_the_record_cap_stops_the_walk_but_still_names_every_unwalked_week() -> None:
    requested: list[str] = []
    store = RecordingStore(written=3)
    weeks = usdm_release_weeks(date(2026, 7, 7), date(2026, 7, 28))
    transport = _archive(
        {f"usdm_2026{month_day}.json": _published(0, 1, 2) for month_day in ("0707", "0714")}, requested
    )
    async with httpx.AsyncClient(transport=transport) as client:
        outcomes = await run_usdm_history_backfill(store, _plan(weeks, client, max_areas=4))

    # Three areas is under the cap, six is over it, and the sixth was still written rather than half-stored.
    assert [outcome.status for outcome in outcomes] == ["ingested", "ingested", "skipped", "skipped"]
    assert [outcome.skip_reason for outcome in outcomes[2:]] == ["record_cap_reached", "record_cap_reached"]
    assert requested == ["usdm_20260707.json", "usdm_20260714.json"]
    assert history_gap_weeks(outcomes) == ("2026-07-21", "2026-07-28")


async def test_the_walk_never_prunes_the_history_it_just_wrote() -> None:
    store = RecordingStore()
    transport = _archive({"usdm_20260728.json": _published(0)})
    async with httpx.AsyncClient(transport=transport) as client:
        await run_usdm_history_backfill(store, _plan((STORED_WEEK,), client))

    assert store.retentions == []


async def test_the_walk_pauses_between_files_but_not_before_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _record)
    store = RecordingStore()
    weeks = usdm_release_weeks(date(2026, 7, 7), date(2026, 7, 28))
    transport = _archive({"usdm_20260707.json": _published(0)})
    async with httpx.AsyncClient(transport=transport) as client:
        await run_usdm_history_backfill(store, HistoryBackfillPlan(weeks=weeks, client=client, pause_seconds=0.25))

    assert slept == [0.25, 0.25, 0.25]


def test_the_summary_reports_a_failed_week_as_a_failed_run() -> None:
    outcomes = [
        WeekOutcome(valid_date="2026-07-21", status="ingested", areas_seen=5, areas_written=5),
        WeekOutcome(
            valid_date="2026-07-28",
            status="failed",
            skip_reason="upstream_unavailable",
            detail="upstream request failed with status 500",
        ),
    ]
    result = merge_week_outcomes(outcomes)

    assert result.source == USDM_HISTORY_SOURCE
    assert result.status == "failed"
    assert result.reason == "upstream request failed with status 500"
    assert result.records_written == 5
    assert result.details["weeks_failed"] == 1
    assert result.details["weeks_missing"] == 1
    assert result.details["weeks_upstream_unavailable"] == 1


def test_a_walk_that_stored_nothing_is_never_reported_as_a_completed_backfill() -> None:
    # A two-year walk that wrote nothing and still reported "ingested" is how a gap gets certified complete.
    outcomes = [
        WeekOutcome(valid_date="2026-07-21", status="skipped", skip_reason="not_published"),
        WeekOutcome(valid_date="2026-07-28", status="skipped", skip_reason="not_published"),
    ]
    result = merge_week_outcomes(outcomes)

    assert result.status == "skipped"
    assert result.reason == "no requested release week was published"
    assert result.details["weeks_not_published"] == 2
    assert result.details["weeks_missing"] == 2


def test_a_walk_that_only_confirmed_stored_weeks_says_so() -> None:
    outcomes = [WeekOutcome(valid_date="2026-07-28", status="skipped", skip_reason="already_stored")]
    result = merge_week_outcomes(outcomes)

    assert result.status == "skipped"
    assert result.reason == "every requested release week was already stored"
    assert result.details["weeks_missing"] == 0


def test_an_empty_window_is_a_skip_rather_than_a_silent_success() -> None:
    result = merge_week_outcomes([])
    assert result.status == "skipped"
    assert result.reason == "the history window contained no release weeks"


def test_the_summary_counts_the_releases_the_weekly_prune_would_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    # The weekly job prunes to DROUGHT_RETAINED_RELEASES after every run, so a backfill wider than that
    # retention is deleted by the next ordinary cron tick unless the retention is raised first.
    monkeypatch.setenv("DROUGHT_RETAINED_RELEASES", "2")
    outcomes = [
        WeekOutcome(valid_date=f"2026-07-{day:02d}", status="ingested", areas_seen=5, areas_written=5)
        for day in (7, 14, 21, 28)
    ]
    result = merge_week_outcomes(outcomes)

    assert result.status == "ingested"
    assert result.details["releases_beyond_retention"] == 2
    assert result.details["weeks_ingested"] == 4
