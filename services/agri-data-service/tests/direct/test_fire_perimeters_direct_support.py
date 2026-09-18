"""The DuckDB spatial repair chain `support.py` restates from `geo.sync_feature_geom_from_properties`.

NEEDS DuckDB's `spatial` extension loadable in the test environment: every test here opens
`fire_perimeter_geometry_session`.

THE GEOMETRIES ARE REAL. `fixtures/wfigs-current-pnw-subset-2026-09-15.geojson` holds five perimeters
byte-for-byte as WFIGS `_Current` served them on 2026-09-15 -- two valid, three that `ST_IsValid`
rejects -- because a synthetic bowtie says nothing about the 41-of-99 invalid rate the live feed
actually carries (this package's `AGENTS.md`, "Geometry repair"). The synthetic shapes that remain
are the cases the feed did not supply that day: a repair that yields a GEOMETRYCOLLECTION, and two
shapes no repair can make polygonal.
"""

# ruff: noqa: PLR2004 - the small literal counts and tolerances ARE the assertion; naming each one hides it.

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest

from agri_data_service.pipeline.direct.fire_perimeters.support import (
    FirePerimeterGeometryError,
    fire_perimeter_geometry_session,
    repair_perimeter_geometries_to_wkb,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.pipeline.direct.fire_perimeters.support import RepairedPerimeterGeometry

FIXTURE_PATH: Final = Path(__file__).resolve().parent / "fixtures" / "wfigs-current-pnw-subset-2026-09-15.geojson"

#: WKB geometry-type codes, little-endian: byte 0 is the endianness flag, bytes 1-4 the type.
WKB_POLYGON: Final = 3
WKB_MULTIPOLYGON: Final = 6

#: Each fixture perimeter by its `poly_SourceOID`, named for what the 2026-09-15 measurement said about it.
SUMMIT_LAKE_VALID_POLYGON: Final = "41985"
MINERS_VALID_MULTIPOLYGON: Final = "39677"
SKULL_INVALID_MULTIPOLYGON: Final = "47669"  # repairs with -7.56% planar area
WOLF_CREEK_INVALID_MULTIPOLYGON: Final = "44567"  # repairs with -2.58% planar area
EGYPT_INVALID_POLYGON: Final = "30043"  # repairs by re-noding one vertex, no measurable area change
FIXTURE_IDENTITIES: Final = (
    SUMMIT_LAKE_VALID_POLYGON,
    MINERS_VALID_MULTIPOLYGON,
    SKULL_INVALID_MULTIPOLYGON,
    WOLF_CREEK_INVALID_MULTIPOLYGON,
    EGYPT_INVALID_POLYGON,
)
REPAIRED_IDENTITIES: Final = frozenset(
    {SKULL_INVALID_MULTIPOLYGON, WOLF_CREEK_INVALID_MULTIPOLYGON, EGYPT_INVALID_POLYGON}
)

VALID_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
OTHER_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-100.0, 40.0], [-99.0, 40.0], [-99.0, 41.0], [-100.0, 41.0], [-100.0, 40.0]]],
}
#: A self-intersecting "bowtie": invalid, zero signed area, repairable into two triangles.
BOWTIE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]],
}
#: A unit square with a zero-width spike out of one corner. `ST_MakeValid` answers
#: GEOMETRYCOLLECTION(POLYGON, LINESTRING) -- the trigger's `GeometryType(repaired) = 'GEOMETRYCOLLECTION'`
#: branch, which the live feed did not exercise on 2026-09-15.
SPIKED_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0], [0.0, 2.0], [0.0, 0.0]]],
}
#: Zero-area: every ordinate identical. `ST_MakeValid` yields a POINT; its polygonal parts are an empty polygon.
DEGENERATE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]],
}
#: A collinear ring. `ST_MakeValid` yields a MULTILINESTRING, which holds no polygonal part at all.
COLLINEAR: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [0.0, 0.0]]],
}


def wfigs_fixture_features() -> tuple[dict[str, Any], ...]:
    """The fixture's features in file order; shared with `test_fire_perimeters_direct_rows.py`."""
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return tuple(document["features"])


def wfigs_fixture_geometries() -> dict[str, dict[str, Any]]:
    """Every fixture perimeter's geometry keyed by its `poly_SourceOID` as text, in file order."""
    features = wfigs_fixture_features()
    return {str(feature["properties"]["poly_SourceOID"]): feature["geometry"] for feature in features}


def _repair(geometries: Sequence[dict[str, Any]], identities: Sequence[str]) -> tuple[RepairedPerimeterGeometry, ...]:
    with fire_perimeter_geometry_session() as session:
        return repair_perimeter_geometries_to_wkb(session, geometries, identities)


def _flagged(identities: Sequence[str], repaired: Sequence[RepairedPerimeterGeometry]) -> frozenset[str]:
    """The identities whose geometry the chain changed."""
    changed = (identity for identity, geometry in zip(identities, repaired, strict=True) if geometry.repaired)
    return frozenset(changed)


def _wkb_type(payload: bytes) -> int:
    """Read the geometry type out of a little-endian WKB header."""
    assert payload[0] == 1, "expected little-endian WKB"
    return int.from_bytes(payload[1:5], "little")


def _plain_wkb(geometry: dict[str, Any]) -> bytes:
    """The bytes a bare `ST_AsWKB(ST_GeomFromGeoJSON(...))` yields, with no repair in the chain at all."""
    with fire_perimeter_geometry_session() as session:
        answer = session.execute(
            "SELECT ST_AsWKB(ST_GeomFromGeoJSON(?))", [json.dumps(geometry, separators=(",", ":"))]
        ).fetchone()
    assert answer is not None
    return bytes(answer[0])


def _is_valid_and_non_empty(payload: bytes) -> bool:
    """Ask DuckDB itself whether the published bytes read back as a valid, non-empty geometry."""
    with fire_perimeter_geometry_session() as session:
        answer = session.execute(
            "SELECT ST_IsValid(ST_GeomFromWKB(?)), ST_IsEmpty(ST_GeomFromWKB(?))", [payload, payload]
        ).fetchone()
    assert answer is not None
    valid, empty = answer
    return bool(valid) and not bool(empty)


def test_the_fixture_still_exercises_both_branches_of_the_chain() -> None:
    """The fixture records what ST_IsValid said on 2026-09-15; a DuckDB that disagrees has moved the ground."""
    features = wfigs_fixture_features()
    assert tuple(str(feature["properties"]["poly_SourceOID"]) for feature in features) == FIXTURE_IDENTITIES
    recorded_invalid = frozenset(
        str(feature["properties"]["poly_SourceOID"])
        for feature in features
        if feature["properties"]["st_isvalid_at_capture"] is False
    )
    assert recorded_invalid == REPAIRED_IDENTITIES

    repaired = _repair([feature["geometry"] for feature in features], FIXTURE_IDENTITIES)

    assert _flagged(FIXTURE_IDENTITIES, repaired) == REPAIRED_IDENTITIES


def test_a_valid_live_perimeter_passes_through_untouched_and_unflagged() -> None:
    """DO NOT round-trip a valid shape through MakeValid: re-noded bytes would read as a content change."""
    geometries = wfigs_fixture_geometries()

    polygon, multipolygon = _repair(
        [geometries[SUMMIT_LAKE_VALID_POLYGON], geometries[MINERS_VALID_MULTIPOLYGON]],
        [SUMMIT_LAKE_VALID_POLYGON, MINERS_VALID_MULTIPOLYGON],
    )

    assert (polygon.repaired, polygon.area_change) == (False, None)
    assert (multipolygon.repaired, multipolygon.area_change) == (False, None)
    assert _wkb_type(polygon.wkb) == WKB_POLYGON
    assert _wkb_type(multipolygon.wkb) == WKB_MULTIPOLYGON
    assert polygon.wkb == _plain_wkb(geometries[SUMMIT_LAKE_VALID_POLYGON])
    assert multipolygon.wkb == _plain_wkb(geometries[MINERS_VALID_MULTIPOLYGON])


def test_an_invalid_live_multipolygon_is_repaired_flagged_and_its_area_loss_recorded() -> None:
    """Skull: the capture's smallest invalid MULTIPOLYGON, which sheds 7.56% of its planar area on repair."""
    (skull,) = _repair([wfigs_fixture_geometries()[SKULL_INVALID_MULTIPOLYGON]], [SKULL_INVALID_MULTIPOLYGON])

    assert skull.repaired is True
    assert skull.area_change == pytest.approx(-0.0756, abs=0.001)
    assert _wkb_type(skull.wkb) == WKB_MULTIPOLYGON
    assert _is_valid_and_non_empty(skull.wkb)


def test_an_invalid_live_polygon_stays_a_polygon_and_records_no_area_change() -> None:
    """Egypt repairs by re-noding one vertex: flagged, still POLYGON (no ST_Multi here), area unchanged."""
    (egypt,) = _repair([wfigs_fixture_geometries()[EGYPT_INVALID_POLYGON]], [EGYPT_INVALID_POLYGON])

    assert egypt.repaired is True
    assert egypt.area_change == pytest.approx(0.0, abs=1e-9)
    assert _wkb_type(egypt.wkb) == WKB_POLYGON
    assert _is_valid_and_non_empty(egypt.wkb)


def test_a_mixed_live_feed_publishes_every_perimeter_instead_of_refusing_the_snapshot() -> None:
    """THE ACTIVATION BLOCKER. Two valid and three invalid real perimeters in: five valid shapes out."""
    geometries = wfigs_fixture_geometries()

    repaired = _repair([geometries[identity] for identity in FIXTURE_IDENTITIES], FIXTURE_IDENTITIES)

    assert len(repaired) == 5
    assert _flagged(FIXTURE_IDENTITIES, repaired) == REPAIRED_IDENTITIES
    assert all(_is_valid_and_non_empty(geometry.wkb) for geometry in repaired)
    assert all(geometry.area_change is None for geometry in repaired if not geometry.repaired)
    assert all(isinstance(geometry.area_change, float) for geometry in repaired if geometry.repaired)
    recorded = [geometry.area_change for geometry in repaired if geometry.area_change is not None]
    assert min(recorded) > -0.25  # the live capture's worst repair moved 22.6%


def test_a_repair_that_produces_a_collection_keeps_only_the_polygonal_parts() -> None:
    """The trigger's GEOMETRYCOLLECTION branch: the spike's LINESTRING debris is extracted away."""
    (spiked,) = _repair([SPIKED_SQUARE], ["SPIKE-1"])

    assert spiked.repaired is True
    assert _wkb_type(spiked.wkb) in {WKB_POLYGON, WKB_MULTIPOLYGON}
    assert _is_valid_and_non_empty(spiked.wkb)
    assert spiked.area_change == pytest.approx(0.0, abs=1e-12)  # the spike enclosed no area to lose


def test_the_bowtie_the_old_tests_refused_is_now_repaired_and_its_zero_area_leaves_no_ratio() -> None:
    """A self-intersecting ring encloses no signed area, so the ratio has no denominator and stays None."""
    (bowtie,) = _repair([BOWTIE], ["OR-BOWTIE"])

    assert bowtie.repaired is True
    assert bowtie.area_change is None
    assert _wkb_type(bowtie.wkb) == WKB_MULTIPOLYGON
    assert _is_valid_and_non_empty(bowtie.wkb)


@pytest.mark.parametrize("unrepairable", [DEGENERATE, COLLINEAR], ids=["degenerate-point", "collinear-lines"])
def test_a_perimeter_with_no_polygonal_part_after_repair_refuses_the_whole_snapshot_by_name(
    unrepairable: dict[str, Any],
) -> None:
    """`refuse_whole_release` survives for what MakeValid cannot make polygonal -- the trigger's final RAISE."""
    with pytest.raises(FirePerimeterGeometryError) as refusal:
        _repair([VALID_SQUARE, unrepairable], ["OR-A", "OR-UNREPAIRABLE"])

    message = str(refusal.value)
    assert "OR-UNREPAIRABLE" in message
    assert "empty" in message
    assert "after ST_MakeValid" in message
    assert "22023" in message


def test_an_empty_input_needs_no_round_trip() -> None:
    with fire_perimeter_geometry_session() as session:
        assert repair_perimeter_geometries_to_wkb(session, [], []) == ()


def test_the_answer_is_positionally_aligned_with_the_input() -> None:
    """A silent reorder would leave every perimeter present, valid, counted -- and drawn on other ground."""
    geometries = wfigs_fixture_geometries()
    with fire_perimeter_geometry_session() as session:
        first = repair_perimeter_geometries_to_wkb(session, [geometries[SKULL_INVALID_MULTIPOLYGON]], ["A"])
        second = repair_perimeter_geometries_to_wkb(session, [geometries[SUMMIT_LAKE_VALID_POLYGON]], ["B"])
        both = repair_perimeter_geometries_to_wkb(
            session,
            [geometries[SKULL_INVALID_MULTIPOLYGON], geometries[SUMMIT_LAKE_VALID_POLYGON]],
            ["A", "B"],
        )

    assert both == (first[0], second[0])


def test_a_refusal_that_could_not_name_its_perimeter_is_refused_first() -> None:
    with fire_perimeter_geometry_session() as session, pytest.raises(FirePerimeterGeometryError, match="identities"):
        repair_perimeter_geometries_to_wkb(session, [VALID_SQUARE, OTHER_SQUARE], ["OR-A"])


def test_a_document_duckdb_cannot_read_as_geojson_is_refused_the_way_postgis_refuses_it() -> None:
    with fire_perimeter_geometry_session() as session, pytest.raises(FirePerimeterGeometryError, match="22023"):
        repair_perimeter_geometries_to_wkb(session, [{"type": "Polygon", "coordinates": "not-a-ring"}], ["OR-BAD"])
