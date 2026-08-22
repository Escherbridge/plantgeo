"""Turn the interior holes a census found into one authored backfill plan, or into a governed absence.

`plan_continuation.py` is the forward-fill model this follows almost verbatim: load a reviewed plan
artifact, decide once whether moving is worth it, emit a typed refusal when it is not, and write
nothing unless explicitly told to. The two differ in exactly one place -- a continuation moves the
window to the provider's live edge, a gap fill moves it to the last day of the oldest hole -- and
everything else is deliberately the same shape so an operator reading one already reads the other.

ONE upstream request per run, not one per day. `contiguous_ranges` collapses 37 scattered missing
days into the handful of runs they actually form, one run is targeted per invocation, and the probe
asks for that whole run in a single request per probed cell. A verb that fetched per day would cost
37 requests to learn one fact.

Nothing here fabricates a day. The two outcomes are:

  * the provider serves the run  -> author a plan; the existing walker fills it.
  * the provider serves nothing  -> record a governed absence in `agri.signal_coverage_audit`, so
                                    the run stops being re-walked and stops being counted as a hole.

The second outcome is MEASURED, never assumed: it requires the probe to have asked for the exact
run and received no value for any requested parameter at any probed cell. An absence recorded on
anything less would be the same class of claim as a lane reporting success having written nothing.

See `src/agri_data_service/execution/AGENTS.md`, the coverage-status/coverage-fill section.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import UUID

import httpx
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.backfill_types import (
    HistoricalBackfillWindow,
    four_calendar_years_before,
)
from agri_data_service.execution.coverage_contract import contiguous_ranges
from agri_data_service.execution.historical_backfill import (
    NASA_POWER_MAX_RESPONSE_BYTES,
    NASA_POWER_SIGNAL_SPECIFICATIONS,
    NASA_POWER_TIMEOUT_SECONDS,
    HistoricalNasaBackfillPlan,
    extract_nasa_power_parameter_values,
    historical_nasa_plan_checksum,
    nasa_power_daily_point_url,
    nasa_power_observed_value,
)
from agri_data_service.execution.historical_open_meteo import (
    OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS,
    HistoricalOpenMeteoArchivePlan,
)
from agri_data_service.execution.plan_continuation import (
    ContinuationLane,
    ContinuationPlan,
    ContinuationSource,
    frontier_probe_cells,
    plan_family,
    retarget_window_suffix,
)
from agri_data_service.ingest.http import UpstreamError
from agri_data_service.ingest.open_meteo import archive_daily_request, fetch_archive_daily

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.coverage_census import LaneCell
    from agri_data_service.execution.coverage_contract import SignalReconciliation
    from agri_data_service.execution.historical_backfill import AnalysisGridCell
    from agri_data_service.ingest.open_meteo import OpenMeteoArchiveModel

_ABSENCE_RELEASE_SQL: Final = text(load_query_sql("execution/coverage_absence_release.sql"))
_INSERT_ABSENCE_SQL: Final = text(load_query_sql("execution/insert_coverage_absence.sql"))

# Three cells, matching `plan_continuation.FRONTIER_PROBE_CELL_COUNT`, and for the same reason: one
# out-of-domain cell must not decide a whole lattice's verdict, and the sample is spread across the
# sorted lattice rather than taken from its head.
GAP_PROBE_CELL_COUNT: Final = 3

# `release_set_as_of` is inside the plan checksum, so a re-authored as-of orphans the checkpoint and
# the raw cache. Thirty days past the decision, matching `CONTINUATION_AS_OF_HORIZON_DAYS`, is far
# enough past any plausible completion of one four-year window that it never needs re-authoring.
GAP_FILL_AS_OF_HORIZON_DAYS: Final = 30

# The token that separates a gap-fill lineage from a continuation lineage in both the artifact name
# and the `release_set_key`. It sits before the window suffix so `plan_family` reads the two as
# different families, which is what stops `superseding_sibling` in the continuation verb from ever
# mistaking a gap fill for a forward move of the same plan.
GAP_FILL_FAMILY_TOKEN: Final = "-gapfill"

GAP_FILL_NOTE_SENTINEL: Final = "AUTOMATED GAP FILL"

# The probe's release identity. Frozen strings rather than a version derived from anything mutable:
# `payload_checksum` plus these three columns are `uq_source_release_identity`, so re-probing the
# same run must land on the same row rather than minting a second lineage of the same evidence.
GAP_PROBE_SOURCE_VERSION: Final = "coverage-gap-probe"
GAP_PROBE_SCHEMA_VERSION: Final = "coverage-gap-probe-v1"
GAP_PROBE_TRANSFORM_VERSION: Final = "coverage-gap-probe-v1"

_ISO_DATE_LENGTH: Final = 10


class CoverageFillError(ValueError):
    """A fault that stops a fill from being decided at all."""


class FillRefusal(StrEnum):
    """Why no plan was authored. None of these is a fault; each is a normal scheduled outcome."""

    NOTHING_MISSING = "lane_has_no_missing_days"
    GAP_EXCEEDS_WINDOW_SPAN = "gap_is_longer_than_the_contract_s_four_calendar_year_window"
    GAP_AT_LIVE_EDGE = "gap_reaches_a_day_the_provider_has_not_published_yet"
    PLAN_ALREADY_AUTHORED = "a_plan_artifact_for_this_gap_already_exists"
    UPSTREAM_SERVES_NOTHING = "provider_published_nothing_for_this_gap_so_it_is_an_absence"


class GapVerdict(StrEnum):
    """What the provider said when asked for the whole run."""

    SERVED = "provider_serves_this_gap"
    """At least one requested parameter carried at least one value. The gap is real and fillable."""

    EMPTY = "provider_serves_nothing_in_this_gap"
    """Nothing, anywhere, for anything asked. Evidence of an absence, not of a hole."""


@dataclass(frozen=True, slots=True)
class GapTarget:
    """One contiguous run of missing days, and which of the lane's signals are missing inside it."""

    first_day: date
    last_day: date
    signal_names: tuple[str, ...]

    @property
    def day_count(self) -> int:
        return (self.last_day - self.first_day).days + 1


@dataclass(frozen=True, slots=True)
class ProbedParameter:
    """How many days of the probed run one requested parameter actually carried a value on."""

    parameter: str
    observed_day_count: int


@dataclass(frozen=True, slots=True)
class ProbedCell:
    """One probed cell's answer, parameter by parameter."""

    cell_key: str
    parameters: tuple[ProbedParameter, ...]


@dataclass(frozen=True, slots=True)
class GapProbe:
    """What the provider returned when asked for one whole run, measured not assumed."""

    start_day: date
    end_day: date
    requested_day_count: int
    measured_at: datetime
    cells: tuple[ProbedCell, ...]

    @property
    def observed_day_total(self) -> int:
        """Every value-carrying day summed over every probed cell and parameter."""
        return sum(entry.observed_day_count for cell in self.cells for entry in cell.parameters)

    @property
    def served_parameters(self) -> tuple[str, ...]:
        """Parameters that carried at least one value somewhere in the run."""
        return tuple(
            sorted(
                {entry.parameter for cell in self.cells for entry in cell.parameters if entry.observed_day_count > 0}
            )
        )

    @property
    def empty_parameters(self) -> tuple[str, ...]:
        """Parameters that carried nothing at any probed cell across the whole run."""
        served = set(self.served_parameters)
        return tuple(sorted({entry.parameter for cell in self.cells for entry in cell.parameters} - served))


@dataclass(frozen=True, slots=True)
class GovernedAbsenceRow:
    """One `agri.signal_coverage_audit` row: one cell, one signal, one whole run, `no_data`."""

    cell_id: UUID
    cell_key: str
    signal_name: str
    source_parameter: str
    support_key: str
    window_start: datetime
    window_end: datetime
    expected_observation_count: int


@dataclass(frozen=True, slots=True)
class PlannedAbsence:
    """The governed absence a run would record, and the evidence that justifies it."""

    start_day: date
    end_day: date
    day_count: int
    rows: tuple[GovernedAbsenceRow, ...]
    payload_checksum: str
    query_parameters: str
    quality_summary: str

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class CoverageFillDecision:
    """Everything an operator needs to accept or reject one proposed gap fill."""

    source_path: Path
    lane: ContinuationLane
    source_key: str
    source_release_set_key: str
    source_window: HistoricalBackfillWindow
    missing_day_count: int
    gap_range_count: int
    target: GapTarget | None
    probe: GapProbe | None
    verdict: GapVerdict | None
    fill_window: HistoricalBackfillWindow | None
    fill_release_set_key: str | None
    output_path: Path | None
    plan_bytes: bytes | None
    plan_checksum: str | None
    projected_observation_rows: int
    absence: PlannedAbsence | None
    refusal: FillRefusal | None


def lane_gap_targets(signals: Sequence[SignalReconciliation]) -> tuple[GapTarget, ...]:
    """Collapse every signal's missing days into the runs a single fetch would cover.

    The union is taken across signals on purpose: one upstream request returns every parameter of
    the lane at once, so a run where only `precipitation` is missing costs exactly what a run where
    all eleven are missing costs. Planning per signal would multiply the request count by eleven and
    buy nothing.
    """
    missing_by_signal = {signal.signal_name: frozenset(signal.missing_days) for signal in signals}
    union: set[date] = set()
    for days in missing_by_signal.values():
        union.update(days)
    targets: list[GapTarget] = []
    for first_day, last_day in contiguous_ranges(sorted(union)):
        span = {first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1)}
        targets.append(
            GapTarget(
                first_day=first_day,
                last_day=last_day,
                signal_names=tuple(sorted(name for name, days in missing_by_signal.items() if days & span)),
            )
        )
    return tuple(targets)


def oldest_gap_target(targets: Sequence[GapTarget]) -> GapTarget | None:
    """Drain oldest first, so a lane converges instead of thrashing on whichever hole is newest."""
    return targets[0] if targets else None


def fill_window(target: GapTarget) -> HistoricalBackfillWindow:
    """Anchor a four-calendar-year window on the run's last day.

    `HistoricalBackfillWindow.require_exact_four_calendar_years` rejects any other shape, so a
    window covering only the hole is structurally impossible on these lanes -- the same constraint
    `plan_continuation.continuation_window` lives under. The end is pinned to the run's last day
    rather than to today so the walk never spends quota on days beyond the hole it is closing.
    """
    return HistoricalBackfillWindow(
        start_date=four_calendar_years_before(target.last_day),
        end_date=target.last_day,
    )


def fill_plan_stem(source_stem: str, window: HistoricalBackfillWindow) -> str:
    """`<family>-gapfill-<YYYYMMDD>-<YYYYMMDD>`, so a fill is never mistaken for a continuation."""
    return retarget_window_suffix(f"{plan_family(source_stem)}{GAP_FILL_FAMILY_TOKEN}", window)


def fill_description(
    source: ContinuationSource,
    target: GapTarget,
    window: HistoricalBackfillWindow,
    probe: GapProbe,
) -> str:
    """Carry the fill's own provenance: which hole, measured how, and what the window really costs."""
    inherited = (source.plan.description or "").rstrip()
    served = ", ".join(probe.served_parameters) or "none"
    probed = ", ".join(cell.cell_key for cell in probe.cells) or "none"
    note = (
        f"{GAP_FILL_NOTE_SENTINEL} {probe.measured_at.date().isoformat()}: authored by agri-cli "
        f"coverage-fill from {source.path.name} to close the interior gap "
        f"{target.first_day.isoformat()}..{target.last_day.isoformat()} ({target.day_count} days) in signals "
        f"{', '.join(target.signal_names)}. The provider was asked for exactly that span at cells {probed} "
        f"and answered with values for {served}, which is why this is treated as a fillable hole rather than "
        f"as an absence. end_date is the gap's last day, not the provider frontier, so this plan spends no "
        f"quota past the hole it closes; start_date follows from it because HistoricalBackfillWindow fixes the "
        f"span at four calendar years, so this plan re-requests "
        f"{max((window.end_date - window.start_date).days + 1 - target.day_count, 0)} already-persisted days "
        f"under a new release lineage. The release-scoped unique index does not dedupe those, so the overlap "
        f"is a storage and throughput cost, and which reader merges it -- and by what precedence -- is that "
        f"reader's own support/source gating, not this plan's (see execution/AGENTS.md). Cells, parameters, "
        f"grid, chunking, source governance and transform version are inherited verbatim from the source plan."
    )
    return f"{inherited}\n\n{note}" if inherited else note


def fill_plan_bytes(
    source: ContinuationSource,
    target: GapTarget,
    window: HistoricalBackfillWindow,
    probe: GapProbe,
    *,
    release_set_as_of: datetime,
) -> tuple[ContinuationPlan, bytes]:
    """Clone the source plan onto the gap window and re-validate it through its own lane contract."""
    release_set_key = retarget_window_suffix(
        f"{plan_family(source.plan.release_set_key)}{GAP_FILL_FAMILY_TOKEN}", window
    )
    description = fill_description(source, target, window, probe)
    if isinstance(source.plan, HistoricalNasaBackfillPlan):
        draft: ContinuationPlan = source.plan.model_copy(
            update={
                "nasa": source.plan.nasa.model_copy(update={"window": window}),
                "release_set_key": release_set_key,
                "release_set_as_of": release_set_as_of,
                "description": description,
            }
        )
    else:
        draft = source.plan.model_copy(
            update={
                "window": window,
                "release_set_key": release_set_key,
                "release_set_as_of": release_set_as_of,
                "description": description,
            }
        )
    # `model_copy` skips validators, so the emitted bytes are re-parsed through the same contract the
    # backfill verb uses. A plan that cannot survive that round trip is never written.
    payload = json.dumps(draft.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    encoded = payload.encode("utf-8")
    validated = _revalidated(source.lane, encoded)
    return validated, encoded


def unprobed_refusal(
    source: ContinuationSource,
    target: GapTarget,
    *,
    output_directory: Path,
    now: datetime,
) -> FillRefusal | None:
    """The refusals that need no upstream request, in the order they are cheapest to decide.

    Separated out so `probe_gap_window` is only ever called when its answer can still change the
    outcome. A verb that probed first and refused afterwards would spend a quota-bound request to
    learn something it already knew.
    """
    if target.last_day >= now.date():
        # A hole touching today is not an interior gap; it is the live edge the forward refresh
        # owns. Filling it here would spend a whole lattice's quota on days the provider has not
        # published, and would then record their emptiness as a governed absence -- retiring days
        # that are simply not out yet.
        return FillRefusal.GAP_AT_LIVE_EDGE
    window = fill_window(target)
    if window.start_date > target.first_day:
        # The window slides rather than extends, so a run longer than four calendar years cannot be
        # covered by one plan. Refusing is correct: authoring the tail would silently leave the head
        # of the same run unplanned and it would look filled on the next census run.
        return FillRefusal.GAP_EXCEEDS_WINDOW_SPAN
    if (output_directory / f"{fill_plan_stem(source.path.stem, window)}.json").exists():
        # Idempotence. The previous run already authored this exact fill; re-authoring it would
        # either overwrite a reviewed artifact or stack a second lineage over the same days.
        return FillRefusal.PLAN_ALREADY_AUTHORED
    return None


def gap_to_probe(
    source: ContinuationSource,
    signals: Sequence[SignalReconciliation],
    *,
    output_directory: Path,
    now: datetime | None = None,
) -> GapTarget | None:
    """The run this invocation would spend its one upstream request on, or None when it would not."""
    target = oldest_gap_target(lane_gap_targets(signals))
    if target is None:
        return None
    decided_at = now or datetime.now(UTC)
    if unprobed_refusal(source, target, output_directory=output_directory, now=decided_at) is not None:
        return None
    return target


def decide_coverage_fill(  # noqa: PLR0913 - one argument per reviewed knob, as this module's verbs are
    source: ContinuationSource,
    signals: Sequence[SignalReconciliation],
    *,
    output_directory: Path,
    lane_cells: Mapping[str, LaneCell],
    support_key: str,
    probe: GapProbe | None = None,
    as_of_horizon_days: int = GAP_FILL_AS_OF_HORIZON_DAYS,
    now: datetime | None = None,
) -> CoverageFillDecision:
    """Decide what to do about the oldest hole: author a plan, record an absence, or refuse.

    Pure apart from one `Path.exists` check. The probe is supplied rather than performed here, so
    the whole decision ladder is unit-testable with no network and no database.
    """
    decided_at = now or datetime.now(UTC)
    targets = lane_gap_targets(signals)
    missing_day_count = sum(target.day_count for target in targets)
    target = oldest_gap_target(targets)
    if target is None:
        return _refused(source, FillRefusal.NOTHING_MISSING, missing_day_count=0, gap_range_count=0)
    cheap = unprobed_refusal(source, target, output_directory=output_directory, now=decided_at)
    if cheap is not None:
        return _refused(source, cheap, missing_day_count=missing_day_count, gap_range_count=len(targets), target=target)
    window = fill_window(target)
    output_path = output_directory / f"{fill_plan_stem(source.path.stem, window)}.json"
    if probe is None:
        raise CoverageFillError("a fillable gap requires a measured provider probe before it can be decided")
    verdict = gap_probe_verdict(probe)
    if verdict is GapVerdict.EMPTY:
        absence = planned_absence(source, target, probe, lane_cells=lane_cells, support_key=support_key)
        return _refused(
            source,
            FillRefusal.UPSTREAM_SERVES_NOTHING,
            missing_day_count=missing_day_count,
            gap_range_count=len(targets),
            target=target,
            probe=probe,
            verdict=verdict,
            absence=absence,
        )
    release_set_as_of = datetime.combine(
        decided_at.date() + timedelta(days=as_of_horizon_days), time(23, 59, 59), tzinfo=UTC
    )
    plan, encoded = fill_plan_bytes(source, target, window, probe, release_set_as_of=release_set_as_of)
    return CoverageFillDecision(
        source_path=source.path,
        lane=source.lane,
        source_key=source.plan.source.key,
        source_release_set_key=source.plan.release_set_key,
        source_window=source.window,
        missing_day_count=missing_day_count,
        gap_range_count=len(targets),
        target=target,
        probe=probe,
        verdict=verdict,
        fill_window=window,
        fill_release_set_key=plan.release_set_key,
        output_path=output_path,
        plan_bytes=encoded,
        plan_checksum=_plan_checksum(plan),
        projected_observation_rows=len(source.cells) * len(source.parameters) * window.day_count,
        absence=None,
        refusal=None,
    )


def gap_probe_verdict(probe: GapProbe) -> GapVerdict:
    """Nothing anywhere for anything asked is an absence; anything at all is a fillable hole.

    Deliberately binary. A run the provider serves for some parameters and not others is SERVED:
    the walk itself already writes a per-parameter `no_data` audit row for the ones that come back
    empty, and second-guessing that here would record an absence for a series the walk was about to
    describe more precisely.
    """
    if not probe.cells:
        raise CoverageFillError("a gap verdict requires at least one probed cell")
    return GapVerdict.EMPTY if probe.observed_day_total == 0 else GapVerdict.SERVED


def planned_absence(
    source: ContinuationSource,
    target: GapTarget,
    probe: GapProbe,
    *,
    lane_cells: Mapping[str, LaneCell],
    support_key: str,
) -> PlannedAbsence:
    """Build the governed-absence rows for a run the provider serves nothing in.

    One row per probed cell per requested parameter, each spanning the WHOLE run. Not one row per
    day: a four-year window with no data is one honest gap record, and 1,462 of them would say the
    same thing 1,462 times. Not one row per lattice cell either -- the evidence is what was probed,
    and inflating three measurements into 397 claims is the fabrication this module exists to avoid.
    """
    window_start = _midnight_utc(target.first_day)
    window_end = _midnight_utc(target.last_day)
    rows: list[GovernedAbsenceRow] = []
    for cell in probe.cells:
        lane_cell = lane_cells.get(cell.cell_key)
        if lane_cell is None:
            raise CoverageFillError(
                f"probed cell {cell.cell_key} is not in the warehouse lattice, so its absence has nothing to attach to"
            )
        rows.extend(
            GovernedAbsenceRow(
                cell_id=lane_cell.cell_id,
                cell_key=lane_cell.cell_key,
                signal_name=signal_name_for(source.lane, parameter),
                source_parameter=parameter,
                support_key=support_key,
                window_start=window_start,
                window_end=window_end,
                expected_observation_count=target.day_count,
            )
            for parameter in sorted(source.parameters)
        )
    query_parameters = json.dumps(
        {
            "verb": "coverage-fill",
            "lane": str(source.lane),
            "source_plan": source.path.name,
            "start_day": target.first_day.isoformat(),
            "end_day": target.last_day.isoformat(),
            "parameters": sorted(source.parameters),
            "probed_cell_keys": [cell.cell_key for cell in probe.cells],
        },
        sort_keys=True,
    )
    quality_summary = json.dumps(
        {
            "requested_day_count": probe.requested_day_count,
            "observed_day_total": probe.observed_day_total,
            "served_parameters": list(probe.served_parameters),
            "empty_parameters": list(probe.empty_parameters),
            "measured_at": probe.measured_at.isoformat(),
        },
        sort_keys=True,
    )
    return PlannedAbsence(
        start_day=target.first_day,
        end_day=target.last_day,
        day_count=target.day_count,
        rows=tuple(rows),
        # The fingerprint of the REQUEST, not of a payload: there is no payload. Two runs probing
        # the same span with the same parameters at the same cells are the same evidence and must
        # land on the same release row rather than minting a second lineage of it.
        payload_checksum=hashlib.sha256(query_parameters.encode("utf-8")).hexdigest(),
        query_parameters=query_parameters,
        quality_summary=quality_summary,
    )


def plan_signal_names(source: ContinuationSource) -> frozenset[str]:
    """The warehouse signals this plan's own parameters persist. See AGENTS.md, coverage-fill."""
    return frozenset(signal_name_for(source.lane, parameter) for parameter in source.parameters)


def signals_this_plan_can_fill(
    source: ContinuationSource,
    signals: Sequence[SignalReconciliation],
) -> tuple[SignalReconciliation, ...]:
    """Narrow a lane census to the signals this plan actually fetches.

    A lane's contracts span every signal the source publishes, but one reviewed plan carries one
    parameter subset -- NASA POWER's eleven signals are split across `weather-fast`,
    `weather-radiation` and `soil-wetness`, and the three ERA5-Land plans partition its eight the
    same way (measured 2026-08-11). Without this, a hole in `soil_wetness_profile` would be targeted
    by the `weather-fast` plan, whose authored window fetches no soil parameter at all: the walk
    would run, the plan would look filled, and the hole would still be there on the next census.
    """
    fillable = plan_signal_names(source)
    return tuple(signal for signal in signals if signal.signal_name in fillable)


def signal_name_for(lane: ContinuationLane, parameter: str) -> str:
    """Map a provider variable onto the warehouse signal its lane persists it as."""
    if lane is ContinuationLane.NASA:
        specification = NASA_POWER_SIGNAL_SPECIFICATIONS.get(parameter)
        if specification is None:
            raise CoverageFillError(f"NASA POWER parameter {parameter} has no declared warehouse signal")
        return specification[0]
    archive = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS.get(parameter)
    if archive is None:
        raise CoverageFillError(f"Open-Meteo parameter {parameter} has no declared warehouse signal")
    return archive.signal_name


async def probe_gap_window(
    source: ContinuationSource,
    target: GapTarget,
    *,
    probe_cell_count: int = GAP_PROBE_CELL_COUNT,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> GapProbe:
    """Ask the provider for the whole run once, and count what actually came back."""
    measured_at = now or datetime.now(UTC)
    cells = frontier_probe_cells(source.cells, probe_cell_count)
    if client is None:
        async with httpx.AsyncClient(follow_redirects=False) as owned:
            measured = await _probe_cells(source, owned, cells, target)
    else:
        measured = await _probe_cells(source, client, cells, target)
    return GapProbe(
        start_day=target.first_day,
        end_day=target.last_day,
        requested_day_count=target.day_count,
        measured_at=measured_at,
        cells=measured,
    )


async def _probe_cells(
    source: ContinuationSource,
    client: httpx.AsyncClient,
    cells: Sequence[AnalysisGridCell],
    target: GapTarget,
) -> tuple[ProbedCell, ...]:
    measured: list[ProbedCell] = []
    for cell in cells:
        if isinstance(source.plan, HistoricalNasaBackfillPlan):
            # The plan's own time standard, never the contract default: a run probed on a different
            # standard than the replay would be measured a day off from it.
            measured.append(
                await _probe_nasa_cell(
                    client, cell, source.parameters, target, time_standard=source.plan.nasa.time_standard
                )
            )
        else:
            measured.append(await _probe_open_meteo_cell(client, cell, source.parameters, target, source.plan.model))
    return tuple(measured)


async def _probe_nasa_cell(
    client: httpx.AsyncClient,
    cell: AnalysisGridCell,
    parameters: Sequence[str],
    target: GapTarget,
    *,
    time_standard: str,
) -> ProbedCell:
    url = nasa_power_daily_point_url(
        latitude=cell.latitude,
        longitude=cell.longitude,
        parameters=parameters,
        start_date=target.first_day,
        end_date=target.last_day,
        time_standard=time_standard,
    )
    payload = await _bounded_get(client, str(url), NASA_POWER_MAX_RESPONSE_BYTES, NASA_POWER_TIMEOUT_SECONDS)
    try:
        series_by_parameter = extract_nasa_power_parameter_values(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CoverageFillError(
            f"NASA POWER gap probe for {cell.cell_key} returned an unusable payload: {exc}"
        ) from exc
    counted: list[ProbedParameter] = []
    for parameter in parameters:
        series = series_by_parameter.get(parameter)
        if series is None:
            raise CoverageFillError(f"NASA POWER gap probe response omits requested parameter {parameter}")
        counted.append(
            ProbedParameter(
                parameter=parameter,
                # `nasa_power_observed_value` is what separates a real value from the provider's
                # -999 fill sentinel. Counting raw keys instead would report a fully-served window
                # for a run the provider padded with fills, and the absence would never be recorded.
                observed_day_count=sum(1 for value in series.values() if nasa_power_observed_value(value) is not None),
            )
        )
    return ProbedCell(cell_key=cell.cell_key, parameters=tuple(counted))


async def _probe_open_meteo_cell(
    client: httpx.AsyncClient,
    cell: AnalysisGridCell,
    parameters: Sequence[str],
    target: GapTarget,
    model: OpenMeteoArchiveModel,
) -> ProbedCell:
    request = archive_daily_request(
        [(cell.latitude, cell.longitude)],
        parameters,
        target.first_day,
        target.last_day,
        # Taken from the plan, never assumed: this lane serves more than one reanalysis, and a run
        # probed against a model that does not publish the variable reads back as a permanent
        # absence rather than as the wrong question.
        model=model,
    )
    try:
        body = await fetch_archive_daily(client, request.request_url)
        daily = _open_meteo_daily_block(json.loads(body))
    except (json.JSONDecodeError, UpstreamError, ValueError) as exc:
        # A quota wall reaches the operator as the provider's own wording, never as a traceback.
        raise CoverageFillError(
            f"Open-Meteo gap probe for {cell.cell_key} did not return a usable payload: {exc}"
        ) from exc
    day_count = len(_open_meteo_days(daily))
    counted: list[ProbedParameter] = []
    for parameter in parameters:
        values = daily.get(parameter)
        if not isinstance(values, list) or len(values) != day_count:
            raise CoverageFillError(f"Open-Meteo gap probe response omits requested parameter {parameter}")
        counted.append(
            ProbedParameter(
                parameter=parameter,
                observed_day_count=sum(1 for value in values if value is not None),
            )
        )
    return ProbedCell(cell_key=cell.cell_key, parameters=tuple(counted))


def _open_meteo_daily_block(decoded: object) -> Mapping[str, object]:
    location = decoded[0] if isinstance(decoded, list) and decoded else decoded
    if not isinstance(location, dict):
        raise CoverageFillError("archive response must be a JSON object or a non-empty list of them")
    daily = location.get("daily")
    if not isinstance(daily, dict):
        raise CoverageFillError("archive response is missing its daily block")
    return daily


def _open_meteo_days(daily: Mapping[str, object]) -> tuple[date, ...]:
    axis = daily.get("time")
    if not isinstance(axis, list):
        raise CoverageFillError("archive daily block is missing its time axis")
    days: list[date] = []
    for entry in axis:
        if not isinstance(entry, str) or len(entry) < _ISO_DATE_LENGTH:
            raise CoverageFillError("archive daily time axis must carry ISO date strings")
        days.append(date.fromisoformat(entry[:_ISO_DATE_LENGTH]))
    return tuple(days)


async def _bounded_get(client: httpx.AsyncClient, url: str, max_bytes: int, timeout_seconds: float) -> bytes:
    payload = bytearray()
    async with client.stream(
        "GET", url, headers={"Accept": "application/json"}, follow_redirects=False, timeout=timeout_seconds
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise CoverageFillError(f"gap probe response exceeds {max_bytes} bytes")
    return bytes(payload)


def write_fill_plan(decision: CoverageFillDecision) -> Path:
    """Write the decided plan, refusing to overwrite an artifact that already exists."""
    if decision.output_path is None or decision.plan_bytes is None:
        raise CoverageFillError("a refused fill has no plan to write")
    if decision.output_path.exists():
        raise CoverageFillError(f"{decision.output_path.name} already exists; a plan artifact is never rewritten")
    decision.output_path.parent.mkdir(parents=True, exist_ok=True)
    with decision.output_path.open("wb") as handle:
        handle.write(decision.plan_bytes)
    return decision.output_path


async def record_governed_absence(
    session: AsyncSession,
    decision: CoverageFillDecision,
    *,
    now: datetime | None = None,
) -> int:
    """Register the probe's release, then write one `no_data` audit row per probed cell and signal.

    Returns the number of rows the database actually INSERTED, never the number offered. The
    statement ends `on conflict ... do nothing`, so a second `--apply` over the same run offers the
    same rows and writes none of them; reporting the offer would print a write that did not happen.
    """
    absence = decision.absence
    if absence is None:
        raise CoverageFillError("only a fill refused as an absence has anything to record")
    recorded_at = now or datetime.now(UTC)
    release = await session.execute(
        _ABSENCE_RELEASE_SQL,
        {
            "source_key": decision.source_key,
            "source_version": GAP_PROBE_SOURCE_VERSION,
            "retrieved_at": recorded_at,
            "observed_from": _midnight_utc(absence.start_day),
            "observed_to": _midnight_utc(absence.end_day),
            "payload_checksum": absence.payload_checksum,
            "schema_version": GAP_PROBE_SCHEMA_VERSION,
            "transform_version": GAP_PROBE_TRANSFORM_VERSION,
            "query_parameters": absence.query_parameters,
            "quality_summary": absence.quality_summary,
        },
    )
    source_release_id: object = release.scalar_one_or_none()
    if source_release_id is None:
        raise CoverageFillError(
            f"no agri.data_source row carries key {decision.source_key}, so this absence has no lane to belong to"
        )
    if not isinstance(source_release_id, UUID):
        raise CoverageFillError("agri.source_release.id must be a uuid")
    written = 0
    for row in absence.rows:
        inserted = await session.execute(
            _INSERT_ABSENCE_SQL,
            {
                "source_release_id": source_release_id,
                "cell_id": row.cell_id,
                "signal_name": row.signal_name,
                "source_parameter": row.source_parameter,
                "support_key": row.support_key,
                "window_start": row.window_start,
                "window_end": row.window_end,
                "expected_observation_count": row.expected_observation_count,
                "details": json.dumps(
                    {
                        "recorded_by": "coverage-fill",
                        "cell_key": row.cell_key,
                        "probe": json.loads(absence.quality_summary),
                        "request": json.loads(absence.query_parameters),
                    },
                    sort_keys=True,
                ),
            },
        )
        written += len(inserted.scalars().all())
    return written


def coverage_fill_payload(
    decision: CoverageFillDecision,
    *,
    plan_written: bool,
    absence_rows_written: int,
) -> dict[str, object]:
    """Render one decision as the JSON line the CLI emits."""
    return {
        "source_plan": str(decision.source_path),
        "lane": str(decision.lane),
        "source_key": decision.source_key,
        "source_release_set_key": decision.source_release_set_key,
        "source_window": {
            "start_date": decision.source_window.start_date.isoformat(),
            "end_date": decision.source_window.end_date.isoformat(),
        },
        "missing_day_count": decision.missing_day_count,
        "gap_range_count": decision.gap_range_count,
        "target_gap": (
            None
            if decision.target is None
            else {
                "first_day": decision.target.first_day.isoformat(),
                "last_day": decision.target.last_day.isoformat(),
                "day_count": decision.target.day_count,
                "signal_names": list(decision.target.signal_names),
            }
        ),
        "provider_probe": None if decision.probe is None else _probe_payload(decision.probe),
        "verdict": None if decision.verdict is None else str(decision.verdict),
        "fill_window": (
            None
            if decision.fill_window is None
            else {
                "start_date": decision.fill_window.start_date.isoformat(),
                "end_date": decision.fill_window.end_date.isoformat(),
            }
        ),
        "fill_release_set_key": decision.fill_release_set_key,
        "fill_plan": None if decision.output_path is None else str(decision.output_path),
        "fill_plan_checksum": decision.plan_checksum,
        "projected_observation_rows": decision.projected_observation_rows,
        "governed_absence": (
            None
            if decision.absence is None
            else {
                "start_day": decision.absence.start_day.isoformat(),
                "end_day": decision.absence.end_day.isoformat(),
                "day_count": decision.absence.day_count,
                "row_count": decision.absence.row_count,
                "payload_checksum": decision.absence.payload_checksum,
            }
        ),
        "plan_written": plan_written,
        "absence_rows_written": absence_rows_written,
        "refusal": None if decision.refusal is None else str(decision.refusal),
    }


def _probe_payload(probe: GapProbe) -> dict[str, object]:
    return {
        "start_day": probe.start_day.isoformat(),
        "end_day": probe.end_day.isoformat(),
        "requested_day_count": probe.requested_day_count,
        "measured_at": probe.measured_at.isoformat(),
        "observed_day_total": probe.observed_day_total,
        "served_parameters": list(probe.served_parameters),
        "empty_parameters": list(probe.empty_parameters),
        "cells": [
            {
                "cell_key": cell.cell_key,
                "parameters": [
                    {"parameter": entry.parameter, "observed_day_count": entry.observed_day_count}
                    for entry in cell.parameters
                ],
            }
            for cell in probe.cells
        ],
    }


def _refused(  # noqa: PLR0913 - one argument per reported dimension, as this module's verbs are
    source: ContinuationSource,
    refusal: FillRefusal,
    *,
    missing_day_count: int,
    gap_range_count: int,
    target: GapTarget | None = None,
    probe: GapProbe | None = None,
    verdict: GapVerdict | None = None,
    absence: PlannedAbsence | None = None,
) -> CoverageFillDecision:
    return CoverageFillDecision(
        source_path=source.path,
        lane=source.lane,
        source_key=source.plan.source.key,
        source_release_set_key=source.plan.release_set_key,
        source_window=source.window,
        missing_day_count=missing_day_count,
        gap_range_count=gap_range_count,
        target=target,
        probe=probe,
        verdict=verdict,
        fill_window=None,
        fill_release_set_key=None,
        output_path=None,
        plan_bytes=None,
        plan_checksum=None,
        projected_observation_rows=0,
        absence=absence,
        refusal=refusal,
    )


def _revalidated(lane: ContinuationLane, payload: bytes) -> ContinuationPlan:
    if lane is ContinuationLane.NASA:
        return HistoricalNasaBackfillPlan.model_validate_json(payload)
    return HistoricalOpenMeteoArchivePlan.model_validate_json(payload)


def _plan_checksum(plan: ContinuationPlan) -> str:
    if isinstance(plan, HistoricalNasaBackfillPlan):
        return historical_nasa_plan_checksum(plan)
    return plan.plan_checksum


def _midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=UTC)
