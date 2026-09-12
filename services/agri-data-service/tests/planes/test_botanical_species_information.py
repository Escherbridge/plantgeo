"""Contract tests for the transitional authoring species-information plane."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from agri_data_service.models.species import EvidenceReviewState, PollinatorValue, RelationshipType
from agri_data_service.planes.botanical_species_information import (
    MAX_COMPANION_LIMIT,
    SpeciesInformationRequest,
    SpeciesInformationRequestError,
    parse_species_information_request,
    read_species_information,
)

SPECIES_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Session:
    def __init__(self, species: Any, rows: list[tuple[Any, ...]]) -> None:
        self.species = species
        self.rows = rows
        self.statements: list[str] = []

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(str(statement))
        return self.species

    async def execute(self, statement: Any) -> _Rows:
        self.statements.append(str(statement))
        return _Rows(self.rows)


def _provider(session: _Session) -> Any:
    @asynccontextmanager
    async def provide() -> Any:
        yield session

    return provide


def _species(species_id: uuid.UUID, scientific: str, common: str) -> Any:
    return SimpleNamespace(
        id=species_id,
        scientific_name=scientific,
        common_name=common,
        usda_symbol=None,
        family="Fabaceae",
        growth_habit="forb",
        native_status=None,
        usda_zones=None,
        min_precip_mm=300.0,
        max_precip_mm=None,
        min_ph=None,
        max_ph=None,
        light_requirement="full_sun",
        drought_tolerance=None,
        salt_tolerance=None,
        nitrogen_fixer=False,
        pollinator_value=PollinatorValue.HIGH,
        edible=False,
        timber_value=False,
        guild_roles=None,
    )


def test_scope_requires_canonical_uuid_and_bounded_companions() -> None:
    parsed = parse_species_information_request(
        {"species_id": str(SPECIES_ID), "companion_limit": str(MAX_COMPANION_LIMIT)}
    )
    assert parsed.species_id == SPECIES_ID
    for invalid in ("Lupinus argenteus", str(SPECIES_ID).upper(), "latest"):
        with pytest.raises(SpeciesInformationRequestError):
            parse_species_information_request({"species_id": invalid})
    with pytest.raises(SpeciesInformationRequestError):
        parse_species_information_request({"species_id": str(SPECIES_ID), "name": "lupine"})


async def test_values_are_unpublished_unverified_and_missingness_is_explicit() -> None:
    session = _Session(_species(SPECIES_ID, "Lupinus argenteus", "silvery lupine"), [])
    result = await read_species_information(SpeciesInformationRequest(SPECIES_ID), session_provider=_provider(session))
    assert result["serving_source"] == "reviewed_authoring_database"
    assert result["publication_state"] == "not_published"
    assert result["profile_release_id"] is None
    growth = result["sections"]["growth_requirements"]["fields"]
    assert growth["growth_habit"]["state"] == "unverified_authoring"
    assert growth["native_status"]["state"] == "unknown"
    assert growth["native_status"]["missingness"] == "not_reported"
    assert result["sections"]["fuel_tissue_composition"]["missingness"] == "not_modeled_in_authoring_schema"
    assert result["claim_limits"]["planting_recommendation"] == "refused"
    assert result["claim_limits"]["documented_occurrence"] == "not_evaluated_no_gis_data_queried"
    rendered_sql = "\n".join(session.statements).lower()
    assert "agri.species" in rendered_sql
    assert "agri.companion_relationships" in rendered_sql
    assert "geometry" not in rendered_sql


async def test_found_species_reports_section_missingness_when_every_field_is_absent() -> None:
    species = _species(SPECIES_ID, "Lupinus argenteus", "silvery lupine")
    for name in (
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
    ):
        setattr(species, name, None)
    result = await read_species_information(
        SpeciesInformationRequest(SPECIES_ID), session_provider=_provider(_Session(species, []))
    )
    growth = result["sections"]["growth_requirements"]
    assert growth["state"] == "unknown"
    assert growth["missingness"] == "not_reported"


async def test_only_pre_filtered_approved_companions_render_with_review_provenance() -> None:
    species = _species(SPECIES_ID, "Lupinus argenteus", "silvery lupine")
    other = _species(OTHER_ID, "Achillea millefolium", "yarrow")
    relationship = SimpleNamespace(
        id=uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        species_a_id=SPECIES_ID,
        species_b_id=OTHER_ID,
        relationship_type=RelationshipType.COMPANION,
        guild_function="pollinator support",
        notes=None,
        evidence_citation="Reviewed trial",
        evidence_source_url="https://example.invalid/trial",
        evidence_grade="B",
        applicability_context="temperate field plot",
        jurisdiction=None,
        review_state=EvidenceReviewState.APPROVED,
        reviewed_at=datetime(2026, 9, 1, tzinfo=UTC),
        reviewed_by="reviewer@example.invalid",
    )
    session = _Session(species, [(relationship, species, other)])
    result = await read_species_information(
        SpeciesInformationRequest(SPECIES_ID, companion_limit=1), session_provider=_provider(session)
    )
    evidence = result["sections"]["companion_evidence"]
    assert evidence["state"] == "approved_evidence"
    assert evidence["relationships"][0]["review_state"]["value"] == "approved"
    assert evidence["relationships"][0]["evidence_citation"]["value"] == "Reviewed trial"
    assert evidence["relationships"][0]["jurisdiction"]["missingness"] == "not_reported"
    statement = session.statements[-1].lower()
    assert "review_state" in statement
    assert "limit" in statement


async def test_database_failure_is_a_typed_refusal_with_no_profile_values() -> None:
    class _Unavailable(_Session):
        async def scalar(self, statement: Any) -> Any:
            del statement
            raise OperationalError("SELECT", {}, OSError("offline"))

    result = await read_species_information(
        SpeciesInformationRequest(SPECIES_ID),
        session_provider=_provider(_Unavailable(None, [])),
    )
    assert result["state"] == "refused"
    assert result["reason"]["code"] == "authoring_database_unavailable"
    assert result["sections"]["identity"]["fields"]["scientific_name"]["value"] is None
