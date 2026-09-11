"""Current MTBS captures preserve historical normalization and explicit partial scope."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.ingest.mtbs import MtbsReleaseNotPublishedError, build_mtbs_record, build_mtbs_snapshot_record
from agri_data_service.pipeline.direct.burn_severity import capture
from agri_data_service.pipeline.direct.burn_severity.capture import CaptureBudget
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import (
    canonical_bytes,
    digest,
    make_source_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


def feature(year: int) -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {
            "fire_id": f"ID42627114403{year}0906",
            "year": year,
            "ig_date": year * 10000 + 906,
            "fire_name": "POWERLINE",
            "fire_type": "Wildfire",
            "acres": 1100.0,
            "map_id": 10029267,
            "asmnt_type": "Initial",
        },
        "geometry": {"type": "Polygon", "coordinates": [[[-120, 45], [-119.9, 45], [-119.9, 45.1], [-120, 45]]]},
    }


def test_snapshot_normalization_preserves_source_fields_and_historical_resolver() -> None:
    historical = build_mtbs_record(feature(2022), 2022)
    snapshot = build_mtbs_snapshot_record(
        feature(2022), 2022, manifest_sha256="a" * 64, available_at=datetime(2026, 9, 11, tzinfo=UTC)
    )
    changed = {"data_available_at", "release_identifier", "mapping_revision"}
    assert historical.model_dump(exclude=changed) == snapshot.model_dump(exclude=changed)
    assert historical.data_available_at.date().isoformat() == "2024-08-22"
    assert snapshot.data_available_at.date().isoformat() == "2026-09-11"


def test_partial_new_year_requires_snapshot_identity() -> None:
    with pytest.raises(MtbsReleaseNotPublishedError, match=r"release|governed|2025"):
        build_mtbs_record(feature(2025), 2025)
    record = build_mtbs_snapshot_record(
        feature(2025), 2025, manifest_sha256="b" * 64, available_at=datetime(2026, 9, 11, tzinfo=UTC)
    )
    assert record.ignition_year == record.ignition_date.year


@pytest.mark.parametrize("identity", ["", "a" * 63, "G" * 64, "../" + "a" * 61])
def test_snapshot_rejects_malformed_identity(identity: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        build_mtbs_snapshot_record(
            feature(2025), 2025, manifest_sha256=identity, available_at=datetime(2026, 9, 11, tzinfo=UTC)
        )


def test_manifest_availability_is_after_actual_capture_and_mapping_stays_partial() -> None:
    start = datetime(2026, 9, 10, 23, 59, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    years = tuple(range(2018, 2027))
    manifest = make_source_manifest(
        bbox=(-125, 42, -111, 49),
        years=years,
        captured_from=start,
        captured_through=end,
        counts=dict.fromkeys(years, 1),
        responses=[],
        source_content_sha256=digest(b""),
    )
    assert manifest["available_day"] == "2026-09-12"
    assert manifest["capture_complete"] is True
    assert manifest["partial_fire_years"] == [2023, 2024, 2025, 2026]


def test_capture_budget_counts_refused_requests_and_bytes() -> None:
    budget = CaptureBudget()
    budget.requests = 400
    with pytest.raises(ValueError, match="request cap"):
        budget.request()
    with pytest.raises(ValueError, match="raw-byte cap"):
        budget.charge(400 * 1024 * 1024 + 1)


def test_capture_refuses_source_revision_between_inventories(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    original_client = httpx.Client
    attribute_reads = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attribute_reads
        query = request.url.params
        selected = query["where"] == "year = 2025"
        if query.get("returnCountOnly"):
            return httpx.Response(200, json={"count": int(selected)})
        record = feature(2025)
        if query["returnGeometry"] == "true":
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [record]})
        if selected:
            attribute_reads += 1
            properties = dict(record["properties"])
            if attribute_reads > 1:
                properties["fire_name"] = "REVISED"
            return httpx.Response(200, json={"features": [{"attributes": properties}]})
        return httpx.Response(200, json={"features": []})

    monkeypatch.setattr(
        capture.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)
    )
    with pytest.raises(ValueError, match="changed its source inventory"):
        capture.capture_snapshot(tmp_path / "capture")
    assert not (tmp_path / "capture" / "manifest.json").exists()


def test_prepare_refuses_manifest_hash_before_reading_source(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed SHA-256"):
        capture.prepare_capture(tmp_path, "a" * 64, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("empty", [False, True])
def test_successful_capture_replays_offline_and_prepares_all_rungs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, empty: bool
) -> None:
    original_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        selected = query["where"] == "year = 2025" and not empty
        if query.get("returnCountOnly"):
            return httpx.Response(200, json={"count": int(selected)})
        rows = [feature(2025)] if selected else []
        if query["returnGeometry"] == "false":
            rows = [{"attributes": row["properties"]} for row in rows]
        return httpx.Response(200, json={"features": rows})

    monkeypatch.setattr(
        capture.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)
    )
    result = capture.capture_snapshot(tmp_path / "capture")

    def no_network(**_kwargs: object) -> None:
        pytest.fail("offline preparation attempted network access")

    monkeypatch.setattr(capture.httpx, "Client", no_network)
    receipt = capture.prepare_capture(tmp_path / "capture", result["manifest_sha256"], tmp_path / "prepared")
    assert receipt["apply_authority"] is False
    assert {rung["zoom"] for rung in receipt["rungs"]} == {0, 5, 9, 13}
    for rung in receipt["rungs"]:
        table = pq.read_table(tmp_path / "prepared" / "blobs" / rung["sha256"])
        assert table.num_rows == int(not empty)
        if not empty:
            assert table["fire_name"].to_pylist() == ["POWERLINE"]
            assert table["release_identifier"].to_pylist() == [f"mtbs-current-snapshot:{result['manifest_sha256']}"]


def test_prepare_rejects_huge_count_before_allocating_page_graph(tmp_path: Path) -> None:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    manifest = make_source_manifest(
        bbox=(-125, 42, -111, 49),
        years=tuple(range(2018, 2027)),
        captured_from=now,
        captured_through=now,
        counts=dict.fromkeys(range(2018, 2027), 0),
        responses=[],
        source_content_sha256=digest(b""),
    )
    manifest["counts_by_year"]["2025"] = 10**15
    raw = canonical_bytes(manifest)
    (tmp_path / "manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="excessive counts"):
        capture.prepare_capture(tmp_path, digest(raw), tmp_path / "prepared")


def test_capture_refuses_transient_geometry_property_revision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    original_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        selected = query["where"] == "year = 2025"
        if query.get("returnCountOnly"):
            return httpx.Response(200, json={"count": int(selected)})
        row = feature(2025)
        if query["returnGeometry"] == "true":
            row["properties"]["fire_name"] = "TRANSIENT REVISION"
            return httpx.Response(200, json={"features": [row]})
        return httpx.Response(200, json={"features": [{"attributes": row["properties"]}] if selected else []})

    monkeypatch.setattr(
        capture.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)
    )
    with pytest.raises(ValueError, match="geometry properties differ"):
        capture.capture_snapshot(tmp_path / "capture")
    assert (tmp_path / "capture" / "capture-journal.json").exists()
    assert not (tmp_path / "capture" / "manifest.json").exists()


def test_edw_coordinate_serialization_tail_is_the_only_property_tolerance() -> None:
    pinned = [{"longitude": -122.52854279, "latitude": 45.0, "acres": 1100.0}]
    observed = [{"longitude": -122.52854278999999, "latitude": 45.0, "acres": 1100.0}]
    assert capture.attributes_match(observed, pinned)
    assert not capture.attributes_match([{**observed[0], "longitude": -122.52854278}], pinned)
    assert not capture.attributes_match([{**observed[0], "acres": 1100.0000000001}], pinned)
    assert not capture.attributes_match([{**observed[0], "new_field": None}], pinned)
    assert capture.attributes_match([{**observed[0], "acres": 1100}], pinned)
    assert not capture.attributes_match([{**observed[0], "acres": "1100"}], pinned)
    assert not capture.attributes_match([{"acres": True}], [{"acres": 1}])
