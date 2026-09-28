"""The S18 registration mirror: one literal data row per Parquet stream a config lane writes.

Layer L2 leaf: stdlib plus `foundation` types only, and it must never import `lane_registry.py`,
which imports it. `lane_registry.py::registration_from_config_stream_row` turns each row into a
refusing `LaneRegistration` (the runner writes; the generic exporter refuses and names the lane's
strategy module) and splices it into `LANE_REGISTRATIONS`, `LANE_REGISTRY` and
`CALENDAR_HISTORY_FLOOR`.

Rows are literals, never synthesised from `lanes/*.toml` at import: `LANE_REGISTRY` is read at
import by `execution/lane_specs.py`, `parquet_ops/authorized_serving.py::_LANES` and the calendar
floor, and the lane loader must stay lazy (spec S18, review N2). The TOML stays the one authored
fact because `tests/parquet/test_config_stream_registrations.py` holds every row equal to its lane
TOML's `[[streams]]` entry through `lane_registry.py::config_stream_mirror_violations`.

Appending a row is the whole registration step for a new stream: `w3-water-gauges` appends
`water-gauges-daily`, `p4-contract-freeze` the climate streams from the frozen contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.foundation.parquet.lane_contract import LaneNature


@dataclass(frozen=True, slots=True)
class ConfigStreamRow:
    """One config-lane stream's registration facts, copied from `lanes/<lane_id>.toml`.

    `history_floor` is the `[[streams]]` entry's own floor, or the lane's `[days] floor` when the
    entry declares none; `publication_lag_days` is the lane's `[days]` lag; `strategy` is the lane's
    S14 strategy key, carried so the refusal can name the module without reading the TOML.
    """

    slug: str
    lane_id: str
    strategy: str
    nature: LaneNature
    history_floor: date
    publication_lag_days: int
    floor_basis: str
    complete_history_floor: date | None = None


#: The shipped mirror. Empty until the first config lane that writes a new stream lands (Phase 3).
CONFIG_STREAM_ROWS: Final[tuple[ConfigStreamRow, ...]] = ()


__all__ = ["CONFIG_STREAM_ROWS", "ConfigStreamRow"]
