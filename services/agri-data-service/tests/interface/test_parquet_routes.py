"""The routes mount where the client looks, and every warehouse state leaves as HTTP 200.

A non-2xx from this plane is a transport or serving fault and never a statement about content -- the
client's `MetricAtDateAvailability.request_failed` exists precisely so the two cannot be confused.
"""

from __future__ import annotations

import json as json_module
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import duckdb
import pytest
from sanic import Sanic

from agri_data_service import app as app_module
from agri_data_service.interface.http import parquet_routes
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.coverage import CensusLane
from tests.contract.wire_contract import WIRE_BASE_PATH, WIRE_ROUTES, WireCoverage, WireWindow
from tests.parquet_ops.fakes import FakeListing, FakeRowReader, instant

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sanic.response import HTTPResponse

    from agri_data_service.parquet_ops.faults import ServingRefusalError

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_CONFLICT = 409
HTTP_SERVICE_UNAVAILABLE = 503

#: The rung `serving_zoom_tier` resolves a z11 request down to.
TIER_BELOW_Z11 = 9


def request_with(**query: str) -> Any:
    """A minimal stand-in for `sanic.Request`: the routes read `args` and nothing else."""
    return SimpleNamespace(args=query)


def payload_of(response: HTTPResponse) -> dict[str, object]:
    """Decode a route's JSON body."""
    body = response.body
    assert body is not None
    return json_module.loads(body)


@pytest.fixture
def warehouse(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeListing, FakeRowReader]:
    """Point the routes at an in-memory warehouse: no bucket, no DuckDB, no credentials."""
    listing = FakeListing()
    reader = FakeRowReader()
    monkeypatch.setattr(parquet_routes, "open_listing", lambda: listing)

    async def fake_run_row_read(
        work: Callable[[FakeRowReader], dict[str, object]],
        *,
        route: str,
    ) -> dict[str, object]:
        del route
        return work(reader)

    monkeypatch.setattr(parquet_routes, "_run_row_read", fake_run_row_read)
    parquet_routes._coverage_cache.clear()
    return (listing, reader)


def test_the_four_routes_mount_where_the_frozen_contract_says_they_do(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client builds `${base}/${route}`; a route that moved is an outage nothing else catches."""
    previous_test_mode = Sanic.test_mode
    Sanic.test_mode = True
    try:
        monkeypatch.setattr(app_module.settings, "service_profile", "combined_local")
        monkeypatch.setattr(
            app_module.settings,
            "database_url",
            "postgresql+asyncpg://owner:password@database.internal:5432/plantgeo",
        )
        paths = {f"/{route.path.strip('/')}" for route in app_module.create_app().router.routes}
    finally:
        Sanic._app_registry.pop("agri-data-service", None)
        Sanic.test_mode = previous_test_mode

    for route in WIRE_ROUTES.values():
        assert f"{WIRE_BASE_PATH}/{route}" in paths


def test_the_write_ingress_profile_does_not_mount_the_read_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    previous_test_mode = Sanic.test_mode
    Sanic.test_mode = True
    try:
        monkeypatch.setattr(app_module.settings, "service_profile", "receiver_writer")
        monkeypatch.setattr(
            app_module.settings,
            "receiver_writer_database_url",
            "postgresql+asyncpg://receiver:password@database.internal:5432/plantgeo",
        )
        paths = {f"/{route.path.strip('/')}" for route in app_module.create_app().router.routes}
    finally:
        Sanic._app_registry.pop("agri-data-service", None)
        Sanic.test_mode = previous_test_mode

    assert not any(path.startswith(WIRE_BASE_PATH) for path in paths)


@pytest.mark.asyncio
async def test_a_written_day_answers_200_with_its_rows(warehouse: tuple[FakeListing, FakeRowReader]) -> None:
    listing, reader = warehouse
    part = listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    reader.rows_by_key[part] = ({"cell_id": "4127", "normalized_value": 0.412},)

    response = await parquet_routes.read_day(request_with(layer="signal", kind="observed", zoom="13", day="2026-08-06"))

    assert response.status == HTTP_OK
    assert payload_of(response)["state"] == "published"


@pytest.mark.asyncio
async def test_every_absent_state_is_still_an_http_200(warehouse: tuple[FakeListing, FakeRowReader]) -> None:
    """A 404 here would turn 'nothing was ingested' and 'the service is down' into the same answer."""
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    listing.write_absence(
        "signal",
        "observed",
        13,
        date(2026, 8, 8),
        reason="upstream published no scenes for this day",
        upstream_response="HTTP 200, features: []",
        recorded_at=instant("2026-08-09T03:02:11Z"),
        run_id="parquet-drain:1a7d9c22",
    )

    absence = await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-08"))
    gap = await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-11"))
    never = await parquet_routes.read_day(request_with(layer="vegetation", zoom="13", day="2026-08-11"))

    assert [response.status for response in (absence, gap, never)] == [HTTP_OK, HTTP_OK, HTTP_OK]
    assert payload_of(absence)["state"] == "governed_absence"
    assert payload_of(gap)["state"] == "day_not_written"
    assert payload_of(never)["state"] == "lane_never_written"


@pytest.mark.asyncio
async def test_kind_defaults_to_observed_so_the_server_never_has_to_guess(
    warehouse: tuple[FakeListing, FakeRowReader],
) -> None:
    listing, reader = warehouse
    part = listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    reader.rows_by_key[part] = ({"cell_id": "4127"},)

    response = await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-06"))

    assert payload_of(response)["state"] == "published"
    assert reader.reads[0].scope.kind == "observed"


@pytest.mark.asyncio
async def test_a_request_zoom_between_two_rungs_is_served_by_the_rung_below_it(
    warehouse: tuple[FakeListing, FakeRowReader],
) -> None:
    """z11 reads the z9 tier: rounding up would claim a resolution the writer never generalised to."""
    listing, reader = warehouse
    part = listing.write_day("signal", "observed", 9, date(2026, 8, 6))
    reader.rows_by_key[part] = ({"cell_id": "4127"},)

    response = await parquet_routes.read_day(request_with(layer="signal", zoom="11", day="2026-08-06"))

    assert payload_of(response)["state"] == "published"
    assert reader.reads[0].scope.tier == TIER_BELOW_Z11


@pytest.mark.asyncio
async def test_the_window_route_answers_every_day_of_its_range(
    warehouse: tuple[FakeListing, FakeRowReader],
) -> None:
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))

    response = await parquet_routes.read_window(
        request_with(layer="signal", zoom="13", first_day="2026-08-01", last_day="2026-08-05")
    )
    window = WireWindow.model_validate(payload_of(response))

    assert response.status == HTTP_OK
    assert [day.requested_day for day in window.days] == [f"2026-08-0{index}" for index in range(1, 6)]


@pytest.mark.asyncio
async def test_the_release_route_reports_the_release_s_own_day(
    warehouse: tuple[FakeListing, FakeRowReader],
) -> None:
    listing, reader = warehouse
    part = listing.write_day("drought", "observed", 13, date(2026, 8, 18))
    reader.rows_by_key[part] = ({"area_id": 3311, "dm_category": "D2"},)

    response = await parquet_routes.read_release(request_with(layer="drought", zoom="13", as_of="2026-08-24"))
    payload = payload_of(response)

    assert payload["served_day"] == "2026-08-18"
    assert payload["requested_day"] == "2026-08-24"


@pytest.mark.asyncio
async def test_the_coverage_route_answers_the_whole_warehouse_with_no_viewport(
    warehouse: tuple[FakeListing, FakeRowReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    monkeypatch.setattr(
        parquet_routes,
        "registered_census_lanes",
        lambda: (CensusLane(layer="signal", nature="daily_series", kind="observed"),),
    )

    response = await parquet_routes.read_coverage(request_with())
    census = WireCoverage.model_validate(payload_of(response))

    assert response.status == HTTP_OK
    assert census.evaluated_through_day == census.generated_at[:10]
    by_zoom = {lane.zoom: lane for lane in census.lanes}
    assert set(by_zoom) == {0, 5, 9, 13}
    assert by_zoom[13].earliest_day == "2026-08-01"
    assert [(span.from_, span.to) for span in by_zoom[13].published_ranges] == [("2026-08-01", "2026-08-01")]
    assert all(by_zoom[tier].earliest_day is None for tier in (0, 5, 9))


@pytest.mark.asyncio
@pytest.mark.usefixtures("warehouse")
@pytest.mark.parametrize(
    ("query", "because"),
    [
        ({"layer": "signal", "zoom": "13"}, "day is required"),
        ({"layer": "signal", "zoom": "13", "day": "2026-08-06T00:00:00Z"}, "a day carries no time"),
        ({"layer": "signal", "zoom": "09", "day": "2026-08-06"}, "zoom is a plain integer, not a path segment"),
        ({"layer": "signal", "zoom": "23", "day": "2026-08-06"}, "zoom is off the web-map scale"),
        ({"layer": "Signal", "zoom": "13", "day": "2026-08-06"}, "a layer slug is lowercase"),
        ({"layer": "signal", "zoom": "13", "day": "2026-08-06", "bbox": "1,2,3"}, "a bbox has four ordinates"),
        ({"layer": "signal", "zoom": "13", "day": "2026-08-06", "kind": "guess"}, "kind is one of two streams"),
    ],
)
async def test_a_malformed_request_is_400_and_never_one_of_the_four_states(
    query: dict[str, str],
    because: str,
) -> None:
    response = await parquet_routes.read_day(request_with(**query))

    assert response.status == HTTP_BAD_REQUEST, because
    assert "state" not in payload_of(response)


@pytest.mark.asyncio
@pytest.mark.usefixtures("warehouse")
async def test_a_backwards_window_is_refused_before_it_reaches_the_warehouse() -> None:
    response = await parquet_routes.read_window(
        request_with(layer="signal", zoom="13", first_day="2026-08-09", last_day="2026-08-01")
    )

    assert response.status == HTTP_BAD_REQUEST


@pytest.mark.asyncio
@pytest.mark.usefixtures("warehouse")
async def test_a_window_longer_than_the_budget_is_refused_rather_than_silently_narrowed() -> None:
    response = await parquet_routes.read_window(
        request_with(layer="signal", zoom="13", first_day="2026-01-01", last_day="2026-08-01")
    )

    assert response.status == HTTP_BAD_REQUEST


@pytest.mark.asyncio
async def test_a_conflicted_day_is_a_serving_fault_and_not_a_content_claim(
    warehouse: tuple[FakeListing, FakeRowReader],
) -> None:
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    listing.write_absence(
        "signal",
        "observed",
        13,
        date(2026, 8, 6),
        reason="r",
        upstream_response="u",
        recorded_at=instant("2026-08-07T00:00:00Z"),
        run_id="run",
    )

    response = await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-06"))

    assert response.status == HTTP_CONFLICT
    assert "state" not in payload_of(response)


@pytest.mark.asyncio
async def test_a_day_mid_export_is_503_rather_than_a_claimed_gap(
    warehouse: tuple[FakeListing, FakeRowReader],
) -> None:
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 6), complete=False)

    response = await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-06"))

    error = payload_of(response)["error"]
    assert response.status == HTTP_SERVICE_UNAVAILABLE
    assert isinstance(error, dict)
    assert error["code"] == "partition_day_incomplete"


#: `upstream-fault.ts` maps 429 and every `>= 500` onto the transient code the map's `retry: 1`
#: honours. A refusal the client would retry against a process already at its ceiling is the fault
#: amplifying itself, so every fault this plane raises has to sit outside that set.
TRANSIENT_TO_THE_CLIENT = (429, 500, 502, 503, 504)


@pytest.mark.asyncio
async def test_an_over_budget_read_is_a_coded_refusal_the_client_will_not_retry(
    warehouse: tuple[FakeListing, FakeRowReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard tripping is the guard WORKING; escaping as a generic 500 is what made it an outage."""
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    monkeypatch.setattr(parquet_routes, "resolve_day", _raising(duckdb.OutOfMemoryException("out of memory")))

    response = await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-06"))
    error = payload_of(response)["error"]

    assert isinstance(duckdb.OutOfMemoryException("x"), duckdb.Error), "the mapping is on the base class"
    assert response.status not in TRANSIENT_TO_THE_CLIENT
    assert isinstance(error, dict)
    assert error["code"] == "read_over_budget"
    assert "state" not in payload_of(response)


@pytest.mark.asyncio
async def test_an_unforeseen_fault_is_a_coded_refusal_rather_than_a_generic_500(
    warehouse: tuple[FakeListing, FakeRowReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A botocore fault, a listing budget, an unrenderable cell: all of them used to reach Sanic bare."""
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    monkeypatch.setattr(parquet_routes, "resolve_day", _raising(ValueError("row attributed to a key nobody asked for")))

    response = await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-06"))
    error = payload_of(response)["error"]

    assert response.status not in TRANSIENT_TO_THE_CLIENT
    assert isinstance(error, dict)
    assert error["code"] == "serving_fault"
    assert "row attributed" not in str(error["message"]), "a fault's own message is logged, never served"


@pytest.mark.asyncio
async def test_a_read_that_cannot_get_a_slot_is_refused_rather_than_queued(
    warehouse: tuple[FakeListing, FakeRowReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool is the memory ceiling; queueing behind it would wait out the client's own budget."""
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    monkeypatch.setattr(
        parquet_routes,
        "_run_row_read",
        _async_raising(faults.serving_at_capacity(operation="day", concurrent_reads=3)),
    )

    response = await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-06"))
    error = payload_of(response)["error"]

    assert response.status not in TRANSIENT_TO_THE_CLIENT
    assert isinstance(error, dict)
    assert error["code"] == "serving_at_capacity"


def test_the_http_adapter_owns_every_core_refusal_status() -> None:
    """A new core refusal cannot inherit a transport status by accident."""
    expected = {
        "partition_day_conflict",
        "partition_day_incomplete",
        "bbox_unsupported",
        "bbox_columns_absent",
        "absence_marker_unreadable",
        "absence_marker_undecodable",
        "read_timed_out",
        "read_over_budget",
        "serving_at_capacity",
        "serving_fault",
        "object_store_session_unavailable",
        "serving_extension_unavailable",
        "census_budget_exhausted",
    }

    assert set(parquet_routes._REFUSAL_HTTP_STATUS) == expected


def _raising(fault: BaseException) -> Callable[..., object]:
    """A stand-in resolver that raises, so `_answer`'s mapping is what the test observes."""

    def resolve(*_args: object, **_kwargs: object) -> object:
        raise fault

    return resolve


def _async_raising(fault: ServingRefusalError) -> Callable[..., object]:
    """An async core-runner stand-in that returns one typed refusal to the adapter."""

    async def run(*_args: object, **_kwargs: object) -> object:
        raise fault

    return run


@pytest.mark.asyncio
async def test_no_day_bearing_field_of_any_answer_ever_carries_a_timezone(
    warehouse: tuple[FakeListing, FakeRowReader],
) -> None:
    """A `T` or a `Z` in a `*_day` is how 6,279 of 16,743 water-gauge rows once moved a day."""
    listing, _ = warehouse
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))

    responses = [
        await parquet_routes.read_day(request_with(layer="signal", zoom="13", day="2026-08-01")),
        await parquet_routes.read_window(
            request_with(layer="signal", zoom="13", first_day="2026-08-01", last_day="2026-08-03")
        ),
        await parquet_routes.read_release(request_with(layer="signal", zoom="13", as_of="2026-08-03")),
    ]

    for response in responses:
        for key, value in _walk(payload_of(response)):
            if key.endswith("_day") and isinstance(value, str):
                assert len(value) == len("YYYY-MM-DD"), f"{key}={value!r}"
                assert "T" not in value
                assert "Z" not in value


def _walk(node: object, key: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(node, dict):
        for child_key, child in node.items():
            yield from _walk(child, child_key)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child, key)
    else:
        yield key, node
