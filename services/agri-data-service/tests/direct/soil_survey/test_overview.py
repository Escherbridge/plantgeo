"""The below-z13 SSURGO overview: area-weighted majority per cell, and the operator verbs that build and publish it."""

from __future__ import annotations

import json
import struct
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.foundation.soil_survey.receipts import (
    AreaCensusEntry,
    AreaInventory,
    Blob,
    SoilSurveyError,
    digest,
    encoded,
)
from agri_data_service.foundation.soil_survey.release import Release, ShardRef
from agri_data_service.pipeline.direct.soil_survey import __main__ as soil_survey_main
from agri_data_service.pipeline.direct.soil_survey.overview import (
    OVERVIEW_CELL_DEGREES,
    OverviewAccumulator,
    decode_overview,
    encode_overview,
)
from agri_data_service.pipeline.direct.soil_survey.prepare import load_candidate_manifest, prepare_candidate
from tests.direct.soil_survey.fakes import AREA, POLYGONS, VINTAGE, completed

if TYPE_CHECKING:
    from pathlib import Path

Ring = list[tuple[float, float]]


def _polygon(*rings: Ring) -> bytes:
    """Little-endian WKB Polygon; the first ring is the exterior, the rest are holes."""
    body = struct.pack("<BII", 1, 3, len(rings))
    for ring in rings:
        body += struct.pack("<I", len(ring)) + b"".join(struct.pack("<dd", x, y) for x, y in ring)
    return body


def _box(west: float, south: float, east: float, north: float) -> Ring:
    return [(west, south), (east, south), (east, north), (west, north), (west, south)]


def _rows_by_cell(accumulator: OverviewAccumulator) -> dict[tuple[float, int, int], dict[str, object]]:
    return {(row["cell_degrees"], row["col"], row["row"]): row for row in accumulator.table().to_pylist()}


def test_each_cell_takes_the_class_holding_most_of_its_area_with_holes_left_to_their_island() -> None:
    accumulator = OverviewAccumulator()
    # Two neighbours west of Greenwich: A is 0.04 deg wide, B 0.01, both one 0.025 cell tall.
    accumulator.add(_polygon(_box(-117.0, 45.0, -116.96, 45.025)), "Well drained", False)
    accumulator.add(_polygon(_box(-116.96, 45.0, -116.95, 45.025)), "Poorly drained", True)
    # A donut whose hole is a different map unit: the hole's area must belong to the island alone.
    accumulator.add(
        _polygon(_box(-116.9, 45.0, -116.875, 45.025), _box(-116.895, 45.005, -116.88, 45.02)),
        "Somewhat poorly drained",
        None,
    )
    accumulator.add(_polygon(_box(-116.895, 45.005, -116.88, 45.02)), "Very poorly drained", None)
    accumulator.add(b"\x01\x01\x00\x00\x00" + struct.pack("<dd", -116.9, 45.0), "Well drained", None)

    cells = _rows_by_cell(accumulator)
    expected = {
        # (degrees, col, row): (class, dominant share, mapped share, hydric fraction, map units)
        (0.025, -4680, 1800): ("well-drained", 1.0, 1.0, 0.0, 1),
        (0.025, -4679, 1800): ("well-drained", 0.6, 1.0, 0.4, 2),
        (0.025, -4676, 1800): ("somewhat-poorly-drained", 0.64, 1.0, None, 2),
        (0.05, -2340, 900): ("well-drained", 0.8, 0.5, 0.2, 2),
        (0.2, -585, 225): ("well-drained", 160 / 300, 300 / 6400, 0.2, 4),
    }
    for key, (drainage, share, mapped, hydric, units) in expected.items():
        cell = cells[key]
        assert cell["drainage_class"] == drainage, key
        assert cell["dominant_share"] == pytest.approx(share), key
        assert cell["mapped_share"] == pytest.approx(mapped), key
        assert cell["hydric_fraction"] == (None if hydric is None else pytest.approx(hydric)), key
        assert cell["map_unit_count"] == units, key
    assert accumulator.undecodable == 1
    assert accumulator.polygons == len(expected) - 1


def test_an_overview_is_refused_for_any_release_but_the_one_it_was_derived_from() -> None:
    accumulator = OverviewAccumulator()
    accumulator.add(_polygon(_box(0.0, 0.0, 0.05, 0.05)), "Well drained", None)
    payload = encode_overview(accumulator.table(), release_sha256="a" * 64)
    assert decode_overview(payload, release_sha256="a" * 64).height == 4 + 1 + 1
    with pytest.raises(SoilSurveyError, match="different release"):
        decode_overview(payload, release_sha256="b" * 64)


SHARD: Final = "ID-1"


async def _local_release(root: Path) -> str:
    """Capture, prepare and index one shard locally, as the operator's root holds it after `release`."""
    await completed(root)
    candidate, candidate_sha = prepare_candidate(root, SHARD, [AREA])
    _, manifest = load_candidate_manifest(root, candidate_sha)
    shard = ShardRef.from_candidate(candidate, Blob(sha256=candidate_sha, byte_count=len(manifest)))
    release = Release(
        scope=AreaInventory(
            response=Blob(sha256=digest(b"{}"), byte_count=2),
            query_sha256=digest(b"census"),
            checked_at=candidate.captured_at,
            areas=(AreaCensusEntry(area=AREA, saverest=VINTAGE),),
            envelope=(-126.0, -1.0, 7.0, 50.0),
            region="pnw",
        ),
        shards=(shard,),
        pending_areas=(),
        source_evidence="capture_volume",
        release_day=shard.release_day,
        captured_at=shard.captured_at,
    )
    payload = encoded(release)
    release_sha = digest(payload)
    (root / f"release-{release_sha}.json").write_bytes(payload)
    return release_sha


async def test_overview_builds_from_the_local_release_and_publishing_needs_the_stage_switch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    release_sha = await _local_release(tmp_path)
    overview = ["overview", "--root", str(tmp_path), "--release", release_sha]

    soil_survey_main.main(overview)
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["outcome"] == "dry_run"
    assert not (tmp_path / f"overview-{release_sha}.parquet").exists()

    soil_survey_main.main([*overview, "--apply"])
    report = json.loads(capsys.readouterr().out)
    # The fixture's 2x2-degree square loses its 0.5-degree hole; its multipolygon is two 1x1 squares.
    assert report["polygons"] == len(POLYGONS)
    assert report["undecodable"] == 0
    assert report["cells_by_degrees"] == {"0.025": 6400 - 400 + 3200, "0.05": 1600 - 100 + 800, "0.2": 96 + 50}
    assert report["serving_published"] is False
    built = decode_overview((tmp_path / f"overview-{release_sha}.parquet").read_bytes(), release_sha256=release_sha)
    coarsest = built.filter(built["cell_degrees"] == OVERVIEW_CELL_DEGREES[-1])
    assert coarsest["map_unit_count"].sum() == 96 + 50

    monkeypatch.delenv("SSURGO_STAGE_ALLOWED", raising=False)
    monkeypatch.setattr(soil_survey_main, "_configured_bucket", lambda: "plantgeo-parquet")
    with pytest.raises(SoilSurveyError, match="SSURGO_STAGE_ALLOWED"):
        soil_survey_main.main(
            [
                "overview-publish",
                "--root",
                str(tmp_path),
                "--release",
                release_sha,
                "--bucket",
                "plantgeo-parquet",
                "--apply",
            ]
        )
