"""USDM release-week enumeration: the Tuesday contract, the archive file each week names, and the floor clamp.

THE POSTGRES HISTORY WALK THIS FILE COVERED WAS DELETED 2026-09-06 with the `ingest-drought-history`
verb (owner directive, "remove the code for ingestion into the DB"). Thirty tests went with it:
`ingest_release_week`, `run_usdm_history_backfill`, `PostgresStoredReleaseIndex`, `HistoryBackfillPlan`,
`release_identities`, `WeekOutcome`/`merge_week_outcomes`/`history_gap_weeks`, and
`two_year_release_weeks`. Every one of them existed to walk USDM archive files into `geo.drought_areas`,
which now has no Python producer at all.

What replaced them: `pipeline/direct/drought/backfill.py` walks the SAME
`usdm_release_weeks(lane.history_floor, settled_through)` calendar asserted below and writes Parquet, so
the Tuesday contract, the archive-file naming and the `USDM_ARCHIVE_START` clamp are still the rules a
drought day is selected by -- which is why they stay pinned here rather than moving.
"""

from __future__ import annotations

from datetime import date

import pytest

from agri_data_service.ingest.usdm_history import (
    USDM_ARCHIVE_START,
    ReleaseWeek,
    UsdmHistoryContractError,
    usdm_release_weeks,
)

STORED_WEEK = ReleaseWeek(release_date=date(2026, 7, 28))


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


def test_a_window_reaching_before_the_first_release_is_clamped_not_invented() -> None:
    weeks = usdm_release_weeks(date(1995, 1, 3), date(2000, 1, 25))
    assert weeks[0].release_date == USDM_ARCHIVE_START
    assert [week.valid_date for week in weeks] == ["2000-01-04", "2000-01-11", "2000-01-18", "2000-01-25"]


def test_a_window_that_ends_before_the_archive_begins_is_empty() -> None:
    assert usdm_release_weeks(date(1990, 1, 1), date(1999, 12, 31)) == ()
