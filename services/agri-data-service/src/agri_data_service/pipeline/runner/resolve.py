"""S14: `strategy = "<layer>.<source>"` resolves to `pipeline/lanes/<layer>/<source>.py::STRATEGY` by `importlib`.

No registry file, no per-lane entry: adding a lane is a TOML plus one module. See
`pipeline/runner/AGENTS.md` "Resolution".
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from agri_data_service.foundation.lane_config.models import STRATEGY_ATTRIBUTE, STRATEGY_PACKAGE
from agri_data_service.pipeline.runner.contract import EdgeProbingStrategy, IngestStrategy, TransformStrategy

if TYPE_CHECKING:
    from agri_data_service.foundation.lane_config.models import LaneConfig


class StrategyResolutionError(Exception):
    """A lane's strategy key names no importable module, no `STRATEGY`, or an object of the wrong shape (exit 78)."""


def strategy_module_name(lane: LaneConfig, *, package: str = STRATEGY_PACKAGE) -> str:
    """The module S14 maps `lane.strategy` to under `package`."""
    return f"{package}.{lane.strategy}"


def _names_module_or_parent(error: ModuleNotFoundError, module_name: str) -> bool:
    """True when the missing module is the strategy module itself or one of its packages (the key is wrong)."""
    missing = error.name or ""
    return bool(missing) and (module_name == missing or module_name.startswith(f"{missing}."))


def resolve_strategy(
    lane: LaneConfig,
    *,
    requires_probe_edge: bool = False,
    package: str = STRATEGY_PACKAGE,
) -> IngestStrategy | TransformStrategy:
    """Import `lane`'s strategy and check it has the shape its `kind` needs (and `probe_edge` when S6 demands it)."""
    module_name = strategy_module_name(lane, package=package)
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if not _names_module_or_parent(error, module_name):
            raise  # the strategy module exists but its own import failed: a code fault (exit 70), not config
        raise StrategyResolutionError(
            f"lane {lane.id!r}: strategy {lane.strategy!r} names module {module_name}, which does not import "
            f"({type(error).__name__})"
        ) from error
    strategy = getattr(module, STRATEGY_ATTRIBUTE, None)
    if strategy is None:
        raise StrategyResolutionError(f"lane {lane.id!r}: module {module_name} exports no {STRATEGY_ATTRIBUTE}")
    if lane.kind == "transform":
        if not isinstance(strategy, TransformStrategy):
            raise StrategyResolutionError(f"lane {lane.id!r}: {module_name}.{STRATEGY_ATTRIBUTE} has no derive()")
        return strategy
    if not isinstance(strategy, IngestStrategy):
        raise StrategyResolutionError(
            f"lane {lane.id!r}: {module_name}.{STRATEGY_ATTRIBUTE} lacks one of plan_requests, fetch, settle, rows"
        )
    if requires_probe_edge and not isinstance(strategy, EdgeProbingStrategy):
        raise StrategyResolutionError(
            f"lane {lane.id!r} is a settled lane on a weighted provider firing more than once a day, so "
            f"{module_name}.{STRATEGY_ATTRIBUTE} must implement probe_edge (S6, S19)"
        )
    return strategy


__all__ = ["StrategyResolutionError", "resolve_strategy", "strategy_module_name"]
