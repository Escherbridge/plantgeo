"""S7: the one generic precedence transform, settled ▷ provisional, with a per-row `precedence_source`.

For each output stream, every key (a lattice cell, a station) takes its row from the highest-ranked
input lane that has one, ranked by the transform lane's `inputs` order. An input stream whose every
row a higher input covers is reported superseded; the runner prunes it only when the lane's
`[pruning]` is enabled (§4.6). Imports only the runner contract. See `pipeline/lanes/AGENTS.md`
"transforms/precedence.py" for the stream-routing rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.runner.contract import Derivation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.pipeline.runner.contract import DayContext

#: The column naming which input lane a row came from.
PRECEDENCE_SOURCE_COLUMN: Final = "precedence_source"
#: Candidate grains, first match wins: every input table of one output must carry the chosen columns.
KEY_COLUMN_CANDIDATES: Final[tuple[tuple[str, ...], ...]] = (("cell_id",), ("station_id",), ("site_id",))


class PrecedenceRouteError(ValueError):
    """An output stream's inputs cannot be routed or merged unambiguously (the day's `strategy_error`)."""


def _tokens(slug: str) -> list[str]:
    return slug.split("-")


def route_input_stream(output: str, candidates: Sequence[str]) -> str | None:
    """The one input stream of one input lane that feeds `output`, or `None` when that lane feeds it nothing.

    Rule 1: a candidate that is `output` with one hyphen token added (`meteorology-era5-dew-point`
    feeds `meteorology-dew-point`). Rule 2, only when rule 1 finds none: the one candidate sharing
    `output`'s first token (`shortwave-power` feeds `shortwave-radiation`). Two matches under either
    rule is ambiguous and refused.
    """
    wanted = _tokens(output)
    inserted = [
        candidate
        for candidate in candidates
        if len(_tokens(candidate)) == len(wanted) + 1
        and any(
            _tokens(candidate)[:index] + _tokens(candidate)[index + 1 :] == wanted
            for index in range(len(_tokens(candidate)))
        )
    ]
    if len(inserted) > 1:
        raise PrecedenceRouteError(f"{output}: input streams {inserted} all route to it")
    if inserted:
        return inserted[0]
    same_family = [candidate for candidate in candidates if _tokens(candidate)[0] == wanted[0]]
    if len(same_family) > 1:
        raise PrecedenceRouteError(f"{output}: input streams {same_family} share its family; name one per lane")
    return same_family[0] if same_family else None


def _key_columns(tables: Sequence[pa.Table]) -> tuple[str, ...]:
    for candidate in KEY_COLUMN_CANDIDATES:
        if all(all(column in table.column_names for column in candidate) for table in tables):
            return candidate
    raise PrecedenceRouteError(f"no shared key among {KEY_COLUMN_CANDIDATES} in the input tables")


def _keys(table: pa.Table, columns: Sequence[str]) -> list[tuple[object, ...]]:
    return list(zip(*(table.column(column).to_pylist() for column in columns), strict=True))


@dataclass(frozen=True, slots=True)
class RankedInput:
    """One input lane's routed stream-day, in precedence order."""

    lane_id: str
    stream: str
    table: pa.Table


def merge_by_precedence(ranked: Sequence[RankedInput]) -> tuple[pa.Table | None, tuple[str, ...]]:
    """Each key's row from the highest-ranked input holding it, stamped with that input's lane id.

    Returns the merged table (or `None` when no input has a row) and the lower-ranked streams whose
    every key a higher input already covered.
    """
    present = [item for item in ranked if item.table.num_rows > 0]
    if not present:
        return None, ()
    key_columns = _key_columns([item.table for item in present])
    top = present[0].table
    columns = [name for name in top.column_names if name != PRECEDENCE_SOURCE_COLUMN]
    schema = top.select(columns).schema
    seen: set[tuple[object, ...]] = set()
    parts: list[pa.Table] = []
    superseded: list[str] = []
    for rank, item in enumerate(present):
        missing = [name for name in columns if name not in item.table.column_names]
        if missing:
            raise PrecedenceRouteError(f"{item.stream} lacks columns {missing} that {present[0].stream} carries")
        keys = _keys(item.table, key_columns)
        keep = [key not in seen for key in keys]
        if rank > 0 and not any(keep):
            superseded.append(item.stream)
        seen.update(keys)
        kept = item.table.filter(pa.array(keep, pa.bool_())).select(columns).cast(schema)
        if kept.num_rows:
            source = pa.array([item.lane_id] * kept.num_rows, pa.string())
            parts.append(kept.append_column(PRECEDENCE_SOURCE_COLUMN, source))
    return pa.concat_tables(parts), tuple(superseded)


@dataclass(frozen=True, slots=True)
class PrecedenceTransform:
    """Settled ▷ provisional over the transform lane's `inputs`, one merge per output stream (S7)."""

    def derive(self, day: date, inputs: Mapping[str, pa.Table | None], context: DayContext) -> Derivation:  # noqa: ARG002 - the Protocol's day
        """Route each output stream to one input stream per input lane, then merge by rank."""
        tables: dict[str, pa.Table] = {}
        superseded: list[str] = []
        for output in context.output_streams:
            ranked: list[RankedInput] = []
            for lane_id, streams in context.input_streams.items():
                routed = route_input_stream(output, streams)
                table = None if routed is None else inputs.get(routed)
                if routed is not None and table is not None:
                    ranked.append(RankedInput(lane_id=lane_id, stream=routed, table=table))
            merged, covered = merge_by_precedence(ranked)
            if merged is not None:
                tables[output] = merged
            superseded.extend(covered)
        return Derivation(table=tables or None, superseded_inputs=tuple(dict.fromkeys(superseded)))


STRATEGY: Final = PrecedenceTransform()

__all__ = [
    "KEY_COLUMN_CANDIDATES",
    "PRECEDENCE_SOURCE_COLUMN",
    "STRATEGY",
    "PrecedenceRouteError",
    "PrecedenceTransform",
    "RankedInput",
    "merge_by_precedence",
    "route_input_stream",
]
