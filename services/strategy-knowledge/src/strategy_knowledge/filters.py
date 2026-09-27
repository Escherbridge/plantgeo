"""One filter expression, compiled to a Chroma `where` for dense search and evaluated in Python for BM25.

Semantics (DESIGN.md sections 9-10; AGENTS.md section "Filters"): values within one field are OR'd, fields are
AND'd, land_use / region always also admit `general` and untagged records, and region also admits the broader
regions of `vocabulary.broader_regions` (`global`, and `north_america_general` for a North American region).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from strategy_knowledge.metadata import (
    GOAL_PREFIX,
    LAND_USE_PREFIX,
    LAND_USE_UNTAGGED,
    LINKED_STRATEGY_PREFIX,
    PHASE_PREFIX,
    REGION_PREFIX,
    REGION_UNTAGGED,
    SCALE_PREFIX,
    SOIL_PREFIX,
    MetadataValue,
)
from strategy_knowledge.vocabulary import EVIDENCE_RANK, GENERAL_VALUE, GOAL_STATES, broader_regions

UNTAGGED_LABEL: Final = "(untagged)"


@dataclass(frozen=True, slots=True)
class Equals:
    """`key == value`; a record without the key does not match."""

    key: str
    value: MetadataValue


@dataclass(frozen=True, slots=True)
class OneOf:
    """`key in values`; a record without the key does not match."""

    key: str
    values: tuple[MetadataValue, ...]


@dataclass(frozen=True, slots=True)
class NoneOf:
    """`key not in values`; only used on keys every record of the collection carries."""

    key: str
    values: tuple[MetadataValue, ...]


@dataclass(frozen=True, slots=True)
class AtLeast:
    """`key >= value` for a numeric key; a record without the key does not match."""

    key: str
    value: int | float


@dataclass(frozen=True, slots=True)
class AllOf:
    """Every clause matches."""

    clauses: tuple["FilterExpression", ...]


@dataclass(frozen=True, slots=True)
class AnyOf:
    """At least one clause matches."""

    clauses: tuple["FilterExpression", ...]


FilterExpression = Equals | OneOf | NoneOf | AtLeast | AllOf | AnyOf


def all_of(clauses: Iterable[FilterExpression | None]) -> FilterExpression | None:
    """AND of the non-empty clauses; Chroma rejects `$and` with fewer than two members, so unwrap."""
    present = tuple(clause for clause in clauses if clause is not None)
    if not present:
        return None
    return present[0] if len(present) == 1 else AllOf(present)


def any_of(clauses: Iterable[FilterExpression | None]) -> FilterExpression | None:
    """OR of the non-empty clauses, unwrapped the same way."""
    present = tuple(clause for clause in clauses if clause is not None)
    if not present:
        return None
    return present[0] if len(present) == 1 else AnyOf(present)


def to_where(expression: FilterExpression | None) -> dict[str, Any] | None:
    """Compile to a Chroma 1.x `where` (operators verified against chromadb/api/types.py validate_where)."""
    if expression is None:
        return None
    match expression:
        case Equals(key, value):
            compiled = {key: {"$eq": value}}
        case OneOf(key, values):
            compiled = {key: {"$in": list(values)}}
        case NoneOf(key, values):
            compiled = {key: {"$nin": list(values)}}
        case AtLeast(key, value):
            compiled = {key: {"$gte": value}}
        case AllOf(clauses):
            compiled = {"$and": [to_where(clause) for clause in clauses]}
        case AnyOf(clauses):
            compiled = {"$or": [to_where(clause) for clause in clauses]}
        case _:
            raise TypeError(f"not a filter expression: {expression!r}")
    return compiled


def matches(expression: FilterExpression | None, metadata: Mapping[str, Any]) -> bool:
    """Evaluate the expression against one record's metadata, with the same semantics as `to_where`."""
    if expression is None:
        return True
    match expression:
        case Equals(key, value):
            result = key in metadata and metadata[key] == value
        case OneOf(key, values):
            result = key in metadata and metadata[key] in values
        case NoneOf(key, values):
            result = metadata.get(key) not in values
        case AtLeast(key, value):
            found = metadata.get(key)
            result = isinstance(found, int | float) and not isinstance(found, bool) and found >= value
        case AllOf(clauses):
            result = all(matches(clause, metadata) for clause in clauses)
        case AnyOf(clauses):
            result = any(matches(clause, metadata) for clause in clauses)
        case _:
            raise TypeError(f"not a filter expression: {expression!r}")
    return result


@dataclass(frozen=True, slots=True)
class FilterRequest:
    """Every filter a search tool accepts; empty fields do not constrain."""

    goals: tuple[str, ...] = ()
    land_use: tuple[str, ...] = ()
    region: tuple[str, ...] = ()
    soil_conditions: tuple[str, ...] = ()
    scale: tuple[str, ...] = ()
    category: tuple[str, ...] = ()
    fire_phase: tuple[str, ...] = ()
    min_evidence: str | None = None
    study_type: tuple[str, ...] = ()
    direction: str | None = None
    source_id: str | None = None
    content_type: tuple[str, ...] = ()
    relevance: tuple[str, ...] = ()
    #: A strategy scope: its canonical id first, then every alias it absorbed (a record linked to any matches).
    strategy_ids: tuple[str, ...] = ()
    excluded_content_type: tuple[str, ...] = ()
    excluded_relevance: tuple[str, ...] = ()


def _flags(prefix: str, values: Iterable[str]) -> FilterExpression | None:
    return any_of(Equals(f"{prefix}{value}", True) for value in values)


def admitted_land_uses(values: Iterable[str]) -> tuple[str, ...]:
    """Requested land uses plus `general`; nothing when none was requested."""
    requested = tuple(values)
    return tuple(dict.fromkeys((*requested, GENERAL_VALUE))) if requested else ()


def admitted_regions(values: Iterable[str]) -> tuple[str, ...]:
    """Requested regions plus every broader region whose records also apply to them."""
    return tuple(dict.fromkeys(item for value in values for item in (value, *broader_regions(value))))


def _admitting(prefix: str, admitted: tuple[str, ...], untagged_key: str) -> FilterExpression | None:
    if not admitted:
        return None
    return any_of([*(Equals(f"{prefix}{value}", True) for value in admitted), Equals(untagged_key, True)])


def _membership(key: str, included: tuple[str, ...], excluded: tuple[str, ...]) -> FilterExpression | None:
    if included:
        return OneOf(key, included)
    return NoneOf(key, excluded) if excluded else None


def build_filter(request: FilterRequest) -> FilterExpression | None:
    """Translate a request into one expression (AND across fields, OR within a field)."""
    goals = any_of(OneOf(f"{GOAL_PREFIX}{goal}", GOAL_STATES) for goal in request.goals)
    return all_of(
        [
            goals,
            _admitting(LAND_USE_PREFIX, admitted_land_uses(request.land_use), LAND_USE_UNTAGGED),
            _admitting(REGION_PREFIX, admitted_regions(request.region), REGION_UNTAGGED),
            _flags(SOIL_PREFIX, request.soil_conditions),
            _flags(SCALE_PREFIX, request.scale),
            OneOf("category", request.category) if request.category else None,
            _flags(PHASE_PREFIX, request.fire_phase),
            AtLeast("evidence_rank", EVIDENCE_RANK[request.min_evidence]) if request.min_evidence else None,
            OneOf("study_type", request.study_type) if request.study_type else None,
            Equals("direction", request.direction) if request.direction else None,
            Equals("source_id", request.source_id) if request.source_id else None,
            _membership("content_type", request.content_type, request.excluded_content_type),
            _membership("relevance", request.relevance, request.excluded_relevance),
            _flags(LINKED_STRATEGY_PREFIX, request.strategy_ids),
        ],
    )


def describe(request: FilterRequest) -> dict[str, Any]:
    """The filters actually applied, as the response echoes them back to the agent."""
    described: dict[str, Any] = {}
    for field_name in FilterRequest.__dataclass_fields__:
        value = getattr(request, field_name)
        if value not in ((), None):
            described[field_name] = list(value) if isinstance(value, tuple) else value
    if request.land_use:
        described["land_use_also_admits"] = [GENERAL_VALUE, UNTAGGED_LABEL]
    if request.region:
        broader = [value for value in admitted_regions(request.region) if value not in request.region]
        described["region_also_admits"] = [*broader, UNTAGGED_LABEL]
    if request.min_evidence:
        described["min_evidence_rank"] = EVIDENCE_RANK[request.min_evidence]
    return described
