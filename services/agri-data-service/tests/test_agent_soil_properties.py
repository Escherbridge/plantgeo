"""The SoilGrids reader and `soil_properties_at_point`: kill switch, nearest centre, integer contract, labels.

Reads run through the real release resolver over `FakeAgentWarehouse`; only DuckDB rows are scripted.
Rationale: agent/AGENTS.md, "Soil properties (SoilGrids)".
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any, Final

import pytest

from agri_data_service.agent import soil_properties, tools
from agri_data_service.parquet_ops import faults
from agri_data_service.warehouse.schemas.soil_properties import SOIL_PROPERTIES_VALUE_COLUMNS
from tests.agent_fakes import FakeAgentWarehouse

BOISE: Final = (-116.2, 43.6)
DENVER: Final = (-104.99, 39.74)
RELEASE_DAY: Final = date(2020, 6, 2)
TODAY: Final = date(2026, 9, 27)
NEAR_ORIGIN: Final = (-116.205, 43.595)
FAR_ORIGIN: Final = (-116.21, 43.61)
#: Centres 1,404 m, 2,510 m and 858 m from BOISE.
FALLBACK_ORIGIN: Final = (-116.205, 43.61)
BEYOND_FALLBACK_ORIGIN: Final = (-116.205, 43.62)
BEYOND_500_M_ORIGIN: Final = (-116.205, 43.605)
PH_MAPPED: Final = 57
PH_PHYSICAL: Final = 5.7
TIE_DISTANCE_METERS: Final = 100.0
ENABLED: Final = "true"


def _row(origin: tuple[float, float], *, value: float = 100.0, ph: float = PH_MAPPED) -> dict[str, Any]:
    """One z13 row in the lane's own columns; every value column mapped, pH set apart."""
    values = dict.fromkeys(SOIL_PROPERTIES_VALUE_COLUMNS, value)
    ph_columns = [column for column in SOIL_PROPERTIES_VALUE_COLUMNS if column.startswith("phh2o_")]
    values.update(dict.fromkeys(ph_columns, ph))
    return {
        "cell_longitude": origin[0],
        "cell_latitude": origin[1],
        **values,
        "source_release": "soilgrids-v2.0",
        "source_manifest_sha256": "0" * 64,
        "release_day": RELEASE_DAY,
        "distance_meters": 0.0,
    }


def _published(rows: list[dict[str, Any]]) -> FakeAgentWarehouse:
    source = FakeAgentWarehouse()
    source.listing_store.write_day(soil_properties.SOIL_PROPERTIES_LANE, "observed", 13, RELEASE_DAY)
    source.answer("agent_point_lane_rows", rows)
    return source


async def _read(source: FakeAgentWarehouse, point: tuple[float, float] = BOISE, **kwargs: Any) -> dict[str, Any]:
    async with tools.run_context(warehouse_source=source):
        return await soil_properties.read_soil_properties(*point, as_of=TODAY, **kwargs)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(soil_properties.READS_ENABLED_VARIABLE, ENABLED)


# --- Kill switch --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "false", "1", "yes", "TRUE-ish", "TRUE", "True"])
async def test_reads_are_off_unless_the_flag_is_exactly_true(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(soil_properties.READS_ENABLED_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(soil_properties.READS_ENABLED_VARIABLE, value)
    source = _published([_row(NEAR_ORIGIN)])
    assert await _read(source) == {"state": "unavailable", "reason": "reads_disabled"}
    assert source.executed == [], "the flag is checked before any read"


# --- The read -------------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
async def test_the_nearest_centre_within_the_radius_is_served_as_mapped_integers() -> None:
    source = _published([_row(FAR_ORIGIN, ph=60), _row(NEAR_ORIGIN)])
    soil = await _read(source)
    centre = (NEAR_ORIGIN[0] + 0.0025, NEAR_ORIGIN[1] + 0.0025)
    expected_distance = int(soil_properties.haversine_meters(*BOISE, *centre) + 0.5)
    assert soil["state"] == "available"
    assert soil["release_id"] == "soilgrids-v2.0/2020-06-02"
    assert soil["distance_m"] == expected_distance
    assert soil["mapped"]["0-5cm"]["phh2o"] == PH_MAPPED
    assert set(soil["mapped"]) == {"0-5cm", "5-15cm", "15-30cm"}
    assert all(isinstance(value, int) for depth in soil["mapped"].values() for value in depth.values())


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("requested", "searched"),
    [
        pytest.param(soil_properties.DEFAULT_RADIUS_METERS, soil_properties.MAX_RADIUS_METERS, id="default-falls-back"),
        pytest.param(500, 500, id="explicit-is-strict"),
    ],
)
async def test_the_read_widens_the_origin_search_by_the_centre_margin(requested: int, searched: int) -> None:
    """`point_lane_rows` measures to the ORIGIN, so the SQL radius is the searched radius plus 400 m."""
    source = _published([_row(NEAR_ORIGIN)])
    await _read(source, radius_meters=requested)
    arguments = source.arguments_for("agent_point_lane_rows")
    assert arguments[4:6] == [BOISE[1], BOISE[0]], "the probe binds latitude first"
    assert arguments[6] == searched + soil_properties.ORIGIN_SEARCH_MARGIN_METERS


# Owner 2026-09-28, "Name the nearest estimate": SoilGrids masks urban pixels, so at the DEFAULT radius a
# point with no centre within 1,000 m is served the nearest centre within 2,000 m, its distance named in the
# label. An explicit radius stays strict. Distances are from BOISE to each origin's centre.
@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("origin", "radius_meters", "expected"),
    [
        pytest.param(
            NEAR_ORIGIN,
            soil_properties.DEFAULT_RADIUS_METERS,
            {
                "state": "available",
                "radius_m": 2000,
                "label": "SoilGrids v2.0 250 m model estimate, sampled at the centre of a ~500 m cell 343 m "
                "from this point (release soilgrids-v2.0/2020-06-02)",
                "topsoil_label": "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted)",
                "depth_label": "SoilGrids v2.0 250 m model estimate, 0-5 cm",
            },
            id="within-1-km",
        ),
        pytest.param(
            FALLBACK_ORIGIN,
            soil_properties.DEFAULT_RADIUS_METERS,
            {
                "state": "available",
                "radius_m": 2000,
                "label": "SoilGrids v2.0 250 m model estimate, nearest cell centre 1,404 m away "
                "(none within 1,000 m) (release soilgrids-v2.0/2020-06-02)",
                "topsoil_label": "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted), "
                "nearest cell centre 1,404 m away (none within 1,000 m)",
                "depth_label": "SoilGrids v2.0 250 m model estimate, 0-5 cm, nearest cell centre 1,404 m away "
                "(none within 1,000 m)",
            },
            id="default-falls-back-to-1.4-km-and-says-so",
        ),
        pytest.param(
            BEYOND_FALLBACK_ORIGIN,
            soil_properties.DEFAULT_RADIUS_METERS,
            {"state": "unavailable", "reason": "no_cell_within_radius", "radius_m": 2000},
            id="beyond-2-km-is-refused-at-2000",
        ),
        pytest.param(
            BEYOND_500_M_ORIGIN,
            500,
            {"state": "unavailable", "reason": "no_cell_within_radius", "radius_m": 500},
            id="explicit-500-never-falls-back",
        ),
    ],
)
async def test_the_default_radius_falls_back_to_the_nearest_named_estimate(
    origin: tuple[float, float], radius_meters: int, expected: dict[str, Any]
) -> None:
    source = _published([_row(origin)])
    async with tools.run_context(warehouse_source=source):
        payload = json.loads(await tools.query_soil_properties_at_point(*BOISE, radius_meters=radius_meters))
    observed = {key: payload[key] for key in ("state", "radius_m")}
    if payload["state"] == "available":
        soilgrids = payload["soilgrids"]
        observed["label"] = soilgrids["label"]
        observed["topsoil_label"] = soilgrids["topsoil_0_30cm"]["label"]
        observed["depth_label"] = soilgrids["depths"]["0-5cm"]["label"]
    else:
        observed["reason"] = payload["reason"]
    assert observed == expected


@pytest.mark.usefixtures("enabled")
async def test_a_lane_never_written_is_its_own_reason() -> None:
    assert await _read(FakeAgentWarehouse()) == {"state": "unavailable", "reason": "lane_never_written"}


@pytest.mark.usefixtures("enabled")
async def test_a_point_outside_the_lattice_is_refused_without_a_read() -> None:
    source = _published([_row(NEAR_ORIGIN)])
    assert await _read(source, DENVER) == {"state": "unavailable", "reason": "outside_release_coverage"}
    assert source.executed == []


@pytest.mark.usefixtures("enabled")
async def test_a_fractional_base_value_fails_the_read_rather_than_rounding() -> None:
    soil = await _read(_published([_row(NEAR_ORIGIN, ph=57.4)]))
    assert soil == {"state": "unavailable", "reason": "read_failed"}


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        (faults.serving_at_capacity(operation="agent_soil", concurrent_reads=3), "serving_at_capacity"),
        (faults.read_timed_out(operation="agent_soil", timeout_seconds=2.0), "timeout"),
        (faults.read_over_budget(operation="agent_soil"), "read_failed"),
    ],
)
async def test_serving_refusals_map_onto_the_one_reason_vocabulary(
    refusal: faults.ServingRefusalError, reason: str
) -> None:
    source = _published([_row(NEAR_ORIGIN)])
    source.run_raises = refusal
    assert await _read(source) == {"state": "unavailable", "reason": reason}


@pytest.mark.usefixtures("enabled")
async def test_a_slow_read_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*_args: Any) -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(soil_properties, "_read_published", slow)
    soil = await _read(FakeAgentWarehouse(), timeout_seconds=0.01)
    assert soil == {"state": "unavailable", "reason": "timeout"}


# --- Pure helpers ----------------------------------------------------------------------------


def test_ties_break_to_the_lower_latitude_then_the_lower_longitude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(soil_properties, "haversine_meters", lambda *_args: TIE_DISTANCE_METERS)
    rows = [_row((-116.2, 43.6)), _row((-116.205, 43.6)), _row((-116.2, 43.595))]
    chosen = soil_properties.select_nearest_cell(rows, longitude=BOISE[0], latitude=BOISE[1], radius_meters=1000)
    assert chosen is not None
    assert (chosen[0]["cell_longitude"], chosen[0]["cell_latitude"]) == (-116.2, 43.595)


def test_the_radius_is_clamped_to_the_contract_bounds() -> None:
    assert soil_properties.clamp_radius(1) == soil_properties.MIN_RADIUS_METERS
    assert soil_properties.clamp_radius(10_000) == soil_properties.MAX_RADIUS_METERS


def test_a_missing_column_is_a_corrupt_lane() -> None:
    row = _row(NEAR_ORIGIN)
    del row["cfvo_15_30cm"]
    with pytest.raises(soil_properties.SoilLaneCorruptError, match="cfvo_15_30cm"):
        soil_properties.mapped_values(row)


# --- The tool ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
async def test_the_tool_returns_labelled_physical_values_and_never_mapped_units() -> None:
    source = _published([_row(NEAR_ORIGIN)])
    async with tools.run_context(warehouse_source=source) as ledger:
        payload = json.loads(await tools.query_soil_properties_at_point(*BOISE, depths=["0-5cm"], properties=["phh2o"]))
    assert payload["state"] == "available"
    assert payload["reason"] is None
    soilgrids = payload["soilgrids"]
    assert soilgrids["basis"] == "model_estimate"
    assert set(soilgrids["depths"]) == {"0-5cm"}
    assert soilgrids["depths"]["0-5cm"] == {"ph": PH_PHYSICAL, "label": "SoilGrids v2.0 250 m model estimate, 0-5 cm"}
    assert "topsoil_0_30cm" in soilgrids, "the topsoil is always returned"
    assert "mapped" not in soilgrids
    assert "model estimate" in payload["note"]
    assert "not a soil sample" in payload["note"]
    assert [(entry["tool"], entry["row_count"], entry["state"]) for entry in ledger] == [
        ("soil_properties_at_point", 1, "available")
    ]


async def test_the_tool_states_a_disabled_lane_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(soil_properties.READS_ENABLED_VARIABLE, raising=False)
    async with tools.run_context(warehouse_source=FakeAgentWarehouse()) as ledger:
        payload = json.loads(await tools.query_soil_properties_at_point(*BOISE))
    assert payload["state"] == "unavailable"
    assert payload["reason"] == "reads_disabled"
    assert payload["soilgrids"] is None
    assert payload["radius_m"] == soil_properties.MAX_RADIUS_METERS, "the default reports the 2,000 m it searches"
    assert ledger[0]["row_count"] == 0


def test_the_tool_is_registered_with_a_bounded_point_schema() -> None:
    by_name = {tool.name: tool.to_dict() for tool in tools.WAREHOUSE_TOOLS}
    definition = by_name["soil_properties_at_point"]
    properties = definition["input_schema"]["properties"]
    assert {"longitude", "latitude", "radius_meters", "depths", "properties"} == set(properties)
    assert "MODEL ESTIMATES" in definition["description"]
    assert "anyOf" not in json.dumps(definition["input_schema"]), "the bridge forwards this schema to Gemini"


# --- Publication gate: the Gemini `schema_too_complex` incident --------------------------------
#
# `soil_properties_at_point` (56467bd4) was published UNCONDITIONALLY, which pushed the combined
# tool catalogue over Gemini's function-declaration complexity ceiling and 400'd every live
# regional-intelligence report. `tools.published_warehouse_tools()` is the one place this is fixed;
# every catalogue-publishing surface must derive from it, never from `WAREHOUSE_TOOLS` itself.


async def test_the_tool_stays_registered_but_unpublished_with_the_flag_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(soil_properties.READS_ENABLED_VARIABLE, raising=False)
    assert "soil_properties_at_point" in {tool.name for tool in tools.WAREHOUSE_TOOLS}
    assert "soil_properties_at_point" not in {tool.name for tool in tools.published_warehouse_tools()}


@pytest.mark.usefixtures("enabled")
def test_the_tool_is_published_with_the_flag_set() -> None:
    assert "soil_properties_at_point" in {tool.name for tool in tools.published_warehouse_tools()}


def test_the_flag_off_published_set_is_the_wave_two_set_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review M7 rule: with the flag unset, every tool except the soil tool -- wave 2 (54e266e3) exactly."""
    monkeypatch.delenv(soil_properties.READS_ENABLED_VARIABLE, raising=False)
    published = {tool.name for tool in tools.published_warehouse_tools()}
    registered = {tool.name for tool in tools.WAREHOUSE_TOOLS}
    assert published == registered - {"soil_properties_at_point"}


def test_publication_is_evaluated_per_call_not_cached_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lazy function, per the fix's own contract: flipping the flag changes the very next call."""
    monkeypatch.delenv(soil_properties.READS_ENABLED_VARIABLE, raising=False)
    assert "soil_properties_at_point" not in {tool.name for tool in tools.published_warehouse_tools()}
    monkeypatch.setenv(soil_properties.READS_ENABLED_VARIABLE, ENABLED)
    assert "soil_properties_at_point" in {tool.name for tool in tools.published_warehouse_tools()}
    monkeypatch.delenv(soil_properties.READS_ENABLED_VARIABLE, raising=False)
    assert "soil_properties_at_point" not in {tool.name for tool in tools.published_warehouse_tools()}
