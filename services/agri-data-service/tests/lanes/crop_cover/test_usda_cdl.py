"""`crop-cover-usda-maintain`: the shipped dark TOML, S14 resolution, and the edition-identity strategy.

The strategy never fetches a tile (`pipeline/lanes/crop_cover/AGENTS.md`); its one process edge is the
USDA catalogue query, faked here through `tests.runner.fakes.ScriptedUpstream` -- the same `ProviderClient`
fake `tests/lanes/water_gauges/test_usgs_water_data.py` drives its own strategy through.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from agri_data_service.execution.lane_catalogue import build_lane_catalogue
from agri_data_service.execution.lane_specs import LANE_SPECS
from agri_data_service.foundation.lane_config.loader import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.pipeline.lanes.crop_cover.usda_cdl import (
    CATALOGUE_QUERY_PARAMETERS,
    EDITION_IDENTITY_ENDPOINT,
    SCRATCH_DIR_ENVIRONMENT_VARIABLE,
    STRATEGY,
    _runner_scratch_dir,
)
from agri_data_service.pipeline.runner.contract import Unsettled, Written
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import NOW, ScriptedUpstream, Send

LANE_ID: Final = "crop-cover-usda-maintain"
#: `native_resolution`: 2022/2023 are native 30 m, 2024/2025 native 10 m (`products.py`).
_RESOLUTION_BY_YEAR: Final = {2022: 30, 2023: 30, 2024: 10, 2025: 10}


def _shipped_lane() -> tuple[object, object]:
    """The real TOML through the real loader, and the real PNW region it validates against."""
    region = load_region("pnw")
    loaded = load_lane_configs(REAL_LANES_DIRECTORY, region)
    assert LANE_ID not in loaded.quarantined, loaded.quarantined.get(LANE_ID)
    return loaded.lanes[LANE_ID], region


def _catalog_body(*, years: tuple[int, ...], duplicate: int | None = None) -> bytes:
    """A USDA `/query` answer naming one raster per `years`, plus a second ambiguous entry for `duplicate`."""
    features = []
    for year in years:
        name = f"{year}_{_RESOLUTION_BY_YEAR[year]}m_cdls"
        features.append({"attributes": {"Name": name, "OBJECTID": 1000 + year}})
        if year == duplicate:
            features.append({"attributes": {"Name": name, "OBJECTID": 2000 + year}})
    return json.dumps({"features": features}).encode()


def _catalog_upstream(*, years: tuple[int, ...], duplicate: int | None = None) -> ScriptedUpstream:
    """A `ProviderClient` fake that answers every send with the same fixed catalogue body."""
    body = _catalog_body(years=years, duplicate=duplicate)
    return ScriptedUpstream(answer=lambda endpoint, parameters, probe: body)  # noqa: ARG005


def test_release_days_asks_only_the_admitted_editions_inside_the_window() -> None:
    """Request planning: `release_series`'s calendar owes only what the window actually contains."""
    owed = STRATEGY.release_days(date(2022, 1, 1), date(2024, 12, 31))
    assert owed == [date(2023, 1, 30), date(2024, 1, 31)]


def test_release_days_owes_nothing_outside_any_admitted_edition() -> None:
    owed = STRATEGY.release_days(date(2024, 2, 1), date(2024, 2, 26))
    assert owed == []


def test_plan_requests_asks_one_catalogue_unit_per_admitted_release_day() -> None:
    """Request planning: one unit per release day, on the shared catalogue endpoint, never a tile."""
    lane, region = _shipped_lane()
    days = STRATEGY.release_days(date(2022, 1, 1), date(2026, 12, 31))
    requests = STRATEGY.plan_requests(days, lane, region)
    # Editions are CROP years (CDL_RELEASE_DATES): the 2022 edition releases 2023-01-30, the 2025
    # edition 2026-02-27 -- a unit is never named after its release day's year.
    assert [request.unit for request in requests] == [
        "edition:2022",
        "edition:2023",
        "edition:2024",
        "edition:2025",
    ]
    assert {request.endpoint for request in requests} == {EDITION_IDENTITY_ENDPOINT}
    assert all(request.query() == dict(CATALOGUE_QUERY_PARAMETERS) for request in requests)


def test_plan_requests_ignores_a_day_that_names_no_admitted_edition() -> None:
    lane, region = _shipped_lane()
    requests = STRATEGY.plan_requests([date(2024, 6, 1)], lane, region)
    assert requests == []


async def test_fetch_sends_the_catalogue_query_and_parses_its_answer() -> None:
    """`fetch` is the ONE process edge: it sends through the handed `ProviderClient`, never raw httpx."""
    lane, region = _shipped_lane()
    [request] = STRATEGY.plan_requests([date(2023, 1, 30)], lane, region)
    upstream = _catalog_upstream(years=(2022, 2023))

    response = await STRATEGY.fetch(request, upstream)

    expected_send = Send(endpoint=EDITION_IDENTITY_ENDPOINT, parameters=dict(CATALOGUE_QUERY_PARAMETERS), probe=False)
    assert upstream.sends == [expected_send]
    assert response.payload == json.loads(_catalog_body(years=(2022, 2023)))
    assert response.retrieved_at == NOW


def test_settle_is_unsettled_before_any_unit_has_answered() -> None:
    """A release day with no answer yet is `unsettled`, never a fabricated write."""
    settlement = STRATEGY.settle(date(2023, 1, 30), [], context=None)
    assert isinstance(settlement, Unsettled)
    assert settlement.reason == "unsettled"


async def test_settle_writes_once_the_catalogue_names_exactly_one_matching_raster() -> None:
    lane, region = _shipped_lane()
    day = date(2023, 1, 30)
    [request] = STRATEGY.plan_requests([day], lane, region)
    upstream = _catalog_upstream(years=(2022, 2023))
    response = await STRATEGY.fetch(request, upstream)

    settlement = STRATEGY.settle(day, [response], context=None)

    assert isinstance(settlement, Written)
    assert (settlement.expected_units, settlement.present_units) == (1, 1)
    # Release day 2023-01-30 IS the 2022 crop-year edition (CDL_RELEASE_DATES[2022]).
    assert settlement.expected_unit_ids == frozenset({"edition:2022"})


async def test_settle_refuses_an_ambiguous_raster_identity_rather_than_guessing() -> None:
    """Unhappy path: two catalogue rows naming the same raster is a refusal, never a silent pick."""
    lane, region = _shipped_lane()
    day = date(2023, 1, 30)
    [request] = STRATEGY.plan_requests([day], lane, region)
    upstream = _catalog_upstream(years=(2022, 2023), duplicate=2022)
    response = await STRATEGY.fetch(request, upstream)

    with pytest.raises(PipelineOperationError):
        STRATEGY.settle(day, [response], context=None)


async def test_rows_emits_exactly_one_edition_identity_row() -> None:
    lane, region = _shipped_lane()
    day = date(2024, 1, 31)
    [request] = STRATEGY.plan_requests([day], lane, region)
    upstream = _catalog_upstream(years=(2023, 2024))
    response = await STRATEGY.fetch(request, upstream)

    table = STRATEGY.rows(day, [response])

    assert table.num_rows == 1
    row = table.to_pylist()[0]
    # Release day 2024-01-31 carries the 2023 crop-year edition, so the row observes 2023.
    assert (row["observed_year"], row["release_day"], row["object_id"]) == (2023, day, 1000 + 2023)


def test_the_shipped_lane_is_dark_and_resolves_its_own_strategy() -> None:
    """The TOML through the real pydantic loader, then S14 resolution, end to end -- never a tile fetch."""
    lane, _region = _shipped_lane()
    assert lane.enabled is False
    assert lane.nature == "release_series"
    assert lane.executor == "legacy"
    assert lane.source.provider == "usda-cdl"
    assert resolve_strategy(lane) is STRATEGY


def test_the_shipped_toml_keeps_the_legacy_maintain_lane_on_its_dispatch_path() -> None:
    """Load-bearing: `executor = "legacy"` must leave `lane_specs.py`'s real spec dispatchable.

    `execution/lane_catalogue.py::build_lane_catalogue` takes a lane id OFF `legacy_specs` the
    instant a TOML with that id loads with `executor = "config"`, regardless of `enabled`
    (`lane_catalogue.py:292-304`) -- so asserting `lane.executor == "legacy"` on the parsed TOML
    alone is not enough to prove the legacy cron survives. This drives the REAL catalogue the
    executor builds from the REAL `LANE_SPECS`, the observable consequence
    (`tests/lanes/burn_severity/test_mtbs_releases.py`'s identical shape for its own CQ-9 carve-out).
    """
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"))

    catalogue = build_lane_catalogue(legacy_specs=LANE_SPECS, configs=configs, environment={})

    assert LANE_ID not in catalogue.quarantined
    assert LANE_ID not in catalogue.config_lanes
    legacy = catalogue.legacy_specs[LANE_ID]
    assert legacy.command is not None
    assert "maintain" in legacy.command
    assert "agri_data_service.pipeline.direct.crop_cover" in legacy.command


def test_runner_scratch_dir_resolves_under_the_os_temp_root_by_default() -> None:
    """The no-/tmp rule: the default root is derived from `tempfile`, never a hardcoded POSIX path."""
    assert _runner_scratch_dir({}) == Path(tempfile.gettempdir()) / "agri-crop-cover"


def test_runner_scratch_dir_honours_an_explicit_override() -> None:
    override = str(Path(tempfile.gettempdir()) / "a-different-crop-cover-root")
    resolved = _runner_scratch_dir({SCRATCH_DIR_ENVIRONMENT_VARIABLE: override})
    assert resolved == Path(override)


def test_runner_scratch_dir_falls_back_on_a_blank_override() -> None:
    resolved = _runner_scratch_dir({SCRATCH_DIR_ENVIRONMENT_VARIABLE: "   "})
    assert resolved == Path(tempfile.gettempdir()) / "agri-crop-cover"
