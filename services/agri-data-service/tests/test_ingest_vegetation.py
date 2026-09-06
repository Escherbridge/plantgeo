"""Sentinel-2 NDVI source adapter: fixed-grid identity, the scene-datetime observed_at rule, grid bounds.

The `geo.features` job this file also covered (`ingest/ndvi.py::run_vegetation_ingestion_job`) was
deleted whole 2026-09-06 with the `ingest-ndvi` verb and the `postgres-vegetation` lane.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agri_data_service.ingest.identity import MissingNativeKeyError
from agri_data_service.ingest.policy import parse_bbox
from agri_data_service.ingest.vegetation import (
    MIN_VALID_SUBSAMPLES,
    NDVI_CELL_RESOLUTION_METRES,
    NDVI_GRID_NAME,
    SENTINEL2_L2A_REFLECTANCE_OFFSET,
    SENTINEL2_NDVI_PRODUCER,
    VEGETATION_CHANNEL,
    SceneMetadataError,
    build_ndvi_identity,
    build_ndvi_write,
    ndvi_grid_cells,
    parse_scene,
    parse_scene_asset,
    parse_scene_timestamp,
)

# A north-up, square-pixel affine transform over a real Sentinel-2 UTM tile origin, shared by every
# fixture asset so only the fields under test need to vary.
BAND_TRANSFORM = [10.0, 0.0, 499_980.0, 0.0, -10.0, 5_900_040.0]
BAND_SHAPE = [10_980, 10_980]  # [height, width]


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS", "VEGETATION_LAYER_ID"):
        monkeypatch.delenv(variable, raising=False)


def _grid_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "cellKey": "45.1250:-122.6250",
        "gridName": NDVI_GRID_NAME,
        "centerLatitude": 45.125,
        "centerLongitude": -122.625,
        "west": -122.75,
        "south": 45.0,
        "east": -122.5,
        "north": 45.25,
        "ndvi": 0.48,
        "observedAt": "2026-07-13T19:22:05.261Z",
        "sceneId": "S2A_10TES_20260713_0_L2A",
        "cloudCover": 4.2,
        "sampleCount": MIN_VALID_SUBSAMPLES,
    }
    record.update(overrides)
    return record


def _asset(**overrides: object) -> dict[str, object]:
    asset: dict[str, object] = {
        "href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/10/T/ES/2026/7/S2A_10TES_20260713_0_L2A/B04.tif",
        "proj:transform": list(BAND_TRANSFORM),
        "proj:shape": list(BAND_SHAPE),
        "raster:bands": [{"scale": 0.0001, "offset": -0.1, "nodata": 0}],
    }
    asset.update(overrides)
    return asset


def _scene_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": "S2A_10TES_20260713_0_L2A",
        "bbox": [-123.0, 44.0, -122.0, 45.0],
        "properties": {"datetime": "2026-07-13T19:22:05.261Z", "eo:cloud_cover": 4.2, "proj:epsg": 32610},
        "assets": {"red": _asset(), "nir": _asset(), "scl": _asset()},
    }
    item.update(overrides)
    return item


def test_ndvi_identity_is_stable_across_two_runs_over_the_same_payload() -> None:
    record = _grid_record()
    first = build_ndvi_identity(record)
    second = build_ndvi_identity(record)
    assert first.natural_key == second.natural_key
    assert first.natural_key == f"{SENTINEL2_NDVI_PRODUCER}:45.1250:-122.6250:2026-07-13T19:22:05.261Z"


def test_observed_at_comes_from_the_scene_datetime_never_the_run_clock() -> None:
    # build_ndvi_identity takes no "now" parameter: the only source of a timestamp is the record itself.
    identity = build_ndvi_identity(_grid_record(observedAt="2024-07-13T19:22:05.261Z"))
    assert identity.observed_at == datetime(2024, 7, 13, 19, 22, 5, 261_000, tzinfo=UTC)


def test_a_record_missing_its_cell_key_raises_rather_than_being_synthesised() -> None:
    with pytest.raises(MissingNativeKeyError, match="cellKey"):
        build_ndvi_identity(_grid_record(cellKey=None))
    with pytest.raises(MissingNativeKeyError, match="cellKey"):
        build_ndvi_identity(_grid_record(cellKey="  "))


def test_a_record_missing_its_observed_at_raises_rather_than_being_synthesised() -> None:
    with pytest.raises(MissingNativeKeyError, match="observedAt"):
        build_ndvi_identity(_grid_record(observedAt=None))
    with pytest.raises(MissingNativeKeyError, match="observedAt"):
        build_ndvi_identity(_grid_record(observedAt=""))


def test_parse_scene_timestamp_accepts_a_zoned_instant_and_refuses_an_unzoned_one() -> None:
    assert parse_scene_timestamp("2026-07-13T19:22:05.261Z") == datetime(2026, 7, 13, 19, 22, 5, 261_000, tzinfo=UTC)
    with pytest.raises(SceneMetadataError, match="UTC offset"):
        parse_scene_timestamp("2026-07-13T19:22:05")


def test_the_grid_never_exceeds_the_record_limit_and_reports_truncation() -> None:
    bbox = "-125,42,-111,49"
    every_cell, not_truncated = ndvi_grid_cells(bbox, 100_000)
    assert not_truncated is False
    assert len(every_cell) > 1

    capped, truncated = ndvi_grid_cells(bbox, len(every_cell) - 1)
    assert truncated is True
    assert len(capped) == len(every_cell) - 1


def test_every_grid_cell_centre_falls_strictly_inside_the_requested_bbox() -> None:
    bbox = "-123.5,44.5,-122.0,45.5"
    cells, _truncated = ndvi_grid_cells(bbox, 1_000)
    west, south, east, north = parse_bbox(bbox)
    assert cells
    for cell in cells:
        assert west <= cell.center_longitude <= east
        assert south <= cell.center_latitude <= north


def test_a_declared_boa_offset_is_never_reapplied_to_already_harmonised_pixels() -> None:
    # The load-bearing correctness finding: sentinel-cogs pixels are already harmonised, so the -0.1
    # `raster:bands.offset` the STAC item declares must be refused, not applied a second time.
    asset = parse_scene_asset(_asset(), epsg=32610)
    assert asset.offset == SENTINEL2_L2A_REFLECTANCE_OFFSET == 0.0
    assert asset.scale == 0.0001


@pytest.mark.parametrize(
    "overrides",
    [
        {"href": "s3://not-https/band.tif"},
        {"proj:transform": [-10.0, 0.0, 499_980.0, 0.0, -10.0, 5_900_040.0]},  # not north-up
        {"proj:transform": [10.0, 0.0, 499_980.0, 0.0, 10.0, 5_900_040.0]},  # not north-up
        {"raster:bands": []},
        {"proj:shape": [10_980]},
    ],
)
def test_an_unusable_scene_asset_is_refused_rather_than_guessed_at(overrides: dict[str, object]) -> None:
    with pytest.raises(SceneMetadataError):
        parse_scene_asset(_asset(**overrides), epsg=32610)


def test_a_scene_parses_into_its_typed_footprint_and_acquisition_instant() -> None:
    scene = parse_scene(_scene_item())
    assert scene.item_id == "S2A_10TES_20260713_0_L2A"
    assert scene.observed_at == datetime(2026, 7, 13, 19, 22, 5, 261_000, tzinfo=UTC)
    assert scene.cloud_cover_percent == 4.2
    assert scene.covers(44.5, -122.5) is True
    assert scene.covers(50.0, -122.5) is False
    # Every band the parsed scene carries refuses the declared offset, matching parse_scene_asset alone.
    assert scene.red.offset == scene.near_infrared.offset == scene.scene_classification.offset == 0.0


def test_a_scene_item_missing_its_acquisition_datetime_is_refused() -> None:
    item = _scene_item()
    del item["properties"]["datetime"]  # type: ignore[index]
    with pytest.raises(SceneMetadataError):
        parse_scene(item)


@pytest.mark.parametrize(
    "overrides",
    [
        {"ndvi": 1.5},  # outside [-1, 1]
        {"ndvi": -1.5},
        {"sampleCount": MIN_VALID_SUBSAMPLES - 1},  # below the usable-lattice floor
    ],
)
def test_an_unmeasurable_or_out_of_range_sample_is_refused_rather_than_stored(overrides: dict[str, object]) -> None:
    assert build_ndvi_write(_grid_record(**overrides), "vegetation") is None


def test_a_measured_cell_writes_its_own_geometry_and_grid_identity() -> None:
    write = build_ndvi_write(_grid_record(), "vegetation")
    assert write is not None
    assert write.natural_key == f"{SENTINEL2_NDVI_PRODUCER}:45.1250:-122.6250:2026-07-13T19:22:05.261Z"
    assert write.channel == VEGETATION_CHANNEL
    assert write.properties["gridName"] == NDVI_GRID_NAME
    assert write.properties["resolutionMetres"] == NDVI_CELL_RESOLUTION_METRES
    assert write.grid_cell is not None
    assert write.grid_cell.grid_name == NDVI_GRID_NAME
    assert write.grid_cell.cell_key == "45.1250:-122.6250"
    assert write.grid_cell.resolution_metres == NDVI_CELL_RESOLUTION_METRES


# SEVEN TESTS STOOD HERE AND ARE DELETED WITH THEIR SUBJECT (2026-09-06). Six covered
# `ingest/ndvi.py::run_vegetation_ingestion_job` -- the bound-writer signature rule, the unset-bbox
# skip, the no-clear-scene skip, and the three `on_persisted` forward-publication orderings -- and the
# module was deleted whole with the `ingest-ndvi` verb and the `postgres-vegetation` lane. The seventh
# asserted `build_vegetation_source()`'s `IngestionSource` composition, which went with
# `ingest-backfill --source sentinel2-ndvi`.
#
# What replaced them: `pipeline/direct/vegetation/` writes Parquet from the SAME sampling code covered
# above (`collect_ndvi_grid_records`, `build_ndvi_identity`, `ndvi_grid_cells`), so the grid, identity
# and scene-timestamp contracts are still pinned here. The forward-publication ordering the three
# `on_persisted` tests protected is now `forward.py`'s own, covered by `tests/direct/`.
#
# STILL OWED, and recorded rather than silently dropped: `bind_vegetation_forward_writer`
# (`pipeline/parquet/vegetation_forward.py:882`) lost its only two call sites with those tests' subject
# and now has ZERO src callers. It is not deleted here because that module is outside this lane's
# ownership; it retires with `parquet-forward-vegetation` and `vegetation-catch-up` under the track's
# D-fills item.
