"""The pinned lattice, the archive document the history was parsed from, and one turn's bounds."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_ARCHIVE_BASE_URL,
    OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL,
)
from agri_data_service.pipeline.direct.soil.products import SOIL_SOURCE_PARAMETERS
from agri_data_service.pipeline.direct.soil.source import (
    ERA5_LAND_CHUNK_CELL_COUNT,
    SoilSourceCache,
    SoilSourceUnsettledError,
    build_soil_day,
    fill_chunk_day_cache,
    parse_soil_chunk_body,
    soil_chunk_url,
)
from agri_data_service.pipeline.direct.soil.support import (
    ERA5_LAND_SUPPORT_CELL_COUNT,
    ERA5_LAND_SUPPORT_CELL_KEY_PREFIX,
    ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES,
    ERA5_LAND_SUPPORT_EAST,
    ERA5_LAND_SUPPORT_NORTH,
    ERA5_LAND_SUPPORT_SOUTH,
    ERA5_LAND_SUPPORT_STEP_DEGREES,
    ERA5_LAND_SUPPORT_WEST,
    ERA5_LAND_VALUE_CELL_COUNT,
    Era5LandSupportCell,
    SoilSupportError,
    quantize_coordinate,
    require_pinned_lattice_cell,
)
from tests.direct.soil.conftest import (
    FETCHED_AT,
    chunk_body,
    chunk_day_response,
    filled_cache,
    masked_cell_keys,
    product_for,
)

if TYPE_CHECKING:
    from agri_data_service.pipeline.direct.soil.source import Era5LandChunk
    from agri_data_service.pipeline.direct.soil.support import Era5LandSupport

DAY: Final = date(2026, 8, 20)
NEXT_DAY: Final = date(2026, 8, 21)
MOISTURE_STREAM: Final = "soil-field-moisture-0-7cm"
TEMPERATURE_STREAM: Final = "soil-temperature-7-to-28cm"
VPD_STREAM: Final = "soil-field-vpd"
EXPECTED_PARAMETER_COUNT: Final = 8
EXPECTED_LONGITUDE_COUNT: Final = 56
EXPECTED_LATITUDE_COUNT: Final = 28
EXPECTED_CHUNK_COUNT: Final = 32


def test_the_pinned_support_is_a_complete_quarter_degree_box_on_half_step_centroids(
    support: Era5LandSupport,
) -> None:
    """56 x 28 = 1,568 and the plan carries 1,568, so this lattice IS its bounding box -- unlike POWER's.

    The centroids sit a half step off the integer degree, which is why an integer-degree guard
    borrowed from the NASA POWER writer would reject every one of them.
    """
    keys = [cell.cell_key for cell in support.cells]
    longitudes = sorted({quantize_coordinate(cell.cell_longitude) for cell in support.cells})
    latitudes = sorted({quantize_coordinate(cell.cell_latitude) for cell in support.cells})

    assert len(keys) == ERA5_LAND_SUPPORT_CELL_COUNT
    assert len(set(keys)) == ERA5_LAND_SUPPORT_CELL_COUNT
    assert keys == sorted(keys)
    assert all(key.startswith(ERA5_LAND_SUPPORT_CELL_KEY_PREFIX) for key in keys)
    assert all(
        (ordinate - ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES) % ERA5_LAND_SUPPORT_STEP_DEGREES == 0
        for ordinate in (*longitudes, *latitudes)
    )
    assert (longitudes[0], longitudes[-1]) == (ERA5_LAND_SUPPORT_WEST, ERA5_LAND_SUPPORT_EAST)
    assert (latitudes[0], latitudes[-1]) == (ERA5_LAND_SUPPORT_SOUTH, ERA5_LAND_SUPPORT_NORTH)
    assert (len(longitudes), len(latitudes)) == (EXPECTED_LONGITUDE_COUNT, EXPECTED_LATITUDE_COUNT)
    assert len(longitudes) * len(latitudes) == ERA5_LAND_SUPPORT_CELL_COUNT


def test_an_integer_degree_cell_is_refused_by_the_lattice_guard() -> None:
    """A re-keyed dimension must not be able to pass itself off as the support the history used."""
    off_step = Era5LandSupportCell(
        cell_id="00000000-0000-4000-8000-000000000002",
        cell_key=f"{ERA5_LAND_SUPPORT_CELL_KEY_PREFIX}46.0000:-119.0000",
        cell_longitude=-119.0,
        cell_latitude=46.0,
        coverage_fraction=1.0,
    )

    with pytest.raises(SoilSupportError, match="off the"):
        require_pinned_lattice_cell(off_step)


def test_a_cell_outside_the_measured_extent_is_refused() -> None:
    """The extent is measured from the reviewed plan, so a cell beyond it is a different lattice."""
    outside = Era5LandSupportCell(
        cell_id="00000000-0000-4000-8000-000000000003",
        cell_key=f"{ERA5_LAND_SUPPORT_CELL_KEY_PREFIX}50.1250:-119.1250",
        cell_longitude=-119.125,
        cell_latitude=50.125,
        coverage_fraction=1.0,
    )

    with pytest.raises(SoilSupportError, match="outside the pinned extent"):
        require_pinned_lattice_cell(outside)


def test_one_chunking_covers_every_support_cell_exactly_once(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """A cell in two chunks would be fetched twice; a cell in none would silently thin every day."""
    covered = [cell.cell_key for chunk in chunks for cell in chunk.cells]

    assert len(chunks) == EXPECTED_CHUNK_COUNT
    assert covered == [cell.cell_key for cell in support.cells]
    assert all(len(chunk.cells) <= ERA5_LAND_CHUNK_CELL_COUNT for chunk in chunks)
    assert [chunk.key for chunk in chunks] == sorted(chunk.key for chunk in chunks)


def test_one_chunk_response_answers_every_product_of_that_day(
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """One request, eight variables: the fact the per-chunk-day cache and the budget rest on."""
    response = chunk_day_response(chunks[0], day=DAY)

    assert len(SOIL_SOURCE_PARAMETERS) == EXPECTED_PARAMETER_COUNT
    assert {parameter for _cell_key, parameter in response.values} == set(SOIL_SOURCE_PARAMETERS)
    assert {cell_key for cell_key, _parameter in response.values} == {cell.cell_key for cell in chunks[0].cells}


def test_a_body_answering_another_day_is_refused_by_the_named_day_rule(
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """The day is the publisher's own ISO prefix; recasting an instant is how a day shifts silently."""
    body = chunk_body(chunks[0], day=DAY)

    with pytest.raises(SoilSourceUnsettledError, match="when 2026-08-21 was asked for"):
        parse_soil_chunk_body(
            chunks[0],
            day=NEXT_DAY,
            body=body,
            request_url=soil_chunk_url(chunks[0], day=NEXT_DAY),
            retrieved_at=FETCHED_AT,
        )


def test_a_complete_day_carries_exactly_the_pinned_value_cell_count(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """1,470 of 1,568 answer on every immutable day; a day with any other count is not comparable."""
    cache = filled_cache(support, chunks, day=DAY)
    responses = [cache.responses[(chunk.key, DAY)] for chunk in chunks]

    source = build_soil_day(product_for(MOISTURE_STREAM), day=DAY, support=support, responses=responses)

    assert len(source.values) == ERA5_LAND_VALUE_CELL_COUNT
    assert source.null_value_cells == ERA5_LAND_SUPPORT_CELL_COUNT - ERA5_LAND_VALUE_CELL_COUNT
    assert source.is_governed_absence is False
    assert [value.support_ordinal for value in source.values] == sorted(
        value.support_ordinal for value in source.values
    )


def test_a_thin_day_is_refused_rather_than_published(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """One extra null cell is a changed land-sea mask, so the day is refused and retried, not thinned."""
    masked = {*masked_cell_keys(support), support.cells[-1].cell_key}
    cache = filled_cache(support, chunks, day=DAY, null_cell_keys=tuple(masked))
    responses = [cache.responses[(chunk.key, DAY)] for chunk in chunks]

    with pytest.raises(SoilSourceUnsettledError, match="value cells, not the"):
        build_soil_day(product_for(VPD_STREAM), day=DAY, support=support, responses=responses)


def test_an_all_null_day_is_a_governed_absence_and_not_a_refusal(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """A day the archive has not mirrored yet answers null everywhere; that is an absence with a receipt."""
    every_key = tuple(cell.cell_key for cell in support.cells)
    cache = filled_cache(support, chunks, day=DAY, null_cell_keys=every_key)
    responses = [cache.responses[(chunk.key, DAY)] for chunk in chunks]

    source = build_soil_day(product_for(TEMPERATURE_STREAM), day=DAY, support=support, responses=responses)

    assert source.is_governed_absence is True
    assert source.values == ()
    assert source.null_value_cells == ERA5_LAND_SUPPORT_CELL_COUNT
    assert source.receipt.as_event()["null_cell_count"] == ERA5_LAND_SUPPORT_CELL_COUNT


def test_the_chunk_url_is_credential_free_and_names_one_day_and_every_variable(
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """This string is written into every row's `selected_source_part_key`; a key in it would persist."""
    url = soil_chunk_url(chunks[0], day=DAY)

    # Either reviewed host: `OPEN_METEO_API_KEY` in the environment moves the HOST to the customer
    # one, and this assertion is about the KEY never reaching the string, not about which host ran.
    assert url.split("?", 1)[0] in {OPEN_METEO_ARCHIVE_BASE_URL, OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL}
    assert "apikey" not in url
    assert "models=era5_land" in url
    assert "start_date=2026-08-20" in url
    assert "end_date=2026-08-20" in url
    assert "timezone=GMT" in url
    for parameter in SOIL_SOURCE_PARAMETERS:
        assert parameter in url


@pytest.mark.asyncio
async def test_a_cache_that_already_holds_the_day_issues_no_request_at_all(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """`--product all` reads eight lane-days out of one fan-out; a re-read must not re-fetch."""
    cache = filled_cache(support, chunks, day=DAY)

    await fill_chunk_day_cache(day=DAY, chunks=chunks, cache=cache)

    assert cache.requests_spent == 0
    assert cache.missing_chunks(chunks, DAY) == ()
    assert cache.can_afford(chunks, DAY) is True


@pytest.mark.asyncio
async def test_a_fan_out_the_budget_cannot_cover_is_refused_before_a_socket_opens(
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """A half-spent budget must refuse the day rather than issue as many requests as it can afford."""
    cache = SoilSourceCache(request_budget=len(chunks) - 1)

    with pytest.raises(SoilSourceUnsettledError, match="budget"):
        await fill_chunk_day_cache(day=DAY, chunks=chunks, cache=cache)

    assert cache.requests_spent == 0


def test_a_partly_held_day_reports_only_the_chunks_it_still_owes(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """A failed request is never held, so a retry re-asks for that chunk and not for the other 31."""
    cache = filled_cache(support, chunks, day=DAY, omit_chunk_keys=(chunks[-1].key,))

    missing = cache.missing_chunks(chunks, DAY)

    assert missing == (chunks[-1],)
    assert cache.can_afford(chunks, DAY) is True


def test_the_day_receipt_is_a_digest_over_every_chunk_response_in_chunk_order(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """One product-day is 32 responses, so its identity must be a digest over all of them."""
    cache = filled_cache(support, chunks, day=DAY)
    responses = [cache.responses[(chunk.key, DAY)] for chunk in chunks]
    expected = hashlib.sha256()
    for response in responses:
        expected.update(response.response_sha256.encode("utf-8"))

    source = build_soil_day(product_for(MOISTURE_STREAM), day=DAY, support=support, responses=responses)

    assert source.receipt.response_sha256 == expected.hexdigest()
    assert source.receipt.request_count == EXPECTED_CHUNK_COUNT
    assert source.receipt.cell_count == ERA5_LAND_SUPPORT_CELL_COUNT
    assert source.receipt.snapshot_id == f"direct:{expected.hexdigest()}"


def test_a_day_missing_one_chunk_is_refused_rather_than_bound_short(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """Binding 31 of 32 chunks would publish a day that silently omits fifty cells."""
    cache = filled_cache(support, chunks, day=DAY, omit_chunk_keys=(chunks[0].key,))
    responses = [cache.responses[(chunk.key, DAY)] for chunk in chunks[1:]]

    with pytest.raises(SoilSourceUnsettledError, match="holds no chunk covering support cell"):
        build_soil_day(product_for(MOISTURE_STREAM), day=DAY, support=support, responses=responses)


def test_quantize_lands_a_half_step_centroid_on_an_exact_key() -> None:
    """Matching is equality on the quantized key; a tolerance window binds a value to the cell next door."""
    assert quantize_coordinate(-124.875) == Decimal("-124.875000")
    assert quantize_coordinate(42.125) == Decimal("42.125000")


def test_the_chunk_count_is_the_ceiling_of_the_lattice_over_the_chunk_width(
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """A floor here would drop the last partial chunk, which is 18 cells of every day."""
    assert len(chunks) == -(-ERA5_LAND_SUPPORT_CELL_COUNT // ERA5_LAND_CHUNK_CELL_COUNT)
    assert len(chunks[-1].cells) == ERA5_LAND_SUPPORT_CELL_COUNT % ERA5_LAND_CHUNK_CELL_COUNT
