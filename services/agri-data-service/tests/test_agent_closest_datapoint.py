"""Closest-datapoint agent reads (owner decisions 2026-10-04) through the public tool and HTTP seams.

Real DuckDB over small Parquet fixtures (`LocalWarehouse`), the map's own day resolver, and the
admitted SSURGO release reader. See agent/AGENTS.md, "Closest-datapoint reads (2026-10-04)".
"""

# ruff: noqa: PLR2004 - fixture coordinates, offsets and distances are the assertions.

from __future__ import annotations

import json
import re
import struct
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
import structlog

from agri_data_service.agent import selection_evidence, soil_survey_reads, tools
from agri_data_service.agent.day_tolerance import nearest_published_day
from agri_data_service.agent.selection_reads import SPARSE_AREA_LANES, SelectionReader
from agri_data_service.agent.selection_scope import (
    MAX_LANE_DAY_READS,
    PAGE_DAYS,
    RESERVED_READS_PER_LANE,
    Selection,
    evidence_days,
    schedule_ceiling,
)
from agri_data_service.agent.soil_properties import READS_ENABLED_VARIABLE
from agri_data_service.agent.surfaces import SURFACE_PARQUET_LANES
from agri_data_service.config import settings
from agri_data_service.foundation.soil_survey.receipts import AreaCensusEntry, AreaInventory, Blob, digest, encoded
from agri_data_service.foundation.soil_survey.release import Release, ShardRef, manifest_key, release_key
from agri_data_service.pipeline.direct.soil_survey.prepare import load_candidate_manifest, prepare_candidate
from agri_data_service.pipeline.parquet.availability_storage import StoredAvailabilityObject
from agri_data_service.routes import agent_tools as route
from tests.direct.soil_survey.fakes import AREA, VINTAGE, completed
from tests.test_agent_selection_evidence import LocalWarehouse, climate_row

#: The web's copy of the published catalogue, measured by `ai-prompt-provider-tools.test.ts`.
WEB_CURRENT_CATALOGUE = (
    Path(__file__).resolve().parents[3] / "src" / "__tests__" / "services" / "agri-tool-catalogue-current.fixture.json"
)
#: The web workflow whose `REGIONAL_SPARSE_AREA_NOUNS` mirrors `SPARSE_AREA_LANES`.
WEB_WORKFLOW = (
    Path(__file__).resolve().parents[3] / "src" / "lib" / "server" / "services" / "regional-analysis-workflow.ts"
)

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
    "case",
    [
        # dew point settles 5 days behind: asking for today may borrow up to 3 + 5 days back
        ("climate-field-dew-point", 0, -8, "published_nearest", 8),
        ("climate-field-dew-point", 0, -9, "day_not_written", 8),
        # three days ago only 2 lag days still overlap the request: 3 + 2
        ("climate-field-dew-point", 3, -5, "published_nearest", 5),
        ("climate-field-dew-point", 3, -6, "day_not_written", 5),
        # NDVI's lag field is its revisit gap, already inside its +/-14: no second allowance
        ("vegetation", 0, -15, "day_not_written", 14),
        # a day AFTER today is still at most the whole lag unsettled: 3 + 5, never wider
        ("climate-field-dew-point", -10, -12, "day_not_written", 8),
    ],
)
async def test_a_request_near_today_allows_for_the_lanes_settle_lag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: tuple[str, int, int, str, int]
) -> None:
    surface, today_after, published_offset, state, tolerance = case
    monkeypatch.setattr(selection_evidence, "utc_today", lambda: DAY + timedelta(days=today_after))
    source = LocalWarehouse()
    write_lane(source, tmp_path, surface, DAY + timedelta(days=published_offset))
    lane = (await read_surface(source, surface))["lanes"][0]
    assert lane["selected"]["state"] == state
    assert lane["tolerance_days"] == tolerance, "the wire states the bound actually applied"


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


@pytest.mark.parametrize(
    ("surface", "requested_day", "published_offset", "state"),
    [
        # Nothing written on the selected day: borrow the nearest published day within tolerance.
        ("vegetation", "unwritten", -5, "published_nearest"),
        ("climate-field-dew-point", "unwritten", -1, "published_nearest"),
        # A governed absence IS a published answer (fire-detections: FIRMS returned zero), so another
        # day's numbers never replace it -- even one day away, well inside tolerance.
        ("vegetation", "governed_absence", -5, "governed_absence"),
        ("climate-field-dew-point", "governed_absence", -1, "governed_absence"),
    ],
)
async def test_only_an_unwritten_day_borrows_and_a_governed_absence_keeps_its_measured_zero(
    tmp_path: Path, surface: str, requested_day: str, published_offset: int, state: str
) -> None:
    source = LocalWarehouse()
    if requested_day == "governed_absence":
        source.listing_store.write_absence(
            surface,
            "observed",
            13,
            DAY,
            reason="source_empty",
            upstream_response="zero records",
            recorded_at=datetime(2026, 6, 16, tzinfo=UTC),
            run_id="fixture",
        )
    nearest = DAY + timedelta(days=published_offset)
    write_lane(source, tmp_path, surface, nearest)
    selected = (await read_surface(source, surface))["lanes"][0]["selected"]
    assert selected["state"] == state
    if state == "published_nearest":
        assert (selected["served_day"], selected["day_offset"]) == (nearest.isoformat(), published_offset)
        assert selected["requested_day_state"] == "day_not_written"
        assert selected["features"]
    else:
        assert selected["features"] == []
        assert selected["absence"]["reason"] == "source_empty"
        assert "served_day" not in selected or selected["served_day"] == DAY.isoformat()
        # The nearest day is information beside the absence, never its value.
        assert (selected["nearest_published_day"], selected["nearest_day_offset"]) == (
            nearest.isoformat(),
            published_offset,
        )


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


def weather_station_row(day: date, *, longitude: float, latitude: float, temperature: float) -> dict[str, Any]:
    observed = datetime.combine(day, datetime.min.time(), UTC)
    return {
        "latitude": latitude,
        "longitude": longitude,
        "observed_at": observed,
        "observed_day": day,
        "external_id": f"station-{longitude}/{latitude}",
        "temperature_c": temperature,
        "relative_humidity_pct": 40.0,
        "wind_speed_ms": 2.0,
        "wind_direction_deg": None,
        "precipitation_mm": 0.0,
        "source": "fixture",
        "feature_id": None,
        "ingested_at": observed,
    }


async def test_a_station_lane_answers_with_its_nearest_station_in_one_query_on_the_selected_day_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_expanding_search(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a station lane needs no expanding nearest-cell boxes")

    monkeypatch.setattr(SelectionReader, "_nearest_cell", no_expanding_search)
    source = LocalWarehouse()
    before = DAY - timedelta(days=1)
    for day in (before, DAY):
        source.write(
            tmp_path,
            "weather-observations",
            day,
            [
                weather_station_row(day, longitude=-116.3, latitude=43.49, temperature=21.0),  # ~15 km east
                weather_station_row(day, longitude=-114.0, latitude=43.49, temperature=30.0),  # ~200 km east
            ],
        )
    result = await read_surface(source, "weather-observations", range_start=before.isoformat())
    lane = result["lanes"][0]
    selected = lane["selected"]
    assert (selected["spatial_relation"], selected["distance_km_basis"]) == ("nearest_cell", "source_coordinate")
    assert selected["distance_km"] == pytest.approx(15.3, abs=0.2)
    assert selected["features"][0]["properties"]["temperature_c"] == 21.0
    # History days are tile-only samples: no station lies in the zoom-13 tile, and none is searched for.
    history = {entry["requested_day"]: entry for entry in lane["history"]}
    assert history[before.isoformat()]["features"] == []
    assert lane["lane_day_reads"] == 2


async def test_a_point_outside_every_drought_area_is_not_given_the_nearest_area_as_its_value(tmp_path: Path) -> None:
    source = LocalWarehouse()
    write_lane(source, tmp_path, "drought-areas", DAY)  # one D1 square, [-117,-116] x [43,44]
    selected = (await read_surface(source, "drought-areas", longitude=-118.0, latitude=43.5))["lanes"][0]["selected"]
    assert selected["state"] == "published"
    # Outside every polygon IS the answer; the nearest area and its centroid distance are context.
    assert (selected["spatial_relation"], selected["distance_km_basis"]) == (
        "nearest_area_outside",
        "geometry_centroid",
    )
    assert selected["distance_km"] == pytest.approx(121.0, abs=1.0)  # to the square's centroid (-116.5, 43.5)
    nearest = selected["features"][0]
    assert (nearest["spatial_relation"], nearest["covers_probe_point"]) == ("nearest_area_outside", False)


def crop_cell_row(release: date, *, west: float, south: float, crop_fraction: float) -> dict[str, Any]:
    """One equal-area crop-cover grid cell (a 0.1-degree square stands in for the 30 km cell)."""
    return {
        "feature_id": f"crop-{west}/{south}",
        "observed_year": release.year,
        "release_day": release,
        "source": "fixture",
        "source_url": "https://example.org/cdl",
        "source_resolution_m": 30.0,
        "analysis_resolution_m": 30.0,
        "aggregation_cell_m": 10_000,
        "grid_x": 0,
        "grid_y": 0,
        "estimation_method": "fixture",
        "dominant_crop_code": 1,
        "dominant_crop_name": "Corn",
        "crop_fraction": crop_fraction,
        "classified_fraction": 1.0,
        "crop_area_ha": 100.0,
        "cell_area_ha": 1000.0,
        "class_areas_json": "{}",
        "class_names_json": "{}",
        "geometry_wkb": _wkb_square(west, south, west + 0.1, south + 0.1),
        "source_sha256": "0" * 64,
        "ingested_at": datetime.combine(release, datetime.min.time(), UTC),
    }


async def test_a_tiling_polygon_lane_uses_its_nearest_cell_as_the_value(tmp_path: Path) -> None:
    """Regression: crop-cover is GeometrySupport-backed but a WALL-TO-WALL grid, not a sparse area."""
    source = LocalWarehouse()
    release = date(2026, 2, 27)  # a registered CDL release day; the as-of rule serves it for DAY
    source.write(
        tmp_path, "crop-cover", release, [crop_cell_row(release, west=-116.3, south=43.45, crop_fraction=0.42)]
    )
    selected = (await read_surface(source, "crop-cover"))["lanes"][0]["selected"]  # BOISE, west of the cell
    assert (selected["state"], selected["served_day"]) == ("published", release.isoformat())
    assert (selected["spatial_relation"], selected["distance_km_basis"]) == ("nearest_cell", "geometry_centroid")
    assert selected["distance_km"] == pytest.approx(19.5, abs=0.5)  # to the cell centroid (-116.25, 43.5)
    nearest = selected["features"][0]
    assert nearest["spatial_relation"] == "nearest_cell"
    assert nearest["properties"]["crop_fraction"] == 0.42


def test_the_web_sparse_area_surfaces_mirror_the_agri_lanes() -> None:
    """The web words `nearest_area_outside` per surface; it must name exactly the sparse-area surfaces."""
    block = re.search(
        r"export const REGIONAL_SPARSE_AREA_NOUNS[^=]*= \{(.*?)\};", WEB_WORKFLOW.read_text(encoding="utf-8"), re.DOTALL
    )
    assert block, "REGIONAL_SPARSE_AREA_NOUNS moved or was renamed in regional-analysis-workflow.ts"
    web = set(re.findall(r"'([a-z0-9-]+)':", block.group(1)))
    agri = {surface for surface, lanes in SURFACE_PARQUET_LANES.items() if set(lanes) <= SPARSE_AREA_LANES}
    assert web == agri
    assert "crop-cover" not in web


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
    # The written lane substituted its unwritten selected day, and that read is inside the budget.
    assert [entry["selected"]["state"] for entry in result["lanes"]].count("published_nearest") == 1
    assert all(entry["lane_day_reads"] <= 2 + RESERVED_READS_PER_LANE for entry in result["lanes"])
    assert history["lane_day_reads"] == sum(entry["lane_day_reads"] for entry in result["lanes"])
    assert history["lane_day_reads"] <= MAX_LANE_DAY_READS


@pytest.mark.parametrize(
    ("kind", "last_scheduled", "future_is_nearest"), [("observed", 0, False), ("forecast", 5, True)]
)
def test_only_a_forecast_read_schedules_or_borrows_days_after_today(
    kind: Literal["observed", "forecast"], last_scheduled: int, *, future_is_nearest: bool
) -> None:
    """An observation dated after today does not exist; a forecast's future days are its data."""
    ceiling = schedule_ceiling(kind, DAY)
    first, last = DAY - timedelta(days=2), DAY + timedelta(days=5)
    selection = Selection.parse(
        **BOISE,
        zoom=13,
        day=DAY.isoformat(),
        range_start=first.isoformat(),
        range_end=last.isoformat(),
        time_scale="day",
        page_start=0,
    )
    expected = DAY + timedelta(days=last_scheduled)
    assert max(selection.page(16, today=ceiling)) == expected
    assert max(evidence_days(first, last, DAY, set(), today=ceiling)) == expected
    nearest = nearest_published_day({DAY + timedelta(days=2)}, DAY, tolerance_days=3, today=ceiling)
    assert (nearest is not None) is future_is_nearest


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
    assert selected["proven_nearest"] is True
    assert len(selected["features"]) == 1


async def test_soil_survey_keeps_a_delineation_found_beyond_the_proof_circle_as_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admit_release(tmp_path, monkeypatch)
    # Diagonal from the (2, 2) corner: ~11 km, inside the last 0.08-degree box but outside its
    # ~8.9 km inscribed circle, so a nearer delineation beyond the box cannot be ruled out.
    selected = (await read_surface(LocalWarehouse(), "soil-survey", longitude=2.07, latitude=2.07))["lanes"][0][
        "selected"
    ]
    assert selected["nearest_search"] == "search_bound_reached"
    assert len(selected["features"]) == 1
    assert selected["spatial_relation"] == "nearest_cell"
    assert selected["distance_km"] == pytest.approx(11.0, abs=0.2)
    assert selected["proven_nearest"] is False


# --- Decision 6: the history tools are published, and cost Gemini no enum-array states ---------


def _enum_arrays(schema: object) -> list[str]:
    """Every array-of-enum property: the Gemini forced-call "too many states" trigger."""
    if isinstance(schema, dict):
        found = ["array-of-enum"] if schema.get("type") == "array" and "enum" in (schema.get("items") or {}) else []
        return found + [hit for value in schema.values() for hit in _enum_arrays(value)]
    if isinstance(schema, list):
        return [hit for value in schema for hit in _enum_arrays(value)]
    return []


def test_the_web_current_catalogue_fixture_is_the_published_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The web measures Gemini's schema budget against this fixture, so it must be what production serves.

    Production runs with the soil flag on. Regenerate after a catalogue change with
    `SOIL_PROPERTIES_READS_ENABLED=true uv run python -c "import json; from agri_data_service.routes.agent_tools
    import environmental_tool_schemas as s; print(json.dumps(s(), indent=2, ensure_ascii=False))"`.
    """
    monkeypatch.setenv(READS_ENABLED_VARIABLE, "true")
    published = json.loads(json.dumps(route.environmental_tool_schemas()))
    assert json.loads(WEB_CURRENT_CATALOGUE.read_text(encoding="utf-8")) == published


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
