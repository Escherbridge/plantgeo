"""`vegetation-ndvi-governed-plane-promotion`: the DISABLED transform naming the legacy promotion's confirmed target.

Strategy key `vegetation.governed_plane_promotion` (spec S14; spec §7 row "A disabled transform. The
output target is confirmed by the author"). The legacy verb is `execution/vegetation_partition_promotion.py`
(never imported: `execution` is outside a strategy's allowed layers). Why `derive` refuses instead of
deriving: `pipeline/lanes/vegetation/AGENTS.md` "Governed-plane promotion".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.warehouse.field_products.vegetation import VEGETATION_DIRECT_KIND, VEGETATION_PRODUCT

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.pipeline.runner.contract import DayContext, Derivation

#: The one input: the forward lane's base-rung `layer=vegetation/kind=observed` day partition.
INPUT_LANE_ID: Final = "vegetation-sentinel2-ndvi-direct-forward"
INPUT_STREAM: Final = VEGETATION_PRODUCT.stream
INPUT_KIND: Final = VEGETATION_DIRECT_KIND
#: The confirmed output: `register_governed_partition_plane` writes the governed forecast plane in Postgres.
OUTPUT_TARGET: Final = "postgres:agri.forecast_observation"
#: The legacy verb's module and register seam, recorded for the parity check in the lane's tests.
LEGACY_PROMOTION_MODULE: Final = "agri_data_service.execution.vegetation_partition_promotion"
LEGACY_REGISTER_VERB: Final = "agri_data_service.execution.vegetation_ndvi_plane.register_governed_partition_plane"


class GovernedPlaneTargetNotExpressibleError(RuntimeError):
    """The promotion's output is a Postgres plane, which no config-lane `[[streams]]` entry can name."""


@dataclass(frozen=True, slots=True)
class GovernedPlanePromotionStrategy:
    """A transform that refuses every day: its target is outside the runner's Parquet-only output contract."""

    def derive(self, day: date, inputs: Mapping[str, pa.Table | None], context: DayContext) -> Derivation:
        """Always refuse (that day's `strategy_error`), naming the target and the legacy verb that owns it."""
        del inputs, context
        raise GovernedPlaneTargetNotExpressibleError(
            f"vegetation promotion {day.isoformat()}: the output target is {OUTPUT_TARGET} via {LEGACY_REGISTER_VERB}, "
            "not a Parquet stream; the legacy verb stays the only path (never armed in production)"
        )


STRATEGY: Final = GovernedPlanePromotionStrategy()

__all__ = [
    "INPUT_KIND",
    "INPUT_LANE_ID",
    "INPUT_STREAM",
    "LEGACY_PROMOTION_MODULE",
    "LEGACY_REGISTER_VERB",
    "OUTPUT_TARGET",
    "STRATEGY",
    "GovernedPlanePromotionStrategy",
    "GovernedPlaneTargetNotExpressibleError",
]
