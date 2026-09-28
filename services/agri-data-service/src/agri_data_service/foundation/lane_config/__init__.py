"""Lane and provider TOML configuration: frozen models, the 5-field cron grammar, and a lazy loader.

Spec §4.1 (D1, S3, S8, S12, S14, S19). Imports only `pydantic`, stdlib and other `foundation`
packages. A ruled exception to `foundation/AGENTS.md`'s admission test, like `foundation/region/`;
see `AGENTS.md` in this directory.
"""

from agri_data_service.foundation.lane_config.cron import CronExpression, CronSyntaxError, parse_cron
from agri_data_service.foundation.lane_config.loader import (
    LANES_DIRECTORY_ENV_VAR,
    PROVIDERS_SUBDIRECTORY,
    LaneConfigSet,
    LaneDirectoryError,
    LaneQuarantine,
    ProviderLoadFailure,
    default_lanes_directory,
    load_lane_configs,
)
from agri_data_service.foundation.lane_config.models import (
    STRATEGY_ATTRIBUTE,
    STRATEGY_PACKAGE,
    LaneBudget,
    LaneConfig,
    LaneDays,
    LanePruning,
    LaneSchedule,
    LaneSource,
    LaneStream,
    ProviderBudget,
    ProviderConfig,
    ProviderEndpoint,
    ProviderWeightRule,
    SourceHistory,
    lane_requires_probe_edge,
)

__all__ = [
    "LANES_DIRECTORY_ENV_VAR",
    "PROVIDERS_SUBDIRECTORY",
    "STRATEGY_ATTRIBUTE",
    "STRATEGY_PACKAGE",
    "CronExpression",
    "CronSyntaxError",
    "LaneBudget",
    "LaneConfig",
    "LaneConfigSet",
    "LaneDays",
    "LaneDirectoryError",
    "LanePruning",
    "LaneQuarantine",
    "LaneSchedule",
    "LaneSource",
    "LaneStream",
    "ProviderBudget",
    "ProviderConfig",
    "ProviderEndpoint",
    "ProviderLoadFailure",
    "ProviderWeightRule",
    "SourceHistory",
    "default_lanes_directory",
    "lane_requires_probe_edge",
    "load_lane_configs",
    "parse_cron",
]
