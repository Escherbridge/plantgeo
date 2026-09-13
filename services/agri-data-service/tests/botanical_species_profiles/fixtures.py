"""Synthetic public-domain botanical evidence for offline publication tests."""

from datetime import UTC, datetime

from agri_data_service.warehouse.botanical_species_profiles.contract import (
    TRAIT_SECTIONS,
    AssertionContext,
    ReleaseRequest,
    SourceRelease,
    TaxonIdentity,
    TaxonName,
    TaxonRecord,
    TraitAssertion,
    TraitValue,
)

INSTANT = datetime(2026, 9, 11, tzinfo=UTC)
IDENTITY = TaxonIdentity(authority="synthetic-authority", authority_version="v1", taxon_id="T-1")


def make_release_request(assertions: tuple[TraitAssertion, ...] = ()) -> ReleaseRequest:
    """Return a small taxonomy-only authoring snapshot unless assertions are supplied."""
    source = SourceRelease(
        source_id="synthetic-source",
        version="v1",
        release_url="https://example.org/releases/v1",
        download_url="https://example.org/releases/v1/data.tsv",
        licence_id="CC0-1.0",
        licence_url="https://creativecommons.org/publicdomain/zero/1.0/",
        content_sha256="a" * 64,
        terms_sha256="b" * 64,
        retrieved_at=INSTANT,
        reviewer="test-admission-reviewer",
        reviewed_at=INSTANT,
        review_basis="Synthetic test fixture; no provider data is represented",
        machine_readable_verified=True,
        reuse_allowed=True,
        admitted_traits=tuple(TRAIT_SECTIONS),
        admitted_taxon_ids=("T-1",),
    )
    taxon = TaxonRecord(
        identity=IDENTITY,
        accepted_name="Syntheticus testii",
        rank="Species",
        source_id=source.source_id,
        source_record_id="T-1",
        source_name="Syntheticus testii",
        synonyms=(
            TaxonName(
                name="Oldus testii",
                source_name_id="S-1",
                accepted_taxon_id="T-1",
                status="synonym",
                source_id=source.source_id,
            ),
        ),
        authoring_provenance="synthetic-test-snapshot",
    )
    return ReleaseRequest(
        taxa=(taxon,),
        assertions=assertions,
        sources=(source,),
        reviewer="test-release-reviewer",
        review_decision_id="test-review-1",
        reviewed_at=INSTANT,
        authoring_census_note="Database census deliberately unavailable in offline tests",
    )


def make_assertion(
    assertion_id: str = "assertion-1",
    *,
    trait: str = "growth_habit",
    value: TraitValue | None = None,
) -> TraitAssertion:
    """Return one approved, attributable synthetic categorical trait assertion."""
    resolved = TraitValue(text="tree") if value is None else value
    return TraitAssertion(
        assertion_id=assertion_id,
        identity=IDENTITY,
        trait=trait,
        source_id="synthetic-source",
        source_version="v1",
        source_url="https://example.org/releases/v1/data.tsv",
        source_record_id="T-1",
        licence_id="CC0-1.0",
        evidence_locator=f"record T-1 field {trait}",
        evidence_kind="categorical_summary",
        raw_value=resolved,
        normalized_value=resolved,
        method="documented source classification",
        context=AssertionContext(),
        retrieved_at=INSTANT,
        review_state="approved",
        reviewer="test-botanical-reviewer",
        review_reason="Synthetic assertion approved solely to exercise deterministic contracts",
        authoring_provenance="synthetic-test-snapshot",
        authoring_row_id="reviewed-row-7",
    )
