"""S4: the runner's exit codes, and the one mapping from a raised error to its code.

`execution/exit_classes.py::classify_exit` reads these natively (75 upstream, 70 code, 78 config).
See `pipeline/runner/AGENTS.md` "Exit codes".
"""

from __future__ import annotations

from typing import Final

from agri_data_service.foundation.lane_config.loader import LaneDirectoryError
from agri_data_service.pipeline.runner.contract import ProviderConfigurationError
from agri_data_service.pipeline.runner.resolve import StrategyResolutionError
from agri_data_service.pipeline.runner.windows import GapFillDisabledError
from agri_data_service.pipeline.runner.writer import CompareModeWriteError, UnregisteredStreamError

#: Completed; days left unwritten are in the report, never in the exit code.
EXIT_COMPLETED: Final = 0
#: Every unit the turn sent ended upstream-unavailable: the breaker's upstream ladder.
EXIT_UPSTREAM_UNAVAILABLE: Final = 75
#: Anything unexpected: the code ladder.
EXIT_INTERNAL_ERROR: Final = 70
#: The lane, its provider or its invocation is wrong: a named configuration error.
EXIT_CONFIGURATION_ERROR: Final = 78


class TurnConfigurationError(Exception):
    """The turn cannot run as asked: an unknown or quarantined lane, a mode its kind forbids, a refused option."""


_CONFIGURATION_ERRORS: Final = (
    TurnConfigurationError,
    StrategyResolutionError,
    GapFillDisabledError,
    ProviderConfigurationError,
    UnregisteredStreamError,
    CompareModeWriteError,
    LaneDirectoryError,
)


def exit_code_for(error: BaseException) -> int:
    """S4 for a turn that raised: a configuration fault is 78, everything else is 70."""
    return EXIT_CONFIGURATION_ERROR if isinstance(error, _CONFIGURATION_ERRORS) else EXIT_INTERNAL_ERROR


__all__ = [
    "EXIT_COMPLETED",
    "EXIT_CONFIGURATION_ERROR",
    "EXIT_INTERNAL_ERROR",
    "EXIT_UPSTREAM_UNAVAILABLE",
    "TurnConfigurationError",
    "exit_code_for",
]
