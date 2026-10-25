"""Every lane digest seam moved onto `_shared/digest.py` still emits the bytes its pre-extraction formula did.

These digests are published (G6 parity, S11 rewrite decisions), so each lane's call is pinned against its
FROZEN pre-extraction expression, copied verbatim from the parent commit (8a0574d9). The fixture is chosen
so every kwarg matters: keys out of order (`sort_keys`), non-ASCII text (`ensure_ascii`), a float and a None
(separators), and an instant (`default=str`). If someone "tidies" a call site's kwargs, its case goes red.
See `pipeline/lanes/_shared/AGENTS.md`, "Digests".
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast

import pytest

from agri_data_service.ingest.usdm import DroughtArea, DroughtRelease
from agri_data_service.pipeline.lanes.crop_cover import source as crop_cover_source
from agri_data_service.pipeline.lanes.drought import usdm_releases
from agri_data_service.pipeline.lanes.fire_perimeters import wfigs
from agri_data_service.pipeline.lanes.land_context import source as land_context_source
from agri_data_service.pipeline.lanes.water_gauges import usgs_water_data
from agri_data_service.pipeline.lanes.watersheds import nhdplus
from agri_data_service.pipeline.lanes.watersheds.source import WatershedRecord

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Unsorted keys, non-ASCII text, a float, a None and a nested mapping: every dumps kwarg changes these bytes.
EVIDENCE: Final[dict[str, object]] = {"zeta": 1.5, "alpha": "Ñandú", "mid": None, "nested": {"b": 2, "a": [1, 2]}}
SQUARE: Final[dict[str, object]] = {
    "type": "Polygon",
    "coordinates": [[[-118.0, 45.0], [-117.0, 45.0], [-117.0, 46.0], [-118.0, 46.0], [-118.0, 45.0]]],
}


def test_land_context_canonical_bytes_and_digest_are_frozen() -> None:
    frozen = json.dumps(EVIDENCE, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert land_context_source.canonical_bytes(EVIDENCE) == frozen
    assert land_context_source.digest(frozen) == hashlib.sha256(frozen).hexdigest()


def test_crop_cover_canonical_bytes_and_sha256_are_frozen() -> None:
    frozen = json.dumps(EVIDENCE, sort_keys=True, separators=(",", ":")).encode()
    assert crop_cover_source.canonical_bytes(EVIDENCE) == frozen
    assert crop_cover_source.sha256(frozen) == hashlib.sha256(frozen).hexdigest()


def test_land_context_refuses_a_non_finite_number_where_crop_cover_admits_it() -> None:
    """The one kwarg the two otherwise-identical canonicalisers disagree on, kept apart on purpose."""
    with pytest.raises(ValueError, match="not JSON compliant"):
        land_context_source.canonical_bytes({"x": math.nan})
    assert crop_cover_source.canonical_bytes({"x": math.nan}) == b'{"x":NaN}'


def test_watersheds_snapshot_digest_body_is_frozen() -> None:
    record = WatershedRecord(
        huc12="170900011201",
        name="Ñandú Creek",
        areasqkm=12.5,
        tohuc=None,
        states="OR",
        hutype="S",
        observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        geometry={"type": "Polygon", "coordinates": SQUARE["coordinates"]},
    )
    fingerprint = hashlib.sha256(
        json.dumps(record.geometry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    facts = [
        ("170900011201", "Ñandú Creek", 12.5, None, "OR", "S", "2026-09-01T00:00:00+00:00", fingerprint),
    ]
    frozen = json.dumps(facts, separators=(",", ":")).encode("utf-8")
    assert nhdplus._snapshot_digest_body([record]) == frozen


def test_drought_source_digest_is_frozen() -> None:
    release = DroughtRelease(
        valid_date="2026-09-01",
        source_url="https://usdm.example/release",
        areas=(DroughtArea(3, SQUARE), DroughtArea(0, {"type": "Polygon", "coordinates": []})),
    )
    canonical = json.dumps(
        {
            "valid_date": "2026-09-01",
            "source_url": "https://usdm.example/release",
            "areas": [
                {"dm_category": 0, "geometry": {"type": "Polygon", "coordinates": []}},
                {"dm_category": 3, "geometry": SQUARE},
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert usdm_releases._source_digest(release) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_fire_perimeters_content_digest_is_frozen() -> None:
    """Default separators and `default=str` over an instant: the per-row rendering joined by newlines."""
    rows = [
        {"unique_fire_identifier": "{B}", "incident_name": "Ñandú", "acres": 2.5, "updated_at": "dropped"},
        {"unique_fire_identifier": "{A}", "incident_name": None, "observed": datetime(2026, 9, 1, tzinfo=UTC)},
    ]
    canonical = "\n".join(
        json.dumps({key: value for key, value in row.items() if key != "updated_at"}, sort_keys=True, default=str)
        for row in sorted(rows, key=lambda row: str(row["unique_fire_identifier"]))
    )
    assert wfigs.content_digest(rows) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _GaugeDay:
    """Only `digest_facts()` is read by `day_source_digest`."""

    def __init__(self, facts: list[object]) -> None:
        self._facts = facts

    def digest_facts(self) -> list[object]:
        return self._facts


def test_water_gauges_day_source_digest_is_frozen() -> None:
    """Unsorted keys stay unsorted: this site never passed `sort_keys`."""
    facts: list[list[object]] = [["USGS-14105700", 1.5, {"zeta": 1, "alpha": "Ñ"}], ["USGS-14211720", None]]
    gauge_days = cast("Sequence[usgs_water_data.GaugeDay]", [_GaugeDay(item) for item in facts])
    canonical = "\n".join(json.dumps(item, separators=(",", ":")) for item in facts)
    assert usgs_water_data.day_source_digest(gauge_days) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
