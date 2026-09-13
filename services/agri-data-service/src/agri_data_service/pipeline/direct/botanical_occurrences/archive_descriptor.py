"""Hardened `meta.xml` and `eml.xml` reading: no DTD, no entities, no network, no unknown rowType."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from xml.etree import ElementTree

from agri_data_service.foundation.botanical_occurrences.terms import (
    OCCURRENCE_ROW_TYPE,
    SUPPORTED_EXTENSION_ROW_TYPES,
    column_for_term,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Constructs that make an XML document able to read a file or open a socket. Refused on the BYTES,
#: before any parser sees them, because the cheapest way to be certain a parser never resolves an
#: external entity is to never hand it one.
_FORBIDDEN_XML_PATTERNS: Final[tuple[tuple[str, re.Pattern[bytes]], ...]] = (
    ("doctype_declaration", re.compile(rb"<!DOCTYPE", re.IGNORECASE)),
    ("entity_declaration", re.compile(rb"<!ENTITY", re.IGNORECASE)),
    ("external_id_system", re.compile(rb"\bSYSTEM\s+[\"']", re.IGNORECASE)),
    ("external_id_public", re.compile(rb"\bPUBLIC\s+[\"']", re.IGNORECASE)),
    ("processing_instruction_entity", re.compile(rb"<\?xml-stylesheet", re.IGNORECASE)),
)

#: The five DwC-A text-file attributes this lane honours. Everything else on a `<files>` element is
#: recorded nowhere and changes nothing, which is safer than half-implementing it.
_DEFAULT_FIELDS_TERMINATED_BY: Final = "\t"
_DEFAULT_LINES_TERMINATED_BY: Final = "\n"
_DEFAULT_ENCODING: Final = "UTF-8"

#: The DwC text namespace `meta.xml` declares. Element lookups strip namespaces rather than match
#: this, because real archives ship both namespaced and bare elements and both are the same document.
_META_NAMESPACE: Final = "http://rs.tdwg.org/dwc/text/"


class ArchiveDescriptorError(ValueError):
    """Raised when a descriptor cannot be read safely, or describes something this lane will not read."""


def _unescape(raw: str | None, fallback: str) -> str:
    """Decode the backslash escapes `meta.xml` writes separators with (`\\t`, `\\n`, `\\r`)."""
    if raw is None or raw == "":
        return fallback
    return raw.replace("\\t", "\t").replace("\\n", "\n").replace("\\r", "\r")


def parse_xml_document(payload: bytes) -> ElementTree.Element:
    """Parse XML with entity and DTD resolution refused, using defusedxml when it is installed.

    The byte scan runs FIRST and runs either way. `defusedxml` is the better parser and is used when
    present, but this lane must be safe in an environment that does not ship it, and a control that
    only works when an optional dependency is installed is not a control.
    """
    for reason, pattern in _FORBIDDEN_XML_PATTERNS:
        if pattern.search(payload):
            raise ArchiveDescriptorError(f"refused XML document containing {reason}")
    try:  # pragma: no cover - exercised only where the optional dependency is installed
        from defusedxml.ElementTree import (  # type: ignore[import-untyped]  # noqa: PLC0415 - optional dependency probed only here
            fromstring as defused_fromstring,
        )
    except ImportError:
        # Stdlib fallback, safe BECAUSE of the byte scan above: with no DOCTYPE and no <!ENTITY able
        # to reach the parser, the only entity references it can meet are the five XML predefines,
        # and any other reference fails as an undefined entity rather than resolving to a file.
        try:
            return ElementTree.fromstring(payload)
        except ElementTree.ParseError as error:
            raise ArchiveDescriptorError(f"XML document is not well formed: {error}") from error
    try:
        parsed: ElementTree.Element = defused_fromstring(payload)
    except Exception as error:
        raise ArchiveDescriptorError(f"XML document refused by defusedxml: {error}") from error
    return parsed


def _strip_namespace(tag: str) -> str:
    return tag.rpartition("}")[2] if "}" in tag else tag


def _find_all(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if _strip_namespace(child.tag) == name]


def _find_one(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    found = _find_all(element, name)
    return found[0] if found else None


@dataclass(frozen=True, slots=True)
class MemberDescriptor:
    """How to read ONE text member of the archive, exactly as `meta.xml` describes it."""

    row_type: str
    member_name: str
    fields_terminated_by: str
    lines_terminated_by: str
    fields_enclosed_by: str
    encoding: str
    ignore_header_lines: int
    #: Column index of the record id (`<id>` on the core, `<coreid>` on an extension).
    id_index: int | None
    #: Column index -> short column name, for every field whose term this lane knows.
    fields: Mapping[int, str]
    #: Column index -> the raw term URI, for EVERY field including unknown ones.
    terms: Mapping[int, str]


@dataclass(frozen=True, slots=True)
class ArchiveDescriptor:
    """The core member and the extensions this lane will read from one archive."""

    core: MemberDescriptor
    extensions: tuple[MemberDescriptor, ...]
    #: Extensions named by `meta.xml` whose rowType this lane does not read. Counted and reported so
    #: "unread" is visible in the release receipt rather than indistinguishable from "absent".
    unread_row_types: tuple[str, ...]


def _member_descriptor(element: ElementTree.Element, *, id_tag: str) -> MemberDescriptor:
    files = _find_one(element, "files")
    location = _find_one(files, "location") if files is not None else None
    if location is None or not (location.text or "").strip():
        raise ArchiveDescriptorError("a meta.xml member declares no <files><location>")
    identifier = _find_one(element, id_tag)
    id_index = int(identifier.get("index", "0")) if identifier is not None else None
    fields: dict[int, str] = {}
    terms: dict[int, str] = {}
    for field in _find_all(element, "field"):
        raw_index = field.get("index")
        term = (field.get("term") or "").strip()
        if raw_index is None or not term:
            # A defaulted field (a constant value with no column) or an unnamed one: nothing to read.
            continue
        index = int(raw_index)
        terms[index] = term
        column = column_for_term(term)
        if column is not None:
            fields[index] = column
    return MemberDescriptor(
        row_type=(element.get("rowType") or "").strip(),
        member_name=(location.text or "").strip(),
        fields_terminated_by=_unescape(element.get("fieldsTerminatedBy"), _DEFAULT_FIELDS_TERMINATED_BY),
        lines_terminated_by=_unescape(element.get("linesTerminatedBy"), _DEFAULT_LINES_TERMINATED_BY),
        fields_enclosed_by=_unescape(element.get("fieldsEnclosedBy"), ""),
        encoding=(element.get("encoding") or _DEFAULT_ENCODING).strip(),
        ignore_header_lines=int(element.get("ignoreHeaderLines") or "0"),
        id_index=id_index,
        fields=fields,
        terms=terms,
    )


def parse_meta_descriptor(payload: bytes) -> ArchiveDescriptor:
    """Read `meta.xml` into one core descriptor plus the extensions this lane can read.

    An archive whose core `rowType` is not `dwc:Occurrence` is REFUSED rather than read: a Taxon-core
    archive is a checklist, and reading its rows as occurrences would manufacture specimen records
    out of a name list.
    """
    root = parse_xml_document(payload)
    if _strip_namespace(root.tag) != "archive":
        raise ArchiveDescriptorError(f"meta.xml root is <{_strip_namespace(root.tag)}>, not <archive>")
    core_element = _find_one(root, "core")
    if core_element is None:
        raise ArchiveDescriptorError("meta.xml declares no <core>")
    core = _member_descriptor(core_element, id_tag="id")
    if core.row_type != OCCURRENCE_ROW_TYPE:
        raise ArchiveDescriptorError(f"core rowType {core.row_type!r} is not an occurrence core; refused")
    extensions: list[MemberDescriptor] = []
    unread: list[str] = []
    for element in _find_all(root, "extension"):
        descriptor = _member_descriptor(element, id_tag="coreid")
        if descriptor.row_type in SUPPORTED_EXTENSION_ROW_TYPES:
            extensions.append(descriptor)
        else:
            unread.append(descriptor.row_type)
    return ArchiveDescriptor(core=core, extensions=tuple(extensions), unread_row_types=tuple(unread))


@dataclass(frozen=True, slots=True)
class EmlFacts:
    """The minimum the release receipt needs from `eml.xml`; everything else stays in the archive."""

    package_id: str | None
    title: str | None
    pub_date: str | None
    rights_uri: str | None
    publisher: str | None
    #: The publisher's own statement of which records its coordinate policy applies to, verbatim.
    coordinate_policy_scope: str | None


_LICENSE_URL_PATTERN: Final = re.compile(r"https?://[^\s\"'<>]+")
_COORDINATE_POLICY_PATTERN: Final = re.compile(
    r"[^.]*\b(coordinate|georeference|locality|location)\b[^.]*\.", re.IGNORECASE
)


def _first_text(root: ElementTree.Element, *names: str) -> str | None:
    for element in root.iter():
        if _strip_namespace(element.tag) in names and (element.text or "").strip():
            return (element.text or "").strip()
    return None


def _all_text(root: ElementTree.Element, *names: str) -> str:
    return " ".join(
        (element.text or "").strip()
        for element in root.iter()
        if _strip_namespace(element.tag) in names and (element.text or "").strip()
    )


def parse_eml_facts(payload: bytes) -> EmlFacts:
    """Read the release-identifying facts out of `eml.xml` through the same hardened parser.

    Minimal on purpose. Every value here also exists in the archive bytes whose hash the release row
    carries, so this is a convenience index over the EML, not a substitute for it, and a fact this
    parser cannot find is reported as `None` rather than guessed from a sibling element.
    """
    root = parse_xml_document(payload)
    # EML licence URLs live on <ulink url="..."> as an attribute, not the element's own text
    # (its text node, if any, precedes the nested <citetitle> caption) -- so the URL must be
    # read from the attribute, and the surrounding paragraph text is searched only as a fallback
    # for a source that inlines the bare URL as plain text instead of a <ulink>.
    ulink_urls = " ".join(
        element.get("url", "")
        for element in root.iter()
        if _strip_namespace(element.tag) == "ulink" and element.get("url")
    )
    rights_text = ulink_urls or _all_text(root, "intellectualRights", "para", "ulink", "citetitle")
    license_match = _LICENSE_URL_PATTERN.search(rights_text)
    policy_source = _all_text(root, "intellectualRights", "para", "abstract", "additionalInfo")
    policy_match = _COORDINATE_POLICY_PATTERN.search(policy_source)
    return EmlFacts(
        package_id=(root.get("packageId") or "").strip() or None,
        title=_first_text(root, "title"),
        pub_date=_first_text(root, "pubDate"),
        rights_uri=license_match.group(0) if license_match else None,
        publisher=_first_text(root, "organizationName"),
        coordinate_policy_scope=policy_match.group(0).strip() if policy_match else None,
    )


__all__ = [
    "ArchiveDescriptor",
    "ArchiveDescriptorError",
    "EmlFacts",
    "MemberDescriptor",
    "parse_eml_facts",
    "parse_meta_descriptor",
    "parse_xml_document",
]
