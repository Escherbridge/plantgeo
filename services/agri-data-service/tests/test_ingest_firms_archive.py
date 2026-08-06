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
    collapse_history_records,
    firms_archive_source,
    firms_day_range,
    history_day_spans,
    normalize_confidence,
    parse_firms_csv,
    parse_product_availability,
    processing_tier,
    product_spatial_support_meters,
    products_covering,
    products_covering_span,
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
# MODIS publishes the same columns with a 0-100 `confidence` rather than the VIIRS `l`/`n`/`h`.
MODIS_ROW = "47.83797,-113.26495,312.4,1.0,1.0,2022-08-05,1106,Terra,78,6.1NRT,33.1"
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
    """One acquisition delivered by two products of one satellite is one detection, so it is one write.

    `build_firms_identity` keys on `satellite:acqDate:acqTime:lat4dp:lon4dp` and cannot carry `product`
    -- the key is byte-identical to the TypeScript `properties->>'id'` that stored rows depend on. Two
    products of the same satellite therefore key identically, and `collapse_history_records` decides
    which survives rather than letting arrival order decide.
    """

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


# --- standard processing supersedes near-real-time for one key, deterministically -------------------


def _keyed_record(product: str, satellite: str = "N20") -> dict[str, object]:
    """One parsed record for a fixed acquisition, so two products of one satellite key identically."""
    return parse_firms_csv(_csv(ARCHIVE_ROW.replace(",N20,", f",{satellite},")), product)[0]


@pytest.mark.parametrize(
    "order",
    [("VIIRS_NOAA20_NRT", "VIIRS_NOAA20_SP"), ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT")],
)
def test_standard_processing_supersedes_near_real_time_whichever_arrives_first(order: tuple[str, str]) -> None:
    """`product` is not in the key, so NRT and SP of one satellite collide; SP must win either way.

    Failure mode this pins out: last-write-wins over `FIRMS_HISTORY_SOURCES` order. That happens to put
    SP after NRT today, so the right record survives by accident. Reorder the tuple, or have a caller
    pass its own candidate list, and the NRT draft silently overwrites the SP reprocessing of the same
    physical detection -- with nothing in the summary to notice by.
    """
    collapsed = collapse_history_records([_keyed_record(product) for product in order])

    assert len(collapsed) == 1
    properties = collapsed[0]["properties"]
    assert isinstance(properties, dict)
    assert properties["product"] == "VIIRS_NOAA20_SP"
    assert properties["supersededProduct"] == "VIIRS_NOAA20_NRT"


def test_two_records_of_the_same_product_keep_the_later_arrival_and_record_no_supersession() -> None:
    """Equal tiers keep the forward job's `Map.set` semantics; nothing was superseded, so nothing says so."""
    collapsed = collapse_history_records([_keyed_record("VIIRS_NOAA20_SP"), _keyed_record("VIIRS_NOAA20_SP")])

    assert len(collapsed) == 1
    properties = collapsed[0]["properties"]
    assert isinstance(properties, dict)
    assert "supersededProduct" not in properties


def test_two_products_of_different_satellites_are_two_detections_and_never_collapse() -> None:
    """The precedence rule must not merge across satellites: N and N20 saw the fire independently."""
    collapsed = collapse_history_records([_keyed_record("VIIRS_SNPP_SP", "N"), _keyed_record("VIIRS_NOAA20_SP")])

    assert len(collapsed) == 2


def test_the_processing_tier_is_read_off_the_product_suffix_and_never_off_the_satellite() -> None:
    assert processing_tier("VIIRS_NOAA20_SP") > processing_tier("VIIRS_NOAA20_NRT")
    assert processing_tier("MODIS_SP") > processing_tier("MODIS_NRT")
    # An unknown or absent token is the lowest tier rather than an error: absence must not outrank SP.
    assert processing_tier(None) == processing_tier("VIIRS_NOAA20_NRT")


# --- a span is up to five days wide, so its products are resolved over all of them -------------------


def test_a_product_whose_coverage_begins_mid_span_is_still_asked_for_the_days_it_covers() -> None:
    """Resolving from `start_day` alone dropped a product for the part of the span it does publish.

    In the committed availability fixture VIIRS_NOAA20_NRT begins 2026-06-01. A five-day span starting
    2026-05-29 covers 05-29..06-02, so that product publishes two of its days -- and resolving from the
    start day alone names none of them, silently answering `records_seen=0` for 06-01 and 06-02.
    """
    availability = parse_product_availability(AVAILABILITY_CSV)
    start = date(2026, 5, 29)

    from_start_day = products_covering(availability, start)
    over_the_span = products_covering_span(availability, start, 5)

    assert "VIIRS_NOAA20_NRT" not in from_start_day
    assert "VIIRS_NOAA20_NRT" in over_the_span
    # Candidate order, so the request sequence stays deterministic across walks.
    assert over_the_span == (
        "VIIRS_SNPP_NRT",
        "VIIRS_NOAA20_NRT",
        "VIIRS_NOAA21_NRT",
        "VIIRS_NOAA20_SP",
    )


def test_a_one_day_span_resolves_exactly_the_products_that_cover_that_day() -> None:
    """Widening to the span must not widen a single day: over-asking is only safe for days in the span."""
    availability = parse_product_availability(AVAILABILITY_CSV)
    for day in (date(2022, 8, 5), date(2026, 5, 29), date(2026, 8, 5)):
        assert products_covering_span(availability, day, 1) == products_covering(availability, day)


async def test_a_span_no_product_covers_is_named_rather_than_reported_as_an_empty_week(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`records_seen=0` from an uncovered span is indistinguishable from "no fires" unless the walk says so.

    `firms.py` takes a plain `structlog.get_logger()`, which lands on stdout in a test run, so the
    warning is read off captured stdout rather than off `caplog` -- see ingest/AGENTS.md
    "results.py, runner.py and commands.py" for why that logger is not yet bound to stderr.
    """
    gapped = "\n".join(("data_id,min_date,max_date", "VIIRS_NOAA20_SP,2018-04-01,2020-12-31"))

    def handler(request: httpx.Request) -> httpx.Response:
        body = gapped if "data_availability" in request.url.path else _csv(ARCHIVE_ROW)
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

    logged = capsys.readouterr().out
    assert results[0].records_seen == 0
    assert writer.writes == []
    assert "firms_history_spans_uncovered" in logged
    assert "2022-08-05+1" in logged


# --- the coverage frontier travels with the record, so an as-of query gates on a field ---------------


async def test_every_archive_record_carries_the_coverage_frontier_of_the_product_that_answered() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "data_availability" in request.url.path:
            body = AVAILABILITY_CSV
        else:
            product = request.url.path.split("/")[_PRODUCT_INDEX]
            body = _csv(ARCHIVE_ROW.replace(",N20,", f",{SATELLITE_BY_PRODUCT[product]},"))
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/csv"})

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await run_source_backfill(
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

    coverage_by_product = {
        str(write.properties["product"]): write.properties["productCoverageThrough"] for write in writer.writes
    }
    assert coverage_by_product == {
        "VIIRS_SNPP_SP": "2026-04-27",
        "VIIRS_NOAA20_SP": "2026-05-31",
        "MODIS_SP": "2026-04-30",
    }


# --- MODIS is a different instrument, not a further satellite ----------------------------------------


def test_the_instrument_pixel_travels_with_the_record_so_frp_is_not_read_as_one_scale() -> None:
    """MODIS_SP's median FRP on production is 33.10 MW against VIIRS' 4.27; the gap is pixel area."""
    viirs = parse_firms_csv(_csv(ARCHIVE_ROW), "VIIRS_NOAA20_SP")[0]["properties"]
    modis = parse_firms_csv(_csv(MODIS_ROW), "MODIS_SP")[0]["properties"]
    assert isinstance(viirs, dict)
    assert isinstance(modis, dict)
    assert viirs["spatialSupportMeters"] == 375
    assert modis["spatialSupportMeters"] == 1000


def test_an_unrecognised_product_omits_the_pixel_rather_than_defaulting_to_one() -> None:
    """A wrong spatial support is worse than an absent one: it makes an incomparable value look comparable."""
    assert product_spatial_support_meters("LANDSAT_SOMETHING") is None
    parsed = parse_firms_csv(_csv(ARCHIVE_ROW), "LANDSAT_SOMETHING")[0]["properties"]
    assert isinstance(parsed, dict)
    assert "spatialSupportMeters" not in parsed


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("l", "low"), ("n", "nominal"), ("h", "high"), ("0", "low"), ("29", "low"), ("30", "nominal"),
     ("80", "nominal"), ("81", "high"), ("100", "high")],
)
def test_the_two_confidence_spellings_normalise_onto_one_scale(raw: str, expected: str) -> None:
    """VIIRS writes l/n/h and MODIS a 0-100 percentage into one `properties.confidence`."""
    assert normalize_confidence(raw) == expected


def test_an_unreadable_confidence_normalises_to_nothing_rather_than_to_nominal() -> None:
    assert normalize_confidence("unknown") is None
    parsed = parse_firms_csv(_csv(ARCHIVE_ROW.replace(",n,", ",unknown,")), "VIIRS_NOAA20_SP")[0]["properties"]
    assert isinstance(parsed, dict)
    assert parsed["confidence"] == "unknown"
    assert "confidenceNormalized" not in parsed


def test_the_raw_confidence_is_kept_verbatim_beside_the_normalised_one() -> None:
    """The normalised band is an addition, never a replacement: MODIS' percentage is a real measurement."""
    modis = parse_firms_csv(_csv(MODIS_ROW), "MODIS_SP")[0]["properties"]
    assert isinstance(modis, dict)
    assert modis["confidence"] == "78"
    assert modis["confidenceNormalized"] == "nominal"
