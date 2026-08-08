"""Report assembly: turn one stream's raw counts into findings, apply every rule, and decide its verdict.

Pure like `completeness`, and deliberately so -- the builder is handed everything it needs rather than going
and fetching any of it, which is what lets the whole rule set be exercised without a database.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from agri_data_service.ingest.validation.completeness import (
    build_slider_window,
    decide_verdict,
    find_observation_gaps,
    find_thin_days,
    rank_gaps,
    sort_observed_days,
    split_days_at_expected_floor,
    summarise_days_below_expected_floor,
)
from agri_data_service.ingest.validation.constants import (
    DAILY_PUBLICATION_CADENCE_DAYS,
    DUPLICATE_IDENTITY_CHECK,
    MALFORMED_IDENTITY_CHECK,
    MAX_REPORTED_GAPS,
    MAX_REPORTED_THIN_DAYS,
    MISSING_VALUE_SENTINEL_CHECK,
    MISSING_VALUE_SENTINEL_PROPERTY_BY_STREAM,
    NO_DETAIL,
    OUTSIDE_BBOX_CHECK,
    PRODUCER_LOCAL_ID_CEILING_BY_STREAM,
    UNDATED_DAY_CHECK,
    VALIDITY_CHECK_CONSEQUENCES,
    VALIDITY_CHECK_ORDER,
)
from agri_data_service.ingest.validation.models import CompletenessReport, StreamReport, ValidityFinding

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.ingest.validation.constants import ExpectedFirstDaySource
    from agri_data_service.ingest.validation.models import LaneState, StreamDefinition, StreamObservations


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
    gaps = find_observation_gaps(
        days,
        expected_first_day=expected_first_day,
        through_day=through_day,
        # A stream that declares no cadence is walked daily, which is what it was walked on before cadence
        # entered the walk at all.
        publication_cadence_days=definition.publication_cadence_days or DAILY_PUBLICATION_CADENCE_DAYS,
    )
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
        # Publications owed and not produced, which is a count of days only on a daily cadence; see
        # `find_observation_gaps`.
        missing_day_count=sum(gap.missed_publications for gap in gaps),
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
        # Calendar days, not owed publications: this is the number the declared cadence is stated in.
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
