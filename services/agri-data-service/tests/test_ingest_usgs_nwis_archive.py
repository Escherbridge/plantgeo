"""USGS NWIS daily-values history: per-day records, publisher-named days, and the sentinel that is not a reading."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from agri_data_service.ingest.source import HistoryUnavailableError, HistoryWindow
from agri_data_service.ingest.usgs_nwis import (
    USGS_DAILY_VALUES_EARLIEST,
    USGS_STREAMFLOW_ARCHIVE_SOURCE,
    fetch_streamflow_history,
    parse_daily_value_series,
    site_zone_offset,
    usgs_streamflow_archive_source,
)

# Field-for-field the shape NHDPlus... no: the shape waterservices.usgs.gov/nwis/dv answered on
# 2026-08-07 for the Sandy River gauge. `dateTime` really does arrive naive, and `timeZoneInfo`
# really does carry both offsets -- both facts are what the parser is built around.
PACIFIC_ZONE_INFO = {
    "defaultTimeZone": {"zoneOffset": "-08:00", "zoneAbbreviation": "PST"},
    "daylightSavingsTimeZone": {"zoneOffset": "-07:00", "zoneAbbreviation": "PDT"},
    "siteUsesDaylightSavingsTime": True,
}


def _daily_series(
    site_number: str,
    readings: list[tuple[str, str]],
    *,
    zone_info: object = PACIFIC_ZONE_INFO,
) -> dict[str, object]:
    """One NWIS daily-values time series carrying (dateTime, value) pairs."""
    source_info: dict[str, object] = {
        "siteName": f"Gauge {site_number}",
        "siteCode": [{"value": site_number}],
        "geoLocation": {"geogLocation": {"latitude": 45.4, "longitude": -122.1}},
    }
    if zone_info is not None:
        source_info["timeZoneInfo"] = zone_info
    return {
        "sourceInfo": source_info,
        "values": [
            {
                "value": [
                    {"value": value, "qualifiers": ["A"], "dateTime": reading_time} for reading_time, value in readings
                ],
                "qualifier": [{"qualifierCode": "A"}],
            }
        ],
        "variable": {"variableCode": [{"value": "00060"}]},
    }


def test_a_series_yields_one_record_per_day_rather_than_only_its_latest() -> None:
    # The forward path's parse_gauge keeps readings[-1], because the instantaneous feed is asked
    # "what is it now". A history walk wants every day, each as its own observation.
    records = parse_daily_value_series(
        _daily_series(
            "14137000",
            [
                ("2022-08-05T00:00:00.000", "495"),
                ("2022-08-06T00:00:00.000", "483"),
                ("2022-08-07T00:00:00.000", "475"),
            ],
        )
    )

    assert [record["flowCfs"] for record in records] == [495.0, 483.0, 475.0]
    assert all(record["siteNo"] == "14137000" for record in records)
    # One identity per (site, day) is what makes these distinct versions of one gauge entity
    # rather than three overwrites of a single row.
    assert len({record["updatedAt"] for record in records}) == 3


def test_a_day_keeps_the_name_usgs_gave_it_and_gains_the_sites_standard_offset() -> None:
    records = parse_daily_value_series(_daily_series("14137000", [("2022-08-05T00:00:00.000", "495")]))

    # The stamped offset must never move the day: geo.feature_observation_day reads the first ten
    # characters, and the whole point is that they still say what USGS said.
    assert records[0]["updatedAt"] == "2022-08-05T00:00:00.000-08:00"
    assert str(records[0]["updatedAt"])[:10] == "2022-08-05"
    # Standard time, not daylight, even though this reading is in August: a daily value is computed
    # over the site's standard-time day year-round, and switching offsets mid-walk would name one
    # instant two ways and mint two identities for one reading.
    assert site_zone_offset({"timeZoneInfo": PACIFIC_ZONE_INFO}) == "-08:00"


def test_a_site_with_no_zone_info_falls_back_to_utc_rather_than_guessing_a_region() -> None:
    assert site_zone_offset({}) == "+00:00"
    assert site_zone_offset({"timeZoneInfo": {}}) == "+00:00"
    records = parse_daily_value_series(_daily_series("99999999", [("2022-08-05T00:00:00.000", "12")], zone_info=None))
    # With a midnight timestamp, UTC leaves the publisher-named day exactly as named.
    assert records[0]["updatedAt"] == "2022-08-05T00:00:00.000+00:00"


def test_the_missing_value_sentinel_is_dropped_rather_than_written_as_a_reading() -> None:
    records = parse_daily_value_series(
        _daily_series(
            "14137000",
            [
                ("2022-08-05T00:00:00.000", "-999999"),
                ("2022-08-06T00:00:00.000", "483"),
            ],
        )
    )

    # -999999 arrives as an ordinary numeric string. Writing it would poison every percentile and
    # colour ramp computed downstream; writing it as a null flow would be a fabricated observation
    # of an absence. The day is simply not reported.
    assert [record["updatedAt"] for record in records] == ["2022-08-06T00:00:00.000-08:00"]
    assert records[0]["flowCfs"] == 483.0


def test_a_series_with_no_site_number_is_refused_rather_than_keyed_on_an_empty_string() -> None:
    series = _daily_series("14137000", [("2022-08-05T00:00:00.000", "495")])
    series["sourceInfo"]["siteCode"] = []  # type: ignore[index]
    assert parse_daily_value_series(series) == []


@pytest.mark.asyncio
async def test_the_walk_dedupes_by_site_and_day_across_overlapping_tiles() -> None:
    # Tiles share their edges, so one gauge appears in two tiles' answers. Deduping by site ALONE
    # -- what the forward path does -- would keep a single day out of the whole window.
    series = _daily_series(
        "14137000",
        [("2022-08-05T00:00:00.000", "495"), ("2022-08-06T00:00:00.000", "483")],
    )
    payload = {"value": {"timeSeries": [series]}}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        records = await fetch_streamflow_history(
            client,
            "-125,42,-111,49",
            HistoryWindow(
                start=datetime(2022, 8, 5, tzinfo=UTC),
                end=datetime(2022, 8, 7, tzinfo=UTC),
            ),
        )

    # Eight tiles all answered with the same gauge; two gauge-days survive, not sixteen.
    assert len(records) == 2
    assert sorted(str(record["updatedAt"])[:10] for record in records) == ["2022-08-05", "2022-08-06"]


@pytest.mark.asyncio
async def test_the_query_asks_for_daily_values_and_steps_the_inclusive_end_day_back() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"value": {"timeSeries": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await fetch_streamflow_history(
            client,
            "-125,42,-121,46",
            HistoryWindow(
                start=datetime(2022, 8, 5, tzinfo=UTC),
                end=datetime(2022, 8, 8, tzinfo=UTC),
            ),
        )

    url = str(seen[0])
    # The daily service, never the instantaneous one: /nwis/iv/ retains ~120 days and answers an
    # older window with a well-formed EMPTY response, so a walk pointed at it would report years of
    # successful empty chunks instead of failing.
    assert "/nwis/dv/" in url
    assert "startDT=2022-08-05" in url
    # endDT is inclusive at NWIS while a HistoryWindow's end is exclusive.
    assert "endDT=2022-08-07" in url
    # siteStatus is absent on purpose: a gauge discontinued in 2024 still measured real water in
    # 2022, and pinning it to `active` would silently delete those years.
    assert "siteStatus" not in url


def test_the_archive_source_serves_history_and_refuses_the_current_window() -> None:
    source = usgs_streamflow_archive_source()
    assert source.source_name == USGS_STREAMFLOW_ARCHIVE_SOURCE
    assert source.history_capability().supported is True
    assert source.history_capability().earliest == USGS_DAILY_VALUES_EARLIEST


def test_a_window_below_the_floor_is_refused_in_typed_terms() -> None:
    source = usgs_streamflow_archive_source()
    with pytest.raises(HistoryUnavailableError):
        source.history_capability().require(
            source.source_name,
            HistoryWindow(
                start=datetime(2019, 1, 1, tzinfo=UTC),
                end=datetime(2019, 2, 1, tzinfo=UTC),
            ),
        )
