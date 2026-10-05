"""The `agent_tool_call` log event's fields: what was asked, what was served, how close, never the payload.

See agent/AGENTS.md, "Closest-datapoint reads (2026-10-04)", for the field list and how to query it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


def _lane_entries(content: dict[str, Any]) -> list[dict[str, Any]]:
    lanes = content.get("lanes")
    return [lane for lane in lanes if isinstance(lane, dict)] if isinstance(lanes, list) else []


def _summary_state(states: Sequence[str]) -> str | None:
    """One queryable state: the lanes' shared state, or `mixed` when they disagree."""
    distinct = set(states)
    if not distinct:
        return None
    return distinct.pop() if len(distinct) == 1 else "mixed"


def tool_call_fields(
    name: str, arguments: dict[str, Any], content: object, ledger: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Scalar, payload-free fields for one call; per-lane detail rides in `lane_states` only.

    Lane-level values collapse to one scalar each so a log query can filter on them directly:
    `day_offset` is the lane offset furthest from zero, `distance_km` the largest lane distance, and
    `spatial_relation` is `nearest_cell` when ANY lane fell back to a nearest cell.
    """
    result = content if isinstance(content, dict) else {}
    lanes = _lane_entries(result)
    selected: list[dict[str, Any]] = []
    for lane in lanes:
        entry = lane.get("selected")
        if isinstance(entry, dict):
            selected.append(entry)
    lane_states = [str(entry.get("state")) for entry in selected]
    offsets = [entry["day_offset"] for entry in selected if isinstance(entry.get("day_offset"), int)]
    distances = [float(entry["distance_km"]) for entry in selected if isinstance(entry.get("distance_km"), int | float)]
    relations = {entry.get("spatial_relation") for entry in selected} - {None}
    served = {entry.get("served_day") for entry in selected} - {None}
    refusal = (
        result.get("refusal_code")
        or result.get("error")
        or next((entry.get("refusal_code") for entry in selected if entry.get("refusal_code")), None)
    )
    surface = result.get("surface_name") or arguments.get("surface_name")
    return {
        "tool": name,
        "surface": surface if isinstance(surface, str) else None,
        "lanes": [str(lane.get("parquet_lane")) for lane in lanes],
        "requested_day": result.get("requested_day") or arguments.get("day") or arguments.get("as_of_day"),
        "served_day": served.pop() if len(served) == 1 else None,
        "day_offset": max(offsets, key=abs) if offsets else None,
        "spatial_relation": "nearest_cell" if "nearest_cell" in relations else relations.pop() if relations else None,
        "distance_km": max(distances) if distances else None,
        "state": result.get("state") if isinstance(result.get("state"), str) else _summary_state(lane_states),
        "lane_states": lane_states,
        "static": any(lane.get("static") is True for lane in lanes),
        "refusal_code": refusal if isinstance(refusal, str) else None,
        "record_count": sum(int(entry.get("row_count") or 0) for entry in ledger),
    }
