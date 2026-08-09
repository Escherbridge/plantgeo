"""Lock the ERA5 -> NASA lattice provenance binding that no production code re-checks.

`nasa_lattice_plan_checksum` is declared once as a bare 64-hex pattern and is never recomputed
or cross-validated anywhere in the service, yet it is folded into the ERA5 plan checksum. These
tests re-derive it from the artifacts on disk so a hand-typed or stale value cannot survive.
See plans/AGENTS.md.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution.historical_backfill import (
    HistoricalNasaBackfillPlan,
    historical_nasa_plan_checksum,
)
from agri_data_service.execution.historical_era5 import (
    ERA5_LAND_SIGNAL_SPECIFICATIONS,
    HistoricalEra5LandBackfillPlan,
    _reject_unaccepted_era5_licences,
)
from agri_data_service.execution.historical_open_meteo import (
    OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS,
    OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS,
    HistoricalOpenMeteoArchivePlan,
)

PLANS_ROOT = Path(__file__).resolve().parent.parent / "plans"
NASA_LATTICE_PLAN_PATH = PLANS_ROOT / "nasa-power-pnw-soil-lattice-20220430-20260430.json"
ERA5_PLAN_PATH = PLANS_ROOT / "era5-land-pnw-soil-20220430-20260430.json"
WESTERN_NASA_LATTICE_PLAN_PATH = PLANS_ROOT / "nasa-power-western-na-soil-lattice-20220430-20260430.json"
WESTERN_ERA5_PLAN_PATH = PLANS_ROOT / "era5-land-western-na-soil-20220430-20260430.json"

PACIFIC_NORTHWEST_ENVELOPE = {"north": 49.0, "west": -125.0, "south": 42.0, "east": -111.0}
WESTERN_NORTH_AMERICA_ENVELOPE = {"north": 51.0, "west": -125.0, "south": 31.0, "east": -104.0}
SOIL_PARAMETERS = ["soil_temperature_level_1", "volumetric_soil_water_layer_1"]

# The widened replay is worth roughly 100x the probe's cells; the count is asserted so a silent
# narrowing of the lattice cannot pass as a successful regeneration.
WESTERN_LATTICE_CELL_COUNT = 397


def _plan_generator() -> ModuleType:
    """Import the committed generator so the artifacts can be re-derived, not just re-read."""
    path = PLANS_ROOT / "author_pnw_soil_moisture_plans.py"
    spec = importlib.util.spec_from_file_location("author_pnw_soil_moisture_plans", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def nasa_lattice_plan() -> HistoricalNasaBackfillPlan:
    return HistoricalNasaBackfillPlan.model_validate_json(NASA_LATTICE_PLAN_PATH.read_bytes())


@pytest.fixture(scope="module")
def era5_plan() -> HistoricalEra5LandBackfillPlan:
    return HistoricalEra5LandBackfillPlan.model_validate_json(ERA5_PLAN_PATH.read_bytes())


@pytest.fixture(scope="module")
def western_nasa_lattice_plan() -> HistoricalNasaBackfillPlan:
    return HistoricalNasaBackfillPlan.model_validate_json(WESTERN_NASA_LATTICE_PLAN_PATH.read_bytes())


@pytest.fixture(scope="module")
def western_era5_plan() -> HistoricalEra5LandBackfillPlan:
    return HistoricalEra5LandBackfillPlan.model_validate_json(WESTERN_ERA5_PLAN_PATH.read_bytes())


def test_era5_plan_quotes_the_real_nasa_lattice_checksum(
    nasa_lattice_plan: HistoricalNasaBackfillPlan,
    era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    assert era5_plan.nasa_lattice_plan_checksum == historical_nasa_plan_checksum(nasa_lattice_plan)


def test_era5_cells_are_established_by_the_named_nasa_lattice(
    nasa_lattice_plan: HistoricalNasaBackfillPlan,
    era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    """Persistence resolves spatial cells by cell_key, so every ERA5 cell must exist in the lattice."""
    lattice = {cell.cell_key: (cell.latitude, cell.longitude) for cell in nasa_lattice_plan.nasa.cells}
    for cell in era5_plan.cells:
        assert cell.cell_key in lattice
        assert (cell.latitude, cell.longitude) == lattice[cell.cell_key]


def test_era5_plan_carries_only_the_two_soil_signals(era5_plan: HistoricalEra5LandBackfillPlan) -> None:
    assert era5_plan.parameters == SOIL_PARAMETERS
    for parameter in era5_plan.parameters:
        assert parameter in ERA5_LAND_SIGNAL_SPECIFICATIONS


def test_era5_plan_covers_the_reviewed_pacific_northwest_envelope(
    era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    assert era5_plan.requested_area.model_dump() == PACIFIC_NORTHWEST_ENVELOPE
    for cell in era5_plan.cells:
        assert PACIFIC_NORTHWEST_ENVELOPE["south"] <= cell.latitude <= PACIFIC_NORTHWEST_ENVELOPE["north"]
        assert PACIFIC_NORTHWEST_ENVELOPE["west"] <= cell.longitude <= PACIFIC_NORTHWEST_ENVELOPE["east"]


def test_era5_periods_exactly_cover_the_four_year_window(era5_plan: HistoricalEra5LandBackfillPlan) -> None:
    assert era5_plan.periods[0].start_date == era5_plan.window.start_date
    assert era5_plan.periods[-1].end_date == era5_plan.window.end_date
    assert len(era5_plan.periods) <= 60  # noqa: PLR2004


def test_western_era5_plan_quotes_the_real_western_nasa_lattice_checksum(
    western_nasa_lattice_plan: HistoricalNasaBackfillPlan,
    western_era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    """The widened pair must chain to its own lattice, not inherit the four-cell probe's checksum."""
    assert western_era5_plan.nasa_lattice_plan_checksum == historical_nasa_plan_checksum(western_nasa_lattice_plan)


def test_western_era5_cells_are_established_by_the_named_western_lattice(
    western_nasa_lattice_plan: HistoricalNasaBackfillPlan,
    western_era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    lattice = {cell.cell_key: (cell.latitude, cell.longitude) for cell in western_nasa_lattice_plan.nasa.cells}
    assert len(western_era5_plan.cells) == WESTERN_LATTICE_CELL_COUNT
    for cell in western_era5_plan.cells:
        assert cell.cell_key in lattice
        assert (cell.latitude, cell.longitude) == lattice[cell.cell_key]


def test_western_lattice_is_a_strict_superset_of_the_probe_lattice(
    era5_plan: HistoricalEra5LandBackfillPlan,
    western_era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    """Reused cell keys keep `_ensure_spatial_cell` idempotent instead of colliding on geometry."""
    probe = {cell.cell_key: (cell.latitude, cell.longitude) for cell in era5_plan.cells}
    widened = {cell.cell_key: (cell.latitude, cell.longitude) for cell in western_era5_plan.cells}
    assert probe.keys() < widened.keys()
    for key, coordinates in probe.items():
        assert widened[key] == coordinates


def test_western_plan_is_a_distinct_release_set_from_the_probe(
    era5_plan: HistoricalEra5LandBackfillPlan,
    western_era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    """A widened lattice is a different release set; the partially fetched probe must stay separate."""
    assert western_era5_plan.release_set_key != era5_plan.release_set_key
    assert western_era5_plan.requested_area != era5_plan.requested_area


def test_western_era5_cells_lie_inside_the_requested_cds_envelope(
    western_era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    """A cell outside `area` is absent from the NetCDF and fails the nearest-point tolerance."""
    assert western_era5_plan.requested_area.model_dump() == WESTERN_NORTH_AMERICA_ENVELOPE
    for cell in western_era5_plan.cells:
        assert WESTERN_NORTH_AMERICA_ENVELOPE["south"] <= cell.latitude <= WESTERN_NORTH_AMERICA_ENVELOPE["north"]
        assert WESTERN_NORTH_AMERICA_ENVELOPE["west"] <= cell.longitude <= WESTERN_NORTH_AMERICA_ENVELOPE["east"]


def test_western_plan_excludes_cells_outside_the_era5_land_domain(
    western_era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    """Out-of-domain cells are not gaps; recording them would write only is_observed=false rows."""
    generator = _plan_generator()
    excluded = generator.CELLS_OUTSIDE_ERA5_LAND_DOMAIN
    assert excluded
    assert not {cell.cell_key for cell in western_era5_plan.cells}.intersection(excluded)


def test_western_plan_carries_only_the_two_soil_signals(
    western_era5_plan: HistoricalEra5LandBackfillPlan,
) -> None:
    assert western_era5_plan.parameters == SOIL_PARAMETERS


@pytest.mark.parametrize("artifact", ["nasa", "era5"])
def test_western_artifacts_regenerate_byte_for_byte(artifact: str) -> None:
    """A hand-edited plan is unreproducible from a clone; re-derive both artifacts to forbid that."""
    generator = _plan_generator()
    nasa_plan, era5_plan_value = generator.build_western_north_america_plans()
    plan, path = (
        (nasa_plan, WESTERN_NASA_LATTICE_PLAN_PATH) if artifact == "nasa" else (era5_plan_value, WESTERN_ERA5_PLAN_PATH)
    )
    assert canonical_json_bytes(plan.model_dump(mode="json")) == path.read_bytes()


OPEN_METEO_PROBE_PLAN_PATH = PLANS_ROOT / "open-meteo-era5-land-boise-ndvi-probe-20220430-20260430.json"
OPEN_METEO_LATTICE_PLAN_PATH = PLANS_ROOT / "open-meteo-era5-land-pnw-ndvi-lattice-20220430-20260430.json"

# The whole point of the Open-Meteo lane: these are the cells the ML covariate layer returns
# all-NULL for. They exist in `agri.spatial_cell` with zero signal rows, and the CDS contract
# structurally cannot address them.
NDVI_LATTICE_CELL_COUNT = 1568
# Cells are ordered by their string cell_key, exactly as `ingest/vegetation.py` orders them, so the
# extremes are the lexical ones: "-111.1250" sorts before "-124.8750".
NDVI_LATTICE_FIRST_KEY = "sentinel2-ndvi-0p25deg:42.1250:-111.1250"
NDVI_LATTICE_LAST_KEY = "sentinel2-ndvi-0p25deg:48.8750:-124.8750"
OPEN_METEO_SOIL_MOISTURE_SIGNALS = {
    "soil_water_content_layer_1",
    "soil_water_content_layer_2",
    "soil_water_content_layer_3",
}


@pytest.fixture(scope="module")
def open_meteo_probe_plan() -> HistoricalOpenMeteoArchivePlan:
    return HistoricalOpenMeteoArchivePlan.model_validate_json(OPEN_METEO_PROBE_PLAN_PATH.read_bytes())


@pytest.fixture(scope="module")
def open_meteo_lattice_plan() -> HistoricalOpenMeteoArchivePlan:
    return HistoricalOpenMeteoArchivePlan.model_validate_json(OPEN_METEO_LATTICE_PLAN_PATH.read_bytes())


def test_open_meteo_lattice_plan_targets_the_whole_ndvi_grid(
    open_meteo_lattice_plan: HistoricalOpenMeteoArchivePlan,
) -> None:
    keys = [cell.cell_key for cell in open_meteo_lattice_plan.cells]
    assert len(keys) == NDVI_LATTICE_CELL_COUNT
    assert keys[0] == NDVI_LATTICE_FIRST_KEY
    assert keys[-1] == NDVI_LATTICE_LAST_KEY
    assert keys == sorted(keys)
    assert open_meteo_lattice_plan.grid_name == "sentinel2-ndvi-0p25deg"
    assert all(key.startswith("sentinel2-ndvi-0p25deg:") for key in keys)


def test_open_meteo_cells_are_off_the_one_degree_grid_the_cds_contract_requires(
    open_meteo_lattice_plan: HistoricalOpenMeteoArchivePlan,
) -> None:
    """`require_governed_monthly_coverage` rejects any cell not on the 1.0-degree grid; none of these are."""
    assert all(cell.latitude % 1.0 and cell.longitude % 1.0 for cell in open_meteo_lattice_plan.cells)


def test_open_meteo_plans_carry_only_volumetric_soil_water(
    open_meteo_lattice_plan: HistoricalOpenMeteoArchivePlan,
    open_meteo_probe_plan: HistoricalOpenMeteoArchivePlan,
) -> None:
    """Soil temperature is excluded on purpose; see execution/AGENTS.md for the measured reason."""
    for plan in (open_meteo_lattice_plan, open_meteo_probe_plan):
        assert plan.parameters == sorted(OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS)
        signals = {OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[name].signal_name for name in plan.parameters}
        assert signals == OPEN_METEO_SOIL_MOISTURE_SIGNALS
        normalized = {OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[name].normalized_unit for name in plan.parameters}
        assert normalized == {"m^3/m^3"}


def test_open_meteo_shares_the_cds_signal_name_but_not_its_support_or_source(
    era5_plan: HistoricalEra5LandBackfillPlan,
    open_meteo_lattice_plan: HistoricalOpenMeteoArchivePlan,
) -> None:
    """Same physical quantity keeps one name; the schema already models the support and provenance split."""
    cds_layer_one = ERA5_LAND_SIGNAL_SPECIFICATIONS["volumetric_soil_water_layer_1"]
    archive_layer_one = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS["soil_moisture_0_to_7cm_mean"]
    # The CDS table is a plain tuple; the archive table is a NamedTuple, read by name.
    assert cds_layer_one[0] == archive_layer_one.signal_name == "soil_water_content_layer_1"
    assert cds_layer_one[2] == archive_layer_one.normalized_unit == "m^3/m^3"
    assert open_meteo_lattice_plan.source.key != era5_plan.source.key
    assert open_meteo_lattice_plan.support_key == "era5-land-0.1deg"


def test_open_meteo_source_records_the_intermediary_relationship(
    open_meteo_lattice_plan: HistoricalOpenMeteoArchivePlan,
) -> None:
    """Provenance here is weaker than a CDS receipt; the registration must say so rather than imply one."""
    citation = open_meteo_lattice_plan.source.citation
    assert "INTERMEDIARY" in citation
    assert "not retrieved from ECMWF or the CDS" in citation
    assert open_meteo_lattice_plan.source.license_name.startswith("CC-BY 4.0 (Open-Meteo)")


def test_open_meteo_probe_is_a_strict_subset_of_the_lattice(
    open_meteo_probe_plan: HistoricalOpenMeteoArchivePlan,
    open_meteo_lattice_plan: HistoricalOpenMeteoArchivePlan,
) -> None:
    probe = {cell.cell_key: (cell.latitude, cell.longitude) for cell in open_meteo_probe_plan.cells}
    lattice = {cell.cell_key: (cell.latitude, cell.longitude) for cell in open_meteo_lattice_plan.cells}
    assert probe.keys() < lattice.keys()
    for key, coordinates in probe.items():
        assert lattice[key] == coordinates
    assert open_meteo_probe_plan.release_set_key != open_meteo_lattice_plan.release_set_key


@pytest.mark.parametrize("artifact", ["probe", "lattice"])
def test_open_meteo_artifacts_regenerate_byte_for_byte(artifact: str) -> None:
    """A hand-edited plan is unreproducible from a clone; the lattice is derived, never read from the warehouse."""
    generator = _plan_generator()
    probe_plan, lattice_plan = generator.build_open_meteo_ndvi_plans()
    plan, path = (
        (probe_plan, OPEN_METEO_PROBE_PLAN_PATH)
        if artifact == "probe"
        else (lattice_plan, OPEN_METEO_LATTICE_PLAN_PATH)
    )
    assert canonical_json_bytes(plan.model_dump(mode="json")) == path.read_bytes()


class _StubResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body


class _StubHttpError(Exception):
    def __init__(self, response: _StubResponse) -> None:
        super().__init__("stub")
        self.response = response


def test_licence_refusal_becomes_an_actionable_value_error() -> None:
    """The CLI prints ValueError verbatim and hides every other exception behind its class name."""
    error = _StubHttpError(
        _StubResponse(
            403,
            {"title": "required licences not accepted", "detail": "please visit https://example.invalid"},
        )
    )
    with pytest.raises(ValueError, match="required licences not accepted") as raised:
        _reject_unaccepted_era5_licences(error, "derived-era5-land-daily-statistics")
    assert "browser action" in str(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        _StubResponse(500, {"title": "internal error"}),
        _StubResponse(403, {"title": "quota exceeded"}),
        _StubResponse(403, "not json at all"),
    ],
)
def test_other_refusals_are_left_for_the_caller_to_reraise(response: _StubResponse) -> None:
    _reject_unaccepted_era5_licences(_StubHttpError(response), "derived-era5-land-daily-statistics")
