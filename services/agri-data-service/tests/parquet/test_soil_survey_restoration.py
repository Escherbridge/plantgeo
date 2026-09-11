"""Restoration retains native soil attributes and requires a complete generalized ladder."""

from __future__ import annotations

import hashlib
import json
import struct
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.soil_survey_restore.prepare import prepare_soil_survey
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

DAY = date(2026, 8, 6)
NATIVE_ROWS = 2


@pytest.fixture
def spatial() -> Iterator[duckdb.DuckDBPyConnection]:
    with duckdb.connect(
        config={"autoinstall_known_extensions": False, "autoload_known_extensions": False}
    ) as connection:
        try:
            connection.execute("LOAD spatial")
        except duckdb.Error:
            pytest.skip("soil restoration geometry proof needs the installed local DuckDB spatial extension")
        yield connection


def _polygon() -> bytes:
    points = [
        (-120.0, 49.0),
        (-119.9, 49.01),
        (-119.8, 49.0),
        (-119.0, 49.0),
        (-119.0, 50.0),
        (-120.0, 50.0),
        (-120.0, 49.0),
    ]
    return struct.pack("<BIII", 1, 3, 1, len(points)) + b"".join(struct.pack("<dd", *point) for point in points)


def _source(tmp_path: Path, *, duplicate: bool = False, empty_geometry: bool = False) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    rows = [
        {
            "natural_key": f"usda-sda:{index}",
            "mupolygonkey": str(index),
            "mukey": "shared-map-unit",
            "map_unit_name": "fixture soil",
            "soil_series": None,
            "drainage_class": "well drained",
            "hydric_rating": hydric,
            "land_capability_class": "3e",
            "survey_area_symbol": "ID001",
            "survey_area_vintage": datetime(2025, 8, 26, tzinfo=UTC),
            "geometry_id": f"preserved-{index}",
            "last_confirmed_at": datetime(2026, 8, 6, tzinfo=UTC),
            "release_day": DAY,
            "geometry_wkb": struct.pack("<BII", 1, 3, 0) if empty_geometry else _polygon(),
            "producer": "usda-sda",
        }
        for index, hydric in enumerate((False, None))
    ]
    if duplicate:
        rows[1]["mupolygonkey"] = rows[0]["mupolygonkey"]
    pq.write_table(pa.Table.from_pylist(rows, schema=SOIL_SURVEY_SCHEMA.arrow_schema), source / "native.parquet")
    part = (source / "native.parquet").read_bytes()
    preservation = b'{"scope":"synthetic preservation test only"}'
    (source / "preservation-receipt.json").write_bytes(preservation)
    manifest = {
        "release_day": DAY.isoformat(),
        "preserved_at": "2026-09-10T00:00:00Z",
        "population_scope": "two synthetic delineations; not a regional source claim",
        "preservation_receipt_sha256": hashlib.sha256(preservation).hexdigest(),
        "preserved_population_complete": True,
        "parts": [
            {
                "path": "native.parquet",
                "sha256": hashlib.sha256(part).hexdigest(),
                "byte_count": len(part),
                "row_count": NATIVE_ROWS,
            }
        ],
    }
    payload = json.dumps(manifest).encode()
    (source / "manifest.json").write_bytes(payload)
    return source, hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "timezone_setting",
    ["SET TimeZone = 'UTC'", "SET TimeZone = 'America/Denver'"],
    ids=["utc", "denver"],
)
def test_restoration_preserves_native_grain_and_hydric_unknown_at_every_rung(
    tmp_path: Path, spatial: duckdb.DuckDBPyConnection, timezone_setting: str
) -> None:
    spatial.execute(timezone_setting)
    source, identity = _source(tmp_path)
    original = pq.ParquetFile(source / "native.parquet").read().sort_by([("mupolygonkey", "ascending")])
    output = tmp_path / "prepared"
    receipt = prepare_soil_survey(
        source_root=source, expected_manifest_sha256=identity, output=output, connection=spatial
    )
    assert receipt["required_rungs"] == [0, 5, 9, 13]
    assert receipt["native_rows"] == NATIVE_ROWS
    assert receipt["availability_published"] is False
    geometries: dict[int, list[object]] = {}
    for rung in (0, 5, 9, 13):
        table = pq.ParquetFile(output / f"zoom={rung:02}" / "part-0.parquet").read()
        assert table.schema == SOIL_SURVEY_SCHEMA.arrow_schema
        assert table.drop(["geometry_wkb"]).equals(original.drop(["geometry_wkb"]), check_metadata=True)
        assert table["mupolygonkey"].to_pylist() == ["0", "1"]
        assert table["mukey"].to_pylist() == ["shared-map-unit", "shared-map-unit"]
        assert table["hydric_rating"].to_pylist() == [False, None]
        assert table["release_day"].to_pylist() == [DAY, DAY]
        geometries[rung] = table["geometry_wkb"].to_pylist()
    assert geometries[13] == [_polygon(), _polygon()]
    assert geometries[0] != geometries[13]


@pytest.mark.parametrize(
    ("defect", "expected_error"),
    [
        ("duplicate", "duplicate native delineation"),
        ("empty_geometry", "valid nonempty WGS84 polygons"),
        ("hash", "preserved part identity disagrees"),
    ],
)
def test_refused_preservation_never_emits_a_candidate(
    tmp_path: Path, spatial: duckdb.DuckDBPyConnection, defect: str, expected_error: str
) -> None:
    source, identity = _source(tmp_path, duplicate=defect == "duplicate", empty_geometry=defect == "empty_geometry")
    if defect == "hash":
        (source / "native.parquet").write_bytes(b"altered")
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match=expected_error):
        prepare_soil_survey(source_root=source, expected_manifest_sha256=identity, output=output, connection=spatial)
    assert not (output / "candidate.json").exists()
