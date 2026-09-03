"""What the direct soil adapter publishes, what it governs absent, and what it refuses outright."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.soil.adapter import (
    SOIL_DIRECT_KIND,
    DirectSoilFieldAdapter,
    DirectSoilFieldError,
    refuse_immutable_day,
)
from agri_data_service.pipeline.direct.soil.products import (
    ERA5_LAND_SNAPSHOT_LAST_DAY,
    ERA5_LAND_SOURCE_KEY,
    ERA5_LAND_SUPPORT_KEY,
    SOIL_DIRECT_SNAPSHOT_PREFIX,
    SOIL_FIELD_PRODUCTS,
)
from agri_data_service.pipeline.direct.soil.rows import SoilRowError, soil_day_table
from agri_data_service.pipeline.direct.soil.source import soil_day_from_cache
from agri_data_service.pipeline.direct.soil.support import ERA5_LAND_VALUE_CELL_COUNT
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, unlocked_lane_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from tests.direct.soil.conftest import FETCHED_AT, filled_cache, product_for
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct
    from agri_data_service.pipeline.direct.soil.source import Era5LandChunk, SoilDaySource
    from agri_data_service.pipeline.direct.soil.support import Era5LandSupport

DAY = date(2026, 8, 20)
TODAY = date(2026, 9, 2)
MOISTURE_STREAM = "soil-field-moisture-7-28cm"
TEMPERATURE_STREAM = "soil-temperature-0-to-7cm"
VPD_STREAM = "soil-field-vpd"
PLANE_COLUMN_COUNT = 12
LANE_COLUMN_COUNT = 21
LINEAGE_COLUMN_COUNT = 33


class SessionDouble:
    """Answers the statement-timeout pin and counts rollbacks; executes no real SQL."""

    def __init__(self) -> None:
        self.rollbacks = 0
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        self.statements.append(str(statement))
        self.bound = params

    async def rollback(self) -> None:
        self.rollbacks += 1


def source_for(
    product: SoilFieldProduct,
    support: Era5LandSupport,
    chunks: Sequence[Era5LandChunk],
    *,
    day: date = DAY,
    null_cell_keys: Sequence[str] | None = None,
) -> SoilDaySource:
    """Read one product out of a turn cache filled from parsed chunk responses, as the live fetch does."""
    cache = filled_cache(support, chunks, day=day, null_cell_keys=null_cell_keys)
    return soil_day_from_cache(product, day=day, support=support, chunks=chunks, cache=cache)


def adapter_for(product: SoilFieldProduct, source: SoilDaySource) -> DirectSoilFieldAdapter:
    """Bind a pre-parsed source into the adapter so no test opens a socket."""

    async def fetch() -> SoilDaySource:
        return source

    return DirectSoilFieldAdapter(product=product, fetch_source=fetch)


@pytest.mark.parametrize("stream", [MOISTURE_STREAM, TEMPERATURE_STREAM, VPD_STREAM])
@pytest.mark.asyncio
async def test_a_settled_day_publishes_every_rung_in_all_three_row_shapes(
    stream: str,
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """The shared finalizer must produce all four rungs for each of the three frozen column sets."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    product = product_for(stream)
    adapter = adapter_for(product, source_for(product, support, chunks))

    outcome, parts, rows, _written_bytes, detail = await fill_one_lane_day(
        SessionDouble(),
        store,
        replace(LANE_REGISTRY[stream], adapter=adapter),
        day=DAY,
        run_id="soil-test",
        now=lambda: FETCHED_AT,
        today=TODAY,
        lane_day_lock=unlocked_lane_day,
    )

    assert outcome == "written"
    # The shared finalizer REPORTS the rungs it derived, so a `None` detail here would mean the three
    # coarse rungs were never written -- which is the one thing this test exists to prove they are.
    assert detail is not None
    assert [f"z{tier}" in detail for tier in DERIVED_ZOOM_TIERS] == [True] * len(DERIVED_ZOOM_TIERS), detail
    assert parts == 1
    assert rows == ERA5_LAND_VALUE_CELL_COUNT
    for tier in ZOOM_TIERS:
        assert store.partition_exists(stream, SOIL_DIRECT_KIND, tier, DAY), tier
        assert store.read_completion_marker(stream, SOIL_DIRECT_KIND, tier, DAY) is not None, tier


@pytest.mark.parametrize(
    ("stream", "column_count"),
    [
        (VPD_STREAM, PLANE_COLUMN_COUNT),
        (TEMPERATURE_STREAM, LANE_COLUMN_COUNT),
        (MOISTURE_STREAM, LINEAGE_COLUMN_COUNT),
    ],
)
def test_each_row_shape_conforms_to_the_column_set_its_snapshot_breakdown_froze(
    stream: str,
    column_count: int,
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """Three shapes, three breakdowns; a forward row that changed one would not merge with its history."""
    product = product_for(stream)
    source = source_for(product, support, chunks)

    table = soil_day_table(product, day=DAY, values=source.values, receipt=source.receipt)

    assert table.num_rows == ERA5_LAND_VALUE_CELL_COUNT
    assert table.num_columns == column_count
    assert table.schema == product.stream_schema.arrow_schema
    row = table.slice(0, 1).to_pylist()[0]
    assert row["support_key"] == ERA5_LAND_SUPPORT_KEY
    assert row["signal_name"] == product.signal_name
    assert row["normalized_unit"] == product.normalized_unit


def test_the_direct_namespace_discriminator_rides_a_column_each_lineage_shape_actually_has(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """A reader joining these ids to `agri.signal_observation` must be able to see they are not its rows."""
    moisture = product_for(MOISTURE_STREAM)
    temperature = product_for(TEMPERATURE_STREAM)
    moisture_source = source_for(moisture, support, chunks)
    temperature_source = source_for(temperature, support, chunks)

    lineage = (
        soil_day_table(moisture, day=DAY, values=moisture_source.values, receipt=moisture_source.receipt)
        .slice(0, 1)
        .to_pylist()[0]
    )
    lane = (
        soil_day_table(temperature, day=DAY, values=temperature_source.values, receipt=temperature_source.receipt)
        .slice(0, 1)
        .to_pylist()[0]
    )

    assert lineage["source_snapshot_id"].startswith(SOIL_DIRECT_SNAPSHOT_PREFIX)
    assert lineage["source_key"] == ERA5_LAND_SOURCE_KEY
    assert lane["selected_source_release_id"].startswith(SOIL_DIRECT_SNAPSHOT_PREFIX)
    assert lane["data_source_key"] == ERA5_LAND_SOURCE_KEY
    assert lane["source_parameter"] == temperature.source_parameter


@pytest.mark.asyncio
async def test_an_all_null_day_becomes_a_governed_absence_carrying_its_receipt(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """A complete answer of nulls is a real absence, and its marker must prove which requests."""
    store = ObjectStore(RecordingBackend())
    product = product_for(VPD_STREAM)
    source = source_for(product, support, chunks, null_cell_keys=[cell.cell_key for cell in support.cells])

    result = await adapter_for(product, source)(SessionDouble(), store, day=DAY, run_id="absence-run")

    assert result.absence_recorded is True
    assert result.row_count == 0
    absence = store.read_absence(VPD_STREAM, SOIL_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
    assert absence is not None
    assert source.receipt.response_sha256 in absence.upstream_response
    assert source.receipt.request_url_sha256 in absence.upstream_response
    assert product.source_parameter in absence.reason


@pytest.mark.asyncio
async def test_a_disproven_absence_is_retracted_at_every_tier_before_the_first_write(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
) -> None:
    """The archive backfills a day it first answered null for; a stale absence must not block that."""
    store = ObjectStore(RecordingBackend())
    product = product_for(VPD_STREAM)
    for tier in ZOOM_TIERS:
        store.write_absence(
            GovernedAbsence(
                reason="every value was null",
                upstream_response="{}",
                recorded_at=FETCHED_AT,
                run_id="initial-empty",
            ),
            layer=VPD_STREAM,
            kind=SOIL_DIRECT_KIND,
            zoom=tier,
            day=DAY,
        )

    result = await adapter_for(product, source_for(product, support, chunks))(
        SessionDouble(), store, day=DAY, run_id="revision-run"
    )

    assert result.absence_recorded is False
    assert result.row_count == ERA5_LAND_VALUE_CELL_COUNT
    for tier in ZOOM_TIERS:
        assert store.absence_exists(VPD_STREAM, SOIL_DIRECT_KIND, tier, DAY) is False, tier


@pytest.mark.parametrize("product", SOIL_FIELD_PRODUCTS, ids=lambda product: product.stream)
def test_every_immutable_day_is_refused_by_the_adapter_guard(product: SoilFieldProduct) -> None:
    """The generic gap-fill driver can reach a registered lane, so the ceiling cannot live on the CLI."""
    with pytest.raises(DirectSoilFieldError, match="immutable"):
        refuse_immutable_day(product, ERA5_LAND_SNAPSHOT_LAST_DAY)


def test_a_valueless_day_never_builds_a_zero_row_table() -> None:
    """A zero-row partition reads as a published day; a day with no values is an absence instead."""
    with pytest.raises(SoilRowError, match="governed absence"):
        soil_day_table(product_for(VPD_STREAM), day=DAY, values=(), receipt=None)  # type: ignore[arg-type]
