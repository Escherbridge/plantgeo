"""The typed shapes the report is made of: the stream catalog, one finding per rule, and the whole report."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.archive_walk import archive_lane_definition_name
from agri_data_service.ingest.lanes import FIRMS_ARCHIVE_LANE, STREAMFLOW_ARCHIVE_LANE
from agri_data_service.ingest.validation.constants import (
    ARCHIVE_LANE_DEFINITION_NAMES,
    MAX_LANE_STATE_ROWS,
    MAX_OBSERVED_DAY_ROWS,
    MAX_REPORTED_GAPS,
    MAX_REPORTED_THIN_DAYS,
    MIRRORED_READ_MODEL_PATH,
    NO_DETAIL,
    OBSERVATION_CLUSTER_GAP_DAYS,
    OBSERVATION_DENSITY_FLOOR_FRACTION,
    PUBLISHED_FEATURE_STATUS,
    STATEMENT_TIMEOUT_SECONDS,
    _day_text,
)
from agri_data_service.ingest.validation.markdown import _render_markdown

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime

    from agri_data_service.ingest.validation.constants import (
        EarliestObservedDayRule,
        ExpectedFirstDaySource,
        StreamKind,
        StreamStore,
        StreamVerdict,
    )


# ---------------------------------------------------------------------------------------------------------------
# The declared shape of a stream. Cadence is what turns a gap into a finding, so it is stated per stream and
# its basis is stated with it.
# ---------------------------------------------------------------------------------------------------------------


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
    # DAYS AND MISSED PUBLICATIONS ARE DIFFERENT NUMBERS ON ANY CADENCE BUT DAILY, and both are kept because
    # both are read: `days` is the calendar silence, which is what `decide_verdict` compares against the
    # declared cadence, while `missed_publications` is how many releases the stream actually owed inside that
    # silence and is what `missing_day_count` sums. On a daily stream they are equal by construction.
    missed_publications: int

    def to_summary(self) -> dict[str, object]:
        """Render the gap as the explicit (from, to, days) triple the report promises."""
        summary: dict[str, object] = {
            "from": self.gap_from.isoformat(),
            "to": self.gap_to.isoformat(),
            "days": self.days,
        }
        if self.missed_publications != self.days:
            summary["missed_publications"] = self.missed_publications
        return summary


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


@dataclass(frozen=True, slots=True)
class StreamObservations:
    """Everything one SQL pass learned about a stream, before any rule was applied to it."""

    total_rows: int = 0
    day_counts: tuple[ObservedDay, ...] = ()
    check_counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    unsupported_checks: frozenset[str] = frozenset()
    unsupported_reason: str | None = None
    duplicate_identity_groups: int = 0
