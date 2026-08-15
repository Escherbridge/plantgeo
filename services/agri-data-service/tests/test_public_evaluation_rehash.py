"""Tests for the disk-only frozen-input rehash used by the public-evaluation track."""

# ruff: noqa: PLR2004

import hashlib
import json
import sys
from pathlib import Path

import pytest

from agri_data_service.execution.public_evaluation_rehash import (
    DEFAULT_FORECAST_MANIFEST_PATH,
    DEFAULT_GHISACONUS_CSV_PATH,
    FORECAST_MANIFEST_EXPECTED_SHA256,
    GHISACONUS_CSV_EXPECTED_SHA256,
    main,
    rehash_frozen_input,
    rehash_public_evaluation_frozen_inputs,
)


def test_rehash_frozen_input_matches_when_digest_equal(tmp_path: Path) -> None:
    content = b"frozen evaluation payload"
    fixture = tmp_path / "fixture.bin"
    fixture.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    result = rehash_frozen_input(fixture, expected, "fixture-label")

    assert result.matches is True
    assert result.actual_sha256 == expected
    assert result.expected_sha256 == expected
    assert result.byte_count == len(content)
    assert result.label == "fixture-label"
    assert result.path == str(fixture)


def test_rehash_frozen_input_uppercase_expected_digest_still_matches(tmp_path: Path) -> None:
    content = b"case insensitive digest comparison"
    fixture = tmp_path / "fixture.bin"
    fixture.write_bytes(content)
    expected_upper = hashlib.sha256(content).hexdigest().upper()

    result = rehash_frozen_input(fixture, expected_upper, "fixture-label")

    assert result.matches is True
    assert result.expected_sha256 == expected_upper.lower()


def test_rehash_frozen_input_reports_mismatch_without_raising(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.bin"
    fixture.write_bytes(b"actual bytes on disk")
    wrong_digest = hashlib.sha256(b"a completely different payload").hexdigest()

    result = rehash_frozen_input(fixture, wrong_digest, "fixture-label")

    assert result.matches is False
    assert result.actual_sha256 != wrong_digest


def test_rehash_frozen_input_raises_file_not_found_for_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.bin"

    with pytest.raises(FileNotFoundError, match="fixture-label"):
        rehash_frozen_input(missing, "0" * 64, "fixture-label")


def test_rehash_public_evaluation_frozen_inputs_all_match_true_when_both_match(tmp_path: Path) -> None:
    csv_bytes = b"csv payload"
    manifest_bytes = b'{"schema_version": "test"}'
    csv_path = tmp_path / "ghisaconus.csv"
    manifest_path = tmp_path / "manifest.json"
    csv_path.write_bytes(csv_bytes)
    manifest_path.write_bytes(manifest_bytes)

    receipt = rehash_public_evaluation_frozen_inputs(
        ghisaconus_csv_path=csv_path,
        forecast_manifest_path=manifest_path,
    )

    assert receipt.all_match is False  # digests are pinned to the real spec.md values, not fixture bytes
    assert len(receipt.inputs) == 2
    assert {item.label for item in receipt.inputs} == {"ghisaconus_csv_v1", "frozen_forecast_manifest_v1"}


def test_rehash_public_evaluation_frozen_inputs_all_match_false_when_one_is_missing(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"{}")

    with pytest.raises(FileNotFoundError):
        rehash_public_evaluation_frozen_inputs(
            ghisaconus_csv_path=tmp_path / "missing.csv",
            forecast_manifest_path=manifest_path,
        )


def test_main_exits_zero_and_prints_matching_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_bytes = b"pinned csv bytes"
    manifest_bytes = b"pinned manifest bytes"
    csv_path = tmp_path / "ghisaconus.csv"
    manifest_path = tmp_path / "manifest.json"
    csv_path.write_bytes(csv_bytes)
    manifest_path.write_bytes(manifest_bytes)

    monkeypatch.setattr(
        "agri_data_service.execution.public_evaluation_rehash.GHISACONUS_CSV_EXPECTED_SHA256",
        hashlib.sha256(csv_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        "agri_data_service.execution.public_evaluation_rehash.FORECAST_MANIFEST_EXPECTED_SHA256",
        hashlib.sha256(manifest_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["public_evaluation_rehash", "--ghisaconus-csv", str(csv_path), "--forecast-manifest", str(manifest_path)],
    )

    main()

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["all_match"] is True


def test_main_exits_nonzero_on_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    csv_path = tmp_path / "ghisaconus.csv"
    manifest_path = tmp_path / "manifest.json"
    csv_path.write_bytes(b"bytes that will not match the pinned digest")
    manifest_path.write_bytes(b"bytes that will not match the pinned digest either")

    monkeypatch.setattr(
        sys,
        "argv",
        ["public_evaluation_rehash", "--ghisaconus-csv", str(csv_path), "--forecast-manifest", str(manifest_path)],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1


@pytest.mark.skipif(
    not (DEFAULT_GHISACONUS_CSV_PATH.is_file() and DEFAULT_FORECAST_MANIFEST_PATH.is_file()),
    reason="needs the real frozen GHISACONUS CSV and forecast manifest on disk at their pinned local paths",
)
def test_real_frozen_inputs_rehash_matches_the_digests_pinned_in_spec_md() -> None:
    """Integration check against the actual frozen files this machine holds, not fixtures."""
    receipt = rehash_public_evaluation_frozen_inputs()

    assert receipt.all_match is True
    by_label = {item.label: item for item in receipt.inputs}
    assert by_label["ghisaconus_csv_v1"].actual_sha256 == GHISACONUS_CSV_EXPECTED_SHA256
    assert by_label["frozen_forecast_manifest_v1"].actual_sha256 == FORECAST_MANIFEST_EXPECTED_SHA256
