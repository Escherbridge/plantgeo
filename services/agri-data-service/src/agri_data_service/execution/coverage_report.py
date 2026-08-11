"""Render a coverage census for a person first and a machine second.

Pure formatting, no I/O and no session: everything here takes a `ContractCensus` and returns text
or a JSON-ready mapping. `coverage-status` is the verb an operator reaches for when a layer looks
wrong, and it is the only liveness signal the gap-fill cron has, so its default output is a table a
person can read at a glance rather than the JSON every other verb in this service emits.

See `src/agri_data_service/execution/AGENTS.md`, the coverage-status/coverage-fill section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from agri_data_service.execution.coverage_contract import contiguous_ranges

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.execution.coverage_census import ContractCensus
    from agri_data_service.execution.coverage_contract import SignalReconciliation

# How many collapsed ranges are spelled out per signal before the rest are summarised. Twelve fits
# an 80x24 terminal alongside the header without scrolling the table off the top, and a lane with
# more than twelve separate holes is a lane whose shape the count already tells you.
MAX_PRINTED_RANGES: Final = 12

# Widths chosen so the widest declared signal name -- `surface_shortwave_radiation`, 27 characters,
# measured against LANE_COVERAGE_CONTRACTS 2026-08-11 -- fits without truncation.
_SIGNAL_COLUMN: Final = 30
_NUMBER_COLUMN: Final = 9


def format_range(span: tuple[date, date]) -> str:
    """One inclusive run as an operator reads it: a single day stays a single day."""
    start, end = span
    days = (end - start).days + 1
    if start == end:
        return f"{start.isoformat()} (1 day)"
    return f"{start.isoformat()}..{end.isoformat()} ({days} days)"


def format_ranges(days: Sequence[date], *, limit: int = MAX_PRINTED_RANGES) -> str:
    """Collapse a day list into runs and spell out the first `limit` of them."""
    ranges = contiguous_ranges(days)
    if not ranges:
        return "none"
    shown = ", ".join(format_range(span) for span in ranges[:limit])
    if len(ranges) <= limit:
        return shown
    return f"{shown}, and {len(ranges) - limit} more range(s)"


def render_contract(census: ContractCensus) -> str:
    """One contract as a header block and a per-signal table, ranges indented under each signal."""
    contract = census.contract
    lines = [
        f"lane                     {contract.source_key}",
        f"grid                     {contract.grid_name} ({census.expected_cell_count} cells)",
        f"support                  {contract.support_key}",
        f"cadence                  {contract.cadence}",
        f"contracted from          {contract.earliest_required_day.isoformat()}",
        f"contracted through       {census.through_day.isoformat()}  ({census.through_day_basis})",
        f"cell floor               {contract.minimum_cell_fraction:.2f} of the lattice",
        "",
        f"{'signal':<{_SIGNAL_COLUMN}}"
        f"{'complete':>{_NUMBER_COLUMN}}"
        f"{'required':>{_NUMBER_COLUMN}}"
        f"{'missing':>{_NUMBER_COLUMN}}"
        f"{'thin':>{_NUMBER_COLUMN}}"
        f"{'absent':>{_NUMBER_COLUMN}}",
        "-" * (_SIGNAL_COLUMN + _NUMBER_COLUMN * 5),
    ]
    for signal in census.signals:
        lines.append(
            f"{signal.signal_name:<{_SIGNAL_COLUMN}}"
            f"{signal.completeness_fraction * 100:>{_NUMBER_COLUMN - 1}.1f}%"
            f"{signal.required_day_count:>{_NUMBER_COLUMN}}"
            f"{len(signal.missing_days):>{_NUMBER_COLUMN}}"
            f"{len(signal.thin_days):>{_NUMBER_COLUMN}}"
            f"{len(signal.absent_days):>{_NUMBER_COLUMN}}"
        )
        if signal.missing_days:
            lines.append(f"    missing: {format_ranges(signal.missing_days)}")
        if signal.thin_days:
            lines.append(f"    thin:    {format_ranges(signal.thin_days)}")
    lines.append("")
    lines.append(verdict_line(census))
    return "\n".join(lines)


def verdict_line(census: ContractCensus) -> str:
    """The one line an operator acts on: what is wrong, and which verb can actually close it.

    A thin-only lane must NOT be sent to `coverage-fill`. That verb reads `missing_days` and a thin
    day is not one, so it would answer `lane_has_no_missing_days` on every tick forever while the
    report kept saying it was incomplete -- a cron that is green about being broken.
    """
    thin_day_count = sum(len(signal.thin_days) for signal in census.signals)
    if census.is_complete:
        return "VERDICT: complete -- nothing to fetch."
    if census.missing_day_count == 0:
        return (
            f"VERDICT: incomplete -- 0 signal-days missing, {thin_day_count} thin. coverage-fill "
            "cannot close a thin day; it acts on missing days only. A thin day landed some cells "
            "and under the lane's cell floor, so either the lattice this lane is measured over is "
            "wider than the one its provider serves -- fix the contract's grid or its "
            "minimum_cell_fraction -- or re-run the lane's own walk over the thin ranges."
        )
    return (
        f"VERDICT: incomplete -- {census.missing_day_count} signal-days missing, {thin_day_count} thin. "
        "Run coverage-fill to turn the oldest missing run into a plan."
    )


def render_census(censuses: Sequence[ContractCensus]) -> str:
    """Every contract, separated by a rule, oldest declaration first."""
    if not censuses:
        return "no coverage contract matched; nothing to report."
    blocks = [render_contract(census) for census in censuses]
    return f"\n\n{'=' * (_SIGNAL_COLUMN + _NUMBER_COLUMN * 5)}\n\n".join(blocks)


def signal_payload(signal: SignalReconciliation) -> dict[str, object]:
    """One signal as JSON: the three numbers the standard requires, plus the collapsed runs."""
    return {
        "signal_name": signal.signal_name,
        "earliest_required_day": signal.earliest_required_day.isoformat(),
        "through_day": signal.through_day.isoformat(),
        "required_day_count": signal.required_day_count,
        "completeness_fraction": round(signal.completeness_fraction, 6),
        "is_complete": signal.is_complete,
        "missing_day_count": len(signal.missing_days),
        "thin_day_count": len(signal.thin_days),
        "absent_day_count": len(signal.absent_days),
        "missing_ranges": [
            {"start_day": start.isoformat(), "end_day": end.isoformat(), "day_count": (end - start).days + 1}
            for start, end in contiguous_ranges(signal.missing_days)
        ],
        "thin_ranges": [
            {"start_day": start.isoformat(), "end_day": end.isoformat(), "day_count": (end - start).days + 1}
            for start, end in contiguous_ranges(signal.thin_days)
        ],
    }


def contract_payload(census: ContractCensus) -> dict[str, object]:
    """One contract as JSON, carrying the denominators so a reader can re-derive every fraction."""
    contract = census.contract
    return {
        "source_key": contract.source_key,
        "grid_name": contract.grid_name,
        "support_key": contract.support_key,
        "cadence": str(contract.cadence),
        "earliest_required_day": contract.earliest_required_day.isoformat(),
        "through_day": census.through_day.isoformat(),
        "through_day_basis": str(census.through_day_basis),
        "expected_cell_count": census.expected_cell_count,
        "minimum_cell_fraction": contract.minimum_cell_fraction,
        "is_complete": census.is_complete,
        "missing_day_count": census.missing_day_count,
        "signals": [signal_payload(signal) for signal in census.signals],
    }


def coverage_status_payload(censuses: Sequence[ContractCensus]) -> dict[str, object]:
    """The whole run as one JSON document."""
    return {
        "contract_count": len(censuses),
        "is_complete": all(census.is_complete for census in censuses),
        "contracts": [contract_payload(census) for census in censuses],
    }
