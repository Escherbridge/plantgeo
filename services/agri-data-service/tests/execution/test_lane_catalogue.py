"""The lane catalogue bridge (spec §4.4; CA1, CA3, CA8, CA12, CA13, H6/FR-11): every lane on exactly one path.

The legacy id set is pinned literally; the config id set is DERIVED from the real `lanes/` tree, so adding a
lane TOML (disabled or not) needs no edit here. The dispatch flows run the real `run_executor_tick`, the real
`_open_scheduled_run` and the real handler with a real child process standing in for the runner CLI; only
the ledger rows are faked (`tests/execution/soft_failure_fakes.py`), and lane TOMLs are written by
`tests/lane_config/builders.py` with the real provider files.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog.testing
from click.testing import CliRunner

from agri_data_service.execution import gap_repair, job_executor_service, lane_catalogue
from agri_data_service.execution.gap_repair import jobs_plan_gap_repair
from agri_data_service.execution.gap_repair_contract import (
    EXECUTOR_REPAIR_WORK_ITEM_KIND,
    REPAIR_BINDINGS,
    REPAIR_LANE_SUFFIX,
)
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_DEFINITION_PREFIX,
    EXECUTOR_WORK_ITEM_KIND,
    ActivationConfig,
    SoftFailureState,
    executor_inventory,
    jobs_executor,
    run_scheduled_command,
)
from agri_data_service.execution.lane_catalogue import (
    CUT_OVER_LANE_IDS,
    CUT_OVER_UNLOADED_REASON,
    GAP_FILL_DISABLED_REASON,
    GAP_FILL_SUFFIX,
    LANE_DISABLED_REASON,
    STOPPED_REASON,
    build_lane_catalogue,
    current_lane_catalogue,
)
from agri_data_service.execution.lane_ids import (
    CLIMATE_DIRECT_LANE_ID,
    DROUGHT_DIRECT_LANE_ID,
    SOIL_DIRECT_LANE_ID,
)
from agri_data_service.execution.lane_specs import (
    EXECUTOR_DEFINITION_VERSION,
    LANE_SPECS,
    STOPPED_LANES_VARIABLE,
)
from agri_data_service.foundation.lane_config import LANES_DIRECTORY_ENV_VAR, load_lane_configs
from agri_data_service.foundation.observability import events
from agri_data_service.foundation.region import load_region
from agri_data_service.jobs import JobInvocation, OpenedJobRun
from agri_data_service.parquet_ops.wire import DayRange, LaneCoverage, WarehouseCoverage
from agri_data_service.pipeline.parquet.lane_registry import CONFIG_RUNNER_MODULE
from agri_data_service.pipeline.runner.__main__ import build_parser
from tests.execution.soft_failure_fakes import (
    COMPLETE_REPORT_SCRIPT,
    HOUR,
    NOW,
    FakeWorld,
    build_lane_spec,
    events_named,
    repair_payload,
    states,
)
from tests.lane_config.builders import (
    REAL_LANES_DIRECTORY,
    merged,
    nasa_power_lane,
    settled_soil_lane,
    transform_lane,
    write_lane_tree,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping
    from pathlib import Path

    import pytest

    from agri_data_service.execution.lane_catalogue import LaneCatalogue
    from agri_data_service.execution.lane_specs import LaneExecutionSpec
    from agri_data_service.jobs import JobDefinitionRecord, JobWorkItemSpec

#: Every lane the legacy table registers, pinned literally: a legacy lane is added or retired in code,
#: and that change edits this set in the same diff.
LEGACY_LANE_IDS: Final = frozenset(
    {
        "fire-detections-direct-forward",
        "water-gauges-direct-forward",
        "mtbs-forward",
        "climate-nasa-power-direct-forward",
        "soil-era5-land-direct-forward",
        "vegetation-sentinel2-ndvi-direct-forward",
        "vegetation-ndvi-governed-plane-promotion",
        "weather-observations-direct-forward",
        "drought-direct-forward",
        "fire-perimeters-direct-forward",
        "sensors-direct-forward",
        "watersheds-direct-forward",
        "evacuation-zones-direct-forward",
        "burn-severity-direct-forward",
        "land-context-blm-forward",
        "land-context-blm-reconcile",
        "land-context-blm-backfill",
        "crop-cover-usda-maintain",
    }
)
#: S2: a migrated lane keeps its legacy id, so the config TOML claims an id the legacy table also holds.
CONFIG_LANE: Final = SOIL_DIRECT_LANE_ID
CONFIG_GAP_FILL: Final = f"{CONFIG_LANE}{GAP_FILL_SUFFIX}"
HEALTHY: Final = "healthy-probe-lane"
RETIRED_LANE: Final = "retired-lane"
CURRENT_FIRE: Final = NOW.replace(minute=0, second=0, microsecond=0)


def _config_lane(lane_id: str = CONFIG_LANE, **overrides: object) -> dict[str, object]:
    """A config-path lane whose forward and gap-fill crons both fire hourly at :00 (gap-fill flipped at G6)."""
    schedule = {
        "forward_cron": "0 * * * *",
        "gap_fill_cron": "0 * * * *",
        "gap_fill_enabled": True,
        "gap_fill_enabled_at_gate": "G6",
    }
    return settled_soil_lane(lane_id, **merged({"executor": "config", "schedule": schedule}, overrides))


def _install_lanes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *lanes: Mapping[str, object]) -> Path:
    directory = write_lane_tree(tmp_path, lanes)
    monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(directory))
    return directory


def _catalogue_of(
    tmp_path: Path, *lanes: Mapping[str, object], environment: Mapping[str, str] | None = None
) -> LaneCatalogue:
    configs = load_lane_configs(write_lane_tree(tmp_path, lanes), load_region())
    return build_lane_catalogue(legacy_specs=LANE_SPECS, configs=configs, environment=environment or {})


def _runner_script(*accepted: tuple[str, str]) -> str:
    """The runner CLI's stand-in: one clean S5 report, and exit 0 only for an argv the catalogue should build."""
    allowed = [["--lane", lane_id, "--mode", mode] for lane_id, mode in accepted]
    return (
        "import json, sys\n"
        f"allowed = {allowed!r}\n"
        "print(json.dumps({'event': 'plantgeo_lane_turn_report', 'outcome': 'completed', 'days_unwritten': 0,"
        " 'unwritten': []}))\n"
        "sys.exit(0 if sys.argv[1:] in allowed else 3)\n"
    )


class _ConfigWorld:
    """`FakeWorld` with the REAL `_open_scheduled_run`, so the CA1 marker is what reaches the ledger edge."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        specs: Mapping[str, LaneExecutionSpec],
        *,
        active: frozenset[str],
        runner_script: str,
    ) -> None:
        real_open_scheduled_run = job_executor_service._open_scheduled_run
        self.world = FakeWorld(specs, active=active).install(monkeypatch)
        self.target_partitions: dict[uuid.UUID, Mapping[str, object]] = {}
        monkeypatch.setattr(job_executor_service, "_open_scheduled_run", real_open_scheduled_run)
        monkeypatch.setattr(job_executor_service, "open_job_run", self._open_job_run)
        monkeypatch.setattr(job_executor_service, "_NEVER_RUN_FIRST_SEEN", {})
        monkeypatch.setattr(lane_catalogue, "RUNNER_COMMAND", (sys.executable, "-c", runner_script))

    async def _open_job_run(  # noqa: PLR0913 - mirrors `jobs/worker.py::open_job_run`'s keywords
        self,
        _session: object,
        definition: JobDefinitionRecord,
        *,
        logical_run_key: str,
        scheduled_for: object,
        requested_by: str,
        target_partitions: Mapping[str, object],
        work_items: tuple[JobWorkItemSpec, ...],
        max_attempts: int | None = None,
    ) -> OpenedJobRun:
        del requested_by, max_attempts
        (item,) = work_items
        lane_id = definition.name.removeprefix(EXECUTOR_DEFINITION_PREFIX)
        run = self.world.seed_run(lane_id, scheduled_for, "queued", kind=item.kind, payload=item.payload)  # type: ignore[arg-type]
        self.target_partitions[run.run_id] = dict(target_partitions)
        return OpenedJobRun(
            job_run_id=run.run_id,
            logical_run_key=logical_run_key,
            created=True,
            added_work_items=1,
            total_work_items=1,
            status="queued",
        )

    def ran_before(self, lane_id: str) -> None:
        """A settled run at the previous fire, so the lane is not a never-run lane waiting for its first fire."""
        self.world.seed_run(lane_id, CURRENT_FIRE - HOUR, "succeeded", exit_class="ok")


def _invocation(kind: str, payload: Mapping[str, object]) -> JobInvocation:
    async def heartbeat() -> bool:
        return True

    return JobInvocation(
        shard_key=CURRENT_FIRE.isoformat(),
        kind=kind,
        payload=payload,
        cursor={"state": "ready", "scheduled_for": CURRENT_FIRE.isoformat()},
        parameters={},
        attempt_number=1,
        max_attempts=5,
        progress_fraction=0.01,
        seconds_remaining=60.0,
        heartbeat=heartbeat,
    )


# --- one path per lane -----------------------------------------------------------------------------------


def test_every_real_lane_toml_puts_its_lane_on_exactly_one_path() -> None:
    """Config wins; the rest of the pinned legacy set stays legacy; a legacy-executor TOML names a legacy lane."""
    assert frozenset(LANE_SPECS) == LEGACY_LANE_IDS
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region())
    config_ids = {lane_id for lane_id, lane in configs.lanes.items() if lane.executor == "config"}
    legacy_executor_ids = {lane_id for lane_id, lane in configs.lanes.items() if lane.executor == "legacy"}

    catalogue = build_lane_catalogue(legacy_specs=LANE_SPECS, configs=configs, environment={})

    assert dict(catalogue.quarantined) == {}, "a shipped lane TOML must load"
    assert set(catalogue.config_lanes) == config_ids
    assert set(catalogue.legacy_specs) == LEGACY_LANE_IDS - config_ids
    assert not set(catalogue.legacy_specs) & set(catalogue.config_lanes)
    assert legacy_executor_ids <= LEGACY_LANE_IDS, "executor = 'legacy' needs a legacy spec to run it"


def test_a_config_toml_takes_its_lane_off_the_legacy_path_under_the_same_definition(tmp_path: Path) -> None:
    """CA13: the config forward definition keeps the lane's name and version, so a flip resumes its checkpoint."""
    catalogue = _catalogue_of(tmp_path, _config_lane())

    assert CONFIG_LANE not in catalogue.legacy_specs
    forward = catalogue.spec_named(CONFIG_LANE)
    gap_fill = catalogue.spec_named(CONFIG_GAP_FILL)
    assert forward is not None
    assert gap_fill is not None
    assert forward.definition_name == LANE_SPECS[CONFIG_LANE].definition_name
    assert forward.definition_spec().version == LANE_SPECS[CONFIG_LANE].definition_spec().version
    assert forward.definition_spec().version == EXECUTOR_DEFINITION_VERSION
    assert forward.command == (*lane_catalogue.RUNNER_COMMAND, "--lane", CONFIG_LANE, "--mode", "forward")
    assert gap_fill.command == (*lane_catalogue.RUNNER_COMMAND, "--lane", CONFIG_LANE, "--mode", "gap-fill")
    assert (forward.work_class, gap_fill.work_class) == ("incremental", "backlog")
    assert catalogue.spec_for_definition(f"{EXECUTOR_DEFINITION_PREFIX}{CONFIG_GAP_FILL}") == gap_fill


def test_a_legacy_executor_toml_leaves_its_lane_on_the_legacy_path(tmp_path: Path) -> None:
    catalogue = _catalogue_of(tmp_path, settled_soil_lane(CONFIG_LANE))

    assert catalogue.legacy_specs[CONFIG_LANE] is LANE_SPECS[CONFIG_LANE]
    assert not catalogue.is_config_lane(CONFIG_LANE)


def test_a_quarantined_lane_toml_runs_on_neither_path_and_its_siblings_still_load(tmp_path: Path) -> None:
    """S8: a TOML that fails its invariants quarantines its lane, never the process or another lane."""
    broken = settled_soil_lane(CONFIG_LANE, executor="config", days={"absence_recheck_days": 3})
    catalogue = _catalogue_of(tmp_path, broken, _config_lane("new-config-lane"))

    assert set(catalogue.quarantined) == {CONFIG_LANE}
    assert CONFIG_LANE not in catalogue.legacy_specs
    assert not catalogue.is_config_lane(CONFIG_LANE)
    assert set(catalogue.config_lanes) == {"new-config-lane"}
    assert set(catalogue.legacy_specs) == LEGACY_LANE_IDS - {CONFIG_LANE}
    assert catalogue.quarantine_reasons()[CONFIG_LANE] == (f"lanes/{CONFIG_LANE}.toml", "invalid_lane_toml")


async def test_a_quarantined_lane_s_leftover_work_item_is_refused_before_any_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_lanes(monkeypatch, tmp_path, settled_soil_lane(CONFIG_LANE, days={"absence_recheck_days": 3}))
    for process_held in ("_LANE_TURN_VERDICTS", "_LANE_EXIT_CLASSES", "_LANE_TURN_REPORTS"):
        monkeypatch.setattr(job_executor_service, process_held, {})

    outcome = await run_scheduled_command(
        _invocation(EXECUTOR_WORK_ITEM_KIND, {"lane_id": CONFIG_LANE, "scheduled_for": CURRENT_FIRE.isoformat()})
    )

    assert outcome.failure_class == "lane_quarantined"
    assert outcome.metrics["spawned"] is False


def test_a_lanes_directory_that_cannot_load_leaves_the_legacy_path_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A packaging fault (no `lanes/` in the image) is one error line per process, never an exit."""
    monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(tmp_path / "missing"))
    monkeypatch.setattr(lane_catalogue, "_LOGGED_LOAD_ERRORS", set())

    with structlog.testing.capture_logs() as logs:
        first = current_lane_catalogue(LANE_SPECS)
        second = current_lane_catalogue(LANE_SPECS)

    assert set(first.legacy_specs) == set(second.legacy_specs) == LEGACY_LANE_IDS
    assert dict(first.config_lanes) == {}
    assert first.load_error is not None
    assert "LaneDirectoryError" in first.load_error
    assert len(events_named(logs, "plantgeo_job_executor_lane_catalogue_unloaded")) == 1


def test_a_config_lane_that_declares_a_conflict_waits_while_its_partner_dispatches(tmp_path: Path) -> None:
    """The partner that already runs is the incumbent; only the declaring config lane waits."""
    catalogue = _catalogue_of(
        tmp_path,
        _config_lane(conflicts_with=[CLIMATE_DIRECT_LANE_ID]),
        nasa_power_lane(conflicts_with=[CONFIG_LANE]),
    )

    with_partner = catalogue.dispatch_activation(ActivationConfig(frozenset({CLIMATE_DIRECT_LANE_ID})))
    without_partner = catalogue.dispatch_activation(ActivationConfig(frozenset()))

    assert with_partner.active_lanes == {CLIMATE_DIRECT_LANE_ID}
    assert without_partner.active_lanes == {CONFIG_LANE, CONFIG_GAP_FILL}


# --- dispatch (CA1, CA3, CA13) ---------------------------------------------------------------------------


async def test_a_config_lane_dispatches_with_neither_a_legacy_spec_nor_an_allow_list_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CA3: TOML `enabled`, the catalogue and the kill-switch are the only gates; CA1: the ledger says so."""
    _install_lanes(monkeypatch, tmp_path, _config_lane())
    ledger = _ConfigWorld(
        monkeypatch,
        {DROUGHT_DIRECT_LANE_ID: build_lane_spec(DROUGHT_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT)},
        active=frozenset({DROUGHT_DIRECT_LANE_ID}),
        runner_script=_runner_script((CONFIG_LANE, "forward"), (CONFIG_LANE, "gap-fill")),
    )
    ledger.ran_before(CONFIG_LANE)
    ledger.ran_before(CONFIG_GAP_FILL)
    assert CONFIG_LANE not in job_executor_service.LANE_SPECS

    summary = await ledger.world.tick(soft=None)

    assert states(summary) == {
        DROUGHT_DIRECT_LANE_ID: "ran",
        CONFIG_LANE: "ran",
        CONFIG_GAP_FILL: "ran",
    }, "a child exits 3 unless it was handed `--lane <id> --mode <mode>`, so `ran` proves the runner argv"
    forward = ledger.world.latest(CONFIG_LANE)
    gap_fill = ledger.world.latest(CONFIG_GAP_FILL)
    legacy = ledger.world.latest(DROUGHT_DIRECT_LANE_ID)
    assert forward is not None
    assert gap_fill is not None
    assert legacy is not None
    assert forward.scheduled_for == gap_fill.scheduled_for == CURRENT_FIRE
    assert forward.payload["executor"] == gap_fill.payload["executor"] == "config"
    assert (forward.payload["lane_id"], forward.payload["mode"]) == (CONFIG_LANE, "forward")
    assert (gap_fill.payload["lane_id"], gap_fill.payload["mode"]) == (CONFIG_LANE, "gap-fill")
    assert ledger.target_partitions[forward.run_id]["executor"] == "config"
    assert "executor" not in legacy.payload, "a legacy item carries no config-path marker"
    assert "executor" not in ledger.target_partitions[legacy.run_id]


async def test_a_never_run_config_lane_waits_for_its_next_fire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """S9: the fire before the lane was first seen is not owed; the next one is."""
    _install_lanes(monkeypatch, tmp_path, _config_lane(schedule={"gap_fill_enabled": False}))
    ledger = _ConfigWorld(monkeypatch, {}, active=frozenset(), runner_script=_runner_script((CONFIG_LANE, "forward")))

    first = await ledger.world.tick(soft=None)
    second = await ledger.world.tick(now=NOW + HOUR, soft=None)

    assert states(first)[CONFIG_LANE] == "not_due"
    assert states(first)[CONFIG_GAP_FILL] == "shadow"
    assert states(second)[CONFIG_LANE] == "ran"
    assert [run.scheduled_for for run in ledger.world.runs_of(CONFIG_LANE)] == [CURRENT_FIRE + HOUR]
    assert ledger.world.runs_of(CONFIG_GAP_FILL) == [], "gap-fill ships off (S12) and never dispatched"


async def test_a_disabled_lane_toml_is_inventoried_but_never_dispatched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_lanes(monkeypatch, tmp_path, _config_lane(enabled=False))
    ledger = _ConfigWorld(monkeypatch, {}, active=frozenset(), runner_script=_runner_script())
    ledger.ran_before(CONFIG_LANE)

    summary = await ledger.world.tick(soft=None)
    inventory = executor_inventory(ActivationConfig(frozenset()))

    assert states(summary) == {CONFIG_LANE: "shadow", CONFIG_GAP_FILL: "shadow"}
    rows = {row["lane_id"]: row for row in inventory["lanes"]}  # type: ignore[attr-defined]
    assert rows[CONFIG_LANE]["not_dispatched_because"] == LANE_DISABLED_REASON
    assert rows[CONFIG_LANE]["active"] is False
    assert [run.status for run in ledger.world.runs_of(CONFIG_LANE)] == ["succeeded"], "nothing new was opened"


# --- the CA12 kill-switch and H6/FR-11 ---------------------------------------------------------------------


async def test_the_kill_switch_stops_a_lane_on_either_path_and_every_other_lane_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CA12: one variable, distinct from the allow-list, stops a legacy lane or a config lane and its gap-fill."""
    _install_lanes(monkeypatch, tmp_path, _config_lane())
    monkeypatch.setenv(STOPPED_LANES_VARIABLE, f"{CONFIG_LANE},{DROUGHT_DIRECT_LANE_ID}")
    ledger = _ConfigWorld(
        monkeypatch,
        {
            DROUGHT_DIRECT_LANE_ID: build_lane_spec(DROUGHT_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT),
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        },
        active=frozenset({DROUGHT_DIRECT_LANE_ID, HEALTHY}),
        runner_script=_runner_script((CONFIG_LANE, "forward"), (CONFIG_LANE, "gap-fill")),
    )
    ledger.ran_before(CONFIG_LANE)
    ledger.ran_before(CONFIG_GAP_FILL)

    summary = await ledger.world.tick(soft=None)

    assert states(summary) == {
        HEALTHY: "ran",
        DROUGHT_DIRECT_LANE_ID: "paused",
        CONFIG_LANE: "paused",
        CONFIG_GAP_FILL: "paused",
    }
    blockers = {lane.lane_id: lane.blockers for lane in summary.lanes}
    assert blockers[CONFIG_GAP_FILL] == (STOPPED_REASON,)
    assert blockers[DROUGHT_DIRECT_LANE_ID] == (f"named in {STOPPED_LANES_VARIABLE}",)
    assert ledger.world.runs_of(DROUGHT_DIRECT_LANE_ID) == []
    assert len(ledger.world.runs_of(CONFIG_LANE)) == 1, "only the seeded run: nothing was opened"

    # A work item already queued for a stopped lane is refused before any spawn, on either path.
    for payload in (
        {"lane_id": CONFIG_LANE, "scheduled_for": CURRENT_FIRE.isoformat(), "executor": "config", "mode": "forward"},
        {"lane_id": DROUGHT_DIRECT_LANE_ID, "scheduled_for": CURRENT_FIRE.isoformat()},
    ):
        outcome = await run_scheduled_command(_invocation(EXECUTOR_WORK_ITEM_KIND, payload))
        assert outcome.failure_class == "lane_stopped", payload
        assert outcome.metrics["spawned"] is False


def test_an_unknown_kill_switch_id_warns_opens_an_incident_and_never_stops_the_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H6/FR-11: the kill-switch follows the allow-list's rule (S8): quarantine the unknown id, run the rest."""
    started: dict[str, object] = {}

    async def service_loop(**kwargs: object) -> int:
        started.update(kwargs)
        return 0

    monkeypatch.setattr(job_executor_service, "_service_loop", service_loop)
    monkeypatch.setenv(STOPPED_LANES_VARIABLE, f"{RETIRED_LANE},{DROUGHT_DIRECT_LANE_ID}")
    with structlog.testing.capture_logs() as logs:
        result = CliRunner().invoke(jobs_executor, ["--once"])

    assert result.exit_code == 0, result.output
    warned = events_named(logs, events.EVENT_LANE_QUARANTINED)
    assert [(entry["lane_id"], entry["variable"], entry["reason"]) for entry in warned] == [
        (RETIRED_LANE, STOPPED_LANES_VARIABLE, "unknown")
    ]
    soft = started["soft_failure"]
    assert isinstance(soft, SoftFailureState)

    world = FakeWorld(
        {
            DROUGHT_DIRECT_LANE_ID: build_lane_spec(DROUGHT_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT),
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        }
    ).install(monkeypatch)
    summary = asyncio.run(world.tick(soft=soft))

    assert states(summary)[HEALTHY] == "ran"
    assert states(summary)[DROUGHT_DIRECT_LANE_ID] == "paused", "the known id in the same variable still stops"
    row = world.incidents.by_fingerprint(f"lane_quarantined:{RETIRED_LANE}")
    assert row is not None
    assert row["detail"] == {"lane_id": RETIRED_LANE, "reason": "unknown", "variable": STOPPED_LANES_VARIABLE}


# --- CA8: legacy repair never authors or drives a config lane ---------------------------------------------


async def test_an_open_gap_repair_run_on_a_config_lane_is_never_driven_and_its_item_never_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_lanes(monkeypatch, tmp_path, _config_lane(schedule={"gap_fill_enabled": False}))
    ledger = _ConfigWorld(
        monkeypatch,
        {
            CONFIG_LANE: build_lane_spec(CONFIG_LANE, COMPLETE_REPORT_SCRIPT),
            DROUGHT_DIRECT_LANE_ID: build_lane_spec(DROUGHT_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT),
        },
        active=frozenset({CONFIG_LANE, DROUGHT_DIRECT_LANE_ID}),
        runner_script=_runner_script((CONFIG_LANE, "forward")),
    )
    ledger.ran_before(CONFIG_LANE)
    ledger.world.seed_settled(DROUGHT_DIRECT_LANE_ID)
    config_repair = ledger.world.open_repair_run(CONFIG_LANE)
    legacy_repair = ledger.world.open_repair_run(DROUGHT_DIRECT_LANE_ID)

    summary = await ledger.world.tick(soft=None)

    lane_states = states(summary)
    assert lane_states[CONFIG_LANE] == "ran", "the config forward turn runs"
    assert f"{CONFIG_LANE}{REPAIR_LANE_SUFFIX}" not in lane_states
    assert config_repair.status == "queued", "the legacy repair run on a config lane is never driven"
    assert lane_states[f"{DROUGHT_DIRECT_LANE_ID}{REPAIR_LANE_SUFFIX}"] == "ran"
    assert legacy_repair.status == "succeeded", "a legacy lane's repair is still driven"

    outcome = await run_scheduled_command(_invocation(EXECUTOR_REPAIR_WORK_ITEM_KIND, repair_payload(CONFIG_LANE)))
    assert outcome.failure_class == "invalid_repair_request"
    assert outcome.metrics["spawned"] is False


def test_the_repair_verb_authors_nothing_for_a_config_lane(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    layer = next(layer for layer, binding in REPAIR_BINDINGS.items() if binding.lane_id == CONFIG_LANE)
    today = datetime.now(UTC).date()  # the verb judges reachability against the real clock
    coverage = WarehouseCoverage(
        generated_at=datetime.now(UTC),
        evaluated_through_day=today,
        lanes=(
            LaneCoverage(
                layer=layer,
                nature="daily_series",
                kind="observed",
                zoom=0,
                earliest_day=date(2026, 8, 1),
                latest_day=today - timedelta(days=6),
                latest_recorded_day=today - timedelta(days=6),
                published_ranges=(),
                gap_ranges=(DayRange(first_day=today - timedelta(days=12), last_day=today - timedelta(days=11)),),
                governed_absence_ranges=(),
                coverage_authority="availability",
                source_ceiling_day=today - timedelta(days=6),
                expected_horizon_day=today - timedelta(days=5),
                staleness_days=1,
                behind_provider=False,
                withheld_reason=None,
            ),
        ),
    )
    monkeypatch.setattr(gap_repair, "read_parquet_coverage", lambda *, now: coverage)  # noqa: ARG005
    monkeypatch.setattr(gap_repair, "parse_activation", lambda: ActivationConfig(frozenset({CONFIG_LANE})))

    def plan() -> dict[str, object]:
        result = CliRunner().invoke(jobs_plan_gap_repair, [])
        assert result.exit_code == 0, result.output
        return json.loads(result.output.strip().splitlines()[-1])["plan"]

    legacy = plan()
    _install_lanes(monkeypatch, tmp_path, _config_lane())
    config = plan()

    assert legacy["authorized"] == [layer], "control: on the legacy path the gap is repairable"
    assert config["authorized"] == []
    assert {candidate["layer"]: candidate["verdict"] for candidate in config["candidates"]} == {layer: "lane_inactive"}


# --- disabled gap-fill ---------------------------------------------------------------------------------------


def test_a_gap_fill_the_toml_leaves_off_is_known_to_the_brake_but_never_dispatched(tmp_path: Path) -> None:
    catalogue = _catalogue_of(tmp_path, _config_lane(schedule={"gap_fill_enabled": False}))
    gap_fill = catalogue.spec_named(CONFIG_GAP_FILL)

    assert gap_fill is not None
    assert catalogue.config_gate(gap_fill) == GAP_FILL_DISABLED_REASON
    assert catalogue.spec_for_definition(gap_fill.definition_name) == gap_fill, "the brake can still name it (CA2)"
    assert CONFIG_GAP_FILL not in catalogue.dispatch_activation(ActivationConfig(frozenset())).active_lanes


# --- review fixes: cut-over safety, gap-fill modes, the runner's command line -------------------------------


def test_a_cut_over_lane_stays_off_the_legacy_path_when_the_lanes_directory_cannot_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M2: a packaging fault must not restart a retired legacy writer beside its config successor."""
    monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(tmp_path / "missing"))
    monkeypatch.setattr(lane_catalogue, "_LOGGED_LOAD_ERRORS", set())
    monkeypatch.setattr(lane_catalogue, "CUT_OVER_LANE_IDS", frozenset({CONFIG_LANE}))

    catalogue = current_lane_catalogue(LANE_SPECS)

    assert CONFIG_LANE not in catalogue.legacy_specs
    assert catalogue.quarantined[CONFIG_LANE] == (CUT_OVER_UNLOADED_REASON,)
    assert set(catalogue.legacy_specs) == LEGACY_LANE_IDS - {CONFIG_LANE}


def test_the_cut_over_list_names_every_real_config_lane_that_took_a_legacy_id() -> None:
    """M2's list is code, so it is pinned to the real `lanes/`: a cut-over TOML and this list change together."""
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region())
    taken = {lane_id for lane_id, lane in configs.lanes.items() if lane.executor == "config" and lane_id in LANE_SPECS}

    assert taken == set(CUT_OVER_LANE_IDS)


def test_a_gap_fill_cron_on_a_lane_with_no_gap_fill_mode_quarantines_it(tmp_path: Path) -> None:
    """L4: a transform has no gap-fill turn, so its gap-fill cron would exit 78 on every fire."""
    schedule = {"forward_cron": "0 * * * *", "gap_fill_cron": "30 * * * *", "gap_fill_enabled": False}
    transform = transform_lane("fixture-transform", inputs=["new-config-lane"], executor="config", schedule=schedule)

    catalogue = _catalogue_of(tmp_path, _config_lane("new-config-lane"), transform)

    assert "fixture-transform" not in catalogue.config_lanes
    assert "has no gap-fill mode" in catalogue.quarantined["fixture-transform"][0]
    assert "new-config-lane" in catalogue.config_lanes


def test_every_config_command_parses_under_the_runner_s_own_parser(tmp_path: Path) -> None:
    """L1: the executor's argv and the runner's `build_parser` agree for all three modes, with no stub runner."""
    transform = transform_lane(
        "fixture-transform", inputs=["new-config-lane"], executor="config", schedule={"gap_fill_cron": None}
    )
    catalogue = _catalogue_of(tmp_path, _config_lane("new-config-lane"), transform)
    parser = build_parser()

    parsed = {
        (arguments.lane, arguments.mode)
        for spec in catalogue.config_specs
        if spec.command is not None
        for arguments in [parser.parse_args(list(spec.command[len(lane_catalogue.RUNNER_COMMAND) :]))]
    }

    assert lane_catalogue.RUNNER_COMMAND[2] == CONFIG_RUNNER_MODULE
    assert parsed == {
        ("new-config-lane", "forward"),
        ("new-config-lane", "gap-fill"),
        ("fixture-transform", "transform"),
    }


def test_naming_a_gap_repair_definition_stops_that_repair_and_leaves_its_forward_running() -> None:
    """L8: `<lane>:gap-repair` is a known id, so the kill-switch stops it instead of quarantining the name."""
    repair = f"{CLIMATE_DIRECT_LANE_ID}{REPAIR_LANE_SUFFIX}"

    catalogue = build_lane_catalogue(
        legacy_specs=LANE_SPECS, configs=None, environment={STOPPED_LANES_VARIABLE: repair}
    )

    assert catalogue.kill_switch.unknown == frozenset()
    assert catalogue.kill_switch.stops(repair)
    assert not catalogue.kill_switch.stops(CLIMATE_DIRECT_LANE_ID)
