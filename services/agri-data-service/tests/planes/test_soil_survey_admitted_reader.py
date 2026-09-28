"""The admitted-release SSURGO reader (port slice S3): bbox pruning, caps, digests, and the SQL layer.

Three independent layers are covered, each at the narrowest scope that exercises it:

- The SQL layer (`run_admitted_soil_survey_query`) against a hand-built Arrow table and a real
  DuckDB connection (`open_guarded_connection`, spatial-extension-loaded) -- no object storage, no
  candidate model. This is where owner Q4's "never a GEOS predicate" claim is actually checked: an
  `invalid_unrepaired` row with a self-intersecting ring is proven to still come back.
- The gather layer (`gather_admitted_soil_survey_viewport`, `load_admitted_release`) against a REAL
  `Candidate` produced by `pipeline.direct.soil_survey.prepare.prepare_candidate` (the same fixture
  `tests/direct/soil_survey/test_prepare.py` uses), wrapped in a hand-built single-shard `Release`
  and an in-memory storage fake. No network and no real bucket.
- The render layer (`render_served_soil_survey`, `soil_survey_unavailable`), pure dict shape.
"""

from __future__ import annotations

import struct
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.soil_survey.receipts import (
    AreaCensusEntry,
    AreaInventory,
    Blob,
    SoilSurveyError,
    digest,
    encoded,
)
from agri_data_service.foundation.soil_survey.release import (
    NATIVE_RUNG,
    Release,
    ShardArea,
    ShardRef,
    manifest_key,
    release_key,
)
from agri_data_service.parquet_ops.duckdb_session import open_guarded_connection
from agri_data_service.pipeline.direct.soil_survey.prepare import load_candidate_manifest, prepare_candidate
from agri_data_service.pipeline.parquet.availability_storage import StoredAvailabilityObject
from agri_data_service.planes.soil_survey import (
    GatheredSoilSurveyViewport,
    SoilSurveyViewport,
    gather_admitted_soil_survey_viewport,
    load_admitted_release,
    render_served_soil_survey,
    run_admitted_soil_survey_query,
    soil_survey_unavailable,
)
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA
from tests.direct.soil_survey.fakes import AREA, VINTAGE, completed

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from duckdb import DuckDBPyConnection

    from agri_data_service.foundation.soil_survey.release import Candidate

SHARD: Final = "ID-1"
REGION_ENVELOPE: Final = (-126.0, 41.0, -110.0, 50.0)


def _wkb_polygon(ring: list[tuple[float, float]]) -> bytes:
    """A minimal little-endian WKB Polygon (no holes) -- structurally valid bytes regardless of
    whether `ring` is a simple (non-self-intersecting) shape."""
    body = struct.pack("<BII", 1, 3, 1) + struct.pack("<I", len(ring))
    for x, y in ring:
        body += struct.pack("<dd", x, y)
    return body


_VINTAGE_AT: Final = datetime.combine(date.fromisoformat(VINTAGE[:10]), datetime.min.time(), UTC)


def _row(
    key: str,
    *,
    bbox: tuple[float, float, float, float],
    ring: list[tuple[float, float]],
    quality: str,
) -> dict[str, object]:
    """One `SOIL_SURVEY_SCHEMA`-shaped row, typed exactly as `prepare.py::_row` builds a real one."""
    west, south, east, north = bbox
    return {
        "natural_key": key,
        "mupolygonkey": key,
        "mukey": f"mu-{key}",
        "map_unit_name": f"Test loam {key}",
        "soil_series": "Series",
        "drainage_class": "Well drained",
        "hydric_rating": None,
        "land_capability_class": "3",
        "survey_area_symbol": AREA,
        "survey_area_vintage": _VINTAGE_AT,
        "geometry_id": None,
        "last_confirmed_at": _VINTAGE_AT,
        "release_day": _VINTAGE_AT.date(),
        "geometry_wkb": _wkb_polygon(ring),
        "producer": "ssurgo-port",
        "bbox_west": west,
        "bbox_south": south,
        "bbox_east": east,
        "bbox_north": north,
        "geometry_quality": quality,
    }


# --- SQL layer: bbox-only selection never drops an invalid_unrepaired row ---------------------


@pytest.fixture
def connection() -> Iterator[DuckDBPyConnection]:
    """A real, spatial-extension-loaded DuckDB connection; closed on teardown."""
    conn = open_guarded_connection()
    try:
        yield conn
    finally:
        conn.close()


def _two_row_table() -> pa.Table:
    valid = _row("1", bbox=(0.0, 0.0, 1.0, 1.0), ring=[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)], quality="valid")
    # A self-intersecting bowtie: structurally valid WKB bytes, geometrically invalid. Q4: served
    # anyway, and selection must never depend on a GEOS predicate that would reject it.
    invalid = _row(
        "2", bbox=(0.0, 0.0, 1.0, 1.0), ring=[(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)], quality="invalid_unrepaired"
    )
    return pa.Table.from_pylist([valid, invalid], schema=SOIL_SURVEY_SCHEMA.arrow_schema)


def test_viewport_query_serves_the_invalid_unrepaired_row_too(connection: DuckDBPyConnection) -> None:
    table = _two_row_table()
    request = SoilSurveyViewport((0.0, 0.0, 1.0, 1.0), 13)
    rows = run_admitted_soil_survey_query(connection, table, request)
    assert {row["mupolygonkey"] for row in rows} == {"1", "2"}
    qualities = {row["mupolygonkey"]: row["geometry_quality"] for row in rows}
    assert qualities == {"1": "valid", "2": "invalid_unrepaired"}
    # Rendering still emits real coordinates for the invalid ring -- it is drawn, not dropped.
    assert all(row["geometry_json"] for row in rows)


def test_point_query_filters_the_bbox_prefilter_by_the_true_ring(connection: DuckDBPyConnection) -> None:
    """Review finding 2: bbox-only selection can serve a delineation whose ring does not actually
    contain the point. (0.3, 0.9) sits in both rows' declared (0,0,1,1) bbox -- a bbox-only answer
    would return both -- but it is interior to row "1"'s square and outside row "2"'s self-
    intersecting bowtie ring (its edges there are the lines y=x, x=1, x+y=1 and x=0; (0.3, 0.9) is
    off all four, so the ray-cast result is unambiguous). The two-stage read (`ssurgo_point.sql`
    bbox prefilter, then `_filter_point_candidates_by_ring`'s exact test) must narrow to row "1".
    """
    table = _two_row_table()
    request = SoilSurveyViewport((0.3, 0.9, 0.3, 0.9), 13, point=(0.3, 0.9))
    rows = run_admitted_soil_survey_query(connection, table, request)
    assert {row["mupolygonkey"] for row in rows} == {"1"}


def test_viewport_query_excludes_rows_whose_bbox_does_not_overlap(connection: DuckDBPyConnection) -> None:
    table = _two_row_table()
    request = SoilSurveyViewport((5.0, 5.0, 6.0, 6.0), 13)
    rows = run_admitted_soil_survey_query(connection, table, request)
    assert rows == []


def test_viewport_query_refuses_over_the_row_cap_instead_of_truncating(
    connection: DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 3, disposition F2: over `MAX_VIEWPORT_ROWS` is a clean 503 refusal, never a
    silently truncated, spatially arbitrary page."""
    monkeypatch.setattr("agri_data_service.planes.soil_survey.MAX_VIEWPORT_ROWS", 1)
    table = _two_row_table()
    request = SoilSurveyViewport((0.0, 0.0, 1.0, 1.0), 13)
    with pytest.raises(SoilSurveyError, match="row"):
        run_admitted_soil_survey_query(connection, table, request)


# --- Render layer: pure dict shape --------------------------------------------------------------


def test_zoom_in_and_not_admitted_are_both_unavailable_with_no_spatial_coverage() -> None:
    zoom_in = soil_survey_unavailable("soil_survey_zoom_in", requested_zoom=9)
    not_admitted = soil_survey_unavailable("soil_survey_release_not_admitted", requested_zoom=13)
    for envelope in (zoom_in, not_admitted):
        assert envelope["availability"] == "unavailable"
        assert envelope["features"] == []
        assert envelope["spatialCoverage"] is None
    assert zoom_in["reason"] == "soil_survey_zoom_in"
    assert not_admitted["reason"] == "soil_survey_release_not_admitted"


def test_empty_gather_still_renders_a_published_envelope() -> None:
    """A viewport no admitted shard's bbox reaches is a real, served EMPTY answer -- not `unavailable`."""
    gathered = GatheredSoilSurveyViewport(table=None, touched_shard_count=0, touched_areas=())
    checked_at = datetime.now(UTC)
    pending_area = "ID002"
    scope = AreaInventory(
        response=Blob(sha256=digest(b"{}"), byte_count=len(b"{}")),
        query_sha256=digest(b"census"),
        checked_at=checked_at,
        areas=(AreaCensusEntry(area=AREA, saverest=VINTAGE), AreaCensusEntry(area=pending_area, saverest=VINTAGE)),
        envelope=REGION_ENVELOPE,
        region="pnw",
    )
    shard = ShardRef(
        shard=SHARD,
        manifest=Blob(sha256=digest(b"manifest"), byte_count=len(b"manifest")),
        release_day=date.fromisoformat(VINTAGE[:10]),
        captured_at=checked_at,
        areas=(ShardArea(area=AREA, saverest=VINTAGE, native_rows=2, repaired_rows=0, labelled_rows=0),),
        bbox=(0.0, 0.0, 1.0, 1.0),
    )
    release = Release(
        scope=scope,
        shards=(shard,),
        pending_areas=(pending_area,),
        source_evidence="capture_volume",
        release_day=shard.release_day,
        captured_at=checked_at,
    )
    # A viewport nowhere near the one admitted shard's bbox.
    request = SoilSurveyViewport((80.0, 80.0, 81.0, 81.0), 13)
    result = render_served_soil_survey(
        [], release=release, request=request, touched_areas=gathered.touched_areas, admitted_sha256="a" * 64
    )
    assert result["availability"] == "published"
    assert result["features"] == []
    assert result["spatialCoverage"] == {"viewportAreas": [], "declaredAreaCount": 1, "pendingAreaCount": 1}


# --- Gather layer: bbox pruning, caps and digests over a REAL prepared candidate ----------------


class _FakeStorage:
    """An in-memory `AvailabilityStorage`, seeded with exactly the objects a test wants present."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.reads: list[str] = []

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        self.reads.append(key)
        payload = self.objects.get(key)
        if payload is None:
            return None
        assert len(payload) <= max_bytes
        return StoredAvailabilityObject(payload=payload, etag=digest(payload))


async def _one_shard_release(tmp_path: Path) -> tuple[Release, Candidate, _FakeStorage]:
    """Prepare one real shard from the shared capture fixture, then wrap it in a single-shard release."""
    await completed(tmp_path)
    candidate, candidate_sha = prepare_candidate(tmp_path, SHARD, [AREA])
    _, payload = load_candidate_manifest(tmp_path, candidate_sha)
    shard = ShardRef.from_candidate(candidate, Blob(sha256=candidate_sha, byte_count=len(payload)))
    scope = AreaInventory(
        response=Blob(sha256=digest(b"{}"), byte_count=len(b"{}")),
        query_sha256=digest(b"census"),
        checked_at=candidate.captured_at,
        areas=(AreaCensusEntry(area=AREA, saverest=VINTAGE),),
        envelope=REGION_ENVELOPE,
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
    objects = {manifest_key(candidate_sha): payload}
    for part in candidate.parts:
        objects[part.blob.key] = (tmp_path / "objects" / part.blob.sha256).read_bytes()
    release_payload = encoded(release)
    release_sha = digest(release_payload)
    objects[release_key(release_sha)] = release_payload
    storage = _FakeStorage(objects)
    storage.release_sha = release_sha  # type: ignore[attr-defined]  # convenience for callers
    return release, candidate, storage


async def test_load_admitted_release_verifies_the_digest(tmp_path: Path) -> None:
    release, _candidate, storage = await _one_shard_release(tmp_path)
    release_sha = storage.release_sha  # type: ignore[attr-defined]
    loaded = load_admitted_release(storage, release_sha)
    assert loaded.release_day == release.release_day
    with pytest.raises(SoilSurveyError, match="exact release SHA-256"):
        load_admitted_release(storage, "z" * 64)  # not hex: refused before any read
    tampered = _FakeStorage({**storage.objects, release_key(release_sha): b"{}"})
    with pytest.raises(SoilSurveyError, match="corrupt"):
        load_admitted_release(tampered, release_sha)


async def test_gather_prunes_by_bbox_and_finds_the_candidates_own_parts(tmp_path: Path) -> None:
    release, candidate, storage = await _one_shard_release(tmp_path)
    request = SoilSurveyViewport(release.shards[0].bbox, 13)
    gathered = gather_admitted_soil_survey_viewport(request, storage=storage, release=release)
    assert gathered.table is not None
    expected_rows = sum(part.row_count for part in candidate.parts if part.rung == NATIVE_RUNG)
    assert gathered.table.num_rows == expected_rows
    assert gathered.touched_areas == (AREA,)
    assert gathered.touched_shard_count == 1


async def test_gather_is_empty_but_not_an_error_far_from_every_shard(tmp_path: Path) -> None:
    release, _candidate, storage = await _one_shard_release(tmp_path)
    far_request = SoilSurveyViewport((70.0, 70.0, 71.0, 71.0), 13)
    gathered = gather_admitted_soil_survey_viewport(far_request, storage=storage, release=release)
    assert gathered.table is None
    assert gathered.touched_areas == ()
    # Nothing overlapped this shard, so its manifest was never even fetched.
    assert not any(key.startswith("soil-survey/candidates/manifests/") for key in storage.reads)


async def test_gather_refuses_a_tampered_part_before_returning_rows(tmp_path: Path) -> None:
    release, _candidate, storage = await _one_shard_release(tmp_path)
    # Flip one byte of every staged part object, length preserved, so `verify_blob`'s digest check
    # is what refuses this -- not the fake storage's own byte-count sanity assertion.
    for key in list(storage.objects):
        if key.startswith("soil-survey/candidates/objects/"):
            original = storage.objects[key]
            storage.objects[key] = original[:-1] + bytes([original[-1] ^ 0xFF])
    request = SoilSurveyViewport(release.shards[0].bbox, 13)
    with pytest.raises(SoilSurveyError, match="checksum"):
        gather_admitted_soil_survey_viewport(request, storage=storage, release=release)


async def test_gather_refuses_an_oversized_viewport_before_touching_any_shard(tmp_path: Path) -> None:
    """R12, review finding 4: over the z13 detail ceiling is refused before any manifest read."""
    release, _candidate, storage = await _one_shard_release(tmp_path)
    oversized_bbox = (-130.0, 20.0, -60.0, 55.0)  # 70 x 35 degrees, far over the 4 sq-degree ceiling
    request = SoilSurveyViewport(oversized_bbox, 13)
    with pytest.raises(SoilSurveyError, match="detail ceiling"):
        gather_admitted_soil_survey_viewport(request, storage=storage, release=release)
    assert storage.reads == []


async def test_gather_refuses_over_the_part_cap_before_fetching_any_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, _candidate, storage = await _one_shard_release(tmp_path)
    monkeypatch.setattr("agri_data_service.planes.soil_survey.MAX_PARTS_PER_VIEWPORT", 0)
    request = SoilSurveyViewport(release.shards[0].bbox, 13)
    reads_before = list(storage.reads)
    with pytest.raises(SoilSurveyError, match="budget"):
        gather_admitted_soil_survey_viewport(request, storage=storage, release=release)
    # Manifests may be read to know each part's declared bytes, but no PART object read happens
    # once the cap is already exceeded.
    assert all(not key.startswith("soil-survey/candidates/objects/") for key in storage.reads[len(reads_before) :])
