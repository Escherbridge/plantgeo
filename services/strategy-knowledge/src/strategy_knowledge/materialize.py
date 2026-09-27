"""Chunk plans + raw text -> passage windows of at most 180 words (MiniLM's 256-token input limit).

See AGENTS.md section "Materializer" for why windows are balanced rather than greedily packed.
"""

import itertools
import logging
import math
import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from strategy_knowledge.corpus import CorpusStore, write_json
from strategy_knowledge.metadata import section_trail_words
from strategy_knowledge.models import Chunk, ChunkPlan, PassageWindow, dump_record

logger = logging.getLogger(__name__)

#: Words of one embedded passage document: section trail + window text.
MAXIMUM_WINDOW_WORDS: Final = 180
#: A sentence runs lazily to terminal punctuation (plus closing quotes/brackets) before whitespace, to a blank
#: line, or to the end of the text. Abbreviations over-split, which only moves a window boundary.
SENTENCE_PATTERN: Final = re.compile(
    r"\S.*?(?:[.!?][\"')\]\u201d\u2019]*(?=\s|$)|(?=\n[ \t]*\n)|$)",
    re.DOTALL,
)
WORD_PATTERN: Final = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class TextUnit:
    """A sentence (or a hard-split piece of an over-long one) as a character span with its word count."""

    start: int
    end: int
    words: int


def sentence_units(text: str, maximum_words: int = MAXIMUM_WINDOW_WORDS) -> list[TextUnit]:
    """Split text into sentence spans; a sentence over `maximum_words` is cut into near-equal word runs."""
    units: list[TextUnit] = []
    for match in SENTENCE_PATTERN.finditer(text):
        words = list(WORD_PATTERN.finditer(match.group()))
        if not words:
            continue
        pieces = math.ceil(len(words) / maximum_words)
        bounds = [round(index * len(words) / pieces) for index in range(pieces + 1)]
        for first, last in itertools.pairwise(bounds):
            piece = words[first:last]
            units.append(TextUnit(match.start() + piece[0].start(), match.start() + piece[-1].end(), len(piece)))
    return units


def _greedy_groups(units: Sequence[TextUnit], capacity: int) -> list[list[TextUnit]]:
    """Consecutive units packed greedily, closing a group when the next unit would exceed `capacity`."""
    groups: list[list[TextUnit]] = []
    current: list[TextUnit] = []
    current_words = 0
    for unit in units:
        if current and current_words + unit.words > capacity:
            groups.append(current)
            current, current_words = [], 0
        current.append(unit)
        current_words += unit.words
    if current:
        groups.append(current)
    return groups


def pack_units(units: Sequence[TextUnit], maximum_words: int = MAXIMUM_WINDOW_WORDS) -> list[list[TextUnit]]:
    """Balanced linear partition: the fewest windows under the cap, with the largest window as small as possible.

    Greedy packing at the cap gives the fewest windows `n`; a binary search then finds the smallest capacity that
    still packs into `n`, and packing at that capacity cannot leave a tail small enough to merge (AGENTS.md
    section "Materializer").
    """
    units = [unit for unit in units if unit.words > 0]
    if not units:
        return []
    window_count = len(_greedy_groups(units, maximum_words))
    total_words = sum(unit.words for unit in units)
    low = max(*(unit.words for unit in units), math.ceil(total_words / window_count))
    high = max(low, maximum_words)
    while low < high:
        middle = (low + high) // 2
        if len(_greedy_groups(units, middle)) <= window_count:
            high = middle
        else:
            low = middle + 1
    return _greedy_groups(units, low)


def window_budget(chunk: Chunk, maximum_words: int = MAXIMUM_WINDOW_WORDS) -> int:
    """Words a window of this chunk may hold once its section trail is counted inside the document budget."""
    return max(1, maximum_words - section_trail_words(chunk.section_path))


def chunk_windows(
    source_id: str,
    chunk: Chunk,
    raw_lines: Sequence[str],
    maximum_words: int = MAXIMUM_WINDOW_WORDS,
) -> list[PassageWindow]:
    """Cut one chunk's verbatim lines into windows (trail + text <= `maximum_words`) that keep the chunk_id."""
    budget = window_budget(chunk, maximum_words)
    lines = raw_lines[chunk.line_start - 1 : chunk.line_end]
    text = "\n".join(lines)
    line_offsets = [0]
    for line in lines[:-1]:
        line_offsets.append(line_offsets[-1] + len(line) + 1)

    def line_of(offset: int) -> int:
        return chunk.line_start + bisect_right(line_offsets, offset) - 1

    windows = []
    for index, group in enumerate(pack_units(sentence_units(text, budget), budget)):
        start, end = group[0].start, group[-1].end
        windows.append(
            PassageWindow(
                passage_id=f"{chunk.chunk_id}#W{index}",
                chunk_id=chunk.chunk_id,
                window_index=index,
                source_id=source_id,
                line_start=line_of(start),
                line_end=line_of(end - 1),
                word_count=sum(unit.words for unit in group),
                text=" ".join(text[start:end].split()),
            ),
        )
    return windows


def materialize_plan(plan: ChunkPlan, raw_lines: Sequence[str]) -> list[PassageWindow]:
    """Every window of every chunk in a plan, in line order."""
    return [
        window
        for chunk in sorted(plan.chunks, key=lambda chunk: chunk.line_start)
        for window in chunk_windows(plan.source_id, chunk, raw_lines)
    ]


def source_usability(store: CorpusStore, source_id: str | None = None) -> tuple[list[str], dict[str, list[str]]]:
    """(usable sources, excluded source -> reasons): usable means registered, planned and fresh."""
    sources = store.load_sources()
    selected = [source_id] if source_id else store.plan_source_ids()
    usable: list[str] = []
    excluded: dict[str, list[str]] = {}
    for candidate in selected:
        entry = sources.get(candidate)
        if entry is None:
            excluded[candidate] = ["no sources.json entry"]
        elif not store.plan_path(candidate).is_file():
            excluded[candidate] = ["no chunk plan"]
        elif not (freshness := store.freshness(candidate, entry.sha256)).usable:
            excluded[candidate] = freshness.problems()
        else:
            usable.append(candidate)
    for excluded_id, reasons in excluded.items():
        logger.warning("source %s left out: %s", excluded_id, "; ".join(reasons))
    return usable, excluded


def usable_source_ids(store: CorpusStore, source_id: str | None = None) -> list[str]:
    """Planned sources whose raw file and plan still agree; stale ones are logged and left out."""
    return source_usability(store, source_id)[0]


def materialize_store(store: CorpusStore, source_id: str | None = None) -> dict[str, int]:
    """Write each usable source's windows to `materialized/<source_id>.json`; return window counts."""
    counts: dict[str, int] = {}
    for usable_id in usable_source_ids(store, source_id):
        plan = store.load_plan(usable_id)
        if plan is None:
            continue
        windows = materialize_plan(plan, store.raw_lines(usable_id))
        write_json(store.materialized_path / f"{usable_id}.json", [dump_record(window) for window in windows])
        counts[usable_id] = len(windows)
    return counts
