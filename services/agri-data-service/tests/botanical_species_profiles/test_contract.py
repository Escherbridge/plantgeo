"""Identity, contextual refusal, deterministic normalization and correction contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agri_data_service.warehouse.botanical_species_profiles.contract import (
    TRAIT_SECTIONS,
    AssertionContext,
    ProfileError,
    ReconciliationDecision,
    TaxonIdentity,
    TraitAssertion,
    TraitValue,
)
from agri_data_service.warehouse.botanical_species_profiles.reconcile import normalize_value, reconcile
from tests.botanical_species_profiles.fixtures import IDENTITY, make_assertion, make_release_request


def _decision(assertions: tuple[TraitAssertion, ...], trait: str = "growth_habit") -> ReconciliationDecision:
    decisions, _profiles = reconcile(make_release_request(assertions))
    return next(decision for decision in decisions if decision.trait == trait)


def test_taxonomy_only_has_explicit_unknown_in_every_distinct_section() -> None:
    decisions, profiles = reconcile(make_release_request())
    assert len(decisions) == len(TRAIT_SECTIONS)
    assert {decision.state for decision in decisions} == {"unknown"}
    assert all(decision.value is None for decision in decisions)
    assert {section.name for section in profiles[0].sections} == set(TRAIT_SECTIONS.values())


def test_same_name_does_not_join_another_authority_version() -> None:
    wrong = make_assertion().model_copy(update={"identity": IDENTITY.model_copy(update={"authority_version": "v2"})})
    with pytest.raises(ProfileError, match="absent canonical taxon"):
        reconcile(make_release_request((wrong,)))


def test_source_version_and_licence_must_match_admission() -> None:
    wrong = make_assertion().model_copy(update={"licence_id": "unknown"})
    with pytest.raises(ProfileError, match="source version and licence"):
        reconcile(make_release_request((wrong,)))


def test_unsupported_normalization_is_refused_before_publication() -> None:
    wrong = make_assertion(value=TraitValue(number=1, unit="mm")).model_copy(
        update={"normalized_value": TraitValue(number=1000, unit="mm")},
    )
    with pytest.raises(ProfileError, match="deterministic recipe"):
        reconcile(make_release_request((wrong,)))


def test_temperature_conversion_preserves_raw_and_canonical_interval() -> None:
    raw = TraitValue(lower=32, upper=212, unit="degF")
    normalized = normalize_value(raw)
    assert normalized == TraitValue(lower=0, upper=100, unit="degC")
    assertion = make_assertion(trait="temperature_envelope", value=raw).model_copy(
        update={"normalized_value": normalized, "evidence_kind": "curated_requirement"},
    )
    assert _decision((assertion,), "temperature_envelope").value == normalized
    assert assertion.raw_value == raw


def test_conflict_retains_both_alternatives_without_a_default_value() -> None:
    assertions = (make_assertion(), make_assertion("assertion-2", value=TraitValue(text="shrub")))
    decision = _decision(assertions)
    assert decision.state == "conflict"
    assert decision.value is None
    assert decision.assertion_ids == ("assertion-1", "assertion-2")
    assert not decision.selected_assertion_ids


def test_context_and_evidence_classes_cannot_be_silently_combined() -> None:
    second = make_assertion("assertion-2").model_copy(update={"evidence_kind": "measured_trait"})
    assert _decision((make_assertion(), second)).state == "conflict"


def test_reviewed_correction_selects_replacement_and_retains_original() -> None:
    second = make_assertion("assertion-2", value=TraitValue(text="shrub")).model_copy(
        update={"corrects_assertion_id": "assertion-1"},
    )
    decision = _decision((second, make_assertion()))
    assert decision.state == "known"
    assert decision.selected_assertion_ids == ("assertion-2",)
    assert decision.excluded_assertion_ids == ("assertion-1",)
    assert decision.assertion_ids == ("assertion-1", "assertion-2")


@pytest.mark.parametrize("review_state", ["unreviewed", "rejected"])
def test_unapproved_correction_does_not_replace_approved_evidence(review_state: str) -> None:
    second = make_assertion("assertion-2", value=TraitValue(text="shrub")).model_copy(
        update={"corrects_assertion_id": "assertion-1", "review_state": review_state},
    )
    assert _decision((second, make_assertion())).selected_assertion_ids == ("assertion-1",)


@pytest.mark.parametrize(
    ("missingness", "expected_state"),
    [("not_reported", "unknown"), ("unresolved", "unknown"), ("restricted", "refused")],
)
def test_approved_missingness_correction_supersedes_stale_value_and_retains_both_records(
    missingness: str,
    expected_state: str,
) -> None:
    original = make_assertion()
    correction = make_assertion("assertion-2").model_copy(
        update={
            "corrects_assertion_id": original.assertion_id,
            "raw_value": None,
            "normalized_value": None,
            "missingness": missingness,
        }
    )
    decision = _decision((original, correction))
    assert decision.state == expected_state
    assert decision.value is None
    assert decision.selected_assertion_ids == ()
    assert decision.assertion_ids == (original.assertion_id, correction.assertion_id)
    assert original.assertion_id in decision.excluded_assertion_ids
    assert original.raw_value == TraitValue(text="tree")


@pytest.mark.parametrize("review_state", ["unreviewed", "rejected"])
def test_unapproved_missingness_correction_does_not_suppress_existing_value(review_state: str) -> None:
    correction = make_assertion("assertion-2").model_copy(
        update={
            "corrects_assertion_id": "assertion-1",
            "review_state": review_state,
            "missingness": "not_reported",
            "raw_value": None,
            "normalized_value": None,
        }
    )
    decision = _decision((make_assertion(), correction))
    assert decision.state == "known"
    assert decision.selected_assertion_ids == ("assertion-1",)
    assert decision.assertion_ids == ("assertion-1", "assertion-2")


def test_withdrawal_retains_original_but_removes_serving_value() -> None:
    withdrawal = make_assertion("withdrawal-1").model_copy(
        update={
            "withdraws_assertion_id": "assertion-1",
            "review_state": "withdrawn",
            "raw_value": None,
            "normalized_value": None,
        }
    )
    decision = _decision((make_assertion(), withdrawal))
    assert decision.state == "withdrawn"
    assert decision.value is None
    assert set(decision.assertion_ids) == {"assertion-1", "withdrawal-1"}


def test_correction_cycle_is_refused() -> None:
    first = make_assertion().model_copy(update={"corrects_assertion_id": "assertion-2"})
    second = make_assertion("assertion-2").model_copy(update={"corrects_assertion_id": "assertion-1"})
    with pytest.raises(ProfileError, match="cycles"):
        _decision((first, second))


@pytest.mark.parametrize(
    "trait", ["volatile_oils", "heat_content", "fire_tolerance", "companion_relationship", "objective_effect"]
)
def test_unsupported_fuel_response_companion_and_effect_claims_are_refused(trait: str) -> None:
    assert _decision((make_assertion(trait=trait),), trait).state == "refused"


def test_measured_live_fuel_moisture_requires_complete_matching_context() -> None:
    context = AssertionContext(
        tissue_or_component="foliage",
        live_dead_state="live",
        moisture_basis="dry mass",
        season="summer",
        life_stage="adult",
        geography="synthetic plot",
        environment="controlled chamber",
        conditions="20 degC; directly sampled",
    )
    assertion = make_assertion(trait="live_fuel_moisture", value=TraitValue(number=100, unit="% dry mass")).model_copy(
        update={"evidence_kind": "measured_trait", "context": context, "method": "gravimetric drying"},
    )
    assert _decision((assertion,), "live_fuel_moisture").state == "known"
    mismatched = assertion.model_copy(update={"context": context.model_copy(update={"live_dead_state": "dead"})})
    assert _decision((mismatched,), "live_fuel_moisture").state == "refused"


def test_explicit_missingness_remains_unknown_and_cannot_carry_false() -> None:
    missing = make_assertion().model_copy(
        update={
            "raw_value": None,
            "normalized_value": None,
            "missingness": "not_measured",
        }
    )
    assert _decision((missing,)).state == "unknown"
    with pytest.raises(ValidationError, match="missingness cannot carry a value"):
        _decision((missing.model_copy(update={"raw_value": TraitValue(boolean=False)}),))


def test_finite_values_and_trimmed_canonical_identity_are_required() -> None:
    with pytest.raises(ValidationError):
        TraitValue(number=float("nan"))
    with pytest.raises(ValidationError):
        TaxonIdentity(authority=" authority", authority_version="v1", taxon_id="T1")


def test_field_and_taxon_admission_scopes_are_enforced() -> None:
    request = make_release_request((make_assertion(),))
    no_traits = request.model_copy(update={"sources": (request.sources[0].model_copy(update={"admitted_traits": ()}),)})
    with pytest.raises(ProfileError, match="field is outside"):
        reconcile(no_traits)
    wrong_taxa = request.model_copy(
        update={
            "sources": (request.sources[0].model_copy(update={"admitted_taxon_ids": ("OTHER-ID",)}),),
        }
    )
    with pytest.raises(ProfileError, match="identity scope"):
        reconcile(wrong_taxa)


def test_multiple_versions_of_same_source_preserve_competing_assertions() -> None:
    first = make_assertion()
    second = make_assertion("assertion-2", value=TraitValue(text="shrub")).model_copy(update={"source_version": "v2"})
    request = make_release_request((first, second))
    request = request.model_copy(
        update={
            "sources": (request.sources[0], request.sources[0].model_copy(update={"version": "v2"})),
        }
    )
    decisions, _profiles = reconcile(request)
    assert next(decision for decision in decisions if decision.trait == "growth_habit").state == "conflict"


def test_temperature_unit_cannot_be_used_to_publish_soil_ph() -> None:
    raw = TraitValue(number=32, unit="degF")
    assertion = make_assertion(trait="soil_ph", value=raw).model_copy(update={"normalized_value": normalize_value(raw)})
    with pytest.raises(ProfileError, match="unrelated botanical trait"):
        _decision((assertion,), "soil_ph")


def test_canonical_taxon_cannot_relabel_an_admitted_authority_record() -> None:
    request = make_release_request()
    relabeled = request.taxa[0].model_copy(
        update={"identity": IDENTITY.model_copy(update={"taxon_id": "T-2"}), "synonyms": ()},
    )
    with pytest.raises(ProfileError, match="identity must match its authority source record identifier"):
        reconcile(request.model_copy(update={"taxa": (relabeled,)}))


def test_canonical_source_assertion_cannot_borrow_another_admitted_taxons_record() -> None:
    assertion = make_assertion().model_copy(update={"source_record_id": "T-2"})
    request = make_release_request((assertion,))
    second_taxon = request.taxa[0].model_copy(
        update={
            "identity": IDENTITY.model_copy(update={"taxon_id": "T-2"}),
            "source_record_id": "T-2",
            "accepted_name": "Syntheticus alterus",
            "source_name": "Syntheticus alterus",
            "synonyms": (),
        },
    )
    request = request.model_copy(
        update={
            "taxa": (*request.taxa, second_taxon),
            "sources": (request.sources[0].model_copy(update={"admitted_taxon_ids": ("T-1", "T-2")}),),
        },
    )
    with pytest.raises(ProfileError, match="same authority source record as its taxon"):
        reconcile(request)
