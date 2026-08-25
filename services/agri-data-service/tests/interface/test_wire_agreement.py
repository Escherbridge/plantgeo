"""The serving side spells the wire exactly as the freeze does.

`tests/contract/` already binds the frozen table to the TypeScript client. This binds it to the
Python that answers, so the chain runs client -> contract -> server with no hopeful copy in it.
"""

from __future__ import annotations

from agri_data_service.foundation.parquet.lane_contract import LANE_NATURES as FOUNDATION_LANE_NATURES
from agri_data_service.foundation.parquet.paths import PARTITION_KINDS as FOUNDATION_PARTITION_KINDS
from agri_data_service.interface.http import wire
from tests.contract.wire_contract import (
    LANE_NATURES,
    PARTITION_KINDS,
    WIRE_BASE_PATH,
    WIRE_PARAMS,
    WIRE_ROUTES,
    WIRE_STATES,
)


def test_the_serving_base_path_is_the_frozen_one() -> None:
    assert wire.BASE_PATH == WIRE_BASE_PATH


def test_every_route_segment_agrees_with_the_freeze() -> None:
    served = {
        "day": wire.ROUTE_DAY,
        "window": wire.ROUTE_WINDOW,
        "release": wire.ROUTE_RELEASE,
        "coverage": wire.ROUTE_COVERAGE,
    }
    assert served == WIRE_ROUTES


def test_every_query_parameter_agrees_with_the_freeze() -> None:
    served = {
        "layer": wire.PARAM_LAYER,
        "kind": wire.PARAM_KIND,
        "zoom": wire.PARAM_ZOOM,
        "bbox": wire.PARAM_BBOX,
        "day": wire.PARAM_DAY,
        "firstDay": wire.PARAM_FIRST_DAY,
        "lastDay": wire.PARAM_LAST_DAY,
        "asOfDay": wire.PARAM_AS_OF,
    }
    assert served == WIRE_PARAMS


def test_the_four_state_names_agree_with_the_freeze() -> None:
    served = (
        wire.STATE_PUBLISHED,
        wire.STATE_GOVERNED_ABSENCE,
        wire.STATE_DAY_NOT_WRITTEN,
        wire.STATE_LANE_NEVER_WRITTEN,
    )
    assert served == WIRE_STATES


def test_the_frozen_enumerations_are_the_warehouse_s_own() -> None:
    """The contract restates `LaneNature` and `PartitionKind`; a drift here would be silent."""
    assert LANE_NATURES == FOUNDATION_LANE_NATURES
    assert PARTITION_KINDS == FOUNDATION_PARTITION_KINDS
