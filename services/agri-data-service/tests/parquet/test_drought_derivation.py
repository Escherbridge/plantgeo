"""Drought's coarse rungs, derived the way the drain derives them: one coverage per USDM release.

Flow: a release's base rung goes through `derive_tier(stream="drought")` for z9, z5 and z0, and the
map paints those rows. The fixture is a real three-class coverage whose shared class edges wiggle,
the shape that, simplified one class at a time, overlapped 0.28 deg2 and opened 0.19 deg2 of seams at
0.2 degrees. Production evidence: `warehouse/parquet/AGENTS.md`, "Coverage-preserving simplification".
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import duckdb
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS, derive_tier
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA, DROUGHT_STREAM

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agri_data_service.foundation.parquet.zoom import ZoomTier

RELEASE_DAY: Final = date(2026, 10, 6)
SEAM_SAMPLES: Final = 240
# How far inside the release's outer edge a gap counts as a seam rather than a generalised outline.
INTERIOR_INSET_DEGREES: Final = 0.5
# Area below which a difference is floating-point noise, not ground.
AREA_NOISE_DEGREES_SQUARED: Final = 1e-9
# The share of its own base area each class must still paint at z0; the shared 5.0 tolerance keeps 47%.
Z0_MINIMUM_KEPT_SHARE: Final = 0.95


@pytest.fixture(name="spatial")
def _spatial() -> Iterator[duckdb.DuckDBPyConnection]:
    """One DuckDB session with the spatial extension, for building WKB and measuring results."""
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("LOAD spatial")
    except duckdb.Error:  # pragma: no cover - only the first run on a machine pays this
        connection.execute("INSTALL spatial")
        connection.execute("LOAD spatial")
    yield connection
    connection.close()


def _class_edge(longitude: float, phase: float) -> list[tuple[float, float]]:
    """A dense south-to-north class boundary that wiggles, as USDM's hand-drawn edges do."""
    return [
        (
            longitude
            + 0.3 * math.sin(phase + 9 * math.pi * step / SEAM_SAMPLES)
            + 0.08 * math.sin(37 * math.pi * step / SEAM_SAMPLES),
            40.0 + 5.0 * step / SEAM_SAMPLES,
        )
        for step in range(SEAM_SAMPLES + 1)
    ]


def _polygon_wkt(points: list[tuple[float, float]]) -> str:
    return "POLYGON((" + ",".join(f"{x} {y}" for x, y in points) + "))"


def _coverage_classes() -> dict[int, str]:
    """D0 | D1 | D2 across a 10 x 5 degree strip, each pair sharing one wiggly edge exactly."""
    west, east = _class_edge(-117.0, 0.0), _class_edge(-113.0, 1.3)
    return {
        0: _polygon_wkt([(-120.0, 40.0), *west, (-120.0, 45.0), (-120.0, 40.0)]),
        1: _polygon_wkt([*west, *reversed(east), west[0]]),
        2: _polygon_wkt([*east, (-110.0, 45.0), (-110.0, 40.0), east[0]]),
    }


def _release(spatial: duckdb.DuckDBPyConnection, classes: dict[int, str]) -> pl.DataFrame:
    """One release's base rung, shaped exactly as `DROUGHT_SCHEMA`."""
    geometries = [
        bytes(spatial.execute("SELECT ST_AsWKB(ST_GeomFromText(?))", [wkt]).fetchone()[0])  # type: ignore[index]
        for wkt in classes.values()
    ]
    count = len(classes)
    table = pa.table(
        {
            "area_id": [f"direct:{RELEASE_DAY}:{category}" for category in classes],
            "valid_date": [RELEASE_DAY] * count,
            "dm_category": list(classes),
            "source_url": ["https://droughtmonitor.unl.edu/data/json/usdm_20261006.json"] * count,
            "ingested_at": [datetime(2026, 10, 10, tzinfo=UTC)] * count,
            "geom": geometries,
        },
        schema=DROUGHT_SCHEMA.arrow_schema,
    )
    frame = pl.from_arrow(table)
    assert isinstance(frame, pl.DataFrame)
    return frame


def _register(spatial: duckdb.DuckDBPyConnection, name: str, frame: pl.DataFrame) -> None:
    spatial.register(name, frame.select("dm_category", "geom").to_arrow())


@pytest.mark.parametrize("tier", DERIVED_ZOOM_TIERS)
def test_every_derived_rung_paints_the_release_with_no_seam_and_no_overlap(
    spatial: duckdb.DuckDBPyConnection, tier: ZoomTier
) -> None:
    """Gate S6: class boundaries stay closed at every rung, so no basemap shows between D-classes."""
    base = _release(spatial, _coverage_classes())
    derived = derive_tier(base, stream=DROUGHT_STREAM, tier=tier, connection=spatial)
    _register(spatial, "base_rung", base)
    _register(spatial, "derived_rung", derived)

    overlap, seams, still_a_coverage = spatial.execute(
        f"""
        WITH base AS (SELECT ST_Union_Agg(ST_GeomFromWKB(geom)) AS area FROM base_rung),
             painted AS (SELECT ST_Union_Agg(ST_GeomFromWKB(geom)) AS area FROM derived_rung)
        SELECT
            (SELECT coalesce(sum(ST_Area(ST_Intersection(ST_GeomFromWKB(a.geom), ST_GeomFromWKB(b.geom)))), 0)
             FROM derived_rung a JOIN derived_rung b ON a.dm_category < b.dm_category),
            ST_Area(ST_Difference(ST_Buffer(base.area, -{INTERIOR_INSET_DEGREES}), painted.area)),
            (SELECT ST_CoverageInvalidEdges(list(ST_GeomFromWKB(geom) ORDER BY dm_category)) IS NULL
             FROM derived_rung)
        FROM base, painted
        """
    ).fetchone()  # type: ignore[misc]

    assert sorted(derived["dm_category"].to_list()) == [0, 1, 2]
    assert overlap < AREA_NOISE_DEGREES_SQUARED, f"z{tier}: two classes paint the same {overlap} deg2"
    assert seams < AREA_NOISE_DEGREES_SQUARED, f"z{tier}: {seams} deg2 of basemap shows between classes"
    assert still_a_coverage, f"z{tier}: neighbouring classes no longer share their edges"


def test_z0_keeps_each_class_where_the_shared_five_degree_tolerance_would_erase_it(
    spatial: duckdb.DuckDBPyConnection,
) -> None:
    """z0 answers z0-z4, the phone's opening camera; each class must still paint its own ground."""
    base = _release(spatial, _coverage_classes())
    derived = derive_tier(base, stream=DROUGHT_STREAM, tier=0, connection=spatial)
    _register(spatial, "base_rung", base)
    _register(spatial, "derived_rung", derived)

    kept = dict(
        spatial.execute(
            """
            SELECT b.dm_category,
                   ST_Area(ST_Intersection(ST_GeomFromWKB(b.geom), ST_GeomFromWKB(d.geom)))
                   / ST_Area(ST_GeomFromWKB(b.geom))
            FROM base_rung b JOIN derived_rung d USING (dm_category)
            """
        ).fetchall()
    )

    assert set(kept) == {0, 1, 2}
    assert all(share >= Z0_MINIMUM_KEPT_SHARE for share in kept.values()), kept


def test_a_release_that_is_not_a_coverage_is_still_simplified_rather_than_shipped_whole(
    spatial: duckdb.DuckDBPyConnection,
) -> None:
    """Overlapping classes are not a coverage; DuckDB would hand them back at base-rung size."""
    classes = _coverage_classes()
    overlapping = {0: classes[1], 1: classes[1]}
    base = _release(spatial, overlapping)
    derived = derive_tier(base, stream=DROUGHT_STREAM, tier=5, connection=spatial)

    def vertices(frame: pl.DataFrame) -> int:
        return sum(
            spatial.execute("SELECT ST_NPoints(ST_GeomFromWKB(?))", [geometry]).fetchone()[0]  # type: ignore[index]
            for geometry in frame["geom"]
        )

    assert derived.height == len(overlapping)
    assert vertices(derived) * 4 < vertices(base), "an invalid coverage was published unsimplified"
