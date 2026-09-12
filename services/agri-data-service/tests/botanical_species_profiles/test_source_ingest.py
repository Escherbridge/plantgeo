"""Offline source admission and canonical WCVP join boundaries."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from agri_data_service.pipeline.direct.botanical_species_profiles.source_ingest import (
    DEFAULT_AUTHORING_CENSUS_NOTE,
    WCVP_HEADERS,
    request_from_wcvp,
)
from agri_data_service.warehouse.botanical_species_profiles.contract import ProfileError, SourceRelease

if TYPE_CHECKING:
    from pathlib import Path

INSTANT = datetime(2026, 9, 11, tzinfo=UTC)


def _row(**changes: str) -> dict[str, str]:
    return (
        dict.fromkeys(WCVP_HEADERS, "")
        | {
            "plant_name_id": "379633",
            "accepted_plant_name_id": "379633",
            "taxon_status": "Accepted",
            "taxon_rank": "Species",
            "taxon_name": "Pseudotsuga menziesii",
            "taxon_authors": "(Mirb.) Franco",
            "reviewed": "Y",
            "lifeform_description": "tree",
            "climate_description": "temperate",
        }
        | changes
    )


def _source(tmp_path: Path, rows: list[dict[str, str]]) -> tuple[Path, SourceRelease]:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(
        text, fieldnames=WCVP_HEADERS, delimiter="|", quotechar=None, quoting=csv.QUOTE_NONE, lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    path = tmp_path / "synthetic-wcvp.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("wcvp_names.csv", text.getvalue())
    return path, SourceRelease(
        source_id="kew-wcvp",
        version="16",
        release_url="https://sftp.kew.org/pub/data-repositories/WCVP/",
        download_url="https://sftp.kew.org/pub/data-repositories/WCVP/wcvp.zip",
        licence_id="CC-BY-3.0",
        licence_url="https://creativecommons.org/licenses/by/3.0/",
        content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        terms_sha256="a" * 64,
        retrieved_at=INSTANT,
        reviewer="synthetic-fixture-reviewer",
        reviewed_at=INSTANT,
        review_basis="Synthetic test source, never an admitted release.",
        machine_readable_verified=True,
        reuse_allowed=True,
        admitted_traits=("growth_habit", "climate_summary"),
    )


def test_pinned_source_preserves_only_direct_synonym_ids_and_raw_summary(tmp_path: Path) -> None:
    path, source = _source(
        tmp_path,
        [
            _row(lifeform_description='tree "evergreen"'),
            _row(plant_name_id="381882", taxon_name="Abies menziesii", taxon_status="Synonym"),
            _row(
                plant_name_id="99", accepted_plant_name_id="98", taxon_name="Other subspecies", taxon_status="Synonym"
            ),
        ],
    )
    request = request_from_wcvp(path, source, ("379633",), review_decision_id="fixture")
    assert request.authoring_census_state == "unavailable"
    assert request.authoring_census_note == DEFAULT_AUTHORING_CENSUS_NOTE
    assert [name.source_name_id for name in request.taxa[0].synonyms] == ["381882"]
    assertion = request.assertions[0]
    assert assertion.raw_value == assertion.normalized_value
    assert assertion.raw_value is not None
    assert assertion.raw_value.text == 'tree "evergreen"'
    assert assertion.evidence_kind == "categorical_summary"
    assert "not independent per-value measurement" in str(assertion.context.conditions)
    assert {item.trait for item in request.assertions} == {"growth_habit", "climate_summary"}


def test_checked_census_metadata_is_bound_without_claiming_database_status_in_source_assertions(
    tmp_path: Path,
) -> None:
    path, source = _source(tmp_path, [_row()])
    note = (
        "Synthetic census receipt: tests/fixtures/census.json; SHA256="
        + "b" * 64
        + "; reviewed relational rows preserved."
    )
    request = request_from_wcvp(
        path,
        source,
        ("379633",),
        review_decision_id="fixture",
        authoring_census_state="reviewed_snapshot",
        authoring_census_note=note,
    )
    payload = request.model_dump(mode="json")
    assert payload["authoring_census_state"] == "reviewed_snapshot"
    assert payload["authoring_census_note"] == note
    assert {assertion.authoring_provenance for assertion in request.assertions} == {
        "admitted-wcvp-v16-source-field-adapter"
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewed": "N"},
        {"accepted_plant_name_id": "98"},
        {"taxon_status": "Unplaced"},
    ],
)
def test_unreviewed_or_unresolved_taxon_refuses_instead_of_guessing(tmp_path: Path, changes: dict[str, str]) -> None:
    path, source = _source(tmp_path, [_row(**changes)])
    with pytest.raises(ProfileError, match="accepted and in a family"):
        request_from_wcvp(path, source, ("379633",), review_decision_id="fixture")


def test_illegitimate_name_is_not_silently_promoted_to_synonym(tmp_path: Path) -> None:
    path, source = _source(tmp_path, [_row(), _row(plant_name_id="88", taxon_status="Illegitimate")])
    with pytest.raises(ProfileError, match="unsupported status"):
        request_from_wcvp(path, source, ("379633",), review_decision_id="fixture")


def test_empty_summary_remains_missing_not_empty_categorical_value(tmp_path: Path) -> None:
    path, source = _source(tmp_path, [_row(climate_description="")])
    request = request_from_wcvp(path, source, ("379633",), review_decision_id="fixture")
    assertion = next(item for item in request.assertions if item.trait == "climate_summary")
    assert assertion.missingness == "not_reported"
    assert assertion.raw_value is None
    assert assertion.normalized_value is None


def test_modified_archive_is_refused_before_source_values_are_used(tmp_path: Path) -> None:
    path, source = _source(tmp_path, [_row()])
    with path.open("ab") as payload:
        payload.write(b"tampered")
    with pytest.raises(ProfileError, match="SHA-256"):
        request_from_wcvp(path, source, ("379633",), review_decision_id="fixture")


def test_source_id_selection_cannot_be_name_join_or_implicit_latest(tmp_path: Path) -> None:
    path, source = _source(tmp_path, [_row()])
    with pytest.raises(ProfileError, match="numeric source IDs"):
        request_from_wcvp(path, source, ("Pseudotsuga menziesii",), review_decision_id="fixture")
    with pytest.raises(ProfileError, match="absent"):
        request_from_wcvp(path, source, ("99",), review_decision_id="fixture")


def test_duplicate_selected_records_refuse(tmp_path: Path) -> None:
    path, source = _source(tmp_path, [_row(), _row()])
    with pytest.raises(ProfileError, match="duplicate selected"):
        request_from_wcvp(path, source, ("379633",), review_decision_id="fixture")
