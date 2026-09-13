"""Darwin Core term URIs mapped to the column names this lane writes."""

from __future__ import annotations

from typing import Final

_DWC: Final = "http://rs.tdwg.org/dwc/terms/"
_DC: Final = "http://purl.org/dc/terms/"

#: Every term URI this lane recognises, mapped to its short name. A `meta.xml` field whose term is
#: absent here is NOT dropped: `rows.py` keeps it in the verbatim JSON under its URI, because a term
#: this map has not learned yet is still something the publisher said.
TERM_COLUMNS: Final[dict[str, str]] = {
    f"{_DWC}occurrenceID": "occurrenceID",
    f"{_DWC}catalogNumber": "catalogNumber",
    f"{_DWC}basisOfRecord": "basisOfRecord",
    f"{_DWC}recordedBy": "recordedBy",
    f"{_DWC}recordNumber": "recordNumber",
    f"{_DWC}eventDate": "eventDate",
    f"{_DWC}verbatimEventDate": "verbatimEventDate",
    f"{_DWC}year": "year",
    f"{_DWC}month": "month",
    f"{_DWC}day": "day",
    f"{_DWC}scientificName": "scientificName",
    f"{_DWC}family": "family",
    f"{_DWC}genus": "genus",
    f"{_DWC}taxonRank": "taxonRank",
    f"{_DWC}identifiedBy": "identifiedBy",
    f"{_DWC}dateIdentified": "dateIdentified",
    f"{_DWC}identificationRemarks": "identificationRemarks",
    f"{_DWC}decimalLatitude": "decimalLatitude",
    f"{_DWC}decimalLongitude": "decimalLongitude",
    f"{_DWC}geodeticDatum": "geodeticDatum",
    f"{_DWC}coordinateUncertaintyInMeters": "coordinateUncertaintyInMeters",
    f"{_DWC}coordinatePrecision": "coordinatePrecision",
    f"{_DWC}country": "country",
    f"{_DWC}stateProvince": "stateProvince",
    f"{_DWC}county": "county",
    f"{_DWC}locality": "locality",
    f"{_DWC}informationWithheld": "informationWithheld",
    f"{_DWC}dataGeneralizations": "dataGeneralizations",
    f"{_DWC}establishmentMeans": "establishmentMeans",
    f"{_DWC}occurrenceStatus": "occurrenceStatus",
    f"{_DC}modified": "modified",
}

#: The two `rowType` values this lane reads. An archive whose core is anything else is rejected
#: rather than guessed at: a Taxon-core archive is a checklist, and reading it as occurrences would
#: turn a name list into fabricated specimen records.
OCCURRENCE_ROW_TYPE: Final = f"{_DWC}Occurrence"
IDENTIFICATION_ROW_TYPE: Final = f"{_DWC}Identification"

#: Extensions this lane preserves. Anything else in the archive is recorded in the release receipt's
#: extension counts and left unread, which is honest: unread is not the same as absent.
SUPPORTED_EXTENSION_ROW_TYPES: Final[frozenset[str]] = frozenset({IDENTIFICATION_ROW_TYPE})


def column_for_term(term: str) -> str | None:
    """Return the short column a term URI maps to, or None when this lane has not learned it."""
    return TERM_COLUMNS.get(term.strip())


__all__ = [
    "IDENTIFICATION_ROW_TYPE",
    "OCCURRENCE_ROW_TYPE",
    "SUPPORTED_EXTENSION_ROW_TYPES",
    "TERM_COLUMNS",
    "column_for_term",
]
