"""Saved numeric support, immutable identities and bounded soil admission refusals."""

from __future__ import annotations

import gzip
import importlib.util
import json
import struct
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from agri_data_service.pipeline.static_soil.models import (
    MAX_METADATA_BYTES,
    PROPERTIES,
    PROPERTY_DIVISORS,
    PROPERTY_UNITS,
    StaticSoilCandidate,
    canonical_bytes,
    digest,
)
from agri_data_service.pipeline.static_soil.point import open_verified_soil_bundle, soil_point
from agri_data_service.pipeline.static_soil.prepare import inspect_pmtiles, prepare_saved_soil

RAW_VALUE = 65
NODATA_VALUE = -32768
SECOND_PIXEL_VALUE = 100
SELECTED_DAY = date(2026, 9, 5)


def _cog(name: str, monkeypatch: pytest.MonkeyPatch) -> bytes:
    spec = importlib.util.find_spec("rasterio")
    assert spec is not None
    assert spec.origin is not None
    proj = str(Path(spec.origin).parent / "proj_data")
    monkeypatch.setenv("PROJ_DATA", proj)
    monkeypatch.setenv("PROJ_LIB", proj)
    rasterio = pytest.importorskip("rasterio")
    index = PROPERTIES.index(name)
    with rasterio.io.MemoryFile() as memory:
        with memory.open(
            driver="COG",
            width=2,
            height=2,
            count=1,
            dtype="int16",
            crs="EPSG:4326",
            transform=rasterio.transform.from_origin(-120.0, 50.0, 0.5, 0.5),
            nodata=NODATA_VALUE,
            blocksize=128,
        ) as output:
            output.write(np.array([[RAW_VALUE, NODATA_VALUE], [0, SECOND_PIXEL_VALUE]], dtype="int16"), 1)
            output.scales = (1.0 / PROPERTY_DIVISORS[index],)
            output.update_tags(
                property=name,
                scale_divisor=str(PROPERTY_DIVISORS[index]),
                unit=PROPERTY_UNITS[index],
                depth="0-5cm",
                statistic="mean",
                source="ISRIC SoilGrids",
                source_release="v2.0",
                license="CC-BY 4.0",
                source_url=f"https://files.isric.org/soilgrids/latest/data/{name}/",
            )
        payload: bytes = memory.read()
    return payload


def _pmtiles(metadata: object) -> bytes:
    encoded = gzip.compress(canonical_bytes(metadata))
    header = bytearray(127)
    header[:8] = b"PMTiles\x03"
    struct.pack_into("<QQ", header, 24, len(header), len(encoded))
    struct.pack_into("<Q", header, 72, 1)
    header[97] = 2
    header[99] = 2
    header[100:102] = bytes((0, 10))
    struct.pack_into("<iiii", header, 102, -1200000000, 490000000, -1190000000, 500000000)
    return bytes(header) + encoded


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "source"
    (source / "pmtiles").mkdir(parents=True)
    entries: list[dict[str, object]] = []
    receipts: list[dict[str, object]] = []
    for name, unit in zip(PROPERTIES, PROPERTY_UNITS, strict=True):
        cog = _cog(name, monkeypatch)
        filename = f"{name}_0-5cm_mean_4326.tif"
        (source / filename).write_bytes(cog)
        tiles = _pmtiles({"unit": unit, "colorRamp": [{"color": "#123456", "value": float(i)} for i in range(7)]})
        (source / "pmtiles" / f"{name}_0-5cm_mean.pmtiles").write_bytes(tiles)
        entries.append({"property": name, "file": filename, "checksumSha256": digest(cog), "sizeBytes": len(cog)})
        for form, payload in (("cog", cog), ("pmtiles", tiles)):
            receipts.append({"property": name, "format": form, "sha256": digest(payload), "bytes": len(payload)})
    manifest = canonical_bytes({"sourceRelease": "v2.0", "license": "CC-BY 4.0", "artifacts": entries})
    receipt = canonical_bytes(receipts)
    (source / "manifest.json").write_bytes(manifest)
    receipt_path = source / "receipt.json"
    receipt_path.write_bytes(receipt)
    output = tmp_path / "candidate"
    prepare_saved_soil(
        source_root=source,
        hash_receipt=receipt_path,
        expected_source_sha256=digest(manifest),
        expected_receipt_sha256=digest(receipt),
        output=output,
    )
    return output


def test_raw_values_scale_once_and_return_static_support(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = _prepare(tmp_path, monkeypatch)
    bundle = open_verified_soil_bundle(
        output, expected_manifest_sha256=digest((output / "candidate.json").read_bytes())
    )
    answer = soil_point(bundle, longitude=-119.75, latitude=49.75, selected_day=SELECTED_DAY)
    assert answer.selected_day == SELECTED_DAY
    assert answer.observed_day is None
    assert answer.temporal_distance_days is None
    assert answer.admission_state == "local_verified_candidate"
    for value, divisor, unit in zip(answer.values, PROPERTY_DIVISORS, PROPERTY_UNITS, strict=True):
        assert value.raw_value == RAW_VALUE
        assert value.value == RAW_VALUE / divisor
        assert value.unit == unit
        assert (value.row, value.column) == (0, 0)
        assert value.distance_m == 0.0
        assert value.source_sha256 == bundle.manifest.assets[PROPERTIES.index(value.property_name)].cog.sha256


def test_nodata_and_cell_edges_are_not_values_or_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = _prepare(tmp_path, monkeypatch)
    bundle = open_verified_soil_bundle(
        output, expected_manifest_sha256=digest((output / "candidate.json").read_bytes())
    )
    nodata = soil_point(bundle, longitude=-119.25, latitude=49.75, selected_day=SELECTED_DAY)
    assert all(
        value.status == "nodata" and value.value is None and value.raw_value == NODATA_VALUE for value in nodata.values
    )
    edge = soil_point(bundle, longitude=-119.5, latitude=49.5, selected_day=SELECTED_DAY)
    assert all((value.row, value.column, value.raw_value) == (1, 1, SECOND_PIXEL_VALUE) for value in edge.values)
    outside = soil_point(bundle, longitude=-119.0, latitude=49.0, selected_day=SELECTED_DAY)
    assert all(value.status == "out_of_extent" and value.row is None for value in outside.values)


def test_altered_middle_bytes_cannot_pass_a_complete_object_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _prepare(tmp_path, monkeypatch)
    candidate_bytes = (output / "candidate.json").read_bytes()
    candidate = StaticSoilCandidate.model_validate_json(candidate_bytes)
    path = output / "objects" / candidate.assets[0].cog.sha256
    changed = bytearray(path.read_bytes())
    changed[len(changed) // 2] ^= 1
    path.write_bytes(changed)
    with pytest.raises(ValueError, match="complete-object verification"):
        open_verified_soil_bundle(output, expected_manifest_sha256=digest(candidate_bytes))


def test_candidate_refuses_missing_property_or_claimed_remote_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _prepare(tmp_path, monkeypatch)
    candidate = json.loads((output / "candidate.json").read_bytes())
    candidate["remote_full_hash_verified"] = True
    with pytest.raises(ValueError, match="remote_full_hash_verified"):
        StaticSoilCandidate.model_validate_json(canonical_bytes(candidate))
    candidate["remote_full_hash_verified"] = False
    candidate["assets"].pop()
    with pytest.raises(ValueError, match="six ordered"):
        StaticSoilCandidate.model_validate_json(canonical_bytes(candidate))


def test_pmtiles_metadata_expansion_is_bounded() -> None:
    with pytest.raises(ValueError, match=r"decoded.*byte budget"):
        inspect_pmtiles(_pmtiles({"untrusted": "x" * MAX_METADATA_BYTES}))
