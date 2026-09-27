"""Prepare (nearest sampling at cell centres, row rule, sort) and publish (parts, archive, idempotence).

Rasters are small EPSG:4326 GeoTIFFs in `tmp_path`; the store and storage are recording fakes. Rationale: the
lane's AGENTS.md, "Prepare" and "Publish".
"""

from __future__ import annotations

import math
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pytest
from rasterio.transform import Affine  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.direct.soil_properties import prepare, publish
from agri_data_service.pipeline.direct.soil_properties.capture import CAPTURE_MANIFEST_NAME
from agri_data_service.pipeline.direct.soil_properties.products import LATTICE_DEGREES, RELEASE_DAY
from agri_data_service.pipeline.direct.soil_properties.source import SoilPropertiesPipelineError, sha256_hex
from agri_data_service.pipeline.parquet.objectstore import ParquetWriteReceipt, PartitionNotWrittenError
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.schemas.soil_properties import SOIL_PROPERTIES_STREAM, SOIL_PROPERTIES_VALUE_COLUMNS
from tests.direct.soil_properties.lane_fixtures import MANIFEST_SHA256, NODATA, lane_table, write_raster

if TYPE_CHECKING:
    from pathlib import Path

    import pyarrow as pa  # type: ignore[import-untyped]

SOURCE_WEST: Final = -117.01
SOURCE_NORTH: Final = 46.76
SOURCE_PIXEL: Final = 0.003
SOURCE_SHAPE: Final = (20, 20)
LATTICE_WEST_SUBSET: Final = -117.0
LATTICE_NORTH_SUBSET: Final = 46.75
SUBSET_SIDE: Final = 4
ROWS_PER_PART: Final = 10
TABLE_ROWS: Final = 25
EXPECTED_PART_SIZES: Final = [10, 10, 5]


# --- Prepare ---------------------------------------------------------------------------------------


def test_lattice_origins_are_exact_thousandths() -> None:
    longitudes, latitudes = prepare.lattice_origins(np.array([1399, 0]), np.array([0, 1]))
    assert longitudes.tolist() == [-125.0, -124.995]
    assert latitudes.tolist() == [42.0, 48.995]


def test_the_lattice_transform_keys_each_pixel_by_its_sw_origin() -> None:
    column, row = 1, 1399
    west, north = prepare.lattice_transform() * (column, row)
    assert (round(west, 9), round(north - LATTICE_DEGREES, 9)) == (-124.995, 42.0)


def test_each_cell_takes_the_native_pixel_containing_its_centre(tmp_path: Path) -> None:
    """Nearest sampling at the CENTRE: the stored value is exactly one source pixel, never an average."""
    source_values = np.arange(SOURCE_SHAPE[0] * SOURCE_SHAPE[1], dtype=np.int16).reshape(SOURCE_SHAPE)
    source_values[5, 5] = NODATA
    raster = write_raster(
        tmp_path / "source.tif", source_values, west=SOURCE_WEST, north=SOURCE_NORTH, pixel=SOURCE_PIXEL
    )
    subset = Affine(LATTICE_DEGREES, 0.0, LATTICE_WEST_SUBSET, 0.0, -LATTICE_DEGREES, LATTICE_NORTH_SUBSET)
    sampled = prepare.sample_nearest(raster, destination_transform=subset, width=SUBSET_SIDE, height=SUBSET_SIDE)
    for row in range(SUBSET_SIDE):
        for column in range(SUBSET_SIDE):
            centre_longitude = LATTICE_WEST_SUBSET + (column + 0.5) * LATTICE_DEGREES
            centre_latitude = LATTICE_NORTH_SUBSET - (row + 0.5) * LATTICE_DEGREES
            source_row = math.floor((SOURCE_NORTH - centre_latitude) / SOURCE_PIXEL)
            source_column = math.floor((centre_longitude - SOURCE_WEST) / SOURCE_PIXEL)
            expected = source_values[source_row, source_column]
            want = prepare.MISSING_VALUE if expected == NODATA else expected
            assert sampled[row, column] == want, (row, column)


def test_the_row_rule_drops_a_cell_missing_any_value_and_sorts_latitude_major() -> None:
    shape = (2, 3)
    sampled = {column: np.full(shape, 57, dtype=np.int32) for column in SOIL_PROPERTIES_VALUE_COLUMNS}
    sampled["cfvo_15_30cm"][0, 1] = prepare.MISSING_VALUE
    table = prepare.assemble_table(sampled, manifest_sha256=MANIFEST_SHA256)
    assert table.schema == observed_stream_schema(SOIL_PROPERTIES_STREAM).arrow_schema
    latitudes = table.column("cell_latitude").to_pylist()
    origins = list(zip(latitudes, table.column("cell_longitude").to_pylist(), strict=True))
    assert origins == sorted(origins)
    assert (48.995, -124.995) not in origins, "the cell missing one value has no row at all"
    assert len(origins) == shape[0] * shape[1] - 1
    assert set(table.column("phh2o_0_5cm").to_pylist()) == {57.0}
    assert set(table.column("source_manifest_sha256").to_pylist()) == {MANIFEST_SHA256}
    assert set(table.column("release_day").to_pylist()) == {RELEASE_DAY}


def test_the_probe_reports_the_nearest_valid_centre() -> None:
    valid = np.zeros((1400, 2800), dtype=bool)
    longitude, latitude = -116.2, 43.6
    column = math.floor((longitude + 125.0) / LATTICE_DEGREES)
    row = math.floor((49.0 - latitude) / LATTICE_DEGREES)
    assert prepare.probe_point(valid, longitude, latitude) == {"cell_within_1000_m": False, "nearest_centre_m": None}
    valid[row, column] = True
    probed = prepare.probe_point(valid, longitude, latitude)
    assert probed["cell_within_1000_m"] is True
    assert probed["nearest_centre_m"] < prepare.PROBE_RADIUS_METERS


# --- Publish -------------------------------------------------------------------------------------------


def _table(rows: int = TABLE_ROWS, **kwargs: Any) -> pa.Table:
    cells = [(-117.0 + LATTICE_DEGREES * index, 46.7) for index in range(rows)]
    return lane_table(cells, **kwargs)


def test_parts_are_contiguous_slices_of_the_sorted_table() -> None:
    table = _table()
    parts = publish.split_parts(table, ROWS_PER_PART)
    assert [part.num_rows for part in parts] == EXPECTED_PART_SIZES
    rejoined = [value for part in parts for value in part.column("cell_longitude").to_pylist()]
    assert rejoined == table.column("cell_longitude").to_pylist()


@pytest.mark.parametrize("rows_per_part", [None, 5, 10_000_000])
def test_rows_per_part_is_required_and_bounded(rows_per_part: int | None) -> None:
    with pytest.raises(SoilPropertiesPipelineError, match="--rows-per-part"):
        publish.validate_rows_per_part(rows_per_part)


def test_a_prepared_table_is_checked_against_its_capture() -> None:
    publish.validate_prepared_table(_table(), MANIFEST_SHA256)
    with pytest.raises(SoilPropertiesPipelineError, match="different capture manifest"):
        publish.validate_prepared_table(_table(), "b" * 64)
    fractional = _table(overrides={"soc_5_15cm": [243.5] * TABLE_ROWS})
    with pytest.raises(SoilPropertiesPipelineError, match="non-integral"):
        publish.validate_prepared_table(fractional, MANIFEST_SHA256)


class _RecordingStore:
    """Records part writes and answers completion markers from a scripted map."""

    def __init__(self, markers: dict[int, Any] | None = None, base: pa.Table | None = None) -> None:
        self.writes: list[tuple[int, int]] = []
        self.markers = markers or {}
        self.base = base
        self.part_reads: list[bool] = []

    def write_partition(self, table: pa.Table, **coordinates: Any) -> ParquetWriteReceipt:
        index = coordinates["part_index"]
        self.writes.append((index, table.num_rows))
        return ParquetWriteReceipt(
            key=f"part-{index}",
            relative_path=f"part-{index}",
            stream=coordinates["layer"],
            kind=coordinates["kind"],
            zoom=coordinates["zoom"],
            day=coordinates["day"],
            row_count=table.num_rows,
            byte_count=1,
            sha256="0" * 64,
        )

    def read_completion_marker(self, _layer: str, _kind: str, tier: int, _day: date) -> Any:
        return self.markers.get(tier)

    def read_partition_with_receipts(
        self, _layer: str, _kind: str, _zoom: int, _day: date, *, part_selector: Any = None
    ) -> Any:
        first = partition_path(SOIL_PROPERTIES_STREAM, "observed", 13, RELEASE_DAY, 0)
        second = partition_path(SOIL_PROPERTIES_STREAM, "observed", 13, RELEASE_DAY, 1)
        self.part_reads.append(part_selector is not None and part_selector(first) and not part_selector(second))
        if self.base is None:
            raise PartitionNotWrittenError("no parts")
        return SimpleNamespace(table=self.base)


class _Session:
    async def rollback(self) -> None:
        return None


async def test_the_adapter_writes_numbered_parts_on_the_release_day_only() -> None:
    store = _RecordingStore()
    adapter = publish.SoilPropertiesAdapter(_table(), ROWS_PER_PART)
    result = await adapter(_Session(), store, day=RELEASE_DAY, run_id="soil-properties:test")  # type: ignore[arg-type]
    assert store.writes == list(enumerate(EXPECTED_PART_SIZES))
    assert result.row_count == TABLE_ROWS
    with pytest.raises(SoilPropertiesPipelineError, match="its own version day"):
        await adapter(_Session(), store, day=date(2026, 9, 27), run_id="x")  # type: ignore[arg-type]


def test_a_ladder_marked_with_these_counts_is_already_published() -> None:
    parts = len(EXPECTED_PART_SIZES)
    base = SimpleNamespace(row_count=TABLE_ROWS, part_count=parts)
    ladder = {0: object(), 5: object(), 9: object(), 13: base}
    complete = _RecordingStore(ladder, base=_table())
    assert publish.already_published(complete, rows=TABLE_ROWS, parts=parts, manifest_sha256=MANIFEST_SHA256)  # type: ignore[arg-type]
    missing_coarse = _RecordingStore({5: object(), 9: object(), 13: base}, base=_table())
    assert not publish.already_published(  # type: ignore[arg-type]
        missing_coarse, rows=TABLE_ROWS, parts=parts, manifest_sha256=MANIFEST_SHA256
    )
    other_counts = _RecordingStore(ladder, base=_table())
    assert not publish.already_published(  # type: ignore[arg-type]
        other_counts, rows=TABLE_ROWS + 1, parts=parts, manifest_sha256=MANIFEST_SHA256
    )
    assert other_counts.part_reads == [], "the base part is read only once the counts already match"


def test_a_different_capture_with_equal_counts_is_not_a_no_op() -> None:
    """Review m2: the manifest hash is part of the idempotency key, so a re-capture republishes."""
    parts = len(EXPECTED_PART_SIZES)
    ladder = {0: object(), 5: object(), 9: object(), 13: SimpleNamespace(row_count=TABLE_ROWS, part_count=parts)}
    store = _RecordingStore(ladder, base=_table())
    assert not publish.already_published(store, rows=TABLE_ROWS, parts=parts, manifest_sha256="b" * 64)  # type: ignore[arg-type]
    assert store.part_reads == [True], "only the first base part is read"
    assert publish.published_manifest_sha256(_RecordingStore(ladder)) is None  # type: ignore[arg-type]


class _RecordingStorage:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        del payload, content_type
        self.keys.append(key)


def test_the_archive_writes_every_window_and_the_manifest_last(tmp_path: Path) -> None:
    files = []
    for index in range(2):
        path = tmp_path / f"window-{index}.tif"
        path.write_bytes(f"bytes-{index}".encode())
        files.append({"path": path.name, "sha256": sha256_hex(path.read_bytes())})
    (tmp_path / CAPTURE_MANIFEST_NAME).write_text("{}", encoding="utf-8")
    storage = _RecordingStorage()
    manifest = {"manifest_sha256": MANIFEST_SHA256, "files": files}
    key = publish.archive_capture(storage, tmp_path, manifest)  # type: ignore[arg-type]
    prefix = publish.archive_prefix(MANIFEST_SHA256)
    assert storage.keys == [f"{prefix}/window-0.tif", f"{prefix}/window-1.tif", f"{prefix}/{CAPTURE_MANIFEST_NAME}"]
    assert key == storage.keys[-1]
    assert prefix.startswith("layer=soil-properties/kind=observed/availability/source-captures/")


async def test_publish_refuses_without_the_measured_part_size(tmp_path: Path) -> None:
    options = SimpleNamespace(capture_dir=tmp_path, rows_per_part=None, force=False, run_id=None)
    with pytest.raises(SoilPropertiesPipelineError, match="P1b"):
        await publish.publish_release(options)  # type: ignore[arg-type]
