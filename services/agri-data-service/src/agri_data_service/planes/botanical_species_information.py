"""Bounded transitional botanical reads from the existing authoring schema."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from agri_data_service.models.species import CompanionRelationship, EvidenceReviewState, Species

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

PRODUCT: Final = "botanical-species-information"
DEFAULT_COMPANION_LIMIT: Final = 20
MAX_COMPANION_LIMIT: Final = 50
MAX_RESPONSE_BYTES: Final = 256 * 1024
_PARAMETERS: Final = frozenset({"species_id", "companion_limit"})

_PROFILE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "identity": ("scientific_name", "common_name", "usda_symbol", "family"),
    "growth_requirements": (
        "growth_habit",
        "native_status",
        "usda_zones",
        "min_precip_mm",
        "max_precip_mm",
        "min_ph",
        "max_ph",
        "light_requirement",
        "drought_tolerance",
        "salt_tolerance",
    ),
    "agricultural_roles": ("nitrogen_fixer", "pollinator_value", "edible", "timber_value", "guild_roles"),
}
_FUEL_FIELDS: Final = (
    "tissue_water_content",
    "live_fuel_moisture",
    "dead_fuel_moisture",
    "leaf_dry_matter_content",
    "volatile_oils",
    "heat_content",
    "bulk_density",
    "curing",
    "litter_persistence",
)


class SpeciesInformationRequestError(ValueError):
    """The lookup does not identify one exact Species UUID within its bounds."""


@dataclass(frozen=True, slots=True)
class SpeciesInformationRequest:
    species_id: uuid.UUID
    companion_limit: int = DEFAULT_COMPANION_LIMIT

    def __post_init__(self) -> None:
        if type(self.companion_limit) is not int or not 1 <= self.companion_limit <= MAX_COMPANION_LIMIT:
            raise SpeciesInformationRequestError(f"companion_limit must be an integer from 1 to {MAX_COMPANION_LIMIT}")


def parse_species_information_request(parameters: Mapping[str, str]) -> SpeciesInformationRequest:
    """Accept only a canonical UUID, never a botanical name or fuzzy identity."""
    if set(parameters) - _PARAMETERS:
        raise SpeciesInformationRequestError("unsupported parameters; lookup requires an exact Species UUID")
    raw_id = parameters.get("species_id")
    if raw_id is None or raw_id != raw_id.strip():
        raise SpeciesInformationRequestError("species_id is required and must be an exact UUID")
    try:
        species_id = uuid.UUID(raw_id)
    except (ValueError, AttributeError) as error:
        raise SpeciesInformationRequestError("species_id is required and must be an exact UUID") from error
    if str(species_id) != raw_id:
        raise SpeciesInformationRequestError("species_id must use canonical lowercase hyphenated UUID form")
    raw_limit = parameters.get("companion_limit", str(DEFAULT_COMPANION_LIMIT))
    if not raw_limit.isascii() or not raw_limit.isdecimal() or len(raw_limit) > len(str(MAX_COMPANION_LIMIT)):
        raise SpeciesInformationRequestError("companion_limit must be an integer")
    return SpeciesInformationRequest(species_id=species_id, companion_limit=int(raw_limit))


def _json_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    lower = getattr(value, "lower", None)
    upper = getattr(value, "upper", None)
    if lower is not None and upper is not None and not callable(lower) and not callable(upper):
        return {"minimum": lower, "maximum_exclusive": upper}
    return value


def _field(value: Any, *, missingness: str = "not_reported") -> dict[str, Any]:
    rendered = _json_value(value)
    if rendered is None:
        return {
            "value": None,
            "state": "unknown",
            "missingness": missingness,
            "provenance": "reviewed_authoring_database",
            "review_posture": "not_individually_reviewed",
        }
    return {
        "value": rendered,
        "state": "unverified_authoring",
        "missingness": None,
        "provenance": "reviewed_authoring_database",
        "review_posture": "not_individually_reviewed",
    }


def _approved_field(value: Any) -> dict[str, Any]:
    """Render one reviewed companion value without upgrading a missing value to evidence."""
    return {
        "value": _json_value(value),
        "state": "approved_evidence" if value is not None else "unknown",
        "missingness": None if value is not None else "not_reported",
        "provenance": "reviewed_authoring_database",
        "review_posture": "approved_relationship",
    }


def _empty_result(request: SpeciesInformationRequest | None) -> dict[str, Any]:
    return {
        "product": PRODUCT,
        "state": "unknown",
        "reason": None,
        "species_id": str(request.species_id) if request else None,
        "serving_source": "reviewed_authoring_database",
        "publication_state": "not_published",
        "profile_release_id": None,
        "sections": {
            **{
                section: {
                    "state": "unknown",
                    "missingness": "species_not_found",
                    "fields": {name: _field(None, missingness="species_not_found") for name in names},
                }
                for section, names in _PROFILE_FIELDS.items()
            },
            "fuel_tissue_composition": {
                "state": "unknown",
                "missingness": "not_modeled_in_authoring_schema",
                "fields": {name: _field(None) for name in _FUEL_FIELDS},
            },
            "companion_evidence": {
                "state": "unknown",
                "missingness": "species_not_found",
                "relationships": [],
                "truncated": False,
            },
        },
        "claim_limits": {
            "species_ranking": "refused",
            "planting_recommendation": "refused",
            "documented_occurrence": "not_evaluated_no_gis_data_queried",
            "establishment_suitability": "not_evaluated",
            "objective_effect": "requires_separate_reviewed_condition_specific_evidence",
            "fuel_or_fire_claim": "refused_no_supported_measurement_context",
        },
    }


def invalid_request(message: str) -> dict[str, Any]:
    result = _empty_result(None)
    result.update(state="refused", reason={"code": "invalid_species_information_request", "message": message})
    return result


def encode_species_information(result: Mapping[str, Any]) -> bytes:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


async def _read_species_information(
    request: SpeciesInformationRequest,
    *,
    session_provider: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> dict[str, Any]:
    """Read one modeled species and approved companion evidence through a supplied reader session."""
    async with session_provider() as session:
        species = await session.scalar(select(Species).where(Species.id == request.species_id))
        if species is None:
            return _empty_result(request)

        species_a = aliased(Species)
        species_b = aliased(Species)
        statement = (
            select(CompanionRelationship, species_a, species_b)
            .join(species_a, CompanionRelationship.species_a_id == species_a.id)
            .join(species_b, CompanionRelationship.species_b_id == species_b.id)
            .where(
                or_(
                    CompanionRelationship.species_a_id == request.species_id,
                    CompanionRelationship.species_b_id == request.species_id,
                ),
                CompanionRelationship.review_state == EvidenceReviewState.APPROVED,
            )
            .order_by(CompanionRelationship.id)
            .limit(request.companion_limit + 1)
        )
        rows = list((await session.execute(statement)).all())

    truncated = len(rows) > request.companion_limit
    companions: list[dict[str, Any]] = []
    for relationship, left, right in rows[: request.companion_limit]:
        counterpart = right if relationship.species_a_id == request.species_id else left
        companions.append(
            {
                "relationship_id": str(relationship.id),
                "related_species": {
                    "species_id": _approved_field(str(counterpart.id)),
                    "scientific_name": _field(counterpart.scientific_name),
                    "common_name": _field(counterpart.common_name),
                },
                "relationship_type": _approved_field(relationship.relationship_type.value),
                "guild_function": _approved_field(relationship.guild_function),
                "notes": _approved_field(relationship.notes),
                "review_state": _approved_field("approved"),
                "evidence_citation": _approved_field(relationship.evidence_citation),
                "evidence_source_url": _approved_field(relationship.evidence_source_url),
                "evidence_grade": _approved_field(relationship.evidence_grade),
                "applicability_context": _approved_field(relationship.applicability_context),
                "jurisdiction": _approved_field(relationship.jurisdiction),
                "reviewed_at": _approved_field(
                    relationship.reviewed_at.isoformat() if relationship.reviewed_at else None
                ),
                "reviewed_by": _approved_field(relationship.reviewed_by),
            }
        )

    result = _empty_result(request)
    sections = result["sections"]
    for section, names in _PROFILE_FIELDS.items():
        fields = {name: _field(getattr(species, name)) for name in names}
        has_value = any(item["value"] is not None for item in fields.values())
        sections[section] = {
            "state": "unverified_authoring" if has_value else "unknown",
            "missingness": None if has_value else "not_reported",
            "fields": fields,
        }
    sections["companion_evidence"] = {
        "state": "approved_evidence" if companions else "unknown",
        "missingness": None if companions else "no_approved_relationships_reported",
        "relationships": companions,
        "truncated": truncated,
        "limit": request.companion_limit,
    }
    result.update(state="found", sections=sections)
    if len(encode_species_information(result)) > MAX_RESPONSE_BYTES:
        refused = _empty_result(request)
        refused.update(
            state="refused",
            reason={
                "code": "species_information_response_over_budget",
                "message": "The bounded response is too large.",
            },
        )
        return refused
    return result


async def read_species_information(
    request: SpeciesInformationRequest,
    *,
    session_provider: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> dict[str, Any]:
    """Fail closed when the reviewed authoring reader cannot answer."""
    try:
        return await _read_species_information(request, session_provider=session_provider)
    except (SQLAlchemyError, OSError, TimeoutError):
        result = _empty_result(request)
        result.update(
            state="refused",
            reason={
                "code": "authoring_database_unavailable",
                "message": "The reviewed authoring database could not complete the bounded read.",
            },
        )
        return result
