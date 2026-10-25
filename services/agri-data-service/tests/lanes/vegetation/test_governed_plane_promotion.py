"""`vegetation-ndvi-governed-plane-promotion`: the DISABLED transform loads, resolves, and names the legacy target.

The output target is checked against the legacy verb itself (`execution/vegetation_partition_promotion.py`
and the SQL its register seam runs), not against this package's own constants.
"""

from __future__ import annotations

import importlib
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution import vegetation_ndvi_plane, vegetation_partition_promotion
from agri_data_service.execution.lane_specs import LANE_SPECS
from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.lanes.vegetation import governed_plane_promotion
from agri_data_service.pipeline.lanes.vegetation.governed_plane_promotion import STRATEGY
from agri_data_service.pipeline.runner.exits import TurnConfigurationError
from agri_data_service.pipeline.runner.reader import registered_stream_contract
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import ManualClock, MemoryLaneStore, ports_for, run, spec_for

if TYPE_CHECKING:
    from agri_data_service.foundation.lane_config.models import LaneConfig

LANE_ID = "vegetation-ndvi-governed-plane-promotion"
FORWARD_LANE_ID = "vegetation-sentinel2-ndvi-direct-forward"
SQL_ROOT = Path(vegetation_ndvi_plane.__file__).parent.parent / "sql"
INSERT_SQL = SQL_ROOT / "execution" / "insert_partition_observations.sql"


def _shipped_lane() -> LaneConfig:
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"), stream_contract=registered_stream_contract)
    return configs.lanes[LANE_ID]


def test_the_disabled_transform_loads_unquarantined_and_resolves_to_a_deriving_strategy() -> None:
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"), stream_contract=registered_stream_contract)
    lane = configs.lanes[LANE_ID]

    assert LANE_ID not in configs.quarantined
    assert (lane.kind, lane.enabled, lane.executor) == ("transform", False, "legacy")
    assert lane.inputs == (FORWARD_LANE_ID,)
    assert FORWARD_LANE_ID in configs.lanes
    # No Parquet output: the only stream involved, `vegetation`, is the forward lane's (S18).
    assert lane.streams == ()
    assert resolve_strategy(lane) is STRATEGY
    assert lane.schedule.forward_cron == LANE_SPECS[LANE_ID].schedule
    assert lane.days is not None
    assert lane.days.floor == date.fromisoformat(str(LANE_SPECS[LANE_ID].writer_floor))


def test_the_declared_input_and_output_target_are_the_legacy_promotion_s() -> None:
    assert governed_plane_promotion.INPUT_STREAM == vegetation_partition_promotion.VEGETATION_PLANE_STREAM
    assert governed_plane_promotion.INPUT_KIND == vegetation_partition_promotion.PROMOTABLE_KIND
    module_name, _, verb_name = governed_plane_promotion.LEGACY_REGISTER_VERB.rpartition(".")
    verb = getattr(importlib.import_module(module_name), verb_name)
    assert vegetation_partition_promotion.register_governed_partition_plane is verb
    table = governed_plane_promotion.OUTPUT_TARGET.removeprefix("postgres:")
    assert f"INSERT INTO {table} (" in INSERT_SQL.read_text(encoding="utf-8")


async def test_a_transform_turn_of_the_shipped_lane_is_refused_before_derive_ever_runs() -> None:
    """The real guard: `turn.py::_validate` refuses ANY `mode="transform"` turn of a lane that
    declares no `[[streams]]`, before any census read or `derive` call -- disabled or not, and
    regardless of what `derive` itself would have raised. Exit 78 (`TurnConfigurationError`,
    `pipeline/runner/exits.py::EXIT_CONFIGURATION_ERROR`).
    """
    spec = spec_for(_shipped_lane(), mode="transform")
    ports = ports_for(STRATEGY, MemoryLaneStore(), clock=ManualClock())

    with pytest.raises(TurnConfigurationError, match=r"declares no \[\[streams\]\]"):
        await run(spec, ports)
