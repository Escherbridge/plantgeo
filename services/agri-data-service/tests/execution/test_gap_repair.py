"""Bounded gap repair: every census layer is classified, a request never exceeds its writer, the tick only drives.

No database and no object store: coverage rows are constructed, the authoring path runs against fakes for
`ensure_lane_definition` and `open_job_run`, and the tick's repair planner runs against pinned probes.
"""

# ruff: noqa: PLR2004 - assertion literals are the measured facts under test

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest
from click.testing import CliRunner

from agri_data_service.execution import gap_repair, job_executor_service
from agri_data_service.execution.gap_repair import (
    PLAN_GAP_REPAIR_COMMAND,
    author_gap_repairs,
    jobs_plan_gap_repair,
    plan_gap_repairs,
    repair_logical_run_key,
)
from agri_data_service.execution.gap_repair_contract import (
    EXECUTOR_REPAIR_WORK_ITEM_KIND,
    REPAIR_BINDINGS,
    REPAIR_EXCLUSIONS,
    REPAIR_LANE_IDS,
    RepairBudget,
    RepairRequest,
    RepairRequestError,
    select_repair_candidates,
    union_day_ranges,
)
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_DEFINITION_PREFIX,
    LANE_SPECS,
    ActivationConfig,
    DueLane,
    LatestRun,
    repair_lane_spec,
)
from agri_data_service.execution.lane_ids import (
    CLIMATE_DIRECT_LANE_ID,
    DROUGHT_DIRECT_LANE_ID,
    SENSORS_DIRECT_LANE_ID,
    SOIL_DIRECT_LANE_ID,
    VEGETATION_DIRECT_LANE_ID,
    WEATHER_OBSERVATIONS_DIRECT_LANE_ID,
)
from agri_data_service.jobs import JobDefinitionRecord, OpenedJobRun
from agri_data_service.parquet_ops.coverage import registered_census_lanes
from agri_data_service.parquet_ops.wire import DayRange, LaneCoverage, WarehouseCoverage
from agri_data_service.pipeline.direct.climate import forward as climate_forward
from agri_data_service.pipeline.direct.drought import forward as drought_forward
from agri_data_service.pipeline.direct.sensors import forward as sensors_forward
from agri_data_service.pipeline.direct.soil import forward as soil_forward
from agri_data_service.pipeline.direct.vegetation import forward as vegetation_forward
from agri_data_service.pipeline.direct.weather_observations import forward as weather_forward

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Sequence

NOW: Final = datetime(2026, 9, 15, 6, tzinfo=UTC)
TODAY: Final = NOW.date()
RUNGS: Final = (0, 5, 9, 13)
SHORTWAVE: Final = "climate-field-shortwave-radiation"
ACTIVE: Final = ActivationConfig(frozenset({CLIMATE_DIRECT_LANE_ID, VEGETATION_DIRECT_LANE_ID, DROUGHT_DIRECT_LANE_ID}))

#: Which writer's parser each repairing lane's argv must satisfy.
WRITER_PARSERS: Final[dict[str, Callable[[], argparse.ArgumentParser]]] = {
    CLIMATE_DIRECT_LANE_ID: climate_forward.parser,
    SOIL_DIRECT_LANE_ID: soil_forward.parser,
    VEGETATION_DIRECT_LANE_ID: vegetation_forward.parser,
    DROUGHT_DIRECT_LANE_ID: drought_forward.parser,
    WEATHER_OBSERVATIONS_DIRECT_LANE_ID: weather_forward.parser,
    SENSORS_DIRECT_LANE_ID: sensors_forward.parser,
}


def _rows(  # noqa: PLR0913 - one coverage fact per argument
    layer: str,
    *,
    gaps: Sequence[DayRange] = (),
    latest: date | None = TODAY - timedelta(days=10),
    withheld: str | None = None,
    behind: bool | None = False,
    nature: str = "daily_series",
) -> tuple[LaneCoverage, ...]:
    return tuple(
        LaneCoverage(
            layer=layer,
            nature=nature,  # type: ignore[arg-type]
            kind="observed",
            zoom=zoom,  # type: ignore[arg-type]
            earliest_day=None if latest is None else latest - timedelta(days=30),
            latest_day=latest,
            latest_recorded_day=latest,
            published_ranges=(),
            gap_ranges=tuple(gaps),
            governed_absence_ranges=(),
            coverage_authority="availability",
            source_ceiling_day=latest,
            expected_horizon_day=TODAY - timedelta(days=5),
            staleness_days=None if latest is None else 5,
            behind_provider=behind,
            withheld_reason=withheld,  # type: ignore[arg-type]
        )
        for zoom in RUNGS
    )


def _gap(first: date, last: date) -> DayRange:
    return DayRange(first_day=first, last_day=last)


# --- the contract ---------------------------------------------------------------------------------


def test_every_census_layer_is_either_bound_to_a_writer_or_excluded_with_a_reason() -> None:
    """A new lane must be classified; an unclassified gap is the F4 silence this path exists to end."""
    layers = {lane.layer for lane in registered_census_lanes()}
    unclassified = sorted(layers - set(REPAIR_BINDINGS) - set(REPAIR_EXCLUSIONS))
    assert unclassified == []
    assert not set(REPAIR_BINDINGS) & set(REPAIR_EXCLUSIONS), "bound and excluded are exclusive verdicts"


def test_every_binding_names_an_executable_lane_whose_writer_accepts_the_knobs_it_is_handed() -> None:
    for layer, binding in REPAIR_BINDINGS.items():
        spec = LANE_SPECS[binding.lane_id]
        assert spec.command is not None, layer
        options = {option for action in WRITER_PARSERS[binding.lane_id]()._actions for option in action.option_strings}
        assert "--max-days" in options, f"{binding.lane_id} exposes no --max-days for {layer}"
        if binding.product_id is not None:
            assert "--product" in options, f"{binding.lane_id} exposes no --product for {layer}"
        assert binding.max_days_cap >= 1
        assert binding.reachable_days >= binding.max_days_cap


def test_a_request_never_exceeds_the_writer_s_own_cap_and_is_rebuilt_only_from_a_valid_payload() -> None:
    cap = REPAIR_BINDINGS[SHORTWAVE].max_days_cap
    kwargs = {
        "lane_id": CLIMATE_DIRECT_LANE_ID,
        "layer": SHORTWAVE,
        "gap_first_day": date(2026, 6, 1),
        "gap_last_day": date(2026, 6, 30),
        "gap_day_count": 30,
        "source_ceiling_day": date(2026, 5, 31),
        "expected_horizon_day": date(2026, 7, 2),
        "authored_at": NOW,
    }
    request = RepairRequest(max_days=cap, **kwargs)  # type: ignore[arg-type]
    assert request.command_arguments() == ("--product", "shortwave-radiation", "--max-days", str(cap))
    assert RepairRequest.from_payload(request.to_payload()) == request
    assert json.dumps(request.to_payload())  # JSON-safe: it is stored in agri.job_work_item.payload

    with pytest.raises(RepairRequestError, match="outside the writer's"):
        RepairRequest(max_days=cap + 1, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(RepairRequestError, match="is repaired by lane"):
        RepairRequest(**{**kwargs, "lane_id": SOIL_DIRECT_LANE_ID}, max_days=1)  # type: ignore[arg-type]
    with pytest.raises(RepairRequestError, match="unknown repair payload version"):
        RepairRequest.from_payload({**request.to_payload(), "repair_payload_version": 0})
    with pytest.raises(RepairRequestError, match="max_days must be an integer"):
        RepairRequest.from_payload({**request.to_payload(), "max_days": "5"})


def test_overlapping_and_adjacent_gap_ranges_across_rungs_fold_into_one_run() -> None:
    folded = union_day_ranges(
        [
            _gap(date(2026, 9, 3), date(2026, 9, 4)),
            _gap(date(2026, 9, 1), date(2026, 9, 2)),
            _gap(date(2026, 9, 4), date(2026, 9, 6)),
            _gap(date(2026, 9, 10), date(2026, 9, 10)),
        ]
    )
    assert folded == (_gap(date(2026, 9, 1), date(2026, 9, 6)), _gap(date(2026, 9, 10), date(2026, 9, 10)))


# --- selection ------------------------------------------------------------------------------------


def test_each_lane_gets_exactly_one_verdict_and_only_a_bound_active_reachable_gap_is_authorized() -> None:
    horizon = TODAY - timedelta(days=75)
    rows = (
        *_rows(SHORTWAVE, gaps=[_gap(horizon - timedelta(days=31), horizon)], behind=True),
        *_rows("vegetation", gaps=[]),
        *_rows("drought", gaps=[_gap(date(2026, 9, 8), date(2026, 9, 8))], withheld="availability_stale"),
        *_rows("fire-detections", gaps=[_gap(date(2026, 8, 24), date(2026, 8, 25))]),
        *_rows("soil-field-moisture-0-7cm", gaps=[_gap(date(2026, 9, 1), date(2026, 9, 2))]),
        *_rows("weather-observations", gaps=[_gap(date(2026, 9, 6), date(2026, 9, 6))]),
    )

    plan = select_repair_candidates(rows, active_lanes=ACTIVE.active_lanes, now=NOW, budget=RepairBudget())

    verdicts = {candidate.layer: candidate.verdict for candidate in plan.candidates}
    assert verdicts == {
        SHORTWAVE: "repair_authorized",
        "vegetation": "complete",
        "drought": "coverage_withheld",
        "fire-detections": "no_repair_binding",
        "soil-field-moisture-0-7cm": "lane_inactive",
        "weather-observations": "lane_inactive",
    }
    by_layer = {candidate.layer: candidate for candidate in plan.candidates}
    assert by_layer["fire-detections"].detail == REPAIR_EXCLUSIONS["fire-detections"]
    request = by_layer[SHORTWAVE].request
    assert request is not None
    assert request.lane_id == CLIMATE_DIRECT_LANE_ID
    assert request.max_days == min(RepairBudget().max_days_per_turn, REPAIR_BINDINGS[SHORTWAVE].max_days_cap)
    assert request.gap_day_count == 32
    assert plan.authorized == (by_layer[SHORTWAVE],)


def test_a_stalled_lane_with_no_measured_gap_is_authorized_as_behind_provider() -> None:
    """THE MOTIVATING CASE: availability rows close against the publisher's own ceiling, so a stall has no gap."""
    newest = TODAY - timedelta(days=107)
    rows = tuple(
        LaneCoverage(
            layer=SHORTWAVE,
            nature="daily_series",
            kind="observed",
            zoom=zoom,  # type: ignore[arg-type]
            earliest_day=newest - timedelta(days=30),
            latest_day=newest,
            latest_recorded_day=newest,
            published_ranges=(),
            gap_ranges=(),
            governed_absence_ranges=(),
            coverage_authority="availability",
            source_ceiling_day=newest,
            expected_horizon_day=TODAY - timedelta(days=75),
            staleness_days=32,
            behind_provider=True,
        )
        for zoom in RUNGS
    )

    plan = select_repair_candidates(rows, active_lanes=ACTIVE.active_lanes, now=NOW, budget=RepairBudget())

    (candidate,) = plan.candidates
    assert candidate.verdict == "behind_provider"
    assert plan.authorized == (candidate,)
    request = candidate.request
    assert request is not None
    assert request.gap_first_day == newest + timedelta(days=1)
    assert request.gap_last_day == TODAY - timedelta(days=75)
    assert request.gap_day_count == 32
    assert request.command_arguments() == ("--product", "shortwave-radiation", "--max-days", "5")

    inactive = select_repair_candidates(rows, active_lanes=frozenset(), now=NOW, budget=RepairBudget())
    assert [candidate.verdict for candidate in inactive.candidates] == ["lane_inactive"]


def test_a_recently_authored_layer_sits_the_pass_out() -> None:
    rows = (
        *_rows(SHORTWAVE, gaps=[_gap(date(2026, 9, 10), date(2026, 9, 10))], behind=True),
        *_rows("vegetation", gaps=[_gap(date(2026, 9, 1), date(2026, 9, 5))]),
    )

    plan = select_repair_candidates(
        rows, active_lanes=ACTIVE.active_lanes, now=NOW, budget=RepairBudget(), recently_authored={SHORTWAVE}
    )

    assert {candidate.layer: candidate.verdict for candidate in plan.candidates} == {
        SHORTWAVE: "deferred_by_rotation",
        "vegetation": "repair_authorized",
    }
    assert [candidate.layer for candidate in plan.authorized] == ["vegetation"]


def test_a_gap_beyond_the_writer_s_backlog_scan_is_reported_not_sent() -> None:
    reach = REPAIR_BINDINGS["vegetation"].reachable_days
    ancient = TODAY - timedelta(days=reach + 30)
    rows = _rows("vegetation", gaps=[_gap(ancient - timedelta(days=5), ancient)])

    plan = select_repair_candidates(rows, active_lanes=ACTIVE.active_lanes, now=NOW, budget=RepairBudget())

    assert [candidate.verdict for candidate in plan.candidates] == ["unreachable_by_forward_writer"]
    assert "R3 source-direct historical verb" in plan.candidates[0].detail
    assert plan.authorized == ()


def test_the_pass_budget_orders_behind_provider_first_then_oldest_gap_and_defers_the_rest() -> None:
    rows = (
        *_rows("vegetation", gaps=[_gap(date(2026, 9, 1), date(2026, 9, 5))], behind=False),
        *_rows("drought", gaps=[_gap(date(2026, 8, 4), date(2026, 8, 4))], behind=False, nature="release_series"),
        *_rows(SHORTWAVE, gaps=[_gap(date(2026, 9, 10), date(2026, 9, 10))], behind=True),
    )

    budget = RepairBudget(max_candidates=2)
    plan = select_repair_candidates(rows, active_lanes=ACTIVE.active_lanes, now=NOW, budget=budget)

    assert [candidate.layer for candidate in plan.authorized] == [SHORTWAVE, "drought"]
    deferred = [candidate for candidate in plan.candidates if candidate.verdict == "deferred_by_budget"]
    assert [candidate.layer for candidate in deferred] == ["vegetation"]
    assert deferred[0].request is None


def test_a_budget_must_be_positive() -> None:
    with pytest.raises(RepairRequestError):
        RepairBudget(max_days_per_turn=0)
    with pytest.raises(RepairRequestError):
        RepairBudget(max_candidates=0)


# --- the repair definition -------------------------------------------------------------------------


def test_a_repair_definition_is_the_lane_s_own_command_under_a_separate_backlog_name() -> None:
    spec = LANE_SPECS[CLIMATE_DIRECT_LANE_ID]
    repair = repair_lane_spec(spec)
    assert repair.lane_id == f"{CLIMATE_DIRECT_LANE_ID}:gap-repair"
    assert repair.definition_name == f"{EXECUTOR_DEFINITION_PREFIX}{CLIMATE_DIRECT_LANE_ID}:gap-repair"
    assert repair.definition_name != spec.definition_name, "a repair run must never settle a cadence checkpoint"
    assert repair.command == spec.command
    assert repair.command_timeout_seconds == spec.command_timeout_seconds
    assert repair.work_class == "backlog"
    assert repair.schedule is None
    definition = repair.definition_spec()
    assert definition.parameters["lane_id"] == repair.lane_id
    assert repair_logical_run_key(repair, now=NOW) == f"{repair.definition_name}:2026-09-15"


def test_no_repair_lane_is_a_scheduled_lane() -> None:
    assert not any(lane_id.endswith(":gap-repair") for lane_id in LANE_SPECS)
    assert set(LANE_SPECS) >= REPAIR_LANE_IDS


# --- authoring ------------------------------------------------------------------------------------


def _definition(repair_lane_id: str) -> JobDefinitionRecord:
    return JobDefinitionRecord(
        id=uuid.uuid4(),
        name=f"{EXECUTOR_DEFINITION_PREFIX}{repair_lane_id}",
        version="2",
        handler="plantgeo.executor.command.v1",
        queue_name="default",
        concurrency_key=None,
        max_attempts=5,
        lease_seconds=1300,
        time_budget_seconds=1200,
        retry_policy={},
        parameters={},
    )


def _authorized_plan() -> gap_repair.RepairPlan:
    rows = _rows(SHORTWAVE, gaps=[_gap(date(2026, 6, 1), date(2026, 6, 30))], behind=True)
    return select_repair_candidates(rows, active_lanes=ACTIVE.active_lanes, now=NOW, budget=RepairBudget())


async def test_a_dry_run_describes_the_run_it_would_open_and_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never_open(*_args: object, **_kwargs: object) -> OpenedJobRun:
        raise AssertionError("a dry run must not reach the ledger")

    monkeypatch.setattr(gap_repair, "open_job_run", never_open)
    monkeypatch.setattr(gap_repair, "ensure_lane_definition", never_open)

    receipts = await author_gap_repairs(None, plan=_authorized_plan(), now=NOW, apply=False)

    assert [receipt.outcome for receipt in receipts] == ["dry_run"]
    receipt = receipts[0]
    assert receipt.lane_id == CLIMATE_DIRECT_LANE_ID
    assert receipt.repair_lane_id == f"{CLIMATE_DIRECT_LANE_ID}:gap-repair"
    assert receipt.shard_key == f"{SHORTWAVE}:2026-06-01:2026-06-30"
    assert receipt.run_id is None
    assert receipt.to_dict()["command_arguments"] == ["--product", "shortwave-radiation", "--max-days", "5"]


async def test_an_applied_pass_opens_one_repair_run_and_a_repeat_reports_it_already_authored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[dict[str, object]] = []
    added = iter((1, 0))

    async def fake_ensure(_session: object, spec: job_executor_service.LaneExecutionSpec) -> JobDefinitionRecord:
        return _definition(spec.lane_id)

    async def fake_open(_session: object, definition: JobDefinitionRecord, **kwargs: object) -> OpenedJobRun:
        opened.append({"definition": definition.name, **kwargs})
        return OpenedJobRun(
            job_run_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            logical_run_key=str(kwargs["logical_run_key"]),
            created=True,
            added_work_items=next(added),
            total_work_items=1,
            status="queued",
        )

    monkeypatch.setattr(gap_repair, "ensure_lane_definition", fake_ensure)
    monkeypatch.setattr(gap_repair, "open_job_run", fake_open)

    first = await author_gap_repairs(object(), plan=_authorized_plan(), now=NOW, apply=True)  # type: ignore[arg-type]
    second = await author_gap_repairs(object(), plan=_authorized_plan(), now=NOW, apply=True)  # type: ignore[arg-type]

    assert [receipt.outcome for receipt in first] == ["authored"]
    assert [receipt.outcome for receipt in second] == ["already_authored"]
    assert first[0].run_id == uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    call = opened[0]
    assert call["definition"] == f"{EXECUTOR_DEFINITION_PREFIX}{CLIMATE_DIRECT_LANE_ID}:gap-repair"
    assert call["requested_by"] == PLAN_GAP_REPAIR_COMMAND
    assert call["scheduled_for"] == NOW
    work_items = call["work_items"]
    assert isinstance(work_items, tuple)
    (item,) = work_items
    assert item.kind == EXECUTOR_REPAIR_WORK_ITEM_KIND
    assert item.payload["lane_id"] == CLIMATE_DIRECT_LANE_ID, "the payload names the OWNING lane"
    assert RepairRequest.from_payload(item.payload).command_arguments() == (
        "--product",
        "shortwave-radiation",
        "--max-days",
        "5",
    )


async def test_an_applied_pass_without_a_session_is_refused() -> None:
    with pytest.raises(RepairRequestError, match="ledger session"):
        await author_gap_repairs(None, plan=_authorized_plan(), now=NOW, apply=True)


def test_plan_gap_repairs_can_narrow_to_the_lanes_named() -> None:
    coverage = WarehouseCoverage(
        generated_at=NOW,
        evaluated_through_day=TODAY,
        lanes=(
            *_rows(SHORTWAVE, gaps=[_gap(date(2026, 9, 1), date(2026, 9, 1))]),
            *_rows("vegetation", gaps=[_gap(date(2026, 9, 1), date(2026, 9, 1))]),
        ),
    )
    plan = plan_gap_repairs(
        coverage, activation=ACTIVE, now=NOW, budget=RepairBudget(), lane_ids={VEGETATION_DIRECT_LANE_ID}
    )
    assert [candidate.layer for candidate in plan.candidates] == ["vegetation"]


# --- the tick drives, never authors ----------------------------------------------------------------


async def _plan_repairs(*, forward_due: set[str] | None = None) -> tuple[list[object], list[DueLane]]:
    """Run the tick's repair planner against the pinned ledger; the session is never touched by the fakes."""
    results, due = await job_executor_service._plan_repair_runs(
        object(),  # type: ignore[arg-type]
        ACTIVE,
        forward_due=set() if forward_due is None else forward_due,
    )
    return list(results), list(due)


def _open_repair_run(*, claimable: bool) -> LatestRun:
    return LatestRun(
        run_id=uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        scheduled_for=NOW - timedelta(minutes=5),
        status="queued",
        work_claimable=claimable,
    )


@pytest.fixture
def repair_ledger(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Pin the tick's ledger reads for the repair planner: which repair definitions exist, and their runs."""
    ledger: dict[str, object] = {"states": {}, "runs": {}, "registered": []}

    async def definition_state(_session: object, spec: job_executor_service.LaneExecutionSpec) -> object:
        return ledger["states"].get(spec.lane_id)  # type: ignore[attr-defined]

    async def load_or_register(_session: object, spec: job_executor_service.LaneExecutionSpec) -> object:
        ledger["registered"].append(spec.lane_id)  # type: ignore[attr-defined]
        return _definition(spec.lane_id)

    async def checkpoint(_session: object, spec: job_executor_service.LaneExecutionSpec) -> object:
        return ledger["runs"].get(spec.lane_id)  # type: ignore[attr-defined]

    async def rollback(_session: object) -> None:
        return None

    monkeypatch.setattr(job_executor_service, "_definition_state", definition_state)
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", load_or_register)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", checkpoint)
    monkeypatch.setattr(job_executor_service, "_rollback_planning_transaction", rollback)
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(dict(LANE_SPECS)))
    return ledger


async def test_a_lane_with_no_repair_definition_costs_one_probe_and_registers_nothing(
    repair_ledger: dict[str, object],
) -> None:
    results, due = await _plan_repairs()
    assert results == []
    assert due == []
    assert repair_ledger["registered"] == [], "the tick never creates a definition nobody asked for"


async def test_an_open_claimable_repair_run_is_driven_under_the_owning_lane_s_repair_definition(
    repair_ledger: dict[str, object],
) -> None:
    repair_id = f"{CLIMATE_DIRECT_LANE_ID}:gap-repair"
    repair_ledger["states"] = {repair_id: (uuid.uuid4(), True)}  # type: ignore[assignment]
    repair_ledger["runs"] = {repair_id: _open_repair_run(claimable=True)}  # type: ignore[assignment]

    results, due = await _plan_repairs()

    assert results == []
    assert len(due) == 1
    candidate: DueLane = due[0]
    assert candidate.spec.lane_id == repair_id
    assert candidate.spec.work_class == "backlog"
    assert candidate.existing_run_id == uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert candidate.definition.name == f"{EXECUTOR_DEFINITION_PREFIX}{repair_id}"


async def test_a_repair_waits_when_its_owning_lane_s_forward_bucket_is_due_this_tick(
    repair_ledger: dict[str, object],
) -> None:
    repair_id = f"{CLIMATE_DIRECT_LANE_ID}:gap-repair"
    repair_ledger["states"] = {repair_id: (uuid.uuid4(), True)}  # type: ignore[assignment]
    repair_ledger["runs"] = {repair_id: _open_repair_run(claimable=True)}  # type: ignore[assignment]

    results, due = await _plan_repairs(forward_due={CLIMATE_DIRECT_LANE_ID})

    assert due == []
    assert [(result.lane_id, result.state) for result in results] == [(repair_id, "deferred_fairness")]


async def test_a_settled_repair_run_is_history_and_an_unclaimable_one_is_not_due(
    repair_ledger: dict[str, object],
) -> None:
    repair_id = f"{VEGETATION_DIRECT_LANE_ID}:gap-repair"
    repair_ledger["states"] = {repair_id: (uuid.uuid4(), True)}  # type: ignore[assignment]
    settled = LatestRun(
        run_id=uuid.uuid4(), scheduled_for=NOW - timedelta(days=1), status="failed", work_claimable=False
    )
    repair_ledger["runs"] = {repair_id: settled}  # type: ignore[assignment]
    results, due = await _plan_repairs()
    assert (results, due) == ([], []), "a failed repair is a record, never re-opened by the clock"

    repair_ledger["runs"] = {repair_id: _open_repair_run(claimable=False)}  # type: ignore[assignment]
    results, due = await _plan_repairs()
    assert due == []
    assert [(result.lane_id, result.state) for result in results] == [(repair_id, "not_due")]


async def test_an_inactive_lane_s_repair_ledger_is_never_read(repair_ledger: dict[str, object]) -> None:
    repair_id = f"{SOIL_DIRECT_LANE_ID}:gap-repair"
    repair_ledger["states"] = {repair_id: (uuid.uuid4(), True)}  # type: ignore[assignment]
    repair_ledger["runs"] = {repair_id: _open_repair_run(claimable=True)}  # type: ignore[assignment]

    results, due = await _plan_repairs()

    assert (results, due) == ([], [])


# --- the verb --------------------------------------------------------------------------------------


def test_the_verb_refuses_a_lane_with_no_repair_binding_before_reading_anything() -> None:
    result = CliRunner().invoke(jobs_plan_gap_repair, ["--lane", "watersheds-direct-forward"])
    assert result.exit_code != 0
    assert "no repair binding names lane(s) watersheds-direct-forward" in result.output


def test_a_dry_run_of_the_verb_prints_the_plan_and_opens_no_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    coverage = WarehouseCoverage(
        generated_at=NOW,
        evaluated_through_day=TODAY,
        lanes=_rows(SHORTWAVE, gaps=[_gap(date(2026, 6, 1), date(2026, 6, 30))], behind=True),
    )

    def pinned_coverage(*, now: datetime) -> WarehouseCoverage:
        del now
        return coverage

    monkeypatch.setattr(gap_repair, "read_parquet_coverage", pinned_coverage)
    monkeypatch.setattr(gap_repair, "parse_activation", lambda: ACTIVE)

    def no_ledger() -> str:
        raise AssertionError("a dry run must not resolve the ledger")

    monkeypatch.setattr(gap_repair, "_resolve_ledger", no_ledger)

    result = CliRunner().invoke(jobs_plan_gap_repair, ["--max-days", "2"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output.strip().splitlines()[-1])
    assert report["event"] == "plantgeo_job_executor_gap_repair_plan"
    assert report["ledger"] is None
    assert report["plan"]["authorized"] == [SHORTWAVE]
    assert report["receipts"][0]["outcome"] == "dry_run"
    assert report["receipts"][0]["command_arguments"] == ["--product", "shortwave-radiation", "--max-days", "2"]
    assert report["freshness"]["freshness_schema_version"] == 1
