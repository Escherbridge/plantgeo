"""The pinned lattice, the real captured point response, and the bounds one turn's fan-out runs under."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final
from unittest.mock import AsyncMock

import httpx
import pytest

from agri_data_service.pipeline.direct.climate import source as climate_source
from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_DEFAULT_TIME_BUDGET_SECONDS,
    CLIMATE_SOURCE_PARAMETERS,
)
from agri_data_service.pipeline.direct.climate.source import (
    NASA_POWER_POINT_CONCURRENCY,
    NASA_POWER_QUOTA_PAUSE_DEADLINE_RESERVE_SECONDS,
    NASA_POWER_QUOTA_PAUSE_LIMIT,
    ClimateProviderDeferredError,
    ClimateSourceCache,
    ClimateSourceUnsettledError,
    build_climate_day,
    climate_point_url,
    fill_cell_day_cache,
    parse_climate_point_body,
    quota_pause_seconds,
)
from agri_data_service.pipeline.direct.climate.support import (
    NASA_POWER_SUPPORT_CELL_COUNT,
    NASA_POWER_SUPPORT_CELL_KEY_PREFIX,
    NASA_POWER_SUPPORT_EAST,
    NASA_POWER_SUPPORT_NORTH,
    NASA_POWER_SUPPORT_SOUTH,
    NASA_POWER_SUPPORT_STEP_DEGREES,
    NASA_POWER_SUPPORT_WEST,
    ClimateSupportError,
    NasaPowerSupportCell,
    quantize_coordinate,
    require_pinned_lattice_cell,
)
from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints
from tests.direct.climate.conftest import FETCHED_AT, cell_day_response, filled_cache, product_for
from tests.parquet.test_availability_index import MemoryAvailabilityStorage

if TYPE_CHECKING:
    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport

FETCH_CELL_DAY: Final = "agri_data_service.pipeline.direct.climate.source._fetch_cell_day"
#: How many 429s in a row make one turn give a day up: one per pause, then the one it will not pause for.
TERMINAL_QUOTA_ANSWERS: Final = NASA_POWER_QUOTA_PAUSE_LIMIT + 1


def quota_answers() -> list[ClimateProviderDeferredError]:
    """Enough consecutive 429 answers to spend every pause a turn has and then be refused."""
    return [ClimateProviderDeferredError("NASA POWER answered 429; provider quota exceeded")] * TERMINAL_QUOTA_ANSWERS


def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every 429 cooldown the fan-out would have slept, and sleep none of it."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(climate_source.asyncio, "sleep", record)
    return slept


async def yield_control() -> None:
    """Let every other ready task run once, without going through the (possibly patched) `asyncio.sleep`."""
    loop = asyncio.get_running_loop()
    resumed: asyncio.Future[None] = loop.create_future()
    loop.call_soon(resumed.set_result, None)
    await resumed


@pytest.mark.asyncio
async def test_a_new_turn_resumes_only_verified_nonfill_responses(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_sleep(monkeypatch)
    storage = MemoryAvailabilityStorage()
    checkpoints = SourceResponseCheckpoints(storage)
    cache = ClimateSourceCache(request_budget=len(support.cells), checkpoints=checkpoints)
    first = replace(
        cell_day_response(support.cells[0], day=DAY),
        request_url=climate_point_url(support.cells[0], day=DAY),
    )
    fetch = AsyncMock(side_effect=[first, *quota_answers()])
    monkeypatch.setattr(FETCH_CELL_DAY, fetch)
    with pytest.raises(ClimateProviderDeferredError):
        await fill_cell_day_cache(day=DAY, support=support, cache=cache, concurrency=1, now=FETCHED_AT)

    resumed = ClimateSourceCache(request_budget=len(support.cells) - 1, checkpoints=SourceResponseCheckpoints(storage))
    remaining = [cell_day_response(cell, day=DAY) for cell in support.cells[1:]]
    resumed_fetch = AsyncMock(side_effect=remaining)
    monkeypatch.setattr(FETCH_CELL_DAY, resumed_fetch)
    await fill_cell_day_cache(
        day=DAY, support=support, cache=resumed, concurrency=1, now=FETCHED_AT + timedelta(days=1)
    )
    assert resumed.requests_spent == len(support.cells) - 1
    assert resumed_fetch.await_count == len(support.cells) - 1
    assert resumed.responses[(first.cell.cell_key, DAY)] == first
    assert resumed.responses[(first.cell.cell_key, DAY)].body == first.body

    changed = replace(support, cells=(replace(support.cells[0], coverage_fraction=0.9), *support.cells[1:]))
    foreign = ClimateSourceCache(request_budget=0, checkpoints=SourceResponseCheckpoints(storage))
    await foreign.restore(changed, DAY, now=FETCHED_AT + timedelta(days=1))
    assert foreign.responses == {}


@pytest.mark.asyncio
async def test_fill_values_are_never_checkpointed(support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch) -> None:
    no_sleep(monkeypatch)
    storage = MemoryAvailabilityStorage()
    cache = ClimateSourceCache(request_budget=len(support.cells), checkpoints=SourceResponseCheckpoints(storage))
    response = cell_day_response(support.cells[0], day=DAY, values=dict.fromkeys(CLIMATE_SOURCE_PARAMETERS))
    fetch = AsyncMock(side_effect=[response, *quota_answers()])
    monkeypatch.setattr(FETCH_CELL_DAY, fetch)
    with pytest.raises(ClimateProviderDeferredError):
        await fill_cell_day_cache(day=DAY, support=support, cache=cache, concurrency=1, now=FETCHED_AT)
    assert storage.objects == {}


#: A byte-identical copy of `.omc/research/nasa-power-point-response-2026-09-02.json`, which is
#: gitignored; the header and the provenance are in the sibling `.md` there.
CAPTURE_PATH: Final = Path(__file__).resolve().parent / "fixtures" / "nasa-power-point-response-2026-09-02.json"
CAPTURE_SHA256: Final = "09d87119b5c156f756601eae92d673c19550ce476a2197b4b9d4eaeac3fd0d50"

#: The same capture with the three soil-wetness series added, CLEARLY SYNTHETIC and labelled as such
#: in its own `_fixture_provenance` block. The real capture was taken with an eight-parameter request
#: months before those depths joined the product table, and POWER returns only what the URL asked
#: for -- so nothing in the tree carries a real eleven-parameter body, and inventing one wholesale
#: would have been a worse fixture than extending the one real response by three series.
SYNTHETIC_PATH: Final = (
    Path(__file__).resolve().parent / "fixtures" / "nasa-power-point-response-soil-wetness-synthetic.json"
)
SYNTHETIC_SHA256: Final = "dfe140f59d94a04404c5626d68401b482b1392248262e786c3e414b52d2c8a5a"
SYNTHETIC_SURFACE_WETNESS: Final = 0.128
CAPTURED_PARAMETERS: Final = (
    "ALLSKY_SFC_SW_DWN",
    "PRECTOTCORR",
    "RH2M",
    "T2M",
    "T2MDEW",
    "T2M_MAX",
    "T2M_MIN",
    "WS2M",
)
CAPTURE_DAY: Final = date(2026, 8, 20)
CAPTURE_LONGITUDE: Final = -119.0
CAPTURE_LATITUDE: Final = 46.0
CAPTURE_AIR_TEMPERATURE_MEAN: Final = 27.35
CAPTURE_PRECIPITATION: Final = 0.0

DAY: Final = date(2026, 8, 20)
PLANE_STREAM: Final = "climate-field-air-temperature-mean"
SHORTWAVE_STREAM: Final = "climate-field-shortwave-radiation"
SOIL_WETNESS_STREAM: Final = "soil-wetness-surface"
EXPECTED_PARAMETER_COUNT: Final = 11
EXPECTED_CAPTURED_PARAMETER_COUNT: Final = 8
EXPECTED_LONGITUDE_COUNT: Final = 22
EXPECTED_LATITUDE_COUNT: Final = 21


def capture_cell() -> NasaPowerSupportCell:
    """The one support cell the live capture was taken at."""
    return NasaPowerSupportCell(
        cell_id="00000000-0000-4000-8000-000000000001",
        cell_key="na-sample:1deg:p046.00:m119.00",
        cell_longitude=CAPTURE_LONGITUDE,
        cell_latitude=CAPTURE_LATITUDE,
        coverage_fraction=1.0,
    )


def test_the_pinned_support_is_a_one_degree_lattice_and_not_a_half_degree_one(
    support: NasaPowerSupport,
) -> None:
    """The `nasa-power-0.5-degree` label names the PRODUCT resolution; the sample is one degree.

    Reading that label as a fact about the sample is what produced a writer that demanded a bijection
    with a 0.5-degree grid and failed every product-day. See `pipeline/direct/AGENTS.md`.
    """
    keys = [cell.cell_key for cell in support.cells]
    longitudes = sorted({quantize_coordinate(cell.cell_longitude) for cell in support.cells})
    latitudes = sorted({quantize_coordinate(cell.cell_latitude) for cell in support.cells})

    assert len(keys) == NASA_POWER_SUPPORT_CELL_COUNT
    assert len(set(keys)) == NASA_POWER_SUPPORT_CELL_COUNT
    assert keys == sorted(keys)
    assert all(key.startswith(NASA_POWER_SUPPORT_CELL_KEY_PREFIX) for key in keys)
    assert all(ordinate % NASA_POWER_SUPPORT_STEP_DEGREES == 0 for ordinate in (*longitudes, *latitudes))
    assert (longitudes[0], longitudes[-1]) == (NASA_POWER_SUPPORT_WEST, NASA_POWER_SUPPORT_EAST)
    assert (latitudes[0], latitudes[-1]) == (NASA_POWER_SUPPORT_SOUTH, NASA_POWER_SUPPORT_NORTH)
    assert (len(longitudes), len(latitudes)) == (EXPECTED_LONGITUDE_COUNT, EXPECTED_LATITUDE_COUNT)
    # 22 x 21 = 462 positions and the plan samples 397 of them, so the lattice is a SUBSET of its own
    # bounding box; nothing may enumerate it from the extent.
    assert len(longitudes) * len(latitudes) > NASA_POWER_SUPPORT_CELL_COUNT


def test_a_half_degree_cell_is_refused_by_the_lattice_guard() -> None:
    """A re-keyed dimension must not be able to pass itself off as the support the history used."""
    off_step = NasaPowerSupportCell(
        cell_id="00000000-0000-4000-8000-000000000002",
        cell_key="na-sample:1deg:p046.50:m119.00",
        cell_longitude=-119.0,
        cell_latitude=46.5,
        coverage_fraction=1.0,
    )

    with pytest.raises(ClimateSupportError, match="off the"):
        require_pinned_lattice_cell(off_step)


def test_a_cell_outside_the_measured_extent_is_refused() -> None:
    """The extent is measured from the plan, so a cell beyond it is a different lattice."""
    outside = NasaPowerSupportCell(
        cell_id="00000000-0000-4000-8000-000000000003",
        cell_key="na-sample:1deg:p046.00:m130.00",
        cell_longitude=-130.0,
        cell_latitude=46.0,
        coverage_fraction=1.0,
    )

    with pytest.raises(ClimateSupportError, match="outside the pinned extent"):
        require_pinned_lattice_cell(outside)


def test_the_real_captured_point_response_carries_every_product_it_was_asked_for() -> None:
    """One request, every requested parameter: the fact the per-cell-day cache and the budget rest on.

    Parsed against the EIGHT parameters that request actually named. Today's product table asks for
    eleven, and POWER returns only what the URL lists, so parsing this body against the current
    default would refuse the only real response in the tree for a reason that says nothing about the
    parser. The eleven-parameter shape is covered by the labelled synthetic fixture below.
    """
    body = CAPTURE_PATH.read_bytes()
    assert hashlib.sha256(body).hexdigest() == CAPTURE_SHA256, "the capture is a fixture; it must not be edited"

    response = parse_climate_point_body(
        capture_cell(),
        day=CAPTURE_DAY,
        body=body,
        request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
        retrieved_at=FETCHED_AT,
        required_parameters=CAPTURED_PARAMETERS,
    )

    assert set(response.parameters) == set(CAPTURED_PARAMETERS)
    assert len(CAPTURED_PARAMETERS) == EXPECTED_CAPTURED_PARAMETER_COUNT
    assert set(CAPTURED_PARAMETERS) < set(CLIMATE_SOURCE_PARAMETERS)
    assert len(CLIMATE_SOURCE_PARAMETERS) == EXPECTED_PARAMETER_COUNT
    assert response.parameters["T2M"] == CAPTURE_AIR_TEMPERATURE_MEAN
    assert response.parameters["PRECTOTCORR"] == CAPTURE_PRECIPITATION
    assert response.response_bytes == len(body)


def test_the_capture_is_refused_against_today_s_eleven_parameter_request() -> None:
    """A body that omits a requested parameter must refuse the cell, whatever else it carries right."""
    with pytest.raises(ClimateSourceUnsettledError, match="omits GWET"):
        parse_climate_point_body(
            capture_cell(),
            day=CAPTURE_DAY,
            body=CAPTURE_PATH.read_bytes(),
            request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
            retrieved_at=FETCHED_AT,
        )


def test_the_synthetic_eleven_parameter_body_serves_the_three_soil_wetness_depths() -> None:
    """The depths ride the same response, so one request really does serve all eleven streams.

    Its values are INVENTED and its provenance block says so; what is asserted here is the shape and
    the routing, never the number.
    """
    body = SYNTHETIC_PATH.read_bytes()
    assert hashlib.sha256(body).hexdigest() == SYNTHETIC_SHA256, "regenerate the fixture, do not edit it"

    response = parse_climate_point_body(
        capture_cell(),
        day=CAPTURE_DAY,
        body=body,
        request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
        retrieved_at=FETCHED_AT,
    )
    surface = build_climate_day(product_for(SOIL_WETNESS_STREAM), day=CAPTURE_DAY, responses=[response])

    assert set(response.parameters) == set(CLIMATE_SOURCE_PARAMETERS)
    assert response.parameters["T2M"] == CAPTURE_AIR_TEMPERATURE_MEAN
    assert surface.is_governed_absence is False
    assert [value.value for value in surface.values] == [SYNTHETIC_SURFACE_WETNESS]


def test_the_captured_solar_value_is_a_fill_and_the_meteorology_values_are_not() -> None:
    """A solar cell that trailed: thirteen days back, only ALLSKY_SFC_SW_DWN was filled in this capture.

    NOT evidence of a 75-day lag, which is what this capture was once read as. Re-probed on
    2026-09-15 the same cell-day reads 23.73, so POWER trailed on this cell and revised it in, while
    its release edge sat 4 days back (`.omc/research/runbook-20260915-shortwave/`). What the capture
    does prove is the shape the writer must handle: a fill beside real meteorology values.
    """
    response = parse_climate_point_body(
        capture_cell(),
        day=CAPTURE_DAY,
        body=CAPTURE_PATH.read_bytes(),
        request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
        retrieved_at=FETCHED_AT,
        required_parameters=CAPTURED_PARAMETERS,
    )

    solar = build_climate_day(product_for(SHORTWAVE_STREAM), day=CAPTURE_DAY, responses=[response])
    mean = build_climate_day(product_for(PLANE_STREAM), day=CAPTURE_DAY, responses=[response])

    assert solar.is_governed_absence is True
    assert solar.receipt.fill_cell_count == 1
    assert mean.is_governed_absence is False
    assert [value.value for value in mean.values] == [CAPTURE_AIR_TEMPERATURE_MEAN]


def test_the_capture_echoes_the_point_that_was_requested_so_matching_needs_no_tolerance() -> None:
    """An integer degree is exactly on POWER's 0.5-degree grid, so the service snaps nothing."""
    neighbour = NasaPowerSupportCell(
        cell_id="00000000-0000-4000-8000-000000000004",
        cell_key="na-sample:1deg:p047.00:m119.00",
        cell_longitude=CAPTURE_LONGITUDE,
        cell_latitude=CAPTURE_LATITUDE + 1,
        coverage_fraction=1.0,
    )

    with pytest.raises(ClimateSourceUnsettledError, match="was not asked for"):
        parse_climate_point_body(
            neighbour,
            day=CAPTURE_DAY,
            body=CAPTURE_PATH.read_bytes(),
            request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
            retrieved_at=FETCHED_AT,
        )


def test_the_point_url_names_one_cell_one_day_and_every_parameter() -> None:
    """The URL is the historical point path, which is what makes a forward day reproduce its semantics."""
    url = climate_point_url(capture_cell(), day=CAPTURE_DAY)

    assert url.startswith("https://power.larc.nasa.gov/api/temporal/daily/point?")
    assert "latitude=46" in url
    assert "longitude=-119" in url
    assert "start=20260820" in url
    assert "end=20260820" in url
    for parameter in CLIMATE_SOURCE_PARAMETERS:
        assert parameter in url


@pytest.mark.asyncio
async def test_a_cache_that_already_holds_the_day_issues_no_request_at_all(
    support: NasaPowerSupport,
) -> None:
    """`--product all` reads eight lane-days out of one fan-out; a re-read must not re-fetch."""
    cache = filled_cache(support, day=DAY)

    await fill_cell_day_cache(day=DAY, support=support, cache=cache)

    assert cache.requests_spent == 0
    assert cache.missing_cells(support, DAY) == ()
    assert cache.can_afford(support, DAY) is True


@pytest.mark.asyncio
async def test_a_fan_out_the_budget_cannot_cover_is_refused_before_a_socket_opens(
    support: NasaPowerSupport,
) -> None:
    """A half-spent budget must refuse the day rather than issue as many requests as it can afford."""
    cache = ClimateSourceCache(request_budget=NASA_POWER_SUPPORT_CELL_COUNT - 1)

    with pytest.raises(ClimateProviderDeferredError, match="budget"):
        await fill_cell_day_cache(day=DAY, support=support, cache=cache)

    assert cache.requests_spent == 0


def test_a_partly_held_day_reports_only_the_cells_it_still_owes(support: NasaPowerSupport) -> None:
    """A failed request is never held, so a retry re-asks for that cell and not for the other 396."""
    cache = ClimateSourceCache(request_budget=NASA_POWER_SUPPORT_CELL_COUNT)
    for cell in support.cells[:-1]:
        cache.hold(cell_day_response(cell, day=DAY))

    missing = cache.missing_cells(support, DAY)

    assert missing == (support.cells[-1],)
    assert cache.can_afford(support, DAY) is True


def test_the_day_receipt_is_a_digest_over_every_cell_response_in_support_order(
    support: NasaPowerSupport,
) -> None:
    """One product-day is 397 responses, so its identity must be a digest over all of them."""
    cache = filled_cache(support, day=DAY)
    responses = [cache.responses[(cell.cell_key, DAY)] for cell in support.cells]
    expected = hashlib.sha256()
    for response in responses:
        expected.update(response.response_sha256.encode("utf-8"))

    source = build_climate_day(product_for(PLANE_STREAM), day=DAY, responses=responses)

    assert source.receipt.response_sha256 == expected.hexdigest()
    assert source.receipt.request_count == NASA_POWER_SUPPORT_CELL_COUNT
    assert source.receipt.cell_count == NASA_POWER_SUPPORT_CELL_COUNT
    assert source.receipt.snapshot_id.endswith(expected.hexdigest())


def test_quantize_lands_an_integer_degree_on_an_exact_key() -> None:
    """Matching is equality on the quantized key; a tolerance window binds a value to the cell next door."""
    assert quantize_coordinate(-119.0) == Decimal("-119.000000")
    assert quantize_coordinate(46.0) == Decimal("46.000000")


@pytest.mark.asyncio
async def test_provider_quota_stops_queued_requests_and_keeps_completed_responses(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused fan-out cannot burn the remaining queue or restart it for another product.

    Refused means POWER kept answering 429 through every pause the turn had. The one response held
    before that stays held, and a second call for the same turn asks nothing: the circuit is open.
    """
    slept = no_sleep(monkeypatch)
    cache = ClimateSourceCache(request_budget=len(support.cells) * 2)
    response = cell_day_response(support.cells[0], day=DAY)
    fetch = AsyncMock(side_effect=[response, *quota_answers()])
    monkeypatch.setattr(FETCH_CELL_DAY, fetch)

    for _call in range(2):
        with pytest.raises(ClimateProviderDeferredError, match="provider quota"):
            await fill_cell_day_cache(day=DAY, support=support, cache=cache, concurrency=1)

    assert fetch.await_count == 1 + TERMINAL_QUOTA_ANSWERS, "one held answer, then every pause spent on one cell"
    assert cache.requests_spent == 1 + TERMINAL_QUOTA_ANSWERS
    assert cache.quota_pauses == NASA_POWER_QUOTA_PAUSE_LIMIT
    assert slept == [quota_pause_seconds(pause) for pause in range(NASA_POWER_QUOTA_PAUSE_LIMIT)]
    assert list(cache.responses.values()) == [response]


@pytest.mark.asyncio
async def test_http_429_pauses_the_fan_out_and_defers_only_once_every_pause_is_spent(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the HTTP boundary, including the bounded response's status classification."""
    slept = no_sleep(monkeypatch)
    requests: list[httpx.Request] = []

    def quota_response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, text="Too Many Requests")

    monkeypatch.setattr(
        "agri_data_service.pipeline.direct.climate.source.upstream_client",
        lambda _bounds: httpx.AsyncClient(transport=httpx.MockTransport(quota_response)),
    )
    cache = ClimateSourceCache(request_budget=len(support.cells))
    with pytest.raises(ClimateProviderDeferredError, match="429"):
        await fill_cell_day_cache(day=DAY, support=support, cache=cache, concurrency=1)
    assert len(requests) == TERMINAL_QUOTA_ANSWERS
    assert cache.requests_spent == TERMINAL_QUOTA_ANSWERS
    assert cache.quota_pauses == NASA_POWER_QUOTA_PAUSE_LIMIT
    assert len(slept) == NASA_POWER_QUOTA_PAUSE_LIMIT
    assert not cache.responses


@pytest.mark.asyncio
async def test_one_429_pauses_the_fan_out_once_and_the_day_still_completes_in_this_turn(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production shape of 2026-09-15/16: the second fan-out of a turn met a 429 partway through.

    Deferring on that first 429 handed the day to "a later turn" that selected a newer day and met
    the same answer, so the shortwave product wrote nothing for 107 days. The day has to finish HERE.
    """
    slept = no_sleep(monkeypatch)
    answers = iter([ClimateProviderDeferredError("NASA POWER answered 429")])

    async def fetch(_client: object, cell: object, *, day: date, now: object) -> object:  # noqa: ARG001
        refusal = next(answers, None)
        if refusal is not None:
            raise refusal
        return cell_day_response(cell, day=day)  # type: ignore[arg-type]

    monkeypatch.setattr(FETCH_CELL_DAY, fetch)
    cache = ClimateSourceCache(request_budget=len(support.cells) + 1)

    await fill_cell_day_cache(day=DAY, support=support, cache=cache, concurrency=1)

    assert len(cache.responses) == NASA_POWER_SUPPORT_CELL_COUNT
    assert cache.requests_spent == NASA_POWER_SUPPORT_CELL_COUNT + 1, "the refused request is charged, and asked again"
    assert cache.quota_pauses == 1
    # The refused worker waits the whole pause; with `asyncio.sleep` recorded rather than slept, the
    # clock never reaches the resume instant, so every later cell also waits out "the remainder" of
    # that same pause. One pause, never a second one.
    assert slept[0] == quota_pause_seconds(0)
    assert max(slept) <= quota_pause_seconds(0)
    assert cache.deferred_refusal is None


@pytest.mark.asyncio
async def test_one_burst_of_429s_across_the_concurrent_workers_is_one_pause(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four workers in flight meet the same 429 within a second; that is one pause, not four."""
    slept = no_sleep(monkeypatch)
    burst = NASA_POWER_POINT_CONCURRENCY
    issued = 0

    async def fetch(_client: object, cell: object, *, day: date, now: object) -> object:  # noqa: ARG001
        nonlocal issued
        issued += 1
        ordinal = issued
        await yield_control()  # every in-flight worker has asked before any of them reads its answer
        if ordinal <= burst:
            raise ClimateProviderDeferredError("NASA POWER answered 429")
        return cell_day_response(cell, day=day)  # type: ignore[arg-type]

    monkeypatch.setattr(FETCH_CELL_DAY, fetch)
    cache = ClimateSourceCache(request_budget=len(support.cells) + burst)

    await fill_cell_day_cache(day=DAY, support=support, cache=cache, concurrency=burst)

    assert len(cache.responses) == NASA_POWER_SUPPORT_CELL_COUNT
    assert cache.quota_pauses == 1, "four refusals within one pause are one pause"
    assert cache.requests_spent == NASA_POWER_SUPPORT_CELL_COUNT + burst
    assert len(slept) >= burst, "each refused worker waits the one shared cooldown out"
    assert all(0 < waited <= quota_pause_seconds(0) for waited in slept), "never longer than the first pause"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget_left_seconds",
    [
        quota_pause_seconds(0) / 2,
        quota_pause_seconds(0) + NASA_POWER_QUOTA_PAUSE_DEADLINE_RESERVE_SECONDS / 2,
    ],
    ids=["shorter-than-the-pause", "pause-fits-but-not-the-reserve"],
)
async def test_a_pause_that_would_outlast_the_turn_deadline_or_its_reserve_is_a_deferral_at_once(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
    budget_left_seconds: float,
) -> None:
    """A cooldown the turn cannot afford is not slept; the day is handed to a later turn without waiting.

    Afford means the pause AND the reserve after it: the products queued behind this one each still
    need a lock, a write and three derivations, and a pause ending at the deadline hands every one
    of them `time_budget_exhausted`.
    """
    slept = no_sleep(monkeypatch)
    fetch = AsyncMock(side_effect=ClimateProviderDeferredError("NASA POWER answered 429"))
    monkeypatch.setattr(FETCH_CELL_DAY, fetch)
    cache = ClimateSourceCache(request_budget=len(support.cells))

    with pytest.raises(ClimateProviderDeferredError, match="429"):
        await fill_cell_day_cache(
            day=DAY,
            support=support,
            cache=cache,
            concurrency=1,
            deadline=time.monotonic() + budget_left_seconds,
        )

    assert fetch.await_count == 1
    assert cache.quota_pauses == 0
    assert slept == []


def test_the_pause_series_fits_inside_one_turn_beside_two_fan_outs() -> None:
    """20 s doubling to 160 s is 300 s of waiting at most, inside a 900 s turn that already spends ~2 min fetching."""
    series = [quota_pause_seconds(pause) for pause in range(NASA_POWER_QUOTA_PAUSE_LIMIT)]

    assert series == [20.0, 40.0, 80.0, 160.0]
    assert sum(series) == 300.0  # noqa: PLR2004 - the whole point is the number
    assert quota_pause_seconds(NASA_POWER_QUOTA_PAUSE_LIMIT) == series[-1], "capped, never past the last step"
    # Two 397-cell fan-outs measured at roughly a minute each in production; allow them two apiece,
    # and the reserve the last pause must still leave for the products queued behind.
    two_fan_outs = 2 * 120.0
    spent = sum(series) + two_fan_outs + NASA_POWER_QUOTA_PAUSE_DEADLINE_RESERVE_SECONDS
    assert spent < CLIMATE_DEFAULT_TIME_BUDGET_SECONDS
