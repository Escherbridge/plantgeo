"""Capture, prepare, publish, verify, maintain or retract the one ISRIC SoilGrids v2.0 soil-properties release.

Registration shell (WS-A A0): the parser and contract are final; every verb refuses until A1 builds it.
See `pipeline/direct/soil_properties/AGENTS.md`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.direct import (
    IDEMPOTENT_NOOP,
    LANE_DAY_OUTCOMES,
    NO_SUCH_DEFECT,
    NOT_BBOX_BOUNDED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.soil_properties.products import RELEASE_ID
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.warehouse.schemas.soil_properties import SOIL_PROPERTIES_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

#: Operator verbs, in pipeline order; `retract` is the rollback verb.
OPERATIONS: Final[tuple[str, ...]] = ("capture", "prepare", "publish", "verify", "maintain", "retract")

DEFAULT_CAPTURE_ROOT: Final = Path("data/soil-properties-captures")
DEFAULT_TIME_BUDGET_SECONDS: Final = 5_400
DEFAULT_RETRY_ATTEMPTS: Final = 4
DEFAULT_RETRY_BASE_SECONDS: Final = 2.0
DEFAULT_RETRY_MAX_SECONDS: Final = 60.0

WRITER_CONTRACT: DirectWriterContract = DirectWriterContract(
    slug=SOIL_PROPERTIES_STREAM,
    identity_defect=NO_SUCH_DEFECT,
    geometry_defect=NO_SUCH_DEFECT,
    unconfigured_bbox=NOT_BBOX_BOUNDED,
    turn_outcomes=LANE_DAY_OUTCOMES | {IDEMPOTENT_NOOP},
    flags_absent_on_purpose={
        "--bbox": (
            "The support is a pinned 0.005-degree origin lattice over (-125, 42) to (-111, 49) declared in "
            "products.py; a bbox would not narrow a query, it would change the set of cells a release is "
            "written against, and a partial lattice is not comparable with the published one."
        ),
        "--product": (
            "All thirty property-depth columns publish as one population under the all-thirty row rule; "
            "selecting a subset would write rows that break that rule."
        ),
        "--contention-timeout-seconds": (
            "The lane-day advisory lock is tried once; a contended one-off operator publication reports "
            "contended and the operator reruns the same verb."
        ),
        "--max-records": (
            "A raster lattice has no upstream record stream to cap; the population is the fixed lattice, "
            "bounded by construction below MAX_DERIVATION_ROWS."
        ),
        "--max-records-per-day": (
            "There is one version day and no per-day record stream; the lattice cell count is fixed by "
            "products.py and cannot grow."
        ),
    },
    policy_basis=(
        "One fixed ISRIC SoilGrids v2.0 release of thirty property-depth mean rasters, sampled by nearest "
        "native pixel at each 0.005-degree lattice cell centre. A cell carries no record identity and no "
        "geometry, so neither defect can occur; a cell missing any of the thirty values is omitted rather "
        "than partially published. Any source file whose Last-Modified or ETag differs from its pin refuses "
        "capture as a suspected new release, never an owed day."
    ),
)


class SoilPropertiesOperationNotBuiltError(PipelineOperationError):
    """Raised when an operator verb is declared by the shell but not yet implemented."""


class SoilPropertiesConfigError(PipelineOperationError):
    """Raised when the operator's arguments contradict the lane's one-release contract."""


def parser() -> argparse.ArgumentParser:
    """Expose the six operator verbs and every flag the writer contract declares."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("operation", choices=OPERATIONS)
    result.add_argument("--capture-dir", type=Path, default=None)
    result.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    result.add_argument("--rows-per-part", type=int, default=None)
    result.add_argument("--reference-archive", default=None)
    result.add_argument("--cogs", type=Path, default=None)
    result.add_argument("--confirm", default=None)
    result.add_argument("--time-budget-seconds", type=int, default=DEFAULT_TIME_BUDGET_SECONDS)
    result.add_argument("--retry-attempts", type=int, default=DEFAULT_RETRY_ATTEMPTS)
    result.add_argument("--retry-base-seconds", type=float, default=DEFAULT_RETRY_BASE_SECONDS)
    result.add_argument("--retry-max-seconds", type=float, default=DEFAULT_RETRY_MAX_SECONDS)
    result.add_argument("--max-days", type=int, default=1)
    result.add_argument("--run-id", default=None)
    return result


def _validate(options: argparse.Namespace) -> None:
    """Refuse arguments no verb of a single fixed release can honour."""
    if options.max_days != 1:
        raise SoilPropertiesConfigError(
            f"{RELEASE_ID} is one indivisible version day; --max-days must be 1",
            code="invalid_arguments",
            lane=SOIL_PROPERTIES_STREAM,
        )
    if options.retry_attempts < 1:
        raise SoilPropertiesConfigError(
            "--retry-attempts must be at least 1", code="invalid_arguments", lane=SOIL_PROPERTIES_STREAM
        )
    if options.retry_max_seconds < options.retry_base_seconds:
        raise SoilPropertiesConfigError(
            "--retry-max-seconds must be at least --retry-base-seconds",
            code="invalid_arguments",
            lane=SOIL_PROPERTIES_STREAM,
        )


#: Verb -> implementation. Empty in the registration shell; A1 fills it verb by verb.
OPERATION_HANDLERS: Final[Mapping[str, Callable[[argparse.Namespace], Awaitable[dict[str, object]]]]] = {}


async def run(options: argparse.Namespace) -> dict[str, object]:
    """Dispatch one operator verb; a verb with no handler refuses by name."""
    _validate(options)
    handler = OPERATION_HANDLERS.get(options.operation)
    if handler is None:
        raise SoilPropertiesOperationNotBuiltError(
            f"soil-properties `{options.operation}` is not built yet (registration shell only); "
            "see pipeline/direct/soil_properties/AGENTS.md",
            code="operation_not_built",
            lane=SOIL_PROPERTIES_STREAM,
            stage=options.operation,
        )
    return await handler(options)


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicit operator verb and print its JSON report."""
    options = parser().parse_args(argv)
    report = await run(options)
    print(json.dumps(report, sort_keys=True, default=str))
    return 0


__all__ = [
    "OPERATIONS",
    "OPERATION_HANDLERS",
    "WRITER_CONTRACT",
    "SoilPropertiesConfigError",
    "SoilPropertiesOperationNotBuiltError",
    "main",
    "parser",
    "run",
]
