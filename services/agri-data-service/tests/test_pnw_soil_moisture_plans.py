"""Lock the ERA5 -> NASA lattice provenance binding that no production code re-checks.

`nasa_lattice_plan_checksum` is declared once as a bare 64-hex pattern and is never recomputed
or cross-validated anywhere in the service, yet it is folded into the ERA5 plan checksum. These
tests re-derive it from the artifacts on disk so a hand-typed or stale value cannot survive.
See plans/AGENTS.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agri_data_service.execution.historical_backfill import (
    HistoricalNasaBackfillPlan,
    historical_nasa_plan_checksum,
)
from agri_data_service.execution.historical_era5 import (
    ERA5_LAND_SIGNAL_SPECIFICATIONS,
    HistoricalEra5LandBackfillPlan,
    _reject_unaccepted_era5_licences,
)

PLANS_ROOT = Path(__file__).resolve().parent.parent / "plans"
NASA_LATTICE_PLAN_PATH = PLANS_ROOT / "nasa-power-pnw-soil-lattice-20220430-20260430.json"
ERA5_PLAN_PATH = PLANS_ROOT / "era5-land-pnw-soil-20220430-20260430.json"

PACIFIC_NORTHWEST_ENVELOPE = {"north": 49.0, "west": -125.0, "south": 42.0, "east": -111.0}
SOIL_PARAMETERS = ["soil_temperature_level_1", "volumetric_soil_water_layer_1"]


@pytest.fixture(scope="module")
def nasa_lattice_plan() -> HistoricalNasaBackfillPlan:
    return HistoricalNasaBackfillPlan.model_validate_json(NASA_LATTICE_PLAN_PATH.read_bytes())


@pytest.fixture(scope="module")
def era5_plan() -> HistoricalEra5LandBackfillPlan:
    return HistoricalEra5LandBackfillPlan.model_validate_json(ERA5_PLAN_PATH.read_bytes())


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
