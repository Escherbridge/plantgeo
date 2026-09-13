"""Read a bounded, admitted WCVP v16 archive offline; see AGENTS.md."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from typing import TYPE_CHECKING, Literal

from agri_data_service.warehouse.botanical_species_profiles.contract import (
    AssertionContext,
    ProfileError,
    ReleaseRequest,
    SourceRelease,
    TaxonIdentity,
    TaxonName,
    TaxonRecord,
    TraitAssertion,
    TraitValue,
    canonical_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

MAX_ADMISSION_BYTES = 32_768
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_NAMES_BYTES = 350 * 1024 * 1024
MAX_SOURCE_ROWS = 1_600_000
MAX_SELECTED_TAXA = 20
MAX_NAMES_PER_TAXON = 200
WCVP_MEMBER = "wcvp_names.csv"
WCVP_HEADERS = (
    "plant_name_id",
    "ipni_id",
    "taxon_rank",
    "taxon_status",
    "family",
    "genus_hybrid",
    "genus",
    "species_hybrid",
    "species",
    "infraspecific_rank",
    "infraspecies",
    "parenthetical_author",
    "primary_author",
    "publication_author",
    "place_of_publication",
    "volume_and_page",
    "first_published",
    "nomenclatural_remarks",
    "geographic_area",
    "lifeform_description",
    "climate_description",
    "taxon_name",
    "taxon_authors",
    "accepted_plant_name_id",
    "basionym_plant_name_id",
    "replaced_synonym_author",
    "homotypic_synonym",
    "parent_plant_name_id",
    "powo_id",
    "hybrid_formula",
    "reviewed",
)
WCVP_FIELDS = {"lifeform_description": "growth_habit", "climate_description": "climate_summary"}
DEFAULT_AUTHORING_CENSUS_NOTE = (
    "No reviewed authoring census attached to this source snapshot; it cannot supersede existing relational rows."
)


def _archive_rows(path: Path, source: SourceRelease) -> Iterator[dict[str, str]]:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ProfileError("WCVP archive exceeds the bounded source size")
    with path.open("rb") as payload:
        digest = hashlib.file_digest(payload, "sha256").hexdigest()
    if digest != source.content_sha256:
        raise ProfileError("WCVP archive SHA-256 differs from its admitted source release")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if [member.filename for member in members].count(WCVP_MEMBER) != 1:
            raise ProfileError("WCVP names member must occur exactly once")
        member = archive.getinfo(WCVP_MEMBER)
        if member.flag_bits & 1 or not 0 < member.file_size <= MAX_NAMES_BYTES:
            raise ProfileError("WCVP names member is encrypted, empty or oversized")
        with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as decoded:
            reader = csv.DictReader(decoded, delimiter="|", quoting=csv.QUOTE_NONE)
            if tuple(reader.fieldnames or ()) != WCVP_HEADERS:
                raise ProfileError("WCVP source schema differs from the admitted v16 header")
            for count, row in enumerate(reader, start=1):
                if count > MAX_SOURCE_ROWS:
                    raise ProfileError("WCVP source exceeds its reviewed row ceiling")
                if None in row or any(value is None for value in row.values()):
                    raise ProfileError("WCVP source row does not match its declared fields")
                yield row


def _selected_rows(
    path: Path,
    source: SourceRelease,
    taxon_ids: tuple[str, ...],
) -> tuple[dict[str, dict[str, str]], dict[str, list[dict[str, str]]]]:
    if not 0 < len(taxon_ids) <= MAX_SELECTED_TAXA or len(set(taxon_ids)) != len(taxon_ids):
        raise ProfileError("select 1 to 20 distinct canonical WCVP plant_name_id values")
    if any(not identity.isascii() or not identity.isdigit() for identity in taxon_ids):
        raise ProfileError("WCVP selection requires numeric source IDs, never name text")
    selected: dict[str, dict[str, str]] = {}
    names: dict[str, list[dict[str, str]]] = {identity: [] for identity in taxon_ids}
    seen_names: set[str] = set()
    for row in _archive_rows(path, source):
        identity, accepted = row["plant_name_id"], row["accepted_plant_name_id"]
        if identity in names:
            if identity in selected:
                raise ProfileError("duplicate selected WCVP taxon ID")
            if accepted != identity or row["taxon_status"] != "Accepted" or row["reviewed"] != "Y":
                raise ProfileError("selected WCVP taxon must be accepted and in a family reviewed by Kew")
            selected[identity] = row
        elif accepted in names:
            if row["taxon_status"] != "Synonym" or identity in seen_names:
                raise ProfileError("linked WCVP name has an unsupported status or duplicate ID")
            names[accepted].append(row)
            seen_names.add(identity)
            if len(names[accepted]) > MAX_NAMES_PER_TAXON:
                raise ProfileError("WCVP synonym population exceeds the profile name ceiling")
    if set(selected) != set(taxon_ids):
        raise ProfileError("selected canonical WCVP IDs are absent from the pinned archive")
    return selected, names


def _taxon(row: Mapping[str, str], synonyms: list[dict[str, str]], source: SourceRelease) -> TaxonRecord:
    identity = TaxonIdentity(authority="WCVP", authority_version=source.version, taxon_id=row["plant_name_id"])
    names = tuple(
        TaxonName(
            name=f"{name['taxon_name']} {name['taxon_authors']}".strip(),
            source_name_id=name["plant_name_id"],
            accepted_taxon_id=identity.taxon_id,
            status="synonym",
            source_id=source.source_id,
            source_record_json=canonical_bytes(name).decode(),
        )
        for name in sorted(synonyms, key=lambda entry: entry["plant_name_id"])
    )
    return TaxonRecord(
        identity=identity,
        accepted_name=row["taxon_name"],
        rank=row["taxon_rank"],
        source_id=source.source_id,
        source_record_id=identity.taxon_id,
        source_name=f"{row['taxon_name']} {row['taxon_authors']}".strip(),
        synonyms=names,
        source_record_json=canonical_bytes(dict(row)).decode(),
    )


def _assertions(taxon: TaxonRecord, row: Mapping[str, str], source: SourceRelease) -> tuple[TraitAssertion, ...]:
    assertions: list[TraitAssertion] = []
    for column, trait in WCVP_FIELDS.items():
        value = TraitValue(text=row[column]) if row[column] else None
        assertions.append(
            TraitAssertion(
                assertion_id=f"wcvp-v{source.version}-{taxon.identity.taxon_id}-{column}",
                identity=taxon.identity,
                trait=trait,
                source_id=source.source_id,
                source_version=source.version,
                source_url=source.download_url,
                source_record_id=taxon.identity.taxon_id,
                licence_id=source.licence_id,
                evidence_locator=f"{WCVP_MEMBER}:plant_name_id={taxon.identity.taxon_id};column={column}",
                evidence_kind="categorical_summary",
                raw_value=value,
                normalized_value=value,
                missingness="not_reported" if value is None else None,
                qualifier=(
                    "Publisher categorical summary; not a quantitative establishment requirement or outcome effect."
                ),
                method="Identity mapping of WCVP v16 README-defined source field, without inferred thresholds.",
                context=AssertionContext(
                    geography=row["geographic_area"] or None,
                    conditions="WCVP reviewed=Y describes family peer review, not independent per-value measurement.",
                ),
                retrieved_at=source.retrieved_at,
                review_state="approved",
                reviewer=source.reviewer,
                review_reason=source.review_basis,
                authoring_provenance="admitted-wcvp-v16-source-field-adapter",
            )
        )
    return tuple(assertions)


def request_from_wcvp(  # noqa: PLR0913 - source and operator census inputs are explicitly separate.
    path: Path,
    source: SourceRelease,
    taxon_ids: tuple[str, ...],
    *,
    review_decision_id: str,
    authoring_census_state: Literal["unavailable", "reviewed_snapshot"] = "unavailable",
    authoring_census_note: str = DEFAULT_AUTHORING_CENSUS_NOTE,
) -> ReleaseRequest:
    """Prepare exact reviewed taxa and categorical source assertions without database or network I/O."""
    if source.source_id != "kew-wcvp" or source.version != "16" or source.licence_id != "CC-BY-3.0":
        raise ProfileError("the offline WCVP adapter only admits the reviewed v16 / CC-BY-3.0 contract")
    if not set(WCVP_FIELDS.values()).issubset(source.admitted_traits):
        raise ProfileError("WCVP categorical fields require explicit per-field source admission")
    if source.admitted_taxon_ids and not set(taxon_ids).issubset(source.admitted_taxon_ids):
        raise ProfileError("selected WCVP taxa are outside the reviewed source admission scope")
    rows, synonyms = _selected_rows(path, source, taxon_ids)
    taxa = tuple(_taxon(rows[identity], synonyms[identity], source) for identity in sorted(rows))
    assertions = tuple(
        assertion for taxon in taxa for assertion in _assertions(taxon, rows[taxon.identity.taxon_id], source)
    )
    return ReleaseRequest(
        taxa=taxa,
        assertions=assertions,
        sources=(source,),
        reviewer=source.reviewer,
        review_decision_id=review_decision_id,
        reviewed_at=source.reviewed_at,
        authoring_census_state=authoring_census_state,
        authoring_census_note=authoring_census_note,
    )


def read_admitted_source(path: Path, expected_sha256: str) -> SourceRelease:
    """Load only the exact externally reviewed admission file passed by its content digest."""
    if path.stat().st_size > MAX_ADMISSION_BYTES:
        raise ProfileError("source admission descriptor exceeds 32 KiB")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ProfileError("source admission descriptor digest mismatch")
    return SourceRelease.model_validate(json.loads(payload))
