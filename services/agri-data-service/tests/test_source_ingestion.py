"""Contracts for the bounded local source-release vertical slice."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from agri_data_service.cli import cli
from agri_data_service.config import settings
from agri_data_service.execution.source_ingestion import (
    SourceIngestionCheckpoint,
    SourceIngestionPlan,
    checkpoint_path,
    is_finalized_release_set_state,
    load_and_validate_geojson,
    load_checkpoint,
    release_set_manifest,
    source_ingestion_plan_checksum,
    write_checkpoint,
)
from agri_data_service.models.provenance import ReleaseSetState


def _plan() -> SourceIngestionPlan:
    timestamp = datetime(2026, 7, 20, tzinfo=UTC)
    return SourceIngestionPlan.model_validate(
        {
            "source": {
                "key": "nasa-firms",
                "name": "NASA FIRMS",
                "owner": "NASA",
                "purpose": "Current fire observations",
                "license_name": "Public data policy",
                "citation": "NASA FIRMS",
                "reviewed_at": timestamp.isoformat(),
                "reviewed_by": "data-governance",
            },
            "release": {
                "source_version": "2026-07-20T00:00:00Z",
                "schema_version": "firms-geojson-v1",
                "data_available_at": timestamp.isoformat(),
            },
            "release_set_key": "nasa-firms-20260720",
            "release_set_as_of": timestamp.isoformat(),
        }
    )


def _write_payload(path: Path) -> bytes:
    value = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-105.2, 39.7]},
                "properties": {"brightness": 301.2},
            }
        ],
    }
    payload = json.dumps(value, separators=(",", ":")).encode()
    path.write_bytes(payload)
    return payload


def test_local_source_release_has_deterministic_identity_and_checkpoint(tmp_path: Path) -> None:
    plan = _plan()
    payload = _write_payload(tmp_path / "release.geojson")
    loaded, quality = load_and_validate_geojson(tmp_path / "release.geojson")
    checksum = hashlib.sha256(payload).hexdigest()
    path = checkpoint_path(tmp_path, plan, checksum)

    assert loaded == payload
    assert quality == {"feature_count": 1, "point_count": 1}
    assert path == checkpoint_path(tmp_path, plan, checksum)
    assert release_set_manifest(plan, checksum) == release_set_manifest(plan, checksum)

    checkpoint = SourceIngestionCheckpoint(
        state="validated",
        source_key=plan.source.key,
        source_version=plan.release.source_version,
        payload_checksum=checksum,
        payload_bytes=len(payload),
        updated_at=datetime(2026, 7, 20, tzinfo=UTC),
        plan_checksum=source_ingestion_plan_checksum(plan),
        release_set_manifest_checksum=release_set_manifest(plan, checksum),
    )
    write_checkpoint(path, checkpoint)
    assert load_checkpoint(path) == checkpoint

    status = CliRunner().invoke(cli, ["pipeline-status", "--checkpoint", str(path)])
    assert status.exit_code == 0
    assert '"state": "runnable"' in status.output
    assert source_ingestion_plan_checksum(plan) in status.output


def test_checkpoint_path_and_receipt_bind_the_complete_reviewed_plan(tmp_path: Path) -> None:
    plan = _plan()
    payload = _write_payload(tmp_path / "release.geojson")
    changed_plan_data = plan.model_dump(mode="json")
    changed_plan_data["description"] = "Corrected reviewed source-release description"
    changed_plan = SourceIngestionPlan.model_validate(changed_plan_data)
    checksum = hashlib.sha256(payload).hexdigest()

    assert source_ingestion_plan_checksum(plan) != source_ingestion_plan_checksum(changed_plan)
    assert checkpoint_path(tmp_path, plan, checksum) != checkpoint_path(tmp_path, changed_plan, checksum)


def test_source_release_rejects_non_finite_and_boolean_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "invalid-numbers.geojson"
    path.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":'
        '{"type":"Point","coordinates":[NaN,39.7]},"properties":{}}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="UTF-8 JSON"):
        load_and_validate_geojson(path)

    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [True, 39.7]},
                        "properties": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="finite WGS84"):
        load_and_validate_geojson(path)


def test_source_release_rejects_sensitive_raw_artifact_values_before_checksum(tmp_path: Path) -> None:
    path = tmp_path / "sensitive.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-105.2, 39.7]},
                        "properties": {"nested": {"x-api-key": "must-not-reach-an-artifact"}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sensitive field"):
        load_and_validate_geojson(path)

    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-105.2, 39.7]},
                        "properties": {"providerApiKey": "must-not-reach-an-artifact"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sensitive field"):
        load_and_validate_geojson(path)

    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-105.2, 39.7]},
                        "properties": {"accessToken": "must-not-reach-an-artifact"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sensitive field"):
        load_and_validate_geojson(path)

    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-105.2, 39.7]},
                        "properties": {"clientSecret": "must-not-reach-an-artifact"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sensitive field"):
        load_and_validate_geojson(path)

    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-105.2, 39.7]},
                        "properties": {"note": "Bearer must-not-reach-an-artifact"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="authorization value"):
        load_and_validate_geojson(path)


def test_source_release_plan_rejects_credentials_and_reversed_observation_window() -> None:
    plan_data = _plan().model_dump(mode="json")
    plan_data["release"]["query_parameters"] = {"api_key": "must-not-be-persisted"}

    with pytest.raises(ValueError, match="sensitive field"):
        SourceIngestionPlan.model_validate(plan_data)

    plan_data = _plan().model_dump(mode="json")
    plan_data["source"]["base_url"] = "https://user:password@example.test/feed"

    with pytest.raises(ValueError, match="URLs with credentials"):
        SourceIngestionPlan.model_validate(plan_data)

    plan_data = _plan().model_dump(mode="json")
    plan_data["source"]["license_url"] = "https://example.test/license?api_key=must-not-be-persisted"

    with pytest.raises(ValueError, match="credential query"):
        SourceIngestionPlan.model_validate(plan_data)

    plan_data = _plan().model_dump(mode="json")
    plan_data["source"]["base_url"] = "https://example.test/feed#access_token=must-not-be-persisted"

    with pytest.raises(ValueError, match="credential query"):
        SourceIngestionPlan.model_validate(plan_data)

    plan_data = _plan().model_dump(mode="json")
    plan_data["release"]["query_parameters"] = {
        "request_url": "https://example.test/feed?api_key=must-not-be-persisted"
    }

    with pytest.raises(ValueError, match="credential query"):
        SourceIngestionPlan.model_validate(plan_data)

    plan_data["release"]["query_parameters"] = {}
    plan_data["release"]["observed_from"] = "2026-07-21T00:00:00Z"
    plan_data["release"]["observed_to"] = "2026-07-20T00:00:00Z"

    with pytest.raises(ValueError, match="observed_to"):
        SourceIngestionPlan.model_validate(plan_data)


def test_source_release_rejects_non_point_or_unbounded_payload(tmp_path: Path) -> None:
    path = tmp_path / "invalid.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": None, "properties": {}}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="only Point"):
        load_and_validate_geojson(path)


def test_source_ingestion_commands_expose_inactive_status_without_starting_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    monkeypatch.setattr(settings, "local_source_loader_database_url", None)

    assert runner.invoke(cli, ["source-ingest", "--help"]).exit_code == 0
    status = runner.invoke(cli, ["pipeline-status"])

    assert status.exit_code == 0
    assert '"state": "inactive"' in status.output
    assert "local_bulk_ingestion" in status.output
    assert "blocked" in status.output
    assert "no model, forecast, or waypoint outputs" in status.output


def test_source_ingest_fails_closed_without_a_dedicated_loader_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    payload_path = tmp_path / "payload.geojson"
    plan_path.write_text("{}", encoding="utf-8")
    payload_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "local_source_loader_database_url", None)

    result = CliRunner().invoke(cli, ["source-ingest", "--plan", str(plan_path), "--payload", str(payload_path)])

    assert result.exit_code != 0
    assert "LOCAL_SOURCE_LOADER_DATABASE_URL" in result.output


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ReleaseSetState.DRAFT, False),
        (ReleaseSetState.RETIRED, False),
        (ReleaseSetState.VALIDATED, True),
        (ReleaseSetState.PUBLISHED, True),
    ],
)
def test_source_ingestion_idempotency_requires_a_finalized_release_set(state: ReleaseSetState, expected: bool) -> None:
    assert is_finalized_release_set_state(state) is expected
