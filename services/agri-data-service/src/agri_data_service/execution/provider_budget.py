"""Paid Open-Meteo admission (spec §4.9.2 "Quota enforcement", WQ-4, FR-37): the one enforced provider cap.

Admission runs per lane inside planning, before `lane_scheduling.py::fair_due_order`: a refused lane is a
`deferred_budget` result that never enters `due`, so it opens no run and never takes a selection slot. Spend
is read ONLY through `usage_report.py::month_to_date` (the single loader of its SQL). See execution/AGENTS.md,
"Budget admission".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

import structlog
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.execution.gap_repair_contract import REPAIR_LANE_SUFFIX
from agri_data_service.execution.lane_catalogue import load_config_lanes, owning_lane_id
from agri_data_service.execution.lane_ids import SOIL_DIRECT_LANE_ID
from agri_data_service.execution.turn_reports import LaneTickResult
from agri_data_service.execution.usage_report import (
    FORWARD_STOP_FRACTION,
    GAP_FILL_CEILING_FRACTION,
    PAID_MONTHLY_BUDGET,
    month_to_date,
)
from agri_data_service.foundation.lane_config import LaneDirectoryError, ProviderBudget
from agri_data_service.foundation.observability.vocabulary import LANE_LOGICAL_CAPS
from agri_data_service.ingest.open_meteo import OPEN_METEO_API_KEY_VARIABLE

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.lane_catalogue import LaneCatalogue
    from agri_data_service.execution.lane_scheduling import DueLane
    from agri_data_service.execution.lane_specs import LaneExecutionSpec

logger = structlog.get_logger(__name__)

#: Suspect spend above this share of charged spend opens `budget_basis_suspect` and refuses gap-fill (WQ-4).
SUSPECT_SHARE_LIMIT: Final = 0.10
#: The provider whose `[budget]` is the paid cap, and the pool it charges when its file cannot load.
OPEN_METEO_PROVIDER_ID: Final = "open-meteo"
PAID_POOL: Final = "open-meteo-paid"

BudgetIncidentKind = Literal["budget_deferred", "budget_basis_suspect"]
BUDGET_DEFERRED_KIND: Final = "budget_deferred"
BUDGET_BASIS_SUSPECT_KIND: Final = "budget_basis_suspect"
BUDGET_INCIDENT_KINDS: Final[frozenset[str]] = frozenset({BUDGET_DEFERRED_KIND, BUDGET_BASIS_SUSPECT_KIND})

EVENT_BUDGET_DEFERRED: Final = "plantgeo_job_executor_budget_deferred"
EVENT_BUDGET_BASIS_SUSPECT: Final = "plantgeo_job_executor_budget_basis_suspect"
EVENT_BUDGET_READ_FAILED: Final = "plantgeo_job_executor_budget_read_failed"
EVENT_BUDGET_FILE_UNLOADED: Final = "plantgeo_job_executor_budget_file_unloaded"

AdmissionMode = Literal["forward", "gap-fill"]
RefusalReason = Literal["forward_stop", "gap_fill_ceiling", "budget_basis_suspect"]


@dataclass(frozen=True, slots=True)
class BudgetedProvider:
    """One provider with a `[budget]`: the cap, and the key variable whose presence selects its paid hosts."""

    budget: ProviderBudget
    key_variable: str | None


@dataclass(frozen=True, slots=True)
class LegacyCharge:
    """A legacy lane that spends a budgeted provider, and the most one turn can spend (G0's hard cap for soil)."""

    provider_id: str
    turn_cap: int


#: The legacy map (spec §4.9.5: removed by `c7-quarantine-1` with the legacy soil lane). Legacy soil and its
#: `:gap-repair` charge the paid pool only while the provider's key (`OPEN_METEO_API_KEY`) is set: the same
#: rule `ingest/open_meteo.py::resolve_open_meteo_api_key` uses to pick the customer host.
LEGACY_CHARGED_LANES: Final[Mapping[str, LegacyCharge]] = MappingProxyType(
    {SOIL_DIRECT_LANE_ID: LegacyCharge(provider_id=OPEN_METEO_PROVIDER_ID, turn_cap=LANE_LOGICAL_CAPS["soil"])}
)


def fallback_paid_budget() -> ProviderBudget:
    """WQ-4's settled cap, used only when `lanes/_providers/open-meteo.toml` cannot load (a packaging fault).

    `usage_report.py`'s constants are pinned equal to the provider file by
    `tests/lane_config/test_provider_hosts.py::test_the_toml_budget_agrees_with_the_hand_copied_usage_report_constants`,
    so the fallback never enforces a different number than the file would.
    """
    return ProviderBudget(
        period="month",
        weighted_calls=PAID_MONTHLY_BUDGET,
        ceiling_fraction=GAP_FILL_CEILING_FRACTION,
        stop_fraction=FORWARD_STOP_FRACTION,
        charged_pool=PAID_POOL,
    )


_LOGGED_UNLOADED: set[str] = set()


def budgeted_providers() -> Mapping[str, BudgetedProvider]:
    """Provider id -> its cap, from the provider files this process loaded; a bad file never lifts the paid cap."""
    fallback = BudgetedProvider(budget=fallback_paid_budget(), key_variable=OPEN_METEO_API_KEY_VARIABLE)
    try:
        providers = load_config_lanes().providers
    except (LaneDirectoryError, ValueError) as error:
        message = f"{type(error).__name__}: {error}"
        if message not in _LOGGED_UNLOADED:
            _LOGGED_UNLOADED.add(message)
            logger.error(EVENT_BUDGET_FILE_UNLOADED, error=message, fallback_pool=PAID_POOL)
        return MappingProxyType({OPEN_METEO_PROVIDER_ID: fallback})
    budgeted = {
        provider_id: BudgetedProvider(budget=provider.budget, key_variable=provider.api_key_env)
        for provider_id, provider in providers.items()
        if provider.budget is not None
    }
    budgeted.setdefault(OPEN_METEO_PROVIDER_ID, fallback)
    return MappingProxyType(budgeted)


def budget_for_pool(pool: str) -> ProviderBudget | None:
    """The cap charged against `pool`, or None for a pool WQ-4 only meters."""
    return next(
        (provider.budget for provider in budgeted_providers().values() if provider.budget.charged_pool == pool), None
    )


@dataclass(frozen=True, slots=True)
class Charge:
    """What one definition's turn would spend: the pool, the admission mode and the turn's own cap."""

    pool: str
    mode: AdmissionMode
    turn_cap: int
    budget: ProviderBudget


def charge_for(
    spec: LaneExecutionSpec,
    catalogue: LaneCatalogue,
    *,
    environment: Mapping[str, str],
    providers: Mapping[str, BudgetedProvider],
) -> Charge | None:
    """The capped pool a definition charges, or None when it spends nothing WQ-4 caps.

    A config lane charges its provider's `[budget]` in its own mode at its TOML's per-mode cap (S19); legacy
    soil and its repair charge through `LEGACY_CHARGED_LANES`. Either charges only while the provider's key
    variable is set. Without it a legacy lane sends to the free host (metered, never capped); a config lane
    on a keyed endpoint sends nothing and exits 78 (FR-2), so it is uncharged and lands on the config ladder.
    """
    if spec.is_config:
        lane = catalogue.config_lanes.get(spec.config_lane_id or "")
        if lane is None or lane.config.source is None:
            return None
        provider_id = lane.config.source.provider
        mode: AdmissionMode = "gap-fill" if spec.config_mode == "gap-fill" else "forward"
        caps = lane.config.budget
        turn_cap = caps.gap_fill_max_weighted_calls if mode == "gap-fill" else caps.forward_max_weighted_calls
    else:
        legacy = LEGACY_CHARGED_LANES.get(owning_lane_id(spec.lane_id))
        if legacy is None:
            return None
        provider_id = legacy.provider_id
        mode = "gap-fill" if spec.lane_id.endswith(REPAIR_LANE_SUFFIX) else "forward"
        turn_cap = legacy.turn_cap
    provider = providers.get(provider_id)
    if provider is None:
        return None
    if provider.key_variable is not None and not environment.get(provider.key_variable, "").strip():
        return None
    return Charge(pool=provider.budget.charged_pool, mode=mode, turn_cap=turn_cap, budget=provider.budget)


@dataclass(frozen=True, slots=True)
class PoolSpend:
    """One pool's month-to-date spend as `month_to_date` reports it (charged and suspect are separate columns)."""

    charged: float
    suspect: float

    @property
    def basis_suspect(self) -> bool:
        """WQ-4: suspect above 10 % of charged means the charged figure cannot be trusted for gap-fill."""
        return self.suspect > SUSPECT_SHARE_LIMIT * self.charged


@dataclass(frozen=True, slots=True)
class AdmissionVerdict:
    admitted: bool
    reason: RefusalReason | None = None


def judge_admission(
    *, mode: AdmissionMode, spend: PoolSpend, budget: ProviderBudget, turn_cap: int
) -> AdmissionVerdict:
    """WQ-4's two lines: forward stops only at 95 % of CHARGED spend (suspect never stops it); gap-fill is
    admitted while charged + suspect + its turn cap stays at or under 60 %, and never on a suspect basis."""
    if mode == "forward":
        if spend.charged >= budget.forward_stop_calls:
            return AdmissionVerdict(admitted=False, reason="forward_stop")
        return AdmissionVerdict(admitted=True)
    if spend.basis_suspect:
        return AdmissionVerdict(admitted=False, reason="budget_basis_suspect")
    if spend.charged + spend.suspect + turn_cap > budget.gap_fill_ceiling_calls:
        return AdmissionVerdict(admitted=False, reason="gap_fill_ceiling")
    return AdmissionVerdict(admitted=True)


@dataclass(slots=True)
class BudgetAdmissionState:
    """What admission keeps across ticks in ONE process: the once-per-lane-per-UTC-day warn set."""

    #: The environment the key rule reads; None is this process's own (`os.environ`), read on every tick.
    environment: Mapping[str, str] | None = None
    #: (lane id, UTC day) pairs already warned this day; the `budget_deferred` bump rides the same gate.
    warned: set[tuple[str, str]] = field(default_factory=set)
    read_failures_logged: set[str] = field(default_factory=set)

    def current_environment(self) -> Mapping[str, str]:
        return os.environ if self.environment is None else self.environment


@dataclass(slots=True)
class PoolAdmission:
    """One pool's outcome this tick: its spend (None when the read failed) and who was admitted or refused."""

    pool: str
    spend: PoolSpend | None
    budget: ProviderBudget
    admitted: list[str] = field(default_factory=list)
    refused: dict[str, RefusalReason] = field(default_factory=dict)
    #: Refused lanes warned for the first time this UTC day: the only ones that bump `budget_deferred`.
    newly_warned: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AdmissionPass:
    """One tick's admission: the lanes that stay due, the `deferred_budget` results, and each pool's outcome."""

    admitted: tuple[DueLane, ...]
    refused: tuple[LaneTickResult, ...]
    pools: Mapping[str, PoolAdmission]


def _connection_invalidated(session: AsyncSession) -> bool:
    bind = getattr(session, "bind", None)
    return bool(bind is not None and getattr(bind, "invalidated", False))


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


async def _read_spend(
    session: AsyncSession, pool: str, *, now: datetime, state: BudgetAdmissionState
) -> PoolSpend | None:
    """Month to date for `pool` in its own savepoint; a failed read admits (logged once), never stops a lane.

    Only a pinned connection that lost its backend re-raises: that keeps the tick-level backoff every other
    planning statement has.
    """
    try:
        async with session.begin_nested():
            section = await month_to_date(session, pool=pool, now=now)
    except SQLAlchemyError as error:
        if _connection_invalidated(session):
            raise
        if pool not in state.read_failures_logged:
            state.read_failures_logged.add(pool)
            logger.error(EVENT_BUDGET_READ_FAILED, pool=pool, error_type=type(error).__name__, admitted=True)
        return None
    state.read_failures_logged.discard(pool)
    return PoolSpend(charged=_number(section.get("charged")), suspect=_number(section.get("suspect")))


def _refusal_detail(reason: RefusalReason, spend: PoolSpend, charge: Charge) -> str:
    line = charge.budget.forward_stop_calls if charge.mode == "forward" else charge.budget.gap_fill_ceiling_calls
    return (
        f"{BUDGET_DEFERRED_KIND}:{charge.pool} ({reason}): {charge.mode} turn refused; charged {spend.charged:.0f}, "
        f"suspect {spend.suspect:.0f}, turn cap {charge.turn_cap}, line {line}"
    )


async def admit_due_lanes(
    session: AsyncSession,
    due: Sequence[DueLane],
    *,
    now: datetime,
    catalogue: LaneCatalogue,
    state: BudgetAdmissionState,
) -> AdmissionPass:
    """Admit or refuse every due definition against its pool's month-to-date spend, before `fair_due_order`.

    Each pool is read once per tick, and only when a due lane charges it. An admitted charging lane carries
    its pool on `DueLane.provider_pool`, so the work queue takes the provider try-lock before it spends.
    """
    environment = state.current_environment()
    providers = budgeted_providers()
    day = now.date().isoformat()
    admitted: list[DueLane] = []
    refused: list[LaneTickResult] = []
    pools: dict[str, PoolAdmission] = {}
    for candidate in due:
        charge = charge_for(candidate.spec, catalogue, environment=environment, providers=providers)
        if charge is None:
            admitted.append(candidate)
            continue
        outcome = pools.get(charge.pool)
        if outcome is None:
            spend = await _read_spend(session, charge.pool, now=now, state=state)
            outcome = pools[charge.pool] = PoolAdmission(pool=charge.pool, spend=spend, budget=charge.budget)
        lane_id = candidate.spec.lane_id
        spend = outcome.spend
        verdict = (
            AdmissionVerdict(admitted=True)
            if spend is None
            else judge_admission(mode=charge.mode, spend=spend, budget=charge.budget, turn_cap=charge.turn_cap)
        )
        if spend is None or verdict.reason is None:
            outcome.admitted.append(lane_id)
            admitted.append(replace(candidate, provider_pool=charge.pool))
            continue
        outcome.refused[lane_id] = verdict.reason
        refused.append(
            LaneTickResult(
                lane_id=lane_id,
                state="deferred_budget",
                scheduled_for=candidate.scheduled_for,
                run_id=candidate.existing_run_id,
                detail=_refusal_detail(verdict.reason, spend, charge),
            )
        )
        if (lane_id, day) not in state.warned:
            state.warned.add((lane_id, day))
            outcome.newly_warned.append(lane_id)
            logger.warning(
                EVENT_BUDGET_DEFERRED,
                lane_id=lane_id,
                pool=charge.pool,
                mode=charge.mode,
                reason=verdict.reason,
                charged=spend.charged,
                suspect=spend.suspect,
                turn_cap=charge.turn_cap,
            )
    state.warned = {entry for entry in state.warned if entry[1] == day}
    return AdmissionPass(admitted=tuple(admitted), refused=tuple(refused), pools=MappingProxyType(pools))


def same_utc_month(first: datetime, second: datetime) -> bool:
    """Whether two instants fall in one UTC month: `budget_basis_suspect` resolves when the month rolls over."""
    return (first.year, first.month) == (second.year, second.month)


__all__ = [
    "BUDGET_BASIS_SUSPECT_KIND",
    "BUDGET_DEFERRED_KIND",
    "BUDGET_INCIDENT_KINDS",
    "EVENT_BUDGET_BASIS_SUSPECT",
    "EVENT_BUDGET_DEFERRED",
    "EVENT_BUDGET_READ_FAILED",
    "LEGACY_CHARGED_LANES",
    "PAID_POOL",
    "SUSPECT_SHARE_LIMIT",
    "AdmissionMode",
    "AdmissionPass",
    "AdmissionVerdict",
    "BudgetAdmissionState",
    "BudgetIncidentKind",
    "BudgetedProvider",
    "Charge",
    "LegacyCharge",
    "PoolAdmission",
    "PoolSpend",
    "admit_due_lanes",
    "budget_for_pool",
    "budgeted_providers",
    "charge_for",
    "fallback_paid_budget",
    "judge_admission",
    "same_utc_month",
]
