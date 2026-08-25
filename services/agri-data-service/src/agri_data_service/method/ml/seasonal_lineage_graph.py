"""Real traversal of a derived-signal lineage graph: cycles, depth and availability ordering.

This module computes its answers. It replaces a deleted stub that returned a hardcoded
``is_acyclic = True``; every predicate below is derived from the edges it is given, and the tests
feed it graphs that genuinely violate each rule.

The same three rules are enforced declaratively in the database. They were written by
`alembic/archive/20260814_0021_forecast_signal_lineage.py` -- applied history since the 2026-08-25
greenfield collapse, so the live definitions now come from `db/agri/**` via
`alembic/versions/20260825_0000_agri_greenfield_baseline.py`. This module is the auditor: given
rows read back out of any database, it re-derives whether the constraints did their job.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

# The bound the schema also carries; a deeper chain is refused rather than truncated.
DEFAULT_MAX_DEPENDENCY_DEPTH: Final = 8


class LineageGraphError(ValueError):
    """The lineage graph is structurally unusable, e.g. an edge names an unknown node."""


@dataclass(frozen=True)
class LineageNode:
    """One derived-signal value and the four times that decide whether it may have parents."""

    value_id: int
    signal_key: str
    lineage_depth: int
    origin_cutoff_time: datetime
    valid_time: datetime
    availability_time: datetime
    max_dependency_depth: int = DEFAULT_MAX_DEPENDENCY_DEPTH


@dataclass(frozen=True)
class LineageEdge:
    """A child derived signal's reference to one parent."""

    child_value_id: int
    parent_value_id: int
    parent_role: str = "feedback_parent"


@dataclass(frozen=True)
class LineageViolation:
    """One broken rule, named so a failure message says which edge and which predicate."""

    rule: str
    child_value_id: int
    parent_value_id: int | None
    detail: str


@dataclass(frozen=True)
class LineageValidation:
    """The full verdict of a traversal."""

    node_count: int
    edge_count: int
    is_acyclic: bool
    cycles: tuple[tuple[int, ...], ...]
    computed_depth: Mapping[int, int]
    max_observed_depth: int
    violations: tuple[LineageViolation, ...]

    @property
    def is_valid(self) -> bool:
        """True only when the graph is acyclic, within depth, and availability-ordered."""
        return self.is_acyclic and not self.violations


class LineageGraph:
    """Adjacency over derived-signal values; parents point at the values a child consumed."""

    def __init__(self, nodes: Iterable[LineageNode], edges: Iterable[LineageEdge]) -> None:
        self._nodes: dict[int, LineageNode] = {}
        for node in nodes:
            if node.value_id in self._nodes:
                raise LineageGraphError(f"duplicate lineage node {node.value_id}")
            self._nodes[node.value_id] = node
        self._parents: dict[int, list[int]] = {value_id: [] for value_id in self._nodes}
        self._children: dict[int, list[int]] = {value_id: [] for value_id in self._nodes}
        self._edges: list[LineageEdge] = []
        for edge in edges:
            if edge.child_value_id not in self._nodes:
                raise LineageGraphError(f"edge names unknown child {edge.child_value_id}")
            if edge.parent_value_id not in self._nodes:
                raise LineageGraphError(f"edge names unknown parent {edge.parent_value_id}")
            self._edges.append(edge)
            self._parents[edge.child_value_id].append(edge.parent_value_id)
            self._children[edge.parent_value_id].append(edge.child_value_id)

    @property
    def nodes(self) -> Mapping[int, LineageNode]:
        """Every node, keyed by value id."""
        return self._nodes

    @property
    def edges(self) -> Sequence[LineageEdge]:
        """Every edge, in insertion order."""
        return tuple(self._edges)

    def parents_of(self, value_id: int) -> tuple[int, ...]:
        """The values a child directly consumed."""
        return tuple(self._parents[value_id])

    def find_cycles(self) -> tuple[tuple[int, ...], ...]:
        """Every simple cycle reachable by depth-first search, as a node path back to its start."""
        white, grey, black = 0, 1, 2
        colour: dict[int, int] = dict.fromkeys(self._nodes, white)
        cycles: list[tuple[int, ...]] = []
        seen: set[frozenset[int]] = set()

        for root in sorted(self._nodes):
            if colour[root] != white:
                continue
            stack: list[tuple[int, int]] = [(root, 0)]
            path: list[int] = [root]
            colour[root] = grey
            while stack:
                node, index = stack[-1]
                parents = self._parents[node]
                if index >= len(parents):
                    colour[node] = black
                    stack.pop()
                    path.pop()
                    continue
                stack[-1] = (node, index + 1)
                parent = parents[index]
                if colour[parent] == grey:
                    start = path.index(parent)
                    cycle = (*path[start:], parent)
                    signature = frozenset(cycle)
                    if signature not in seen:
                        seen.add(signature)
                        cycles.append(cycle)
                    continue
                if colour[parent] == white:
                    colour[parent] = grey
                    stack.append((parent, 0))
                    path.append(parent)
        return tuple(cycles)

    def computed_depth(self) -> dict[int, int]:
        """Longest path to a root, by Kahn ordering. Nodes inside a cycle are absent from the result."""
        remaining = {value_id: len(self._parents[value_id]) for value_id in self._nodes}
        depth = {value_id: 0 for value_id in self._nodes if remaining[value_id] == 0}
        queue = deque(sorted(depth))
        resolved: dict[int, int] = dict(depth)
        while queue:
            node = queue.popleft()
            for child in sorted(self._children[node]):
                resolved[child] = max(resolved.get(child, 0), resolved[node] + 1)
                remaining[child] -= 1
                if remaining[child] == 0:
                    queue.append(child)
        return {value_id: value for value_id, value in resolved.items() if remaining[value_id] == 0}

    def validate(self) -> LineageValidation:
        """Traverse the graph and report every cycle, depth breach and availability inversion."""
        cycles = self.find_cycles()
        depth = self.computed_depth()
        violations: list[LineageViolation] = []

        for edge in self._edges:
            child = self._nodes[edge.child_value_id]
            parent = self._nodes[edge.parent_value_id]
            if edge.child_value_id == edge.parent_value_id:
                violations.append(
                    LineageViolation(
                        rule="self_reference",
                        child_value_id=edge.child_value_id,
                        parent_value_id=edge.parent_value_id,
                        detail="a derived signal cannot be its own parent",
                    )
                )
            if parent.origin_cutoff_time >= child.origin_cutoff_time:
                violations.append(
                    LineageViolation(
                        rule="parent_cutoff_not_earlier",
                        child_value_id=child.value_id,
                        parent_value_id=parent.value_id,
                        detail=(
                            f"parent cutoff {parent.origin_cutoff_time.isoformat()} is not strictly before "
                            f"child cutoff {child.origin_cutoff_time.isoformat()}"
                        ),
                    )
                )
            if parent.valid_time >= child.origin_cutoff_time:
                violations.append(
                    LineageViolation(
                        rule="parent_valid_time_not_earlier",
                        child_value_id=child.value_id,
                        parent_value_id=parent.value_id,
                        detail=(
                            f"parent valid time {parent.valid_time.isoformat()} is not strictly before "
                            f"child cutoff {child.origin_cutoff_time.isoformat()}"
                        ),
                    )
                )
            if parent.availability_time > child.origin_cutoff_time:
                violations.append(
                    LineageViolation(
                        rule="parent_not_available_at_child_origin",
                        child_value_id=child.value_id,
                        parent_value_id=parent.value_id,
                        detail=(
                            f"parent became available {parent.availability_time.isoformat()} after the child "
                            f"origin {child.origin_cutoff_time.isoformat()}"
                        ),
                    )
                )
            if child.availability_time < parent.availability_time:
                violations.append(
                    LineageViolation(
                        rule="child_available_before_parent",
                        child_value_id=child.value_id,
                        parent_value_id=parent.value_id,
                        detail=(
                            f"child availability {child.availability_time.isoformat()} precedes parent "
                            f"availability {parent.availability_time.isoformat()}"
                        ),
                    )
                )
            if child.lineage_depth != parent.lineage_depth + 1:
                violations.append(
                    LineageViolation(
                        rule="stored_depth_not_parent_plus_one",
                        child_value_id=child.value_id,
                        parent_value_id=parent.value_id,
                        detail=f"stored child depth {child.lineage_depth} != parent depth {parent.lineage_depth} + 1",
                    )
                )

        for value_id, node in sorted(self._nodes.items()):
            computed = depth.get(value_id)
            if computed is not None and computed != node.lineage_depth:
                violations.append(
                    LineageViolation(
                        rule="stored_depth_disagrees_with_traversal",
                        child_value_id=value_id,
                        parent_value_id=None,
                        detail=f"stored {node.lineage_depth}, traversed {computed}",
                    )
                )
            if node.lineage_depth > node.max_dependency_depth:
                violations.append(
                    LineageViolation(
                        rule="depth_bound_exceeded",
                        child_value_id=value_id,
                        parent_value_id=None,
                        detail=f"depth {node.lineage_depth} exceeds the declared bound {node.max_dependency_depth}",
                    )
                )

        return LineageValidation(
            node_count=len(self._nodes),
            edge_count=len(self._edges),
            is_acyclic=not cycles,
            cycles=cycles,
            computed_depth=depth,
            max_observed_depth=max(depth.values(), default=0),
            violations=tuple(violations),
        )


def snapshot_eligible_values(
    graph: LineageGraph,
    snapshot_as_of: datetime,
) -> tuple[int, ...]:
    """The derived values an ML feature snapshot with this as-of may consume.

    A value qualifies only when it is available at or before the snapshot as-of **and** every value
    in its transitive lineage is too. One late ancestor disqualifies the whole descendant chain, so
    a snapshot can never contain a feature whose provenance was not yet knowable.
    """
    validation = graph.validate()
    if not validation.is_valid:
        return ()
    eligible: set[int] = set()
    for value_id in sorted(graph.nodes):
        stack = [value_id]
        seen: set[int] = set()
        admissible = True
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if graph.nodes[current].availability_time > snapshot_as_of:
                admissible = False
                break
            stack.extend(graph.parents_of(current))
        if admissible:
            eligible.add(value_id)
    return tuple(sorted(eligible))
