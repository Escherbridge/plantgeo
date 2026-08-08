"""Cross-stream completeness and validity report over the warehouse, the slider window it produces, and its lanes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from itertools import pairwise
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text

from agri_data_service.ingest.archive_walk import archive_lane_definition_name
from agri_data_service.ingest.identity import MAX_NATURAL_KEY_LENGTH, PRODUCER_BY_LAYER_NAME
from agri_data_service.ingest.lanes import BACKFILL_LANES, FIRMS_ARCHIVE_LANE, STREAMFLOW_ARCHIVE_LANE
from agri_data_service.ingest.policy import parse_bbox, resolve_bounded_bbox

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

# ---------------------------------------------------------------------------------------------------------------
# Constants mirrored from the TypeScript read model. See ingest/AGENTS.md "validation.py".
#
# THE COUPLING IS THE POINT: this report exists to answer "what will the user actually be able to scrub
# through", and it can only answer that if it applies the SAME two axis rules the slider applies. A report
# that used a bare MIN(observed_day) would call water-gauges complete back to 1990 while the slider starts it
# at 2026-08. Every value below is copied from
# `src/lib/server/services/environmental-read-model.ts`; the line numbers are where each one lives today.
#
# THE COUPLING IS ENFORCED, not trusted: `tests/test_ingest_validation.py` reads the two axis constants back
# out of that TypeScript file and asserts they equal the mirrors here, so a change on either side fails the
# Python suite loudly. Line numbers drift, so the test greps the declarations by name rather than by line.
# ---------------------------------------------------------------------------------------------------------------

# Repository-root-relative, which is what makes it readable from both the Python suite and a report reader.
MIRRORED_READ_MODEL_PATH: Final = "src/lib/server/services/environmental-read-model.ts"

# environmental-read-model.ts:1961 `OBSERVATION_CLUSTER_GAP_DAYS`. Gap, in days, that ends a layer's
# continuous record. 21, NOT 45: measured against production the largest gap inside water-gauges' real
# record is 15 days while the gap back to its 1990s stragglers is 23, and fire-perimeters' largest real gap
# is 12 against 324 back to its isolated 2025-07-28 row. Widening this to 45 would swallow the 23-day
# straggler gap and hand the slider a 36-year axis again.
OBSERVATION_CLUSTER_GAP_DAYS: Final = 21

# environmental-read-model.ts:1998 `OBSERVATION_DENSITY_FLOOR_FRACTION`. A day must carry at least this
# fraction of the busiest day in the newest cluster to anchor the start of the axis. 1% sits inside the
# measured band (0.064%, 5.26%] that holds for every layer at once.
OBSERVATION_DENSITY_FLOOR_FRACTION: Final = Decimal("0.01")

# Postgres evaluates `CEIL(MAX(observation_count) * 0.01::numeric)` in exact numeric arithmetic. Doing the
# same multiplication in binary floating point disagrees: 300 * 0.01 is 3.0000000000000004 as a double, so
# math.ceil answers 4 where Postgres answers 3, and the report would exclude a day the slider keeps.
# Decimal reproduces the numeric result exactly, which is why the fraction above is a Decimal.
MINIMUM_DENSITY_FLOOR: Final = 1

# environmental-read-model.ts:117 `USGS_NO_DATA_SENTINEL`. USGS NWIS writes this in place of a reading it
# does not have. Compared EXACTLY, never as "negative means missing": genuine reverse flow is recorded at
# these gauges down to -172,000 cfs.
USGS_NO_DATA_SENTINEL: Final = -999999.0

# environmental-read-model.ts:2290-2292, the `observed` CTE of readObservationWindows. The axis counts only
# published rows that are linked to geo.geometry; an unlinked row is drawn on the map but is invisible to the
# time axis, so counting it here would advertise a day the slider cannot serve.
PUBLISHED_FEATURE_STATUS: Final = "published"

# environmental-read-model.ts:2168 `METRIC_SOURCES["streamflow-cfs"].missingValueSentinel`, which names the
# one property the sentinel is stored in. Only water-gauges has an upstream "no reading" marker that is a
# real number rather than an absent key.
MISSING_VALUE_SENTINEL_PROPERTY_BY_STREAM: Final[Mapping[str, str]] = MappingProxyType({"water-gauges": "flowCfs"})


def producer_local_id_ceiling(producer: str) -> int:
    """The longest stored `properties->>'id'` identity.py can still namespace inside its 255-character key."""
    # writer.py:66-68 stores `identity.producer_local_id`, NOT `identity.natural_key` -- the producer prefix is
    # deliberately absent from the stored id and lives only on the geometry dimension's key. So the ceiling the
    # warehouse must respect is the natural key's 255 minus the producer token and its colon. Checking for the
    # prefix instead would flag every producer-backed row in production as malformed; measured 2026-08-08,
    # that mistake reported 476,016 of 476,016 fire-detections rows as broken.
    return MAX_NATURAL_KEY_LENGTH - len(producer) - 1


PRODUCER_LOCAL_ID_CEILING_BY_STREAM: Final[Mapping[str, int]] = MappingProxyType(
    {stream: producer_local_id_ceiling(producer) for stream, producer in PRODUCER_BY_LAYER_NAME.items()}
)

# ---------------------------------------------------------------------------------------------------------------
# Scan bounds. Every statement below is read-only, holds no lock beyond the snapshot, and runs inside a
# transaction pinned READ ONLY with a 120-second statement timeout (the repo's direct-SQL convention).
# ---------------------------------------------------------------------------------------------------------------

# The full-scan convention for direct SQL in this service; see ingest/AGENTS.md.
STATEMENT_TIMEOUT_SECONDS: Final = 120

# The day series is one row per (stream, observed day). Production holds ~2,200 such rows across eleven
# layers and fourteen years. 200,000 is two orders of magnitude of headroom and still small enough to hold in
# memory. Hitting it is REFUSED rather than truncated: a truncated day series silently invents gaps at the
# tail, which is exactly the defect this report exists to find.
MAX_OBSERVED_DAY_ROWS: Final = 200_000

# One row per (lane, run, work-item status). Eight statuses times any plausible lane-and-run count; a lane
# mints one extra run each time its floor is lowered, which is a handful over the service's whole life.
MAX_LANE_STATE_ROWS: Final = 1_000

# The worst gaps and the thinnest days are listed explicitly; the rest are counted. Never silently truncated
# -- both caps report how many entries they omitted.
MAX_REPORTED_GAPS: Final = 10
MAX_REPORTED_THIN_DAYS: Final = 10

_ISO_DAY_IN_SHARD_KEY: Final = re.compile(r"\d{4}-\d{2}-\d{2}")

# A work item in one of these states is settled; anything else is a window the lane still owes.
SETTLED_WORK_ITEM_STATES: Final[frozenset[str]] = frozenset({"succeeded", "cancelled"})
DEAD_LETTER_WORK_ITEM_STATE: Final = "dead_letter"

NO_DETAIL: Final[Mapping[str, object]] = MappingProxyType({})

StreamKind = Literal["time_series", "snapshot", "reference"]
StreamStore = Literal["features", "drought_areas", "historical_table"]
StreamVerdict = Literal["complete", "incomplete", "invalid"]
ExpectedFirstDaySource = Literal["declared", "lane_floor", "first_observed", "none"]

# environmental-read-model.ts:2005-2013 `EarliestObservedDateRule`, member for member.
EarliestObservedDayRule = Literal["gap_clustered", "density_floored", "full_history", "no_observations"]


class ValidationRowError(RuntimeError):
    """Raised when a result column comes back in a shape the report's own SQL does not declare."""


class ObservedDayScanTooLargeError(RuntimeError):
    """Raised when the day series exceeds its row cap; truncating it would invent gaps that are not there."""


# ---------------------------------------------------------------------------------------------------------------
# What each validity finding breaks downstream. A count with no consequence beside it is a number nobody acts on.
# ---------------------------------------------------------------------------------------------------------------

NULL_GEOMETRY_CHECK: Final = "null_geom"
UNLINKED_GEOMETRY_CHECK: Final = "unlinked_geometry"
MISSING_EXTERNAL_ID_CHECK: Final = "missing_external_id"
MALFORMED_IDENTITY_CHECK: Final = "malformed_identity"
DUPLICATE_IDENTITY_CHECK: Final = "duplicate_identity"
UNDATED_DAY_CHECK: Final = "undated_day"
FUTURE_DAY_CHECK: Final = "future_day"
OUTSIDE_BBOX_CHECK: Final = "outside_bbox"
MISSING_VALUE_SENTINEL_CHECK: Final = "missing_value_sentinel"

VALIDITY_CHECK_ORDER: Final[tuple[str, ...]] = (
    NULL_GEOMETRY_CHECK,
    UNLINKED_GEOMETRY_CHECK,
    MISSING_EXTERNAL_ID_CHECK,
    MALFORMED_IDENTITY_CHECK,
    DUPLICATE_IDENTITY_CHECK,
    UNDATED_DAY_CHECK,
    FUTURE_DAY_CHECK,
    OUTSIDE_BBOX_CHECK,
    MISSING_VALUE_SENTINEL_CHECK,
)

VALIDITY_CHECK_CONSEQUENCES: Final[Mapping[str, str]] = MappingProxyType(
    {
        NULL_GEOMETRY_CHECK: (
            "the row is stored with no shape, so no viewport query can ever return it and no tile can draw it"
        ),
        UNLINKED_GEOMETRY_CHECK: (
            "readObservationWindows requires geometry_id IS NOT NULL, so the row is invisible to the time axis "
            "while the map still draws it -- the slider loses the day, not the pixel"
        ),
        MISSING_EXTERNAL_ID_CHECK: (
            "there is no producer-local key to refresh against, so the next run inserts a second row instead of "
            "updating this one and the duplicate count grows every tick"
        ),
        MALFORMED_IDENTITY_CHECK: (
            "the stored producer-local id is too long for identity.py to namespace, so FeatureIdentity refuses "
            "to rebuild it and the next refresh cannot match the row it is meant to update"
        ),
        DUPLICATE_IDENTITY_CHECK: (
            "two published rows claim one producer-local key, so a viewport draws the feature twice and every "
            "per-feature aggregate double-counts it"
        ),
        UNDATED_DAY_CHECK: (
            "geo.feature_observation_day returns NULL, so the client filter treats the row as undated and shows "
            "it at EVERY date on the slider instead of on its own day"
        ),
        FUTURE_DAY_CHECK: (
            "the observed day is past the server's own day, so the row sits beyond the right edge of the axis "
            "and no date the user can select will reach it"
        ),
        OUTSIDE_BBOX_CHECK: (
            "the row falls outside INGEST_BBOX, so it was written past the bounded-ingestion contract and no "
            "cron tick will ever refresh or retire it"
        ),
        MISSING_VALUE_SENTINEL_CHECK: (
            "-999999 is USGS's 'no reading' marker stored as a JSON number, so it is served as a real "
            "measurement and flattens every colour scale it lands in"
        ),
    }
)


# ---------------------------------------------------------------------------------------------------------------
# The declared shape of a stream. Cadence is what turns a gap into a finding, so it is stated per stream and
# its basis is stated with it.
# ---------------------------------------------------------------------------------------------------------------

# Every `agri.job_definition.name` the durable runtime can actually mint, DERIVED and never spelled here.
# `archive_walk.archive_lane_definition_name` is the only producer of a definition name and
# `lanes.BACKFILL_LANES` is the only registry of lanes it can be handed, so this set is the entire namespace a
# `LaneState.lane` can ever carry.
#
# This is a regression guard, not decoration. This report first shipped naming its lanes `agri.backfill.firms`
# and `agri.backfill.streamflow` -- a parallel namespace matching no row in the ledger. Every stream's lane list
# was therefore EMPTY against production and two guarantees died silently together: `decide_verdict` saw no
# dead-lettered lane, so the 169 dead-lettered FIRMS windows measured on 2026-08-07 were no evidence at all and
# a clean validity sweep reported the stream COMPLETE; and `_resolve_expected_first_day` found no lane floor,
# fell through to `first_observed`, and the 2000->2022 hole the full-archive walk exists to fill measured as
# zero missing days, because a head gap requires `expected_first_day < first_observed`.
ARCHIVE_LANE_DEFINITION_NAMES: Final[frozenset[str]] = frozenset(
    archive_lane_definition_name(lane) for lane in BACKFILL_LANES.values()
)


@dataclass(frozen=True, slots=True)
class StreamDefinition:
    """One reportable stream: where it is stored, how often it should publish, and which lanes fill it."""

    stream: str
    kind: StreamKind
    store: StreamStore
    publication_cadence_days: int | None = None
    cadence_basis: str | None = None
    lane_names: tuple[str, ...] = ()
    expected_first_day: date | None = None

    def __post_init__(self) -> None:
        """Refuse a cadence with no measurement behind it, and a lane name no lane registry can mint."""
        if not self.stream.strip():
            raise ValueError("stream must not be blank")
        if self.publication_cadence_days is not None:
            if self.publication_cadence_days < 1:
                raise ValueError(f"{self.stream}: publication_cadence_days must be at least one day")
            if not (self.cadence_basis or "").strip():
                raise ValueError(f"{self.stream}: a declared cadence must carry the basis it was measured from")
        unmintable = tuple(name for name in self.lane_names if name not in ARCHIVE_LANE_DEFINITION_NAMES)
        if unmintable:
            # A hand-written lane name does not fail loudly on its own: it simply matches no ledger row, and an
            # empty lane list reads exactly like a lane with nothing outstanding. Refusing at construction is
            # the only place the difference is still visible.
            raise ValueError(
                f"{self.stream}: lane name(s) {', '.join(unmintable)} name no registered lane; a lane name must "
                "come from archive_lane_definition_name() over lanes.BACKFILL_LANES, never a literal string"
            )

    @property
    def is_time_series(self) -> bool:
        """True when the stream owes a new observation on a cadence, so silence at the tail is a gap."""
        return self.publication_cadence_days is not None


# Cadence basis strings name a measurement, never an intention. A cron schedule is read from the service's own
# `infra/cron-<name>/railway.json`; an upstream cadence is the publisher's stated release rhythm.
#
# `lane_names` is DERIVED from the archive-walk naming function over a registered lane object, so the catalog
# and the ledger cannot spell the same lane two ways. Only the two lanes `lanes.BACKFILL_LANES` registers have
# a durable ledger behind them today; every other stream is filled by a cron tick that keeps no work-item
# record, so it carries NO lane rather than a plausible-looking name that would match nothing. A stream with no
# lane says so in the report ("No lane in the job ledger claims this stream"), which is the honest reading --
# the previous invented names said the opposite in a way nothing could detect.
DEFAULT_STREAM_DEFINITIONS: Final[tuple[StreamDefinition, ...]] = (
    StreamDefinition(
        stream="fire-detections",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis="infra/cron-firms/railway.json runs `30 */3 * * *`, so a day with no detection row is a gap",
        lane_names=(archive_lane_definition_name(FIRMS_ARCHIVE_LANE),),
    ),
    StreamDefinition(
        stream="water-gauges",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis="infra/cron-streamflow/railway.json runs `*/30 * * * *`",
        lane_names=(archive_lane_definition_name(STREAMFLOW_ARCHIVE_LANE),),
    ),
    StreamDefinition(
        stream="weather-observations",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis="infra/cron-weather/railway.json runs `10 * * * *`",
    ),
    StreamDefinition(
        stream="vegetation",
        kind="time_series",
        store="features",
        publication_cadence_days=5,
        cadence_basis=(
            "infra/cron-ndvi/railway.json runs `0 5 * * *`, but Sentinel-2 L2A revisits mid-latitudes about "
            "every five days, so five days is the shortest cadence the upstream can actually honour"
        ),
    ),
    StreamDefinition(
        stream="fire-perimeters",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis="infra/cron-fire-perimeters/railway.json runs `20 * * * *`",
    ),
    StreamDefinition(
        stream="evacuation-zones",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis="infra/cron-evacuation-zones/railway.json runs `*/15 * * * *`",
    ),
    StreamDefinition(stream="sensors", kind="snapshot", store="features", cadence_basis=None),
    StreamDefinition(stream="soil-survey", kind="reference", store="features"),
    StreamDefinition(stream="watersheds", kind="reference", store="features"),
    StreamDefinition(stream="burn-severity", kind="reference", store="features"),
    StreamDefinition(stream="interventions", kind="reference", store="features"),
    StreamDefinition(
        stream="drought_areas",
        kind="time_series",
        store="drought_areas",
        publication_cadence_days=7,
        cadence_basis="USDM publishes one release every Tuesday, so a weekly rhythm is complete, not sparse",
    ),
    StreamDefinition(stream="historical_vegetation", kind="reference", store="historical_table"),
    StreamDefinition(stream="historical_fire_data", kind="reference", store="historical_table"),
    StreamDefinition(stream="historical_water_drought", kind="reference", store="historical_table"),
)

# `historical_*` rows have no status, no properties, no external id and no geometry link, so those checks
# cannot be answered there and are reported as unevaluated rather than as a reassuring zero.
_FEATURE_ONLY_CHECKS: Final[frozenset[str]] = frozenset(
    {UNLINKED_GEOMETRY_CHECK, MISSING_EXTERNAL_ID_CHECK, MALFORMED_IDENTITY_CHECK, DUPLICATE_IDENTITY_CHECK}
)


# ---------------------------------------------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObservedDay:
    """One observed calendar day and how many rows landed on it."""

    day: date
    observation_count: int


@dataclass(frozen=True, slots=True)
class ObservationGap:
    """A run of consecutive calendar days the stream published nothing on."""

    gap_from: date
    gap_to: date
    days: int

    def to_summary(self) -> dict[str, object]:
        """Render the gap as the explicit (from, to, days) triple the report promises."""
        return {"from": self.gap_from.isoformat(), "to": self.gap_to.isoformat(), "days": self.days}


@dataclass(frozen=True, slots=True)
class ObservationsBelowExpectedFloor:
    """Real observed days that sit before the day the stream declared it owed data from."""

    day_count: int
    observation_count: int
    earliest_day: date
    latest_day: date

    def to_summary(self) -> dict[str, object]:
        """Render the block of days that is neither a gap nor coverage, so a reader can see it was not lost."""
        return {
            "day_count": self.day_count,
            "observation_count": self.observation_count,
            "earliest_day": self.earliest_day.isoformat(),
            "latest_day": self.latest_day.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ThinDay:
    """A day inside the slider window carrying fewer rows than the density floor the axis was anchored on."""

    day: date
    observation_count: int
    density_floor: int

    def to_summary(self) -> dict[str, object]:
        """Render the thin day and the floor it fell under."""
        return {"day": self.day.isoformat(), "rows": self.observation_count, "density_floor": self.density_floor}


@dataclass(frozen=True, slots=True)
class SliderWindow:
    """The window the time slider would actually offer, after continuity clustering and the density floor."""

    earliest_observed_day: date | None
    latest_observed_day: date | None
    observed_day_count: int
    rule: EarliestObservedDayRule
    earliest_recorded_day: date | None
    earliest_continuous_day: date | None
    gap_excluded_day_count: int
    density_excluded_day_count: int
    density_floor: int | None

    @property
    def span_days(self) -> int:
        """Calendar days the user can scrub through, inclusive of both ends."""
        if self.earliest_observed_day is None or self.latest_observed_day is None:
            return 0
        return (self.latest_observed_day - self.earliest_observed_day).days + 1

    def to_summary(self) -> dict[str, object]:
        """Render the window the UI would draw, plus what each rule removed to get there."""
        return {
            "earliest_observed_day": _day_text(self.earliest_observed_day),
            "latest_observed_day": _day_text(self.latest_observed_day),
            "observed_day_count": self.observed_day_count,
            "span_days": self.span_days,
            "rule": self.rule,
            "earliest_recorded_day": _day_text(self.earliest_recorded_day),
            "earliest_continuous_day": _day_text(self.earliest_continuous_day),
            "gap_excluded_day_count": self.gap_excluded_day_count,
            "density_excluded_day_count": self.density_excluded_day_count,
            "density_floor": self.density_floor,
        }


@dataclass(frozen=True, slots=True)
class CompletenessReport:
    """What the stream holds against what it owed, and what the slider will let a user reach."""

    first_observed_day: date | None
    last_observed_day: date | None
    observed_day_count: int
    total_rows: int
    expected_first_day: date | None
    expected_first_day_source: ExpectedFirstDaySource
    expected_day_span: int | None
    reported_through_day: date | None
    missing_day_count: int
    gap_count: int
    worst_gaps: tuple[ObservationGap, ...]
    omitted_gap_count: int
    thin_day_count: int
    thin_days: tuple[ThinDay, ...]
    omitted_thin_day_count: int
    slider_window: SliderWindow
    days_below_expected_floor: ObservationsBelowExpectedFloor | None = None

    @property
    def largest_gap_days(self) -> int:
        """Length of the worst gap, or zero when the stream has none."""
        return max((gap.days for gap in self.worst_gaps), default=0)

    @property
    def observed_day_count_inside_expected_window(self) -> int:
        """Observed days that are actually coverage; a day below the declared floor is not counted as one."""
        below = 0 if self.days_below_expected_floor is None else self.days_below_expected_floor.day_count
        return self.observed_day_count - below

    def to_summary(self) -> dict[str, object]:
        """Render the completeness half of a stream row."""
        return {
            "first_observed_day": _day_text(self.first_observed_day),
            "last_observed_day": _day_text(self.last_observed_day),
            "observed_day_count": self.observed_day_count,
            "observed_day_count_inside_expected_window": self.observed_day_count_inside_expected_window,
            "days_below_expected_floor": (
                None if self.days_below_expected_floor is None else self.days_below_expected_floor.to_summary()
            ),
            "total_rows": self.total_rows,
            "expected_first_day": _day_text(self.expected_first_day),
            "expected_first_day_source": self.expected_first_day_source,
            "expected_day_span": self.expected_day_span,
            "reported_through_day": _day_text(self.reported_through_day),
            "missing_day_count": self.missing_day_count,
            "gap_count": self.gap_count,
            "worst_gaps": [gap.to_summary() for gap in self.worst_gaps],
            "omitted_gap_count": self.omitted_gap_count,
            "thin_day_count": self.thin_day_count,
            "thin_days": [thin.to_summary() for thin in self.thin_days],
            "omitted_thin_day_count": self.omitted_thin_day_count,
            "slider_window": self.slider_window.to_summary(),
        }


@dataclass(frozen=True, slots=True)
class ValidityFinding:
    """One validity check, its count, and the one line saying what a non-zero count breaks downstream."""

    check: str
    count: int
    breaks: str
    evaluated: bool = True
    skipped_reason: str | None = None
    detail: Mapping[str, object] = field(default=NO_DETAIL)

    def __post_init__(self) -> None:
        """An unevaluated check must say why; a silent zero would read as a clean bill of health."""
        if not self.evaluated and not (self.skipped_reason or "").strip():
            raise ValueError(f"{self.check}: an unevaluated check must carry the reason it could not run")
        if self.evaluated and self.skipped_reason is not None:
            raise ValueError(f"{self.check}: an evaluated check must not also carry a skip reason")

    @property
    def is_failing(self) -> bool:
        """True only when the check ran and found something; an unevaluated check never decides a verdict."""
        return self.evaluated and self.count > 0

    def to_summary(self) -> dict[str, object]:
        """Render the finding, omitting the fields that only apply to a check that could not run."""
        summary: dict[str, object] = {"check": self.check, "count": self.count, "breaks": self.breaks}
        if not self.evaluated:
            summary["evaluated"] = False
            summary["skipped_reason"] = self.skipped_reason
        if self.detail:
            summary["detail"] = dict(self.detail)
        return summary


@dataclass(frozen=True, slots=True)
class LaneState:
    """One RUN of one durable lane: what that run planned, what settled, and what is still outstanding."""

    lane: str
    run_key: str
    total_windows: int
    succeeded: int
    retry_wait: int
    dead_letter: int
    queued: int
    other_states: Mapping[str, int]
    oldest_outstanding_window: str | None
    newest_outstanding_window: str | None
    lane_floor_day: date | None

    @property
    def outstanding_windows(self) -> int:
        """Windows the lane still owes: everything that has not succeeded and was not cancelled."""
        settled = self.succeeded + self.other_states.get("cancelled", 0)
        return self.total_windows - settled

    def to_summary(self) -> dict[str, object]:
        """Render one lane-run row."""
        summary: dict[str, object] = {
            "lane": self.lane,
            "run_key": self.run_key,
            "total_windows": self.total_windows,
            "succeeded": self.succeeded,
            "retry_wait": self.retry_wait,
            "dead_letter": self.dead_letter,
            "queued": self.queued,
            "outstanding_windows": self.outstanding_windows,
            "oldest_outstanding_window": self.oldest_outstanding_window,
            "newest_outstanding_window": self.newest_outstanding_window,
        }
        if self.other_states:
            summary["other_states"] = dict(self.other_states)
        if self.lane_floor_day is not None:
            summary["lane_floor_day"] = self.lane_floor_day.isoformat()
        return summary


@dataclass(frozen=True, slots=True)
class StreamReport:
    """One stream's completeness, validity and lane state, and the verdict the three of them decided."""

    stream: str
    kind: StreamKind
    store: StreamStore
    publication_cadence_days: int | None
    verdict: StreamVerdict
    evidence: tuple[str, ...]
    completeness: CompletenessReport
    validity: tuple[ValidityFinding, ...]
    lanes: tuple[LaneState, ...]

    @property
    def failing_validity(self) -> tuple[ValidityFinding, ...]:
        """The checks that ran and found something, in declared order."""
        return tuple(finding for finding in self.validity if finding.is_failing)

    def to_summary(self) -> dict[str, object]:
        """Render one whole stream, machine-readable first."""
        return {
            "stream": self.stream,
            "kind": self.kind,
            "store": self.store,
            "publication_cadence_days": self.publication_cadence_days,
            "verdict": self.verdict,
            "evidence": list(self.evidence),
            "completeness": self.completeness.to_summary(),
            "validity": [finding.to_summary() for finding in self.validity],
            "lanes": [lane.to_summary() for lane in self.lanes],
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The whole cross-stream report: every stream, the bounds it was measured under, and the two renderers."""

    generated_at: datetime
    server_day: date
    bbox: str | None
    streams: tuple[StreamReport, ...]
    unknown_streams: tuple[str, ...]
    unmatched_lanes: tuple[LaneState, ...]
    mirrored_from: str = MIRRORED_READ_MODEL_PATH

    @property
    def verdict_counts(self) -> Mapping[str, int]:
        """How many streams landed on each verdict, for the one-line headline."""
        counts = {"complete": 0, "incomplete": 0, "invalid": 0}
        for stream in self.streams:
            counts[stream.verdict] += 1
        return MappingProxyType(counts)

    def to_summary(self) -> dict[str, object]:
        """Render the whole report as one JSON object; this is the primary, machine-readable output."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "server_day": self.server_day.isoformat(),
            "bbox": self.bbox,
            "verdicts": dict(self.verdict_counts),
            "axis_rules": {
                "mirrored_from": self.mirrored_from,
                "observation_cluster_gap_days": OBSERVATION_CLUSTER_GAP_DAYS,
                "observation_density_floor_fraction": float(OBSERVATION_DENSITY_FLOOR_FRACTION),
            },
            "scan_bounds": {
                "statement_timeout_seconds": STATEMENT_TIMEOUT_SECONDS,
                "max_observed_day_rows": MAX_OBSERVED_DAY_ROWS,
                "max_lane_state_rows": MAX_LANE_STATE_ROWS,
                "max_reported_gaps": MAX_REPORTED_GAPS,
                "max_reported_thin_days": MAX_REPORTED_THIN_DAYS,
                "feature_status": PUBLISHED_FEATURE_STATUS,
            },
            "streams": [stream.to_summary() for stream in self.streams],
            "unknown_streams": list(self.unknown_streams),
            "unmatched_lanes": [lane.to_summary() for lane in self.unmatched_lanes],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Render the report as a JSON document; `indent=None` gives the one-line cron-log form."""
        return json.dumps(self.to_summary(), sort_keys=True, indent=indent)

    def to_markdown(self) -> str:
        """Render the same object as a compact Markdown document suitable for docs/reports/."""
        return _render_markdown(self)


# ---------------------------------------------------------------------------------------------------------------
# Pure logic. Everything below this line is testable with no database at all, which is where the report's
# credibility lives: the SQL only counts rows, the rules that turn counts into a verdict are here.
# ---------------------------------------------------------------------------------------------------------------


def _day_text(day: date | None) -> str | None:
    """Render an optional calendar day as an ISO string."""
    return None if day is None else day.isoformat()


def sort_observed_days(days: Iterable[ObservedDay]) -> tuple[ObservedDay, ...]:
    """Return the day series in ascending calendar order, which every rule below assumes."""
    return tuple(sorted(days, key=lambda observed: observed.day))


def split_days_at_expected_floor(
    days: Sequence[ObservedDay],
    expected_first_day: date | None,
) -> tuple[tuple[ObservedDay, ...], tuple[ObservedDay, ...]]:
    """Split the ordered day series into the days below the declared floor and the days inside the window."""
    # A DAY BELOW THE FLOOR IS NEITHER A GAP NOR COVERAGE, and that is why it gets its own return value rather
    # than being dropped or merged. It is not coverage: the stream never claimed to serve it, so counting it
    # would credit the stream for a day outside its declared window. It is not a gap either, and it does not
    # OPEN one: nobody owes data for the 30 years between two stragglers that sit below the floor.
    #
    # Measured on water-gauges 2026-08-08. 46 rows from decommissioned USGS gauges carry their last-ever
    # reading's own timestamp, 38 distinct days spanning 1990-09-30 to 2020-12-03 at 1-4 rows each, against 448
    # real days carrying 800+ rows each. Walking `pairwise` over the WHOLE series counted every inter-straggler
    # hole, and the head-gap branch could not compensate because it only fires when
    # `expected_first_day < first_observed` -- with floor 2022-08-05 against first_observed 1990-09-30 it is
    # skipped, so 30 years of holes stayed in the total. The report read 12,611 missing days while the stream's
    # own declared span is 1,465 days; clipped here it reads 1,017 of 1,465, and `missing_day_count` can no
    # longer exceed `expected_day_span`.
    #
    # The stragglers are still REAL OBSERVATIONS, so they are handed back for `days_below_expected_floor` to
    # report rather than discarded. The slider window is deliberately built from the UNCLIPPED series: the
    # 21-day continuity rule plus the density floor already drop them, and that half agrees with what the UI
    # draws.
    ordered = sort_observed_days(days)
    if expected_first_day is None:
        return (), ordered
    below = tuple(entry for entry in ordered if entry.day < expected_first_day)
    inside = tuple(entry for entry in ordered if entry.day >= expected_first_day)
    return below, inside


def find_observation_gaps(
    days: Sequence[ObservedDay],
    *,
    expected_first_day: date | None = None,
    through_day: date | None = None,
) -> tuple[ObservationGap, ...]:
    """List every run of days the stream owed and published nothing on, head and tail runs included."""
    # Clipped here rather than by the caller so no caller can walk the unclipped series by omission.
    _, ordered = split_days_at_expected_floor(days, expected_first_day)
    if not ordered:
        if expected_first_day is None or through_day is None or through_day < expected_first_day:
            return ()
        return (ObservationGap(expected_first_day, through_day, (through_day - expected_first_day).days + 1),)

    gaps: list[ObservationGap] = []

    first_observed = ordered[0].day
    if expected_first_day is not None and expected_first_day < first_observed:
        gaps.append(_gap_between(expected_first_day, first_observed - timedelta(days=1)))

    for earlier, later in pairwise(ordered):
        if (later.day - earlier.day).days > 1:
            gaps.append(_gap_between(earlier.day + timedelta(days=1), later.day - timedelta(days=1)))

    last_observed = ordered[-1].day
    if through_day is not None and through_day > last_observed:
        gaps.append(_gap_between(last_observed + timedelta(days=1), through_day))

    return tuple(gaps)


def summarise_days_below_expected_floor(days: Sequence[ObservedDay]) -> ObservationsBelowExpectedFloor | None:
    """Summarise the days clipped off below the declared floor, or None when every day is inside the window."""
    if not days:
        return None
    ordered = sort_observed_days(days)
    return ObservationsBelowExpectedFloor(
        day_count=len(ordered),
        observation_count=sum(entry.observation_count for entry in ordered),
        earliest_day=ordered[0].day,
        latest_day=ordered[-1].day,
    )


def _gap_between(gap_from: date, gap_to: date) -> ObservationGap:
    """Build the inclusive gap between two missing days."""
    return ObservationGap(gap_from, gap_to, (gap_to - gap_from).days + 1)


def rank_gaps(gaps: Sequence[ObservationGap], limit: int) -> tuple[tuple[ObservationGap, ...], int]:
    """Return the worst `limit` gaps, longest first then earliest, and how many were left out."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    ranked = sorted(gaps, key=lambda gap: (-gap.days, gap.gap_from))
    return tuple(ranked[:limit]), max(0, len(ranked) - limit)


def newest_observation_cluster(
    days: Sequence[ObservedDay],
    *,
    cluster_gap_days: int = OBSERVATION_CLUSTER_GAP_DAYS,
) -> tuple[ObservedDay, ...]:
    """Keep the most recent run of days, where any gap wider than the mirrored threshold ends the run."""
    if not days:
        return ()
    ordered = sort_observed_days(days)
    start_index = 0
    for index in range(1, len(ordered)):
        if (ordered[index].day - ordered[index - 1].day).days > cluster_gap_days:
            start_index = index
    return ordered[start_index:]


def density_floor_for(cluster: Sequence[ObservedDay]) -> int:
    """The observations a day needs to anchor the axis: 1% of the busiest day in the cluster, never below one."""
    if not cluster:
        return MINIMUM_DENSITY_FLOOR
    busiest = max(observed.observation_count for observed in cluster)
    # Exact numeric arithmetic, matching `CEIL(MAX(observation_count) * 0.01::numeric)`; see the constant's note.
    scaled = Decimal(busiest) * OBSERVATION_DENSITY_FLOOR_FRACTION
    return max(MINIMUM_DENSITY_FLOOR, int(scaled.to_integral_value(rounding=ROUND_CEILING)))


def apply_density_floor(cluster: Sequence[ObservedDay], floor: int) -> tuple[ObservedDay, ...]:
    """Move the start of the axis to the first day that meets the floor; days inside stay, however thin."""
    ordered = sort_observed_days(cluster)
    for index, observed in enumerate(ordered):
        if observed.observation_count >= floor:
            return ordered[index:]
    return ()


def build_slider_window(
    days: Sequence[ObservedDay],
    *,
    cluster_gap_days: int = OBSERVATION_CLUSTER_GAP_DAYS,
) -> SliderWindow:
    """Run the day series through both axis rules and report the window the slider would offer."""
    ordered = sort_observed_days(days)
    if not ordered:
        return SliderWindow(None, None, 0, "no_observations", None, None, 0, 0, None)

    recorded_earliest = ordered[0].day
    cluster = newest_observation_cluster(ordered, cluster_gap_days=cluster_gap_days)
    continuous_earliest = cluster[0].day
    floor = density_floor_for(cluster)
    dense = apply_density_floor(cluster, floor)
    dense_earliest = dense[0].day if dense else None

    if dense_earliest is None:
        rule: EarliestObservedDayRule = "no_observations"
    elif dense_earliest != continuous_earliest:
        rule = "density_floored"
    elif continuous_earliest != recorded_earliest:
        rule = "gap_clustered"
    else:
        rule = "full_history"

    return SliderWindow(
        earliest_observed_day=dense_earliest,
        latest_observed_day=ordered[-1].day if dense else None,
        observed_day_count=len(dense),
        rule=rule,
        earliest_recorded_day=recorded_earliest,
        earliest_continuous_day=continuous_earliest,
        gap_excluded_day_count=max(0, len(ordered) - len(cluster)),
        density_excluded_day_count=max(0, len(cluster) - len(dense)),
        density_floor=floor,
    )


def find_thin_days(days: Sequence[ObservedDay], window: SliderWindow, limit: int) -> tuple[tuple[ThinDay, ...], int]:
    """List the days inside the slider window that fall under its density floor, thinnest first."""
    if window.earliest_observed_day is None or window.density_floor is None:
        return (), 0
    floor = window.density_floor
    thin = [
        ThinDay(observed.day, observed.observation_count, floor)
        for observed in sort_observed_days(days)
        if observed.day >= window.earliest_observed_day and observed.observation_count < floor
    ]
    thin.sort(key=lambda entry: (entry.observation_count, entry.day))
    return tuple(thin[:limit]), max(0, len(thin) - limit)


def decide_verdict(
    *,
    total_rows: int,
    validity: Sequence[ValidityFinding],
    lanes: Sequence[LaneState],
    largest_gap_days: int,
    publication_cadence_days: int | None,
) -> tuple[StreamVerdict, tuple[str, ...]]:
    """Decide the stream's verdict and name the evidence that decided it; `invalid` outranks `incomplete`."""
    failing = [finding for finding in validity if finding.is_failing]
    dead_lettered = [lane for lane in lanes if lane.dead_letter > 0]

    evidence: list[str] = [f"{finding.check}: {finding.count:,} row(s) -- {finding.breaks}" for finding in failing]
    evidence.extend(
        f"lane {lane.lane} run {lane.run_key} has {lane.dead_letter:,} dead-lettered window(s); "
        "a dead letter is never success"
        for lane in dead_lettered
    )
    if total_rows == 0:
        evidence.append("the stream holds no published rows at all, so nothing it claims to serve is served")
    if publication_cadence_days is not None and largest_gap_days > publication_cadence_days:
        evidence.append(
            f"largest gap is {largest_gap_days:,} day(s) against a {publication_cadence_days}-day publication cadence"
        )

    if failing:
        return "invalid", tuple(evidence)
    if evidence:
        return "incomplete", tuple(evidence)
    return "complete", ("no dead-lettered window, no gap beyond the cadence, and every validity check at zero",)


# ---------------------------------------------------------------------------------------------------------------
# Raw observations and the pure stream builder
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamObservations:
    """Everything one SQL pass learned about a stream, before any rule was applied to it."""

    total_rows: int = 0
    day_counts: tuple[ObservedDay, ...] = ()
    check_counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    unsupported_checks: frozenset[str] = frozenset()
    unsupported_reason: str | None = None
    duplicate_identity_groups: int = 0


def build_validity_findings(
    definition: StreamDefinition,
    observations: StreamObservations,
    *,
    bbox: str | None,
) -> tuple[ValidityFinding, ...]:
    """Turn the raw per-check counts into findings, marking a check that could not run as unevaluated."""
    findings: list[ValidityFinding] = []
    for check in VALIDITY_CHECK_ORDER:
        breaks = VALIDITY_CHECK_CONSEQUENCES[check]
        skipped_reason = _skip_reason(definition, observations, check, bbox=bbox)
        skipped_detail: Mapping[str, object] = NO_DETAIL
        if skipped_reason is not None:
            if check == UNDATED_DAY_CHECK and definition.kind == "reference":
                # The count is carried machine-readably as well as in the prose, because this is the one skip
                # that skips a check which DID run: the number is real, it is just not a defect.
                skipped_detail = MappingProxyType(
                    {
                        "undated_row_count": observations.check_counts.get(UNDATED_DAY_CHECK, 0),
                        "total_rows": observations.total_rows,
                    }
                )
            findings.append(
                ValidityFinding(check, 0, breaks, evaluated=False, skipped_reason=skipped_reason, detail=skipped_detail)
            )
            continue
        detail: Mapping[str, object] = NO_DETAIL
        if check == DUPLICATE_IDENTITY_CHECK and observations.duplicate_identity_groups:
            detail = MappingProxyType({"duplicate_identity_groups": observations.duplicate_identity_groups})
        findings.append(ValidityFinding(check, observations.check_counts.get(check, 0), breaks, detail=detail))
    return tuple(findings)


def _skip_reason(
    definition: StreamDefinition,
    observations: StreamObservations,
    check: str,
    *,
    bbox: str | None,
) -> str | None:
    """Name the reason a check cannot run against this stream, or None when it can."""
    if check in observations.unsupported_checks:
        return observations.unsupported_reason or f"{definition.store} cannot answer this check"
    if check == UNDATED_DAY_CHECK and definition.kind == "reference":
        return _reference_undated_day_note(observations)
    if check == OUTSIDE_BBOX_CHECK and bbox is None:
        return "INGEST_BBOX is not configured, so there is no boundary to measure rows against"
    if check == MISSING_VALUE_SENTINEL_CHECK and definition.stream not in MISSING_VALUE_SENTINEL_PROPERTY_BY_STREAM:
        return "this producer emits no numeric missing-value marker, so there is no sentinel to find"
    if check == MALFORMED_IDENTITY_CHECK and definition.stream not in PRODUCER_LOCAL_ID_CEILING_BY_STREAM:
        return "identity.PRODUCER_BY_LAYER_NAME names no producer for this stream, so no key ceiling applies"
    return None


def _reference_undated_day_note(observations: StreamObservations) -> str:
    """State the undated-row count of a reference stream as a fact about the layer rather than as a defect."""
    # KEYED ON `kind`, NEVER ON WHETHER THE ROWS HAPPEN TO BE UNDATED. A reference layer describes places, not
    # moments, so carrying no observation date is how it is modelled -- the same statement holds for watersheds,
    # which is reference-kind and DOES carry dates. A rule of the form "every row is undated, so exempt it"
    # would flip a stream's verdict the day its data changed shape, which is precisely the silent behaviour
    # this report exists to remove.
    #
    # Measured on soil-survey 2026-08-08: 218,653 SSURGO map units carry mukey/muname/hydric and no date field
    # at all, so `geo.feature_observation_day` returns NULL for every one of them and the report called a
    # correctly-modelled reference layer INVALID. Exempted, NOT hidden: the count and its downstream
    # consequence stay on the page, because "every row shows at every slider date" is a real thing a reader
    # needs to know about this layer even though nothing is broken.
    undated = observations.check_counts.get(UNDATED_DAY_CHECK, 0)
    return (
        f"a reference layer describes places rather than moments, so an undated row is how it is modelled and "
        f"not a defect: {undated:,} of {observations.total_rows:,} published row(s) carry no observation date, "
        "and every one of them shows at EVERY date on the slider rather than on its own day"
    )


def build_stream_report(  # noqa: PLR0913 - one parameter per input the pure builder must not go and fetch
    definition: StreamDefinition,
    observations: StreamObservations,
    lanes: Sequence[LaneState],
    *,
    bbox: str | None,
    server_day: date,
    max_reported_gaps: int = MAX_REPORTED_GAPS,
    max_reported_thin_days: int = MAX_REPORTED_THIN_DAYS,
) -> StreamReport:
    """Apply every rule to one stream's raw observations and return its finished report row."""
    days = sort_observed_days(observations.day_counts)
    first_observed = days[0].day if days else None
    last_observed = days[-1].day if days else None

    expected_first_day, expected_source = _resolve_expected_first_day(definition, lanes, first_observed)
    through_day = server_day if definition.is_time_series else last_observed

    below_floor, _ = split_days_at_expected_floor(days, expected_first_day)
    gaps = find_observation_gaps(days, expected_first_day=expected_first_day, through_day=through_day)
    worst_gaps, omitted_gaps = rank_gaps(gaps, max_reported_gaps)

    # The UNCLIPPED series on purpose: the slider window must keep answering "what will the user be able to
    # scrub through", and the axis rules it mirrors see every day the warehouse holds.
    window = build_slider_window(days)
    thin_days, omitted_thin = find_thin_days(days, window, max_reported_thin_days)
    thin_day_count = len(thin_days) + omitted_thin

    completeness = CompletenessReport(
        first_observed_day=first_observed,
        last_observed_day=last_observed,
        observed_day_count=len(days),
        total_rows=observations.total_rows,
        expected_first_day=expected_first_day,
        expected_first_day_source=expected_source,
        expected_day_span=_expected_day_span(expected_first_day, through_day),
        reported_through_day=through_day,
        missing_day_count=sum(gap.days for gap in gaps),
        gap_count=len(gaps),
        worst_gaps=worst_gaps,
        omitted_gap_count=omitted_gaps,
        thin_day_count=thin_day_count,
        thin_days=thin_days,
        omitted_thin_day_count=omitted_thin,
        slider_window=window,
        days_below_expected_floor=summarise_days_below_expected_floor(below_floor),
    )

    validity = build_validity_findings(definition, observations, bbox=bbox)
    verdict, evidence = decide_verdict(
        total_rows=observations.total_rows,
        validity=validity,
        lanes=lanes,
        largest_gap_days=max((gap.days for gap in gaps), default=0),
        publication_cadence_days=definition.publication_cadence_days,
    )
    return StreamReport(
        stream=definition.stream,
        kind=definition.kind,
        store=definition.store,
        publication_cadence_days=definition.publication_cadence_days,
        verdict=verdict,
        evidence=evidence,
        completeness=completeness,
        validity=validity,
        lanes=tuple(lanes),
    )


def _resolve_expected_first_day(
    definition: StreamDefinition,
    lanes: Sequence[LaneState],
    first_observed: date | None,
) -> tuple[date | None, ExpectedFirstDaySource]:
    """Pick the day the stream owed data from: a declared floor, else the lane's own floor, else what it holds."""
    if definition.expected_first_day is not None:
        return definition.expected_first_day, "declared"
    lane_floors = [lane.lane_floor_day for lane in lanes if lane.lane_floor_day is not None]
    if lane_floors:
        return min(lane_floors), "lane_floor"
    if first_observed is not None:
        return first_observed, "first_observed"
    return None, "none"


def _expected_day_span(expected_first_day: date | None, through_day: date | None) -> int | None:
    """Calendar days the stream owed, inclusive of both ends; None when neither end is known."""
    if expected_first_day is None or through_day is None or through_day < expected_first_day:
        return None
    return (through_day - expected_first_day).days + 1


# ---------------------------------------------------------------------------------------------------------------
# SQL. Every statement is schema-qualified (`db/base.py` sets no search_path, so an unqualified name resolves
# to nothing), parameterised, and read-only. Each opens with a `-- <name>` marker the unit tests match on.
#
# Deliberate deviation from sql.md's "runtime SQL lives in sql/<package>/*.sql": that directory and its loader
# `db/sql_queries.load_query_sql` do not exist yet, and `db/` is outside this module's file boundary. Extraction
# is mechanical follow-up; the same deviation is recorded in jobs/AGENTS.md.
# ---------------------------------------------------------------------------------------------------------------

_SET_READ_ONLY_SNAPSHOT: Final[TextClause] = text("-- set_read_only_snapshot\nSET LOCAL transaction_read_only = on")

_SET_STATEMENT_TIMEOUT: Final[TextClause] = text(
    f"-- set_statement_timeout\nSET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_SECONDS}s'"
)

_SERVER_DAY: Final[TextClause] = text(
    """
-- server_day
SELECT (now() AT TIME ZONE 'UTC')::date AS server_day
"""
)

# Mirrors readObservationWindows' `observed` CTE filter for filter: published rows, linked geometry, and a
# publisher-named day that geo.feature_observation_day could actually parse. Anything looser reports days the
# slider will not offer.
_FEATURE_OBSERVED_DAYS: Final[TextClause] = text(
    """
-- feature_observed_days
SELECT layers.name                                            AS stream,
       geo.feature_observation_day(features.properties)       AS observed_day,
       count(*)                                               AS observation_count
  FROM geo.layers AS layers
  JOIN geo.features AS features
    ON features.layer_id = layers.id
 WHERE features.status = :published_status
   AND features.geometry_id IS NOT NULL
   AND geo.feature_observation_day(features.properties) IS NOT NULL
 GROUP BY layers.name, geo.feature_observation_day(features.properties)
 ORDER BY layers.name, geo.feature_observation_day(features.properties)
 LIMIT :row_limit
"""
)

# One grouped pass over geo.features answers every per-row check, so production is scanned once rather than
# eight times. The bbox ordinates arrive as NULL when INGEST_BBOX is unset; ST_MakeEnvelope is STRICT, so the
# predicate evaluates to NULL and the FILTER counts nothing instead of raising -- and the Python side still
# reports that check as unevaluated rather than as a clean zero.
_FEATURE_VALIDITY_COUNTS: Final[TextClause] = text(
    """
-- feature_validity_counts
SELECT layers.name AS stream,
       count(*)    AS total_rows,
       count(*) FILTER (WHERE features.geom IS NULL)          AS null_geom,
       count(*) FILTER (WHERE features.geometry_id IS NULL)   AS unlinked_geometry,
       count(*) FILTER (
           WHERE nullif(btrim(coalesce(features.properties ->> 'id', '')), '') IS NULL
       ) AS missing_external_id,
       count(*) FILTER (
           WHERE (:producer_local_id_ceiling_by_stream)::jsonb ? layers.name
             AND nullif(btrim(coalesce(features.properties ->> 'id', '')), '') IS NOT NULL
             AND length(features.properties ->> 'id')
                 > (((:producer_local_id_ceiling_by_stream)::jsonb ->> layers.name))::integer
       ) AS malformed_identity,
       count(*) FILTER (
           WHERE geo.feature_observation_day(features.properties) IS NULL
       ) AS undated_day,
       count(*) FILTER (
           WHERE geo.feature_observation_day(features.properties) > :server_day
       ) AS future_day,
       count(*) FILTER (
           WHERE features.geom IS NOT NULL
             AND NOT ST_Intersects(
                     features.geom,
                     ST_MakeEnvelope(
                         (:bbox_west)::double precision,
                         (:bbox_south)::double precision,
                         (:bbox_east)::double precision,
                         (:bbox_north)::double precision,
                         4326
                     )
                 )
       ) AS outside_bbox,
       count(*) FILTER (
           WHERE (:sentinel_property_by_stream)::jsonb ? layers.name
             AND jsonb_typeof(
                     features.properties -> ((:sentinel_property_by_stream)::jsonb ->> layers.name)
                 ) = 'number'
             AND (features.properties ->> ((:sentinel_property_by_stream)::jsonb ->> layers.name))
                     ::double precision = :missing_value_sentinel
       ) AS missing_value_sentinel
  FROM geo.layers AS layers
  JOIN geo.features AS features
    ON features.layer_id = layers.id
 WHERE features.status = :published_status
 GROUP BY layers.name
 ORDER BY layers.name
"""
)

# `features_layer_external_id_unique` makes a genuine duplicate impossible for any row that carries an id, so
# this should return nothing. It is run anyway: the index is partial, and the check is what proves the index
# is still there rather than assuming it.
_FEATURE_DUPLICATE_IDENTITIES: Final[TextClause] = text(
    """
-- feature_duplicate_identities
WITH duplicated AS (
    SELECT features.layer_id            AS layer_id,
           features.properties ->> 'id' AS producer_local_id,
           count(*)                     AS occurrence_count
      FROM geo.features AS features
     WHERE features.status = :published_status
       AND features.properties ? 'id'
     GROUP BY features.layer_id, features.properties ->> 'id'
    HAVING count(*) > 1
)
SELECT layers.name                                     AS stream,
       count(*)                                        AS duplicate_identity_groups,
       sum(duplicated.occurrence_count - 1)::bigint    AS duplicate_identity
  FROM duplicated
  JOIN geo.layers AS layers
    ON layers.id = duplicated.layer_id
 GROUP BY layers.name
 ORDER BY layers.name
"""
)

# geo.drought_areas.valid_date is a VARCHAR, so the same two guards geo.feature_observation_day applies are
# applied here by hand: the regex proves the shape and pg_input_is_valid proves the day exists, because
# to_date('2026-02-31', ...) raises rather than returning NULL.
_DROUGHT_AREA_OBSERVED_DAYS: Final[TextClause] = text(
    """
-- drought_area_observed_days
SELECT to_date(drought_areas.valid_date, 'YYYY-MM-DD') AS observed_day,
       count(*)                                        AS observation_count
  FROM geo.drought_areas AS drought_areas
 WHERE drought_areas.valid_date ~ '^\\d{4}-\\d{2}-\\d{2}$'
   AND pg_input_is_valid(drought_areas.valid_date, 'date')
 GROUP BY to_date(drought_areas.valid_date, 'YYYY-MM-DD')
 ORDER BY to_date(drought_areas.valid_date, 'YYYY-MM-DD')
 LIMIT :row_limit
"""
)

_DROUGHT_AREA_VALIDITY_COUNTS: Final[TextClause] = text(
    """
-- drought_area_validity_counts
SELECT count(*) AS total_rows,
       count(*) FILTER (WHERE drought_areas.geom IS NULL) AS null_geom,
       count(*) FILTER (
           WHERE drought_areas.valid_date IS NULL
              OR drought_areas.valid_date !~ '^\\d{4}-\\d{2}-\\d{2}$'
              OR NOT pg_input_is_valid(drought_areas.valid_date, 'date')
       ) AS undated_day,
       count(*) FILTER (
           WHERE drought_areas.valid_date ~ '^\\d{4}-\\d{2}-\\d{2}$'
             AND pg_input_is_valid(drought_areas.valid_date, 'date')
             AND to_date(drought_areas.valid_date, 'YYYY-MM-DD') > :server_day
       ) AS future_day,
       count(*) FILTER (
           WHERE drought_areas.geom IS NOT NULL
             AND NOT ST_Intersects(
                     drought_areas.geom,
                     ST_MakeEnvelope(
                         (:bbox_west)::double precision,
                         (:bbox_south)::double precision,
                         (:bbox_east)::double precision,
                         (:bbox_north)::double precision,
                         4326
                     )
                 )
       ) AS outside_bbox
  FROM geo.drought_areas AS drought_areas
"""
)

# `date_bucket` is timestamptz, so `::date` alone would move with the session TimeZone. Pinning UTC keeps the
# day this report names identical between a local run and a Railway one.
_HISTORICAL_OBSERVED_DAYS: Final[TextClause] = text(
    """
-- historical_observed_days
  SELECT 'historical_vegetation' AS stream,
         (historical.date_bucket AT TIME ZONE 'UTC')::date AS observed_day,
         count(*) AS observation_count
    FROM geo.historical_vegetation AS historical
   WHERE historical.date_bucket IS NOT NULL
   GROUP BY (historical.date_bucket AT TIME ZONE 'UTC')::date
UNION ALL
  SELECT 'historical_fire_data' AS stream,
         (historical.date_bucket AT TIME ZONE 'UTC')::date AS observed_day,
         count(*) AS observation_count
    FROM geo.historical_fire_data AS historical
   WHERE historical.date_bucket IS NOT NULL
   GROUP BY (historical.date_bucket AT TIME ZONE 'UTC')::date
UNION ALL
  SELECT 'historical_water_drought' AS stream,
         (historical.date_bucket AT TIME ZONE 'UTC')::date AS observed_day,
         count(*) AS observation_count
    FROM geo.historical_water_drought AS historical
   WHERE historical.date_bucket IS NOT NULL
   GROUP BY (historical.date_bucket AT TIME ZONE 'UTC')::date
   ORDER BY 1, 2
   LIMIT :row_limit
"""
)

_HISTORICAL_VALIDITY_COUNTS: Final[TextClause] = text(
    """
-- historical_validity_counts
  SELECT 'historical_vegetation' AS stream,
         count(*) AS total_rows,
         count(*) FILTER (WHERE historical.geom IS NULL) AS null_geom,
         count(*) FILTER (WHERE historical.date_bucket IS NULL) AS undated_day,
         count(*) FILTER (
             WHERE (historical.date_bucket AT TIME ZONE 'UTC')::date > :server_day
         ) AS future_day,
         count(*) FILTER (
             WHERE historical.geom IS NOT NULL
               AND NOT ST_Intersects(
                       historical.geom,
                       ST_MakeEnvelope(
                           (:bbox_west)::double precision,
                           (:bbox_south)::double precision,
                           (:bbox_east)::double precision,
                           (:bbox_north)::double precision,
                           4326
                       )
                   )
         ) AS outside_bbox
    FROM geo.historical_vegetation AS historical
UNION ALL
  SELECT 'historical_fire_data' AS stream,
         count(*) AS total_rows,
         count(*) FILTER (WHERE historical.geom IS NULL) AS null_geom,
         count(*) FILTER (WHERE historical.date_bucket IS NULL) AS undated_day,
         count(*) FILTER (
             WHERE (historical.date_bucket AT TIME ZONE 'UTC')::date > :server_day
         ) AS future_day,
         count(*) FILTER (
             WHERE historical.geom IS NOT NULL
               AND NOT ST_Intersects(
                       historical.geom,
                       ST_MakeEnvelope(
                           (:bbox_west)::double precision,
                           (:bbox_south)::double precision,
                           (:bbox_east)::double precision,
                           (:bbox_north)::double precision,
                           4326
                       )
                   )
         ) AS outside_bbox
    FROM geo.historical_fire_data AS historical
UNION ALL
  SELECT 'historical_water_drought' AS stream,
         count(*) AS total_rows,
         count(*) FILTER (WHERE historical.geom IS NULL) AS null_geom,
         count(*) FILTER (WHERE historical.date_bucket IS NULL) AS undated_day,
         count(*) FILTER (
             WHERE (historical.date_bucket AT TIME ZONE 'UTC')::date > :server_day
         ) AS future_day,
         count(*) FILTER (
             WHERE historical.geom IS NOT NULL
               AND NOT ST_Intersects(
                       historical.geom,
                       ST_MakeEnvelope(
                           (:bbox_west)::double precision,
                           (:bbox_south)::double precision,
                           (:bbox_east)::double precision,
                           (:bbox_north)::double precision,
                           4326
                       )
                   )
         ) AS outside_bbox
    FROM geo.historical_water_drought AS historical
   ORDER BY 1
"""
)

# shard_key is the (job_run_id, shard_key) idempotency key and embeds the window it covers, so min/max over it
# is the oldest and newest window a run still owes. See jobs/AGENTS.md.
#
# Grouped PER RUN, not per definition. `archive_lane_run_key` puts the lane's FLOOR in the `logical_run_key`
# precisely so that lowering the floor mints a second run rather than reopening a finished one, and a run's
# grid is anchored at its own floor -- so two runs of one lane hold two overlapping window sets with different
# shard keys. Folding them together would count the same calendar day twice in `total_windows` and report a
# lane as owing roughly double what it owes. The alternative, keeping only the newest run, was rejected: it
# discards the superseded run's dead letters, and losing a dead letter is the exact failure class this whole
# ledger exists to make impossible. Per run, every dead letter stays visible and every total is its own run's.
_JOB_LANE_STATE: Final[TextClause] = text(
    """
-- job_lane_state
SELECT definitions.name       AS lane,
       runs.logical_run_key   AS run_key,
       work_items.status      AS work_item_status,
       count(*)               AS window_count,
       min(work_items.shard_key) AS oldest_shard_key,
       max(work_items.shard_key) AS newest_shard_key
  FROM agri.job_definition AS definitions
  JOIN agri.job_run AS runs
    ON runs.job_definition_id = definitions.id
  JOIN agri.job_work_item AS work_items
    ON work_items.job_run_id = runs.id
 GROUP BY definitions.name, runs.logical_run_key, work_items.status
 ORDER BY definitions.name, runs.logical_run_key, work_items.status
 LIMIT :row_limit
"""
)


# ---------------------------------------------------------------------------------------------------------------
# Result-row readers. A column that comes back in a shape the SQL above does not declare is a typed refusal,
# never a coerced guess.
# ---------------------------------------------------------------------------------------------------------------


async def _fetch_rows(
    session: AsyncSession,
    statement: TextClause,
    parameters: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    """Execute one read-only statement and return its rows as plain mappings."""
    result = await session.execute(statement, dict(parameters))
    return tuple({str(column): value for column, value in row.items()} for row in result.mappings().all())


def _required_text(row: Mapping[str, object], column: str) -> str:
    """Read a NOT NULL text column, refusing anything that is not a string."""
    value = row.get(column)
    if not isinstance(value, str):
        raise ValidationRowError(f"column {column!r} must be text, got {type(value).__name__}")
    return value


def _optional_text(row: Mapping[str, object], column: str) -> str | None:
    """Read a nullable text column, refusing a non-string value."""
    value = row.get(column)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationRowError(f"column {column!r} must be text or NULL, got {type(value).__name__}")
    return value


def _required_count(row: Mapping[str, object], column: str) -> int:
    """Read a count column, refusing a non-integer and treating SQL NULL as zero (an empty FILTER sums to NULL)."""
    value = row.get(column)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationRowError(f"column {column!r} must be an integer count, got {type(value).__name__}")
    return value


def _required_day(row: Mapping[str, object], column: str) -> date:
    """Read a NOT NULL date column, refusing a datetime so a day can never silently carry a time."""
    value = row.get(column)
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValidationRowError(f"column {column!r} must be a date, got {type(value).__name__}")
    return value


# ---------------------------------------------------------------------------------------------------------------
# The async entry point
# ---------------------------------------------------------------------------------------------------------------


async def build_validation_report(  # noqa: PLR0913 - one parameter per knob the CLI verb exposes
    session: AsyncSession,
    *,
    definitions: Sequence[StreamDefinition] = DEFAULT_STREAM_DEFINITIONS,
    bbox: str | None = None,
    now: datetime | None = None,
    max_reported_gaps: int = MAX_REPORTED_GAPS,
    max_reported_thin_days: int = MAX_REPORTED_THIN_DAYS,
) -> ValidationReport:
    """Read the warehouse and the job ledger read-only and return the whole cross-stream report object."""
    resolved_bbox = bbox if bbox is not None else resolve_bounded_bbox()
    await session.execute(_SET_READ_ONLY_SNAPSHOT)
    await session.execute(_SET_STATEMENT_TIMEOUT)

    server_day = _required_day((await _fetch_rows(session, _SERVER_DAY, {}))[0], "server_day")
    bbox_parameters = _bbox_parameters(resolved_bbox)

    day_series = await _read_day_series(session)
    observations = await _read_observations(session, day_series, server_day=server_day, bbox=bbox_parameters)
    lanes = await _read_lane_states(session)

    streams: list[StreamReport] = []
    claimed_lanes: set[str] = set()
    for definition in definitions:
        stream_lanes = tuple(lane for lane in lanes if lane.lane in definition.lane_names)
        claimed_lanes.update(lane.lane for lane in stream_lanes)
        streams.append(
            build_stream_report(
                definition,
                observations.get(definition.stream, StreamObservations()),
                stream_lanes,
                bbox=resolved_bbox,
                server_day=server_day,
                max_reported_gaps=max_reported_gaps,
                max_reported_thin_days=max_reported_thin_days,
            )
        )

    declared = {definition.stream for definition in definitions}
    return ValidationReport(
        generated_at=now if now is not None else datetime.now(UTC),
        server_day=server_day,
        bbox=resolved_bbox,
        streams=tuple(streams),
        unknown_streams=tuple(sorted(stream for stream in observations if stream not in declared)),
        unmatched_lanes=tuple(lane for lane in lanes if lane.lane not in claimed_lanes),
    )


def _bbox_parameters(bbox: str | None) -> Mapping[str, object]:
    """Bind the four bbox ordinates, or four NULLs so the STRICT envelope makes the predicate a no-op."""
    if bbox is None:
        return MappingProxyType({"bbox_west": None, "bbox_south": None, "bbox_east": None, "bbox_north": None})
    west, south, east, north = parse_bbox(bbox)
    return MappingProxyType({"bbox_west": west, "bbox_south": south, "bbox_east": east, "bbox_north": north})


async def _read_day_series(session: AsyncSession) -> Mapping[str, tuple[ObservedDay, ...]]:
    """Read every stream's observed-day series, refusing a result that hit the row cap rather than truncating."""
    series: dict[str, list[ObservedDay]] = {}
    for statement, label in (
        (_FEATURE_OBSERVED_DAYS, "feature_observed_days"),
        (_HISTORICAL_OBSERVED_DAYS, "historical_observed_days"),
    ):
        rows = await _fetch_rows(
            session,
            statement,
            {"published_status": PUBLISHED_FEATURE_STATUS, "row_limit": MAX_OBSERVED_DAY_ROWS + 1},
        )
        _refuse_truncated_scan(rows, label)
        for row in rows:
            stream = _required_text(row, "stream")
            entry = ObservedDay(_required_day(row, "observed_day"), _required_count(row, "observation_count"))
            series.setdefault(stream, []).append(entry)

    drought_rows = await _fetch_rows(session, _DROUGHT_AREA_OBSERVED_DAYS, {"row_limit": MAX_OBSERVED_DAY_ROWS + 1})
    _refuse_truncated_scan(drought_rows, "drought_area_observed_days")
    for row in drought_rows:
        entry = ObservedDay(_required_day(row, "observed_day"), _required_count(row, "observation_count"))
        series.setdefault("drought_areas", []).append(entry)

    return MappingProxyType({stream: sort_observed_days(days) for stream, days in series.items()})


def _refuse_truncated_scan(rows: Sequence[Mapping[str, object]], label: str) -> None:
    """Refuse a day series that reached its cap; a truncated series invents gaps that are not in the warehouse."""
    if len(rows) > MAX_OBSERVED_DAY_ROWS:
        raise ObservedDayScanTooLargeError(
            f"{label} returned more than {MAX_OBSERVED_DAY_ROWS} observed-day rows; "
            "raise MAX_OBSERVED_DAY_ROWS rather than reporting a truncated series"
        )


async def _read_observations(
    session: AsyncSession,
    day_series: Mapping[str, tuple[ObservedDay, ...]],
    *,
    server_day: date,
    bbox: Mapping[str, object],
) -> Mapping[str, StreamObservations]:
    """Read every per-row validity count and fold it together with the day series, one entry per stream."""
    counts: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    duplicate_groups: dict[str, int] = {}
    unsupported: dict[str, tuple[frozenset[str], str]] = {}

    shared: dict[str, object] = {"server_day": server_day, **bbox}
    feature_rows = await _fetch_rows(
        session,
        _FEATURE_VALIDITY_COUNTS,
        {
            **shared,
            "published_status": PUBLISHED_FEATURE_STATUS,
            "producer_local_id_ceiling_by_stream": json.dumps(
                dict(PRODUCER_LOCAL_ID_CEILING_BY_STREAM), sort_keys=True
            ),
            "sentinel_property_by_stream": json.dumps(dict(MISSING_VALUE_SENTINEL_PROPERTY_BY_STREAM), sort_keys=True),
            "missing_value_sentinel": USGS_NO_DATA_SENTINEL,
        },
    )
    for row in feature_rows:
        stream = _required_text(row, "stream")
        totals[stream] = _required_count(row, "total_rows")
        counts[stream] = {check: _required_count(row, check) for check in VALIDITY_CHECK_ORDER if check in row}

    duplicate_rows = await _fetch_rows(
        session, _FEATURE_DUPLICATE_IDENTITIES, {"published_status": PUBLISHED_FEATURE_STATUS}
    )
    for row in duplicate_rows:
        stream = _required_text(row, "stream")
        counts.setdefault(stream, {})[DUPLICATE_IDENTITY_CHECK] = _required_count(row, DUPLICATE_IDENTITY_CHECK)
        duplicate_groups[stream] = _required_count(row, "duplicate_identity_groups")

    drought_rows = await _fetch_rows(session, _DROUGHT_AREA_VALIDITY_COUNTS, dict(shared))
    for row in drought_rows:
        totals["drought_areas"] = _required_count(row, "total_rows")
        counts["drought_areas"] = {check: _required_count(row, check) for check in VALIDITY_CHECK_ORDER if check in row}
        unsupported["drought_areas"] = (
            _FEATURE_ONLY_CHECKS | {MISSING_VALUE_SENTINEL_CHECK},
            "geo.drought_areas holds no properties, no external id and no geometry link",
        )

    for row in await _fetch_rows(session, _HISTORICAL_VALIDITY_COUNTS, dict(shared)):
        stream = _required_text(row, "stream")
        totals[stream] = _required_count(row, "total_rows")
        counts[stream] = {check: _required_count(row, check) for check in VALIDITY_CHECK_ORDER if check in row}
        unsupported[stream] = (
            _FEATURE_ONLY_CHECKS | {MISSING_VALUE_SENTINEL_CHECK},
            "the geo.historical_* tables hold no properties, no external id and no geometry link",
        )

    streams = set(totals) | set(day_series)
    observations: dict[str, StreamObservations] = {}
    for stream in streams:
        checks, reason = unsupported.get(stream, (frozenset(), None))
        observations[stream] = StreamObservations(
            total_rows=totals.get(stream, 0),
            day_counts=day_series.get(stream, ()),
            check_counts=MappingProxyType(dict(counts.get(stream, {}))),
            unsupported_checks=checks,
            unsupported_reason=reason,
            duplicate_identity_groups=duplicate_groups.get(stream, 0),
        )
    return MappingProxyType(observations)


async def _read_lane_states(session: AsyncSession) -> tuple[LaneState, ...]:
    """Fold the job ledger's per-status window counts into one row per (lane, run)."""
    rows = await _fetch_rows(session, _JOB_LANE_STATE, {"row_limit": MAX_LANE_STATE_ROWS})
    per_run: dict[tuple[str, str], dict[str, int]] = {}
    outstanding_keys: dict[tuple[str, str], list[str]] = {}
    # The run key carries the lane's declared floor (`archive-walk:<lane>:<floor day>`), so it is read for the
    # floor beside the shard keys: it names the day the run OWES data from even if the oldest windows were
    # never fanned out, where the shard keys can only name the oldest window that actually exists.
    floor_keys: dict[tuple[str, str], list[str]] = {}

    for row in rows:
        run = (_required_text(row, "lane"), _required_text(row, "run_key"))
        status = _required_text(row, "work_item_status")
        per_run.setdefault(run, {})[status] = _required_count(row, "window_count")
        shard_keys = [
            key
            for key in (_optional_text(row, "oldest_shard_key"), _optional_text(row, "newest_shard_key"))
            if key is not None
        ]
        floor_keys.setdefault(run, [run[1]]).extend(shard_keys)
        if status not in SETTLED_WORK_ITEM_STATES:
            outstanding_keys.setdefault(run, []).extend(shard_keys)

    lanes: list[LaneState] = []
    known = frozenset({"succeeded", "retry_wait", DEAD_LETTER_WORK_ITEM_STATE, "queued"})
    for run in sorted(per_run):
        by_status = per_run[run]
        outstanding = sorted(outstanding_keys.get(run, ()))
        lane, run_key = run
        lanes.append(
            LaneState(
                lane=lane,
                run_key=run_key,
                total_windows=sum(by_status.values()),
                succeeded=by_status.get("succeeded", 0),
                retry_wait=by_status.get("retry_wait", 0),
                dead_letter=by_status.get(DEAD_LETTER_WORK_ITEM_STATE, 0),
                queued=by_status.get("queued", 0),
                other_states=MappingProxyType(
                    {status: count for status, count in sorted(by_status.items()) if status not in known}
                ),
                oldest_outstanding_window=outstanding[0] if outstanding else None,
                newest_outstanding_window=outstanding[-1] if outstanding else None,
                lane_floor_day=lane_floor_day(floor_keys.get(run, ())),
            )
        )
    return tuple(lanes)


def lane_floor_day(keys: Iterable[str]) -> date | None:
    """Read the oldest ISO day a run's own keys name; that day is the floor the lane declared it would fill."""
    days: list[date] = []
    for key in keys:
        match = _ISO_DAY_IN_SHARD_KEY.search(key)
        if match is None:
            continue
        try:
            days.append(date.fromisoformat(match.group(0)))
        except ValueError:
            # A key can embed a shaped-but-unreal day; it names no floor and must not become one.
            continue
    return min(days) if days else None


# ---------------------------------------------------------------------------------------------------------------
# The Markdown renderer, built from the same object as the JSON one.
# ---------------------------------------------------------------------------------------------------------------

_VERDICT_MARK: Final[Mapping[str, str]] = MappingProxyType(
    {"complete": "complete", "incomplete": "INCOMPLETE", "invalid": "INVALID"}
)


def _render_markdown(report: ValidationReport) -> str:
    """Render the report as a compact Markdown document; every number here is the one in `to_summary()`."""
    counts = report.verdict_counts
    lines: list[str] = [
        "# PlantGeo warehouse completeness and validity",
        "",
        f"Generated {report.generated_at.isoformat()} against server day {report.server_day.isoformat()}.",
        f"Bbox: `{report.bbox}`." if report.bbox else "Bbox: **unset**, so the out-of-bbox check did not run.",
        (f"Verdicts: {counts['complete']} complete, {counts['incomplete']} incomplete, {counts['invalid']} invalid."),
        "",
        (
            f"Axis rules mirrored from `{report.mirrored_from}`: continuity gap "
            f"{OBSERVATION_CLUSTER_GAP_DAYS} days, density floor "
            f"{float(OBSERVATION_DENSITY_FLOOR_FRACTION):.0%} of the busiest day in the newest cluster."
        ),
        (
            f"Scan bounds: {STATEMENT_TIMEOUT_SECONDS}s statement timeout, read-only snapshot, "
            f"at most {MAX_OBSERVED_DAY_ROWS:,} observed-day rows, "
            f"{MAX_REPORTED_GAPS} gaps and {MAX_REPORTED_THIN_DAYS} thin days listed per stream."
        ),
        "",
        "## Summary",
        "",
        "| Stream | Verdict | Rows | Days | First | Last | Worst gap | Slider window |",
        "| --- | --- | ---: | ---: | --- | --- | ---: | --- |",
    ]
    lines.extend(_summary_row(stream) for stream in report.streams)
    lines.append("")

    for stream in report.streams:
        lines.extend(_stream_section(stream))

    if report.unknown_streams:
        lines.extend(
            [
                "## Streams the catalog does not declare",
                "",
                "These hold rows but have no `StreamDefinition`, so no cadence and no verdict apply to them.",
                "",
                *(f"- `{stream}`" for stream in report.unknown_streams),
                "",
            ]
        )
    if report.unmatched_lanes:
        lines.extend(
            [
                "## Lanes no stream claims",
                "",
                "These runs hold windows in the ledger but no `StreamDefinition.lane_names` names their lane, so "
                "nothing they record decides a verdict. A lane registered in `ingest/lanes.py` appearing here is a "
                "DEFECT IN THIS FILE'S CATALOG, not an operational fact: its dead letters are being ignored.",
                "",
                *(
                    f"- `{lane.lane}` run `{lane.run_key}`: {lane.total_windows} window(s), "
                    f"{lane.dead_letter} dead-lettered, {lane.outstanding_windows} outstanding"
                    for lane in report.unmatched_lanes
                ),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _summary_row(stream: StreamReport) -> str:
    """Render one stream's row in the summary table."""
    completeness = stream.completeness
    window = completeness.slider_window
    window_text = (
        "none"
        if window.earliest_observed_day is None
        else f"{window.earliest_observed_day.isoformat()} to {window.latest_observed_day} ({window.span_days}d)"
    )
    return (
        f"| `{stream.stream}` | {_VERDICT_MARK[stream.verdict]} | {completeness.total_rows:,} "
        f"| {completeness.observed_day_count:,} | {_day_text(completeness.first_observed_day) or '-'} "
        f"| {_day_text(completeness.last_observed_day) or '-'} | {completeness.largest_gap_days:,} "
        f"| {window_text} |"
    )


def _stream_section(stream: StreamReport) -> list[str]:
    """Render one stream's full section: verdict evidence, completeness, validity and lanes."""
    completeness = stream.completeness
    window = completeness.slider_window
    cadence = (
        f"{stream.publication_cadence_days} day(s)" if stream.publication_cadence_days is not None else "none declared"
    )
    lines = [
        f"## `{stream.stream}` -- {_VERDICT_MARK[stream.verdict]}",
        "",
        f"{stream.kind} stream in `{stream.store}`; publication cadence {cadence}.",
        "",
        *(f"- {item}" for item in stream.evidence),
        "",
        "### Completeness",
        "",
        f"- {completeness.total_rows:,} rows over {completeness.observed_day_count:,} observed days, "
        f"{_day_text(completeness.first_observed_day) or 'never'} to "
        f"{_day_text(completeness.last_observed_day) or 'never'}.",
        f"- Expected from {_day_text(completeness.expected_first_day) or 'unknown'} "
        f"({completeness.expected_first_day_source}) through "
        f"{_day_text(completeness.reported_through_day) or 'unknown'}: "
        f"{completeness.expected_day_span if completeness.expected_day_span is not None else 'unknown'} day(s).",
        f"- {completeness.missing_day_count:,} missing days across {completeness.gap_count:,} gap(s), measured "
        f"inside the expected window only.",
    ]
    below = completeness.days_below_expected_floor
    if below is not None:
        lines.append(
            f"- {below.day_count:,} observed day(s) carrying {below.observation_count:,} row(s) fall BELOW the "
            f"expected first day, {below.earliest_day.isoformat()} to {below.latest_day.isoformat()}. These are "
            "real observations outside the declared window, so they are neither a gap nor coverage: they open no "
            f"missing day, and only {completeness.observed_day_count_inside_expected_window:,} of "
            f"{completeness.observed_day_count:,} observed day(s) count towards the span above."
        )
    if completeness.worst_gaps:
        lines.append("- Worst gaps:")
        lines.extend(
            f"  - {gap.gap_from.isoformat()} to {gap.gap_to.isoformat()} ({gap.days:,} days)"
            for gap in completeness.worst_gaps
        )
        if completeness.omitted_gap_count:
            lines.append(f"  - ...and {completeness.omitted_gap_count:,} further gap(s) not listed.")
    lines.append(
        f"- Slider window: {_day_text(window.earliest_observed_day) or 'empty'} to "
        f"{_day_text(window.latest_observed_day) or 'empty'} ({window.observed_day_count:,} days, "
        f"rule `{window.rule}`); clustering dropped {window.gap_excluded_day_count:,} day(s) and the "
        f"density floor dropped {window.density_excluded_day_count:,} more."
    )
    if completeness.thin_day_count:
        lines.append(
            f"- {completeness.thin_day_count:,} day(s) inside that window carry fewer rows than the "
            f"density floor of {window.density_floor}, so they draw as near-empty days:"
        )
        lines.extend(
            f"  - {thin.day.isoformat()}: {thin.observation_count:,} row(s)" for thin in completeness.thin_days
        )
        if completeness.omitted_thin_day_count:
            lines.append(f"  - ...and {completeness.omitted_thin_day_count:,} further thin day(s) not listed.")

    lines.extend(["", "### Validity", ""])
    failing = stream.failing_validity
    if failing:
        lines.extend(f"- **{finding.check}: {finding.count:,}** -- {finding.breaks}" for finding in failing)
    else:
        lines.append("- Every evaluated check returned zero.")
    lines.extend(
        f"- _{finding.check}: not evaluated -- {finding.skipped_reason}_"
        for finding in stream.validity
        if not finding.evaluated
    )

    lines.extend(["", "### Lanes", ""])
    if stream.lanes:
        lines.extend(
            f"- `{lane.lane}` run `{lane.run_key}`: {lane.total_windows:,} window(s), "
            f"{lane.succeeded:,} succeeded, {lane.retry_wait:,} retry_wait, {lane.dead_letter:,} dead_letter, "
            f"{lane.queued:,} queued; outstanding {lane.oldest_outstanding_window or '-'} to "
            f"{lane.newest_outstanding_window or '-'}"
            for lane in stream.lanes
        )
    else:
        lines.append("- No lane in the job ledger claims this stream, so nothing records what filled it.")
    lines.append("")
    return lines
