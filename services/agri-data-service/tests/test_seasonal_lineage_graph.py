"""Property tests for the derived-signal lineage graph traversal.

These replace a deleted stub that returned a hardcoded ``is_acyclic = True``. Every test below
builds a graph that genuinely breaks the rule under test, so a stub that answered "valid" would
fail here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agri_data_service.method.ml.seasonal_lineage_graph import (
    LineageEdge,
    LineageGraph,
    LineageGraphError,
    LineageNode,
    snapshot_eligible_values,
)

BASE = datetime(2025, 1, 1, tzinfo=UTC)


def _node(  # noqa: PLR0913
    value_id: int,
    *,
    depth: int,
    origin_days: int,
    valid_days: int | None = None,
    availability_days: int | None = None,
    max_depth: int = 8,
) -> LineageNode:
    origin = BASE + timedelta(days=origin_days)
    return LineageNode(
        value_id=value_id,
        signal_key=f"signal_{value_id}",
        lineage_depth=depth,
        origin_cutoff_time=origin,
        valid_time=BASE + timedelta(days=valid_days if valid_days is not None else origin_days - 1),
        availability_time=BASE + timedelta(days=availability_days if availability_days is not None else origin_days),
        max_dependency_depth=max_depth,
    )


def _chain(length: int) -> LineageGraph:
    nodes = [
        _node(index, depth=index, origin_days=index * 10, valid_days=index * 10 - 1, availability_days=index * 10)
        for index in range(length)
    ]
    edges = [LineageEdge(child_value_id=index, parent_value_id=index - 1) for index in range(1, length)]
    return LineageGraph(nodes, edges)


def test_a_valid_chain_is_acyclic_bounded_and_available() -> None:
    validation = _chain(4).validate()
    assert validation.is_acyclic
    assert validation.violations == ()
    assert validation.is_valid
    assert validation.max_observed_depth == 3  # noqa: PLR2004
    assert validation.computed_depth == {0: 0, 1: 1, 2: 2, 3: 3}


def test_a_two_node_cycle_is_detected() -> None:
    nodes = [
        _node(1, depth=1, origin_days=10),
        _node(2, depth=2, origin_days=20),
    ]
    edges = [
        LineageEdge(child_value_id=2, parent_value_id=1),
        LineageEdge(child_value_id=1, parent_value_id=2),
    ]
    validation = LineageGraph(nodes, edges).validate()
    assert not validation.is_acyclic
    assert validation.cycles
    assert not validation.is_valid


def test_a_three_node_cycle_is_detected_and_leaves_those_nodes_undepthed() -> None:
    nodes = [_node(index, depth=index, origin_days=index * 10) for index in (1, 2, 3)]
    edges = [
        LineageEdge(child_value_id=2, parent_value_id=1),
        LineageEdge(child_value_id=3, parent_value_id=2),
        LineageEdge(child_value_id=1, parent_value_id=3),
    ]
    validation = LineageGraph(nodes, edges).validate()
    assert not validation.is_acyclic
    assert validation.computed_depth == {}


def test_self_reference_is_rejected() -> None:
    graph = LineageGraph([_node(1, depth=1, origin_days=10)], [LineageEdge(child_value_id=1, parent_value_id=1)])
    validation = graph.validate()
    assert not validation.is_acyclic
    assert any(violation.rule == "self_reference" for violation in validation.violations)


def test_depth_bound_is_enforced_against_the_declared_maximum() -> None:
    nodes = [
        _node(index, depth=index, origin_days=index * 10, valid_days=index * 10 - 1, max_depth=2) for index in range(4)
    ]
    edges = [LineageEdge(child_value_id=index, parent_value_id=index - 1) for index in range(1, 4)]
    validation = LineageGraph(nodes, edges).validate()
    breaches = [violation for violation in validation.violations if violation.rule == "depth_bound_exceeded"]
    assert {violation.child_value_id for violation in breaches} == {3}


def test_parent_available_after_the_child_origin_is_leakage() -> None:
    nodes = [
        _node(1, depth=0, origin_days=0, valid_days=-1, availability_days=25),
        _node(2, depth=1, origin_days=20, valid_days=19, availability_days=30),
    ]
    validation = LineageGraph(nodes, [LineageEdge(child_value_id=2, parent_value_id=1)]).validate()
    assert any(violation.rule == "parent_not_available_at_child_origin" for violation in validation.violations)
    assert not validation.is_valid


def test_child_available_before_its_parent_is_rejected() -> None:
    nodes = [
        _node(1, depth=0, origin_days=0, valid_days=-1, availability_days=15),
        _node(2, depth=1, origin_days=20, valid_days=19, availability_days=10),
    ]
    validation = LineageGraph(nodes, [LineageEdge(child_value_id=2, parent_value_id=1)]).validate()
    assert any(violation.rule == "child_available_before_parent" for violation in validation.violations)


def test_parent_cutoff_not_strictly_earlier_is_rejected() -> None:
    nodes = [
        _node(1, depth=0, origin_days=20, valid_days=19, availability_days=20),
        _node(2, depth=1, origin_days=20, valid_days=19, availability_days=20),
    ]
    validation = LineageGraph(nodes, [LineageEdge(child_value_id=2, parent_value_id=1)]).validate()
    assert any(violation.rule == "parent_cutoff_not_earlier" for violation in validation.violations)


def test_parent_valid_time_on_or_after_the_child_cutoff_is_rejected() -> None:
    nodes = [
        _node(1, depth=0, origin_days=0, valid_days=25, availability_days=0),
        _node(2, depth=1, origin_days=20, valid_days=19, availability_days=20),
    ]
    validation = LineageGraph(nodes, [LineageEdge(child_value_id=2, parent_value_id=1)]).validate()
    assert any(violation.rule == "parent_valid_time_not_earlier" for violation in validation.violations)


def test_stored_depth_must_equal_parent_depth_plus_one() -> None:
    nodes = [
        _node(1, depth=0, origin_days=0, valid_days=-1),
        _node(2, depth=5, origin_days=20, valid_days=19),
    ]
    validation = LineageGraph(nodes, [LineageEdge(child_value_id=2, parent_value_id=1)]).validate()
    rules = {violation.rule for violation in validation.violations}
    assert "stored_depth_not_parent_plus_one" in rules
    assert "stored_depth_disagrees_with_traversal" in rules


def test_an_edge_naming_an_unknown_node_is_refused_at_construction() -> None:
    with pytest.raises(LineageGraphError, match="unknown parent"):
        LineageGraph([_node(1, depth=0, origin_days=0)], [LineageEdge(child_value_id=1, parent_value_id=99)])


def test_duplicate_nodes_are_refused_at_construction() -> None:
    with pytest.raises(LineageGraphError, match="duplicate lineage node"):
        LineageGraph([_node(1, depth=0, origin_days=0), _node(1, depth=0, origin_days=0)], [])


def test_snapshot_eligibility_requires_the_whole_ancestry_to_be_available() -> None:
    graph = _chain(3)
    # depths 0/1/2 become available at day 0, 10 and 20.
    assert snapshot_eligible_values(graph, BASE + timedelta(days=9)) == (0,)
    assert snapshot_eligible_values(graph, BASE + timedelta(days=19)) == (0, 1)
    assert snapshot_eligible_values(graph, BASE + timedelta(days=20)) == (0, 1, 2)


def test_snapshot_eligibility_returns_nothing_for_an_invalid_graph() -> None:
    nodes = [_node(index, depth=index, origin_days=index * 10) for index in (1, 2)]
    edges = [
        LineageEdge(child_value_id=2, parent_value_id=1),
        LineageEdge(child_value_id=1, parent_value_id=2),
    ]
    assert snapshot_eligible_values(LineageGraph(nodes, edges), BASE + timedelta(days=999)) == ()


def test_eligibility_stops_at_the_last_ancestor_available_by_the_as_of() -> None:
    nodes = [
        _node(1, depth=0, origin_days=0, valid_days=-1, availability_days=100),
        _node(2, depth=1, origin_days=200, valid_days=199, availability_days=200),
    ]
    graph = LineageGraph(nodes, [LineageEdge(child_value_id=2, parent_value_id=1)])
    assert snapshot_eligible_values(graph, BASE + timedelta(days=150)) == (1,)


def test_a_child_available_before_its_parent_yields_no_eligible_value_at_all() -> None:
    """The transitive ancestry walk is belt-and-braces: the schema already forbids this ordering.

    A child that claims to be available before an ancestor is an invalid graph, so eligibility is
    empty for every value in it rather than merely for the offending pair.
    """
    nodes = [
        _node(1, depth=0, origin_days=0, valid_days=-1, availability_days=100),
        _node(2, depth=1, origin_days=200, valid_days=199, availability_days=50),
    ]
    graph = LineageGraph(nodes, [LineageEdge(child_value_id=2, parent_value_id=1)])
    assert snapshot_eligible_values(graph, BASE + timedelta(days=999)) == ()
