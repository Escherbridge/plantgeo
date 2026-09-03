"""The pinned lattice, the real captured point response, and the bounds one turn's fan-out runs under."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.pipeline.direct.climate.products import CLIMATE_SOURCE_PARAMETERS
from agri_data_service.pipeline.direct.climate.source import (
    ClimateSourceCache,
    ClimateSourceUnsettledError,
    build_climate_day,
    climate_point_url,
    fill_cell_day_cache,
    parse_climate_point_body,
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
from tests.direct.climate.conftest import FETCHED_AT, cell_day_response, filled_cache, product_for

if TYPE_CHECKING:
    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport

#: A byte-identical copy of `.omc/research/nasa-power-point-response-2026-09-02.json`, which is
#: gitignored; the header and the provenance are in the sibling `.md` there.
CAPTURE_PATH: Final = Path(__file__).resolve().parent / "fixtures" / "nasa-power-point-response-2026-09-02.json"
CAPTURE_SHA256: Final = "09d87119b5c156f756601eae92d673c19550ce476a2197b4b9d4eaeac3fd0d50"
CAPTURE_DAY: Final = date(2026, 8, 20)
CAPTURE_LONGITUDE: Final = -119.0
CAPTURE_LATITUDE: Final = 46.0
CAPTURE_AIR_TEMPERATURE_MEAN: Final = 27.35
CAPTURE_PRECIPITATION: Final = 0.0

DAY: Final = date(2026, 8, 20)
PLANE_STREAM: Final = "climate-field-air-temperature-mean"
SHORTWAVE_STREAM: Final = "climate-field-shortwave-radiation"
EXPECTED_PARAMETER_COUNT: Final = 8
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


def test_the_real_captured_point_response_carries_every_product_in_one_request() -> None:
    """One request, eight parameters: the fact the per-cell-day cache and the budget rest on."""
    body = CAPTURE_PATH.read_bytes()
    assert hashlib.sha256(body).hexdigest() == CAPTURE_SHA256, "the capture is a fixture; it must not be edited"

    response = parse_climate_point_body(
        capture_cell(),
        day=CAPTURE_DAY,
        body=body,
        request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
        retrieved_at=FETCHED_AT,
    )

    assert set(response.parameters) == set(CLIMATE_SOURCE_PARAMETERS)
    assert len(CLIMATE_SOURCE_PARAMETERS) == EXPECTED_PARAMETER_COUNT
    assert response.parameters["T2M"] == CAPTURE_AIR_TEMPERATURE_MEAN
    assert response.parameters["PRECTOTCORR"] == CAPTURE_PRECIPITATION
    assert response.response_bytes == len(body)


def test_the_captured_solar_value_is_a_fill_and_the_meteorology_values_are_not() -> None:
    """The 75-day solar lag, observed directly: thirteen days back, only ALLSKY_SFC_SW_DWN is filled."""
    response = parse_climate_point_body(
        capture_cell(),
        day=CAPTURE_DAY,
        body=CAPTURE_PATH.read_bytes(),
        request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
        retrieved_at=FETCHED_AT,
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

    with pytest.raises(ClimateSourceUnsettledError, match="budget"):
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
