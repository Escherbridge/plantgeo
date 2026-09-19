"""What one bounded gap repair IS: which writer owns a measured gap, the request a work item carries, how one is chosen.

A LEAF beneath the scheduler: `job_executor_service.py` imports this to validate and run a repair work
item, and `gap_repair.py` imports it to author one, so nothing here may import either. Rationale and
the RUNBOOK directive it obeys live in `execution/AGENTS.md`, "Bounded gap repair".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.execution.lane_ids import (
    BURN_SEVERITY_DIRECT_LANE_ID,
    CLIMATE_DIRECT_LANE_ID,
    DROUGHT_DIRECT_LANE_ID,
    FIRE_DETECTIONS_DIRECT_LANE_ID,
    SENSORS_DIRECT_LANE_ID,
    SOIL_DIRECT_LANE_ID,
    VEGETATION_DIRECT_LANE_ID,
    WATER_GAUGES_DIRECT_LANE_ID,
    WEATHER_OBSERVATIONS_DIRECT_LANE_ID,
)
from agri_data_service.parquet_ops.wire import DayRange, render_day, render_instant
from agri_data_service.pipeline.direct.climate.forward import CLIMATE_BACKLOG_SCAN_DAYS, CLIMATE_MAX_DAYS
from agri_data_service.pipeline.direct.climate.products import CLIMATE_FIELD_PRODUCTS
from agri_data_service.pipeline.direct.drought.forward import DROUGHT_BACKLOG_SCAN_WEEKS, DROUGHT_MAX_DAYS
from agri_data_service.pipeline.direct.sensors.forward import SENSORS_MAX_DAYS
from agri_data_service.pipeline.direct.soil.forward import SOIL_BACKLOG_SCAN_DAYS, SOIL_MAX_DAYS
from agri_data_service.pipeline.direct.soil.products import SOIL_FIELD_PRODUCTS
from agri_data_service.pipeline.direct.vegetation.forward import VEGETATION_BACKLOG_SCAN_DAYS, VEGETATION_MAX_DAYS
from agri_data_service.pipeline.direct.weather_observations.forward import WEATHER_OBSERVATIONS_MAX_DAYS

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    from agri_data_service.parquet_ops.wire import LaneCoverage
    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct

#: The work item kind a repair run carries; `run_scheduled_command` accepts exactly this and the scheduled kind.
EXECUTOR_REPAIR_WORK_ITEM_KIND: Final = "gap-repair-command"
#: Appended to the owning lane's id to name its repair definition, so a repair run can never be mistaken
#: for a cadence checkpoint by `select_latest_run.sql`.
REPAIR_LANE_SUFFIX: Final = ":gap-repair"
REPAIR_PAYLOAD_VERSION: Final = 1
#: One turn's `--max-days`, before each writer's own cap is applied. Five is the largest cap any writer declares.
REPAIR_DEFAULT_MAX_DAYS_PER_TURN: Final = 5
#: How many lanes one authoring pass may hand work to. Two matches the tick's `MIN_LANES_PER_TICK`.
REPAIR_DEFAULT_MAX_CANDIDATES: Final = 2
DAYS_PER_WEEK: Final = 7

RepairVerdict = Literal[
    "repair_authorized",
    "behind_provider",
    "deferred_by_budget",
    "deferred_by_rotation",
    "complete",
    "coverage_withheld",
    "no_repair_binding",
    "lane_inactive",
    "unreachable_by_forward_writer",
]


class RepairRequestError(ValueError):
    """A repair payload the executor refuses to turn into a command."""


@dataclass(frozen=True, slots=True)
class RepairBinding:
    """How one census layer's measured gaps reach the writer that owns the layer, and how far that writer can see."""

    lane_id: str
    #: `--product` value for a writer that fans one source out to several streams; `None` for a one-stream writer.
    product_id: str | None
    #: The writer's own `--max-days` ceiling. A repair never asks for more than the writer would accept.
    max_days_cap: int
    #: How many days behind today the writer's backlog scan reaches. A gap wholly older than this is not
    #: repairable by the forward writer at all, and is reported rather than sent.
    reachable_days: int
    basis: str


def _product_bindings(
    products: Iterable[ClimateFieldProduct | SoilFieldProduct],
    *,
    lane_id: str,
    max_days_cap: int,
    reachable_days: int,
    basis: str,
) -> dict[str, RepairBinding]:
    return {
        product.stream: RepairBinding(
            lane_id=lane_id,
            product_id=product.product_id,
            max_days_cap=max_days_cap,
            reachable_days=reachable_days,
            basis=basis,
        )
        for product in products
    }


REPAIR_BINDINGS: Final[Mapping[str, RepairBinding]] = MappingProxyType(
    {
        **_product_bindings(
            CLIMATE_FIELD_PRODUCTS,
            lane_id=CLIMATE_DIRECT_LANE_ID,
            max_days_cap=CLIMATE_MAX_DAYS,
            reachable_days=CLIMATE_BACKLOG_SCAN_DAYS,
            basis="pipeline/direct/climate/forward.py CLIMATE_BACKLOG_SCAN_DAYS; newest unfilled settled day first",
        ),
        **_product_bindings(
            SOIL_FIELD_PRODUCTS,
            lane_id=SOIL_DIRECT_LANE_ID,
            max_days_cap=SOIL_MAX_DAYS,
            reachable_days=SOIL_BACKLOG_SCAN_DAYS,
            basis="pipeline/direct/soil/forward.py SOIL_BACKLOG_SCAN_DAYS; newest unfilled settled day first",
        ),
        "vegetation": RepairBinding(
            lane_id=VEGETATION_DIRECT_LANE_ID,
            product_id=None,
            max_days_cap=VEGETATION_MAX_DAYS,
            reachable_days=VEGETATION_BACKLOG_SCAN_DAYS,
            basis="pipeline/direct/vegetation/forward.py VEGETATION_BACKLOG_SCAN_DAYS",
        ),
        "drought": RepairBinding(
            lane_id=DROUGHT_DIRECT_LANE_ID,
            product_id=None,
            max_days_cap=DROUGHT_MAX_DAYS,
            reachable_days=DROUGHT_BACKLOG_SCAN_WEEKS * DAYS_PER_WEEK,
            basis="pipeline/direct/drought/forward.py DROUGHT_BACKLOG_SCAN_WEEKS, one USDM release per --max-days unit",
        ),
        "weather-observations": RepairBinding(
            lane_id=WEATHER_OBSERVATIONS_DIRECT_LANE_ID,
            product_id=None,
            max_days_cap=WEATHER_OBSERVATIONS_MAX_DAYS,
            reachable_days=WEATHER_OBSERVATIONS_MAX_DAYS,
            basis="a current-conditions feed: only the buckets one poll touches can ever be re-observed",
        ),
        "sensors": RepairBinding(
            lane_id=SENSORS_DIRECT_LANE_ID,
            product_id=None,
            max_days_cap=SENSORS_MAX_DAYS,
            reachable_days=SENSORS_MAX_DAYS,
            basis="pipeline/direct/sensors/forward.py SENSORS_MAX_DAYS: the NWS rolling retention window",
        ),
    }
)

#: Layers whose gaps the census CAN measure and this path deliberately does not send anywhere, with the reason.
REPAIR_EXCLUSIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "fire-detections": (
            f"{FIRE_DETECTIONS_DIRECT_LANE_ID} exposes no --max-days; its five-day NRT window self-heals and "
            "older days need the R3 source-direct historical verb"
        ),
        "water-gauges": (
            f"{WATER_GAUGES_DIRECT_LANE_ID} exposes no --max-days; a gap outside its rolling window needs the "
            "R3 source-direct historical verb"
        ),
        "burn-severity": (
            f"{BURN_SEVERITY_DIRECT_LANE_ID} publishes release cohorts, not days; R3 reconciles cohort "
            "semantics before any repair is authored"
        ),
        "fire-perimeters": "static_lookup: a version stamp has no owed day to repair",
        "evacuation-zones": "static_lookup: a version stamp has no owed day to repair",
        "watersheds": "static_lookup: a version stamp has no owed day to repair",
        "soil-survey": "static_lookup with no admitted publisher yet (R4)",
        "fire-risk": "forecast-originated: services/plantgeo-ml-service writes this lane, not an agri direct writer",
        "weather-forecast": (
            "forecast-originated: services/plantgeo-ml-service writes this lane, not an agri direct writer"
        ),
    }
)

#: Every executor lane at least one layer repairs through. The tick consults this before reading a repair ledger.
REPAIR_LANE_IDS: Final[frozenset[str]] = frozenset(binding.lane_id for binding in REPAIR_BINDINGS.values())


@dataclass(frozen=True, slots=True)
class RepairBudget:
    """What one authoring pass may hand out: days per turn per lane, and lanes per pass."""

    max_days_per_turn: int = REPAIR_DEFAULT_MAX_DAYS_PER_TURN
    max_candidates: int = REPAIR_DEFAULT_MAX_CANDIDATES

    def __post_init__(self) -> None:
        if self.max_days_per_turn < 1:
            raise RepairRequestError("max_days_per_turn must be at least 1")
        if self.max_candidates < 1:
            raise RepairRequestError("max_candidates must be at least 1")


@dataclass(frozen=True, slots=True)
class RepairRequest:
    """The bounded arguments one repair work item hands to the owning lane's OWN writer, and the evidence behind them.

    Constructed only by `select_repair_candidates` or `from_payload`; both paths run the same bounds.
    """

    lane_id: str
    layer: str
    max_days: int
    gap_first_day: date
    gap_last_day: date
    gap_day_count: int
    source_ceiling_day: date | None
    expected_horizon_day: date | None
    authored_at: datetime

    def __post_init__(self) -> None:
        binding = REPAIR_BINDINGS.get(self.layer)
        if binding is None:
            raise RepairRequestError(f"layer {self.layer!r} has no repair binding")
        if binding.lane_id != self.lane_id:
            raise RepairRequestError(
                f"layer {self.layer!r} is repaired by lane {binding.lane_id!r}, not {self.lane_id!r}"
            )
        if not 1 <= self.max_days <= binding.max_days_cap:
            raise RepairRequestError(
                f"max_days {self.max_days} for {self.layer!r} is outside the writer's 1..{binding.max_days_cap}"
            )
        if self.gap_last_day < self.gap_first_day:
            raise RepairRequestError("the gap range runs backwards")
        if self.gap_day_count < 1:
            raise RepairRequestError("a repair must name at least one owed day")
        if self.authored_at.utcoffset() is None:
            raise RepairRequestError("authored_at must include a timezone")

    @property
    def binding(self) -> RepairBinding:
        return REPAIR_BINDINGS[self.layer]

    def command_arguments(self) -> tuple[str, ...]:
        """The arguments appended to the lane's own command: only knobs that writer already accepts."""
        product = () if self.binding.product_id is None else ("--product", self.binding.product_id)
        return (*product, "--max-days", str(self.max_days))

    def shard_key(self) -> str:
        return f"{self.layer}:{render_day(self.gap_first_day)}:{render_day(self.gap_last_day)}"

    def to_payload(self) -> dict[str, object]:
        return {
            "repair_payload_version": REPAIR_PAYLOAD_VERSION,
            "lane_id": self.lane_id,
            "layer": self.layer,
            "max_days": self.max_days,
            "gap_first_day": render_day(self.gap_first_day),
            "gap_last_day": render_day(self.gap_last_day),
            "gap_day_count": self.gap_day_count,
            "source_ceiling_day": None if self.source_ceiling_day is None else render_day(self.source_ceiling_day),
            "expected_horizon_day": (
                None if self.expected_horizon_day is None else render_day(self.expected_horizon_day)
            ),
            "authored_at": render_instant(self.authored_at),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> RepairRequest:
        """Rebuild a request from a stored payload, refusing any shape the constructor would not have produced."""
        if payload.get("repair_payload_version") != REPAIR_PAYLOAD_VERSION:
            raise RepairRequestError("unknown repair payload version")
        try:
            return cls(
                lane_id=_text(payload, "lane_id"),
                layer=_text(payload, "layer"),
                max_days=_integer(payload, "max_days"),
                gap_first_day=date.fromisoformat(_text(payload, "gap_first_day")),
                gap_last_day=date.fromisoformat(_text(payload, "gap_last_day")),
                gap_day_count=_integer(payload, "gap_day_count"),
                source_ceiling_day=_optional_day(payload, "source_ceiling_day"),
                expected_horizon_day=_optional_day(payload, "expected_horizon_day"),
                authored_at=datetime.fromisoformat(_text(payload, "authored_at")),
            )
        except RepairRequestError:
            raise
        except (TypeError, ValueError) as error:
            raise RepairRequestError(f"malformed repair payload: {error}") from error


@dataclass(frozen=True, slots=True)
class LaneGapFacts:
    """What the census measured about one lane, before any verdict: the same on every verdict it can reach."""

    layer: str
    kind: str
    lane_id: str | None
    gap_ranges: tuple[DayRange, ...]
    gap_day_count: int
    latest_recorded_day: date | None
    source_ceiling_day: date | None
    expected_horizon_day: date | None
    staleness_days: int | None
    behind_provider: bool | None
    #: The first withholding any rung states; unproven coverage authors no work.
    withheld_reason: str | None = None

    @property
    def oldest_gap_day(self) -> date | None:
        return self.gap_ranges[0].first_day if self.gap_ranges else None

    @property
    def newest_gap_day(self) -> date | None:
        return self.gap_ranges[-1].last_day if self.gap_ranges else None


@dataclass(frozen=True, slots=True)
class RepairCandidate:
    """One census lane's measured gaps and the single verdict this path reached about them."""

    facts: LaneGapFacts
    verdict: RepairVerdict
    detail: str
    request: RepairRequest | None = None

    @property
    def layer(self) -> str:
        return self.facts.layer

    @property
    def lane_id(self) -> str | None:
        return self.facts.lane_id

    def to_dict(self) -> dict[str, object]:
        facts = self.facts
        return {
            "layer": facts.layer,
            "kind": facts.kind,
            "lane_id": facts.lane_id,
            "verdict": self.verdict,
            "detail": self.detail,
            "gap_ranges": [entry.to_wire() for entry in facts.gap_ranges],
            "gap_day_count": facts.gap_day_count,
            "latest_recorded_day": None if facts.latest_recorded_day is None else render_day(facts.latest_recorded_day),
            "source_ceiling_day": None if facts.source_ceiling_day is None else render_day(facts.source_ceiling_day),
            "expected_horizon_day": (
                None if facts.expected_horizon_day is None else render_day(facts.expected_horizon_day)
            ),
            "staleness_days": facts.staleness_days,
            "behind_provider": facts.behind_provider,
            "request": None if self.request is None else self.request.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """Every census lane's verdict, the authorized subset first."""

    candidates: tuple[RepairCandidate, ...]
    budget: RepairBudget

    @property
    def authorized(self) -> tuple[RepairCandidate, ...]:
        """The candidates handed a turn this pass: a measured hole, or a lane behind its provider with none."""
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.verdict in AUTHORIZING_VERDICTS and candidate.request is not None
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "budget": {
                "max_days_per_turn": self.budget.max_days_per_turn,
                "max_candidates": self.budget.max_candidates,
            },
            "authorized": [candidate.layer for candidate in self.authorized],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


#: The two verdicts that carry a request and may be handed a turn.
AUTHORIZING_VERDICTS: Final[frozenset[str]] = frozenset({"repair_authorized", "behind_provider"})


def select_repair_candidates(
    rows: Sequence[LaneCoverage],
    *,
    active_lanes: AbstractSet[str],
    now: datetime,
    budget: RepairBudget,
    recently_authored: AbstractSet[str] = frozenset(),
) -> RepairPlan:
    """Turn coverage rows into bounded repair verdicts: which lanes get a turn, and why every other one does not.

    The rows are the census's Parquet-derived evidence and the only input; nothing here reads a
    database or lists an object prefix. Lanes flagged `behind_provider` are ordered first, then the
    oldest gap, so an unsettled newest day cannot starve an older repair of its turn (R3). A layer in
    `recently_authored` sits this pass out (`deferred_by_rotation`) so a persistently unfillable layer
    cannot take the budget every pass.
    """
    today = now.astimezone(UTC).date()
    grouped: dict[tuple[str, str], list[LaneCoverage]] = {}
    for row in rows:
        grouped.setdefault((row.layer, row.kind), []).append(row)
    judged = [
        _judge(measure_lane_gaps(lane_rows), active_lanes=active_lanes, today=today, now=now, budget=budget)
        for lane_rows in grouped.values()
    ]
    authorizing = [candidate for candidate in judged if candidate.verdict in AUTHORIZING_VERDICTS]
    rotated_out = [candidate for candidate in authorizing if candidate.layer in recently_authored]
    eligible = sorted(
        (candidate for candidate in authorizing if candidate.layer not in recently_authored),
        key=lambda candidate: (
            not candidate.facts.behind_provider,
            candidate.facts.oldest_gap_day or today,
            candidate.layer,
        ),
    )
    ordered: list[RepairCandidate] = list(eligible[: budget.max_candidates])
    ordered.extend(
        RepairCandidate(
            facts=candidate.facts,
            verdict="deferred_by_budget",
            detail=f"authorized, but this pass hands work to at most {budget.max_candidates} lane(s)",
        )
        for candidate in eligible[budget.max_candidates :]
    )
    ordered.extend(
        RepairCandidate(
            facts=candidate.facts,
            verdict="deferred_by_rotation",
            detail="authorized, but this layer was authored recently and the pass looks past it",
        )
        for candidate in rotated_out
    )
    ordered.extend(
        sorted(
            (candidate for candidate in judged if candidate.verdict not in AUTHORIZING_VERDICTS),
            key=lambda candidate: (candidate.verdict, candidate.layer),
        )
    )
    return RepairPlan(candidates=tuple(ordered), budget=budget)


def measure_lane_gaps(rows: Sequence[LaneCoverage]) -> LaneGapFacts:
    """Fold one lane's rung rows into the facts a verdict reads; a hole on ANY rung is a hole in the lane."""
    first = rows[0]
    gaps = union_day_ranges(entry for row in rows for entry in row.gap_ranges)
    binding = REPAIR_BINDINGS.get(first.layer)
    flagged = [row.behind_provider for row in rows if row.behind_provider is not None]
    return LaneGapFacts(
        layer=first.layer,
        kind=first.kind,
        lane_id=None if binding is None else binding.lane_id,
        gap_ranges=gaps,
        gap_day_count=sum((entry.last_day - entry.first_day).days + 1 for entry in gaps),
        latest_recorded_day=_newest(row.latest_recorded_day for row in rows),
        source_ceiling_day=_newest(row.source_ceiling_day for row in rows),
        expected_horizon_day=_newest(row.expected_horizon_day for row in rows),
        staleness_days=max((row.staleness_days for row in rows if row.staleness_days is not None), default=None),
        behind_provider=any(flagged) if flagged else None,
        withheld_reason=next((row.withheld_reason for row in rows if row.withheld_reason is not None), None),
    )


def _judge(  # noqa: PLR0911 - one return per verdict word
    facts: LaneGapFacts,
    *,
    active_lanes: AbstractSet[str],
    today: date,
    now: datetime,
    budget: RepairBudget,
) -> RepairCandidate:
    if facts.withheld_reason is not None:
        return RepairCandidate(
            facts=facts,
            verdict="coverage_withheld",
            detail=f"coverage is {facts.withheld_reason}; unproven coverage authors no work",
        )
    binding = REPAIR_BINDINGS.get(facts.layer)
    if binding is None:
        detail = REPAIR_EXCLUSIONS.get(facts.layer, "no executor lane repairs this layer through a bounded knob")
        return RepairCandidate(facts=facts, verdict="no_repair_binding", detail=detail)
    if not facts.gap_ranges and not facts.behind_provider:
        return RepairCandidate(facts=facts, verdict="complete", detail="no gap measured through the lane's own ceiling")
    if binding.lane_id not in active_lanes:
        return RepairCandidate(
            facts=facts,
            verdict="lane_inactive",
            detail=(
                f"{binding.lane_id} is not in the active allow-list; activation is the only control that runs a writer"
            ),
        )
    if not facts.gap_ranges:
        return _behind_provider(facts, binding=binding, now=now, budget=budget)
    reach_floor = today - timedelta(days=binding.reachable_days)
    newest_gap = facts.newest_gap_day
    if newest_gap is not None and newest_gap < reach_floor:
        return RepairCandidate(
            facts=facts,
            verdict="unreachable_by_forward_writer",
            detail=(
                f"every gap ends before {render_day(reach_floor)}, the oldest day {binding.lane_id}'s backlog scan "
                f"reaches ({binding.basis}); needs the R3 source-direct historical verb"
            ),
        )
    request = RepairRequest(
        lane_id=binding.lane_id,
        layer=facts.layer,
        max_days=min(budget.max_days_per_turn, binding.max_days_cap),
        gap_first_day=facts.gap_ranges[0].first_day,
        gap_last_day=facts.gap_ranges[-1].last_day,
        gap_day_count=facts.gap_day_count,
        source_ceiling_day=facts.source_ceiling_day,
        expected_horizon_day=facts.expected_horizon_day,
        authored_at=now,
    )
    return RepairCandidate(
        facts=facts,
        verdict="repair_authorized",
        detail=(
            f"{binding.lane_id} runs its own writer with {' '.join(request.command_arguments())}; the writer "
            "selects the days"
        ),
        request=request,
    )


def _behind_provider(
    facts: LaneGapFacts, *, binding: RepairBinding, now: datetime, budget: RepairBudget
) -> RepairCandidate:
    """Authorize a lane that measures NO gap yet sits behind its provider: the stalled-publisher case.

    Availability rows close against the publisher's own ceiling, so a stalled lane's `gap_ranges` is empty
    by construction (the shortwave shape). The span handed to the writer is the days between its newest
    recorded day and the horizon its registered lag predicts; the writer's own newest-first selection
    decides which of them are settled. A distinct verdict word so the tick log separates it from a hole.
    """
    if facts.latest_recorded_day is None or facts.expected_horizon_day is None:
        return RepairCandidate(
            facts=facts, verdict="complete", detail="behind its provider but with no recorded edge to measure from"
        )
    first = facts.latest_recorded_day + timedelta(days=1)
    last = max(first, facts.expected_horizon_day)
    request = RepairRequest(
        lane_id=binding.lane_id,
        layer=facts.layer,
        max_days=min(budget.max_days_per_turn, binding.max_days_cap),
        gap_first_day=first,
        gap_last_day=last,
        gap_day_count=(last - first).days + 1,
        source_ceiling_day=facts.source_ceiling_day,
        expected_horizon_day=facts.expected_horizon_day,
        authored_at=now,
    )
    return RepairCandidate(
        facts=facts,
        verdict="behind_provider",
        detail=(
            f"no gap below its own ceiling, but {facts.staleness_days} day(s) behind the horizon its registered lag "
            f"predicts; {binding.lane_id} runs its own writer with {' '.join(request.command_arguments())}"
        ),
        request=request,
    )


def union_day_ranges(ranges: Iterable[DayRange]) -> tuple[DayRange, ...]:
    """Merge overlapping and adjacent closed ranges into ascending disjoint runs; rungs may each state the same hole."""
    ordered = sorted(ranges, key=lambda entry: (entry.first_day, entry.last_day))
    merged: list[DayRange] = []
    for entry in ordered:
        if merged and entry.first_day <= merged[-1].last_day + timedelta(days=1):
            merged[-1] = DayRange(first_day=merged[-1].first_day, last_day=max(merged[-1].last_day, entry.last_day))
            continue
        merged.append(entry)
    return tuple(merged)


def _newest(days: Iterable[date | None]) -> date | None:
    known = [day for day in days if day is not None]
    return max(known) if known else None


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RepairRequestError(f"{key} must be a non-empty string")
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepairRequestError(f"{key} must be an integer")
    return value


def _optional_day(payload: Mapping[str, object], key: str) -> date | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RepairRequestError(f"{key} must be a calendar day or null")
    return date.fromisoformat(value)


__all__ = [
    "AUTHORIZING_VERDICTS",
    "EXECUTOR_REPAIR_WORK_ITEM_KIND",
    "REPAIR_BINDINGS",
    "REPAIR_DEFAULT_MAX_CANDIDATES",
    "REPAIR_DEFAULT_MAX_DAYS_PER_TURN",
    "REPAIR_EXCLUSIONS",
    "REPAIR_LANE_IDS",
    "REPAIR_LANE_SUFFIX",
    "REPAIR_PAYLOAD_VERSION",
    "LaneGapFacts",
    "RepairBinding",
    "RepairBudget",
    "RepairCandidate",
    "RepairPlan",
    "RepairRequest",
    "RepairRequestError",
    "RepairVerdict",
    "measure_lane_gaps",
    "select_repair_candidates",
    "union_day_ranges",
]
