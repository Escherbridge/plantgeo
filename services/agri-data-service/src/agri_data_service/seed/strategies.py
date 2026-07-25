"""Definition-only, source-backed draft conservation-practice seeds."""

from dataclasses import dataclass
from typing import Any

from agri_data_service.models.strategy import StrategyReviewState

UNSUPPORTED_PRESCRIPTIVE_FIELDS: dict[str, Any] = {
    "min_precip_mm": None,
    "max_precip_mm": None,
    "min_temp_c": None,
    "max_temp_c": None,
    "suitable_soil_types": None,
    "suitable_drainage": None,
    "max_slope_pct": None,
    "min_organic_matter_pct": None,
    "water_requirement": None,
    "labor_intensity": None,
    "time_to_yield_years": None,
    "carbon_seq_potential": None,
    "biodiversity_impact": None,
}


@dataclass(frozen=True, slots=True)
class _StrategyDefinition:
    name: str
    slug: str
    category: str
    practice_code: str
    description: str
    evidence_citation: str
    evidence_source_url: str
    limitations: str


def _definition_seed(definition: _StrategyDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "slug": definition.slug,
        "category": definition.category,
        "authority": "USDA-NRCS",
        "practice_code": definition.practice_code,
        "description": definition.description,
        "evidence_citation": definition.evidence_citation,
        "evidence_source_url": definition.evidence_source_url,
        "jurisdiction": "US",
        "limitations": definition.limitations,
        "review_state": StrategyReviewState.DRAFT,
        "reviewed_at": None,
        "reviewed_by": None,
        **UNSUPPORTED_PRESCRIPTIVE_FIELDS,
    }


STRATEGY_SEEDS = [
    _definition_seed(
        _StrategyDefinition(
            name="Cover Crop (NRCS 340)",
            slug="cover-crop-340",
            category="agriculture",
            practice_code="340",
            description=(
                "Temporary vegetative cover of grasses, legumes, or forbs established for conservation purposes."
            ),
            evidence_citation=(
                "USDA NRCS Conservation Practice Standard 340, Cover Crop; exact current "
                "version remains pending source capture."
            ),
            evidence_source_url=(
                "https://www.nrcs.usda.gov/resources/guides-and-instructions/conservation-practice-standards"
            ),
            limitations=(
                "Definition only. No species, planting date or rate, termination, fertilizer, "
                "grazing, yield, water-use, climate, soil, or slope recommendation. Resolve the "
                "current state Field Office Technical Guide and local guidance before design."
            ),
        ),
    ),
    _definition_seed(
        _StrategyDefinition(
            name="Alley Cropping (NRCS 311)",
            slug="alley-cropping-311",
            category="agroforestry",
            practice_code="311",
            description=("Crops or forage grown in alleys between deliberately arranged rows of trees or shrubs."),
            evidence_citation=(
                "USDA NRCS Conservation Practice Standard 311, Alley Cropping; exact current "
                "version remains pending source capture."
            ),
            evidence_source_url=(
                "https://www.nrcs.usda.gov/conservation-basics/land/forests/agroforestry-systems/alley-cropping"
            ),
            limitations=(
                "Definition only. No layout, spacing, species, irrigation, yield, fire-behavior, "
                "or site-suitability recommendation. Require the current state Field Office "
                "Technical Guide and qualified local design."
            ),
        ),
    ),
    _definition_seed(
        _StrategyDefinition(
            name="Tree/Shrub Establishment (NRCS 612)",
            slug="tree-shrub-establishment-612",
            category="reforestation",
            practice_code="612",
            description=(
                "Establishment of adapted woody vegetation through planting, seeding, cuttings, "
                "or planned natural regeneration."
            ),
            evidence_citation=("USDA NRCS, Tree/Shrub Establishment, Conservation Practice Standard 612, May 2016."),
            evidence_source_url=(
                "https://www.nrcs.usda.gov/sites/default/files/2022-10/Tree-Shrub-Establishment-612-CPS-May-2016.pdf"
            ),
            limitations=(
                "Definition only; practice 612 is broader than reforestation and the captured "
                "standard is dated. No species, provenance, seed-zone, stock type, timing, "
                "spacing, survival, or impact claim. Verify current national and state guidance."
            ),
        ),
    ),
    _definition_seed(
        _StrategyDefinition(
            name="Silvopasture (NRCS 381)",
            slug="silvopasture-381",
            category="silvopasture",
            practice_code="381",
            description=(
                "Deliberate establishment or management of trees and forage on the same "
                "land unit as an integrated system."
            ),
            evidence_citation=(
                "USDA NRCS Conservation Practice Standard 381, Silvopasture; exact current "
                "version remains pending source capture."
            ),
            evidence_source_url=(
                "https://www.nrcs.usda.gov/resources/guides-and-instructions/"
                "silvopasture-ac-381-conservation-practice-standard"
            ),
            limitations=(
                "Definition only. No stocking, species, density, rotation, climate, soil, water, "
                "labor, yield, carbon, biodiversity, or fire-risk rating. Require the current "
                "state Field Office Technical Guide and qualified design."
            ),
        ),
    ),
]
