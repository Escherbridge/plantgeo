"""WQ-4 admission (spec §4.9.2 "Quota enforcement", FR-37): the paid Open-Meteo cap, per lane, before `fair_due_order`.

The flows run the real `run_executor_tick`, the real handler and a real child per turn; the ledger rows are
`tests/execution/soft_failure_fakes.py`'s, extended here to answer `usage_report.py::month_to_date`'s one
statement with a pool's month-to-date spend. The line tests drive `provider_budget.admit_due_lanes`, the
admission seam the tick calls, against the same statement.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.execution import job_executor_service, lane_catalogue, provider_budget, usage_report
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_DEFINITION_PREFIX,
    SoftFailureState,
    repair_lane_spec,
    run_executor_tick,
)
from agri_data_service.execution.lane_catalogue import current_lane_catalogue
from agri_data_service.execution.lane_ids import SOIL_DIRECT_LANE_ID
from agri_data_service.execution.lane_scheduling import DueLane
from agri_data_service.execution.lane_specs import LANE_SPECS
from agri_data_service.execution.provider_budget import PAID_POOL, BudgetAdmissionState, admit_due_lanes
from agri_data_service.foundation.lane_config import LANES_DIRECTORY_ENV_VAR
from agri_data_service.jobs import JobDefinitionRecord
from tests.execution.soft_failure_fakes import (
    COMPLETE_REPORT_SCRIPT,
    HOUR,
    NOW,
    FakeWorld,
    build_lane_spec,
    states,
)
from tests.lane_config.builders import merged, settled_soil_lane, write_lane_tree

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from agri_data_service.execution.job_executor_service import ExecutorTickSummary
    from agri_data_service.execution.lane_specs import LaneExecutionSpec

#: The key's VALUE never matters to admission, only that the variable is set; a test placeholder, never a key.
KEYED: Final[Mapping[str, str]] = {"OPEN_METEO_API_KEY": "test-placeholder"}
HEALTHY: Final = "healthy-probe-lane"
PAID_CONFIG_LANE: Final = "paid-pilot-lane"
SOIL_REPAIR: Final = f"{SOIL_DIRECT_LANE_ID}:gap-repair"
CURRENT_FIRE: Final = NOW.replace(minute=0, second=0, microsecond=0)
FORWARD_STOP: Final = 4_750_000
GAP_FILL_CEILING: Final = 3_000_000
SOIL_TURN_CAP: Final = 1602
TICKS: Final = 3


def month_to_date_row(pool: str, *, charged: float, suspect: float) -> dict[str, object]:
    """One `select_provider_month_to_date.sql` row."""
    return {
        "pool": pool,
        "epoch_at": datetime(2026, 9, 1, tzinfo=UTC),
        "metered_count": 1,
        "reported_count": 0,
        "suspect_basis_count": 0,
        "not_spawned_count": 0,
        "lost_count": 0,
        "charged": charged,
        "suspect": suspect,
    }


class BudgetWorld(FakeWorld):
    """`FakeWorld` whose ledger also answers `month_to_date`, from one (charged, suspect) per pool."""

    def __init__(self, specs: Mapping[str, LaneExecutionSpec], *, spend: Mapping[str, tuple[float, float]]) -> None:
        super().__init__(specs)
        self.spend = dict(spend)
        self.month_reads: list[dict[str, object]] = []

    def answer(self, statement: object, params: dict[str, object]) -> list[dict[str, object]]:
        if statement is usage_report._SELECT_MONTH_TO_DATE:
            self.month_reads.append(params)
            charged, suspect = self.spend.get(str(params["pool"]), (0.0, 0.0))
            return [month_to_date_row(str(params["pool"]), charged=charged, suspect=suspect)]
        return super().answer(statement, params)

    async def budget_tick(
        self, *, now: datetime, budget: BudgetAdmissionState, soft: SoftFailureState | None = None, max_lanes: int = 2
    ) -> ExecutorTickSummary:
        return await run_executor_tick(
            self.session,  # type: ignore[arg-type]
            activation=self.activation,
            now=now,
            max_lanes_per_tick=max_lanes,
            soft_failure=soft,
            budget=budget,
        )


def _paid_config_lane() -> dict[str, object]:
    """A config lane on the keyed Open-Meteo archive whose forward cron fires hourly at :00; gap-fill off."""
    schedule = {"forward_cron": "0 * * * *", "gap_fill_cron": None, "gap_fill_enabled": False}
    return settled_soil_lane(PAID_CONFIG_LANE, **merged({"executor": "config", "schedule": schedule}, {}))


def _install(monkeypatch: pytest.MonkeyPatch, world: BudgetWorld, *, lanes_root: Path | None = None) -> BudgetWorld:
    if lanes_root is not None:
        monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(write_lane_tree(lanes_root, [_paid_config_lane()])))
    world.install(monkeypatch)
    monkeypatch.setattr(job_executor_service, "_NEVER_RUN_FIRST_SEEN", {})
    monkeypatch.setattr(lane_catalogue, "RUNNER_COMMAND", (sys.executable, "-c", COMPLETE_REPORT_SCRIPT))
    return world


# --- the flows through the tick ---------------------------------------------------------------------------


async def test_refused_lanes_never_take_a_selection_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two refused lanes (legacy soil and a config lane on the paid pool, both with the OLDEST checkpoints, so
    `fair_due_order` would hand them both of the tick's two slots) and one healthy lane: at 95 % charged the
    healthy lane runs on every tick, and neither refused lane ever opens a run."""
    world = _install(
        monkeypatch,
        BudgetWorld(
            {
                SOIL_DIRECT_LANE_ID: build_lane_spec(SOIL_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT),
                HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
            },
            spend={PAID_POOL: (FORWARD_STOP, 0.0)},
        ),
        lanes_root=tmp_path,
    )
    for lane_id in (SOIL_DIRECT_LANE_ID, PAID_CONFIG_LANE):
        world.seed_run(lane_id, CURRENT_FIRE - 3 * HOUR, "succeeded", exit_class="ok")
    world.seed_run(HEALTHY, CURRENT_FIRE - HOUR, "succeeded", exit_class="ok")
    budget = BudgetAdmissionState(environment=KEYED)
    soft = SoftFailureState()

    summaries = [await world.budget_tick(now=NOW + HOUR * tick, budget=budget, soft=soft) for tick in range(TICKS)]

    for summary in summaries:
        assert states(summary)[HEALTHY] == "ran"
        assert states(summary)[SOIL_DIRECT_LANE_ID] == "deferred_budget"
        assert states(summary)[PAID_CONFIG_LANE] == "deferred_budget"
    assert [run.status for run in world.runs_of(SOIL_DIRECT_LANE_ID)] == ["succeeded"], "no refused run was opened"
    assert [run.status for run in world.runs_of(PAID_CONFIG_LANE)] == ["succeeded"]
    assert len(world.runs_of(HEALTHY)) == 1 + TICKS, "the seeded run plus one per tick"
    deferred = world.incidents.by_fingerprint(f"budget_deferred:{PAID_POOL}")
    assert deferred is not None
    assert deferred["detail"]["refused"] == {PAID_CONFIG_LANE: "forward_stop", SOIL_DIRECT_LANE_ID: "forward_stop"}  # type: ignore[index]
    assert deferred["occurrence_count"] == 1, "one bump per lane per UTC day, not one per tick"
    assert len(world.month_reads) == TICKS, "the pool is read once per tick, however many lanes charge it"


async def test_suspect_charges_never_stop_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suspect spend far past both lines: legacy soil's forward turn still runs (G0's forward is never stopped
    below the 95 % CHARGED line), while its gap-repair turn is refused on the suspect basis and the pool's
    `budget_basis_suspect` incident opens."""
    world = _install(
        monkeypatch,
        BudgetWorld(
            {SOIL_DIRECT_LANE_ID: build_lane_spec(SOIL_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT)},
            spend={PAID_POOL: (100_000.0, 10_000_000.0)},
        ),
    )
    world.seed_run(SOIL_DIRECT_LANE_ID, CURRENT_FIRE - HOUR, "succeeded", exit_class="ok")
    budget = BudgetAdmissionState(environment=KEYED)
    soft = SoftFailureState()

    forward = await world.budget_tick(now=NOW, budget=budget, soft=soft)
    repair_run = world.open_repair_run(SOIL_DIRECT_LANE_ID, now=NOW + HOUR / 6)
    repair = await world.budget_tick(now=NOW + HOUR / 4, budget=budget, soft=soft)

    assert states(forward)[SOIL_DIRECT_LANE_ID] == "ran"
    latest = world.latest(SOIL_DIRECT_LANE_ID)
    assert latest is not None
    assert (latest.scheduled_for, latest.status) == (CURRENT_FIRE, "succeeded")
    assert states(repair)[SOIL_REPAIR] == "deferred_budget"
    refusal = next(lane for lane in repair.lanes if lane.lane_id == SOIL_REPAIR)
    assert refusal.detail is not None
    assert "budget_basis_suspect" in refusal.detail
    assert repair_run.status == "queued", "the refused repair run was never driven"
    suspect = world.incidents.by_fingerprint(f"budget_basis_suspect:{PAID_POOL}")
    assert suspect is not None
    assert suspect["severity"] == "warning"


async def test_the_budget_deferred_incident_resolves_on_the_pools_next_admitted_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _install(
        monkeypatch,
        BudgetWorld(
            {SOIL_DIRECT_LANE_ID: build_lane_spec(SOIL_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT)},
            spend={PAID_POOL: (FORWARD_STOP, 0.0)},
        ),
    )
    world.seed_run(SOIL_DIRECT_LANE_ID, CURRENT_FIRE - HOUR, "succeeded", exit_class="ok")
    budget = BudgetAdmissionState(environment=KEYED)
    soft = SoftFailureState()

    refused = await world.budget_tick(now=NOW, budget=budget, soft=soft)
    world.spend[PAID_POOL] = (FORWARD_STOP - 1, 0.0)
    admitted = await world.budget_tick(now=NOW + HOUR, budget=budget, soft=soft)

    assert states(refused)[SOIL_DIRECT_LANE_ID] == "deferred_budget"
    assert states(admitted)[SOIL_DIRECT_LANE_ID] == "ran"
    assert world.incidents.by_fingerprint(f"budget_deferred:{PAID_POOL}") is None
    (resolved,) = world.incidents.resolved(f"budget_deferred:{PAID_POOL}")
    assert resolved["detail"]["resolution_reason"] == "admitted_turn"  # type: ignore[index]


# --- the admission seam -------------------------------------------------------------------------------------


class _MonthToDateSession:
    """Just enough session for `admit_due_lanes`: savepoints and `month_to_date`'s statement, nothing else."""

    def __init__(self, *, charged: float, suspect: float) -> None:
        self.charged = charged
        self.suspect = suspect
        self.executed: list[tuple[object, dict[str, object]]] = []
        self.bind = None

    async def execute(self, statement: object, parameters: dict[str, object] | None = None) -> object:
        params = dict(parameters or {})
        self.executed.append((statement, params))
        if statement is not usage_report._SELECT_MONTH_TO_DATE:
            raise AssertionError(f"admission executed a statement other than month_to_date's: {statement}")
        return _Rows([month_to_date_row(str(params["pool"]), charged=self.charged, suspect=self.suspect)])

    def begin_nested(self) -> _Savepoint:
        return _Savepoint()


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _Savepoint:
    async def __aenter__(self) -> _Savepoint:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False


def _due(spec: LaneExecutionSpec) -> DueLane:
    definition = JobDefinitionRecord(
        id=uuid.uuid4(),
        name=f"{EXECUTOR_DEFINITION_PREFIX}{spec.lane_id}",
        version="2",
        handler="plantgeo.executor.command.v1",
        queue_name="default",
        concurrency_key=None,
        max_attempts=5,
        lease_seconds=600,
        time_budget_seconds=600,
        retry_policy={},  # type: ignore[arg-type]
        parameters={},
    )
    return DueLane(
        spec=spec, definition=definition, scheduled_for=CURRENT_FIRE, existing_run_id=None, last_scheduled_for=None
    )


async def _admit(
    spec: LaneExecutionSpec, *, charged: float, suspect: float, environment: Mapping[str, str] = KEYED
) -> tuple[provider_budget.AdmissionPass, _MonthToDateSession]:
    session = _MonthToDateSession(charged=charged, suspect=suspect)
    admission = await admit_due_lanes(
        session,  # type: ignore[arg-type]
        [_due(spec)],
        now=NOW,
        catalogue=current_lane_catalogue(LANE_SPECS, {}),
        state=BudgetAdmissionState(environment=environment),
    )
    return admission, session


async def test_admission_imports_month_to_date() -> None:
    """Admission reads spend through `usage_report.month_to_date` and never loads the SQL a second time: the
    ONE statement it executes is that function's own compiled statement, bound to the paid pool and the tick."""
    admission, session = await _admit(LANE_SPECS[SOIL_DIRECT_LANE_ID], charged=1_000.0, suspect=0.0)

    assert [statement for statement, _ in session.executed] == [usage_report._SELECT_MONTH_TO_DATE]
    (_, params) = session.executed[0]
    assert (params["pool"], params["now"]) == (PAID_POOL, NOW)
    (admitted,) = admission.admitted
    assert admitted.provider_pool == PAID_POOL, "an admitted paying turn carries its pool to the provider lock"


SOIL_FORWARD: Final = LANE_SPECS[SOIL_DIRECT_LANE_ID]
SOIL_GAP_REPAIR: Final = repair_lane_spec(LANE_SPECS[SOIL_DIRECT_LANE_ID])


@pytest.mark.parametrize(
    ("spec", "charged", "suspect", "refusal"),
    [
        pytest.param(SOIL_FORWARD, FORWARD_STOP - 1, 0.0, None, id="forward-just-under-95"),
        pytest.param(SOIL_FORWARD, FORWARD_STOP, 0.0, "forward_stop", id="forward-at-95"),
        pytest.param(SOIL_FORWARD, FORWARD_STOP - 1, 9_000_000.0, None, id="forward-ignores-suspect"),
        pytest.param(SOIL_GAP_REPAIR, GAP_FILL_CEILING - SOIL_TURN_CAP, 0.0, None, id="gap-fill-lands-on-60"),
        pytest.param(
            SOIL_GAP_REPAIR, GAP_FILL_CEILING - SOIL_TURN_CAP + 1, 0.0, "gap_fill_ceiling", id="gap-fill-over-60"
        ),
        pytest.param(SOIL_GAP_REPAIR, 2_000_000.0, 200_000.0, None, id="gap-fill-suspect-exactly-10-percent"),
        pytest.param(SOIL_GAP_REPAIR, 2_000_000.0, 200_001.0, "budget_basis_suspect", id="gap-fill-suspect-over-10"),
        pytest.param(
            SOIL_GAP_REPAIR, GAP_FILL_CEILING - SOIL_TURN_CAP - 5, 5.0, None, id="gap-fill-counts-suspect-in-its-line"
        ),
        pytest.param(
            SOIL_GAP_REPAIR,
            GAP_FILL_CEILING - SOIL_TURN_CAP - 4,
            5.0,
            "gap_fill_ceiling",
            id="gap-fill-suspect-tips-it",
        ),
    ],
)
async def test_gap_fill_is_refused_at_sixty_percent_and_forward_at_ninety_five_percent(
    spec: LaneExecutionSpec, charged: float, suspect: float, refusal: str | None
) -> None:
    """WQ-4's two lines at their boundaries, read from `lanes/_providers/open-meteo.toml [budget]`: gap-fill
    (legacy soil's repair, at G0's 1,602 turn cap) is admitted while charged + suspect + its turn cap is at or
    under 3,000,000; forward is refused only once CHARGED reaches 4,750,000."""
    admission, _ = await _admit(spec, charged=charged, suspect=suspect)

    if refusal is None:
        assert [candidate.spec.lane_id for candidate in admission.admitted] == [spec.lane_id]
        assert admission.refused == ()
    else:
        assert admission.admitted == ()
        (refused,) = admission.refused
        assert (refused.lane_id, refused.state) == (spec.lane_id, "deferred_budget")
        assert admission.pools[PAID_POOL].refused == {spec.lane_id: refusal}


async def test_legacy_soil_charges_nothing_while_the_key_is_unset() -> None:
    """Without `OPEN_METEO_API_KEY` soil runs on the free host, which WQ-4 meters but never caps."""
    admission, session = await _admit(SOIL_FORWARD, charged=float(FORWARD_STOP * 2), suspect=0.0, environment={})

    assert [candidate.provider_pool for candidate in admission.admitted] == [None]
    assert session.executed == [], "an uncharged lane never reads the ledger"
