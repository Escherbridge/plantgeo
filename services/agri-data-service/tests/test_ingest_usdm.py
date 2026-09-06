"""USDM source adapter: the Tuesday guard, the 404 rule, and the whole-release rejections.

The PostGIS store, the weekly retention prune and the forward job this file also covered were
deleted 2026-09-06 with the `ingest-drought` verb and the `postgres-drought` lane.
"""

from __future__ import annotations

import httpx
import pytest

from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError
from agri_data_service.ingest.usdm import (
    fetch_drought_release,
    parse_drought_release,
    usdm_source_url,
)

# 2026-07-28: a real published Tuesday, the release production held in geo.drought_areas before the
# layer froze. Kept as the fixture date so the pinned provenance URL stays a real one.
LATEST_TUESDAY = "2026-07-28"

SQUARE_RING = [[-113.0, 47.0], [-113.0, 47.1], [-112.9, 47.1], [-112.9, 47.0], [-113.0, 47.0]]


def _feature(drought_class: object, geometry_type: str = "MultiPolygon") -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": geometry_type, "coordinates": [[SQUARE_RING]]},
        "properties": {"DM": drought_class},
    }


def _collection(*drought_classes: int) -> dict[str, object]:
    return {"type": "FeatureCollection", "features": [_feature(item) for item in drought_classes]}


def test_the_source_url_matches_the_provenance_production_stored() -> None:
    assert usdm_source_url(LATEST_TUESDAY) == "https://droughtmonitor.unl.edu/data/json/usdm_20260728.json"


def test_a_release_sorts_its_classes_and_records_its_provenance() -> None:
    release = parse_drought_release(LATEST_TUESDAY, _collection(3, 0, 1))
    assert [area.drought_monitor_category for area in release.areas] == [0, 1, 3]
    assert release.source_url.endswith("usdm_20260728.json")
    assert release.valid_date == LATEST_TUESDAY


def test_the_release_date_is_the_request_parameter_and_never_read_out_of_the_payload() -> None:
    # The published GeoJSON carries no date field, so a date-shaped property must not be believed.
    payload = _collection(0, 1)
    payload["validDate"] = "1999-01-05"
    payload["date"] = "1999-01-05"
    release = parse_drought_release(LATEST_TUESDAY, payload)
    assert release.valid_date == LATEST_TUESDAY
    assert release.source_url.endswith("usdm_20260728.json")


def test_a_repeated_drought_class_rejects_the_whole_release_rather_than_picking_one() -> None:
    with pytest.raises(UpstreamPayloadError, match="repeats drought class D2"):
        parse_drought_release(LATEST_TUESDAY, _collection(0, 2, 2))


def test_a_release_with_no_drought_classes_is_refused() -> None:
    with pytest.raises(UpstreamPayloadError, match="contained no drought classes"):
        parse_drought_release(LATEST_TUESDAY, _collection())


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "Something", "features": []},
        {"type": "FeatureCollection", "features": "not-a-list"},
        {"type": "FeatureCollection", "features": [_feature(9)]},
        {"type": "FeatureCollection", "features": [_feature(True)]},
        {"type": "FeatureCollection", "features": [_feature(1, geometry_type="LineString")]},
        {"type": "FeatureCollection", "features": [_feature(1, geometry_type="GeometryCollection")]},
    ],
)
def test_an_unexpected_release_shape_is_refused(payload: object) -> None:
    with pytest.raises(UpstreamPayloadError):
        parse_drought_release(LATEST_TUESDAY, payload)


def test_a_single_part_polygon_class_is_accepted_rather_than_rejecting_the_whole_release() -> None:
    """USDM ships a contiguous drought class as a bare Polygon; the store's ST_Multi already promotes it.

    This case was pinned as a REFUSAL until 2026-08-05, when it was measured to be the sole cause of 26
    of the 29 release weeks missing from production `geo.drought_areas` -- every one a week whose D4
    class happened to be one contiguous area. See ingest/AGENTS.md "usdm.py".
    """
    release = parse_drought_release(
        LATEST_TUESDAY,
        {
            "type": "FeatureCollection",
            "features": [
                *(_feature(drought_class) for drought_class in (0, 1, 2, 3)),
                _feature(4, geometry_type="Polygon"),
            ],
        },
    )
    assert [area.drought_monitor_category for area in release.areas] == [0, 1, 2, 3, 4]
    assert release.areas[4].geometry["type"] == "Polygon"


async def test_a_non_tuesday_is_refused_before_any_request_is_made() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="Tuesdays"):
            await fetch_drought_release(client, "2026-07-27")


async def test_a_malformed_date_is_refused() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
            await fetch_drought_release(client, "28-07-2026")


@pytest.mark.parametrize("value", ["20260728", "2026-W31-2"])
async def test_a_date_only_python_accepts_is_refused_like_the_typescript_regex_refuses_it(value: str) -> None:
    # date.fromisoformat() parses both of these to a real Tuesday; `/^\d{4}-\d{2}-\d{2}$/` does not.
    # Without the round-trip spelling check they would pass the Tuesday guard and request a wrong URL.
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
            await fetch_drought_release(client, value)


async def test_a_404_means_not_published_yet_rather_than_a_failure() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(404))) as client:
        assert await fetch_drought_release(client, LATEST_TUESDAY) is None


async def test_a_non_404_status_still_fails() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500))) as client:
        with pytest.raises(UpstreamHttpError):
            await fetch_drought_release(client, LATEST_TUESDAY)


async def test_a_garbled_body_stays_a_failure_rather_than_being_read_as_unpublished() -> None:
    # The 404-versus-payload split is why a mid-week walk is normal and a corrupt file is still red.
    response = httpx.Response(200, content=b"<html>maintenance</html>", headers={"content-type": "application/json"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        with pytest.raises(UpstreamPayloadError):
            await fetch_drought_release(client, LATEST_TUESDAY)


# FIFTEEN TESTS STOOD HERE AND ARE DELETED WITH THEIR SUBJECT (2026-09-06): the newest-published-release
# walk (`fetch_latest_drought_release`, `usdm_valid_date_candidates`), the `DROUGHT_RETAINED_RELEASES`
# retention clamp, `run_drought_ingestion_job`, and every `PostgresDroughtStore` test -- the in-database
# repair chain, the `replace` conflict predicate, the repaired-to-empty refusal, the one-transaction
# write, the rollback, and the prune. All of them wrote or pruned `geo.drought_areas`, which now has no
# Python producer at all: `pipeline/direct/drought/` owns the full floor-to-settled window and writes
# Parquet.
#
# `sql/ingest/store_drought_area.sql` and `sql/ingest/prune_drought_releases.sql` were deleted with
# them, because a `.sql` file with zero `load_query_sql` call sites FAILS `test_sql_tree_conventions.py`
# -- an orphaned statement is a test failure in this tree, not dead weight. The PostGIS repair chain
# those tests pinned is restated and asserted in `pipeline/direct/drought/support.py` and
# `tests/direct/test_drought_support.py`, which is why nothing is lost by their going.
#
# What stays above is everything that is still true of the source: the Tuesday guard, the 404 rule, the
# whole-release rejections and the single-part-Polygon acceptance, all of which
# `pipeline/direct/drought/source.py` reaches through this module's `fetch_drought_release`.
