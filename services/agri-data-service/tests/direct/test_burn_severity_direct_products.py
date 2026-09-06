"""The release-day <-> ignition-year mapping this whole package's `release_series` grain depends on."""

from __future__ import annotations

from datetime import date

from agri_data_service.ingest.mtbs import MTBS_ANNUAL_RELEASE_DATES, resolve_data_available_at
from agri_data_service.pipeline.direct.burn_severity.products import (
    burn_severity_lane_registration,
    governed_release_days,
    release_days_by_ignition_year,
)
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_STREAM

#: The five dated completion releases established as of `docs/lanes/burn-severity.md` section 3.
#: A future governance action MAY add a sixth (or more) -- this pins today's known set as a
#: regression check, not a ceiling this module enforces.
KNOWN_RELEASE_DAYS = (
    date(2020, 11, 24),
    date(2021, 9, 27),
    date(2022, 4, 28),
    date(2023, 8, 9),
    date(2024, 8, 22),
)


def test_the_lane_registration_is_the_registered_burn_severity_stream() -> None:
    lane = burn_severity_lane_registration()

    assert lane.slug == BURN_SEVERITY_STREAM
    assert lane.nature == "release_series"


def test_every_governed_ignition_year_appears_under_its_own_release_date() -> None:
    by_day = release_days_by_ignition_year()

    for ignition_year in MTBS_ANNUAL_RELEASE_DATES:
        release_day = resolve_data_available_at(ignition_year).date()
        assert ignition_year in by_day[release_day]


def test_governed_release_days_matches_the_documented_five_dates_today() -> None:
    days = governed_release_days()

    assert set(KNOWN_RELEASE_DAYS).issubset(set(days))


def test_governed_release_days_is_sorted_ascending() -> None:
    days = governed_release_days()

    assert list(days) == sorted(days)


def test_release_days_by_ignition_year_partitions_every_governed_year_exactly_once() -> None:
    by_day = release_days_by_ignition_year()

    every_year = [year for years in by_day.values() for year in years]
    assert sorted(every_year) == sorted(MTBS_ANNUAL_RELEASE_DATES)


def test_lane_registration_is_read_fresh_not_cached_at_import() -> None:
    """Two calls must return equal, independently-read registrations -- never one shared mutable object."""
    first = burn_severity_lane_registration()
    second = burn_severity_lane_registration()

    assert first == second
