"""The lane catalogue: every executor definition on exactly one path, legacy or config (spec §4.4; CA1-CA3, CA12).

Legacy definitions are `lane_specs.LANE_SPECS`; config definitions are built here from the lane TOMLs
that say `executor = "config"`, and each runs the runner CLI on its cron. Config wins, and no id is on
both paths. See `execution/AGENTS.md` "Lane catalogue".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.execution.gap_repair_contract import REPAIR_LANE_SUFFIX
from agri_data_service.execution.lane_specs import (
    COMMAND_CLEANUP_MARGIN_SECONDS,
    CONFIG_EXECUTOR,
    STOPPED_LANES_VARIABLE,
    ActivationConfig,
    ConfigTurnMode,
    ExecutorConfigurationError,
    KillSwitch,
    LaneExecutionSpec,
    parse_kill_switch,
)
from agri_data_service.foundation.lane_config import (
    LaneConfig,
    LaneConfigSet,
    LaneDirectoryError,
    default_lanes_directory,
    load_lane_configs,
)
from agri_data_service.foundation.region.manifest import load_region
from agri_data_service.pipeline.parquet.lane_registry import CONFIG_RUNNER_MODULE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

logger = structlog.get_logger(__name__)

#: The config path's child (spec §4.3). A module global so a test can pin a probe child in its place.
RUNNER_COMMAND: tuple[str, ...] = ("python", "-m", CONFIG_RUNNER_MODULE)
#: Every legacy lane id a config TOML has taken over (CA13 cut-over appends its id here in the same diff).
#: If `lanes/` cannot load, these stay OFF the legacy path, so a packaging fault never restarts a retired
#: writer beside its config successor (review M2). `test_lane_catalogue.py` pins it to the real `lanes/`.
CUT_OVER_LANE_IDS: Final[frozenset[str]] = frozenset()
#: Why a cut-over lane runs on neither path when the lanes directory failed to load.
CUT_OVER_UNLOADED_REASON: Final = "cut over to the config path, and lanes/ did not load; its legacy writer stays off"
#: Natures and kinds whose turn has no gap-fill mode: a static lookup reads its watermark, a transform its inputs.
_NO_GAP_FILL_NATURES: Final = frozenset({"static_lookup"})
#: A config lane's gap-fill fires under its own definition, `<lane>:gap-fill` (spec §4.4 "Cron").
GAP_FILL_SUFFIX: Final = ":gap-fill"
#: Why a config definition does not dispatch: its TOML says so.
LANE_DISABLED_REASON: Final = "lane TOML enabled = false"
GAP_FILL_DISABLED_REASON: Final = "gap_fill_enabled = false (S12: gap-fill ships off)"
STOPPED_REASON: Final = f"stopped by {STOPPED_LANES_VARIABLE}"
#: A lane TOML that keeps the legacy path but names no legacy spec: nothing could run it.
LEGACY_WITHOUT_SPEC_REASON: Final = "executor = 'legacy', but no legacy LaneExecutionSpec has this id"

_CONFIG_SET_CACHE: dict[tuple[Path, str], LaneConfigSet] = {}
_LOGGED_LOAD_ERRORS: set[str] = set()


def owning_lane_id(lane_id: str) -> str:
    """The lane a `<lane>:gap-fill` or `<lane>:gap-repair` definition belongs to; a plain id is its own."""
    return lane_id.partition(":")[0]


def config_lane_spec(config: LaneConfig, *, gap_fill: bool) -> LaneExecutionSpec:
    """One config definition: the runner CLI on the TOML's cron, under the lane's own definition name (CA13).

    The forward definition keeps the lane id, so a cut-over lane keeps its ledger checkpoint; a transform
    lane's forward cron dispatches `--mode transform`. The gap-fill definition is `<lane>:gap-fill`, backlog
    class, and coalesces missed fires because every gap-fill turn re-reads the whole hole list.
    """
    schedule = config.schedule
    cron = schedule.gap_fill_cron if gap_fill else schedule.forward_cron
    if cron is None:
        raise ExecutorConfigurationError(f"lane {config.id!r} declares no gap_fill_cron")
    if gap_fill and (config.kind == "transform" or config.nature in _NO_GAP_FILL_NATURES):
        # The runner has no gap-fill turn for these, so the cron would fail (or bypass S12) every fire (review L4).
        raise ExecutorConfigurationError(
            f"lane {config.id!r} declares a gap_fill_cron, "
            f"but a {config.kind} {config.nature} lane has no gap-fill mode"
        )
    mode: ConfigTurnMode = "gap-fill" if gap_fill else ("transform" if config.kind == "transform" else "forward")
    days = config.days
    return LaneExecutionSpec(
        lane_id=f"{config.id}{GAP_FILL_SUFFIX}" if gap_fill else config.id,
        conflicts_with=(),
        work_class="backlog" if gap_fill else "incremental",
        migration_disposition="source-specific",
        cadence_seconds=None,
        phase_offset_seconds=0,
        schedule=cron,
        publication_lag_days=None if days is None else days.publication_lag_days,
        publication_cadence_days=None,
        publication_lag_source=f"lanes/{config.id}.toml [days]",
        selection_policy=(
            "the runner's gap-fill hole list, capped by capability, retention and budget"
            if gap_fill
            else "the runner's forward window up to the probed or lag-derived edge"
        ),
        catch_up_policy="coalesce_latest" if gap_fill else schedule.catch_up,
        command=(*RUNNER_COMMAND, "--lane", config.id, "--mode", mode),
        command_timeout_seconds=config.budget.turn_timeout_seconds + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=f"Config-driven {mode} turn for lanes/{config.id}.toml, dispatched through the runner CLI.",
        writer_floor=None if days is None or days.floor is None else days.floor.isoformat(),
        executor=CONFIG_EXECUTOR,
        cron=cron,
        config_lane_id=config.id,
        config_mode=mode,
    )


@dataclass(frozen=True, slots=True)
class ConfigLane:
    """One `executor = "config"` lane TOML and the definitions it dispatches."""

    config: LaneConfig
    forward: LaneExecutionSpec
    #: Present when the TOML declares a `gap_fill_cron`, enabled or not, so the brake can name it (CA2).
    gap_fill: LaneExecutionSpec | None

    @property
    def specs(self) -> tuple[LaneExecutionSpec, ...]:
        return (self.forward,) if self.gap_fill is None else (self.forward, self.gap_fill)

    def disabled_reason(self, spec: LaneExecutionSpec) -> str | None:
        """Why the TOML itself does not dispatch `spec`: the lane is disabled, or its gap-fill is (S12)."""
        if not self.config.enabled:
            return LANE_DISABLED_REASON
        if spec.config_mode == "gap-fill" and not self.config.schedule.gap_fill_enabled:
            return GAP_FILL_DISABLED_REASON
        return None


@dataclass(frozen=True, slots=True)
class LaneCatalogue:
    """Every definition the executor knows, each on exactly one path, and the CA12 kill-switch over both."""

    #: `LANE_SPECS` minus every id a config TOML claims and every quarantined id.
    legacy_specs: Mapping[str, LaneExecutionSpec]
    config_lanes: Mapping[str, ConfigLane]
    #: Lane id -> why its TOML quarantines it (S8). A quarantined lane runs on NEITHER path.
    quarantined: Mapping[str, tuple[str, ...]]
    kill_switch: KillSwitch
    #: Set when the lanes directory could not load: the legacy path then runs alone, loudly.
    load_error: str | None = None

    @property
    def config_specs(self) -> tuple[LaneExecutionSpec, ...]:
        """Every config definition, forward first then gap-fill, in lane id order."""
        return tuple(spec for lane in self.config_lanes.values() for spec in lane.specs)

    def is_config_lane(self, lane_id: str) -> bool:
        """True when `lane_id` (or the lane a suffixed definition belongs to) runs on the config path."""
        return owning_lane_id(lane_id) in self.config_lanes

    def config_spec(self, lane_id: str, mode: str) -> LaneExecutionSpec | None:
        """The config definition a work item names by its owning lane and runner mode."""
        lane = self.config_lanes.get(lane_id)
        if lane is None:
            return None
        return next((spec for spec in lane.specs if spec.config_mode == mode), None)

    def spec_named(self, lane_id: str) -> LaneExecutionSpec | None:
        """A definition by its lane id on either path: a legacy id, a config id or `<config id>:gap-fill`."""
        legacy = self.legacy_specs.get(lane_id)
        if legacy is not None:
            return legacy
        lane = self.config_lanes.get(owning_lane_id(lane_id))
        if lane is None:
            return None
        return next((spec for spec in lane.specs if spec.lane_id == lane_id), None)

    def spec_for_definition(self, definition_name: str) -> LaneExecutionSpec | None:
        """The executable definition `jobs-set-lane-enabled` may brake, by its exact ledger name (CA2)."""
        return next(
            (
                spec
                for spec in (*self.legacy_specs.values(), *self.config_specs)
                if spec.definition_name == definition_name and spec.executable
            ),
            None,
        )

    def config_gate(self, spec: LaneExecutionSpec) -> str | None:
        """CA3: why a config definition may not dispatch or be superseded, or None.

        The ONLY gates are the TOML's `enabled` (and `gap_fill_enabled`), the catalogue itself and the CA12
        kill-switch: never the legacy allow-list and never a `LaneExecutionSpec` in `LANE_SPECS`.
        """
        lane = self.config_lanes.get(spec.config_lane_id or "")
        if lane is None:
            return "no config lane in the catalogue names this definition"
        disabled = lane.disabled_reason(spec)
        if disabled is not None:
            return disabled
        if self.kill_switch.stops(spec.lane_id):
            return STOPPED_REASON
        return None

    def config_conflict(self, spec: LaneExecutionSpec, legacy_activation: ActivationConfig) -> str | None:
        """A dispatch-time refusal: this config lane declares a conflict with a lane that also dispatches.

        The partner that already runs is the incumbent; only the declarer waits. When two enabled config
        lanes name each other, both wait, which is the loader's symmetric `conflicts_with` read as a rule.
        """
        lane = self.config_lanes.get(spec.config_lane_id or "")
        if lane is None:
            return None
        for partner in lane.config.conflicts_with:
            if self.kill_switch.stops(partner):
                continue
            if partner in self.legacy_specs and legacy_activation.is_active(partner):
                return f"conflicts_with {partner!r}, which the legacy path dispatches"
            partner_lane = self.config_lanes.get(partner)
            if partner_lane is not None and partner_lane.config.enabled:
                return f"conflicts_with {partner!r}, an enabled config lane"
        return None

    def legacy_activation(self, activation: ActivationConfig) -> ActivationConfig:
        """The allow-list narrowed to the legacy path: config-claimed, quarantined and stopped ids leave it.

        Every legacy repair planner reads this, so legacy repair authoring and driving skip a config lane
        (CA8) and a stopped lane (CA12) without an edit of their own.
        """
        return replace(
            activation,
            active_lanes=frozenset(
                lane_id
                for lane_id in activation.active_lanes
                if lane_id in self.legacy_specs and not self.kill_switch.stops(lane_id)
            ),
        )

    def dispatch_activation(self, activation: ActivationConfig) -> ActivationConfig:
        """Every definition id this tick may dispatch: the legacy path plus each config definition that passes."""
        legacy = self.legacy_activation(activation)
        config = frozenset(
            spec.lane_id
            for spec in self.config_specs
            if self.config_gate(spec) is None and self.config_conflict(spec, legacy) is None
        )
        return replace(legacy, active_lanes=legacy.active_lanes | config)

    def quarantine_reasons(self) -> dict[str, tuple[str, str]]:
        """Lane id -> (the variable or file that named it, the reason): TOML quarantines and unknown kill-switch ids."""
        reasons = {lane_id: (f"lanes/{lane_id}.toml", "invalid_lane_toml") for lane_id in self.quarantined}
        for lane_id in self.kill_switch.unknown:
            reasons.setdefault(lane_id, (STOPPED_LANES_VARIABLE, "unknown"))
        return reasons

    def inventory_rows(self, activation: ActivationConfig) -> list[dict[str, object]]:
        """The `--inventory` rows: legacy rows as before, then config definitions, then quarantined lane TOMLs."""
        legacy = self.legacy_activation(activation)
        rows = [spec.inventory_row(active=legacy.is_active(spec.lane_id)) for spec in self.legacy_specs.values()]
        for spec in self.config_specs:
            refusal = self.config_gate(spec) or self.config_conflict(spec, legacy)
            rows.append({**spec.inventory_row(active=refusal is None), "not_dispatched_because": refusal})
        rows.extend(
            {"lane_id": lane_id, "active": False, "quarantined": list(reasons)}
            for lane_id, reasons in self.quarantined.items()
        )
        return rows


def _config_lane(config: LaneConfig) -> ConfigLane:
    gap_fill = None if config.schedule.gap_fill_cron is None else config_lane_spec(config, gap_fill=True)
    return ConfigLane(config=config, forward=config_lane_spec(config, gap_fill=False), gap_fill=gap_fill)


def build_lane_catalogue(
    *,
    legacy_specs: Mapping[str, LaneExecutionSpec],
    configs: LaneConfigSet | None,
    environment: Mapping[str, str] | None = None,
    load_error: str | None = None,
) -> LaneCatalogue:
    """Split every lane onto exactly one path: a config TOML wins over a legacy spec of the same id."""
    config_lanes: dict[str, ConfigLane] = {}
    quarantined: dict[str, tuple[str, ...]] = {}
    if configs is None:
        quarantined.update(dict.fromkeys(sorted(CUT_OVER_LANE_IDS), (CUT_OVER_UNLOADED_REASON,)))
    else:
        quarantined.update({lane_id: entry.reasons for lane_id, entry in configs.quarantined.items()})
        for lane_id, config in configs.lanes.items():
            if config.executor != CONFIG_EXECUTOR:
                if lane_id not in legacy_specs:
                    quarantined[lane_id] = (LEGACY_WITHOUT_SPEC_REASON,)
                continue
            try:
                config_lanes[lane_id] = _config_lane(config)
            except ExecutorConfigurationError as error:
                quarantined[lane_id] = (str(error),)
    legacy = {
        lane_id: spec
        for lane_id, spec in legacy_specs.items()
        if lane_id not in config_lanes and lane_id not in quarantined
    }
    known = {
        *legacy_specs,
        # A legacy lane's `:gap-repair` definition: naming it stops the repair and leaves the forward (L8).
        *(f"{lane_id}{REPAIR_LANE_SUFFIX}" for lane_id in legacy),
        *quarantined,
        *(spec.lane_id for lane in config_lanes.values() for spec in lane.specs),
    }
    return LaneCatalogue(
        legacy_specs=MappingProxyType(legacy),
        config_lanes=MappingProxyType(dict(sorted(config_lanes.items()))),
        quarantined=MappingProxyType(dict(sorted(quarantined.items()))),
        kill_switch=parse_kill_switch(frozenset(known), environment),
        load_error=load_error,
    )


def load_config_lanes() -> LaneConfigSet:
    """The lane TOMLs under the configured directory for the active region, parsed once per process.

    The directory is baked into the image (S13), so it never changes under a running process; the cache
    is keyed by (directory, region) so a test that points `PLANTGEO_LANES_DIRECTORY` elsewhere reloads.
    """
    directory = default_lanes_directory()
    region = load_region()
    key = (directory, region.slug)
    cached = _CONFIG_SET_CACHE.get(key)
    if cached is None:
        cached = load_lane_configs(directory, region)
        _CONFIG_SET_CACHE[key] = cached
    return cached


def current_lane_catalogue(
    legacy_specs: Mapping[str, LaneExecutionSpec], environment: Mapping[str, str] | None = None
) -> LaneCatalogue:
    """The catalogue this process dispatches from. A lanes directory that cannot load leaves the legacy
    path running alone and logs one error per process (a packaging fault, never an exit)."""
    try:
        configs = load_config_lanes()
    except (LaneDirectoryError, ValueError) as error:
        message = f"{type(error).__name__}: {error}"
        if message not in _LOGGED_LOAD_ERRORS:
            _LOGGED_LOAD_ERRORS.add(message)
            logger.error("plantgeo_job_executor_lane_catalogue_unloaded", error=message)
        return build_lane_catalogue(
            legacy_specs=legacy_specs, configs=None, environment=environment, load_error=message
        )
    return build_lane_catalogue(legacy_specs=legacy_specs, configs=configs, environment=environment)


__all__ = [
    "CUT_OVER_LANE_IDS",
    "CUT_OVER_UNLOADED_REASON",
    "GAP_FILL_DISABLED_REASON",
    "GAP_FILL_SUFFIX",
    "LANE_DISABLED_REASON",
    "LEGACY_WITHOUT_SPEC_REASON",
    "RUNNER_COMMAND",
    "STOPPED_REASON",
    "ConfigLane",
    "LaneCatalogue",
    "build_lane_catalogue",
    "config_lane_spec",
    "current_lane_catalogue",
    "load_config_lanes",
    "owning_lane_id",
]
