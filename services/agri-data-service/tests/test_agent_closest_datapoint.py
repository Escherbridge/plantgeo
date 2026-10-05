"""Closest-datapoint agent reads (owner decisions 2026-10-04) through the public tool and HTTP seams.

Real DuckDB over small Parquet fixtures (`LocalWarehouse`), the map's own day resolver, and the
admitted SSURGO release reader. See agent/AGENTS.md, "Closest-datapoint reads (2026-10-04)".
"""

# ruff: noqa: PLR2004 - fixture coordinates, offsets and distances are the assertions.

from __future__ import annotations

import json
import struct
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import structlog

from agri_data_service.agent import selection_evidence, soil_survey_reads, tools
from agri_data_service.agent.selection_scope import PAGE_DAYS, evidence_days
from agri_data_service.config import settings
from agri_data_service.foundation.soil_survey.receipts import AreaCensusEntry, AreaInventory, Blob, digest, encoded
from agri_data_service.foundation.soil_survey.release import Release, ShardRef, manifest_key, release_key
from agri_data_service.pipeline.direct.soil_survey.prepare import load_candidate_manifest, prepare_candidate
from agri_data_service.pipeline.parquet.availability_storage import StoredAvailabilityObject
from agri_data_service.routes import agent_tools as route
from tests.direct.soil_survey.fakes import AREA, VINTAGE, completed
from tests.test_agent_selection_evidence import LocalWarehouse, climate_row

if TYPE_CHECKING:
    from pathlib import Path

DAY = date(2026, 6, 15)
#: The diagnosed production point: no NDVI cell covers it on any day.
GORGE = {"longitude": -121.95, "latitude": 45.68}
BOISE = {"longitude": -116.49, "latitude": 43.49}


def ndvi_row(day: date, *, longitude: float, latitude: float, value: float = 0.6) -> dict[str, Any]:
    return {
        "cell_id": f"ndvi-{longitude}/{latitude}",
        "grid_name": "sentinel2-ndvi-0p25deg",
        "metric_name": "ndvi",
        "metric_unit": "unitless",
        "observed_day": day,
        "metric_value": value,
        "observation_checksum": "fixture",
        "data_available_at": datetime.combine(day, datetime.min.time(), UTC),
        "release_count": 1,
        "allowed_client_exposure": True,
        "cell_longitude": longitude,
        "cell_latitude": latitude,
    }


def _wkb_square(west: float, south: float, east: float, north: float) -> bytes:
    ring = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    body = struct.pack("<BII", 1, 3, 1) + struct.pack("<I", len(ring))
    return body + b"".join(struct.pack("<dd", x, y) for x, y in ring)


def drought_row(day: date) -> dict[str, Any]:
    return {
        "area_id": f"usdm-{day.isoformat()}-d1",
        "valid_date": day,
        "dm_category": 1,
        "source_url": "https://droughtmonitor.unl.edu/fixture",
        "ingested_at": datetime.combine(day, datetime.min.time(), UTC),
        "geom": _wkb_square(-117.0, 43.0, -116.0, 44.0),
    }


def write_lane(source: LocalWarehouse, root: Path, surface: str, day: date) -> None:
    """One published day for a surface's single lane, covering the Boise probe point."""
    if surface == "vegetation":
        source.write(root, "vegetation", day, [ndvi_row(day, longitude=-116.375, latitude=43.375)])
    elif surface == "drought-areas":
        source.write(root, "drought", day, [drought_row(day)])
    else:
        source.write(root, surface, day, [climate_row(day, longitude=-116, latitude=43, value=day.day)])


async def read_surface(source: LocalWarehouse, surface: str, **overrides: Any) -> dict[str, Any]:
    arguments = {
        "surface_name": surface,
        "day": DAY.isoformat(),
        **BOISE,
        "range_start": DAY.isoformat(),
        "range_end": DAY.isoformat(),
        "zoom": 13,
        **overrides,
    }
    async with tools.run_context(warehouse_source=source):
        return json.loads(await tools.query_surface_evidence_for_selection(**arguments))


# --- Decision 1: nearest published day within the lane's tolerance -----------------------------


@pytest.mark.parametrize(
    ("surface", "published_offsets", "state", "offset"),
    [
        # exact day: no substitution, offset 0
        ("climate-field-dew-point", (0, -1), "published", 0),
        # daily lanes: +/-3 days, both directions, tie goes to the earlier (settled) day
        ("climate-field-dew-point", (-2,), "published_nearest", -2),
        ("climate-field-dew-point", (3,), "published_nearest", 3),
        ("climate-field-dew-point", (-2, 2), "published_nearest", -2),
        ("climate-field-dew-point", (-4, 5), "day_not_written", -4),
        # NDVI revisit: 2 x the registry's measured 7-day gap = +/-14
        ("vegetation", (-14,), "published_nearest", -14),
        ("vegetation", (15,), "day_not_written", 15),
        # weekly drought: 2 x cadence 7 = +/-14
        ("drought-areas", (6, -8), "published_nearest", 6),
        ("drought-areas", (-15,), "day_not_written", -15),
    ],
)
async def test_selected_day_resolves_to_the_nearest_published_day_within_tolerance(
    tmp_path: Path, surface: str, published_offsets: tuple[int, ...], state: str, offset: int
) -> None:
    source = LocalWarehouse()
    for published in published_offsets:
        write_lane(source, tmp_path, surface, DAY + timedelta(days=published))
    selected = (await read_surface(source, surface))["lanes"][0]["selected"]
    assert selected["state"] == state
    assert selected["requested_day"] == DAY.isoformat()
    served = (DAY + timedelta(days=offset)).isoformat()
    if state == "day_not_written":
        # Beyond tolerance: still unpublished, but the caller can say how far the nearest day is.
        assert selected["features"] == []
        assert (selected["nearest_published_day"], selected["nearest_day_offset"]) == (served, offset)
    else:
        assert (selected["served_day"], selected["day_offset"]) == (served, offset)
        assert selected["features"], "a substituted day still serves the nearest day's own numbers"
        assert selected["spatial_relation"] == "covers"
        assert selected["distance_km"] == 0.0


async def test_a_governed_absence_on_the_selected_day_names_itself_when_substituted(tmp_path: Path) -> None:
    source = LocalWarehouse()
    source.listing_store.write_absence(
        "vegetation",
        "observed",
        13,
        DAY,
        reason="source_empty",
        upstream_response="cloud screened",
        recorded_at=datetime(2026, 6, 16, tzinfo=UTC),
        run_id="fixture",
    )
    write_lane(source, tmp_path, "vegetation", DAY - timedelta(days=5))
    selected = (await read_surface(source, "vegetation"))["lanes"][0]["selected"]
    assert selected["state"] == "published_nearest"
    assert selected["day_offset"] == -5
    assert selected["requested_day_state"] == "governed_absence"
    assert selected["requested_day_absence"]["reason"] == "source_empty"


# --- Decision 2: always the nearest cell, with its distance ------------------------------------


async def test_a_point_outside_every_cell_gets_the_nearest_cell_and_its_great_circle_distance(tmp_path: Path) -> None:
    source = LocalWarehouse()
    near = ndvi_row(DAY, longitude=-121.625, latitude=45.875, value=0.41)  # support [-121.75,-121.5]x[45.75,46]
    far = ndvi_row(DAY, longitude=-120.125, latitude=44.125, value=0.88)
    source.write(tmp_path, "vegetation", DAY, [near, far])
    selected = (await read_surface(source, "vegetation", **GORGE))["lanes"][0]["selected"]
    assert selected["state"] == "published"
    assert selected["spatial_relation"] == "nearest_cell"
    assert selected["distance_km_basis"] == "cell_edge"
    # The cell's nearest corner (-121.75, 45.75) is 17.37 km from the probe by great circle.
    assert selected["distance_km"] == pytest.approx(17.37, abs=0.02)
    nearest = selected["features"][0]
    assert nearest["spatial_relation"] == "nearest_cell"
    assert nearest["covers_probe_point"] is False
    assert nearest["properties"]["metric_value"] == 0.41


async def test_a_covering_cell_is_covers_at_zero_km(tmp_path: Path) -> None:
    source = LocalWarehouse()
    covering = ndvi_row(DAY, longitude=-121.875, latitude=45.625)  # support [-122,-121.75]x[45.5,45.75]
    source.write(tmp_path, "vegetation", DAY, [covering])
    selected = (await read_surface(source, "vegetation", **GORGE))["lanes"][0]["selected"]
    assert (selected["spatial_relation"], selected["distance_km"]) == ("covers", 0.0)
    assert [entry["spatial_relation"] for entry in selected["features"]] == ["contains_selection"]


# --- Decision 3: page-1 ranking and the multi-lane page budget ---------------------------------


def test_page_one_ranks_selected_then_nearest_published_then_endpoints() -> None:
    first, last = date(2022, 1, 1), date(2026, 12, 31)
    before, after = DAY - timedelta(days=9), DAY + timedelta(days=17)
    published = {date(2022, 3, 4), before, after, date(2026, 11, 27)}
    ordered = evidence_days(first, last, DAY, published, today=last)
    assert ordered[:5] == (DAY, before, after, first, last)
    assert len(ordered) == len(set(ordered)) == (last - first).days + 1
    assert max(evidence_days(first, last, DAY, published, today=DAY)) == DAY


async def test_the_first_history_page_reads_the_selected_day_and_its_published_neighbours(tmp_path: Path) -> None:
    source = LocalWarehouse()
    lane = "climate-field-dew-point"
    first, last = DAY - timedelta(days=60), DAY + timedelta(days=60)
    before, after = DAY - timedelta(days=9), DAY + timedelta(days=17)
    for day in (first, before, after, last):
        write_lane(source, tmp_path, lane, day)
    result = await read_surface(source, lane, range_start=first.isoformat(), range_end=last.isoformat())
    assert result["history"]["days_per_page"] == PAGE_DAYS
    assert result["history"]["sampled_days"] == [before.isoformat(), DAY.isoformat(), after.isoformat()]


async def test_multi_lane_surfaces_read_two_history_days_and_nothing_after_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(selection_evidence, "utc_today", lambda: DAY)
    source = LocalWarehouse()
    lane = "soil-temperature-0-to-7cm"
    lineage = {
        "data_source_key": "era5-land",
        "source_parameter": "stl1",
        "physical_candidate_count": 1,
        "lineage_sha256": "0" * 64,
        "input_manifest_sha256": "1" * 64,
    }
    for day in (DAY - timedelta(days=2), DAY - timedelta(days=1)):
        source.write(tmp_path, lane, day, [{**climate_row(day, longitude=-116, latitude=43, value=9), **lineage}])
    result = await read_surface(
        source,
        "soil-field-temperature",
        range_start=(DAY - timedelta(days=2)).isoformat(),
        range_end=(DAY + timedelta(days=30)).isoformat(),
    )
    history = result["history"]
    assert history["days_per_page"] == 2
    assert history["schedulable_day_count"] == 3
    assert all(day <= DAY.isoformat() for day in history["sampled_days"])
    assert [len(entry["history"]) for entry in result["lanes"]] == [2, 2, 2, 2]


# --- Decision 4: soil survey through the admitted release reader -------------------------------


class _MemoryStorage:
    """An in-memory `AvailabilityStorage` holding exactly one admitted release."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        payload = self.objects.get(key)
        if payload is None:
            return None
        assert len(payload) <= max_bytes
        return StoredAvailabilityObject(payload=payload, etag=digest(payload))


async def _admit_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prepare one real shard from the capture fixture, pin it, and point the agent's reader at it."""
    await completed(tmp_path)
    candidate, candidate_sha = prepare_candidate(tmp_path, "ID-1", [AREA])
    _, manifest = load_candidate_manifest(tmp_path, candidate_sha)
    shard = ShardRef.from_candidate(candidate, Blob(sha256=candidate_sha, byte_count=len(manifest)))
    scope = AreaInventory(
        response=Blob(sha256=digest(b"{}"), byte_count=2),
        query_sha256=digest(b"census"),
        checked_at=candidate.captured_at,
        areas=(AreaCensusEntry(area=AREA, saverest=VINTAGE),),
        envelope=(-126.0, 41.0, -110.0, 50.0),
        region="pnw",
    )
    release = Release(
        scope=scope,
        shards=(shard,),
        pending_areas=(),
        source_evidence="capture_volume",
        release_day=shard.release_day,
        captured_at=shard.captured_at,
    )
    objects = {manifest_key(candidate_sha): manifest}
    for part in candidate.parts:
        objects[part.blob.key] = (tmp_path / "objects" / part.blob.sha256).read_bytes()
    payload = encoded(release)
    objects[release_key(digest(payload))] = payload
    monkeypatch.setattr(settings, "ssurgo_admitted_release_sha256", digest(payload))
    monkeypatch.setattr(soil_survey_reads, "release_storage", lambda: _MemoryStorage(objects))


async def test_soil_survey_reads_map_units_from_the_admitted_release_with_no_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admit_release(tmp_path, monkeypatch)
    source = LocalWarehouse()
    result = await read_surface(source, "soil-survey", longitude=0.3, latitude=0.3)
    lane = result["lanes"][0]
    assert (lane["static"], lane["reader"], lane["history"]) == (True, "planes.soil_survey", [])
    selected = lane["selected"]
    assert selected["state"] == "published"
    assert selected["static"] is True
    assert "requested_day" not in selected
    assert "served_day" not in selected
    assert selected["release_day"] == VINTAGE[:10]
    assert [feature["properties"]["areaSymbol"] for feature in selected["features"]] == [AREA]
    assert selected["features"][0]["properties"]["muname"]
    assert "geometry" not in selected["features"][0]
    assert (selected["spatial_relation"], selected["distance_km"]) == ("covers", 0.0)
    assert result["history"]["sampling"] == "static_current_release"
    # Never the observed-partition listing that answered day_not_written for every date.
    assert source.operations == ["agent_soil_survey_release"]


async def test_soil_survey_outside_every_delineation_reports_the_nearest_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admit_release(tmp_path, monkeypatch)
    # 0.05 degrees east of the first fixture polygon's x=2 edge, inside no delineation.
    selected = (await read_surface(LocalWarehouse(), "soil-survey", longitude=2.05, latitude=0.5))["lanes"][0][
        "selected"
    ]
    assert selected["spatial_relation"] == "nearest_cell"
    assert selected["distance_km_basis"] == "delineation_edge"
    assert selected["distance_km"] == pytest.approx(5.56, abs=0.05)
    assert len(selected["features"]) == 1


# --- Decision 6: the history tools are published, and cost Gemini no enum-array states ---------


def _enum_arrays(schema: object) -> list[str]:
    """Every array-of-enum property: the Gemini forced-call "too many states" trigger."""
    if isinstance(schema, dict):
        found = ["array-of-enum"] if schema.get("type") == "array" and "enum" in (schema.get("items") or {}) else []
        return found + [hit for value in schema.values() for hit in _enum_arrays(value)]
    if isinstance(schema, list):
        return [hit for value in schema for hit in _enum_arrays(value)]
    return []


async def test_the_drought_history_tool_is_published_and_answers_through_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = {schema["function"]["name"]: schema for schema in route.environmental_tool_schemas()}
    for name in ("drought_history_at_point", "fire_history_near_point"):
        assert _enum_arrays(published[name]) == [], name
    source = LocalWarehouse()
    release = DAY - timedelta(days=5)
    source.write(tmp_path, "drought", release, [drought_row(release)])
    monkeypatch.setattr(route, "run_context", lambda: tools.run_context(warehouse_source=source))
    payload = {
        "name": "drought_history_at_point",
        "arguments": {**BOISE, "weeks_back": 2, "as_of_day": DAY.isoformat()},
    }
    response = await route.call_agent_tool(SimpleNamespace(json=payload, body=json.dumps(payload).encode()))
    assert response.status == 200
    weekly = json.loads(response.body)["result"]["weekly_severity"]
    assert [(row["valid_date"], row["severity_class"]) for row in weekly] == [(release.isoformat(), 1)]


# --- Decision 5: one structured log event per HTTP tool call -----------------------------------


async def test_the_bridge_logs_one_agent_tool_call_event_without_the_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = LocalWarehouse()
    write_lane(source, tmp_path, "climate-field-dew-point", DAY - timedelta(days=2))
    monkeypatch.setattr(route, "run_context", lambda: tools.run_context(warehouse_source=source))
    arguments = {
        "surface_name": "climate-field-dew-point",
        "day": DAY.isoformat(),
        **BOISE,
        "range_start": DAY.isoformat(),
        "range_end": DAY.isoformat(),
    }
    payload = {"name": "surface_evidence_for_selection", "arguments": arguments}
    request = SimpleNamespace(json=payload, body=json.dumps(payload).encode())
    with structlog.testing.capture_logs() as logs:
        response = await route.call_agent_tool(request)
    assert response.status == 200
    events = [entry for entry in logs if entry["event"] == "agent_tool_call"]
    assert len(events) == 1
    event = events[0]
    assert {key: event[key] for key in ("tool", "surface", "lanes", "requested_day", "state")} == {
        "tool": "surface_evidence_for_selection",
        "surface": "climate-field-dew-point",
        "lanes": ["climate-field-dew-point"],
        "requested_day": DAY.isoformat(),
        "state": "published_nearest",
    }
    assert (event["served_day"], event["day_offset"]) == ((DAY - timedelta(days=2)).isoformat(), -2)
    assert (event["spatial_relation"], event["distance_km"]) == ("covers", 0.0)
    assert event["refusal_code"] is None
    assert event["record_count"] == 1
    assert event["duration_ms"] >= 0
    # Never the payload or the caller's coordinates.
    assert not {"features", "result", "arguments", "longitude", "latitude"} & set(event)
