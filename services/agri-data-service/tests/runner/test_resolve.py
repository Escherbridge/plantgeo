"""S14 resolution: `strategy = "<layer>.<source>"` -> `<package>.<layer>.<source>::STRATEGY`, shape-checked."""

from __future__ import annotations

import pytest

from agri_data_service.pipeline.lanes.transforms.precedence import PrecedenceTransform
from agri_data_service.pipeline.runner.exits import EXIT_INTERNAL_ERROR, exit_code_for
from agri_data_service.pipeline.runner.resolve import StrategyResolutionError, resolve_strategy
from tests.runner.fakes import FIXTURE_STRATEGY_PACKAGE, grid_lane, point_lane, precedence_lanes
from tests.runner.fixtures.grid_refuse import GridRefuseStrategy
from tests.runner.fixtures.point_recheck import PointRecheckStrategy


def test_the_precedence_transform_resolves_from_the_lanes_package_by_convention() -> None:
    """S7 + S14: `transforms.precedence` is found like any strategy; nothing lists it."""
    transform, _ = precedence_lanes()

    assert isinstance(resolve_strategy(transform), PrecedenceTransform)


@pytest.mark.parametrize(
    ("lane", "requires_probe_edge", "expected"),
    [
        (grid_lane(), True, GridRefuseStrategy),
        (point_lane(), False, PointRecheckStrategy),
    ],
    ids=["settled-weighted-with-probe", "provisional-without-probe"],
)
def test_a_fixture_strategy_resolves_with_the_shape_its_lane_needs(
    lane: object, requires_probe_edge: bool, expected: type
) -> None:
    resolved = resolve_strategy(lane, requires_probe_edge=requires_probe_edge, package=FIXTURE_STRATEGY_PACKAGE)  # type: ignore[arg-type]

    assert isinstance(resolved, expected)


@pytest.mark.parametrize(
    ("lane", "requires_probe_edge", "message"),
    [
        (grid_lane(strategy="fixtures.no_such_module"), False, "does not import"),
        (point_lane(), True, "must implement probe_edge"),
        (grid_lane(kind="transform", source=None, inputs=["fixture-point-recheck"]), False, "has no derive()"),
    ],
    ids=["missing-module", "settled-weighted-lane-without-probe-edge", "ingest-strategy-on-a-transform"],
)
def test_a_strategy_that_cannot_serve_its_lane_is_a_configuration_error(
    lane: object, requires_probe_edge: bool, message: str
) -> None:
    """Exit 78 material: an unimportable key, a missing S6 probe, or the wrong kind of strategy."""
    with pytest.raises(StrategyResolutionError, match=message):
        resolve_strategy(lane, requires_probe_edge=requires_probe_edge, package=FIXTURE_STRATEGY_PACKAGE)  # type: ignore[arg-type]


def test_a_strategy_whose_own_import_fails_is_a_code_fault_not_a_configuration_error() -> None:
    """L5: the key names a real module whose dependency is missing, so exit 70 (the code ladder), never 78."""
    lane = grid_lane(strategy="fixtures.broken_dependency")

    with pytest.raises(ModuleNotFoundError) as raised:
        resolve_strategy(lane, package=FIXTURE_STRATEGY_PACKAGE)

    assert raised.value.name == "agri_data_service.no_such_dependency"
    assert exit_code_for(raised.value) == EXIT_INTERNAL_ERROR
