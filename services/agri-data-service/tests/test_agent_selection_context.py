"""Active map windows survive ingress and remain bound across model turns."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from agri_data_service.agent import tools
from agri_data_service.agent.graph import AgentRequest, AssessSufficiency, WarehouseEvidence, populated_sources
from agri_data_service.agent.prompts import build_location_context
from agri_data_service.agent.selection_context import MapSelection, bind_selection_tools
from agri_data_service.config import Settings
from agri_data_service.routes.agent_analysis import AgentAnalyzeRequest


def selection(**changes: Any) -> MapSelection:
    """A caller's month-scale comparison with an independent VPD day."""
    return MapSelection.model_validate(
        {
            "day": "2024-03-14",
            "range_start": "2024-02-14",
            "range_end": "2024-04-14",
            "time_scale": "month",
            "zoom": 10,
            "layers": [
                {
                    "surface_name": "soil-field-vpd",
                    "day": "2024-02-29",
                    "range_start": "2024-01-29",
                    "range_end": "2024-03-29",
                    "time_scale": "month",
                }
            ],
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"range_start": "2024-04-01"},
        {"range_end": "2024-03-01"},
        {"day": "2024-02-30"},
        {"zoom": float("nan")},
        {"time_scale": "minute"},
    ],
)
def test_invalid_selection_is_rejected_before_retrieval(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        selection(**changes)


def test_ingress_rejects_conflicting_day_and_duplicate_layers() -> None:
    chosen = selection()
    with pytest.raises(ValueError, match="selected_day must match"):
        AgentAnalyzeRequest(longitude=0, latitude=0, selected_day=date(2023, 1, 1), map_selection=chosen)
    with pytest.raises(ValueError, match="repeats a layer"):
        selection(layers=[chosen.layers[0], chosen.layers[0]])
    parsed = AgentAnalyzeRequest(longitude=0, latitude=0, map_selection=chosen)
    assert parsed.map_selection == chosen


async def test_model_reads_use_current_turn_and_layer_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def query(**arguments: Any) -> str:
        calls.append(arguments)
        return json.dumps({"requested_day": arguments["day"]})

    monkeypatch.setattr(tools, "query_surface_evidence_for_selection", query)
    registry = [SimpleNamespace(name="surface_evidence_for_selection")]
    first = bind_selection_tools(registry, longitude=0, latitude=1, selection=selection())[0]
    assert set(first.input_schema["properties"]) == {"surface_name", "page_start"}
    await first.call({"surface_name": "soil-field-vpd", "page_start": 31})
    assert calls[-1] == {
        "surface_name": "soil-field-vpd",
        "day": "2024-02-29",
        "range_start": "2024-01-29",
        "range_end": "2024-03-29",
        "time_scale": "month",
        "zoom": selection().zoom,
        "longitude": 0,
        "latitude": 1,
        "page_start": 31,
    }
    second_selection = selection(day="2024-04-01", range_end="2024-05-01", layers=[])
    next_longitude = 2
    second = bind_selection_tools(registry, longitude=next_longitude, latitude=3, selection=second_selection)[0]
    await second.call({"surface_name": "vegetation"})
    assert calls[-1]["day"] == "2024-04-01"
    assert calls[-1]["range_end"] == "2024-05-01"
    assert calls[-1]["longitude"] == next_longitude
    await first.call({"surface_name": "vegetation"})
    assert calls[-1]["day"] == "2024-03-14"
    assert calls[-1]["longitude"] == 0


def test_followup_context_identifies_current_selection_and_legacy_single_day() -> None:
    request = AgentRequest(longitude=0, latitude=1, precision="exact", map_selection=selection())
    context = build_location_context(
        longitude=0,
        latitude=1,
        precision="exact",
        as_of=datetime(2026, 9, 20, tzinfo=UTC),
        question="Compare with my earlier answer",
        map_selection=request.active_selection(),
    )
    assert "2024-02-14" in context
    assert "2024-04-14" in context
    assert "supersedes earlier turns" in context
    assert "soil-field-vpd" in context
    legacy = AgentRequest(longitude=0, latitude=1, precision="exact", selected_day=date(2024, 2, 29))
    assert legacy.active_selection().range_start == legacy.active_selection().range_end == date(2024, 2, 29)


@pytest.mark.parametrize(
    "url",
    ["http://example.com", "https://user:pass@example.com", "https://example.com?key=x", "https://example.com/path"],
)
def test_map_app_bridge_rejects_unsafe_origin(url: str) -> None:
    with pytest.raises(ValueError, match="AGENT_MAP_APP_URL"):
        Settings(_env_file=None, agent_map_app_url=url)


def test_map_app_bridge_accepts_explicit_public_or_loopback_origin() -> None:
    assert Settings(_env_file=None, agent_map_app_url="https://example.com/").agent_map_app_url == "https://example.com"
    assert (
        Settings(_env_file=None, agent_map_app_url="http://localhost:3001").agent_map_app_url == "http://localhost:3001"
    )


def test_sufficiency_counts_measured_layers_and_ignores_coverage_metadata() -> None:
    ledger = (
        {"tool": "surface_evidence_for_selection", "surface_name": "vegetation", "row_count": 1},
        {"tool": "surface_evidence_for_selection", "surface_name": "soil-field-vpd", "row_count": 1},
        {"tool": "surface_evidence_for_selection", "surface_name": "vegetation", "row_count": 2},
        {"tool": "observation_coverage_on_day", "surface_name": "watersheds", "row_count": 4},
        {"tool": "surface_evidence_for_selection", "surface_name": "soil-survey", "row_count": 0},
    )
    populated = populated_sources(ledger)
    assert populated == ("vegetation", "soil-field-vpd")
    verdict = AssessSufficiency.decide(WarehouseEvidence(ledger, populated, False), has_question=False)
    assert verdict.warehouse_is_sufficient
