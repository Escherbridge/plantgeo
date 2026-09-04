"""The agent's bounded read seam onto the day-partitioned Parquet warehouse.

Every environmental answer the agent gives comes through here: day resolution and the four states
from `parquet_ops.serving`, rows from one admitted, memory-capped DuckDB session, and coverage from
the availability index. No second definition of a day, a state or a lane root lives in this module.
See `agent/AGENTS.md`, "Reading the Parquet warehouse", for why the agent shares the map's core
rather than calling its HTTP surface.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol

import duckdb

from agri_data_service.agent.surfaces import AGENT_ZOOM_TIER
from agri_data_service.config import settings
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.availability_coverage import (
    AvailabilityCoverageReaderHolder,
    resolve_availability_lanes,
)
from agri_data_service.parquet_ops.coverage import registered_census_lanes
from agri_data_service.parquet_ops.duckdb_session import run_serving_read
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.parquet_ops.serving import day_status_sets, read_absence_evidence
from agri_data_service.parquet_ops.warehouse_reader import ObjectStoreListing, part_keys_for_day
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from contextvars import Token

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.coverage import CensusLane
    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.serving import DayStatusSets
    from agri_data_service.parquet_ops.warehouse_reader import WarehouseListing
    from agri_data_service.parquet_ops.wire import AbsenceEvidence
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityIndex

#: Every agent read addresses the OBSERVED half of a lane. A forecast is a different question and
#: `forecast_summary_for_cell` answers it from the governed ML plane, not from `kind=forecast`.
OBSERVED: Final[PartitionKind] = "observed"

#: How many written day partitions ONE agent read may address.
#:
#: CHOSEN, NOT MEASURED, and the tools say so. A day partition is at least one object-store GET, so
#: a decade-deep window is not a longer index range the way it was against the dropped matview -- it
#: is thousands of GETs on a request path. 120 keeps the worst case inside the serving read budget
#: while covering a season of any daily lane. When a caller's window holds more written days than
#: this, the read is NARROWED TO THE NEWEST ONES and the narrowed span is echoed in the payload:
#: answering a decade from four months of it, silently, is the fabricated-absence bug in another
#: costume.
MAX_SCANNED_DAY_PARTITIONS: Final = 120


class AgentWarehouseSource(Protocol):
    """The three capabilities an agent answer needs; one object so a test can replace all three."""

    def listing(self) -> WarehouseListing:
        """Return the object-store listing the day resolution walks."""
        ...

    async def run[T](self, work: Callable[[ServingSession], T], *, operation: str) -> T:
        """Run one bounded read on an admitted, memory-capped DuckDB session."""
        ...

    def availability_evidence(self, layers: Sequence[str], now: datetime) -> tuple[LaneEvidence, ...]:
        """Answer each lane's coverage from its published availability index. Blocking; run in a thread."""
        ...


@dataclass(frozen=True, slots=True)
class LaneWindow:
    """One lane-tier's day states over a bounded span, and the part files behind the written days."""

    layer: str
    kind: PartitionKind
    tier: ZoomTier
    keys: tuple[str, ...]
    statuses: DayStatusSets
    lane_written: bool

    def state_of(self, day: date) -> str:
        """Name the ONE state this lane is in on `day`, in the frozen four-state vocabulary."""
        if day in self.statuses.conflict:
            return "day_conflict"
        if day in self.statuses.incomplete:
            return "day_incomplete"
        if day in self.statuses.absent:
            return "governed_absence"
        if day in self.statuses.data:
            return "published"
        return "day_not_written" if self.lane_written else "lane_never_written"

    def published_days(self, first_day: date, last_day: date) -> tuple[date, ...]:
        """Every day of the closed range this lane actually wrote rows for, ascending."""
        return tuple(sorted(day for day in self.statuses.data if first_day <= day <= last_day))

    def resolvable_days(self, first_day: date, last_day: date) -> tuple[date, ...]:
        """Days the lane settled either way -- rows or a governed absence -- ascending."""
        return tuple(sorted(day for day in self.statuses.resolvable if first_day <= day <= last_day))

    def part_keys(self, days: Sequence[date]) -> tuple[str, ...]:
        """Every part file of the named days, in the layout's own order."""
        return tuple(
            key
            for day in days
            for key in part_keys_for_day(self.keys, layer=self.layer, kind=self.kind, tier=self.tier, day=day)
        )

    def raise_on_unserveable(self, days: Sequence[date]) -> None:
        """Refuse a span holding a day the warehouse cannot state, before any of it is read."""
        for day in days:
            if day in self.statuses.conflict:
                raise faults.day_conflict(layer=self.layer, day=day.isoformat())
            if day in self.statuses.incomplete:
                raise faults.day_incomplete(layer=self.layer, day=day.isoformat())


@dataclass(frozen=True, slots=True)
class ScannedSpan:
    """The days a read actually addressed, after the partition budget narrowed the caller's window."""

    days: tuple[date, ...]
    requested_from: date
    requested_through: date
    narrowed: bool

    @property
    def scanned_from(self) -> date:
        """The oldest day this read looked at; equal to `requested_from` unless the budget narrowed it."""
        return self.days[0] if self.days else self.requested_from

    @property
    def scanned_through(self) -> date:
        """The newest day this read looked at."""
        return self.days[-1] if self.days else self.requested_through


def narrow_to_budget(
    days: Sequence[date],
    *,
    requested_from: date,
    requested_through: date,
    budget: int = MAX_SCANNED_DAY_PARTITIONS,
) -> ScannedSpan:
    """Keep the NEWEST `budget` written days and record that the older half was never looked at."""
    ordered = tuple(sorted(days))
    if len(ordered) <= budget:
        return ScannedSpan(
            days=ordered,
            requested_from=requested_from,
            requested_through=requested_through,
            narrowed=False,
        )
    return ScannedSpan(
        days=ordered[-budget:],
        requested_from=requested_from,
        requested_through=requested_through,
        narrowed=True,
    )


# --- Ambient run state -------------------------------------------------------------
#
# The source travels in a ContextVar for the same reason the reader session does: a tool function's
# signature IS its model-facing schema, so a warehouse parameter would become something the model is
# asked to supply. `agent.tools.run_context` binds it for the duration of one run.


class _ObjectStoreSource:
    """The production source: one boto-backed listing, the admitted read pool, the index reader."""

    def __init__(self) -> None:
        self._listing: ObjectStoreListing | None = None
        self._availability = AvailabilityCoverageReaderHolder()

    def listing(self) -> WarehouseListing:
        """Build the listing once per process; a client per request costs more than the read does."""
        if self._listing is None:
            credentials = settings.require_object_store()
            self._listing = ObjectStoreListing(
                backend=BotoObjectStoreBackend.from_credentials(credentials),
                prefix=settings.object_store_prefix,
            )
        return self._listing

    async def run[T](self, work: Callable[[ServingSession], T], *, operation: str) -> T:
        """Take one of the three process-wide serving slots, then open a capped session inside it."""
        return await run_serving_read(
            settings.require_object_store(),
            work,
            prefix=settings.object_store_prefix,
            operation=operation,
        )

    def availability_evidence(self, layers: Sequence[str], now: datetime) -> tuple[LaneEvidence, ...]:
        """Resolve every named lane against its published index, under the configured authority.

        `resolve_availability_lanes` is asked FIRST and decides the policy question -- unpublished,
        stale, malformed, checksum-invalid, or a `static_lookup` lane that owns no index at all.
        Only a lane it admitted is read a second time for its rows, and that second read is served
        from the reader's own generation cache rather than from the network.
        """
        reader = self._availability.get(settings)
        registered = {lane.layer: lane for lane in registered_census_lanes()}
        known: list[CensusLane] = [registered[layer] for layer in layers if layer in registered]
        resolution = resolve_availability_lanes(
            reader,
            lanes=known,
            policy=settings.parquet_coverage_authority,
            now=now,
        )
        withheld_by_lane = {entry.layer: entry.reason for entry in resolution.withheld}
        census_layers = {lane.layer for lane in resolution.census_lanes}
        evidence: list[LaneEvidence] = []
        for layer in layers:
            lane = registered.get(layer)
            if lane is None:
                evidence.append(LaneEvidence(layer=layer, unregistered=True))
                continue
            reason = withheld_by_lane.get(layer)
            if reason is None and layer in census_layers:
                # `census_until_bootstrap`, or a `static_lookup` lane that owns no index at all.
                # Either way the only remaining evidence is the whole-stream listing the agent
                # refuses to pay, so the lane says why rather than reporting itself empty.
                reason = "availability_unpublished"
            if reason is not None:
                evidence.append(LaneEvidence(layer=layer, nature=lane.nature, withheld_reason=reason))
                continue
            evidence.append(fold_availability_index(reader.read(lane, now=now), layer=layer, nature=lane.nature))
        return tuple(evidence)


_default_source: Final = _ObjectStoreSource()

_warehouse_source: ContextVar[AgentWarehouseSource] = ContextVar("agri_agent_warehouse_source", default=_default_source)


def set_source(chosen: AgentWarehouseSource | None) -> Token[AgentWarehouseSource]:
    """Bind one run's warehouse source and return the token that restores the previous one.

    `None` KEEPS WHATEVER IS ALREADY BOUND rather than reaching for the object store. A run that
    names no source in a process that has one bound -- a test suite, above all -- must not fall
    through to the production bucket, which is how `test_agent_graph.py` spent 17 seconds reading
    live R2 objects before this was noticed.
    """
    return _warehouse_source.set(chosen or _warehouse_source.get())


def reset_source(token: Token[AgentWarehouseSource]) -> None:
    """Restore the source bound before `set_source`."""
    _warehouse_source.reset(token)


def source() -> AgentWarehouseSource:
    """Return the source bound for this run."""
    return _warehouse_source.get()


# --- Day resolution ----------------------------------------------------------------


#: EVERY OBJECT-STORE CALL BELOW RUNS OFF THE EVENT LOOP. `ObjectStoreListing` is boto3, which is
#: synchronous: a listing issued directly from a coroutine stalls every other request this Sanic
#: worker is serving for the length of an S3 LIST. The HTTP route never does it either -- its
#: `resolve_day` runs inside `run_serving_read`'s worker thread -- so the agent does the same thing
#: rather than a cheaper-looking one.


async def lane_window(
    *,
    layer: str,
    first_day: date,
    last_day: date,
    kind: PartitionKind = OBSERVED,
    tier: ZoomTier = AGENT_ZOOM_TIER,
) -> LaneWindow:
    """Classify every day of a closed range through the SAME rule the map's `/day` route applies.

    `day_status_sets` is imported rather than re-implemented, so a derived rung that generalised
    every base row away reads `data` here and in the census, and a marker with no parts beside it --
    a LOST rung -- reads `incomplete` in both and is refused rather than served as an unwritten day.
    """
    listing = source().listing()

    def walk() -> LaneWindow:
        keys = _keys_for_months(listing, layer=layer, kind=kind, tier=tier, first_day=first_day, last_day=last_day)
        statuses = day_status_sets(keys, layer=layer, kind=kind, tier=tier)
        # A non-empty month listing already proves the lane wrote SOMETHING at this tier, so the
        # whole-tier probe is paid only by a window that fell outside every written month.
        lane_written = bool(keys) or bool(listing.list_keys(layer, kind, tier))
        return LaneWindow(layer=layer, kind=kind, tier=tier, keys=keys, statuses=statuses, lane_written=lane_written)

    return await asyncio.to_thread(walk)


async def lane_years(
    *,
    layer: str,
    years: Sequence[int],
    kind: PartitionKind = OBSERVED,
    tier: ZoomTier = AGENT_ZOOM_TIER,
) -> LaneWindow:
    """Classify whole calendar years at once; a release lane's window is sparse and months cost more."""
    listing = source().listing()
    wanted = tuple(years)

    def walk() -> LaneWindow:
        keys = tuple(sorted({key for year in wanted for key in listing.list_keys(layer, kind, tier, year=year)}))
        statuses = day_status_sets(keys, layer=layer, kind=kind, tier=tier)
        lane_written = bool(keys) or bool(listing.list_keys(layer, kind, tier))
        return LaneWindow(layer=layer, kind=kind, tier=tier, keys=keys, statuses=statuses, lane_written=lane_written)

    return await asyncio.to_thread(walk)


async def absence_evidence(window: LaneWindow, day: date) -> AbsenceEvidence:
    """Decode one governed-absence marker; an absence served without its evidence is not one."""
    scope = _absence_scope(window)
    listing = source().listing()
    return await asyncio.to_thread(read_absence_evidence, listing, scope=scope, day=day)


def _absence_scope(window: LaneWindow) -> ReadScope:
    """Build the scope the shared absence decoder keys its marker path from."""
    return ReadScope(layer=window.layer, kind=window.kind, tier=window.tier, bbox=None)


def _keys_for_months(  # noqa: PLR0913 - one coordinate per partition axis the listing needs
    listing: WarehouseListing,
    *,
    layer: str,
    kind: PartitionKind,
    tier: ZoomTier,
    first_day: date,
    last_day: date,
) -> tuple[str, ...]:
    """List every month the closed range touches, once each."""
    months: list[tuple[int, int]] = []
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        months.append((cursor.year, cursor.month))
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == _DECEMBER else date(cursor.year, cursor.month + 1, 1)
    return tuple(
        sorted({key for year, month in months for key in listing.list_keys(layer, kind, tier, year=year, month=month)})
    )


_DECEMBER: Final = 12


# --- Bounded row reads -------------------------------------------------------------


#: The probe that turns a schema drift into a typed refusal instead of a raw binder error.
#:
#: MEASURED AGAINST PRODUCTION 2026-09-04, and this is why the probe exists rather than a comment
#: about robustness: `layer=signal/kind=observed/zoom=13/year=2026/month=08/day=06/part-0.parquet`
#: carries eleven columns and NEITHER `cell_longitude` NOR `cell_latitude`, although
#: `warehouse/parquet/schema.py` declares both non-nullable. The lane was exported before the
#: positions were added and has not been re-exported since. Without this probe every signal tool
#: answers a `duckdb.BinderException` -- an unexplained tool error, which is precisely the outcome
#: the refusal discipline exists to prevent.
_COLUMN_PROBE: Final = "SELECT DISTINCT name FROM parquet_schema(?)"

#: A schema footer or two below which the probe just reads every part anyway; no sampling saved.
_COLUMN_PROBE_SAMPLE_THRESHOLD: Final = 2


def _column_probe_sample(uris: Sequence[str]) -> list[str]:
    """Probe the OLDEST and NEWEST part, not every footer `union_by_name` will open anyway.

    A schema drift is a property of WHEN a part was exported, not of which of one day's several
    parts it is, so the two ends of the caller's ascending-by-day list bound every generation a read
    could see. This is what keeps the probe from re-opening up to `MAX_SCANNED_DAY_PARTITIONS`
    duplicate metadata GETs on a request path; a drift confined to a middle day, between two
    correctly-shaped exports, would still slip past a two-key sample.
    """
    if len(uris) <= _COLUMN_PROBE_SAMPLE_THRESHOLD:
        return list(uris)
    return [uris[0], uris[-1]]


def lane_columns_absent(*, layer: str, columns: Sequence[str], key: str) -> faults.ServingRefusalError:
    """Refuse a read whose objects do not carry the columns the lane's registered schema promises."""
    return faults.ServingRefusalError(
        "lane_columns_absent",
        f"{layer} declares {', '.join(columns)} in its registered schema and the published objects "
        f"(for example {key}) do not carry them, so this question cannot be asked of the bytes that "
        "exist. The lane owes a re-export; this is a statement about the published objects and says "
        "nothing about whether the data exists",
    )


async def scan(  # noqa: PLR0913 - mirrors scan_all's binding contract, one coordinate per part-file read
    statement: str,
    parameters: Sequence[object],
    *,
    part_keys: Sequence[str],
    operation: str,
    layer: str,
    required_columns: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Run one bounded DuckDB statement over an explicit part-file list, on an admitted session."""
    answers = await scan_all(
        ((statement, parameters),),
        part_keys=part_keys,
        operation=operation,
        layer=layer,
        required_columns=required_columns,
    )
    return answers[0]


async def scan_all(
    reads: Sequence[tuple[str, Sequence[object]]],
    *,
    part_keys: Sequence[str],
    operation: str,
    layer: str,
    required_columns: Sequence[str] = (),
) -> list[list[dict[str, Any]]]:
    """Run several statements over the SAME part files inside ONE admitted session.

    One session and one admission slot, because a tool asking two questions of one day should not
    queue twice behind the three-slot serving gate, and because the second statement then reads the
    part files this process has already opened.

    The keys are resolved by the layout parser before they arrive here and rendered to `s3://` URIs
    by the session that owns the bucket, so no caller composes an object path and no path reaches
    DuckDB as text this module concatenated.

    `required_columns` is checked ONCE, across the whole read, before any statement runs. The union
    is the right grain because `union_by_name=true` makes a column visible to every statement as
    soon as ONE object carries it, so a column missing from the union is missing from all of them.
    """
    if not part_keys:
        return [[] for _ in reads]

    def work(session: ServingSession) -> list[list[dict[str, Any]]]:
        uris = [session.object_uri(key) for key in part_keys]
        try:
            if required_columns:
                probe_uris = _column_probe_sample(uris)
                present = {str(row[0]) for row in session.connection.execute(_COLUMN_PROBE, [probe_uris]).fetchall()}
                missing = tuple(sorted(set(required_columns) - present))
                if missing:
                    raise lane_columns_absent(layer=layer, columns=missing, key=part_keys[0])
            answered: list[list[dict[str, Any]]] = []
            for statement, parameters in reads:
                cursor = session.connection.execute(statement, [uris, *parameters])
                columns = [description[0] for description in cursor.description or ()]
                answered.append([dict(zip(columns, values, strict=True)) for values in cursor.fetchall()])
        except duckdb.Error as exc:
            # Mirrors `interface/http/parquet_routes.py::_as_refusal` -- `OutOfMemoryException` is
            # the memory guard doing its job, and every reader of this warehouse must refuse it the
            # same honest way rather than let a raw DuckDB fault reach the model, contradicting the
            # promise `tools.py` makes about what this decorator refuses.
            raise faults.read_over_budget(operation=operation) from exc
        return answered

    return await source().run(work, operation=operation)


# --- Coverage --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DayEvidence:
    """What one lane's availability index says about one day at the rung that holds its rows."""

    state: str
    row_count: int
    published_at: datetime | None
    absence_reason: str | None


@dataclass(frozen=True, slots=True)
class LaneEvidence:
    """One lane's settled day states, ALREADY FOLDED, or the single reason it could prove none.

    The fold happens where the index is read rather than here, so this carries an answer and not a
    parsing job: `days` holds one entry per SELECTABLE day at the rung that holds the rows, and a
    lane that could not prove itself carries no days at all and a `withheld_reason` that says why.
    """

    layer: str
    #: What this lane's partition day MEANS, carried from its registration so a static lookup's
    #: version stamp is never reported as an observation day.
    nature: str | None = None
    days: Mapping[date, DayEvidence] = field(default_factory=dict)
    #: The newest day this lane's SOURCE could have published; a later empty day is not a gap.
    source_ceiling_day: date | None = None
    withheld_reason: str | None = None
    unregistered: bool = False

    @property
    def proven(self) -> bool:
        """True when this lane published an index this process trusts, and false for every refusal."""
        return self.withheld_reason is None and not self.unregistered

    @property
    def published_days(self) -> tuple[date, ...]:
        """Every selectable day this lane holds rows for, ascending."""
        return tuple(sorted(day for day, entry in self.days.items() if entry.state == "published"))


def fold_availability_index(index: AvailabilityIndex, *, layer: str, nature: str | None) -> LaneEvidence:
    """Fold one published index into settled per-day evidence, taken at the rung that holds the rows.

    SELECTABLE DAYS ONLY. `selectable_days()` is the index's own rule -- a day whose whole
    authoritative rung set agrees on one terminal state, one source receipt and one absence reason --
    and a day that fails it is not evidence of anything, so it is left out rather than reported at
    whichever rung happened to answer. The ceiling closes the top: a day past what the source could
    have published is not a gap the lane owes.
    """
    ceiling = index.pointer.source_ceiling
    selectable = frozenset(day for day in index.selectable_days() if day <= ceiling)
    rung = max(index.pointer.required_rungs)
    return LaneEvidence(
        layer=layer,
        nature=nature,
        days={
            row.day: DayEvidence(
                state=row.terminal_state,
                row_count=row.row_count,
                published_at=row.published_at,
                absence_reason=row.absence_reason,
            )
            for row in index.rows
            if row.day in selectable and row.rung == rung
        },
        source_ceiling_day=ceiling,
    )


async def lane_evidence(layers: Sequence[str], *, now: datetime | None = None) -> tuple[LaneEvidence, ...]:
    """Answer each lane's coverage from its availability index; never from a whole-stream listing.

    ONE POINTER GET AND ONE BOUNDED GENERATION GET per lane, which is what makes a coverage question
    affordable on a request path at all. A lane the index cannot answer is REPORTED WITHHELD with its
    reason and is never quietly filled in from an object listing: the listing walk is the whole-stream
    LIST the A4 tripwire forbids here, and a lane answered from a different evidence source than the
    map used could disagree with the slider about the same day.
    """
    moment = now or datetime.now(UTC)
    return await asyncio.to_thread(source().availability_evidence, layers, moment)


def surface_covered_days(evidence: Sequence[LaneEvidence]) -> frozenset[date]:
    """Days EVERY lane of a surface published rows for; a surface is only as covered as its thinnest lane.

    The intersection, not the union. `soil-field-temperature` is four depth lanes and
    `climate-field-air-temperature` three statistics: a day one of them is missing is a day the
    surface cannot be drawn, and reporting it covered would put the agent one step ahead of the map.
    `parquet-slider-capabilities.ts::commonPublishedRanges` intersects for the same reason.
    """
    if not evidence:
        return frozenset()
    covered: frozenset[date] | None = None
    for lane in evidence:
        published = frozenset(lane.published_days)
        covered = published if covered is None else covered & published
    return covered or frozenset()
