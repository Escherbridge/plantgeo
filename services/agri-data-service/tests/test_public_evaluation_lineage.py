"""Tests for the GHISACONUS lineage loader's pure parts, with the DB mocked out."""

# ruff: noqa: PLR2004

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agri_data_service.execution import public_evaluation_lineage as lineage_module
from agri_data_service.execution.public_evaluation_lineage import (
    EXPECTED_ARTIFACT_COUNT,
    GhisaconusLineageVerificationCounts,
    GhisaconusRehashResults,
    build_ghisaconus_lineage_fields,
    persist_ghisaconus_lineage,
    rehash_ghisaconus_lineage_inputs,
)
from agri_data_service.execution.public_evaluation_rehash import FrozenInputRehash
from agri_data_service.models.provenance import ReleaseSetState


def _matching_input(label: str, path: str = "C:\\fixture\\path", byte_count: int = 100) -> FrozenInputRehash:
    digest = "a" * 64
    return FrozenInputRehash(
        label=label,
        path=path,
        expected_sha256=digest,
        actual_sha256=digest,
        byte_count=byte_count,
        matches=True,
        checked_at="2026-08-14T00:00:00+00:00",
    )


def _mismatched_input(label: str) -> FrozenInputRehash:
    return FrozenInputRehash(
        label=label,
        path="C:\\fixture\\path",
        expected_sha256="a" * 64,
        actual_sha256="b" * 64,
        byte_count=100,
        matches=False,
        checked_at="2026-08-14T00:00:00+00:00",
    )


def _sample_rehash() -> GhisaconusRehashResults:
    return GhisaconusRehashResults(
        csv=_matching_input("ghisaconus_csv_v1", path="C:\\fixture\\csv.csv", byte_count=11_540_638),
        archive=_matching_input("ghisaconus_archive_v1", path="C:\\fixture\\archive.zip", byte_count=5_225_446),
        metadata=_matching_input("ghisaconus_metadata_v1", path="C:\\fixture\\metadata.json", byte_count=8_877),
    )


# --- digest / rehash gate -----------------------------------------------------------------------


def test_rehash_ghisaconus_lineage_inputs_reads_all_three_local_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_bytes = b"csv payload"
    archive_bytes = b"archive payload"
    metadata_bytes = b"metadata payload"
    csv_path = tmp_path / "ghisaconus.csv"
    archive_path = tmp_path / "ghisaconus.zip"
    metadata_path = tmp_path / "metadata.json"
    csv_path.write_bytes(csv_bytes)
    archive_path.write_bytes(archive_bytes)
    metadata_path.write_bytes(metadata_bytes)

    monkeypatch.setattr(lineage_module, "GHISACONUS_CSV_EXPECTED_SHA256", hashlib.sha256(csv_bytes).hexdigest())
    monkeypatch.setattr(lineage_module, "GHISACONUS_ARCHIVE_EXPECTED_SHA256", hashlib.sha256(archive_bytes).hexdigest())
    monkeypatch.setattr(
        lineage_module, "GHISACONUS_METADATA_EXPECTED_SHA256", hashlib.sha256(metadata_bytes).hexdigest()
    )

    result = rehash_ghisaconus_lineage_inputs(csv_path, archive_path, metadata_path)

    assert result.csv.matches is True
    assert result.archive.matches is True
    assert result.metadata.matches is True


def test_require_all_match_raises_on_any_single_mismatch() -> None:
    results = GhisaconusRehashResults(
        csv=_matching_input("ghisaconus_csv_v1"),
        archive=_mismatched_input("ghisaconus_archive_v1"),
        metadata=_matching_input("ghisaconus_metadata_v1"),
    )

    with pytest.raises(ValueError, match="ghisaconus_archive_v1"):
        results.require_all_match()


def test_require_all_match_passes_when_all_three_match() -> None:
    _sample_rehash().require_all_match()  # must not raise


# --- pure row construction -----------------------------------------------------------------------


def test_build_ghisaconus_lineage_fields_refuses_on_mismatch() -> None:
    results = GhisaconusRehashResults(
        csv=_mismatched_input("ghisaconus_csv_v1"),
        archive=_matching_input("ghisaconus_archive_v1"),
        metadata=_matching_input("ghisaconus_metadata_v1"),
    )

    with pytest.raises(ValueError, match="ghisaconus_csv_v1"):
        build_ghisaconus_lineage_fields(results)


def test_build_ghisaconus_lineage_fields_uses_measured_checksums_and_byte_counts() -> None:
    rehash = _sample_rehash()

    fields = build_ghisaconus_lineage_fields(rehash)

    assert fields.data_source["key"] == "kaggle-ghisaconus-mirror"
    assert fields.data_source["review_state"].value == "approved"
    assert isinstance(fields.data_source["reviewed_at"], datetime)
    assert fields.data_source["reviewed_at"].tzinfo is not None

    assert fields.source_release["payload_checksum"] == rehash.csv.actual_sha256
    assert fields.source_release["payload_bytes"] == rehash.csv.byte_count == 11_540_638
    assert isinstance(fields.source_release["observed_from"], datetime)
    assert isinstance(fields.source_release["observed_to"], datetime)
    assert fields.source_release["observed_to"] >= fields.source_release["observed_from"]
    assert fields.source_release["validation_state"].value == "valid"

    assert fields.release_set["logical_key"] == "ghisaconus-v1-public-benchmark-20260726"
    assert isinstance(fields.release_set["as_of_time"], datetime)

    assert len(fields.artifacts) == EXPECTED_ARTIFACT_COUNT
    by_kind = {artifact["kind"]: artifact for artifact in fields.artifacts}
    assert by_kind["source_csv"]["checksum_sha256"] == rehash.csv.actual_sha256
    assert by_kind["source_csv"]["size_bytes"] == rehash.csv.byte_count
    assert by_kind["source_archive"]["checksum_sha256"] == rehash.archive.actual_sha256
    assert by_kind["source_metadata"]["checksum_sha256"] == rehash.metadata.actual_sha256
    for artifact in fields.artifacts:
        assert artifact["storage_class"] == "local_raw_cache"
        assert artifact["content_bytes"] is None
        assert artifact["metadata_json"]["local_path"]  # a real local pointer, not dropped bytes
        # `artifact["uri"]` is a `warehouse://public-benchmarks/...` locator that resolves nowhere;
        # this is the one place each artifact carries a real, fetchable upstream pointer.
        assert artifact["metadata_json"]["upstream_source_url"] == fields.data_source["base_url"]


def test_build_ghisaconus_lineage_fields_uses_utc_aware_datetimes() -> None:
    fields = build_ghisaconus_lineage_fields(_sample_rehash())

    assert fields.source_release["observed_from"] == datetime(2008, 1, 1, tzinfo=UTC)
    assert fields.source_release["observed_to"] == datetime(2015, 12, 31, 23, 59, 59, tzinfo=UTC)


# --- verification-count predicate -------------------------------------------------------------


def _counts(**overrides: object) -> GhisaconusLineageVerificationCounts:
    base = {
        "data_source_count": 1,
        "source_release_count": 1,
        "release_set_count": 1,
        "release_set_item_count": 1,
        "artifact_count": EXPECTED_ARTIFACT_COUNT,
        "release_set_state": "validated",
    }
    base.update(overrides)
    return GhisaconusLineageVerificationCounts(**base)  # type: ignore[arg-type]


def test_verification_counts_is_complete_when_everything_matches() -> None:
    assert _counts().is_complete is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"data_source_count": 0},
        {"source_release_count": 2},
        {"release_set_item_count": 0},
        {"artifact_count": 2},
        {"release_set_state": "draft"},
    ],
)
def test_verification_counts_is_incomplete_on_any_single_deviation(overrides: dict[str, object]) -> None:
    assert _counts(**overrides).is_complete is False


# --- persist orchestration, DB mocked out -------------------------------------------------------


async def test_persist_ghisaconus_lineage_orchestrates_ensure_helpers_and_returns_a_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields = build_ghisaconus_lineage_fields(_sample_rehash())
    source_id = uuid.uuid4()
    release_id = uuid.uuid4()
    release_set_id = uuid.uuid4()
    artifact_ids = [uuid.uuid4() for _ in fields.artifacts]

    fake_ensure_data_source = AsyncMock(
        return_value=(SimpleNamespace(id=source_id, key="kaggle-ghisaconus-mirror"), False)
    )
    fake_ensure_source_release = AsyncMock(return_value=(SimpleNamespace(id=release_id), False))
    fake_ensure_artifact = AsyncMock(side_effect=[(SimpleNamespace(id=aid), False) for aid in artifact_ids])
    fake_release_set = SimpleNamespace(
        id=release_set_id, logical_key="ghisaconus-v1-public-benchmark-20260726", state=ReleaseSetState.VALIDATED
    )
    fake_ensure_release_set = AsyncMock(return_value=(fake_release_set, False))
    fake_verify = AsyncMock(
        return_value=GhisaconusLineageVerificationCounts(
            data_source_count=1,
            source_release_count=1,
            release_set_count=1,
            release_set_item_count=1,
            artifact_count=EXPECTED_ARTIFACT_COUNT,
            release_set_state="validated",
        )
    )

    monkeypatch.setattr(lineage_module, "ensure_data_source", fake_ensure_data_source)
    monkeypatch.setattr(lineage_module, "ensure_source_release", fake_ensure_source_release)
    monkeypatch.setattr(lineage_module, "ensure_artifact", fake_ensure_artifact)
    monkeypatch.setattr(lineage_module, "_ensure_ghisaconus_release_set", fake_ensure_release_set)
    monkeypatch.setattr(lineage_module, "_verify_ghisaconus_lineage", fake_verify)

    fake_session = AsyncMock()
    receipt = await persist_ghisaconus_lineage(fake_session, fields)

    fake_ensure_data_source.assert_awaited_once()
    fake_ensure_source_release.assert_awaited_once()
    assert fake_ensure_artifact.await_count == EXPECTED_ARTIFACT_COUNT
    fake_ensure_release_set.assert_awaited_once()
    fake_verify.assert_awaited_once_with(
        fake_session, "kaggle-ghisaconus-mirror", "ghisaconus-v1-public-benchmark-20260726"
    )

    assert receipt.data_source_id == str(source_id)
    assert receipt.source_release_id == str(release_id)
    assert receipt.release_set_id == str(release_set_id)
    assert receipt.release_set_state == "validated"
    assert set(receipt.artifact_ids) == {"source_csv", "source_archive", "source_metadata"}
    assert receipt.verification.is_complete is True


async def test_persist_ghisaconus_lineage_refuses_to_return_on_incomplete_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields = build_ghisaconus_lineage_fields(_sample_rehash())

    monkeypatch.setattr(
        lineage_module,
        "ensure_data_source",
        AsyncMock(return_value=(SimpleNamespace(id=uuid.uuid4(), key="kaggle-ghisaconus-mirror"), False)),
    )
    monkeypatch.setattr(
        lineage_module, "ensure_source_release", AsyncMock(return_value=(SimpleNamespace(id=uuid.uuid4()), False))
    )
    monkeypatch.setattr(
        lineage_module, "ensure_artifact", AsyncMock(return_value=(SimpleNamespace(id=uuid.uuid4()), False))
    )
    monkeypatch.setattr(
        lineage_module,
        "_ensure_ghisaconus_release_set",
        AsyncMock(
            return_value=(
                SimpleNamespace(
                    id=uuid.uuid4(),
                    logical_key="ghisaconus-v1-public-benchmark-20260726",
                    state=ReleaseSetState.VALIDATED,
                ),
                False,
            )
        ),
    )
    monkeypatch.setattr(
        lineage_module,
        "_verify_ghisaconus_lineage",
        AsyncMock(
            return_value=GhisaconusLineageVerificationCounts(
                data_source_count=1,
                source_release_count=1,
                release_set_count=1,
                release_set_item_count=0,  # short one row: not complete
                artifact_count=EXPECTED_ARTIFACT_COUNT,
                release_set_state="validated",
            )
        ),
    )

    with pytest.raises(ValueError, match="verification incomplete"):
        await persist_ghisaconus_lineage(AsyncMock(), fields)
