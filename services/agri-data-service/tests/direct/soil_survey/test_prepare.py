"""SSURGO shard preparation: native grain, Q4 repair-else-label, Morton chunks, shard caps and replay.

Two archive tests are adapted here (plan section 1c): the native-grain test now expects the frozen
`(13,)` ladder, and `test_invalid_native_geometry_cannot_form_a_candidate` became
`test_invalid_geometry_is_repaired_else_labelled_and_always_served` for owner Q4.
"""

from __future__ import annotations

import io
import json
import random
from datetime import date
from typing import TYPE_CHECKING, Final

import httpx
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError

from agri_data_service.foundation.soil_survey.receipts import SoilSurveyError, encoded
from agri_data_service.foundation.soil_survey.release import (
    MAX_PART_ROWS,
    MAX_PARTS_PER_VIEWPORT,
    MAX_SURVEY_AREAS,
    NATIVE_RUNG,
    REQUIRED_RUNGS,
    Candidate,
    union_bounds,
)
from agri_data_service.pipeline.direct.soil_survey.capture import capture_area
from agri_data_service.pipeline.direct.soil_survey.prepare import (
    PreparedRow,
    candidate_manifest_path,
    load_candidate_manifest,
    prepare_candidate,
    spatial_chunks,
    verify_candidate_replay,
)
from agri_data_service.warehouse.parquet.tiers import derivation_session
from tests.direct.soil_survey.fakes import AREA, POLYGONS, Source, completed

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.foundation.soil_survey.release import Bounds

SHARD: Final = "ID-1"
#: A zero-area spike: invalid, and `ST_MakeValid` collapses it to linework, never a polygon.
UNREPAIRABLE_SPIKE: Final = "POLYGON ((0 0, 2 0, 2 2, 2 0, 0 0))"
SPIKE_BOUNDS: Final = (0.0, 0.0, 2.0, 2.0)
#: Synthetic grid: ~16,000 delineations per sq deg, near the pilot's measured density.
GRID_SPACING_DEGREES: Final = 0.0079
AREA_CELLS: Final = 64
#: A z13 viewport at the TS reader ceiling (`MAX_SOIL_BBOX_SQUARE_DEGREES` = 0.02 sq deg).
VIEWPORT_WIDTH_DEGREES: Final = 0.2
VIEWPORT_HEIGHT_DEGREES: Final = 0.1
GRID_WEST: Final = -117.0
GRID_SOUTH: Final = 43.0


class SpikeSource(Source):
    """`Source` whose second delineation is `UNREPAIRABLE_SPIKE`."""

    def response(self, request: httpx.Request) -> httpx.Response:
        response = super().response(request)
        if "STAsText" not in json.loads(request.content)["query"] or response.status_code != httpx.codes.OK:
            return response
        header, *rows = json.loads(response.content)["Table"]
        geometry_column = header.index("geom")
        for values in rows:
            if values[0] == "2":
                values[geometry_column] = UNREPAIRABLE_SPIKE
        return httpx.Response(httpx.codes.OK, json={"Table": [header, *rows]})


async def _capture(root: Path, source: Source) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        capture = await capture_area(client, root, AREA, max_pages=2, page_size=1)
    assert capture.closing is not None


def _served_rows(root: Path, candidate: Candidate) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for part in candidate.parts:
        rows.extend(pq.read_table(io.BytesIO((root / "objects" / part.blob.sha256).read_bytes())).to_pylist())
    return rows


def _wkb_of(wkt: str) -> bytes:
    """`ST_AsWKB(ST_GeomFromText(wkt))`, the same round trip `ssurgo_native_geometry.sql` runs."""
    with derivation_session() as session:
        (wkb,) = session.execute("SELECT ST_AsWKB(ST_GeomFromText($1))", [wkt]).fetchone()
    return bytes(wkb)


def _synthetic_row(native_key: int, column: int, row: int) -> PreparedRow:
    """One grid cell whose box overhangs its neighbours by 0.3 cells, as jagged boundaries do."""
    west = GRID_WEST + (column - 0.3) * GRID_SPACING_DEGREES
    south = GRID_SOUTH + (row - 0.3) * GRID_SPACING_DEGREES
    bounds = (west, south, west + 1.6 * GRID_SPACING_DEGREES, south + 1.6 * GRID_SPACING_DEGREES)
    return PreparedRow(native_key=native_key, bounds=bounds, quality="valid", values={})


def _area_grid(first_key: int, column_offset: int, row_offset: int) -> list[PreparedRow]:
    return [
        _synthetic_row(first_key + column * AREA_CELLS + row, column_offset + column, row_offset + row)
        for column in range(AREA_CELLS)
        for row in range(AREA_CELLS)
    ]


def _viewport_at(column: int, row: int) -> Bounds:
    center_x = GRID_WEST + column * GRID_SPACING_DEGREES
    center_y = GRID_SOUTH + row * GRID_SPACING_DEGREES
    half_width, half_height = VIEWPORT_WIDTH_DEGREES / 2, VIEWPORT_HEIGHT_DEGREES / 2
    return (center_x - half_width, center_y - half_height, center_x + half_width, center_y + half_height)


def _intersects(box: Bounds, viewport: Bounds) -> bool:
    return box[0] <= viewport[2] and box[2] >= viewport[0] and box[1] <= viewport[3] and box[3] >= viewport[1]


async def test_candidate_preserves_native_grain_and_distinct_vintage_capture_clocks(tmp_path: Path) -> None:
    await completed(tmp_path)
    candidate, sha = prepare_candidate(tmp_path, SHARD, [AREA])
    assert candidate.release_day == date(2025, 8, 27)
    assert candidate.captured_at.date() > candidate.release_day
    assert candidate.required_rungs == REQUIRED_RUNGS == (NATIVE_RUNG,)
    assert sum(part.row_count for part in candidate.parts) == len(POLYGONS)
    assert candidate.quality[0].valid_rows == len(POLYGONS)
    assert candidate_manifest_path(tmp_path, sha).read_bytes() == encoded(candidate)
    corrupted = candidate.model_dump(mode="json")
    corrupted["parts"][0]["row_count"] = 1
    with pytest.raises(ValidationError, match="reconcile"):
        Candidate.model_validate(corrupted)


async def test_invalid_geometry_is_repaired_else_labelled_and_always_served(tmp_path: Path) -> None:
    repaired_root = tmp_path / "repaired"
    bowtie = Source()
    bowtie.invalid_geometry = True
    await _capture(repaired_root, bowtie)
    repaired, _ = prepare_candidate(repaired_root, SHARD, [AREA])
    (quality,) = repaired.quality
    assert (quality.valid_rows, quality.repaired_rows, quality.labelled_rows) == (0, len(POLYGONS), 0)
    assert {row["geometry_quality"] for row in _served_rows(repaired_root, repaired)} == {"repaired"}

    labelled_root = tmp_path / "labelled"
    await _capture(labelled_root, SpikeSource())
    labelled, _ = prepare_candidate(labelled_root, SHARD, [AREA])
    (quality,) = labelled.quality
    assert (quality.valid_rows, quality.repaired_rows, quality.labelled_rows) == (1, 0, 1)
    assert quality.total_rows == labelled.areas[0].opening.count
    assert sum(part.labelled_rows for part in labelled.parts) == 1
    served = {str(row["mupolygonkey"]): row for row in _served_rows(labelled_root, labelled)}
    assert set(served) == {"1", "2"}
    spike = served["2"]
    assert spike["geometry_quality"] == "invalid_unrepaired"
    assert (spike["bbox_west"], spike["bbox_south"], spike["bbox_east"], spike["bbox_north"]) == SPIKE_BOUNDS
    # The core "serve always" claim: a labelled row's bytes are the ORIGINAL geometry, not linework
    # from a failed repair. Without this, the test would still pass if `_classified` served the
    # repaired (non-polygonal) bytes under the invalid_unrepaired label.
    assert spike["geometry_wkb"] == _wkb_of(UNREPAIRABLE_SPIKE)
    assert served["1"]["geometry_quality"] == "valid"


def test_morton_chunking_is_deterministic_and_ignores_page_order() -> None:
    rows = _area_grid(1, 0, 0)
    shuffled = list(rows)
    random.Random(20260927).shuffle(shuffled)
    chunks = spatial_chunks(rows)
    assert spatial_chunks(shuffled) == chunks
    assert all(1 <= len(chunk) <= MAX_PART_ROWS for chunk in chunks)
    assert sorted(row.native_key for chunk in chunks for row in chunk) == sorted(row.native_key for row in rows)
    twins = spatial_chunks([_synthetic_row(9, 0, 0), _synthetic_row(3, 0, 0)])
    assert [row.native_key for row in twins[0]] == [3, 9]


def test_z13_viewport_touches_at_most_the_part_cap() -> None:
    """Three abutting areas (A | B below, C straddling above); boxes overlap at shared edges."""
    areas = {
        "A": _area_grid(1, 0, 0),
        "B": _area_grid(100_000, AREA_CELLS, 0),
        "C": _area_grid(200_000, AREA_CELLS // 2, AREA_CELLS),
    }
    part_boxes: list[Bounds] = []
    for rows in areas.values():
        chunks = spatial_chunks(rows)
        assert sum(len(chunk) for chunk in chunks) == len(rows)
        part_boxes.extend(union_bounds([row.bounds for row in chunk]) for chunk in chunks)
    interior = _viewport_at(AREA_CELLS // 2, AREA_CELLS // 2)
    boundary = _viewport_at(AREA_CELLS, AREA_CELLS // 2)
    three_area_corner = _viewport_at(AREA_CELLS, AREA_CELLS)
    for viewport in (interior, boundary, three_area_corner):
        touched = sum(_intersects(box, viewport) for box in part_boxes)
        assert 1 <= touched <= MAX_PARTS_PER_VIEWPORT


def test_shard_caps_refuse_too_many_areas_and_a_malformed_shard(tmp_path: Path) -> None:
    too_many = [f"ID{index:03d}" for index in range(MAX_SURVEY_AREAS + 1)]
    with pytest.raises(SoilSurveyError, match="unique survey areas"):
        prepare_candidate(tmp_path, SHARD, too_many)
    with pytest.raises(SoilSurveyError, match="shard id"):
        prepare_candidate(tmp_path, "id-1", [AREA])


async def test_verify_replay_passes_then_detects_a_changed_part(tmp_path: Path) -> None:
    await completed(tmp_path)
    candidate, sha = prepare_candidate(tmp_path, SHARD, [AREA])
    assert verify_candidate_replay(tmp_path, sha) == candidate
    part_path = tmp_path / "objects" / candidate.parts[0].blob.sha256
    payload = part_path.read_bytes()
    part_path.write_bytes(b"X" + payload[1:])
    with pytest.raises(SoilSurveyError, match="checksum"):
        verify_candidate_replay(tmp_path, sha)


async def test_a_manifest_whose_bytes_do_not_hash_to_its_name_is_refused(tmp_path: Path) -> None:
    await completed(tmp_path)
    _, sha = prepare_candidate(tmp_path, SHARD, [AREA])
    renamed = candidate_manifest_path(tmp_path, "0" * 64)
    renamed.write_bytes(candidate_manifest_path(tmp_path, sha).read_bytes())
    with pytest.raises(SoilSurveyError, match="hash"):
        load_candidate_manifest(tmp_path, "0" * 64)
