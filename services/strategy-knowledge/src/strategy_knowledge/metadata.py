"""DESIGN.md section 8: scalar-only Chroma metadata with one-hot keys for every multi-valued field.

See AGENTS.md section "Metadata" for the `*_untagged` markers and the keys added beyond section 8.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Final

from strategy_knowledge.models import Chunk, Finding, PassageWindow, RegistryStrategy
from strategy_knowledge.vocabulary import EVIDENCE_RANK, FACET_LABELS

MetadataValue = str | int | float | bool
Metadata = dict[str, MetadataValue]

GOAL_PREFIX: Final = "goal_"
LAND_USE_PREFIX: Final = "lu_"
REGION_PREFIX: Final = "region_"
SOIL_PREFIX: Final = "soil_"
SCALE_PREFIX: Final = "scale_"
PHASE_PREFIX: Final = "phase_"
LINKED_STRATEGY_PREFIX: Final = "linked_"
#: Set when a record carries no land_use / region at all, so filters can admit untagged records.
LAND_USE_UNTAGGED: Final = "lu_untagged"
REGION_UNTAGGED: Final = "region_untagged"
LIST_SEPARATOR: Final = "|"
PATH_SEPARATOR: Final = " > "
RECORD_HASH_KEY: Final = "record_hash"
CORPUS_VERSION_KEY: Final = "corpus_version"
#: The raw facet text, kept for display because the embedded document is prefixed with the strategy name.
FACET_TEXT_KEY: Final = "facet_text"
WILDFIRE_GOAL: Final = "wildfire_resilience"
#: A passage's section trail is cut to its last this-many words, so a window always keeps >= 120 of its 180.
MAXIMUM_TRAIL_WORDS: Final = 60


def goal_keys(goals: Mapping[str, str]) -> Metadata:
    """`goal_<goal>` = "stated" | "inferred"; absent goals get no key."""
    return {f"{GOAL_PREFIX}{goal}": state for goal, state in sorted(goals.items())}


def one_hot(prefix: str, values: Iterable[str]) -> Metadata:
    """`<prefix><value>` = True for each value."""
    return {f"{prefix}{value}": True for value in sorted(set(values))}


def tagged_one_hot(prefix: str, values: Iterable[str], untagged_key: str) -> Metadata:
    """One-hot keys, or the untagged marker when there are no values."""
    keys = one_hot(prefix, values)
    return keys or {untagged_key: True}


def fire_phase_keys(fire_phases: Iterable[str], goals: Mapping[str, str]) -> Metadata:
    """`phase_<phase>` keys, kept only on records carrying wildfire_resilience (DESIGN.md section 3)."""
    return one_hot(PHASE_PREFIX, fire_phases) if WILDFIRE_GOAL in goals else {}


def joined(values: Iterable[str]) -> str:
    """Display-only `|`-joined list."""
    return LIST_SEPARATOR.join(value for value in values if value)


def record_hash(document: str, metadata: Mapping[str, MetadataValue]) -> str:
    """Content hash of a record, ignoring corpus_version, so an unchanged record re-indexes as a no-op."""
    stable = {key: value for key, value in metadata.items() if key not in {CORPUS_VERSION_KEY, RECORD_HASH_KEY}}
    payload = json.dumps({"document": document, "metadata": stable}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def sealed(document: str, metadata: Metadata, corpus_version: str) -> Metadata:
    """Stamp corpus_version and the record hash onto a record's metadata."""
    return {**metadata, CORPUS_VERSION_KEY: corpus_version, RECORD_HASH_KEY: record_hash(document, metadata)}


def facet_document(strategy_name: str, facet: str, text: str) -> str:
    """The embedded facet text `"<strategy name> (<facet label>): <facet text>"`, so every vector names its strategy."""
    return f"{strategy_name} ({FACET_LABELS.get(facet, facet)}): {text}"


def strategy_facet_metadata(strategy: RegistryStrategy, facet: str, facet_text: str = "") -> Metadata:
    """Metadata of one `strategy_facets` document (strategy x facet), carrying the raw facet text for display.

    `keywords` leaves the strategy name out: `facet_document` already prefixes it, and BM25 must count it once.
    """
    goals = dict(strategy.goals)
    lexical_terms = [*strategy.materials, *strategy.equipment, strategy.nrcs_practice_code or ""]
    metadata: Metadata = {
        "strategy_id": strategy.strategy_id,
        "facet": facet,
        FACET_TEXT_KEY: facet_text,
        "name": strategy.name,
        "category": strategy.category,
        "evidence_strength": strategy.evidence_strength,
        "evidence_rank": EVIDENCE_RANK[strategy.evidence_strength],
        "source_ids": joined(dict.fromkeys(citation.source_id for citation in strategy.sources)),
        "keywords": joined(lexical_terms),
        **goal_keys(goals),
        **tagged_one_hot(LAND_USE_PREFIX, strategy.land_use, LAND_USE_UNTAGGED),
        **tagged_one_hot(REGION_PREFIX, strategy.region, REGION_UNTAGGED),
        **one_hot(SOIL_PREFIX, strategy.soil_conditions),
        **one_hot(SCALE_PREFIX, strategy.scale),
        **fire_phase_keys(strategy.fire_phase, goals),
    }
    if strategy.family_id:
        metadata["family_id"] = strategy.family_id
    return metadata


def finding_document(finding: Finding) -> str:
    """DESIGN.md section 8: claim + conditions + magnitude."""
    return " ".join(part for part in (finding.claim, finding.conditions, finding.magnitude) if part)


def finding_metadata(finding: Finding, source_id: str) -> Metadata:
    """Metadata of one `findings` document."""
    return {
        "finding_id": finding.finding_id,
        "source_id": source_id,
        "study_type": finding.study_type,
        "direction": finding.direction,
        "evidence_strength": finding.evidence_strength,
        "evidence_rank": EVIDENCE_RANK[finding.evidence_strength],
        "line_start": finding.line_start,
        "line_end": finding.line_end,
        "strategy_ids": joined(finding.linked_strategy_ids),
        "keywords": joined(variable.name for variable in finding.variables),
        **goal_keys(finding.goals),
        **tagged_one_hot(LAND_USE_PREFIX, finding.land_use, LAND_USE_UNTAGGED),
        **tagged_one_hot(REGION_PREFIX, finding.region, REGION_UNTAGGED),
        **one_hot(LINKED_STRATEGY_PREFIX, finding.linked_strategy_ids),
    }


def section_trail(section_path: Iterable[str]) -> str:
    """The `a > b > c` heading trail a passage document starts with, cut to its last `MAXIMUM_TRAIL_WORDS` words."""
    trail = PATH_SEPARATOR.join(part for part in section_path if part)
    words = trail.split()
    return " ".join(words[-MAXIMUM_TRAIL_WORDS:]) if len(words) > MAXIMUM_TRAIL_WORDS else trail


def section_trail_words(section_path: Iterable[str]) -> int:
    """Words (separators included) the trail adds to a passage document; the materializer budgets for them."""
    return len(section_trail(section_path).split())


def passage_document(section_path: Iterable[str], text: str) -> str:
    """DESIGN.md section 8: the window's verbatim text, prefixed by its section path."""
    trail = section_trail(section_path)
    return f"{trail}\n\n{text}" if trail else text


def passage_metadata(chunk: Chunk, window: PassageWindow) -> Metadata:
    """Metadata of one `passages` document (a chunk window)."""
    return {
        "passage_id": window.passage_id,
        "chunk_id": chunk.chunk_id,
        "window_index": window.window_index,
        "source_id": window.source_id,
        "line_start": window.line_start,
        "line_end": window.line_end,
        "content_type": chunk.content_type,
        "relevance": chunk.relevance,
        "title": chunk.title,
        "section_path": PATH_SEPARATOR.join(chunk.section_path),
        "strategy_ids": joined(chunk.linked_strategy_ids),
        "finding_ids": joined(chunk.linked_finding_ids),
        "keywords": joined([chunk.title, *chunk.keywords]),
        **goal_keys(chunk.goals),
        **tagged_one_hot(LAND_USE_PREFIX, chunk.land_use, LAND_USE_UNTAGGED),
        **tagged_one_hot(REGION_PREFIX, chunk.region, REGION_UNTAGGED),
        **one_hot(SOIL_PREFIX, chunk.soil_conditions),
        **one_hot(LINKED_STRATEGY_PREFIX, chunk.linked_strategy_ids),
    }
