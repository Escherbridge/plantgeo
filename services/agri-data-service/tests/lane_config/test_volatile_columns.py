"""`[[streams]] volatile_columns` through the real loader and the real stream registry (G6 compare prerequisite).

A name the stream schema does not carry, or a grain column, quarantines the lane at load (S8) rather
than going silently unused at compare time. See `pipeline/runner/AGENTS.md` "Volatile columns".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.runner.reader import registered_stream_contract
from tests.lane_config.builders import REAL_LANES_DIRECTORY, settled_soil_lane, write_lane_tree

if TYPE_CHECKING:
    from pathlib import Path

LANE: Final = "drought-volatile-fixture"
#: A registered stream whose schema carries `ingested_at` and whose grain is (`valid_date`, `dm_category`).
REGISTERED_STREAM: Final = "drought"


@pytest.mark.parametrize(
    ("slug", "volatile_columns", "refusal"),
    [
        pytest.param(REGISTERED_STREAM, ["ingested_at"], None, id="registered-capture-column-loads"),
        pytest.param(REGISTERED_STREAM, ["ingested_at", "ingest_time"], "['ingest_time'] are not columns", id="typo"),
        pytest.param(REGISTERED_STREAM, ["valid_date"], "['valid_date'] are grain columns", id="grain-column"),
        pytest.param("volatile-fixture-unregistered", ["ingested_at"], "has no registered schema", id="unregistered"),
    ],
)
def test_the_loader_checks_volatile_columns_against_the_registered_stream_schema(
    tmp_path: Path, slug: str, volatile_columns: list[str], refusal: str | None
) -> None:
    """Every declared volatile column must be a non-grain column of the stream's registered schema."""
    streams = [{"slug": slug, "floor_basis": "volatile-column fixture", "volatile_columns": volatile_columns}]
    directory = write_lane_tree(tmp_path, [settled_soil_lane(LANE, streams=streams)])

    config = load_lane_configs(directory, load_region("pnw"), stream_contract=registered_stream_contract)

    if refusal is None:
        assert config.lanes[LANE].streams[0].volatile_columns == tuple(volatile_columns)
        assert LANE not in config.quarantined
    else:
        assert LANE not in config.lanes
        assert any(refusal in reason for reason in config.quarantined[LANE].reasons), config.quarantined[LANE]


def test_every_shipped_volatile_column_declaration_loads_against_the_registry() -> None:
    """The shipped `lanes/*.toml` quarantine nothing more once their volatile columns are checked."""
    region = load_region("pnw")
    unchecked = load_lane_configs(REAL_LANES_DIRECTORY, region)

    checked = load_lane_configs(REAL_LANES_DIRECTORY, region, stream_contract=registered_stream_contract)

    assert checked.quarantined == unchecked.quarantined
    declared = {stream.slug for lane in checked.lanes.values() for stream in lane.streams if stream.volatile_columns}
    assert declared  # the check above ran over real declarations, not an empty set
