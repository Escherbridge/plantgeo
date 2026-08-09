"""Author a forward continuation of a completed fixed-window historical backfill plan."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal

import httpx
from pydantic import ValidationError

from agri_data_service.execution.historical_backfill import (
    NASA_POWER_DAILY_SCHEMA_VERSION,
    NASA_POWER_MAX_RESPONSE_BYTES,
    NASA_POWER_TIMEOUT_SECONDS,
    AnalysisGridCell,
    HistoricalBackfillWindow,
    HistoricalNasaBackfillPlan,
    extract_nasa_power_parameter_values,
    four_calendar_years_before,
    historical_nasa_plan_checksum,
    nasa_power_daily_point_url,
    nasa_power_observed_value,
)
from agri_data_service.execution.historical_open_meteo import (
    OPEN_METEO_ARCHIVE_SCHEMA_VERSION,
    HistoricalOpenMeteoArchivePlan,
)
from agri_data_service.ingest.http import UpstreamError
from agri_data_service.ingest.open_meteo import archive_daily_request, fetch_archive_daily

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from agri_data_service.ingest.open_meteo import OpenMeteoArchiveModel

ContinuationPlan = HistoricalNasaBackfillPlan | HistoricalOpenMeteoArchivePlan

# `<lane>-<region>-<signal>-<YYYYMMDD>-<YYYYMMDD>` is the naming convention every plan artifact and
# every `release_set_key` follows. Only the trailing pair is rewritten; the two are retargeted
# independently because they are not always equal (the Open-Meteo VPD plan's key names the lattice
# its filename omits).
PLAN_WINDOW_SUFFIX: Final = re.compile(r"-(\d{8})-(\d{8})$")

# A continuation slides a structurally four-calendar-year window, so it re-requests and re-persists
# the whole overlap under a new release lineage. That cost is paid per continuation regardless of how
# far the window moves, so the only lever is how rarely one is authored. See execution/AGENTS.md
# §plan_continuation.
MINIMUM_CONTINUATION_ADVANCE_DAYS: Final = 30

# `release_set_as_of` is inside the plan checksum, so a re-authored as-of orphans the checkpoint and
# the raw cache. Set it far past any plausible completion rather than forecasting one.
CONTINUATION_AS_OF_HORIZON_DAYS: Final = 30

# Long enough to measure NASA POWER's ~2-month `ALLSKY_SFC_SW_DWN` publication lag, short enough that
# one probe response stays far inside the reviewed byte ceiling.
FRONTIER_PROBE_DAYS: Final = 240
FRONTIER_PROBE_CELL_COUNT: Final = 3

CONTINUATION_NOTE_SENTINEL: Final = "AUTOMATED CONTINUATION"
_CONTINUATION_NOTE_BLOCK: Final = re.compile(
    rf"\n\n{re.escape(CONTINUATION_NOTE_SENTINEL)}\b.*\Z",
    re.DOTALL,
)
_ISO_DATE_LENGTH: Final = 10


class ContinuationLane(StrEnum):
    """The `durable-backfill.sh` lane a plan belongs to."""

    NASA = "nasa"
    OPEN_METEO = "open-meteo"


class ContinuationRefusal(StrEnum):
    """Why no continuation plan was authored. None of these is a fault."""

    NOT_MARKED_COMPLETE = "driver_has_not_marked_the_plan_complete"
    ALREADY_SUPERSEDED = "plan_is_already_superseded_by_a_later_sibling"
    FRONTIER_UNKNOWN = "provider_frontier_is_unknown"
    FRONTIER_IN_FUTURE = "provider_frontier_end_date_is_in_the_future"
    FRONTIER_NOT_ADVANCED = "provider_frontier_has_not_advanced"
    ADVANCE_BELOW_MINIMUM = "advance_below_minimum_continuation_advance_days"
    WOULD_LEAVE_COVERAGE_GAP = "continuation_window_would_leave_an_uncovered_gap"


class PlanContinuationError(ValueError):
    """A fault that stops a continuation from being decided at all."""


@dataclass(frozen=True)
class ParameterFrontier:
    """The newest published day one requested parameter has at one probed cell."""

    parameter: str
    last_available_day: date | None


@dataclass(frozen=True)
class CellFrontier:
    """One probed cell's per-parameter frontier."""

    cell_key: str
    parameters: tuple[ParameterFrontier, ...]


@dataclass(frozen=True)
class ProviderFrontier:
    """The newest day on which every one of a plan's parameters is published, measured not assumed."""

    end_date: date | None
    mode: Literal["probed", "declared"]
    measured_at: datetime
    cells: tuple[CellFrontier, ...]
    limiting_parameters: tuple[str, ...]


@dataclass(frozen=True)
class ContinuationSource:
    """One on-disk plan, the lane that drives it, and whether that driver has retired it."""

    path: Path
    lane: ContinuationLane
    plan: ContinuationPlan
    done_marker_path: Path
    driver_marked_complete: bool

    @property
    def window(self) -> HistoricalBackfillWindow:
        return plan_window(self.plan)

    @property
    def cells(self) -> tuple[AnalysisGridCell, ...]:
        return plan_cells(self.plan)

    @property
    def parameters(self) -> tuple[str, ...]:
        return plan_parameters(self.plan)


@dataclass(frozen=True)
class ContinuationDecision:
    """Everything an operator needs to accept or reject one proposed continuation."""

    source_path: Path
    lane: ContinuationLane
    source_release_set_key: str
    source_window: HistoricalBackfillWindow
    driver_marked_complete: bool
    frontier: ProviderFrontier
    minimum_advance_days: int
    advance_days: int
    overlap_days: int
    cell_count: int
    parameter_count: int
    projected_new_observation_rows: int
    projected_duplicated_observation_rows: int
    continuation_window: HistoricalBackfillWindow | None
    continuation_release_set_key: str | None
    output_path: Path | None
    plan_bytes: bytes | None
    plan_checksum: str | None
    refusal: ContinuationRefusal | None


def plan_window(plan: ContinuationPlan) -> HistoricalBackfillWindow:
    """Return the reviewed window whichever lane's contract carries it."""
    return plan.nasa.window if isinstance(plan, HistoricalNasaBackfillPlan) else plan.window


def plan_cells(plan: ContinuationPlan) -> tuple[AnalysisGridCell, ...]:
    """Return the reviewed cell lattice whichever lane's contract carries it."""
    return tuple(plan.nasa.cells if isinstance(plan, HistoricalNasaBackfillPlan) else plan.cells)


def plan_parameters(plan: ContinuationPlan) -> tuple[str, ...]:
    """Return the reviewed parameter set whichever lane's contract carries it."""
    return tuple(plan.nasa.parameters if isinstance(plan, HistoricalNasaBackfillPlan) else plan.parameters)


def load_continuation_source(plan_path: Path, *, local_execution_root: Path) -> ContinuationSource:
    """Parse one plan file through its own lane contract and read its driver completion marker."""
    payload = plan_path.read_bytes()
    lane, plan = _validated_plan(plan_path, payload)
    done_marker_path = local_execution_root / "locks" / f"{plan_path.stem}.done"
    return ContinuationSource(
        path=plan_path,
        lane=lane,
        plan=plan,
        done_marker_path=done_marker_path,
        driver_marked_complete=done_marker_path.is_file(),
    )


def _plan_lane(document: Mapping[str, object]) -> ContinuationLane | None:
    """Route on the `schema_version` each lane contract pins, never on which keys happen to be present.

    Four Open-Meteo lanes carry `cells` and `chunk_cell_count` at the top level -- the daily archive
    plus CAMS, GloFAS and the ensemble forecast -- so key-shape duck typing routes a CAMS plan into the
    archive contract. Every lane's `schema_version` is a distinct pinned literal or a lane-specific
    pattern, which is what makes it the discriminator. The NASA contract carries its own under `nasa`.
    """
    if document.get("schema_version") == OPEN_METEO_ARCHIVE_SCHEMA_VERSION:
        return ContinuationLane.OPEN_METEO
    nasa = document.get("nasa")
    if isinstance(nasa, dict) and nasa.get("schema_version") == NASA_POWER_DAILY_SCHEMA_VERSION:
        return ContinuationLane.NASA
    return None


def _validated_plan(plan_path: Path, payload: bytes) -> tuple[ContinuationLane, ContinuationPlan]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanContinuationError(f"{plan_path.name} is not UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise PlanContinuationError(f"{plan_path.name} is not a JSON object")
    lane = _plan_lane(document)
    if lane is None:
        raise PlanContinuationError(
            f"{plan_path.name} is not a continuable lane plan (expected schema_version "
            f"'{OPEN_METEO_ARCHIVE_SCHEMA_VERSION}', or '{NASA_POWER_DAILY_SCHEMA_VERSION}' under 'nasa')"
        )
    # A recognised lane whose document does not satisfy its own contract is still this module's
    # refusal, not a bare pydantic error: `scan_plan_staleness` skips a `PlanContinuationError` and
    # would otherwise abort a whole sweep on one malformed file.
    try:
        if lane is ContinuationLane.NASA:
            return lane, HistoricalNasaBackfillPlan.model_validate(document)
        return lane, HistoricalOpenMeteoArchivePlan.model_validate(document)
    except ValidationError as exc:
        raise PlanContinuationError(f"{plan_path.name} does not satisfy the {lane} lane contract: {exc}") from exc


def frontier_probe_cells(cells: Sequence[AnalysisGridCell], count: int) -> tuple[AnalysisGridCell, ...]:
    """Spread the probe evenly over the sorted lattice so one bad cell cannot decide the frontier."""
    if count < 1:
        raise PlanContinuationError("frontier probe cell count must be positive")
    if count >= len(cells):
        return tuple(cells)
    if count == 1:
        return (cells[0],)
    step = (len(cells) - 1) / (count - 1)
    return tuple(cells[index] for index in sorted({round(ordinal * step) for ordinal in range(count)}))


def resolve_frontier(cells: Sequence[CellFrontier], parameters: Sequence[str]) -> tuple[date | None, tuple[str, ...]]:
    """Take the newest day each parameter reaches anywhere, then the oldest of those.

    `require_complete_nasa_result` fails a whole cell when one parameter is short of the window, and
    the Open-Meteo accounting guard demands one coverage row per requested series, so a plan can only
    be extended to the day its *slowest* parameter has reached. Across cells the maximum is taken:
    an out-of-domain cell publishes nothing and must not be read as provider lag.
    """
    per_parameter: dict[str, date | None] = {}
    for parameter in parameters:
        days = [
            entry.last_available_day
            for cell in cells
            for entry in cell.parameters
            if entry.parameter == parameter and entry.last_available_day is not None
        ]
        per_parameter[parameter] = max(days) if days else None
    if any(value is None for value in per_parameter.values()):
        return None, tuple(sorted(name for name, value in per_parameter.items() if value is None))
    resolved = min(value for value in per_parameter.values() if value is not None)
    limiting = tuple(sorted(name for name, value in per_parameter.items() if value == resolved))
    return resolved, limiting


def declared_frontier(end_date: date, *, measured_at: datetime) -> ProviderFrontier:
    """Wrap an operator-supplied cutoff so an offline run carries the same shape as a probe."""
    return ProviderFrontier(
        end_date=end_date,
        mode="declared",
        measured_at=measured_at,
        cells=(),
        limiting_parameters=(),
    )


async def probe_provider_frontier(
    source: ContinuationSource,
    *,
    probe_cell_count: int = FRONTIER_PROBE_CELL_COUNT,
    probe_days: int = FRONTIER_PROBE_DAYS,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> ProviderFrontier:
    """Measure how fresh this exact parameter set can be, from the provider, right now."""
    measured_at = now or datetime.now(UTC)
    end_date = measured_at.date()
    start_date = end_date - timedelta(days=probe_days)
    cells = frontier_probe_cells(source.cells, probe_cell_count)
    parameters = source.parameters
    if client is None:
        async with httpx.AsyncClient(follow_redirects=False) as owned:
            measured = await _probe_cells(source, owned, cells, parameters, start_date, end_date)
    else:
        measured = await _probe_cells(source, client, cells, parameters, start_date, end_date)
    resolved, limiting = resolve_frontier(measured, parameters)
    return ProviderFrontier(
        end_date=resolved,
        mode="probed",
        measured_at=measured_at,
        cells=measured,
        limiting_parameters=limiting,
    )


async def _probe_cells(  # noqa: PLR0913 - one argument per probe dimension, as this module's verbs are
    source: ContinuationSource,
    client: httpx.AsyncClient,
    cells: Sequence[AnalysisGridCell],
    parameters: Sequence[str],
    start_date: date,
    end_date: date,
) -> tuple[CellFrontier, ...]:
    measured: list[CellFrontier] = []
    for cell in cells:
        if isinstance(source.plan, HistoricalNasaBackfillPlan):
            # The plan's own time standard, not a repeat of the contract's default: a frontier probed
            # on a different standard than the replay would be measured a day off from it.
            measured.append(
                await _probe_nasa_cell(
                    client,
                    cell,
                    parameters,
                    start_date,
                    end_date,
                    time_standard=source.plan.nasa.time_standard,
                )
            )
        else:
            # The probe asks for the reanalysis the plan itself declares; this lane serves more than one.
            measured.append(
                await _probe_open_meteo_cell(client, cell, parameters, start_date, end_date, model=source.plan.model)
            )
    return tuple(measured)


async def _probe_nasa_cell(  # noqa: PLR0913 - one argument per probe dimension, as this module's verbs are
    client: httpx.AsyncClient,
    cell: AnalysisGridCell,
    parameters: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    time_standard: str,
) -> CellFrontier:
    url = nasa_power_daily_point_url(
        latitude=cell.latitude,
        longitude=cell.longitude,
        parameters=parameters,
        start_date=start_date,
        end_date=end_date,
        time_standard=time_standard,
    )
    payload = await _bounded_get(client, str(url), NASA_POWER_MAX_RESPONSE_BYTES, NASA_POWER_TIMEOUT_SECONDS)
    try:
        series_by_parameter = extract_nasa_power_parameter_values(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PlanContinuationError(
            f"NASA POWER frontier probe for {cell.cell_key} returned an unusable payload: {exc}"
        ) from exc
    frontiers: list[ParameterFrontier] = []
    for parameter in parameters:
        series = series_by_parameter.get(parameter)
        if series is None:
            raise PlanContinuationError(f"NASA POWER frontier probe response omits requested parameter {parameter}")
        days = [
            date(int(key[0:4]), int(key[4:6]), int(key[6:8]))
            for key, value in series.items()
            if len(key) == len("YYYYMMDD") and key.isdigit() and nasa_power_observed_value(value) is not None
        ]
        frontiers.append(ParameterFrontier(parameter=parameter, last_available_day=max(days) if days else None))
    return CellFrontier(cell_key=cell.cell_key, parameters=tuple(frontiers))


async def _probe_open_meteo_cell(  # noqa: PLR0913 - one argument per probe dimension, as this module's verbs are
    client: httpx.AsyncClient,
    cell: AnalysisGridCell,
    parameters: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    model: OpenMeteoArchiveModel,
) -> CellFrontier:
    request = archive_daily_request(
        [(cell.latitude, cell.longitude)],
        parameters,
        start_date,
        end_date,
        # Taken from the plan, never assumed: this lane serves more than one reanalysis, and a
        # frontier probed against a model that does not publish the variable reads back as
        # "no data since forever" rather than as a lag.
        model=model,
    )
    try:
        body = await fetch_archive_daily(client, request.request_url)
        daily = _open_meteo_daily_block(json.loads(body))
    except (json.JSONDecodeError, UpstreamError, ValueError) as exc:
        # A quota wall reaches the operator as the provider's own wording, never as a traceback.
        raise PlanContinuationError(
            f"Open-Meteo frontier probe for {cell.cell_key} did not return a usable payload: {exc}"
        ) from exc
    days = _open_meteo_days(daily)
    frontiers: list[ParameterFrontier] = []
    for parameter in parameters:
        values = daily.get(parameter)
        if not isinstance(values, list) or len(values) != len(days):
            raise PlanContinuationError(f"Open-Meteo frontier probe response omits requested parameter {parameter}")
        observed = [day for day, value in zip(days, values, strict=True) if value is not None]
        frontiers.append(ParameterFrontier(parameter=parameter, last_available_day=max(observed) if observed else None))
    return CellFrontier(cell_key=cell.cell_key, parameters=tuple(frontiers))


def _open_meteo_daily_block(decoded: object) -> Mapping[str, object]:
    location = decoded[0] if isinstance(decoded, list) and decoded else decoded
    if not isinstance(location, dict):
        raise ValueError("archive response must be a JSON object or a non-empty list of them")
    daily = location.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("archive response is missing its daily block")
    return daily


def _open_meteo_days(daily: Mapping[str, object]) -> tuple[date, ...]:
    axis = daily.get("time")
    if not isinstance(axis, list):
        raise ValueError("archive daily block is missing its time axis")
    days: list[date] = []
    for entry in axis:
        if not isinstance(entry, str) or len(entry) < _ISO_DATE_LENGTH:
            raise ValueError("archive daily time axis must carry ISO date strings")
        days.append(date.fromisoformat(entry[:_ISO_DATE_LENGTH]))
    return tuple(days)


async def _bounded_get(client: httpx.AsyncClient, url: str, max_bytes: int, timeout_seconds: float) -> bytes:
    payload = bytearray()
    async with client.stream(
        "GET",
        url,
        headers={"Accept": "application/json"},
        follow_redirects=False,
        timeout=timeout_seconds,
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise PlanContinuationError(f"frontier probe response exceeds {max_bytes} bytes")
    return bytes(payload)


def continuation_window(frontier_end: date) -> HistoricalBackfillWindow:
    """Slide the window forward; the start moves with it because the contract fixes the span.

    `HistoricalBackfillWindow.require_exact_four_calendar_years` rejects any other shape, so a
    tail-only continuation covering just the new days is structurally impossible on these lanes.
    """
    return HistoricalBackfillWindow(start_date=four_calendar_years_before(frontier_end), end_date=frontier_end)


def retarget_window_suffix(value: str, window: HistoricalBackfillWindow) -> str:
    """Rewrite a trailing `-YYYYMMDD-YYYYMMDD` pair, or append one when the name carries none."""
    suffix = f"-{window.start_date:%Y%m%d}-{window.end_date:%Y%m%d}"
    if PLAN_WINDOW_SUFFIX.search(value) is None:
        return f"{value}{suffix}"
    return PLAN_WINDOW_SUFFIX.sub(suffix, value)


def plan_family(stem: str) -> str:
    """The lane/region/signal identity a plan shares with every window of itself."""
    return PLAN_WINDOW_SUFFIX.sub("", stem)


def superseding_sibling(plan_path: Path, window: HistoricalBackfillWindow, candidates: Sequence[Path]) -> Path | None:
    """Find a same-family plan whose window already reaches at least as far forward as a continuation would.

    Continuing a plan that a later sibling already covers is how the redundant ERA5 plan happened: the
    replay is real work and real rows, and the release-scoped unique index does not dedupe a second
    lineage over the same days. The comparison reads the filename's own date pair rather than parsing
    every candidate, so a directory sweep stays cheap.
    """
    family = plan_family(plan_path.stem)
    for candidate in candidates:
        if candidate == plan_path:
            continue
        match = PLAN_WINDOW_SUFFIX.search(candidate.stem)
        if match is None or plan_family(candidate.stem) != family:
            continue
        if datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=UTC).date() > window.end_date:
            return candidate
    return None


def sibling_plan_candidates(source_path: Path, output_directory: Path) -> tuple[Path, ...]:
    """Every plan a later sibling could already be: beside the source, and where this one would land.

    `historical-plan-continue --output-directory` writes outside the source plan's own directory, so a
    search of only `source.path.parent` cannot see the continuation a previous invocation just wrote,
    and `superseding_sibling` is the one guard against stacking duplicate lineages over the same span.
    Both directories are searched and de-duplicated by resolved path, since they are frequently the
    same directory reached by two spellings.
    """
    directories: dict[Path, Path] = {}
    for directory in (source_path.parent, output_directory):
        directories.setdefault(directory.resolve(), directory)
    candidates: dict[Path, Path] = {}
    for directory in directories.values():
        if not directory.is_dir():
            continue
        for candidate in directory.glob("*.json"):
            candidates.setdefault(candidate.resolve(), candidate)
    return tuple(sorted(candidates.values()))


def continuation_description(
    source: ContinuationSource,
    window: HistoricalBackfillWindow,
    frontier: ProviderFrontier,
) -> str:
    """Carry the continuation's own provenance, replacing any note an earlier continuation wrote."""
    inherited = _CONTINUATION_NOTE_BLOCK.sub("", source.plan.description or "").rstrip()
    limiting = ", ".join(frontier.limiting_parameters) or "not measured"
    probed = ", ".join(cell.cell_key for cell in frontier.cells) or "none"
    note = (
        f"{CONTINUATION_NOTE_SENTINEL} {frontier.measured_at.date().isoformat()}: authored by "
        f"agri-cli historical-plan-continue from {source.path.name}, whose window "
        f"{source.window.start_date.isoformat()}..{source.window.end_date.isoformat()} was complete and could not "
        f"advance itself. The window slides rather than extends because HistoricalBackfillWindow fixes the span at "
        f"four calendar years, so this plan re-requests "
        f"{max((source.window.end_date - window.start_date).days + 1, 0)} already-persisted days under a new release "
        f"lineage; the release-scoped unique index does not dedupe those, so the overlap is a storage and "
        f"throughput cost, and which reader merges it -- and by what precedence -- is that reader's own "
        f"support/source gating, not this plan's (see execution/AGENTS.md). "
        f"end_date is the {frontier.mode} provider frontier "
        f"for this exact parameter set, limited by {limiting}; probed cells: {probed}. Cells, parameters, grid, "
        f"chunking, source governance and transform version are inherited verbatim from the source plan."
    )
    return f"{inherited}\n\n{note}" if inherited else note


def continuation_plan_bytes(
    source: ContinuationSource,
    window: HistoricalBackfillWindow,
    frontier: ProviderFrontier,
    *,
    release_set_as_of: datetime,
) -> tuple[ContinuationPlan, bytes]:
    """Clone the source plan onto the new window and re-validate it through its own lane contract."""
    release_set_key = retarget_window_suffix(source.plan.release_set_key, window)
    description = continuation_description(source, window, frontier)
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
    lane, validated = _validated_plan(source.path, encoded)
    if lane is not source.lane:
        raise PlanContinuationError("continuation plan changed lane, which a clone must never do")
    return validated, encoded


def decide_continuation(  # noqa: PLR0913 - one argument per reviewed knob, as this module's verbs are
    source: ContinuationSource,
    frontier: ProviderFrontier,
    *,
    output_directory: Path,
    minimum_advance_days: int = MINIMUM_CONTINUATION_ADVANCE_DAYS,
    as_of_horizon_days: int = CONTINUATION_AS_OF_HORIZON_DAYS,
    allow_incomplete: bool = False,
    now: datetime | None = None,
) -> ContinuationDecision:
    """Decide whether this lane can move forward far enough to be worth a new release lineage."""
    decided_at = now or datetime.now(UTC)
    window = source.window
    cell_count = len(source.cells)
    parameter_count = len(source.parameters)
    superseded_by = superseding_sibling(source.path, window, sibling_plan_candidates(source.path, output_directory))
    refusal = _refusal(
        source,
        frontier,
        minimum_advance_days=minimum_advance_days,
        allow_incomplete=allow_incomplete,
        superseded_by=superseded_by,
        now=decided_at,
    )
    advance_days = 0 if frontier.end_date is None else max((frontier.end_date - window.end_date).days, 0)
    if refusal is not None:
        return _refused(
            source,
            frontier,
            refusal,
            minimum_advance_days=minimum_advance_days,
            advance_days=advance_days,
            cell_count=cell_count,
            parameter_count=parameter_count,
        )
    frontier_end = frontier.end_date
    if frontier_end is None:  # unreachable once _refusal accepted; kept so the narrowing is typed
        raise PlanContinuationError("an accepted continuation requires a measured provider frontier")
    proposed = continuation_window(frontier_end)
    overlap_days = max((window.end_date - proposed.start_date).days + 1, 0)
    release_set_as_of = datetime.combine(
        decided_at.date() + timedelta(days=as_of_horizon_days), time(23, 59, 59), tzinfo=UTC
    )
    plan, encoded = continuation_plan_bytes(source, proposed, frontier, release_set_as_of=release_set_as_of)
    output_path = output_directory / f"{retarget_window_suffix(source.path.stem, proposed)}.json"
    return ContinuationDecision(
        source_path=source.path,
        lane=source.lane,
        source_release_set_key=source.plan.release_set_key,
        source_window=window,
        driver_marked_complete=source.driver_marked_complete,
        frontier=frontier,
        minimum_advance_days=minimum_advance_days,
        advance_days=advance_days,
        overlap_days=overlap_days,
        cell_count=cell_count,
        parameter_count=parameter_count,
        projected_new_observation_rows=cell_count * parameter_count * advance_days,
        projected_duplicated_observation_rows=cell_count * parameter_count * overlap_days,
        continuation_window=proposed,
        continuation_release_set_key=plan.release_set_key,
        output_path=output_path,
        plan_bytes=encoded,
        plan_checksum=_plan_checksum(plan),
        refusal=None,
    )


def _refusal(  # noqa: PLR0911, PLR0913 - an ordered ladder: one argument per knob, one return per refusal
    source: ContinuationSource,
    frontier: ProviderFrontier,
    *,
    minimum_advance_days: int,
    allow_incomplete: bool,
    superseded_by: Path | None,
    now: datetime,
) -> ContinuationRefusal | None:
    if not source.driver_marked_complete and not allow_incomplete:
        return ContinuationRefusal.NOT_MARKED_COMPLETE
    if superseded_by is not None:
        return ContinuationRefusal.ALREADY_SUPERSEDED
    if frontier.end_date is None:
        return ContinuationRefusal.FRONTIER_UNKNOWN
    # `--end-date` accepts any calendar date, and a declared frontier is an operator's claim rather
    # than a measurement. A day the provider has not reached is not freshness that can be fetched: it
    # spends a full lattice's quota on days that do not exist yet.
    if frontier.end_date > now.date():
        return ContinuationRefusal.FRONTIER_IN_FUTURE
    advance_days = (frontier.end_date - source.window.end_date).days
    if advance_days <= 0:
        return ContinuationRefusal.FRONTIER_NOT_ADVANCED
    if advance_days < minimum_advance_days:
        return ContinuationRefusal.ADVANCE_BELOW_MINIMUM
    # The window slides rather than extends, so a far enough jump moves the START past the day the
    # source stopped and leaves days covered by neither lineage. A continuation may overlap -- it
    # always does -- but it may never leave a hole, so its start is at most one day past the source's
    # end. That bound is what makes "how far can this move" a coverage question, not just a quota one.
    if continuation_window(frontier.end_date).start_date > source.window.end_date + timedelta(days=1):
        return ContinuationRefusal.WOULD_LEAVE_COVERAGE_GAP
    return None


def _refused(  # noqa: PLR0913 - one argument per reported dimension, as this module's verbs are
    source: ContinuationSource,
    frontier: ProviderFrontier,
    refusal: ContinuationRefusal,
    *,
    minimum_advance_days: int,
    advance_days: int,
    cell_count: int,
    parameter_count: int,
) -> ContinuationDecision:
    return ContinuationDecision(
        source_path=source.path,
        lane=source.lane,
        source_release_set_key=source.plan.release_set_key,
        source_window=source.window,
        driver_marked_complete=source.driver_marked_complete,
        frontier=frontier,
        minimum_advance_days=minimum_advance_days,
        advance_days=advance_days,
        overlap_days=0,
        cell_count=cell_count,
        parameter_count=parameter_count,
        projected_new_observation_rows=0,
        projected_duplicated_observation_rows=0,
        continuation_window=None,
        continuation_release_set_key=None,
        output_path=None,
        plan_bytes=None,
        plan_checksum=None,
        refusal=refusal,
    )


def _plan_checksum(plan: ContinuationPlan) -> str:
    if isinstance(plan, HistoricalNasaBackfillPlan):
        return historical_nasa_plan_checksum(plan)
    return plan.plan_checksum


def write_continuation_plan(decision: ContinuationDecision) -> Path:
    """Write the decided plan, refusing to overwrite an artifact that already exists."""
    if decision.output_path is None or decision.plan_bytes is None:
        raise PlanContinuationError("a refused continuation has no plan to write")
    if decision.output_path.exists():
        raise PlanContinuationError(f"{decision.output_path.name} already exists; a plan artifact is never rewritten")
    decision.output_path.parent.mkdir(parents=True, exist_ok=True)
    with decision.output_path.open("wb") as handle:
        handle.write(decision.plan_bytes)
    return decision.output_path


def continuation_decision_payload(decision: ContinuationDecision, *, written: bool) -> dict[str, object]:
    """Render one decision as the JSON line the CLI emits."""
    return {
        "source_plan": str(decision.source_path),
        "lane": str(decision.lane),
        "source_release_set_key": decision.source_release_set_key,
        "source_window": {
            "start_date": decision.source_window.start_date.isoformat(),
            "end_date": decision.source_window.end_date.isoformat(),
        },
        "driver_marked_complete": decision.driver_marked_complete,
        "provider_frontier": _frontier_payload(decision.frontier),
        "minimum_advance_days": decision.minimum_advance_days,
        "advance_days": decision.advance_days,
        "overlap_days": decision.overlap_days,
        "cell_count": decision.cell_count,
        "parameter_count": decision.parameter_count,
        "projected_new_observation_rows": decision.projected_new_observation_rows,
        "projected_duplicated_observation_rows": decision.projected_duplicated_observation_rows,
        "continuation_window": (
            None
            if decision.continuation_window is None
            else {
                "start_date": decision.continuation_window.start_date.isoformat(),
                "end_date": decision.continuation_window.end_date.isoformat(),
            }
        ),
        "continuation_release_set_key": decision.continuation_release_set_key,
        "continuation_plan": None if decision.output_path is None else str(decision.output_path),
        "continuation_plan_checksum": decision.plan_checksum,
        "written": written,
        "refusal": None if decision.refusal is None else str(decision.refusal),
    }


@dataclass(frozen=True)
class PlanStaleness:
    """One plan's distance from the calendar and from what its provider has actually published."""

    path: Path
    lane: ContinuationLane
    release_set_key: str
    window_end: date
    driver_marked_complete: bool
    days_behind_today: int
    frontier: ProviderFrontier | None
    recoverable_days: int | None
    superseded_by: Path | None
    continuable: bool | None


async def scan_plan_staleness(  # noqa: PLR0913 - one argument per reviewed knob, as this module's verbs are
    plan_paths: Sequence[Path],
    *,
    local_execution_root: Path,
    probe: bool = True,
    probe_cell_count: int = FRONTIER_PROBE_CELL_COUNT,
    minimum_advance_days: int = MINIMUM_CONTINUATION_ADVANCE_DAYS,
    now: datetime | None = None,
) -> tuple[PlanStaleness, ...]:
    """Report every continuable plan's staleness, probing each distinct lane/parameter set once.

    The frontier is a property of the provider and the requested parameters, not of a lattice, so
    plans that share both share one probe. That keeps a full sweep inside a keyless quota.
    """
    measured_at = now or datetime.now(UTC)
    probes: dict[tuple[ContinuationLane, tuple[str, ...]], ProviderFrontier] = {}
    report: list[PlanStaleness] = []
    async with httpx.AsyncClient(follow_redirects=False) as client:
        for plan_path in plan_paths:
            try:
                source = load_continuation_source(plan_path, local_execution_root=local_execution_root)
            except PlanContinuationError:
                continue
            frontier: ProviderFrontier | None = None
            if probe:
                # The key omits `model` on purpose but only survives because of a coupling worth
                # naming: `require_governed_lattice` refuses a plan carrying a parameter its own model
                # does not publish, so the parameter set functionally determines the model. If one
                # variable ever became publishable by both archive models, `model` joins this key.
                key = (source.lane, source.parameters)
                if key not in probes:
                    # One group's quota wall or transport fault must not blind the whole sweep: that
                    # group reports an unknown frontier and the remaining lanes are still measured.
                    try:
                        probes[key] = await probe_provider_frontier(
                            source,
                            probe_cell_count=probe_cell_count,
                            now=measured_at,
                            client=client,
                        )
                    except (PlanContinuationError, httpx.HTTPError):
                        probes[key] = ProviderFrontier(
                            end_date=None,
                            mode="probed",
                            measured_at=measured_at,
                            cells=(),
                            limiting_parameters=source.parameters,
                        )
                frontier = probes[key]
            recoverable = (
                None
                if frontier is None or frontier.end_date is None
                else max((frontier.end_date - source.window.end_date).days, 0)
            )
            superseded_by = superseding_sibling(plan_path, source.window, plan_paths)
            report.append(
                PlanStaleness(
                    path=plan_path,
                    lane=source.lane,
                    release_set_key=source.plan.release_set_key,
                    window_end=source.window.end_date,
                    driver_marked_complete=source.driver_marked_complete,
                    days_behind_today=max((measured_at.date() - source.window.end_date).days, 0),
                    frontier=frontier,
                    recoverable_days=recoverable,
                    superseded_by=superseded_by,
                    continuable=(
                        None if recoverable is None else superseded_by is None and recoverable >= minimum_advance_days
                    ),
                )
            )
    return tuple(report)


def plan_staleness_payload(entry: PlanStaleness) -> dict[str, object]:
    """Render one staleness row as the JSON the CLI emits."""
    return {
        "plan": str(entry.path),
        "lane": str(entry.lane),
        "release_set_key": entry.release_set_key,
        "window_end": entry.window_end.isoformat(),
        "driver_marked_complete": entry.driver_marked_complete,
        "days_behind_today": entry.days_behind_today,
        "provider_frontier": None if entry.frontier is None else _frontier_payload(entry.frontier),
        "recoverable_days": entry.recoverable_days,
        "superseded_by": None if entry.superseded_by is None else str(entry.superseded_by),
        "continuable": entry.continuable,
    }


def _frontier_payload(frontier: ProviderFrontier) -> dict[str, object]:
    return {
        "end_date": None if frontier.end_date is None else frontier.end_date.isoformat(),
        "mode": frontier.mode,
        "measured_at": frontier.measured_at.isoformat(),
        "limiting_parameters": list(frontier.limiting_parameters),
        "cells": [
            {
                "cell_key": cell.cell_key,
                "parameters": {
                    entry.parameter: (
                        None if entry.last_available_day is None else entry.last_available_day.isoformat()
                    )
                    for entry in cell.parameters
                },
            }
            for cell in frontier.cells
        ],
    }
