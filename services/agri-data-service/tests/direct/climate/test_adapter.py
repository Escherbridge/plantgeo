"""What the direct climate adapter publishes, what it governs absent, and what it refuses outright."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.climate.adapter import (
    CLIMATE_DIRECT_KIND,
    DirectClimateFieldAdapter,
    DirectClimateFieldError,
    refuse_immutable_day,
)
from agri_data_service.pipeline.direct.climate.products import (
    CANONICAL_SNAPSHOT_LAST_DAY,
    CLIMATE_DIRECT_SNAPSHOT_PREFIX,
    CLIMATE_FIELD_PRODUCTS,
    CLIMATE_SOURCE_PARAMETERS,
    SHORTWAVE_RADIATION_SNAPSHOT_LAST_DAY,
)
from agri_data_service.pipeline.direct.climate.rows import climate_day_table
from agri_data_service.pipeline.direct.climate.source import (
    ClimateSourceUnsettledError,
    climate_day_from_cache,
    parse_climate_point_body,
)
from agri_data_service.pipeline.direct.climate.support import (
    NASA_POWER_SUPPORT_CELL_COUNT,
    ClimateSupportError,
    build_support,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, unlocked_lane_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from tests.direct.climate.conftest import FETCHED_AT, filled_cache, plan_cells, point_body, product_for
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.climate.source import ClimateDaySource
    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport

DAY = date(2026, 8, 20)
PLANE_STREAM = "climate-field-air-temperature-mean"
LINEAGE_STREAM = "climate-field-precipitation"
#: The third row shape, and the reason the row builders are a table rather than a ternary: the
#: soil-wetness breakdown froze nineteen columns with their own selection vocabulary.
LANE_STREAM = "soil-wetness-surface"
LANE_COLUMN_COUNT = 19
MEAN_TEMPERATURE_VALUE = 9.5
PRECIPITATION_VALUE = 2.25


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
    product: ClimateFieldProduct,
    support: NasaPowerSupport,
    *,
    day: date = DAY,
    fill_cell_keys: Sequence[str] = (),
    omit_cell_keys: Sequence[str] = (),
) -> ClimateDaySource:
    """Read one product out of a turn cache filled from parsed point responses, as the live fetch does."""
    cache = filled_cache(support, day=day, fill_cell_keys=fill_cell_keys, omit_cell_keys=omit_cell_keys)
    return climate_day_from_cache(product, day=day, support=support, cache=cache)


def adapter_for(product: ClimateFieldProduct, source: ClimateDaySource) -> DirectClimateFieldAdapter:
    """Bind a pre-parsed source into the adapter so no test opens a socket."""

    async def fetch() -> ClimateDaySource:
        return source

    return DirectClimateFieldAdapter(product=product, fetch_source=fetch)


@pytest.mark.parametrize("stream", [PLANE_STREAM, LINEAGE_STREAM, LANE_STREAM])
@pytest.mark.asyncio
async def test_a_settled_day_publishes_every_rung_and_marks_the_base_last(
    stream: str,
    support: NasaPowerSupport,
) -> None:
    """The shared finalizer must produce all four rungs, and the base marker must land after them."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    product = product_for(stream)
    adapter = adapter_for(product, source_for(product, support))

    outcome, parts, rows, _written_bytes, detail = await fill_one_lane_day(
        SessionDouble(),
        store,
        replace(LANE_REGISTRY[stream], adapter=adapter),
        day=DAY,
        run_id="climate-test",
        now=lambda: FETCHED_AT,
        today=date(2026, 8, 26),
        lane_day_lock=unlocked_lane_day,
    )

    assert outcome == "written"
    # The shared finalizer REPORTS the rungs it derived, so a `None` detail here would mean the three
    # coarse rungs were never written -- which is the one thing this test exists to prove they are.
    assert detail is not None
    assert [f"z{tier}" in detail for tier in DERIVED_ZOOM_TIERS] == [True] * len(DERIVED_ZOOM_TIERS), detail
    assert parts == 1
    assert rows == NASA_POWER_SUPPORT_CELL_COUNT
    for tier in ZOOM_TIERS:
        assert store.partition_exists(stream, CLIMATE_DIRECT_KIND, tier, DAY), tier
        assert store.read_completion_marker(stream, CLIMATE_DIRECT_KIND, tier, DAY) is not None, tier
    written = [key for key in backend.objects if f"layer={stream}/" in key]
    base_marker = next(key for key in written if "_complete.json" in key and "zoom=13" in key)
    assert written.index(base_marker) == max(written.index(key) for key in written if "_complete.json" in key), (
        "the base completion marker must be the last claim written for the day"
    )


@pytest.mark.asyncio
async def test_an_all_fill_value_day_becomes_a_governed_absence_carrying_its_receipt(
    support: NasaPowerSupport,
) -> None:
    """A complete answer of fill values is a real absence, and its marker must prove which requests."""
    store = ObjectStore(RecordingBackend())
    product = product_for(PLANE_STREAM)
    source = source_for(product, support, fill_cell_keys=[cell.cell_key for cell in support.cells])

    result = await adapter_for(product, source)(SessionDouble(), store, day=DAY, run_id="absence-run")

    assert result.absence_recorded is True
    assert result.row_count == 0
    absence = store.read_absence(PLANE_STREAM, CLIMATE_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
    assert absence is not None
    assert source.receipt.response_sha256 in absence.upstream_response
    assert source.receipt.request_url_sha256 in absence.upstream_response
    assert f'"request_count": {NASA_POWER_SUPPORT_CELL_COUNT}' in absence.upstream_response
    assert str(NASA_POWER_SUPPORT_CELL_COUNT) in absence.reason


@pytest.mark.asyncio
async def test_a_disproven_absence_is_retracted_inside_the_lock_before_the_first_write(
    support: NasaPowerSupport,
) -> None:
    """POWER revises a fill-value day into real values; a stale absence must not block that."""
    store = ObjectStore(RecordingBackend())
    product = product_for(PLANE_STREAM)
    store.write_absence(
        GovernedAbsence(
            reason="every value was a fill value",
            upstream_response="{}",
            recorded_at=FETCHED_AT,
            run_id="initial-empty",
        ),
        layer=PLANE_STREAM,
        kind=CLIMATE_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=DAY,
    )

    result = await adapter_for(product, source_for(product, support))(
        SessionDouble(), store, day=DAY, run_id="revision-run"
    )

    assert result.row_count == NASA_POWER_SUPPORT_CELL_COUNT
    assert store.absence_exists(PLANE_STREAM, CLIMATE_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is False
    assert store.partition_exists(PLANE_STREAM, CLIMATE_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is True


def test_a_cell_with_no_held_response_is_refused_rather_than_published(support: NasaPowerSupport) -> None:
    """A partial day published once becomes a day nothing ever revisits; refusing keeps it owed."""
    product = product_for(PLANE_STREAM)
    dropped = support.cells[7].cell_key

    with pytest.raises(ClimateSourceUnsettledError, match="holds no response"):
        source_for(product, support, omit_cell_keys=[dropped])


def test_one_fill_cell_beside_real_ones_is_real_data_and_is_counted_not_refused(
    support: NasaPowerSupport,
) -> None:
    """POWER fills a cell its inputs have not reached; refusing would hold the lane behind that cell.

    This deliberately reverses the earlier single-regional-response rule, which refused any mixed
    day. See `pipeline/direct/AGENTS.md`, "Fill cells, absence and refusal".
    """
    product = product_for(PLANE_STREAM)

    source = source_for(product, support, fill_cell_keys=[support.cells[3].cell_key])

    assert len(source.values) == NASA_POWER_SUPPORT_CELL_COUNT - 1
    assert source.fill_value_cells == 1
    assert source.receipt.fill_cell_count == 1
    assert source.receipt.cell_count == NASA_POWER_SUPPORT_CELL_COUNT
    assert source.is_governed_absence is False
    assert support.cells[3].cell_key not in {value.cell.cell_key for value in source.values}


def test_a_response_echoing_a_point_the_cell_did_not_ask_for_is_refused(support: NasaPowerSupport) -> None:
    """A value is never bound to a point that was not requested, however close that point is."""
    cell = support.cells[0]
    neighbour = support.cells[1]
    body = point_body(neighbour, day=DAY)

    with pytest.raises(ClimateSourceUnsettledError, match="was not asked for"):
        parse_climate_point_body(
            cell,
            day=DAY,
            body=body,
            request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
            retrieved_at=FETCHED_AT,
        )


def test_a_response_missing_one_requested_parameter_is_refused(support: NasaPowerSupport) -> None:
    """One response serves eleven streams, so a parameter that did not arrive refuses the whole cell."""
    cell = support.cells[0]
    body = point_body(cell, day=DAY).replace(b'"WS2M"', b'"WS10M"', 1)

    with pytest.raises(ClimateSourceUnsettledError, match="omits WS2M"):
        parse_climate_point_body(
            cell,
            day=DAY,
            body=body,
            request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
            retrieved_at=FETCHED_AT,
        )


@pytest.mark.parametrize(
    ("stream", "last_immutable_day"),
    [
        (PLANE_STREAM, CANONICAL_SNAPSHOT_LAST_DAY),
        ("climate-field-shortwave-radiation", SHORTWAVE_RADIATION_SNAPSHOT_LAST_DAY),
    ],
)
def test_a_day_the_immutable_snapshot_owns_is_refused_per_product(stream: str, last_immutable_day: date) -> None:
    """Each product owns only the days after ITS OWN history; shortwave's ends nine weeks earlier."""
    product = product_for(stream)

    with pytest.raises(DirectClimateFieldError, match="immutable"):
        refuse_immutable_day(product, last_immutable_day)
    with pytest.raises(DirectClimateFieldError, match="immutable"):
        refuse_immutable_day(product, last_immutable_day - timedelta(days=1))
    refuse_immutable_day(product, last_immutable_day + timedelta(days=1))


@pytest.mark.asyncio
async def test_the_adapter_refuses_an_immutable_day_before_it_fetches_anything(
    support: NasaPowerSupport,
) -> None:
    """The guard lives on the adapter so the generic gap-fill driver cannot walk around it."""
    product = product_for(PLANE_STREAM)
    fetched: list[str] = []

    async def fetch() -> ClimateDaySource:
        fetched.append("fetched")
        return source_for(product, support)

    adapter = DirectClimateFieldAdapter(product=product, fetch_source=fetch)

    with pytest.raises(DirectClimateFieldError, match="immutable"):
        await adapter(SessionDouble(), ObjectStore(RecordingBackend()), day=CANONICAL_SNAPSHOT_LAST_DAY, run_id="r")
    assert fetched == []


def test_a_direct_lineage_row_names_its_own_namespace_on_every_lineage_column(
    support: NasaPowerSupport,
) -> None:
    """A direct row was never selected out of PostgreSQL, so its lineage must say which namespace it is in."""
    product = product_for(LINEAGE_STREAM)
    source = source_for(product, support)

    table = climate_day_table(product, day=DAY, values=source.values, receipt=source.receipt)

    assert table.schema == product.stream_schema.arrow_schema
    row = table.to_pylist()[0]
    assert row["source_snapshot_id"].startswith(CLIMATE_DIRECT_SNAPSHOT_PREFIX)
    assert row["source_snapshot_id"].endswith(source.receipt.response_sha256)
    assert row["source_manifest_sha256"] == source.receipt.response_sha256
    assert row["selected_source_row_id"] == row["selected_source_row_ordinal"]
    # The PART is this cell's own request, not the day's aggregate: one row, one response.
    assert row["selected_source_part_key"] == source.values[0].request_url
    assert row["selected_source_part_sha256"] == source.values[0].response_sha256
    assert row["input_source_part_keys"] == [source.values[0].request_url]
    assert row["input_source_row_count"] == 1
    assert row["input_source_row_ids"] == [row["selected_source_row_ordinal"]]
    assert row["input_source_row_sha256s"] == [row["selected_source_row_sha256"]]


def test_two_products_of_one_day_share_a_response_and_differ_only_in_their_parameter(
    support: NasaPowerSupport,
) -> None:
    """One point request carries every parameter, which is what makes the per-day cache correct."""
    cache = filled_cache(
        support,
        day=DAY,
        values={"T2M": MEAN_TEMPERATURE_VALUE, "PRECTOTCORR": PRECIPITATION_VALUE},
    )

    mean = climate_day_from_cache(product_for(PLANE_STREAM), day=DAY, support=support, cache=cache)
    precipitation = climate_day_from_cache(product_for(LINEAGE_STREAM), day=DAY, support=support, cache=cache)

    assert cache.requests_spent == 0, "a pre-filled cache spends nothing; the fan-out is what spends"
    assert mean.receipt.response_sha256 == precipitation.receipt.response_sha256
    assert {value.value for value in mean.values} == {MEAN_TEMPERATURE_VALUE}
    assert {value.value for value in precipitation.values} == {PRECIPITATION_VALUE}


def test_a_plane_row_carries_the_same_twelve_columns_the_history_carries(support: NasaPowerSupport) -> None:
    """A forward day must be readable by whatever already reads the historical plane days."""
    product = product_for(PLANE_STREAM)
    source = source_for(product, support)

    table = climate_day_table(product, day=DAY, values=source.values, receipt=source.receipt)

    assert table.schema == product.stream_schema.arrow_schema
    row = table.to_pylist()[0]
    assert row["support_key"] == "surface"
    assert row["signal_name"] == product.signal_name
    assert row["normalized_unit"] == product.normalized_unit
    assert row["observed_day"] == DAY
    assert row["newest_observed_at"] == datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC)
    assert row["observation_count"] == 1
    assert row["allowed_client_exposure"] is True
    assert table.column("cell_id").null_count == 0


def test_the_support_must_be_the_exact_historical_lattice() -> None:
    """A day written on a different support is not comparable with the history it claims to extend."""
    with pytest.raises(ClimateSupportError, match="not the 397"):
        build_support(plan_cells()[:-1])


def test_the_soil_wetness_lane_shape_carries_its_own_selection_vocabulary(support: NasaPowerSupport) -> None:
    """Nineteen columns, and a `direct:` discriminator on the only column this shape can carry one on.

    `_lineage_row`'s `source_snapshot_id` does not exist here, so a reader that checked only that
    column would read these rows as canonical PostgreSQL selections. They are not: the id is a
    RESPONSE ORDINAL and the release is the request that returned it.
    """
    product = product_for(LANE_STREAM)
    source = source_for(product, support)

    table = climate_day_table(product, day=DAY, values=source.values, receipt=source.receipt)
    row = table.slice(0, 1).to_pylist()[0]

    assert table.num_columns == LANE_COLUMN_COUNT
    assert table.schema == product.stream_schema.arrow_schema
    assert "source_snapshot_id" not in row
    assert row["selected_source_release_id"].startswith(CLIMATE_DIRECT_SNAPSHOT_PREFIX)
    assert row["selected_observation_id"] == source.values[0].response_ordinal
    assert row["signal_name"] == "soil_wetness_surface"
    assert row["normalized_unit"] == "fraction_of_saturation"


def test_every_product_maps_to_exactly_one_power_parameter() -> None:
    """Eleven distinct parameters on one request is what lets one cell-day response serve eleven streams."""
    parameters = [product.source_parameter for product in CLIMATE_FIELD_PRODUCTS]

    assert len(parameters) == len(set(parameters))
    assert len(CLIMATE_FIELD_PRODUCTS) == len({product.stream for product in CLIMATE_FIELD_PRODUCTS})
    assert tuple(sorted(parameters)) == CLIMATE_SOURCE_PARAMETERS
