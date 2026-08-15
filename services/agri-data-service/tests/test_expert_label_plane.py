"""Harvest validation, lineage checksums, and the envelope-to-stream verdict rules."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path  # noqa: TC003

import pytest

from agri_data_service.method.ml.covariates_v2 import SiteClimateTerms, classify_aridity
from agri_data_service.method.ml.expert_label_plane import (
    CONFIDENCE_WEIGHTS,
    ENVELOPE_TERM_SUPPORT,
    ExpertLabelPlaneError,
    HarvestDocument,
    HarvestLabel,
    bounded_issue_days,
    evaluate_envelope,
    label_checksum_for,
    label_key_for,
    load_harvest_document,
    normalize_subject,
    release_checksum_for,
    source_key_for,
)

_LABEL = {
    "label_kind": "species_fit",
    "subject": "Purshia tridentata",
    "condition_envelope": {
        "mean_annual_precipitation_mm": {"min": 283, "max": 283},
        "elevation_m": {"min": 1310, "max": 1460},
        "aridity": "semi_arid",
    },
    "outcome": "unfit",
    "rationale": "Seedling mortality was near total under Boise-comparable precipitation.",
    "source": {
        "doi": "10.1016/j.rama.2022.08.001",
        "title": "Reducing Exotic Annual Grass Competition Did Not Improve Shrub Restoration Success",
        "journal": "Rangeland Ecology & Management",
        "year": 2022,
        "supporting_quote_or_finding": "Only 4 of 500 planted bitterbrush seedlings survived to the second year.",
    },
    "confidence": "high",
    "harvest_slice": "species-trees-shrubs",
    "citation_check": {"refuted": False, "doi_resolves": True, "reason": "DOI resolves; figures match."},
}


def _document(**overrides: object) -> dict[str, object]:
    return {
        "harvested_at": "2026-08-14",
        "workflow": "literature-label-harvest",
        "kept": [{**_LABEL, **overrides}],
        "rejected": [],
    }


def test_harvest_document_validates_and_digests_exact_bytes(tmp_path: Path) -> None:
    path = tmp_path / "harvest.json"
    payload = json.dumps(_document())
    path.write_text(payload, encoding="utf-8")

    document, checksum = load_harvest_document(path)

    assert len(document.kept) == 1
    assert checksum == __import__("hashlib").sha256(payload.encode("utf-8")).hexdigest()


def test_harvest_document_rejects_an_outcome_that_does_not_match_its_kind(tmp_path: Path) -> None:
    path = tmp_path / "harvest.json"
    path.write_text(json.dumps(_document(outcome="effective")), encoding="utf-8")

    with pytest.raises((ExpertLabelPlaneError, ValueError)):
        load_harvest_document(path)


def test_harvest_document_rejects_an_envelope_term_outside_the_vocabulary(tmp_path: Path) -> None:
    path = tmp_path / "harvest.json"
    path.write_text(json.dumps(_document(condition_envelope={"annual_snowfall_cm": 40})), encoding="utf-8")

    with pytest.raises(ExpertLabelPlaneError, match="outside the vocabulary"):
        load_harvest_document(path)


def test_an_empty_envelope_is_reported_not_stored(tmp_path: Path) -> None:
    path = tmp_path / "harvest.json"
    path.write_text(json.dumps(_document(condition_envelope={})), encoding="utf-8")

    document, _checksum = load_harvest_document(path)

    # It validates as a document -- the load partitions it out with a reason instead of crashing.
    assert document.kept[0].condition_envelope == {}


def test_label_identity_is_deterministic_and_content_bound() -> None:
    label = HarvestLabel.model_validate(_LABEL)
    other = HarvestLabel.model_validate({**_LABEL, "outcome": "marginal"})

    assert label_key_for(label) == label_key_for(HarvestLabel.model_validate(_LABEL))
    assert label_checksum_for(label) != label_checksum_for(other)
    assert source_key_for(label.source) == "doi:10.1016/j.rama.2022.08.001"


def test_release_checksum_is_order_independent_over_its_labels() -> None:
    first = release_checksum_for(
        harvest_document_checksum="a" * 64, label_keys=["b", "a"], review_tier="agent_reviewed_pending_owner_signature"
    )
    second = release_checksum_for(
        harvest_document_checksum="a" * 64, label_keys=["a", "b"], review_tier="agent_reviewed_pending_owner_signature"
    )

    assert first == second


def test_confidence_weights_cover_every_admitted_ordinal() -> None:
    assert set(CONFIDENCE_WEIGHTS) == {"high", "medium", "low"}
    assert all(0.0 <= weight <= 1.0 for weight in CONFIDENCE_WEIGHTS.values())


def _climate(**overrides: object) -> SiteClimateTerms:
    defaults: dict[str, object] = {
        "observed_date": date(2025, 6, 1),
        "mean_annual_precipitation_mm": 300.0,
        "mean_annual_temperature_c": 11.0,
        "growing_season_frost_free_days": 160,
        "aridity": "semi_arid",
        "aridity_index": 0.31,
        "contributing_day_count": 365,
    }
    return SiteClimateTerms(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_envelope_within_tolerance_matches_and_records_the_tolerance() -> None:
    verdicts, unexpressible, state = evaluate_envelope(
        {"mean_annual_precipitation_mm": {"min": 283, "max": 283}, "aridity": "semi_arid"}, _climate()
    )

    assert state == "matched"
    assert unexpressible == ()
    assert verdicts["mean_annual_precipitation_mm"].satisfied is True
    assert verdicts["mean_annual_precipitation_mm"].tolerance == 60.0  # noqa: PLR2004


def test_envelope_outside_tolerance_is_excluded() -> None:
    _verdicts, _unexpressible, state = evaluate_envelope(
        {"mean_annual_precipitation_mm": {"min": 900, "max": 1100}}, _climate()
    )

    assert state == "excluded"


def test_unexpressible_terms_are_carried_out_not_counted_either_way() -> None:
    verdicts, unexpressible, state = evaluate_envelope(
        {"elevation_m": {"min": 1310, "max": 1460}, "soil_texture": ["silt_loam"]}, _climate()
    )

    assert state == "unexpressible"
    assert set(unexpressible) == {"elevation_m", "soil_texture"}
    assert verdicts["elevation_m"].satisfied is None
    assert "topography_profiles" in str(verdicts["elevation_m"].reason)


def test_a_term_the_streams_could_express_but_did_not_is_neither_matched_nor_violated() -> None:
    verdicts, _unexpressible, state = evaluate_envelope(
        {"mean_annual_precipitation_mm": 300, "aridity": "arid"},
        _climate(aridity=None, aridity_index=None),
    )

    assert verdicts["aridity"].satisfied is None
    assert state == "matched"


def test_every_vocabulary_term_declares_its_support_class() -> None:
    assert set(ENVELOPE_TERM_SUPPORT.values()) <= {"direct", "derived_proxy", "unexpressible"}


def test_issue_day_grid_is_bounded_and_deterministic() -> None:
    days = bounded_issue_days(date(2025, 1, 1), date(2025, 3, 31))

    assert days == bounded_issue_days(date(2025, 1, 1), date(2025, 3, 31))
    assert len(days) == 6  # noqa: PLR2004
    assert all(day.day in {1, 15} for day in days)


def test_subject_normalization_folds_case_and_separators() -> None:
    assert normalize_subject("Managed_Grazing") == "managed grazing"
    assert normalize_subject("  Pinus  ponderosa ") == "pinus ponderosa"


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0.01, "hyper_arid"), (0.1, "arid"), (0.31, "semi_arid"), (0.6, "dry_subhumid"), (0.9, "humid")],
)
def test_aridity_classification_follows_the_unep_bands(index: float, expected: str) -> None:
    assert classify_aridity(index) == expected


def test_harvest_document_round_trips_through_its_model() -> None:
    document = HarvestDocument.model_validate(_document())

    assert document.kept[0].resolved_quote() is not None
