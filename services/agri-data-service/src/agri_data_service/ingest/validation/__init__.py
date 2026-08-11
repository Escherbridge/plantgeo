"""Cross-stream completeness and validity report over the warehouse, the slider window it produces, and its lanes.

The async scan band lives HERE rather than in a submodule on purpose. `MAX_OBSERVED_DAY_ROWS` is a module
global that `tests/test_ingest_validation.py` rebinds with `monkeypatch.setattr` on this package; a reader
defined in a submodule would resolve its *own* global and sail straight past the patch, so the test that proves
a truncated day series is refused would silently stop proving it. Everything else is split by seam --
`constants` (the values mirrored from the TypeScript read model, the scan bounds and the validity vocabulary),
`errors`, `models`, `completeness` (the pure rules), `report` (assembly), `queries` (the `sql/ingest/*.sql`
bindings), `rows` (typed result readers) and `markdown` (the renderer) -- and every name this module ever
exposed is re-exported below, so no importer and no test has to change. See ingest/AGENTS.md "validation.py".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from agri_data_service.ingest.policy import parse_bbox, resolve_bounded_bbox
from agri_data_service.ingest.validation.completeness import (
    _gap_from_silence,
    apply_density_floor,
    build_slider_window,
    decide_verdict,
    density_floor_for,
    find_observation_gaps,
    find_thin_days,
    lane_floor_day,
    missing_publication_days,
    newest_observation_cluster,
    publication_grid_points,
    rank_gaps,
    sort_observed_days,
    split_days_at_expected_floor,
    summarise_days_below_expected_floor,
)
from agri_data_service.ingest.validation.constants import (
    _FEATURE_ONLY_CHECKS,
    _ISO_DAY_IN_SHARD_KEY,
    ARCHIVE_LANE_DEFINITION_NAMES,
    DAILY_PUBLICATION_CADENCE_DAYS,
    DEAD_LETTER_WORK_ITEM_STATE,
    DUPLICATE_IDENTITY_CHECK,
    FUTURE_DAY_CHECK,
    MALFORMED_IDENTITY_CHECK,
    MAX_LANE_STATE_ROWS,
    MAX_OBSERVED_DAY_ROWS,
    MAX_REPORTED_GAPS,
    MAX_REPORTED_THIN_DAYS,
    MINIMUM_DENSITY_FLOOR,
    MIRRORED_READ_MODEL_PATH,
    MISSING_EXTERNAL_ID_CHECK,
    MISSING_VALUE_SENTINEL_CHECK,
    MISSING_VALUE_SENTINEL_PROPERTY_BY_STREAM,
    NO_DETAIL,
    NULL_GEOMETRY_CHECK,
    OBSERVATION_CLUSTER_GAP_DAYS,
    OBSERVATION_DENSITY_FLOOR_FRACTION,
    OUTSIDE_BBOX_CHECK,
    PRODUCER_LOCAL_ID_CEILING_BY_STREAM,
    PUBLISHED_FEATURE_STATUS,
    SETTLED_WORK_ITEM_STATES,
    STATEMENT_TIMEOUT_SECONDS,
    UNDATED_DAY_CHECK,
    UNLINKED_GEOMETRY_CHECK,
    USGS_NO_DATA_SENTINEL,
    VALIDITY_CHECK_CONSEQUENCES,
    VALIDITY_CHECK_ORDER,
    EarliestObservedDayRule,
    ExpectedFirstDaySource,
    StreamKind,
    StreamStore,
    StreamVerdict,
    _day_text,
    producer_local_id_ceiling,
)
from agri_data_service.ingest.validation.errors import ObservedDayScanTooLargeError, ValidationRowError
from agri_data_service.ingest.validation.markdown import _VERDICT_MARK, _render_markdown, _stream_section, _summary_row
from agri_data_service.ingest.validation.models import (
    DEFAULT_STREAM_DEFINITIONS,
    CompletenessReport,
    LaneState,
    ObservationGap,
    ObservationsBelowExpectedFloor,
    ObservedDay,
    SliderWindow,
    StreamDefinition,
    StreamObservations,
    StreamReport,
    ThinDay,
    ValidationReport,
    ValidityFinding,
    lane_publication_cadence_days,
    stream_definition_for_lane,
)
from agri_data_service.ingest.validation.queries import (
    _DROUGHT_AREA_OBSERVED_DAYS,
    _DROUGHT_AREA_VALIDITY_COUNTS,
    _FEATURE_DUPLICATE_IDENTITIES,
    _FEATURE_OBSERVED_DAYS,
    _FEATURE_VALIDITY_COUNTS,
    _HISTORICAL_OBSERVED_DAYS,
    _HISTORICAL_VALIDITY_COUNTS,
    _JOB_LANE_STATE,
    _SERVER_DAY,
    _SET_READ_ONLY_SNAPSHOT,
    _SET_STATEMENT_TIMEOUT,
    OBSERVED_DAYS_FOR_LAYER,
)
from agri_data_service.ingest.validation.report import (
    _expected_day_span,
    _reference_undated_day_note,
    _resolve_expected_first_day,
    _skip_reason,
    build_stream_report,
    build_validity_findings,
)
from agri_data_service.ingest.validation.rows import (
    _fetch_rows,
    _optional_text,
    _required_count,
    _required_day,
    _required_text,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


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
        (_FEATURE_OBSERVED_DAYS, "observed_days"),
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


__all__ = [
    "ARCHIVE_LANE_DEFINITION_NAMES",
    "DAILY_PUBLICATION_CADENCE_DAYS",
    "DEAD_LETTER_WORK_ITEM_STATE",
    "DEFAULT_STREAM_DEFINITIONS",
    "DUPLICATE_IDENTITY_CHECK",
    "FUTURE_DAY_CHECK",
    "MALFORMED_IDENTITY_CHECK",
    "MAX_LANE_STATE_ROWS",
    "MAX_OBSERVED_DAY_ROWS",
    "MAX_REPORTED_GAPS",
    "MAX_REPORTED_THIN_DAYS",
    "MINIMUM_DENSITY_FLOOR",
    "MIRRORED_READ_MODEL_PATH",
    "MISSING_EXTERNAL_ID_CHECK",
    "MISSING_VALUE_SENTINEL_CHECK",
    "MISSING_VALUE_SENTINEL_PROPERTY_BY_STREAM",
    "NO_DETAIL",
    "NULL_GEOMETRY_CHECK",
    "OBSERVATION_CLUSTER_GAP_DAYS",
    "OBSERVATION_DENSITY_FLOOR_FRACTION",
    "OBSERVED_DAYS_FOR_LAYER",
    "OUTSIDE_BBOX_CHECK",
    "PRODUCER_LOCAL_ID_CEILING_BY_STREAM",
    "PUBLISHED_FEATURE_STATUS",
    "SETTLED_WORK_ITEM_STATES",
    "STATEMENT_TIMEOUT_SECONDS",
    "UNDATED_DAY_CHECK",
    "UNLINKED_GEOMETRY_CHECK",
    "USGS_NO_DATA_SENTINEL",
    "VALIDITY_CHECK_CONSEQUENCES",
    "VALIDITY_CHECK_ORDER",
    "_DROUGHT_AREA_OBSERVED_DAYS",
    "_DROUGHT_AREA_VALIDITY_COUNTS",
    "_FEATURE_DUPLICATE_IDENTITIES",
    "_FEATURE_OBSERVED_DAYS",
    "_FEATURE_ONLY_CHECKS",
    "_FEATURE_VALIDITY_COUNTS",
    "_HISTORICAL_OBSERVED_DAYS",
    "_HISTORICAL_VALIDITY_COUNTS",
    "_ISO_DAY_IN_SHARD_KEY",
    "_JOB_LANE_STATE",
    "_SERVER_DAY",
    "_SET_READ_ONLY_SNAPSHOT",
    "_SET_STATEMENT_TIMEOUT",
    "_VERDICT_MARK",
    "CompletenessReport",
    "EarliestObservedDayRule",
    "ExpectedFirstDaySource",
    "LaneState",
    "ObservationGap",
    "ObservationsBelowExpectedFloor",
    "ObservedDay",
    "ObservedDayScanTooLargeError",
    "SliderWindow",
    "StreamDefinition",
    "StreamKind",
    "StreamObservations",
    "StreamReport",
    "StreamStore",
    "StreamVerdict",
    "ThinDay",
    "ValidationReport",
    "ValidationRowError",
    "ValidityFinding",
    "_bbox_parameters",
    "_day_text",
    "_expected_day_span",
    "_fetch_rows",
    "_gap_from_silence",
    "_optional_text",
    "_read_day_series",
    "_read_lane_states",
    "_read_observations",
    "_reference_undated_day_note",
    "_refuse_truncated_scan",
    "_render_markdown",
    "_required_count",
    "_required_day",
    "_required_text",
    "_resolve_expected_first_day",
    "_skip_reason",
    "_stream_section",
    "_summary_row",
    "apply_density_floor",
    "build_slider_window",
    "build_stream_report",
    "build_validation_report",
    "build_validity_findings",
    "decide_verdict",
    "density_floor_for",
    "find_observation_gaps",
    "find_thin_days",
    "lane_floor_day",
    "lane_publication_cadence_days",
    "missing_publication_days",
    "newest_observation_cluster",
    "producer_local_id_ceiling",
    "publication_grid_points",
    "rank_gaps",
    "sort_observed_days",
    "split_days_at_expected_floor",
    "stream_definition_for_lane",
    "summarise_days_below_expected_floor",
]
