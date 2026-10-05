"""Which overview rung a below-z13 viewport is drawn at, and what happens when even the coarsest overflows."""

from __future__ import annotations

import json
from typing import Final

import polars as pl
import pytest

from agri_data_service.foundation.soil_survey.release import Release
from agri_data_service.pipeline.direct.soil_survey.overview import OVERVIEW_CELL_DEGREES, OVERVIEW_SCHEMA
from agri_data_service.planes import soil_survey as plane
from agri_data_service.planes.soil_survey import SoilSurveyViewport, render_soil_survey_overview

#: A fully surveyed 2 x 2 degree block west of Greenwich, at every rung: 6,400 / 1,600 / 100 cells.
BLOCK: Final = (-118.0, 44.0, -116.0, 46.0)

_RELEASE: Final = Release.model_validate_json(
    json.dumps(
        {
            "scope": {
                "response": {"sha256": "b" * 64, "byte_count": 2},
                "query_sha256": "c" * 64,
                "checked_at": "2026-09-28T12:00:00+00:00",
                "areas": [{"area": "ID001", "saverest": "2025-08-27"}],
                "envelope": [-126, 41, -110, 50],
                "region": "pnw",
            },
            "shards": [
                {
                    "shard": "ID-1",
                    "manifest": {"sha256": "b" * 64, "byte_count": 2},
                    "release_day": "2025-08-27",
                    "captured_at": "2026-09-28T12:00:00+00:00",
                    "areas": [
                        {
                            "area": "ID001",
                            "saverest": "2025-08-27",
                            "native_rows": 2,
                            "repaired_rows": 0,
                            "labelled_rows": 0,
                        }
                    ],
                    "bbox": list(BLOCK),
                }
            ],
            "pending_areas": [],
            "source_evidence": "staged",
            "release_day": "2025-08-27",
            "captured_at": "2026-09-28T12:00:00+00:00",
        }
    )
)


def _block_overview() -> pl.DataFrame:
    frames = []
    for degrees in OVERVIEW_CELL_DEGREES:
        cols = range(round(BLOCK[0] / degrees), round(BLOCK[2] / degrees))
        rows = range(round(BLOCK[1] / degrees), round(BLOCK[3] / degrees))
        frames.append(
            pl.DataFrame(
                [(degrees, col, row, "well-drained", 1.0, 1.0, None, 3) for col in cols for row in rows],
                schema=list(OVERVIEW_SCHEMA.names),
                orient="row",
            )
        )
    return pl.from_arrow(pl.concat(frames).to_arrow().cast(OVERVIEW_SCHEMA))  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("zoom", "bbox", "degrees", "cells"),
    [
        # z12 over a quarter-degree: the finest rung fits.
        (12, (-117.25, 45.0, -117.0, 45.25), 0.025, 100),
        # z9 over the whole block: 6,400 fine cells overflow the 4,000 budget, so one rung coarser.
        (9, BLOCK, 0.05, 1600),
        # z6 is floored at 0.05 even where 0.025 would fit: sub-pixel cells are never drawn.
        (6, (-117.25, 45.0, -117.0, 45.25), 0.05, 25),
        # z3 only ever draws the coarsest rung.
        (3, BLOCK, 0.2, 100),
    ],
)
def test_a_viewport_draws_the_finest_rung_its_tier_allows_that_fits_the_cell_budget(
    zoom: int, bbox: tuple[float, float, float, float], degrees: float, cells: int
) -> None:
    result = render_soil_survey_overview(
        _block_overview(), release=_RELEASE, request=SoilSurveyViewport(bbox, zoom), admitted_sha256="a" * 64
    )
    assert {feature["properties"]["cellDegrees"] for feature in result["features"]} == {degrees}
    assert len(result["features"]) == cells
    assert result["truncated"] is False


def test_an_overflowing_coarsest_rung_is_truncated_to_the_cells_nearest_the_view_centre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plane, "MAX_OVERVIEW_CELLS", 4)
    result = render_soil_survey_overview(
        _block_overview(), release=_RELEASE, request=SoilSurveyViewport(BLOCK, 3), admitted_sha256="a" * 64
    )
    assert result["truncated"] is True
    corners = sorted(tuple(feature["geometry"]["coordinates"][0][0]) for feature in result["features"])
    # The four 0.2-degree cells meeting at the block's centre (-117, 45).
    assert corners == [(-117.2, 44.8), (-117.2, 45.0), (-117.0, 44.8), (-117.0, 45.0)]
