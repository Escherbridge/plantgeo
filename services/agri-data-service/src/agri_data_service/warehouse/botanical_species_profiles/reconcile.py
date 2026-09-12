"""Conservative deterministic evidence reconciliation; see AGENTS.md."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Literal

from agri_data_service.warehouse.botanical_species_profiles.contract import (
    SECTION_NAMES,
    TRAIT_SECTIONS,
    ProfileError,
    ProfileField,
    ProfileSection,
    ReconciliationDecision,
    ReleaseRequest,
    SpeciesProfile,
    TaxonIdentity,
    TraitAssertion,
    TraitValue,
    canonical_bytes,
    content_sha256,
)

if TYPE_CHECKING:
    from agri_data_service.warehouse.botanical_species_profiles.contract import SourceRelease

_KELVIN_OFFSET = 273.15
_FAHRENHEIT_OFFSET = 32
_FAHRENHEIT_SCALE = 5 / 9
_UNSPECIFIED = frozenset({"", "unknown", "unspecified", "not_reported", "not_measured"})
_FUEL_CONTEXT_FIELDS = (
    "tissue_or_component",
    "live_dead_state",
    "moisture_basis",
    "season",
    "life_stage",
    "geography",
    "environment",
    "conditions",
)
_TEMPERATURE_UNITS = frozenset({"K", "degF", "°F", "F", "degC", "°C", "C"})
_MOISTURE_TRAITS = frozenset(
    {
        "tissue_water_content",
        "live_fuel_moisture",
        "dead_fuel_moisture",
        "leaf_dry_matter_content",
    }
)
_MOISTURE_BASES = frozenset(
    {
        "dry mass",
        "wet mass",
        "fresh mass",
        "dry basis",
        "wet basis",
        "fresh basis",
        "dry-mass basis",
        "wet-mass basis",
        "fresh-mass basis",
        "oven-dry mass / fresh mass",
    }
)
Eligibility = Literal["candidate", "unknown", "refused", "withdrawn"]


def normalize_value(value: TraitValue) -> TraitValue:
    """Apply only explicit temperature unit conversion; preserve all other units."""
    if value.unit not in _TEMPERATURE_UNITS:
        return value
    if value.text is not None or value.boolean is not None:
        raise ProfileError("temperature normalization requires a numeric value")
    offset = 0.0
    scale = 1.0
    if value.unit == "K":
        offset = _KELVIN_OFFSET
    elif value.unit in {"degF", "°F", "F"}:
        offset, scale = _FAHRENHEIT_OFFSET, _FAHRENHEIT_SCALE
    values = value.model_dump()
    for key in ("number", "lower", "upper"):
        if values[key] is not None:
            values[key] = round((values[key] - offset) * scale, 10)
    values["unit"] = "degC"
    return TraitValue.model_validate(values)


def canonical_request(request: ReleaseRequest) -> ReleaseRequest:
    """Validate and sort the full authoring snapshot without losing alternatives."""
    request = ReleaseRequest.model_validate(request.model_dump(mode="python"))
    _validate_inventory(request)
    taxa = tuple(
        taxon.model_copy(
            update={
                "synonyms": tuple(sorted(taxon.synonyms, key=lambda name: (name.source_id, name.source_name_id))),
                "cultivars": tuple(sorted(taxon.cultivars, key=lambda name: (name.source_id, name.source_name_id))),
            }
        )
        for taxon in sorted(request.taxa, key=lambda taxon: taxon.identity.key)
    )
    return request.model_copy(
        update={
            "taxa": taxa,
            "assertions": tuple(sorted(request.assertions, key=lambda assertion: assertion.assertion_id)),
            "sources": tuple(
                source.model_copy(
                    update={
                        "admitted_traits": tuple(sorted(source.admitted_traits)),
                        "admitted_taxon_ids": tuple(sorted(source.admitted_taxon_ids)),
                    }
                )
                for source in sorted(request.sources, key=lambda source: (source.source_id, source.version))
            ),
        }
    )


def _validate_inventory(request: ReleaseRequest) -> None:
    sources = {(source.source_id, source.version): source for source in request.sources}
    taxa = {taxon.identity.key: taxon for taxon in request.taxa}
    assertions = {assertion.assertion_id: assertion for assertion in request.assertions}
    if len(sources) != len(request.sources) or len(taxa) != len(request.taxa):
        raise ProfileError("duplicate source identities or canonical taxon identities")
    if len(assertions) != len(request.assertions):
        raise ProfileError("assertion identifiers must be unique across the release")
    for taxon in request.taxa:
        source = sources.get((taxon.source_id, taxon.identity.authority_version))
        if source is None:
            raise ProfileError("canonical taxa require their admitted authority version")
        if taxon.identity.taxon_id != taxon.source_record_id:
            raise ProfileError("canonical taxon identity must match its authority source record identifier")
        if source.admitted_taxon_ids and taxon.source_record_id not in source.admitted_taxon_ids:
            raise ProfileError("canonical taxon is outside the admitted source identity scope")
        names = (*taxon.synonyms, *taxon.cultivars)
        identities = {(name.source_id, name.source_name_id) for name in names}
        if len(identities) != len(names) or any(
            (name.source_id, name.source_version or taxon.identity.authority_version) not in sources for name in names
        ):
            raise ProfileError("source names require unique identifiers and an admitted source")
    _validate_assertions(request, sources)
    _validate_changes(assertions)


def _validate_assertions(request: ReleaseRequest, sources: dict[tuple[str, str], SourceRelease]) -> None:
    taxa = {taxon.identity.key: taxon for taxon in request.taxa}
    for assertion in request.assertions:
        if assertion.identity.key not in taxa:
            raise ProfileError("assertion cannot join by name or reference an absent canonical taxon")
        taxon = taxa[assertion.identity.key]
        if (assertion.source_id, assertion.source_version) == (
            taxon.source_id,
            taxon.identity.authority_version,
        ) and assertion.source_record_id != taxon.source_record_id:
            raise ProfileError("canonical-source assertion must identify the same authority source record as its taxon")
        source = sources.get((assertion.source_id, assertion.source_version))
        if source is None or assertion.licence_id != source.licence_id:
            raise ProfileError("assertion source version and licence must match source admission")
        if assertion.review_state == "approved" and assertion.missingness is None:
            if assertion.trait not in source.admitted_traits:
                raise ProfileError("approved assertion field is outside its source admission scope")
            if source.admitted_taxon_ids and assertion.source_record_id not in source.admitted_taxon_ids:
                raise ProfileError("approved assertion taxon is outside its source admission scope")
        if (
            assertion.raw_value is not None
            and assertion.raw_value.unit in _TEMPERATURE_UNITS
            and assertion.trait != "temperature_envelope"
        ):
            raise ProfileError("temperature units cannot normalize an unrelated botanical trait")
        if assertion.raw_value is not None and normalize_value(assertion.raw_value) != assertion.normalized_value:
            raise ProfileError("normalized assertion differs from the declared deterministic recipe")


def _validate_changes(assertions: dict[str, TraitAssertion]) -> None:
    for assertion in assertions.values():
        if assertion.corrects_assertion_id and assertion.withdraws_assertion_id:
            raise ProfileError("a single assertion cannot both correct and withdraw")
        target_id = assertion.corrects_assertion_id or assertion.withdraws_assertion_id
        seen = {assertion.assertion_id}
        while target_id is not None:
            target = assertions.get(target_id)
            if target is None or (target.identity, target.trait) != (assertion.identity, assertion.trait):
                raise ProfileError("corrections and withdrawals must retain the same taxon/trait and their original")
            if target_id in seen:
                raise ProfileError("corrections and withdrawals cannot form cycles")
            seen.add(target_id)
            target_id = target.corrects_assertion_id or target.withdraws_assertion_id


def _context_complete(assertion: TraitAssertion, fields: tuple[str, ...]) -> bool:
    context = assertion.context.model_dump()
    return all(isinstance(context[name], str) and context[name].strip().lower() not in _UNSPECIFIED for name in fields)


def assertion_eligibility(assertion: TraitAssertion) -> tuple[Eligibility, str]:
    """Refuse unsupported claims while retaining their complete evidence records."""
    if assertion.review_state in {"unreviewed", "rejected"}:
        return "refused", "assertion has no approval for publication"
    if assertion.review_state == "withdrawn" or assertion.withdraws_assertion_id:
        return "withdrawn", "reviewed withdrawal retained; value is not serving evidence"
    if assertion.missingness == "restricted":
        return "refused", "restricted evidence cannot support a published value"
    if assertion.missingness:
        return "unknown", f"source missingness: {assertion.missingness}"
    return _evidence_eligibility(assertion)


def _evidence_eligibility(assertion: TraitAssertion) -> tuple[Eligibility, str]:
    section = TRAIT_SECTIONS[assertion.trait]
    if section == "objective_effects":
        return "refused", "objective-effect evaluation belongs to the separate recommendation validation contract"
    if assertion.evidence_kind == "occurrence_association":
        return "refused", "occurrence associations cannot become establishment requirements or causal effects"
    if section == "fuel_traits":
        return _fuel_eligibility(assertion)
    if section == "companion_evidence" and (
        assertion.evidence_kind != "reviewed_effect"
        or assertion.related_taxon is None
        or not _context_complete(assertion, ("geography", "environment", "conditions"))
    ):
        return "refused", "companion claims require a reviewed effect, identified partner and applicable conditions"
    if section == "fire_response" and (
        assertion.evidence_kind not in {"measured_trait", "reviewed_literature"}
        or not assertion.method
        or not _context_complete(assertion, ("geography", "conditions"))
    ):
        return "refused", "fire response requires attributable contextual evidence, separate from fuel composition"
    return "candidate", "approved evidence with its original source, units and context retained"


def _fuel_eligibility(assertion: TraitAssertion) -> tuple[Eligibility, str]:
    if assertion.evidence_kind not in {"measured_trait", "reviewed_literature"}:
        return "refused", "fuel values require measured or individually reviewable literature evidence"
    if not assertion.method or not _context_complete(assertion, _FUEL_CONTEXT_FIELDS):
        return "refused", "fuel evidence lacks tissue, live/dead, moisture basis, method, season or contextual support"
    expected_state = {"live_fuel_moisture": "live", "dead_fuel_moisture": "dead"}.get(assertion.trait)
    if expected_state is not None and assertion.context.live_dead_state != expected_state:
        return "refused", "live/dead measurement context disagrees with the fuel-moisture trait"
    value = assertion.normalized_value
    if assertion.trait in _MOISTURE_TRAITS and (
        assertion.context.moisture_basis not in _MOISTURE_BASES
        or value is None
        or (value.number is None and value.lower is None)
    ):
        return "refused", "water and dry-matter traits require numeric measurements and an explicit wet/dry mass basis"
    if value is not None and (value.number is not None or value.lower is not None) and not value.unit:
        return "refused", "numeric fuel evidence requires its measurement unit"
    return "candidate", "context-complete fuel evidence; no fire-mitigation or suitability conclusion is implied"


def reconcile(request: ReleaseRequest) -> tuple[tuple[ReconciliationDecision, ...], tuple[SpeciesProfile, ...]]:
    """Reconcile every declared field, including explicit unknowns in taxonomy-only releases."""
    request = canonical_request(request)
    by_field: dict[tuple[str, str], list[TraitAssertion]] = defaultdict(list)
    for assertion in request.assertions:
        by_field[(assertion.identity.key, assertion.trait)].append(assertion)
    decisions = tuple(
        _decide(taxon.identity, trait, by_field[(taxon.identity.key, trait)])
        for taxon in request.taxa
        for trait in TRAIT_SECTIONS
    )
    by_taxon: dict[str, list[ReconciliationDecision]] = defaultdict(list)
    for decision in decisions:
        by_taxon[decision.identity.key].append(decision)
    profiles = tuple(
        SpeciesProfile(
            identity=taxon.identity,
            accepted_name=taxon.accepted_name,
            sections=tuple(
                ProfileSection(
                    name=section,
                    fields=tuple(
                        ProfileField(
                            trait=decision.trait,
                            state=decision.state,
                            value=decision.value,
                            assertion_ids=decision.assertion_ids,
                            decision_id=decision.decision_id,
                            reason=decision.reason,
                        )
                        for decision in by_taxon[taxon.identity.key]
                        if TRAIT_SECTIONS[decision.trait] == section
                    ),
                )
                for section in SECTION_NAMES
            ),
        )
        for taxon in request.taxa
    )
    return decisions, profiles


def _decide(identity: TaxonIdentity, trait: str, assertions: list[TraitAssertion]) -> ReconciliationDecision:
    eligibility = {item.assertion_id: assertion_eligibility(item) for item in assertions}
    excluded = {
        target
        for item in assertions
        if (item.review_state == "approved" and item.corrects_assertion_id)
        or (eligibility[item.assertion_id][0] == "withdrawn" and item.withdraws_assertion_id)
        if (target := item.corrects_assertion_id or item.withdraws_assertion_id)
    }
    candidates = [
        item
        for item in assertions
        if eligibility[item.assertion_id][0] == "candidate" and item.assertion_id not in excluded
    ]
    selected, state, value, reason = _select(candidates, assertions, eligibility)
    excluded.update(item.assertion_id for item in assertions if item.assertion_id not in selected)
    payload = {
        "identity": identity.model_dump(mode="json"),
        "trait": trait,
        "state": state,
        "assertion_ids": tuple(item.assertion_id for item in assertions),
        "selected_assertion_ids": tuple(selected),
        "excluded_assertion_ids": tuple(sorted(excluded)),
        "value": value.model_dump(mode="json") if value else None,
        "reason": reason,
    }
    return ReconciliationDecision.model_validate(
        {
            **payload,
            "decision_id": "bspd-" + content_sha256(canonical_bytes(payload)),
        }
    )


def _select(
    candidates: list[TraitAssertion],
    assertions: list[TraitAssertion],
    eligibility: dict[str, tuple[Eligibility, str]],
) -> tuple[list[str], Literal["known", "unknown", "conflict", "refused", "withdrawn"], TraitValue | None, str]:
    if candidates:
        signatures = {
            canonical_bytes(
                {
                    "value": item.normalized_value.model_dump(mode="json") if item.normalized_value else None,
                    "context": item.context.model_dump(),
                    "kind": item.evidence_kind,
                    "method": item.method,
                    "qualifier": item.qualifier,
                    "related_taxon": item.related_taxon.model_dump() if item.related_taxon else None,
                }
            )
            for item in candidates
        }
        if len(signatures) != 1:
            return (
                [],
                "conflict",
                None,
                "approved alternatives differ in value, evidence class or context; no merge rule",
            )
        return (
            [item.assertion_id for item in candidates],
            "known",
            candidates[0].normalized_value,
            ("approved concordant evidence; original alternatives and all exclusions remain linked"),
        )
    states = {eligibility[item.assertion_id][0] for item in assertions}
    if "withdrawn" in states:
        return (
            [],
            "withdrawn",
            None,
            "all supporting evidence was withdrawn or superseded without a serving replacement",
        )
    if "refused" in states:
        reasons = sorted(
            {eligibility[item.assertion_id][1] for item in assertions if eligibility[item.assertion_id][0] == "refused"}
        )
        return [], "refused", None, "; ".join(reasons)
    return [], "unknown", None, "no approved value was reported; unknown is not false and does not imply compatibility"
