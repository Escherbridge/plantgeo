"""Resolving raw Sentinel-2 grid records into one governed value per cell-day."""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agri_data_service.pipeline.direct.vegetation.source import (
    VegetationSourceError,
    _day_response_sha256,
    _parse_scene_instant,
    _record_sha256,
    _select_clearest,
)
from agri_data_service.pipeline.direct.vegetation.support import VegetationSupportCell

_CELL = VegetationSupportCell(
    cell_id="00000000-0000-4000-8000-000000000001",
    cell_key="sentinel2-ndvi-0p25deg:43.1250:-116.3750",
    cell_longitude=-116.375,
    cell_latitude=43.125,
)


def _record(*, scene_id: str, cloud_cover: float, ndvi: float = 0.5) -> dict[str, object]:
    return {
        "cellKey": "43.1250:-116.3750",
        "gridName": "sentinel2-ndvi-0p25deg",
        "ndvi": ndvi,
        "observedAt": "2026-09-06T18:30:00.000Z",
        "sceneId": scene_id,
        "cloudCover": cloud_cover,
        "sampleCount": 21,
    }


def test_the_clearest_of_several_same_day_records_is_selected() -> None:
    records = [_record(scene_id="cloudy", cloud_cover=18.0), _record(scene_id="clear", cloud_cover=2.0)]

    chosen = _select_clearest(_CELL, records)

    assert chosen.scene_id == "clear"
    assert chosen.cloud_cover_percent == 2.0


def test_release_count_carries_how_many_candidates_a_cell_day_had() -> None:
    records = [_record(scene_id="a", cloud_cover=10.0), _record(scene_id="b", cloud_cover=5.0)]

    chosen = _select_clearest(_CELL, records)

    assert chosen.release_count == 2


def test_a_single_record_day_reports_release_count_one() -> None:
    chosen = _select_clearest(_CELL, [_record(scene_id="only", cloud_cover=1.0)])

    assert chosen.release_count == 1


def test_the_record_digest_is_stable_across_calls_and_changes_with_the_value() -> None:
    record = _record(scene_id="a", cloud_cover=1.0)

    assert _record_sha256(record) == _record_sha256(dict(record))
    assert _record_sha256(record) != _record_sha256(_record(scene_id="a", cloud_cover=1.0, ndvi=0.51))


def test_the_day_digest_is_order_independent() -> None:
    """DO NOT DELETE. Sorting on `cellKey` alone was not a total order: a Sentinel-2 revisit gives one
    cell several records in one UTC day, Python's sort is stable, and the digest then depended on the
    order Earth Search happened to answer in -- for a value carried into every day's receipt."""
    forward = [_record(scene_id="a", cloud_cover=1.0), _record(scene_id="b", cloud_cover=2.0)]
    reversed_records = list(reversed(forward))

    assert _day_response_sha256(forward) == _day_response_sha256(reversed_records)


def test_the_day_digest_still_changes_when_a_value_changes() -> None:
    """Order-independence must not have been bought by hashing less."""
    original = [_record(scene_id="a", cloud_cover=1.0), _record(scene_id="b", cloud_cover=2.0)]
    altered = [_record(scene_id="a", cloud_cover=1.0), _record(scene_id="b", cloud_cover=2.0, ndvi=0.51)]

    assert _day_response_sha256(original) != _day_response_sha256(altered)


def test_a_perfectly_clear_scene_is_the_clearest_not_the_cloudiest() -> None:
    """DO NOT DELETE. `record.get("cloudCover", 100.0) or 100.0` folded a genuine 0.0 into the
    missing-value default, so a cloud-free acquisition ranked LAST among its candidates."""
    records = [_record(scene_id="thin-cloud", cloud_cover=4.0), _record(scene_id="cloud-free", cloud_cover=0.0)]

    chosen = _select_clearest(_CELL, records)

    assert chosen.scene_id == "cloud-free"
    assert chosen.cloud_cover_percent == 0.0


def test_an_unreported_cloud_cover_ranks_worst_rather_than_clearest() -> None:
    unreported = _record(scene_id="unknown", cloud_cover=0.0)
    unreported["cloudCover"] = None

    chosen = _select_clearest(_CELL, [unreported, _record(scene_id="measured", cloud_cover=40.0)])

    assert chosen.scene_id == "measured"


def test_a_malformed_ndvi_raises_this_modules_own_error_not_a_bare_value_error() -> None:
    """Every other failure in `source.py` is a `VegetationSourceError` the adapter can classify."""
    malformed = _record(scene_id="a", cloud_cover=1.0)
    malformed["ndvi"] = "not a number"

    with pytest.raises(VegetationSourceError, match="'ndvi'"):
        _select_clearest(_CELL, [malformed])


def test_a_missing_scene_id_raises_this_modules_own_error() -> None:
    malformed = _record(scene_id="a", cloud_cover=1.0)
    del malformed["sceneId"]

    with pytest.raises(VegetationSourceError, match="'sceneId'"):
        _select_clearest(_CELL, [malformed])


def test_a_non_integer_sample_count_raises_this_modules_own_error() -> None:
    malformed = _record(scene_id="a", cloud_cover=1.0)
    malformed["sampleCount"] = 21.5

    with pytest.raises(VegetationSourceError, match="'sampleCount'"):
        _select_clearest(_CELL, [malformed])


def test_a_malformed_observed_at_raises_this_modules_own_error() -> None:
    malformed = _record(scene_id="a", cloud_cover=1.0)
    malformed["observedAt"] = "the sixth of September"

    with pytest.raises(VegetationSourceError, match="observedAt"):
        _select_clearest(_CELL, [malformed])


def test_parse_scene_instant_is_always_timezone_aware() -> None:
    parsed = _parse_scene_instant("2026-09-06T18:30:00.000Z")

    assert parsed.tzinfo is not None
    assert parsed == datetime(2026, 9, 6, 18, 30, tzinfo=UTC)
