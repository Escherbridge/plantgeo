"""The UTC edge gates full support without manufacturing dates, absences or quota evidence."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agri_data_service.ingest.http import BoundedResponse, UpstreamError
from agri_data_service.pipeline.direct.climate import edge, forward
from agri_data_service.pipeline.direct.climate.source import ClimateSourceCache, climate_day_from_cache
from agri_data_service.pipeline.direct.climate.support import NASA_POWER_SUPPORT_CELL_COUNT
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.runner.contract import ProbeWindow, ProviderEdge
from tests.direct.climate.conftest import filled_cache, product_for
from tests.direct.climate.test_forward_command import SessionDouble, always_granted, bounded_config
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from agri_data_service.ingest.http import UpstreamBounds
    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.climate.source import ClimateDaySource
    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport

PRODUCT = product_for("climate-field-shortwave-radiation")
TODAY = date(2026, 10, 2)
UTC_EDGE = date(2026, 6, 30)
FIRST_DAY = PRODUCT.history_floor
LAST_DAY = forward.settled_through(PRODUCT, today=TODAY)
TURN_COUNT = 3


def probe_body(
    url: str, *, valued_through: date | None, time_standard: str = "UTC", valued_from: date | None = None
) -> str:
    """Echo the actual requested support point and every daily value in its UTC window."""
    parameters = httpx.URL(url).params
    first = date.fromisoformat(parameters["start"])
    last = date.fromisoformat(parameters["end"])
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    return json.dumps(
        {
            "geometry": {"coordinates": [float(parameters["longitude"]), float(parameters["latitude"]), 0]},
            "header": {"time_standard": time_standard},
            "properties": {
                "parameter": {
                    PRODUCT.source_parameter: {
                        day.strftime("%Y%m%d"): (
                            12.5
                            if valued_through is not None
                            and day <= valued_through
                            and (valued_from is None or day >= valued_from)
                            else -999
                        )
                        for day in days
                    }
                }
            },
        }
    )


def install_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    valued_through: date | None = UTC_EDGE,
    status: int = 200,
    valued_from: date | None = None,
) -> list[str]:
    """Fake only the HTTP boundary so probe URL construction and source parsing stay real."""
    calls: list[str] = []

    async def fetch(_client: httpx.AsyncClient, url: str, _bounds: UpstreamBounds) -> BoundedResponse:
        calls.append(url)
        return BoundedResponse(
            status, "application/json", probe_body(url, valued_through=valued_through, valued_from=valued_from), None
        )

    monkeypatch.setattr(edge, "fetch_bounded", fetch)
    return calls


async def measured_probe(support: NasaPowerSupport, cache: ClimateSourceCache) -> ProviderEdge:
    """Measure the complete owed scan window under a bounded clock."""
    return await edge.probe_shortwave_edge(
        PRODUCT,
        support=support,
        cache=cache,
        first_day=FIRST_DAY,
        last_day=LAST_DAY,
        deadline=time.monotonic() + 60,
    )


@pytest.mark.asyncio
async def test_three_utc_probes_find_an_edge_ninety_days_behind_the_calendar(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_probe(monkeypatch)
    cache = ClimateSourceCache(request_budget=edge.SHORTWAVE_PROBE_REQUESTS)

    probe = await measured_probe(support, cache)

    assert probe.status == "ok"
    assert probe.edge == UTC_EDGE
    assert len(calls) == cache.requests_spent == edge.SHORTWAVE_PROBE_REQUESTS
    assert len(set(calls)) == edge.SHORTWAVE_PROBE_REQUESTS
    for url in calls:
        parameters = httpx.URL(url).params
        assert parameters["time-standard"] == "UTC"
        assert parameters["parameters"] == PRODUCT.source_parameter
    assert max(probe.valued_days) < LAST_DAY


@pytest.mark.asyncio
async def test_fresh_turns_drain_oldest_eligible_days_as_calendar_days_and_the_probe_edge_advance(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ObjectStore(RecordingBackend())
    fetched: list[date] = []

    async def fetch(
        product: ClimateFieldProduct,
        *,
        day: date,
        support: NasaPowerSupport,
        cache: ClimateSourceCache,
        **_kwargs: object,
    ) -> ClimateDaySource:
        fetched.append(day)
        cache.requests_spent += NASA_POWER_SUPPORT_CELL_COUNT
        return climate_day_from_cache(product, day=day, support=support, cache=filled_cache(support, day=day))

    monkeypatch.setattr(forward, "fetch_climate_day", fetch)
    monkeypatch.setattr(forward, "postgres_lane_day_lock", always_granted)
    for turn in range(TURN_COUNT):
        measured_edge = UTC_EDGE + timedelta(days=turn)
        probes = install_probe(monkeypatch, valued_through=measured_edge)
        config = bounded_config(product_id="shortwave-radiation")
        cache = ClimateSourceCache(request_budget=config.request_budget)
        report = await forward._publish_product(
            SessionDouble(),
            store,
            PRODUCT,
            support=support,
            cache=cache,
            today=TODAY + timedelta(days=turn),
            run_id=f"fresh-solar-process-{turn}",
            config=config,
            deadline=time.monotonic() + 60,
            availability_storage=None,
            availability=AvailabilityExtensionTally(),
        )
        expected_day = FIRST_DAY + timedelta(days=turn)
        assert fetched[-1] == expected_day
        assert report["outcome"] == "published"
        assert report["probe_edge"] == measured_edge.isoformat()
        assert report["probe_status"] == "ok"
        assert report["probe_gated_days"] == (LAST_DAY - UTC_EDGE).days
        assert len(probes) == report["probe_requests_spent"] == edge.SHORTWAVE_PROBE_REQUESTS
        assert httpx.URL(probes[0]).params["end"] == (LAST_DAY + timedelta(days=turn)).strftime("%Y%m%d")
        assert cache.requests_spent == NASA_POWER_SUPPORT_CELL_COUNT + edge.SHORTWAVE_PROBE_REQUESTS
        assert cache.requests_spent <= cache.request_budget
        assert all(status == "data" for status in forward._tier_status_day(store, PRODUCT, expected_day).values())
    assert fetched == [FIRST_DAY + timedelta(days=offset) for offset in range(TURN_COUNT)]


@pytest.mark.asyncio
async def test_a_fresh_lane_publishes_a_valued_anchor_before_draining_two_leading_fill_days(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh processes make progress without repeatedly spending both fan-outs on leading nulls."""
    leading_fill_days = (FIRST_DAY, FIRST_DAY + timedelta(days=1))
    first_valued_day = leading_fill_days[-1] + timedelta(days=1)
    expected_turns = (
        (UTC_EDGE, "written", "data"),
        (leading_fill_days[0], "absent", "absent"),
        (leading_fill_days[1], "absent", "absent"),
        (first_valued_day, "written", "data"),
    )
    store = ObjectStore(RecordingBackend())
    fetched: list[date] = []

    async def fetch(
        product: ClimateFieldProduct,
        *,
        day: date,
        support: NasaPowerSupport,
        cache: ClimateSourceCache,
        **_kwargs: object,
    ) -> ClimateDaySource:
        fetched.append(day)
        cache.requests_spent += NASA_POWER_SUPPORT_CELL_COUNT
        fills = [cell.cell_key for cell in support.cells] if day in leading_fill_days else []
        source_cache = filled_cache(support, day=day, fill_cell_keys=fills)
        return climate_day_from_cache(product, day=day, support=support, cache=source_cache)

    monkeypatch.setattr(forward, "fetch_climate_day", fetch)
    monkeypatch.setattr(forward, "postgres_lane_day_lock", always_granted)
    for turn, (expected_day, expected_outcome, expected_state) in enumerate(expected_turns):
        probes = install_probe(monkeypatch, valued_from=first_valued_day)
        config = bounded_config(product_id="shortwave-radiation")
        cache = ClimateSourceCache(request_budget=config.request_budget)
        report = await forward._publish_product(
            SessionDouble(),
            store,
            PRODUCT,
            support=support,
            cache=cache,
            today=TODAY + timedelta(days=turn),
            run_id=f"fresh-anchor-process-{turn}",
            config=config,
            deadline=time.monotonic() + 60,
            availability_storage=None,
            availability=AvailabilityExtensionTally(),
        )
        assert report["outcome"] == "published"
        days = report["days"]
        assert isinstance(days, list)
        assert [(item["day"], item["outcome"]) for item in days] == [(expected_day.isoformat(), expected_outcome)]
        assert fetched[-1] == expected_day
        assert report["probe_edge"] == UTC_EDGE.isoformat()
        assert httpx.URL(probes[0]).params["end"] == (LAST_DAY + timedelta(days=turn)).strftime("%Y%m%d")
        assert cache.requests_spent == NASA_POWER_SUPPORT_CELL_COUNT + edge.SHORTWAVE_PROBE_REQUESTS
        assert cache.requests_spent < cache.request_budget
        assert all(value == expected_state for value in forward._tier_status_day(store, PRODUCT, expected_day).values())
        if turn == 0:
            for missing_day in leading_fill_days:
                assert all(
                    value == "missing" for value in forward._tier_status_day(store, PRODUCT, missing_day).values()
                ), "a probe value authorizes no absence without the later full-support publication"
    assert fetched == [expected_day for expected_day, _outcome, _state in expected_turns]


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected_status"), [(200, "ok"), (429, "deferred"), (503, "unavailable")])
async def test_null_or_unavailable_probe_never_fans_out_or_writes_an_absence(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch, status: int, expected_status: str
) -> None:
    calls = install_probe(monkeypatch, valued_through=None, status=status)
    fetch = AsyncMock(side_effect=AssertionError("a probe-gated day must not fan out"))
    monkeypatch.setattr(forward, "fetch_climate_day", fetch)
    backend = RecordingBackend()
    store = ObjectStore(backend)
    cache = ClimateSourceCache(request_budget=bounded_config().request_budget)

    report = await forward._publish_product(
        SessionDouble(),
        store,
        PRODUCT,
        support=support,
        cache=cache,
        today=TODAY,
        run_id="gated-solar-process",
        config=bounded_config(product_id="shortwave-radiation"),
        deadline=time.monotonic() + 60,
        availability_storage=None,
        availability=AvailabilityExtensionTally(),
    )

    fetch.assert_not_awaited()
    assert report["days"] == []
    assert report["outcome"] == "source_unsettled"
    assert report["probe_status"] == expected_status
    assert report["probe_edge"] is None
    assert report["probe_gated_days"] == report["backlog_days"]
    assert cache.requests_spent == len(calls)
    assert cache.requests_spent <= edge.SHORTWAVE_PROBE_REQUESTS
    assert (cache.deferred_refusal is not None) == (status == HTTPStatus.TOO_MANY_REQUESTS)
    assert all(value == "missing" for value in forward._tier_status_day(store, PRODUCT, LAST_DAY).values())


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["lst", "wrong_point", "nonfinite_point", "missing_day", "bad_json", "transport"])
async def test_untrustworthy_probe_does_not_authorize_any_day(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    async def fetch(_client: httpx.AsyncClient, url: str, _bounds: UpstreamBounds) -> BoundedResponse:
        if fault == "transport":
            raise UpstreamError("probe offline")
        body = probe_body(url, valued_through=UTC_EDGE, time_standard="LST" if fault == "lst" else "UTC")
        decoded = json.loads(body)
        if fault == "wrong_point":
            decoded["geometry"]["coordinates"][0] += 1
        if fault == "nonfinite_point":
            decoded["geometry"]["coordinates"][0] = float("inf")
        if fault == "missing_day":
            del decoded["properties"]["parameter"][PRODUCT.source_parameter][LAST_DAY.strftime("%Y%m%d")]
        return BoundedResponse(200, "application/json", "{" if fault == "bad_json" else json.dumps(decoded), None)

    monkeypatch.setattr(edge, "fetch_bounded", fetch)
    cache = ClimateSourceCache(request_budget=edge.SHORTWAVE_PROBE_REQUESTS)
    probe = await measured_probe(support, cache)

    assert probe.status == ("unavailable" if fault == "transport" else "invalid")
    assert probe.edge is None
    assert not probe.valued_days
    assert cache.requests_spent == 1
    assert cache.deferred_refusal is None


@pytest.mark.asyncio
async def test_probe_budget_and_deadline_stop_before_opening_a_request(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_probe(monkeypatch)
    cache = ClimateSourceCache(request_budget=edge.SHORTWAVE_PROBE_REQUESTS - 1)
    probe = await measured_probe(support, cache)
    assert probe.status == "unavailable"
    assert probe.detail == "request_budget_exhausted"
    cache = ClimateSourceCache(request_budget=edge.SHORTWAVE_PROBE_REQUESTS)
    expired = await edge.probe_shortwave_edge(
        PRODUCT, support=support, cache=cache, first_day=FIRST_DAY, last_day=LAST_DAY, deadline=time.monotonic() - 1
    )
    assert expired.detail == "time_budget_exhausted"
    assert calls == []
    assert cache.requests_spent == 0


def test_measured_edge_cannot_turn_a_probe_value_into_an_absence_proof() -> None:
    statuses: Any = {tier: {FIRST_DAY: "missing", UTC_EDGE: "missing"} for tier in forward.CLIMATE_DIRECT_ALL_TIERS}
    probe = ProviderEdge("ok", ProbeWindow(FIRST_DAY, LAST_DAY), frozenset({UTC_EDGE}))

    assert forward._shortwave_pending_days((UTC_EDGE, FIRST_DAY), statuses, probe) == (UTC_EDGE, FIRST_DAY)
    assert forward._mirrored_past_day(statuses, FIRST_DAY) is None
