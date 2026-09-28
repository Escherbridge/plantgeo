"""Pin that the legacy NASA POWER climate and soil streams still repair through the legacy writers.

Owner 2026-09-28 deferred M5 (dropping shortwave from the legacy climate turn) and N6 (removing the
climate and soil `REPAIR_BINDINGS` product blocks): both stay until each lane's TOML config
replacement is live and takes over gap repair for that stream. This file is the tripwire -- an
accidental N6 (or a partial one that strips only some streams) fails loudly here instead of surfacing
later as a silently unrepaired production gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.execution.gap_repair_contract import REPAIR_BINDINGS
from agri_data_service.execution.lane_ids import CLIMATE_DIRECT_LANE_ID, SOIL_DIRECT_LANE_ID
from agri_data_service.pipeline.direct.climate.products import CLIMATE_FIELD_PRODUCTS
from agri_data_service.pipeline.direct.soil.products import SOIL_FIELD_PRODUCTS

if TYPE_CHECKING:
    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct

#: Every legacy product this file pins, paired with the lane its `REPAIR_BINDINGS` entry must still
#: name. Read from the product tuples themselves (not restated stream literals) so a product added or
#: removed there is picked up here automatically.
_LEGACY_REPAIRED_PRODUCTS: Final = tuple(
    (product, CLIMATE_DIRECT_LANE_ID) for product in CLIMATE_FIELD_PRODUCTS
) + tuple((product, SOIL_DIRECT_LANE_ID) for product in SOIL_FIELD_PRODUCTS)


@pytest.mark.parametrize(
    ("product", "expected_lane_id"),
    _LEGACY_REPAIRED_PRODUCTS,
    ids=[product.stream for product, _ in _LEGACY_REPAIRED_PRODUCTS],
)
def test_every_legacy_climate_and_soil_stream_still_has_a_repair_binding(
    product: ClimateFieldProduct | SoilFieldProduct, expected_lane_id: str
) -> None:
    binding = REPAIR_BINDINGS.get(product.stream)
    assert binding is not None, (
        f"{product.stream} lost its REPAIR_BINDINGS entry; M5/N6 are deferred by owner 2026-09-28 until "
        f"{expected_lane_id}'s config lane replaces this stream's gap repair"
    )
    assert binding.lane_id == expected_lane_id
    assert binding.product_id == product.product_id
