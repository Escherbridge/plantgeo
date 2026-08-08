"""The Markdown renderer, built from the same object as the JSON one.

Imports the report models under `TYPE_CHECKING` only. `models.ValidationReport.to_markdown` calls into this
module, so a runtime import back the other way would close a cycle; everything this file needs at runtime is a
constant or `_day_text`, which is why both live in `constants`.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.validation.constants import (
    DAILY_PUBLICATION_CADENCE_DAYS,
    MAX_OBSERVED_DAY_ROWS,
    MAX_REPORTED_GAPS,
    MAX_REPORTED_THIN_DAYS,
    OBSERVATION_CLUSTER_GAP_DAYS,
    OBSERVATION_DENSITY_FLOOR_FRACTION,
    STATEMENT_TIMEOUT_SECONDS,
    _day_text,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.ingest.validation.models import ObservationGap, StreamReport, ValidationReport

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


def _gap_line(gap: ObservationGap) -> str:
    """Render one gap: the calendar silence, and what the stream owed inside it when the two differ."""
    # They differ on any cadence but daily, and saying only "13 days" of a single missed weekly release would
    # read as thirteen missed releases.
    owed = "" if gap.missed_publications == gap.days else f", {gap.missed_publications:,} owed publication(s)"
    return f"  - {gap.gap_from.isoformat()} to {gap.gap_to.isoformat()} ({gap.days:,} days{owed})"


def _stream_section(stream: StreamReport) -> list[str]:
    """Render one stream's full section: verdict evidence, completeness, validity and lanes."""
    completeness = stream.completeness
    window = completeness.slider_window
    cadence_days = stream.publication_cadence_days or DAILY_PUBLICATION_CADENCE_DAYS
    cadence = (
        f"{stream.publication_cadence_days} day(s)" if stream.publication_cadence_days is not None else "none declared"
    )
    # `missing_day_count` counts owed publications, which is a count of days only on a daily cadence.
    missing_unit = (
        "missing days"
        if cadence_days == DAILY_PUBLICATION_CADENCE_DAYS
        else f"missing publications on a {cadence_days}-day cadence"
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
        f"- {completeness.missing_day_count:,} {missing_unit} across {completeness.gap_count:,} gap(s), measured "
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
        lines.extend(_gap_line(gap) for gap in completeness.worst_gaps)
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
