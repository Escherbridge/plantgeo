"""One config-lane turn (spec §4.3; D2 "thin strategy, fat runner"): days, budget, fetch, settle, write, facts.

`run_turn` owns everything a strategy does not: the window and the S19 probe gate, the per-mode
cap, checkpoints, per-unit retries, the S11 rewrite rules, the writer calls, CA20's owed-claim
retry, CA17's republish, compare mode and every S5 fact. See `pipeline/runner/AGENTS.md` "The turn".
"""

from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Protocol

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.observability import events
from agri_data_service.foundation.observability.redaction import describe_error
from agri_data_service.pipeline.runner.budget import (
    BudgetedClient,
    TurnBudget,
    budget_basis,
    mode_cap,
    select_affordable_days,
    unsent_budget,
)
from agri_data_service.pipeline.runner.checkpoints import TurnCheckpoints
from agri_data_service.pipeline.runner.clock import utc_today
from agri_data_service.pipeline.runner.contract import (
    PROBE_STATUSES,
    TURN_MODES,
    UNWRITTEN_REASONS,
    Absent,
    DayContext,
    EdgeProbingStrategy,
    IngestStrategy,
    ProbeWindow,
    ProviderEdge,
    ReleaseCalendarStrategy,
    StreamDayState,
    TransformStrategy,
    Unsettled,
    Written,
)
from agri_data_service.pipeline.runner.digests import responses_digest
from agri_data_service.pipeline.runner.exits import (
    EXIT_COMPLETED,
    EXIT_CONFIGURATION_ERROR,
    EXIT_UPSTREAM_UNAVAILABLE,
    TurnConfigurationError,
)
from agri_data_service.pipeline.runner.fetch import (
    DEFAULT_FETCH_RETRY_POLICY,
    FetchTally,
    UnitFetcher,
    UnitOutcome,
    classify_fetch_error,
)
from agri_data_service.pipeline.runner.receipts import DayReceipt, transform_receipt_stream
from agri_data_service.pipeline.runner.report import UnwrittenEntry
from agri_data_service.pipeline.runner.windows import (
    PROBE_WINDOW_MAX_DAYS,
    DayRange,
    ensure_gap_fill_enabled,
    forward_window,
    gap_fill_window,
    plan_forward,
    plan_gap_fill,
    probe_window,
    revision_block,
    transform_dirty_days,
)
from agri_data_service.pipeline.runner.writer import (
    CompareModeWriteError,
    LadderRepairError,
    LaneDayContendedError,
    decide_rewrite,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import date

    from agri_data_service.foundation.lane_config.models import LaneConfig, PartialDayPolicy, ProviderConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.census import LaneCensus
    from agri_data_service.pipeline.runner.checkpoints import CheckpointStore
    from agri_data_service.pipeline.runner.clock import TurnClock
    from agri_data_service.pipeline.runner.contract import (
        ProbeStatus,
        ProviderClient,
        Settlement,
        SourceRequest,
        SourceResponse,
        TurnMode,
        UnwrittenReason,
    )
    from agri_data_service.pipeline.runner.fetch import FetchRetryPolicy
    from agri_data_service.pipeline.runner.reader import LaneReader
    from agri_data_service.pipeline.runner.report import TurnLog, TurnReportBuilder
    from agri_data_service.pipeline.runner.writer import LaneWriter, WriteResult

#: The share of `turn_timeout_seconds` fetching may use; the rest is kept for writing what arrived.
FETCH_SHARE_OF_TURN_TIMEOUT: Final = 0.8
#: Ladder repairs (H1) start only before this share of `turn_timeout_seconds`; later ones are `ladder_owed`.
REPAIR_SHARE_OF_TURN_TIMEOUT: Final = 0.9
#: A stream-day a checkpoint may finish: one the bucket does not serve yet. A served day is re-asked (M3).
_RESTORABLE_STATUSES: Final = frozenset({"missing", "incomplete"})
#: When a day's units failed differently, the reason reported is the first of these it carries.
_FAILURE_PRECEDENCE: Final[tuple[UnwrittenReason, ...]] = (
    "strategy_error",
    "upstream_unavailable",
    "deferred_quota",
    "deferred_budget",
)
#: A partial `write_and_recheck` day's `unwritten` detail names at most this many of its unanswered units.
_DETAIL_UNIT_LIMIT: Final = 4
#: S19 + O-R3-1: the S5 reason an owed day held by the probe gate is reported under.
_GATE_REASON: Final[Mapping[str, UnwrittenReason]] = {
    "newer_than_probed_edge": "unsettled",
    "probe_null_behind_edge": "unsettled",
    "probe_invalid": "unsettled",
    "probe_blind": "unsettled",
    "probe_unavailable": "upstream_unavailable",
    "probe_deferred": "deferred_quota",
}
_PHASES: Final = ("census", "probe", "fetch", "settle", "write")


@dataclass(frozen=True, slots=True)
class TurnSpec:
    """What one invocation asks for: the lane, its provider and region, the mode, and the operator options."""

    lane: LaneConfig
    provider: ProviderConfig | None
    region: Region
    mode: TurnMode
    run_id: str
    #: A transform's input lanes by id (every id in `lane.inputs`).
    input_lanes: Mapping[str, LaneConfig] = field(default_factory=dict)
    compare: bool = False
    republish_current: bool = False
    #: Lowers (never raises) the mode's per-turn cap: operator or admission `--weighted-budget`.
    weighted_budget: int | None = None


@dataclass(slots=True)
class TurnPorts:
    """The turn's collaborators; production binds them in `pipeline/runner/binding.py`, tests bind fakes."""

    strategy: IngestStrategy | TransformStrategy
    reader: LaneReader
    clock: TurnClock
    writer: LaneWriter | None = None
    client: ProviderClient | None = None
    checkpoint_store: CheckpointStore | None = None
    fetch_policy: FetchRetryPolicy = DEFAULT_FETCH_RETRY_POLICY


@dataclass(frozen=True, slots=True)
class _SettledDay:
    """A day `settle` answered with a write or a proven absence, and the tables its rows produced."""

    day: date
    settlement: Written | Absent
    tables: Mapping[str, pa.Table]
    responses_digest: str


@dataclass(slots=True)
class _CompareTally:
    """What `--compare` found, per stream: built days that match, differ from, or are missing in the bucket."""

    days_equal: Counter[str] = field(default_factory=Counter)
    days_differ: Counter[str] = field(default_factory=Counter)
    days_unpublished: Counter[str] = field(default_factory=Counter)
    rows_published: Counter[str] = field(default_factory=Counter)
    differing_days: dict[str, list[str]] = field(default_factory=dict)

    def as_report(self) -> dict[str, object]:
        """The report's `compare` block."""
        streams = sorted(set(self.days_equal) | set(self.days_differ) | set(self.days_unpublished))
        return {
            stream: {
                "days_equal": self.days_equal[stream],
                "days_differ": self.days_differ[stream],
                "days_unpublished": self.days_unpublished[stream],
                "rows_published": self.rows_published[stream],
                "differing_days": self.differing_days.get(stream, [])[:20],
            }
            for stream in streams
        }


@dataclass(slots=True)
class _TurnTally:
    """The turn's counts, folded into the S5 report as facts."""

    days_candidate: int = 0
    days_fanned_out: int = 0
    days_written: int = 0
    days_absent: int = 0
    days_unchanged: int = 0
    days_ladder_owed: int = 0
    ladder_repairs: int = 0
    rows_written: int = 0
    rows_built: int = 0
    partitions_written: int = 0
    bytes_written: int = 0
    units_planned: int = 0
    units_sent: int = 0
    days_rechecked: int = 0
    #: Published days the rolling revision re-asked (S11: late approvals and revisions).
    days_revised: int = 0
    pruned_stream_days: int = 0
    availability_retried: int = 0
    streams_written: Counter[str] = field(default_factory=Counter)
    rewrite_reasons: Counter[str] = field(default_factory=Counter)
    #: Source rows strategies dropped, by reason (`Written.dropped_rows`).
    rows_dropped_by_reason: Counter[str] = field(default_factory=Counter)
    fetch: FetchTally = field(default_factory=FetchTally)

    def add_write(self, stream: str, result: WriteResult) -> None:
        """Fold one stream-day write into the counts."""
        self.streams_written[stream] += 1
        self.rows_written += result.rows
        self.partitions_written += result.partitions
        self.bytes_written += result.bytes_written


def _validate(spec: TurnSpec, ports: TurnPorts) -> None:  # noqa: PLR0912 - one refusal per invalid invocation
    """Refuse (exit 78) every invocation the lane or the options cannot support, before any read or send."""
    lane = spec.lane
    if spec.mode not in TURN_MODES:
        raise TurnConfigurationError(f"mode {spec.mode!r} is not one of {TURN_MODES}")
    if (lane.kind == "transform") != (spec.mode == "transform"):
        raise TurnConfigurationError(f"lane {lane.id!r} is an {lane.kind} lane; mode {spec.mode!r} is not its mode")
    if not lane.streams:
        raise TurnConfigurationError(f"lane {lane.id!r} declares no [[streams]], so a turn has nothing to write")
    if spec.compare:
        if ports.writer is not None:
            raise CompareModeWriteError("a --compare turn builds rows and diffs them; it is never handed a writer")
        if spec.republish_current or spec.mode == "gap-fill":
            raise TurnConfigurationError("--compare runs a forward or transform turn, never gap-fill or a republish")
    else:
        if lane.executor != "config":
            raise TurnConfigurationError(
                f"lane {lane.id!r} has executor = {lane.executor!r}; only --compare may run it before its cut-over"
            )
        if not lane.enabled:
            raise TurnConfigurationError(f"lane {lane.id!r} has enabled = false")
        if ports.writer is None:
            raise TurnConfigurationError("a writing turn was built without a writer")
    if spec.republish_current and (lane.nature != "static_lookup" or spec.mode != "forward"):
        raise TurnConfigurationError("--republish-current is a static_lookup forward option (CA17)")
    if lane.kind == "ingest":
        if spec.provider is None or ports.client is None:
            raise TurnConfigurationError(f"ingest lane {lane.id!r} has no provider client")
        if not isinstance(ports.strategy, IngestStrategy):
            raise TurnConfigurationError(f"lane {lane.id!r}: its strategy is not an IngestStrategy")
        if lane.nature == "static_lookup" and not isinstance(ports.strategy, EdgeProbingStrategy):
            raise TurnConfigurationError(f"static_lookup lane {lane.id!r} reads its watermark through probe_edge")
    else:
        if not isinstance(ports.strategy, TransformStrategy):
            raise TurnConfigurationError(f"lane {lane.id!r}: its strategy is not a TransformStrategy")
        missing = [input_id for input_id in lane.inputs if input_id not in spec.input_lanes]
        if missing:
            raise TurnConfigurationError(f"transform {lane.id!r}: input lanes {missing} are not loaded")


async def run_turn(spec: TurnSpec, ports: TurnPorts, report: TurnReportBuilder, log: TurnLog) -> int:
    """Run one turn and return its S4 exit code; a configuration or internal fault raises instead."""
    _validate(spec, ports)
    turn = _Turn(spec=spec, ports=ports, report=report, log=log)
    log.info(
        events.EVENT_LANE_TURN_STARTED,
        run_id=spec.run_id,
        compare=spec.compare,
        republish_current=spec.republish_current,
        cap=turn.budget.cap,
    )
    try:
        exit_code = await turn.run()
    finally:
        report.set(**turn.facts())
    report.close_unwritten()
    return exit_code


@dataclass(slots=True)
class _Turn:
    """One turn's state: the lane's facts, the budget it spends, and what it has done so far."""

    spec: TurnSpec
    ports: TurnPorts
    report: TurnReportBuilder
    log: TurnLog
    started: float = 0.0
    today: date | None = None
    budget: TurnBudget = field(default_factory=unsent_budget)
    client: BudgetedClient | None = None
    tally: _TurnTally = field(default_factory=_TurnTally)
    compare_tally: _CompareTally = field(default_factory=_CompareTally)
    phase_seconds: dict[str, float] = field(default_factory=lambda: dict.fromkeys(_PHASES, 0.0))
    probe: ProviderEdge | None = None
    #: The probe was refused by the turn's own cap, so its gated days are `deferred_budget`, not quota.
    probe_refused_by_budget: bool = False
    probe_gated_days: int = 0
    edge: date | None = None
    edge_source: str | None = None
    window: DayRange | None = None
    #: The published block this forward turn re-asked for S11 (`revision_block`), when the lane has one.
    revision: DayRange | None = None
    checkpoint_restores: int = 0
    checkpoints_retained: int = 0
    republish: dict[str, object] | None = None
    #: Owed days whose base rung is published: repaired after the fan-out, never asked upstream (H1).
    ladder_days: tuple[date, ...] = ()
    _receipts: dict[tuple[str, date], DayReceipt | None] = field(default_factory=dict)
    _census_views: dict[int, tuple[Mapping[str, tuple[date, ...]], Mapping[str, tuple[date, ...]]]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        lane = self.spec.lane
        self.started = self.ports.clock.monotonic()
        self.today = utc_today(self.ports.clock)
        if lane.kind == "ingest" and self.ports.client is not None:
            cap = mode_cap(lane.budget, self.spec.mode, weighted_budget=self.spec.weighted_budget)
            self.budget = TurnBudget(cap=cap, basis=budget_basis(self.spec.provider), provider=self.spec.provider)
            self.client = BudgetedClient(inner=self.ports.client, budget=self.budget)

    # --- facts --------------------------------------------------------------------------------

    @property
    def streams(self) -> tuple[str, ...]:
        """The lane's streams, in `[[streams]]` order."""
        return tuple(stream.slug for stream in self.spec.lane.streams)

    @property
    def partial_day(self) -> PartialDayPolicy:
        """The lane's partial-day policy; a static lookup refuses a partial snapshot."""
        days = self.spec.lane.days
        return "refuse" if days is None else days.partial_day

    @property
    def fetch_deadline(self) -> float:
        """The monotonic instant fetching stops; the remainder of the turn timeout is kept for writes."""
        return self.started + self.spec.lane.budget.turn_timeout_seconds * FETCH_SHARE_OF_TURN_TIMEOUT

    @contextlib.contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Time one phase into `phase_seconds_<name>`."""
        began = self.ports.clock.monotonic()
        try:
            yield
        finally:
            self.phase_seconds[name] += self.ports.clock.monotonic() - began

    def facts(self) -> dict[str, object]:
        """Every S5 fact this turn states beside `unwritten` (USE-6; `weighted_calls` is logical)."""
        ledger = self.budget.ledger
        writer = self.ports.writer
        fetch = self.tally.fetch
        facts: dict[str, object] = {
            "lane_kind": self.spec.lane.kind,
            "nature": self.spec.lane.nature,
            "strategy_module": self.spec.lane.strategy_module,
            "provider": None if self.spec.provider is None else self.spec.provider.id,
            "window_first": None if self.window is None else self.window.first.isoformat(),
            "window_last": None if self.window is None else self.window.last.isoformat(),
            "edge": None if self.edge is None else self.edge.isoformat(),
            "edge_source": self.edge_source,
            "probe": self._probe_block(),
            "probe_status": None if self.probe is None else self.probe.status,
            "requests": ledger.requests,
            "weighted_calls": round(ledger.weighted_calls, 3),
            "fetch_attempts": ledger.fetch_attempts,
            "probe_requests": ledger.probe_requests,
            "probe_weighted_calls": round(ledger.probe_weighted_calls, 3),
            "cap": self.budget.cap,
            "cap_basis": self.budget.basis,
            "weighted_budget": self.spec.weighted_budget,
            "units_planned": self.tally.units_planned,
            "units_sent": self.tally.units_sent,
            "units_fetched": fetch.units_fetched,
            "units_failed": fetch.units_failed,
            "quota_circuit_open": fetch.quota_circuit_open,
            "retry_backoff_seconds": round(fetch.retry_backoff_seconds, 3),
            "checkpoint_restores": self.checkpoint_restores,
            "checkpoints_retained": self.checkpoints_retained,
            "days_candidate": self.tally.days_candidate,
            "days_fanned_out": self.tally.days_fanned_out,
            "days_written": self.tally.days_written,
            "days_absent": self.tally.days_absent,
            "days_unchanged": self.tally.days_unchanged,
            "days_ladder_owed": self.tally.days_ladder_owed,
            "ladder_repairs": self.tally.ladder_repairs,
            "days_rechecked": self.tally.days_rechecked,
            "days_revised": self.tally.days_revised,
            "revision_first": None if self.revision is None else self.revision.first.isoformat(),
            "revision_last": None if self.revision is None else self.revision.last.isoformat(),
            "rows_dropped_by_reason": dict(sorted(self.tally.rows_dropped_by_reason.items())),
            "rows_written": self.tally.rows_written,
            "rows_built": self.tally.rows_built,
            "partitions_written": self.tally.partitions_written,
            "bytes_written": self.tally.bytes_written,
            "streams_written": dict(sorted(self.tally.streams_written.items())),
            "rewrite_reasons": dict(sorted(self.tally.rewrite_reasons.items())),
            "pruned_stream_days": self.tally.pruned_stream_days,
            "availability_retried": self.tally.availability_retried,
            "compare_result": self.compare_tally.as_report() if self.spec.compare else None,
            "republish": self.republish,
            "elapsed_seconds": round(self.ports.clock.monotonic() - self.started, 3),
            **{f"phase_seconds_{name}": round(seconds, 3) for name, seconds in self.phase_seconds.items()},
        }
        if writer is not None:
            facts.update(writer.publication_summary())
        return facts

    def _probe_block(self) -> dict[str, object] | None:
        probe = self.probe
        if probe is None:
            return None
        edge = probe.edge
        return {
            "status": probe.status,
            "window_first": probe.window.first.isoformat(),
            "window_last": probe.window.last.isoformat(),
            "valued_days": len(probe.valued_days),
            "edge": None if edge is None else edge.isoformat(),
            "edge_source": probe.source,
            "requests": self.budget.ledger.probe_requests,
            "weighted_calls": round(self.budget.ledger.probe_weighted_calls, 3),
            "gated_days": self.probe_gated_days,
            "detail": probe.detail,
        }

    def unwritten(
        self,
        day: date,
        reason: UnwrittenReason,
        detail: str = "",
        *,
        stream: str | None = None,
        behind_edge: bool = True,
    ) -> None:
        """Record one owed day this turn will not write."""
        self.report.add_unwritten(
            UnwrittenEntry(day=day, reason=reason, detail=detail, stream=stream, behind_edge=behind_edge)
        )
        self.log.debug(events.EVENT_LANE_TURN_DAY_SETTLED, day=day.isoformat(), outcome=reason, detail=detail[:200])

    # --- dispatch -----------------------------------------------------------------------------

    async def run(self) -> int:
        """Dispatch on kind, nature, mode and options."""
        lane = self.spec.lane
        if lane.kind == "transform":
            return await self._transform()
        if self.spec.mode == "forward" and not self.spec.compare:
            await self._retry_owed_availability()
        if self.spec.republish_current:
            return await self._republish_current()
        if lane.nature == "static_lookup":
            return await self._static_forward()
        if self.spec.mode == "gap-fill":
            return await self._gap_fill()
        return await self._series_forward()

    async def _retry_owed_availability(self) -> None:
        """CA20: a forward turn retries its streams' owed `availability/pending/` claims before anything else."""
        writer = self.ports.writer
        if writer is None:
            return
        try:
            self.tally.availability_retried = await writer.retry_owed_availability(self.streams)
        except Exception as error:  # the legacy contract: an owed claim never blocks a write
            self.log.warn("plantgeo_lane_turn_availability_retry_failed", error=describe_error(error))

    # --- ingest: choosing days ----------------------------------------------------------------

    def _census(self, streams: Sequence[str], window: DayRange) -> LaneCensus:
        with self.phase("census"):
            return self.ports.reader.census(streams, window.first, window.last)

    def _release_filter(self, days: Sequence[date], window: DayRange) -> list[date]:
        """A `release_series` owes only its release days; any other lane owes every day."""
        strategy = self.ports.strategy
        if not isinstance(strategy, ReleaseCalendarStrategy):
            return list(days)
        releases = set(strategy.release_days(window.first, window.last))
        return [day for day in days if day in releases]

    def _owed_days(self, census: LaneCensus, window: DayRange) -> list[date]:
        """The days a turn asks upstream about; a day whose base rung is published owes only ladder work (H1)."""
        ladder_only = census.base_data_days() & set(census.owed_days())
        self.tally.days_ladder_owed = len(ladder_only)
        self.ladder_days = tuple(sorted(ladder_only))
        return self._release_filter([day for day in census.owed_days() if day not in ladder_only], window)

    async def _repair_ladders(self, census: LaneCensus) -> None:
        """H1: derive the coarse rungs of every ladder-owed stream-day from its published base, touching no source.

        A repair the lock refuses is `contended`; one that fails, or that the turn's time no longer covers,
        is `ladder_owed` behind the edge, so the executor's alarm sees the hole instead of it staying silent.
        """
        if not self.ladder_days or self.spec.compare or self.ports.writer is None:
            return
        writer = self._writer()
        deadline = self.started + self.spec.lane.budget.turn_timeout_seconds * REPAIR_SHARE_OF_TURN_TIMEOUT
        with self.phase("write"):
            for day in self.ladder_days:
                for stream in self.streams:
                    if census.status(stream, day) != "incomplete":
                        continue
                    if self.ports.clock.monotonic() >= deadline:
                        self.unwritten(day, "ladder_owed", "the turn's time ran out before its repair", stream=stream)
                        continue
                    try:
                        result = await writer.repair_ladder(stream, day)
                    except LaneDayContendedError as error:
                        self.unwritten(day, "contended", describe_error(error), stream=stream)
                        continue
                    except LadderRepairError as error:
                        self.unwritten(day, "ladder_owed", describe_error(error), stream=stream)
                        continue
                    self.tally.ladder_repairs += 1
                    self.tally.partitions_written += result.partitions
                    self.tally.bytes_written += result.bytes_written

    async def _series_forward(self) -> int:
        """S19: probe first, fan out only what the probe shows valued, walk what lies before the probe window."""
        days = self.spec.lane.days
        if days is None:  # pragma: no cover - the lane model requires [days] for every series nature
            raise TurnConfigurationError(f"lane {self.spec.lane.id!r} has no [days]")
        self.window = window = forward_window(days, today=self._today)
        if window is None:
            return EXIT_COMPLETED
        census = self._census(self.streams, window)
        if self.spec.compare or days.partial_day == "write_and_recheck":
            owed = self._release_filter(window.days(), window)
        else:
            owed = self._owed_days(census, window)
        rechecks = self._release_filter(census.absent_days(), window)
        self.tally.days_candidate = len(set(owed) | set(rechecks))
        probe = None
        if isinstance(self.ports.strategy, EdgeProbingStrategy) and (owed or rechecks):
            probe = await self._spend_probe(probe_window(window), clip=True)
        plan = plan_forward(
            owed=owed,
            rechecks=rechecks,
            window=window,
            probe=probe,
            published_days=census.lane_data_days(),
            base_data_days=census.base_data_days(),
        )
        self.edge, self.edge_source = plan.edge, plan.edge_source
        self.probe_gated_days = len(plan.gated)
        for gated in plan.gated:
            self.unwritten(gated.day, self._gate_reason(gated.detail), gated.detail, behind_edge=gated.behind_edge)
        self.tally.days_unchanged += len(plan.unchanged_absences)
        recheck_within = None if self.spec.compare else window
        exit_code = await self._fan_out_and_publish(plan.fan_out, census, recheck_within=recheck_within)
        if exit_code == EXIT_COMPLETED:
            await self._revise(window)
        await self._repair_ladders(census)
        return exit_code

    async def _revise(self, window: DayRange) -> None:
        """S11 rolling revision: re-ask one block of published days behind the forward window (`revision_block`).

        Only days every stream publishes are re-asked (holes are gap-fill's); a digest change rewrites one.
        It spends what the forward fan-out left of the cap, and never reports a day unwritten.
        """
        days = self.spec.lane.days
        if days is None or self.spec.compare:
            return
        block = revision_block(days, window, today=self._today)
        if block is None:
            return
        self.revision = block
        census = self._census(self.streams, block)
        settled = await self._fan_out(census.lane_data_days(), census, probe=None, revise=True)
        await self._publish(settled, census, force=False)

    async def _gap_fill(self) -> int:
        """D3: the census holes before the forward window, oldest first, capped (S12: disabled by default)."""
        ensure_gap_fill_enabled(self.spec.lane)
        self.window = window = gap_fill_window(self.spec.lane, today=self._today)
        if window is None:
            return EXIT_COMPLETED
        census = self._census(self.streams, window)
        plan = plan_gap_fill(self.spec.lane, owed=self._owed_days(census, window))
        self.tally.days_candidate = len(plan.fill) + len(plan.retention_exceeded) + len(plan.overflow)
        for day in plan.retention_exceeded:
            self.unwritten(day, "retention_exceeded", "older than the source's history capability")
        for day in plan.overflow:
            self.unwritten(day, "deferred_budget", "past this turn's gap-fill day cap")
        exit_code = await self._fan_out_and_publish(plan.fill, census)
        await self._repair_ladders(census)
        return exit_code

    async def _static_forward(self) -> int:
        """A static lookup owes one snapshot at its source watermark, read by `probe_edge`, and nothing when current."""
        window = ProbeWindow(first=self._today - timedelta(days=PROBE_WINDOW_MAX_DAYS - 1), last=self._today)
        probe = await self._spend_probe(window, clip=False)
        watermark = probe.edge if probe.status == "ok" else None
        if watermark is None:
            detail = "probe_blind" if probe.status == "ok" else f"probe_{probe.status}"
            self.unwritten(self._today, self._gate_reason(detail), detail)
            return EXIT_COMPLETED
        self.edge, self.edge_source = watermark, "probe"
        self.window = DayRange(first=watermark, last=watermark)
        served = {stream: self.ports.reader.newest_data_day(stream) for stream in self.streams}
        current = all(day is not None and day >= watermark for day in served.values())
        self.tally.days_candidate = 1
        if current and not self.spec.compare:
            self.tally.days_unchanged += 1
            return EXIT_COMPLETED
        census = self._census(self.streams, self.window)
        return await self._fan_out_and_publish((watermark,), census)

    async def _republish_current(self) -> int:
        """CA17: refetch the served snapshot and rewrite it only when its digest equals what is served."""
        reader = self.ports.reader
        served = {stream: reader.newest_data_day(stream) for stream in self.streams}
        served_days = set(served.values())
        if None in served_days or len(served_days) != 1:
            self.republish = {"refused": "no_single_served_snapshot", "served": _iso_map(served)}
            return EXIT_CONFIGURATION_ERROR
        day = next(iter(served_days))
        if day is None:  # pragma: no cover - excluded just above; narrows the type
            return EXIT_CONFIGURATION_ERROR
        self.window = DayRange(first=day, last=day)
        self.edge, self.edge_source = day, "probe"
        census = self._census(self.streams, self.window)
        settled = await self._fan_out((day,), census, probe=ProviderEdge(status="ok", window=ProbeWindow(day, day)))
        if not settled or not isinstance(settled[0].settlement, Written):
            self.republish = {"refused": "source_did_not_answer_the_served_day", "day": day.isoformat()}
            return EXIT_CONFIGURATION_ERROR
        built = settled[0]
        mismatched: list[str] = []
        for stream, table in built.tables.items():
            published = reader.read_published(stream, day)
            if published is None or reader.canonical_digest(stream, published) != reader.canonical_digest(
                stream, table
            ):
                mismatched.append(stream)
        if mismatched:
            self.republish = {"refused": "digest_mismatch", "day": day.isoformat(), "streams": mismatched}
            return EXIT_CONFIGURATION_ERROR
        await self._publish([built], census, force=True)
        self.republish = {"republished": day.isoformat(), "streams": list(built.tables)}
        return EXIT_COMPLETED

    # --- ingest: spending, fetching, settling ---------------------------------------------------

    def _gate_reason(self, detail: str) -> UnwrittenReason:
        """The S5 reason for a day the probe gate held, by the gate's own word."""
        if detail == "probe_deferred" and self.probe_refused_by_budget:
            return "deferred_budget"
        return _GATE_REASON.get(detail, "unsettled")

    @property
    def _today(self) -> date:
        if self.today is None:  # pragma: no cover - set in __post_init__
            raise RuntimeError("the turn has no day")
        return self.today

    async def _spend_probe(self, window: ProbeWindow, *, clip: bool) -> ProviderEdge:
        """Spend `probe_edge` once; a probe that cannot answer is `unavailable`, a throttled one `deferred` (O-R3-1)."""
        strategy = self.ports.strategy
        with self.phase("probe"):
            if not isinstance(strategy, EdgeProbingStrategy) or self.client is None:
                raise TurnConfigurationError("a probe was asked of a strategy without probe_edge")
            if self.ports.clock.monotonic() >= self.fetch_deadline:
                probe = ProviderEdge(status="unavailable", window=window, detail="the turn's time budget ran out")
            else:
                try:
                    answered = await strategy.probe_edge(self.client, window)
                except Exception as error:
                    kind = classify_fetch_error(error)
                    if kind == "config":
                        raise
                    status: ProbeStatus = "deferred" if kind in {"throttled", "budget"} else "unavailable"
                    self.probe_refused_by_budget = kind == "budget"
                    probe = ProviderEdge(status=status, window=window, detail=describe_error(error))
                else:
                    probe = _checked_probe(answered, window, clip=clip)
        self.probe = probe
        return probe

    def _context(
        self, day: date, census: LaneCensus, probe: ProviderEdge | None, *, planned: Sequence[SourceRequest] = ()
    ) -> DayContext:
        """What `settle` may know about `day`: the census the turn paid for, the receipts, the probe, its units."""
        states: dict[str, StreamDayState] = {}
        for stream in self.streams:
            status = census.status(stream, day)
            receipt = self._receipt(stream, day) if status == "data" else None
            states[stream] = StreamDayState(
                status=status,
                source_digest=None if receipt is None else receipt.source_digest,
                present_units=None if receipt is None else receipt.present_units,
                expected_units=None if receipt is None else receipt.expected_units,
            )
        source = self.spec.lane.source
        published_days, absent_days = self._census_days(census)
        return DayContext(
            day=day,
            states=states,
            published_days=published_days,
            absent_days=absent_days,
            edge=probe,
            retention_floor=None if source is None else source.history.earliest,
            output_streams=self.streams,
            planned_units=tuple(planned),
        )

    def _census_days(self, census: LaneCensus) -> tuple[Mapping[str, tuple[date, ...]], Mapping[str, tuple[date, ...]]]:
        """Per stream, the census's published and governed-absent days: read once per census, not once per day."""
        cached = self._census_views.get(id(census))
        if cached is None:
            published = {stream: census.data_days(stream) for stream in self.streams}
            absent = {
                stream: tuple(day for day in census.days() if census.status(stream, day) == "absent")
                for stream in self.streams
            }
            cached = self._census_views[id(census)] = (published, absent)
        return cached

    def _receipt(self, stream: str, day: date) -> DayReceipt | None:
        key = (stream, day)
        if key not in self._receipts:
            self._receipts[key] = self.ports.reader.receipt(stream, day)
        return self._receipts[key]

    async def _fan_out_and_publish(
        self, days: Sequence[date], census: LaneCensus, *, recheck_within: DayRange | None = None
    ) -> int:
        """Fan out `days`, settle them, write (or compare) the answers, and decide the exit."""
        settled = await self._fan_out(days, census, probe=self.probe, recheck_within=recheck_within)
        await self._publish(settled, census, force=False)
        fetch = self.tally.fetch
        answered_nothing = not fetch.units_fetched and not self.checkpoint_restores
        if self.tally.units_sent and answered_nothing and fetch.units_upstream_unavailable:
            return EXIT_UPSTREAM_UNAVAILABLE
        return EXIT_COMPLETED

    async def _fan_out(
        self,
        days: Sequence[date],
        census: LaneCensus,
        *,
        probe: ProviderEdge | None,
        recheck_within: DayRange | None = None,
        revise: bool = False,
    ) -> list[_SettledDay]:
        """Plan units, restore checkpoints, take what the cap affords, fetch, and settle each day in order.

        With `recheck_within`, every published day a fetched unit also answered (a 14-day request
        answers 14 days) is settled too, at no extra cost: the S11 digest rewrite of ERA5T revisions.
        Such a day is already written, so nothing about it is ever reported unwritten. With `revise`,
        every day is such a published day (`_revise`), and one short of any unit is left as written.
        """
        strategy = self.ports.strategy
        if not days or not isinstance(strategy, IngestStrategy) or self.client is None or self.spec.provider is None:
            return []
        ordered = sorted(set(days))
        owe: _Owe = _record_nothing if revise else self.unwritten
        if revise:
            self.tally.days_revised += len(ordered)
        else:
            self.tally.days_fanned_out += len(ordered)
        planned = tuple(strategy.plan_requests(ordered, self.spec.lane, self.spec.region))
        self.tally.units_planned += len(planned)
        units_by_day = {day: [request for request in planned if day in request.days] for day in ordered}
        plannable: list[date] = []
        for day in ordered:
            if units_by_day[day]:
                plannable.append(day)
            else:
                owe(day, "strategy_error", "plan_requests planned no unit covering this day")
        checkpoints = TurnCheckpoints(
            store=self.ports.checkpoint_store,
            lane_id=self.spec.lane.id,
            provider=self.spec.provider,
            writable=not self.spec.compare,
        )
        restored: dict[str, SourceResponse] = {}
        owed_set = frozenset(ordered)
        with self.phase("fetch"):
            for request in {request.unit: request for day in plannable for request in units_by_day[day]}.values():
                if not self._restorable(request, census, owed_set):
                    continue
                response = await checkpoints.restore(request, strategy, now=self.ports.clock.now())
                if response is not None:
                    restored[request.unit] = response
            selection = select_affordable_days(plannable, units_by_day, self.budget, free_units=frozenset(restored))
            for day in selection.deferred:
                owe(day, "deferred_budget", f"the turn's {self.budget.cap} {self.budget.basis} cap")
            fetcher = UnitFetcher(
                strategy=strategy,
                client=self.client,
                clock=self.ports.clock,
                deadline=self.fetch_deadline,
                policy=self.ports.fetch_policy,
                checkpoints=checkpoints,
                backoff_host=_endpoint_host(self.spec),
                tally=self.tally.fetch,
            )
            outcomes: dict[str, UnitOutcome] = await fetcher.fetch_all(
                selection.requests, concurrency=self.spec.lane.budget.max_concurrency
            )
            self.tally.units_sent += len(selection.requests)
            for unit, response in restored.items():
                outcomes[unit] = UnitOutcome(request=response.request, response=response)
        self.checkpoint_restores += checkpoints.restores
        self.checkpoints_retained += checkpoints.retained
        revisions = self._free_rechecks(planned, outcomes, census, recheck_within, exclude=set(ordered))
        units_by_day.update(revisions)
        owed_days = dict.fromkeys(selection.selected, not revise) | dict.fromkeys(revisions, False)
        with self.phase("settle"):
            return self._settle_answered(owed_days, units_by_day, outcomes, census, probe)

    def _settle_answered(
        self,
        owed_days: Mapping[date, bool],
        units_by_day: Mapping[date, Sequence[SourceRequest]],
        outcomes: Mapping[str, UnitOutcome],
        census: LaneCensus,
        probe: ProviderEdge | None,
    ) -> list[_SettledDay]:
        """Settle each fanned-out day in order; a day short of a unit is held, or settled partial (spec §7a).

        A `write_and_recheck` owed day one of whose units failed settles from the units that answered; the
        failed units are its `unwritten` entry (a tile that stays down is unwritten for its gauges only).
        """
        settled: list[_SettledDay] = []
        for day, owed in sorted(owed_days.items()):
            day_units = units_by_day[day]
            day_outcomes = [outcomes[request.unit] for request in day_units]
            failed = [outcome for outcome in day_outcomes if outcome.response is None]
            responses = [outcome.response for outcome in day_outcomes if outcome.response is not None]
            if failed:
                worst = min(failed, key=lambda outcome: _FAILURE_PRECEDENCE.index(outcome.reason or "strategy_error"))
                reason, detail = worst.reason or "strategy_error", worst.detail or ""
                if not owed or not responses or self.partial_day != "write_and_recheck":
                    if owed:
                        self.unwritten(day, reason, detail)
                    continue
                self.unwritten(day, reason, _short_day_detail(failed, planned=len(day_outcomes), detail=detail))
            answered = self._settle(day, responses, self._context(day, census, probe, planned=day_units), owed=owed)
            if answered is not None:
                settled.append(answered)
        return settled

    def _restorable(self, request: SourceRequest, census: LaneCensus, owed: frozenset[date]) -> bool:
        """M3: a checkpoint may finish an unserved day, never stand in for a recheck of a served or absent one."""
        return all(
            census.status(stream, day) in _RESTORABLE_STATUSES
            for day in request.days
            if day in owed
            for stream in self.streams
        )

    def _free_rechecks(
        self,
        planned: Sequence[SourceRequest],
        outcomes: Mapping[str, UnitOutcome],
        census: LaneCensus,
        within: DayRange | None,
        *,
        exclude: set[date],
    ) -> dict[date, list[SourceRequest]]:
        """Published days in `within` whose every covering unit was answered this turn: rechecked for free."""
        if within is None:
            return {}
        answered = {unit for unit, outcome in outcomes.items() if outcome.response is not None}
        covered = sorted(
            {day for request in planned if request.unit in answered for day in request.days if day in within} - exclude
        )
        rechecks: dict[date, list[SourceRequest]] = {}
        for day in covered:
            units = [request for request in planned if day in request.days]
            if all(request.unit in answered for request in units) and all(
                census.status(stream, day) == "data" for stream in self.streams
            ):
                rechecks[day] = units
        self.tally.days_rechecked += len(rechecks)
        return rechecks

    def _settle(  # noqa: PLR0911 - one return per settlement outcome
        self, day: date, responses: Sequence[SourceResponse], context: DayContext, *, owed: bool = True
    ) -> _SettledDay | None:
        """`settle` then `rows`; every strategy fault is this day's `strategy_error`, never the turn's.

        `owed = False` (a free recheck of a published day) records nothing unwritten: the day is served.
        """
        strategy = self.ports.strategy
        if not isinstance(strategy, IngestStrategy):  # pragma: no cover - checked by _validate
            return None
        owe: _Owe = self.unwritten if owed else _record_nothing
        try:
            settlement: Settlement = strategy.settle(day, responses, context)
        except Exception as error:
            self.log.warn("plantgeo_lane_turn_settle_failed", day=day.isoformat(), error=describe_error(error))
            owe(day, "strategy_error", describe_error(error))
            return None
        digest = responses_digest(responses)
        if isinstance(settlement, Unsettled):
            owe(day, settlement.reason if settlement.reason in UNWRITTEN_REASONS else "unsettled", settlement.detail)
            return None
        if isinstance(settlement, Absent):
            if not settlement.proof.strip():
                owe(day, "strategy_error", "an Absent settlement carried no proof")
                return None
            return _SettledDay(day=day, settlement=settlement, tables={}, responses_digest=digest)
        if not isinstance(settlement, Written):
            owe(day, "strategy_error", f"settle returned {type(settlement).__name__}")
            return None
        if settlement.partial and self.partial_day == "refuse":
            owe(day, "refused_partial", f"{settlement.present_units} of {settlement.expected_units} units present")
            return None
        self.tally.rows_dropped_by_reason.update(settlement.dropped_rows)
        try:
            produced = strategy.rows(day, responses)
        except Exception as error:
            self.log.warn("plantgeo_lane_turn_rows_failed", day=day.isoformat(), error=describe_error(error))
            owe(day, "strategy_error", describe_error(error))
            return None
        tables = self._tables_by_stream(produced, day, owe=owe)
        if tables is None:
            return None
        return _SettledDay(day=day, settlement=settlement, tables=tables, responses_digest=digest)

    def _tables_by_stream(
        self, produced: object, day: date, *, allow_subset: bool = False, owe: _Owe | None = None
    ) -> dict[str, pa.Table] | None:
        """One non-empty table per declared stream (a transform may answer a subset), or the day's `strategy_error`."""
        record: _Owe = self.unwritten if owe is None else owe
        streams = self.streams
        if isinstance(produced, pa.Table):
            if len(streams) != 1:
                record(day, "strategy_error", f"one bare table for a lane writing {len(streams)} streams")
                return None
            produced = {streams[0]: produced}
        if not isinstance(produced, Mapping):
            record(day, "strategy_error", f"rows returned {type(produced).__name__}")
            return None
        tables = dict(produced)
        unknown = sorted(set(tables) - set(streams))
        missing = [] if allow_subset else sorted(set(streams) - set(tables))
        if unknown or missing:
            record(day, "strategy_error", f"rows named streams {unknown} and left out {missing}")
            return None
        answered = [stream for stream in streams if stream in tables]
        empty = [
            stream for stream in answered if not isinstance(tables[stream], pa.Table) or tables[stream].num_rows == 0
        ]
        if empty:
            record(day, "strategy_error", f"zero-row tables for {empty}; an empty day is an Absent settlement")
            return None
        self.tally.rows_built += sum(tables[stream].num_rows for stream in answered)
        return {stream: tables[stream] for stream in answered}

    # --- writing ------------------------------------------------------------------------------

    async def _publish(self, settled: Sequence[_SettledDay], census: LaneCensus, *, force: bool) -> None:
        """Apply S11 to every settled stream-day and write what it says to write (compare: diff instead)."""
        with self.phase("write"):
            for answered in settled:
                if self.spec.compare:
                    self._compare(answered, census)
                elif isinstance(answered.settlement, Absent):
                    await self._write_absence(answered, answered.settlement, census)
                else:
                    await self._write_rows(answered, answered.settlement, census, force=force)

    async def _write_rows(self, answered: _SettledDay, written: Written, census: LaneCensus, *, force: bool) -> None:
        writer = self._writer()
        reader = self.ports.reader
        wrote_any = False
        for stream, table in answered.tables.items():
            status = census.status(stream, answered.day)
            receipt = self._receipt(stream, answered.day) if status == "data" else None
            digest = written.source_digest or reader.canonical_digest(stream, table)
            decision = decide_rewrite(
                status=status,
                receipt=receipt,
                settlement=written,
                source_digest=digest,
                partial_day=self.partial_day,
                force=force,
            )
            self.tally.rewrite_reasons[decision.reason] += 1
            if not decision.writes:
                continue
            try:
                result = await writer.write_day(
                    stream,
                    answered.day,
                    table,
                    DayReceipt(
                        stream=stream,
                        day=answered.day,
                        lane=self.spec.lane.id,
                        outcome="republished" if force else "written",
                        source_digest=digest,
                        present_units=written.present_units,
                        expected_units=written.expected_units,
                        run_id=self.spec.run_id,
                        recorded_at=self.ports.clock.now(),
                    ),
                    availability=True,
                )
            except LaneDayContendedError as error:
                self.unwritten(answered.day, "contended", describe_error(error), stream=stream)
                continue
            self.tally.add_write(stream, result)
            wrote_any = True
        if wrote_any:
            self.tally.days_written += 1
        else:
            self.tally.days_unchanged += 1

    async def _write_absence(self, answered: _SettledDay, absence: Absent, census: LaneCensus) -> None:
        writer = self._writer()
        governed_any = False
        for stream in self.streams:
            decision = decide_rewrite(
                status=census.status(stream, answered.day),
                receipt=None,
                settlement=absence,
                source_digest=answered.responses_digest,
                partial_day=self.partial_day,
            )
            self.tally.rewrite_reasons[decision.reason] += 1
            if decision.reason == "data_never_retracted_by_absence":
                self.log.warn(
                    "plantgeo_lane_turn_absence_over_data_refused", stream=stream, day=answered.day.isoformat()
                )
            if not decision.writes:
                continue
            try:
                result = await writer.write_absence(
                    stream,
                    answered.day,
                    absence,
                    DayReceipt(
                        stream=stream,
                        day=answered.day,
                        lane=self.spec.lane.id,
                        outcome="absent",
                        source_digest=answered.responses_digest,
                        run_id=self.spec.run_id,
                        recorded_at=self.ports.clock.now(),
                    ),
                )
            except LaneDayContendedError as error:
                self.unwritten(answered.day, "contended", describe_error(error), stream=stream)
                continue
            self.tally.add_write(stream, result)
            governed_any = True
        if governed_any:
            self.tally.days_absent += 1
        else:
            self.tally.days_unchanged += 1

    def _compare(self, answered: _SettledDay, census: LaneCensus) -> None:
        """Diff one built day against what the bucket serves; nothing is written."""
        reader = self.ports.reader
        tally = self.compare_tally
        streams = self.streams if isinstance(answered.settlement, Absent) else tuple(answered.tables)
        for stream in streams:
            status = census.status(stream, answered.day)
            if isinstance(answered.settlement, Absent):
                bucket = tally.days_equal if status == "absent" else tally.days_differ
                bucket[stream] += 1
                continue
            published = reader.read_published(stream, answered.day) if status == "data" else None
            if published is None:
                tally.days_unpublished[stream] += 1
                continue
            tally.rows_published[stream] += published.num_rows
            built = answered.tables[stream]
            if reader.canonical_digest(stream, built) == reader.canonical_digest(stream, published):
                tally.days_equal[stream] += 1
            else:
                tally.days_differ[stream] += 1
                tally.differing_days.setdefault(stream, []).append(answered.day.isoformat())

    def _writer(self) -> LaneWriter:
        writer = self.ports.writer
        if writer is None or self.spec.compare:
            raise CompareModeWriteError("a --compare turn reached a write; compare builds rows and diffs them")
        return writer

    # --- transforms ---------------------------------------------------------------------------

    async def _transform(self) -> int:
        """D4 + S11: rebuild each window day whose input digests differ from the transform's receipt."""
        lane = self.spec.lane
        strategy = self.ports.strategy
        if lane.days is None or not isinstance(strategy, TransformStrategy):  # pragma: no cover - validated
            raise TurnConfigurationError(f"transform {lane.id!r} needs [days] and a TransformStrategy")
        self.window = window = forward_window(lane.days, today=self._today)
        if window is None:
            return EXIT_COMPLETED
        input_streams = {
            input_id: tuple(stream.slug for stream in self.spec.input_lanes[input_id].streams)
            for input_id in lane.inputs
        }
        flat_inputs = [stream for streams in input_streams.values() for stream in streams]
        prunable = {
            stream
            for input_id, streams in input_streams.items()
            if (days := self.spec.input_lanes[input_id].days) is not None and days.partial_day == "write_and_recheck"
            for stream in streams
        }
        inputs_census = self._census(flat_inputs, window)
        outputs_census = self._census(self.streams, window)
        input_digests: dict[date, dict[str, str]] = {}
        receipt_digests: dict[date, dict[str, str]] = {}
        pending_prunes: dict[date, list[str]] = {}
        with self.phase("settle"):
            for day in window.days():
                current = {
                    stream: digest
                    for stream in flat_inputs
                    if (digest := self._input_digest(stream, day, inputs_census)) is not None
                }
                receipt = self._transform_receipt(day, outputs_census)
                if receipt is not None:
                    receipt_digests[day] = dict(receipt.input_digests)
                    for stream, pruned_digest in receipt.pruned_inputs.items():
                        if stream in current:
                            pending_prunes.setdefault(day, []).append(stream)
                        current.setdefault(stream, pruned_digest)
                input_digests[day] = current
        dirty = transform_dirty_days(window.days(), input_digests=input_digests, receipt_digests=receipt_digests)
        self.tally.days_candidate = len(dirty)
        for day in dirty:
            await self._derive_day(day, inputs_census, outputs_census, input_digests[day], input_streams, prunable)
        if lane.pruning.enabled and not self.spec.compare:
            for day, streams in sorted(pending_prunes.items()):
                if day not in dirty:
                    await self._prune(day, streams)
        return EXIT_COMPLETED

    def _input_digest(self, stream: str, day: date, census: LaneCensus) -> str | None:
        """An input stream-day's digest: its turn receipt's, else the published table's; `absent`; `None` if missing."""
        status = census.status(stream, day)
        if status == "absent":
            return "absent"
        if status != "data":
            return None
        receipt = self._receipt(stream, day)
        if receipt is not None and receipt.source_digest:
            return receipt.source_digest
        table = self.ports.reader.read_published(stream, day)
        return None if table is None else "published:" + self.ports.reader.canonical_digest(stream, table)

    def _transform_receipt(self, day: date, census: LaneCensus) -> DayReceipt | None:
        """The day's lane-level transform receipt (M5), else the one every output stream holds, else `None` (dirty).

        The lane-level receipt is written after every answered output, so a derivation that legitimately
        answers a subset of its outputs is clean until its inputs change, and a turn that died between two
        outputs leaves the day dirty.
        """
        lane_receipt = self._receipt(transform_receipt_stream(self.spec.lane.id), day)
        if lane_receipt is not None:
            return lane_receipt
        receipts = [
            self._receipt(stream, day) if census.status(stream, day) in {"data", "absent"} else None
            for stream in self.streams
        ]
        if any(receipt is None for receipt in receipts):
            return None
        first = receipts[0]
        if first is None or any(
            receipt is None or dict(receipt.input_digests) != dict(first.input_digests) for receipt in receipts
        ):
            return None
        return first

    async def _derive_day(  # noqa: PLR0913 - the day, both censuses, its digests and the two stream maps
        self,
        day: date,
        inputs_census: LaneCensus,
        outputs_census: LaneCensus,
        digests: Mapping[str, str],
        input_streams: Mapping[str, tuple[str, ...]],
        prunable: set[str],
    ) -> None:
        strategy = self.ports.strategy
        if not isinstance(strategy, TransformStrategy):  # pragma: no cover - validated
            return
        reader = self.ports.reader
        flat_inputs = [stream for streams in input_streams.values() for stream in streams]
        with self.phase("settle"):
            inputs = {
                stream: reader.read_published(stream, day) if inputs_census.status(stream, day) == "data" else None
                for stream in flat_inputs
            }
            context = DayContext(
                day=day,
                states={stream: StreamDayState(status=inputs_census.status(stream, day)) for stream in flat_inputs},
                published_days={stream: inputs_census.data_days(stream) for stream in flat_inputs},
                absent_days={},
                output_streams=self.streams,
                input_streams=input_streams,
            )
            try:
                derivation = strategy.derive(day, inputs, context)
            except Exception as error:
                self.log.warn("plantgeo_lane_turn_derive_failed", day=day.isoformat(), error=describe_error(error))
                self.unwritten(day, "strategy_error", describe_error(error))
                return
            if derivation.table is None:
                self.tally.days_unchanged += 1
                return
            tables = self._tables_by_stream(derivation.table, day, allow_subset=True)
        if tables is None:
            return
        pruning = self.spec.lane.pruning.enabled and not self.spec.compare
        superseded = [stream for stream in derivation.superseded_inputs if stream in prunable and stream in digests]
        pruned = {stream: digests[stream] for stream in superseded} if pruning else {}
        if self.spec.compare:
            self._compare(
                _SettledDay(day=day, settlement=Written(1, 1), tables=tables, responses_digest=""), outputs_census
            )
            return
        await self._write_derived(day, tables, outputs_census, digests, pruned)
        if pruned:
            await self._prune(day, list(pruned))

    async def _write_derived(
        self,
        day: date,
        tables: Mapping[str, pa.Table],
        census: LaneCensus,
        digests: Mapping[str, str],
        pruned: Mapping[str, str],
    ) -> None:
        """S11 for a transform: rebuild on an input-digest change; an unchanged output only refreshes its receipt."""
        writer = self._writer()
        reader = self.ports.reader
        wrote_any = contended = False
        with self.phase("write"):
            for stream, table in tables.items():
                digest = reader.canonical_digest(stream, table)
                status = census.status(stream, day)
                receipt = DayReceipt(
                    stream=stream,
                    day=day,
                    lane=self.spec.lane.id,
                    outcome="derived",
                    source_digest=digest,
                    present_units=table.num_rows,
                    expected_units=table.num_rows,
                    input_digests=dict(digests),
                    pruned_inputs=dict(pruned),
                    run_id=self.spec.run_id,
                    recorded_at=self.ports.clock.now(),
                )
                decision = decide_rewrite(
                    status=status,
                    receipt=self._receipt(stream, day) if status == "data" else None,
                    settlement=Written(expected_units=table.num_rows, present_units=table.num_rows),
                    source_digest=digest,
                    partial_day="refuse",
                )
                self.tally.rewrite_reasons[decision.reason] += 1
                if not decision.writes:
                    await writer.write_receipt(receipt)
                    continue
                try:
                    self.tally.add_write(stream, await writer.write_day(stream, day, table, receipt, availability=True))
                except LaneDayContendedError as error:
                    self.unwritten(day, "contended", describe_error(error), stream=stream)
                    contended = True
                    continue
                wrote_any = True
            if not contended:
                # Last, after every answered output: the day is clean only once all of them landed (M5).
                await writer.write_receipt(
                    DayReceipt(
                        stream=transform_receipt_stream(self.spec.lane.id),
                        day=day,
                        lane=self.spec.lane.id,
                        outcome="derived",
                        input_digests=dict(digests),
                        pruned_inputs=dict(pruned),
                        run_id=self.spec.run_id,
                        recorded_at=self.ports.clock.now(),
                    )
                )
        if wrote_any:
            self.tally.days_written += 1
        else:
            self.tally.days_unchanged += 1

    async def _prune(self, day: date, streams: Sequence[str]) -> None:
        """§4.6: retract superseded provisional input stream-days; a contended one is retried next turn."""
        writer = self._writer()
        with self.phase("write"):
            for stream in streams:
                try:
                    await writer.prune(stream, day)
                except LaneDayContendedError as error:
                    self.log.warn(
                        "plantgeo_lane_turn_prune_contended",
                        stream=stream,
                        day=day.isoformat(),
                        error=describe_error(error),
                    )
                    continue
                self.tally.pruned_stream_days += 1


class _Owe(Protocol):
    """Records one day the turn owes, or (for a free recheck) records nothing."""

    def __call__(self, day: date, reason: UnwrittenReason, detail: str = "") -> None: ...


def _short_day_detail(failed: Sequence[UnitOutcome], *, planned: int, detail: str) -> str:
    """A partial `write_and_recheck` day's S5 detail: which of its units did not answer, and the worst why."""
    units = sorted(outcome.request.unit for outcome in failed)
    named = ", ".join(units[:_DETAIL_UNIT_LIMIT]) + (" ..." if len(units) > _DETAIL_UNIT_LIMIT else "")
    return f"{len(failed)} of {planned} units unanswered, the rest settled ({named}): {detail}"


def _record_nothing(day: date, reason: UnwrittenReason, detail: str = "") -> None:
    """A free recheck's outcome: the day is already served, so nothing is owed."""
    del day, reason, detail


def _checked_probe(answered: object, window: ProbeWindow, *, clip: bool) -> ProviderEdge:
    """A strategy's probe answer, trusted only in shape: its window is the runner's, its days inside it."""
    if not isinstance(answered, ProviderEdge) or answered.status not in PROBE_STATUSES:
        return ProviderEdge(status="unavailable", window=window, detail="probe_edge returned no ProviderEdge")
    valued = answered.valued_days
    if clip:
        valued = frozenset(day for day in valued if window.first <= day <= window.last)
    return replace(answered, window=window, valued_days=valued, source="probe")


def _endpoint_host(spec: TurnSpec) -> str | None:
    """The free host of the lane's endpoint: where the runner's own backoff is metered."""
    if spec.provider is None or spec.lane.source is None:
        return None
    endpoint = spec.provider.endpoints.get(spec.lane.source.endpoint)
    return None if endpoint is None else endpoint.host


def _iso_map(days: Mapping[str, date | None]) -> dict[str, str | None]:
    return {stream: None if day is None else day.isoformat() for stream, day in days.items()}


__all__ = ["FETCH_SHARE_OF_TURN_TIMEOUT", "TurnPorts", "TurnSpec", "run_turn"]
