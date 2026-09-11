"""Older MTBS recovery remains bounded, reproducible and isolated from current admission."""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

import duckdb
import httpx
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.ingest.mtbs import MtbsReleaseNotPublishedError, build_mtbs_record
from agri_data_service.pipeline.direct.burn_severity import capture, older_capture, older_recovery
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import (
    canonical_bytes,
    digest,
    validate_source_manifest,
)
from agri_data_service.pipeline.direct.burn_severity.older_capture import (
    OLDER_YEARS,
    capture_older_population,
    validate_older_capture,
    validate_older_manifest,
)
from agri_data_service.pipeline.direct.burn_severity.older_recovery import (
    prepare_older_capture,
    verify_current_preservation,
    write_recovery_packet,
)

if TYPE_CHECKING:
    from pathlib import Path

OLDER_TEST_YEARS = (OLDER_YEARS[0], OLDER_YEARS[-1])


@pytest.mark.parametrize("operation", ["older", "current_preservation"])
def test_offline_replay_refuses_missing_spatial_without_installing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str
) -> None:
    """A missing local extension stops before source replay or output creation."""
    statements: list[str] = []

    class MissingSpatial:
        def __enter__(self) -> MissingSpatial:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: str) -> None:
            statements.append(statement)
            raise duckdb.IOException("spatial is not installed")

    def connect(*, config: dict[str, bool]) -> MissingSpatial:
        assert config == {"autoinstall_known_extensions": False, "autoload_known_extensions": False}
        return MissingSpatial()

    monkeypatch.setattr(older_recovery.duckdb, "connect", connect)
    monkeypatch.setattr(older_recovery, "extension_directory_setting", lambda: None)
    replay = (
        partial(prepare_older_capture, tmp_path / "capture", "0" * 64, tmp_path / "output")
        if operation == "older"
        else partial(verify_current_preservation, tmp_path / "capture", tmp_path / "prepared", "0" * 64)
    )
    with pytest.raises(ValueError, match="requires an installed DuckDB spatial extension"):
        replay()
    assert statements == ["LOAD spatial"]
    assert not (tmp_path / "output").exists()


def _feature(year: int) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "fire_id": f"ID42627114403{year}0906",
            "year": year,
            "ig_date": year * 10000 + 906,
            "fire_name": "PRESERVED",
            "fire_type": "Wildfire",
            "acres": None,
            "map_id": 10029267,
            "asmnt_type": "Initial",
        },
        "geometry": {"type": "Polygon", "coordinates": [[[-120, 45], [-119.9, 45], [-119.9, 45.1], [-120, 45]]]},
    }


def _source(monkeypatch: pytest.MonkeyPatch, *, changed: bool = False, empty: bool = False) -> None:
    original_client = httpx.Client
    attribute_reads: dict[int, int] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        assert query["geometry"] == "-125.0,42.0,-111.0,49.0"
        year = int(query["where"].split(" = ")[1])
        selected = year in {1984, 2017, 2025} and not empty
        if query.get("returnCountOnly"):
            return httpx.Response(200, json={"count": int(selected)})
        rows = [_feature(year)] if selected else []
        if query["returnGeometry"] == "false":
            attribute_reads[year] = attribute_reads.get(year, 0) + 1
            if changed and year == OLDER_YEARS[0] and attribute_reads[year] > 1:
                rows[0]["properties"]["fire_name"] = "MUTATED"
            rows = [{"attributes": row["properties"]} for row in rows]
        return httpx.Response(200, json={"features": rows})

    monkeypatch.setattr(
        older_capture.httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )


def test_older_capture_replays_exact_horizon_and_is_rejected_by_current_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _source(monkeypatch)
    result = capture_older_population(tmp_path / "older")
    manifest, features = validate_older_capture(tmp_path / "older", str(result["manifest_sha256"]))
    assert manifest["source_row_count"] == len(features) == len(OLDER_TEST_YEARS)
    assert set(manifest["counts_by_year"]) == {str(year) for year in range(1984, 2018)}
    assert {feature["properties"]["year"] for feature in features} == {1984, 2017}
    assert date.fromisoformat(manifest["available_day"]) == date.fromisoformat(
        manifest["captured_through"][:10]
    ) + timedelta(days=1)
    assert manifest["mode"] == "full_replacement"
    assert manifest["upstream_population_complete"] is False
    assert manifest["fire_season_completeness"] == "not_assessed"
    with pytest.raises(ValueError, match="horizon"):
        validate_source_manifest(manifest)
    with pytest.raises(MtbsReleaseNotPublishedError):
        build_mtbs_record(_feature(1984), 1984)


@pytest.mark.parametrize(
    ("field", "value"), [("available_day", "1984-09-07"), ("capture_complete", False), ("bbox", [-125, 42, -112, 49])]
)
def test_older_manifest_refuses_scope_and_knowledge_time_forgery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, value: object
) -> None:
    _source(monkeypatch)
    result = capture_older_population(tmp_path / "older")
    manifest, _features = validate_older_capture(tmp_path / "older", str(result["manifest_sha256"]))
    manifest[field] = value
    with pytest.raises(ValueError, match="exact recovery contract"):
        validate_older_manifest(manifest)


def test_older_capture_refuses_changed_source_without_complete_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _source(monkeypatch, changed=True)
    with pytest.raises(ValueError, match="inventory changed"):
        capture_older_population(tmp_path / "older")
    assert not (tmp_path / "older" / "manifest.json").exists()
    assert json.loads((tmp_path / "older" / "capture-journal.json").read_bytes())["status"] == "failed"


def test_older_capture_replay_refuses_missing_receipt_and_mutated_blob(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _source(monkeypatch)
    result = capture_older_population(tmp_path / "older")
    manifest_path = tmp_path / "older" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    removed = manifest["responses"].pop()
    raw = canonical_bytes(manifest)
    manifest_path.write_bytes(raw)
    with pytest.raises(ValueError, match="incomplete or out of order"):
        validate_older_capture(tmp_path / "older", digest(raw))
    manifest["responses"].append(removed)
    manifest_path.write_bytes(canonical_bytes(manifest))
    source_blob = tmp_path / "older" / "blobs" / manifest["responses"][0]["sha256"]
    source_blob.write_bytes(b"changed")
    with pytest.raises(ValueError, match="blob identity"):
        validate_older_capture(tmp_path / "older", str(result["manifest_sha256"]))


def test_empty_older_capture_is_replacement_evidence_not_governed_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _source(monkeypatch, empty=True)
    result = capture_older_population(tmp_path / "older")
    candidate = prepare_older_capture(tmp_path / "older", str(result["manifest_sha256"]), tmp_path / "prepared")
    assert candidate["descriptor"]["mode"] == "full_replacement"
    assert candidate["descriptor"]["source_row_count"] == 0
    assert candidate["publication_status"] == "not_admitted"
    assert all(rung["rows"] == 0 for rung in candidate["rungs"])
    assert not list((tmp_path / "prepared").rglob("*_ABSENT*"))


def test_local_candidates_preserve_all_rungs_current_scope_and_refuse_changed_packet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _source(monkeypatch)
    older = capture_older_population(tmp_path / "older")
    current = capture.capture_snapshot(tmp_path / "current")
    capture.prepare_capture(tmp_path / "current", str(current["manifest_sha256"]), tmp_path / "current-prepared")

    def no_network(**_kwargs: object) -> None:
        pytest.fail("offline recovery attempted a network request")

    monkeypatch.setattr(older_capture.httpx, "Client", no_network)
    preservation = verify_current_preservation(
        tmp_path / "current", tmp_path / "current-prepared", str(current["manifest_sha256"])
    )
    candidate = prepare_older_capture(tmp_path / "older", str(older["manifest_sha256"]), tmp_path / "prepared")
    assert candidate["apply_authority"] is False
    for rung in candidate["rungs"]:
        assert rung["rows"] == len(OLDER_TEST_YEARS)
        assert [part["fire_year"] for part in rung["parts"]] == list(OLDER_YEARS)
        for part in rung["parts"]:
            table = pq.read_table(tmp_path / "prepared" / "blobs" / part["sha256"])
            assert table.num_rows == int(part["fire_year"] in {1984, 2017})
            if table.num_rows:
                assert table["acres"].to_pylist() == [None]
                assert table["release_identifier"].to_pylist() == [f"mtbs-older-recovery:{older['manifest_sha256']}"]
    packet = write_recovery_packet(tmp_path / "prepared", preservation, captured=tmp_path / "older")
    assert packet["status"] == "candidate_prepared_admission_blocked"
    assert packet["current_snapshot_preservation"]["descriptor"]["covered_years"] == {"from": 2018, "to": 2026}
    assert packet["current_snapshot_preservation"]["descriptor"]["partial_fire_years"] == [2023, 2024, 2025, 2026]
    assert packet["rollback"]["destructive_action_authorized"] is False
    candidate["rungs"].pop()
    (tmp_path / "prepared" / "preparation.json").write_bytes(canonical_bytes(candidate))
    with pytest.raises(ValueError, match="incomplete ladder"):
        write_recovery_packet(tmp_path / "prepared", preservation, captured=tmp_path / "older")


def test_current_preservation_refuses_changed_saved_blob(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _source(monkeypatch)
    current = capture.capture_snapshot(tmp_path / "current")
    prepared = capture.prepare_capture(
        tmp_path / "current", str(current["manifest_sha256"]), tmp_path / "current-prepared"
    )
    rung = prepared["rungs"][0]
    (tmp_path / "current-prepared" / "blobs" / rung["sha256"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="blob identity"):
        verify_current_preservation(
            tmp_path / "current", tmp_path / "current-prepared", str(current["manifest_sha256"])
        )
