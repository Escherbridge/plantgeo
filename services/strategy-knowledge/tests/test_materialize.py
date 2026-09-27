"""Passage windows: sentence-aware, at most 180 words, balanced, parent chunk_id + window index + line span."""

import itertools

from conftest import SOURCE_ID

from strategy_knowledge.corpus import CorpusStore, read_json
from strategy_knowledge.materialize import (
    MAXIMUM_WINDOW_WORDS,
    TextUnit,
    chunk_windows,
    materialize_store,
    pack_units,
    sentence_units,
)
from strategy_knowledge.metadata import passage_document
from strategy_knowledge.models import Chunk


def _chunk(line_start: int, line_end: int) -> Chunk:
    return Chunk(
        chunk_id=f"{SOURCE_ID}#L{line_start}-{line_end}",
        line_start=line_start,
        line_end=line_end,
        content_type="practice_guidance",
        relevance="core",
    )


def test_sentence_units_split_on_terminal_punctuation_and_blank_lines() -> None:
    text = 'One two. Three "four" five!\n\nHeading without stop\nSix seven? Decimal 3.5 stays'
    units = sentence_units(text)
    pieces = [text[unit.start : unit.end] for unit in units]
    assert pieces == ["One two.", 'Three "four" five!', "Heading without stop\nSix seven?", "Decimal 3.5 stays"]
    assert [unit.words for unit in units] == [2, 3, 5, 3]


def test_an_over_long_sentence_is_cut_into_near_equal_runs() -> None:
    text = " ".join(f"word{index}" for index in range(400))
    units = sentence_units(text)
    assert [unit.words for unit in units] == [133, 134, 133]
    assert text[units[1].start : units[1].end].split()[0] == "word133"


def _sizes(windows: list[list[TextUnit]]) -> list[int]:
    return [sum(unit.words for unit in window) for window in windows]


def test_pack_units_balances_windows_under_the_cap() -> None:
    units = [TextUnit(index * 10, index * 10 + 9, 50) for index in range(4)]
    assert _sizes(pack_units(units)) == [100, 100]
    assert pack_units([]) == []


def test_pack_units_leaves_no_tiny_tail_when_a_merge_fits() -> None:
    units = [TextUnit(0, 9, 90), TextUnit(10, 19, 90), TextUnit(20, 29, 20)]
    assert _sizes(pack_units(units)) == [90, 110]
    many = [TextUnit(index, index + 1, 12) for index in range(40)]
    sizes = _sizes(pack_units(many))
    assert len(sizes) == 3
    assert max(sizes) - min(sizes) <= 24
    assert all(size <= MAXIMUM_WINDOW_WORDS for size in sizes)


def test_the_section_trail_counts_inside_the_window_budget() -> None:
    section_path = ["Chapter thirteen making and using composts", "Using compost on burned ground"]
    lines = [f"Sentence number {index} talks about soil cover and erosion on slopes today." for index in range(15)]
    chunk = Chunk(
        chunk_id="demo#L1-15",
        line_start=1,
        line_end=15,
        section_path=section_path,
        content_type="practice_guidance",
        relevance="core",
    )
    windows = chunk_windows("demo", chunk, lines)
    assert len(windows) == 2
    for window in windows:
        assert len(passage_document(section_path, window.text).split()) <= MAXIMUM_WINDOW_WORDS


def test_fixture_chunk_is_one_window_with_its_line_span(fixture_raw_lines: list[str]) -> None:
    windows = chunk_windows(SOURCE_ID, _chunk(14, 20), fixture_raw_lines)
    assert len(windows) == 1
    window = windows[0]
    assert window.passage_id == f"{SOURCE_ID}#L14-20#W0"
    assert window.chunk_id == f"{SOURCE_ID}#L14-20"
    assert (window.line_start, window.line_end) == (14, 19)
    assert window.text.startswith("Straw mulching on burned slopes Spread certified weed-free straw")
    assert window.word_count == len(window.text.split())


def test_long_chunk_splits_into_ordered_windows_with_contiguous_lines() -> None:
    lines = [f"Sentence number {index} talks about soil cover and erosion on slopes today." for index in range(40)]
    chunk = Chunk(
        chunk_id="demo#L1-40",
        line_start=1,
        line_end=40,
        content_type="practice_guidance",
        relevance="core",
    )
    windows = chunk_windows("demo", chunk, lines)
    assert len(windows) == 3
    assert [window.window_index for window in windows] == [0, 1, 2]
    assert all(window.word_count <= MAXIMUM_WINDOW_WORDS for window in windows)
    assert sum(window.word_count for window in windows) == 40 * 12
    assert windows[0].line_start == 1
    assert windows[-1].line_end == 40
    for previous, following in itertools.pairwise(windows):
        assert following.line_start == previous.line_end + 1


def test_materialize_store_writes_every_usable_source(fixture_store: CorpusStore) -> None:
    counts = materialize_store(fixture_store)
    assert counts == {SOURCE_ID: 5}
    written = read_json(fixture_store.materialized_path / f"{SOURCE_ID}.json")
    assert next(window["chunk_id"] for window in written) == f"{SOURCE_ID}#L7-13"
