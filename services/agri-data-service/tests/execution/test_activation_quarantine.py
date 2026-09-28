"""`o2b-incidents`, GL-5 (spec Sec 4.9.3 "Quarantine"; plan `config_driven_ingestion_20260926` 0W.5):
`ActivationConfig` gains a `quarantined` side-list, `active_lanes` is defined as the parsed ids minus
it, and `parse_activation` never exits the process over a bad allow-list entry -- it quarantines the
offending id and keeps going. No database and no subprocess: every case here is a pure function of an
environment mapping (or, for the last test, of `LANE_SPECS` itself)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

import pytest

import agri_data_service.execution.lane_specs as lane_specs_module
from agri_data_service.execution.gap_repair import plan_gap_repairs
from agri_data_service.execution.gap_repair_contract import REPAIR_BINDINGS, RepairBudget
from agri_data_service.execution.job_executor_service import (
    ACTIVE_LANES_VARIABLE,
    LANE_SPECS,
    ActivationConfig,
    parse_activation,
)
from agri_data_service.execution.lane_ids import VEGETATION_DIRECT_LANE_ID
from agri_data_service.execution.lane_specs import (
    _conflict_quarantine,
)
from agri_data_service.parquet_ops.wire import DayRange, LaneCoverage, WarehouseCoverage

NOW: Final = datetime(2026, 9, 28, 6, tzinfo=UTC)


def _coverage(layer: str, lane_id: str) -> WarehouseCoverage:
    """One lane's worth of overdue coverage, bound through `REPAIR_BINDINGS[layer]` to `lane_id`."""
    assert REPAIR_BINDINGS[layer].lane_id == lane_id, f"fixture assumes {layer!r} binds to {lane_id!r}"
    row = LaneCoverage(
        layer=layer,
        nature="daily_series",
        kind="observed",
        zoom=0,
        earliest_day=None,
        latest_day=None,
        latest_recorded_day=None,
        published_ranges=(),
        gap_ranges=(DayRange(first_day=NOW.date().replace(day=1), last_day=NOW.date()),),
        governed_absence_ranges=(),
        coverage_authority="availability",
        source_ceiling_day=NOW.date(),
        expected_horizon_day=NOW.date(),
        staleness_days=1,
        behind_provider=False,
        withheld_reason=None,
    )
    return WarehouseCoverage(generated_at=NOW, evaluated_through_day=NOW.date(), lanes=(row,))


# --- ActivationConfig / parse_activation: quarantine, never a process exit -------------------------


def test_empty_activation_quarantines_nothing() -> None:
    activation = parse_activation({})
    assert activation == ActivationConfig(frozenset())
    assert activation.quarantined == frozenset()


def test_known_executable_lanes_stay_active_and_unquarantined() -> None:
    selected = {"fire-detections-direct-forward", "water-gauges-direct-forward"}
    activation = parse_activation({ACTIVE_LANES_VARIABLE: ",".join(sorted(selected))})
    assert activation.active_lanes == selected
    assert activation.quarantined == frozenset()


def test_unknown_ids_are_quarantined_not_rejected() -> None:
    activation = parse_activation({ACTIVE_LANES_VARIABLE: "retired-lane, also-retired"})
    assert activation.active_lanes == frozenset()
    assert activation.quarantined == frozenset({"retired-lane", "also-retired"})


def test_non_executable_ids_are_quarantined() -> None:
    non_executable = [lane_id for lane_id, spec in LANE_SPECS.items() if not spec.executable]
    if not non_executable:
        pytest.skip("every registered lane is currently executable; nothing to quarantine for this reason")
    lane_id = non_executable[0]
    activation = parse_activation({ACTIVE_LANES_VARIABLE: lane_id})
    assert lane_id in activation.quarantined
    assert lane_id not in activation.active_lanes


def test_activation_config_quarantined_defaults_to_empty_and_stays_positional_compatible() -> None:
    """Every existing single-argument construction (`ActivationConfig(frozenset({...}))`, used all
    over this service's other test modules) must keep working unedited."""
    activation = ActivationConfig(frozenset({"a", "b"}))
    assert activation.active_lanes == frozenset({"a", "b"})
    assert activation.quarantined == frozenset()


def test_active_lanes_excludes_quarantined_through_plan_gap_repairs() -> None:
    """No edit was needed in `gap_repair.py`: it reads `activation.active_lanes` straight through, and
    that field already excludes a quarantined id by construction (spec Sec 4.9.3)."""
    layer = "vegetation"
    coverage = _coverage(layer, VEGETATION_DIRECT_LANE_ID)

    active = parse_activation({ACTIVE_LANES_VARIABLE: VEGETATION_DIRECT_LANE_ID})
    quarantined_instead = parse_activation({ACTIVE_LANES_VARIABLE: "retired-lane"})

    admitted = plan_gap_repairs(coverage, activation=active, now=NOW, budget=RepairBudget())
    withheld = plan_gap_repairs(coverage, activation=quarantined_instead, now=NOW, budget=RepairBudget())

    admitted_candidate = next(candidate for candidate in admitted.candidates if candidate.facts.layer == layer)
    withheld_candidate = next(candidate for candidate in withheld.candidates if candidate.facts.layer == layer)
    assert admitted_candidate.verdict != "lane_inactive"
    assert withheld_candidate.verdict == "lane_inactive"


class _FakeSpec:
    """The only field `_conflict_quarantine` reads off a `LaneExecutionSpec`."""

    def __init__(self, conflicts_with: tuple[str, ...]) -> None:
        self.conflicts_with = conflicts_with


def test_conflict_keeps_the_legacy_incumbent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_conflict_quarantine` unit-tested directly (no lane in `LANE_SPECS` declares a conflict at
    HEAD, spec Sec 4.9.3, so this cannot be exercised through `parse_activation` against the real
    registry yet): the lane that DECLARES `conflicts_with` is the newcomer and is quarantined; the
    lane it names (the incumbent) is kept. A mutual declaration -- "both the same kind" -- quarantines
    both."""
    incumbent_id, newcomer_id = "incumbent-lane", "newcomer-lane"
    candidate = frozenset({incumbent_id, newcomer_id})

    monkeypatch.setattr(
        lane_specs_module,
        "LANE_SPECS",
        MappingProxyType({incumbent_id: _FakeSpec(()), newcomer_id: _FakeSpec((incumbent_id,))}),
    )
    assert _conflict_quarantine(candidate) == frozenset({newcomer_id})

    monkeypatch.setattr(
        lane_specs_module,
        "LANE_SPECS",
        MappingProxyType({incumbent_id: _FakeSpec((newcomer_id,)), newcomer_id: _FakeSpec((incumbent_id,))}),
    )
    assert _conflict_quarantine(candidate) == candidate
