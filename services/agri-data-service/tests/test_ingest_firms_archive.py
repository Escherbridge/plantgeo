"""FIRMS archive history: the availability table, per-day product selection, span cutting, age bounds."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.backfill import BackfillPlan, run_source_backfill
from agri_data_service.ingest.firms import (
    MAX_FIRMS_DAY_RANGE,
    build_fire_detection_write,
    firms_archive_source,
    firms_day_range,
    history_day_spans,
    parse_firms_csv,
    parse_product_availability,
    products_covering,
)
from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.policy import PACIFIC_NORTHWEST_COVERAGE_BBOX
from agri_data_service.ingest.source import FetchRequest, HistoryUnavailableError, HistoryWindow

if TYPE_CHECKING:
    from agri_data_service.ingest.writer import FeatureWrite

# Captured verbatim from the live `data_availability` table on 2026-08-05. The disjointness this
# fixture encodes -- VIIRS_NOAA20_SP ending 2026-05-31, the day before VIIRS_NOAA20_NRT begins -- is an
# upstream fact that moves with every reprocessing campaign, which is why the code reads it per walk.
AVAILABILITY_CSV = "\n".join(
    (
        "data_id,min_date,max_date",
        "MODIS_NRT,2026-05-01,2026-08-05",
        "MODIS_SP,2000-11-01,2026-04-30",
        "VIIRS_NOAA20_NRT,2026-06-01,2026-08-05",
        "VIIRS_NOAA20_SP,2018-04-01,2026-05-31",
        "VIIRS_NOAA21_NRT,2024-01-17,2026-08-05",
        "VIIRS_SNPP_NRT,2026-04-28,2026-08-05",
        "VIIRS_SNPP_SP,2012-01-20,2026-04-27",
    )
)

VIIRS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,frp"
# Standard-processing rows carry a bare version (`2.0`); near-real-time carries `2.0NRT`.
ARCHIVE_ROW = "47.83797,-113.26495,312.4,0.4,0.4,2022-08-05,1106,N20,n,2.0,12.3"
RUN_CLOCK = datetime(2026, 8, 5, tzinfo=UTC)

# Where the product sits in `/api/area/csv/{key}/{product}/{area}/{day_range}/{start_date}`.
_PRODUCT_INDEX = -4
SATELLITE_BY_PRODUCT = {"VIIRS_SNPP_SP": "N", "VIIRS_NOAA20_SP": "N20", "MODIS_SP": "Terra"}


class RecordingWriter:
    """A feature writer that records what a walk handed it, so this test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: list[FeatureWrite]) -> int:
        self.writes.extend(writes)
        return len(writes)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NASA_FIRMS_KEY", "test-key")
    monkeypatch.delenv("FIRMS_DAY_RANGE", raising=False)


def _csv(*rows: str) -> str:
    return "\n".join((VIIRS_HEADER, *rows))


def _point_feature(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-113.26495, 47.83797]},
        "properties": properties,
    }


def test_the_availability_table_is_parsed_by_header_name() -> None:
    availability = parse_product_availability(AVAILABILITY_CSV)
    assert availability["VIIRS_SNPP_SP"].earliest == date(2012, 1, 20)
    assert availability["VIIRS_SNPP_SP"].latest == date(2026, 4, 27)
    assert availability["VIIRS_SNPP_SP"].covers(date(2022, 8, 5)) is True
    assert availability["VIIRS_SNPP_NRT"].covers(date(2022, 8, 5)) is False


@pytest.mark.parametrize("payload", ["", "data_id,min_date,max_date", "product,from,to\nX,1,2"])
def test_an_empty_or_mistyped_availability_table_is_refused_rather_than_read_as_no_products(payload: str) -> None:
    with pytest.raises(UpstreamPayloadError):
        parse_product_availability(payload)


def test_the_near_real_time_and_standard_processing_series_are_never_asked_for_the_same_day() -> None:
    availability = parse_product_availability(AVAILABILITY_CSV)
    archive_day = products_covering(availability, date(2022, 8, 5))
    current_day = products_covering(availability, date(2026, 8, 5))
    assert archive_day == ("VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "MODIS_SP")
    assert current_day == ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
    for day in (archive_day, current_day):
        for satellite_family in ("SNPP", "NOAA20", "NOAA21"):
            assert len({product for product in day if satellite_family in product}) <= 1


def test_a_day_no_product_covers_yields_no_products_rather_than_a_guess() -> None:
    assert products_covering(parse_product_availability(AVAILABILITY_CSV), date(1999, 1, 1)) == ()


def test_the_parsed_row_carries_the_product_that_answered_as_its_discriminator() -> None:
    """`satellite` cannot serve as the discriminator: NOAA20's NRT and SP series both report `N20`."""
    near_real_time = parse_firms_csv(_csv(ARCHIVE_ROW), "VIIRS_NOAA20_NRT")[0]["properties"]
    standard = parse_firms_csv(_csv(ARCHIVE_ROW), "VIIRS_NOAA20_SP")[0]["properties"]
    assert isinstance(near_real_time, dict)
    assert isinstance(standard, dict)
    assert near_real_time["satellite"] == standard["satellite"] == "N20"
    assert near_real_time["product"] == "VIIRS_NOAA20_NRT"
    assert standard["product"] == "VIIRS_NOAA20_SP"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2022-08-05T00:00:00+00:00", "2022-08-06T00:00:00+00:00", ((date(2022, 8, 5), 1),)),
        ("2022-08-05T00:00:00+00:00", "2022-08-10T00:00:00+00:00", ((date(2022, 8, 5), 5),)),
        (
            "2022-08-05T00:00:00+00:00",
            "2022-08-13T00:00:00+00:00",
            ((date(2022, 8, 5), 5), (date(2022, 8, 10), 3)),
        ),
    ],
)
def test_a_chunk_is_cut_into_spans_no_wider_than_the_five_days_firms_allows(
    start: str,
    end: str,
    expected: tuple[tuple[date, int], ...],
) -> None:
    spans = history_day_spans(HistoryWindow(start=datetime.fromisoformat(start), end=datetime.fromisoformat(end)))
    assert spans == expected
    assert all(span <= MAX_FIRMS_DAY_RANGE for _day, span in spans)


def test_a_chunk_bound_carrying_a_local_offset_is_bucketed_by_its_own_utc_date() -> None:
    """A `-07:00` window covers two UTC days, and the span says so rather than silently naming one.

    Midnight on 2022-08-05 at `-07:00` is 07:00Z on the 5th and its exclusive end is 07:00Z on the 6th,
    so the honest request is two days from the 5th -- a superset of what was asked for, every record in
    it a real detection carrying the acquisition day FIRMS itself named. The failure this pins out is the
    opposite one: a bound read as a naive local date would start the walk on 2022-08-04 and fetch a day
    nobody asked for while still missing part of the day they did.
    """
    spans = history_day_spans(
        HistoryWindow(
            start=datetime.fromisoformat("2022-08-05T00:00:00-07:00"),
            end=datetime.fromisoformat("2022-08-06T00:00:00-07:00"),
        )
    )
    assert spans == ((date(2022, 8, 5), 2),)


def test_the_day_range_clamp_refuses_the_six_to_ten_range_the_api_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured live 2026-08-05: 1-5 answer HTTP 200; 6, 7 and 10 answer `400 Invalid day range. Expects [1..5].`"""
    assert MAX_FIRMS_DAY_RANGE == 5
    for requested in ("6", "7", "10"):
        monkeypatch.setenv("FIRMS_DAY_RANGE", requested)
        assert firms_day_range() == MAX_FIRMS_DAY_RANGE


async def test_the_archive_source_refuses_a_current_window_and_a_pre_archive_past_one() -> None:
    source = firms_archive_source()
    request = FetchRequest(bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX, max_records=10)
    with pytest.raises(NotImplementedError):
        await source.fetch_current(request)
    with pytest.raises(HistoryUnavailableError):
        await source.fetch_history(
            request,
            HistoryWindow(start=datetime(1999, 1, 1, tzinfo=UTC), end=datetime(1999, 1, 2, tzinfo=UTC)),
        )


async def test_a_history_walk_reads_availability_once_and_writes_the_dated_archive_products() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if "data_availability" in request.url.path:
            body = AVAILABILITY_CSV
        else:
            # Each product answers with its OWN satellite token, which is what really happens and what
            # keeps three products from keying to one detection. `_PRODUCT_INDEX` is where the requested
            # product sits in `/api/area/csv/{key}/{product}/{area}/{day_range}/{start_date}`.
            product = request.url.path.split("/")[_PRODUCT_INDEX]
            body = _csv(ARCHIVE_ROW.replace(",N20,", f",{SATELLITE_BY_PRODUCT[product]},"))
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/csv"})

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await run_source_backfill(
            firms_archive_source(),
            writer,
            BackfillPlan(
                window=HistoryWindow(start=datetime(2022, 8, 5, tzinfo=UTC), end=datetime(2022, 8, 6, tzinfo=UTC)),
                chunk=timedelta(days=1),
                bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
                now=RUN_CLOCK,
                client=client,
            ),
        )

    assert sum(1 for path in requested if "data_availability" in path) == 1
    assert sorted(path.split("/")[_PRODUCT_INDEX] for path in requested if "/area/" in path) == [
        "MODIS_SP",
        "VIIRS_NOAA20_SP",
        "VIIRS_SNPP_SP",
    ]
    assert all(path.endswith("/1/2022-08-05") for path in requested if "/area/" in path)
    assert [result.records_seen for result in results] == [3]
    assert len(writer.writes) == 3
    assert sorted(str(write.properties["product"]) for write in writer.writes) == [
        "MODIS_SP",
        "VIIRS_NOAA20_SP",
        "VIIRS_SNPP_SP",
    ]
    written = next(write for write in writer.writes if write.properties["product"] == "VIIRS_NOAA20_SP")
    assert written.identity.natural_key == "firms:N20:2022-08-05:1106:47.8380:-113.2649"
    assert written.identity.observed_at == datetime(2022, 8, 5, 11, 6, tzinfo=UTC)
    assert written.properties["observedAt"] == "2022-08-05T11:06:00.000Z"


async def test_two_products_of_one_satellite_collapse_to_a_single_write() -> None:
    """The backstop for the `ON CONFLICT DO UPDATE` a duplicated key inside one batch would fail on."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Every product answers as N20 -- the shape the per-day availability selection prevents.
        body = AVAILABILITY_CSV if "data_availability" in request.url.path else _csv(ARCHIVE_ROW)
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/csv"})

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await run_source_backfill(
            firms_archive_source(),
            writer,
            BackfillPlan(
                window=HistoryWindow(start=datetime(2022, 8, 5, tzinfo=UTC), end=datetime(2022, 8, 6, tzinfo=UTC)),
                chunk=timedelta(days=1),
                bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
                now=RUN_CLOCK,
                client=client,
            ),
        )
    assert [result.records_seen for result in results] == [1]
    assert len(writer.writes) == 1
    assert len({write.external_id for write in writer.writes}) == 1


def test_a_four_year_old_archive_detection_is_written_where_the_forward_path_would_reject_it() -> None:
    """The forward job's rolling age window is exactly what a history walk must not re-impose."""
    stale = _point_feature({"satellite": "N20", "acqDate": "2022-08-05", "acqTime": "1106"})
    assert build_fire_detection_write(stale, "fire-detections", timedelta(days=2), RUN_CLOCK) is None
    archived = build_fire_detection_write(stale, "fire-detections", None, RUN_CLOCK)
    assert archived is not None
    assert archived.identity.observed_at == datetime(2022, 8, 5, 11, 6, tzinfo=UTC)


def test_an_archive_detection_dated_after_the_run_clock_is_still_refused() -> None:
    """Removing the age floor must not remove the future-skew guard: a future acquisition is garbage."""
    future = _point_feature({"satellite": "N20", "acqDate": "2026-09-01", "acqTime": "1106"})
    assert build_fire_detection_write(future, "fire-detections", None, RUN_CLOCK) is None
