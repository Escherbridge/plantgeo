"""MTBS release eligibility excludes stray calendar markers without weakening genuine absence."""

from datetime import UTC, date, datetime

import pytest

from agri_data_service.foundation.parquet.paths import absence_marker_path
from agri_data_service.ingest.mtbs import MTBS_ANNUAL_RELEASE_DATES as INGEST_RELEASE_DATES
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.parquet_ops.serving import resolve_day, resolve_release
from agri_data_service.parquet_ops.wire import DayNotWritten, GovernedAbsenceDay, LaneNeverWritten, PublishedDay
from agri_data_service.warehouse.mtbs_releases import MTBS_ANNUAL_RELEASE_DATES
from tests.parquet_ops.fakes import FakeListing, FakeRowReader

EARLIER_RELEASE = date(2023, 8, 9)
LATER_RELEASE = date(2024, 8, 22)
STRAY_DAY = date(2024, 8, 21)
MTBS_SCOPE = ReadScope(layer="burn-severity", kind="observed", tier=13, bbox=None)


def _absence(listing: FakeListing, day: date, *, layer: str = "burn-severity") -> None:
    listing.write_absence(
        layer,
        "observed",
        13,
        day,
        reason="captured source has no fires in scope",
        upstream_response="complete source capture returned no features",
        recorded_at=datetime(2026, 9, 10, tzinfo=UTC),
        run_id="release-test",
    )


def test_ingest_and_serving_share_the_unchanged_completed_cohort_contract() -> None:
    assert INGEST_RELEASE_DATES is MTBS_ANNUAL_RELEASE_DATES
    assert dict(MTBS_ANNUAL_RELEASE_DATES) == {
        2018: date(2020, 11, 24),
        2019: date(2021, 9, 27),
        2020: date(2022, 4, 28),
        2021: EARLIER_RELEASE,
        2022: LATER_RELEASE,
    }


def test_nonrelease_absence_does_not_shadow_an_earlier_mtbs_publication() -> None:
    listing = FakeListing()
    part = listing.write_day("burn-severity", "observed", 13, EARLIER_RELEASE)
    listing.write_day("burn-severity", "observed", 13, LATER_RELEASE)
    _absence(listing, STRAY_DAY)
    reader = FakeRowReader(rows_by_key={part: ({"fire_id": "retained-fire"},)})

    envelope = resolve_release(listing, reader, scope=MTBS_SCOPE, as_of=STRAY_DAY)

    assert isinstance(envelope, PublishedDay)
    assert envelope.served_day == EARLIER_RELEASE
    assert envelope.requested_day == STRAY_DAY
    assert envelope.rows == ({"fire_id": "retained-fire"},)
    assert isinstance(resolve_day(listing, reader, scope=MTBS_SCOPE, day=STRAY_DAY), GovernedAbsenceDay)


def test_registered_mtbs_release_absence_keeps_its_evidence_and_shadows_prior_data() -> None:
    listing = FakeListing()
    listing.write_day("burn-severity", "observed", 13, EARLIER_RELEASE)
    _absence(listing, LATER_RELEASE)
    reader = FakeRowReader()

    envelope = resolve_release(listing, reader, scope=MTBS_SCOPE, as_of=date(2026, 9, 10))

    assert isinstance(envelope, GovernedAbsenceDay)
    assert envelope.served_day == LATER_RELEASE
    assert envelope.absence.run_id == "release-test"
    assert reader.reads == []


@pytest.mark.parametrize(
    ("as_of", "served_day"),
    [
        (EARLIER_RELEASE, EARLIER_RELEASE),
        (STRAY_DAY, EARLIER_RELEASE),
        (LATER_RELEASE, LATER_RELEASE),
        (date(2026, 9, 10), LATER_RELEASE),
    ],
)
def test_mtbs_release_selection_is_inclusive_and_never_selects_future_data(as_of: date, served_day: date) -> None:
    listing = FakeListing()
    for day in (EARLIER_RELEASE, LATER_RELEASE):
        listing.write_day("burn-severity", "observed", 13, day)
    result = resolve_release(listing, FakeRowReader(), scope=MTBS_SCOPE, as_of=as_of)
    assert isinstance(result, PublishedDay)
    assert result.served_day == served_day
    assert result.requested_day == as_of


def test_before_first_registered_mtbs_release_reports_written_lane_without_hindsight() -> None:
    listing = FakeListing()
    first = min(MTBS_ANNUAL_RELEASE_DATES.values())
    listing.write_day("burn-severity", "observed", 13, first)
    as_of = date(2020, 11, 23)
    _absence(listing, as_of)
    assert resolve_release(listing, FakeRowReader(), scope=MTBS_SCOPE, as_of=as_of) == DayNotWritten(
        requested_day=as_of
    )


def test_empty_mtbs_lane_remains_never_written() -> None:
    as_of = date(2026, 9, 10)
    assert resolve_release(FakeListing(), FakeRowReader(), scope=MTBS_SCOPE, as_of=as_of) == LaneNeverWritten(
        requested_day=as_of
    )


def test_registered_mtbs_conflict_still_refuses() -> None:
    listing = FakeListing()
    listing.write_day("burn-severity", "observed", 13, LATER_RELEASE)
    _absence(listing, LATER_RELEASE)
    with pytest.raises(ServingRefusalError, match="carries both"):
        resolve_release(listing, FakeRowReader(), scope=MTBS_SCOPE, as_of=LATER_RELEASE)


def test_unreadable_registered_absence_still_refuses() -> None:
    listing = FakeListing()
    _absence(listing, LATER_RELEASE)
    listing.objects.pop(absence_marker_path("burn-severity", "observed", 13, LATER_RELEASE))
    with pytest.raises(ServingRefusalError, match="absence"):
        resolve_release(listing, FakeRowReader(), scope=MTBS_SCOPE, as_of=LATER_RELEASE)


@pytest.mark.parametrize("layer", ["drought", "soil-survey"])
def test_other_layers_keep_absence_on_dates_outside_the_mtbs_registry(layer: str) -> None:
    listing = FakeListing()
    listing.write_day(layer, "observed", 13, EARLIER_RELEASE)
    _absence(listing, STRAY_DAY, layer=layer)
    scope = ReadScope(layer=layer, kind="observed", tier=13, bbox=None)
    result = resolve_release(listing, FakeRowReader(), scope=scope, as_of=STRAY_DAY)
    assert isinstance(result, GovernedAbsenceDay)
    assert result.served_day == STRAY_DAY
