"""Per-turn, per-mode caps (S19): a turn never sends a logical request that would take it past its mode's cap.

Weights come from `foundation/observability/usage.py::open_meteo_weight_for_url`, the one formula;
an unweighted provider is capped in logical requests instead. See `pipeline/runner/AGENTS.md` "Budget".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlencode

from agri_data_service.foundation.observability.usage import open_meteo_weight_for_url
from agri_data_service.pipeline.runner.contract import TurnBudgetExhaustedError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import date

    from agri_data_service.foundation.lane_config.models import LaneBudget, ProviderConfig
    from agri_data_service.pipeline.runner.contract import (
        ProviderClient,
        ProviderResponse,
        SourceRequest,
        TurnMode,
    )

BudgetBasis = Literal["weighted_calls", "requests"]


def mode_cap(budget: LaneBudget, mode: TurnMode, *, weighted_budget: int | None = None) -> int:
    """The mode's per-turn cap, lowered (never raised) by an operator or admission `--weighted-budget`."""
    cap = budget.gap_fill_max_weighted_calls if mode == "gap-fill" else budget.forward_max_weighted_calls
    if weighted_budget is not None:
        cap = min(cap, max(0, weighted_budget))
    return cap


def budget_basis(provider: ProviderConfig | None) -> BudgetBasis:
    """What the cap counts: weighted calls on a weighted provider, logical requests otherwise."""
    return "weighted_calls" if provider is not None and provider.weighted else "requests"


def weighted_calls_for_url(provider: ProviderConfig | None, url: str) -> float:
    """One logical send's weighted calls: the usage formula on a weighted provider, 0 otherwise."""
    if provider is None or not provider.weighted:
        return 0.0
    return open_meteo_weight_for_url(url)


def request_key(endpoint: str, parameters: Mapping[str, str], *, probe: bool = False) -> str:
    """The identity a logical request is counted once under, however often it is retried.

    A probe and a fan-out unit are two sends even with identical parameters, so the probe flag is
    part of the key: the provider charges both.
    """
    query = "&".join(f"{name}={value}" for name, value in sorted(parameters.items()))
    return f"{'probe:' if probe else ''}{endpoint}?{query}"


@dataclass(slots=True)
class TurnLedger:
    """What the turn spent: logical requests and weighted calls once each, every fetch attempt."""

    requests: int = 0
    weighted_calls: float = 0.0
    fetch_attempts: int = 0
    probe_requests: int = 0
    probe_weighted_calls: float = 0.0
    seen: set[str] = field(default_factory=set)


@dataclass(slots=True)
class TurnBudget:
    """The mode's cap in its basis, charged once per logical request before it is sent."""

    cap: int
    basis: BudgetBasis
    provider: ProviderConfig | None
    ledger: TurnLedger = field(default_factory=TurnLedger)

    @property
    def spent(self) -> float:
        """The cap's basis, spent so far."""
        return self.ledger.weighted_calls if self.basis == "weighted_calls" else float(self.ledger.requests)

    @property
    def remaining(self) -> float:
        """What is left of the cap."""
        return max(0.0, self.cap - self.spent)

    def cost_of_url(self, url: str) -> float:
        """One logical request's cost against the cap."""
        return weighted_calls_for_url(self.provider, url) if self.basis == "weighted_calls" else 1.0

    def cost_of(self, requests: Iterable[SourceRequest]) -> float:
        """The cost of sending `requests` that have not been sent yet this turn."""
        total = 0.0
        for request in requests:
            if request_key(request.endpoint, request.query()) in self.ledger.seen or self.provider is None:
                continue
            total += self.cost_of_url(request.credential_free_url(self.provider))
        return total

    def can_afford(self, cost: float) -> bool:
        """Whether `cost` fits under the cap (a float tolerance, never a rounding past it)."""
        return self.spent + cost <= self.cap + 1e-9

    def charge(self, endpoint: str, parameters: Mapping[str, str], *, probe: bool) -> None:
        """Charge one send: a new logical request costs its weight; a retry costs only an attempt."""
        key = request_key(endpoint, parameters, probe=probe)
        if key in self.ledger.seen:
            self.ledger.fetch_attempts += 1
            return
        url = self._url(endpoint, parameters)
        cost = self.cost_of_url(url)
        if not self.can_afford(cost):
            raise TurnBudgetExhaustedError(
                f"a {endpoint} request costing {cost:g} would take the turn past its {self.cap} {self.basis} cap "
                f"({self.spent:g} spent)"
            )
        weighted = weighted_calls_for_url(self.provider, url)
        self.ledger.seen.add(key)
        self.ledger.fetch_attempts += 1
        self.ledger.requests += 1
        self.ledger.weighted_calls += weighted
        if probe:
            self.ledger.probe_requests += 1
            self.ledger.probe_weighted_calls += weighted

    def _url(self, endpoint: str, parameters: Mapping[str, str]) -> str:
        if self.provider is None:
            return f"https://transform.invalid/{endpoint}"
        if endpoint not in self.provider.endpoints:
            raise ValueError(f"endpoint {endpoint!r} is not declared by provider {self.provider.id!r}")
        declared = self.provider.endpoints[endpoint]
        return f"https://{declared.host}{declared.path}?{urlencode(sorted(parameters.items()))}"


@dataclass(slots=True)
class BudgetedClient:
    """The provider client every strategy call goes through: it charges, and refuses, before the send."""

    inner: ProviderClient
    budget: TurnBudget

    async def get(self, endpoint: str, parameters: Mapping[str, str], *, probe: bool = False) -> ProviderResponse:
        """Charge the send against the turn's cap, then make it."""
        self.budget.charge(endpoint, parameters, probe=probe)
        return await self.inner.get(endpoint, parameters, probe=probe)


def unsent_budget() -> TurnBudget:
    """The budget of a turn that sends nothing (a transform): a zero cap, so any send is refused."""
    return TurnBudget(cap=0, basis="requests", provider=None)


@dataclass(frozen=True, slots=True)
class DaySelection:
    """The days a turn can afford, in the order offered, and the unsent units they need."""

    selected: tuple[date, ...]
    #: Days whose units would take the turn past its cap: `deferred_budget`, asked again next fire.
    deferred: tuple[date, ...]
    requests: tuple[SourceRequest, ...]
    reserved: float


def select_affordable_days(
    days: Sequence[date],
    units_by_day: Mapping[date, Sequence[SourceRequest]],
    budget: TurnBudget,
    *,
    free_units: frozenset[str] = frozenset(),
) -> DaySelection:
    """Greedy in `days` order: a day is taken only when every unit it still needs fits under what the cap has left.

    A unit shared with an already-taken day (a 14-day request) costs nothing more, and a unit in
    `free_units` (restored from a checkpoint) costs nothing at all. A day that does not fit is
    deferred whole, never half-fetched, and later cheaper days are still offered.
    """
    reserved = 0.0
    chosen: dict[str, SourceRequest] = {}
    selected: list[date] = []
    deferred: list[date] = []
    for day in days:
        needed = [
            request
            for request in units_by_day.get(day, ())
            if request.unit not in chosen and request.unit not in free_units
        ]
        cost = budget.cost_of(needed)
        if budget.can_afford(reserved + cost):
            reserved += cost
            chosen.update((request.unit, request) for request in needed)
            selected.append(day)
        else:
            deferred.append(day)
    return DaySelection(
        selected=tuple(selected), deferred=tuple(deferred), requests=tuple(chosen.values()), reserved=reserved
    )


__all__ = [
    "BudgetBasis",
    "BudgetedClient",
    "DaySelection",
    "TurnBudget",
    "TurnLedger",
    "budget_basis",
    "mode_cap",
    "request_key",
    "select_affordable_days",
    "unsent_budget",
    "weighted_calls_for_url",
]
