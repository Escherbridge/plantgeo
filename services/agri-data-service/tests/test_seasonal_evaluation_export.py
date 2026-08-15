"""Tests for the frozen, checksummed evaluation export and its verification."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from agri_data_service.execution.seasonal_evaluation_export import (
    MANIFEST_DIGEST_FILE_NAME,
    MANIFEST_FILE_NAME,
    OBSERVATION_FILE_NAME,
    ExportObservation,
    ExportScope,
    SeasonalExportError,
    SeasonalExportManifest,
    build_manifest,
    known_missing_inputs,
    load_observations_csv,
    render_float,
    summarize_series,
    verify_export,
    write_export,
)
from agri_data_service.execution.seasonal_evidence_report import ReleaseLineageRow

if TYPE_CHECKING:
    from pathlib import Path

FROZEN_AT = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
WINDOW_START = datetime(2022, 4, 30, tzinfo=UTC)
WINDOW_END = datetime(2022, 5, 10, tzinfo=UTC)
RELEASE_ID = UUID("11111111-1111-1111-1111-111111111111")


def _observation(day_offset: int, *, value: float | None = 1.25, signal: str = "wind_speed") -> ExportObservation:
    return ExportObservation(
        cell_key="na-sample:1deg:p044.00:m116.00",
        signal_name=signal,
        support_key="surface",
        observed_date=date(2022, 4, 30) + timedelta(days=day_offset),
        normalized_value=value,
        normalized_unit="m/s",
        is_observed=value is not None,
        quality_flag="accepted" if value is not None else "source_missing",
        data_available_at=datetime(2026, 8, 5, tzinfo=UTC),
        source_key="nasa-power-daily",
        source_release_id=RELEASE_ID,
        source_payload_checksum="a" * 64,
        source_version="v1",
        transform_version="t1",
    )


def _release() -> ReleaseLineageRow:
    return ReleaseLineageRow(
        source_key="nasa-power-daily",
        source_release_id=RELEASE_ID,
        source_version="v1",
        transform_version="t1",
        payload_checksum="a" * 64,
        validation_state="valid",
        schema_version="s1",
        license_snapshot_checksum="b" * 64,
        retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        release_data_available_at=datetime(2026, 8, 5, tzinfo=UTC),
        contributed_row_count=5,
        first_observed_date=date(2022, 4, 30),
        last_observed_date=date(2022, 5, 4),
        earliest_data_available_at=datetime(2026, 8, 5, tzinfo=UTC),
        latest_data_available_at=datetime(2026, 8, 5, tzinfo=UTC),
    )


def _build(observations: list[ExportObservation]) -> tuple[SeasonalExportManifest, str, str]:
    scope = ExportScope(
        export_key="test-export",
        frozen_at=FROZEN_AT,
        cell_keys=("na-sample:1deg:p044.00:m116.00",),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    return build_manifest(scope, observations, [_release()])


def test_render_float_round_trips_exactly() -> None:
    for value in (0.1, 1e-17, 12345.6789012345, -0.0):
        assert float(render_float(value)) == value
    assert render_float(None) == ""


def test_the_manifest_checksum_is_stable_across_identical_builds() -> None:
    observations = [_observation(offset) for offset in range(5)]
    first, _, _ = _build(observations)
    second, _, _ = _build(observations)
    assert first.manifest_checksum == second.manifest_checksum


def test_the_manifest_checksum_changes_when_a_value_changes() -> None:
    baseline, _, _ = _build([_observation(offset) for offset in range(5)])
    changed, _, _ = _build([_observation(offset, value=9.0 if offset == 2 else 1.25) for offset in range(5)])  # noqa: PLR2004
    assert baseline.manifest_checksum != changed.manifest_checksum


def test_an_empty_selection_refuses_to_freeze() -> None:
    with pytest.raises(SeasonalExportError, match="refusing to freeze an empty export"):
        _build([])


def test_series_summaries_record_gaps_and_availability_bounds() -> None:
    observations = [_observation(offset) for offset in (0, 1, 2, 5)]
    summaries = summarize_series(observations)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.observed_day_count == 4  # noqa: PLR2004
    assert summary.span_day_count == 6  # noqa: PLR2004
    assert summary.gap_day_count == 2  # noqa: PLR2004
    assert summary.earliest_data_available_at == datetime(2026, 8, 5, tzinfo=UTC)


def test_known_missing_inputs_names_gaps_and_null_values() -> None:
    observations = [_observation(offset) for offset in (0, 1, 4)]
    observations.append(_observation(5, value=None))
    notes = known_missing_inputs(summarize_series(observations))
    assert any("calendar day(s) with no observation" in note for note in notes)
    assert any("null value" in note for note in notes)


def test_a_written_export_verifies_and_round_trips(tmp_path: Path) -> None:
    observations = [_observation(offset) for offset in range(5)]
    manifest, observation_text, lineage_text = _build(observations)
    destination = tmp_path / "export"
    write_export(destination, manifest, observation_text, lineage_text)
    verified = verify_export(destination)
    assert verified.export_key == "test-export"
    assert verified.observation_row_count == 5  # noqa: PLR2004
    restored = load_observations_csv(destination)
    assert [row.observed_date for row in restored] == [row.observed_date for row in observations]
    assert [row.normalized_value for row in restored] == [row.normalized_value for row in observations]


def test_refuses_to_overwrite_an_existing_export(tmp_path: Path) -> None:
    manifest, observation_text, lineage_text = _build([_observation(0)])
    destination = tmp_path / "export"
    write_export(destination, manifest, observation_text, lineage_text)
    with pytest.raises(SeasonalExportError, match="never overwritten"):
        write_export(destination, manifest, observation_text, lineage_text)


def test_a_tampered_observation_file_fails_verification(tmp_path: Path) -> None:
    manifest, observation_text, lineage_text = _build([_observation(offset) for offset in range(3)])
    destination = tmp_path / "export"
    write_export(destination, manifest, observation_text, lineage_text)
    target = destination / OBSERVATION_FILE_NAME
    target.write_text(target.read_text(encoding="utf-8").replace("1.25", "9.99"), encoding="utf-8", newline="")
    with pytest.raises(SeasonalExportError, match=r"observations\.csv digest mismatch"):
        verify_export(destination)


def test_a_tampered_manifest_fails_verification(tmp_path: Path) -> None:
    manifest, observation_text, lineage_text = _build([_observation(0)])
    destination = tmp_path / "export"
    write_export(destination, manifest, observation_text, lineage_text)
    document = json.loads((destination / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    document["observation_row_count"] = 999
    (destination / MANIFEST_FILE_NAME).write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8", newline=""
    )
    with pytest.raises(SeasonalExportError, match="manifest digest mismatch"):
        verify_export(destination)


def test_the_digest_file_names_the_manifest_it_covers(tmp_path: Path) -> None:
    manifest, observation_text, lineage_text = _build([_observation(0)])
    destination = tmp_path / "export"
    write_export(destination, manifest, observation_text, lineage_text)
    line = (destination / MANIFEST_DIGEST_FILE_NAME).read_text(encoding="utf-8").strip()
    digest, name = line.split()
    assert name == MANIFEST_FILE_NAME
    assert digest == manifest.manifest_checksum
