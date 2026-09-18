"""West/south/east/north envelope parsing and CLI-argument formatting.

Layer L0 (`foundation`). Extracted 2026-09-18 from `ingest/mtbs.py`, which minted these mechanisms
for MTBS's own `--bbox` flag; six `pipeline/direct/<domain>` lanes came to import
`inline_bbox_value` from a single source's ingest module -- the exact cross-domain leak
`conductor/code_styleguides/layer-lanes.md` §5a names directly: "the shared half moves down; the
dependents never move sideways." See `foundation/geography/AGENTS.md` for the full extraction record
and `ingest/mtbs.py`'s `__getattr__` for the deprecated re-export this move left behind.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Final

BoundingBox = tuple[float, float, float, float]

#: argparse reads a leading `-` in `-125,42,...` as a flag; this is the option
#: `format_bounding_box_inline` rewrites before argparse ever sees the value.
BBOX_OPTION: Final = "--bbox"
BBOX_ORDINATE_COUNT: Final = 4


def parse_bounding_box(value: str) -> BoundingBox:
    """Parse a `west,south,east,north` bounding box, rejecting an inverted or malformed envelope."""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != BBOX_ORDINATE_COUNT:
        raise argparse.ArgumentTypeError("bbox must be west,south,east,north")
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError("bbox ordinates must be numbers") from error
    if west >= east or south >= north:
        raise argparse.ArgumentTypeError("bbox must satisfy west < east and south < north")
    return west, south, east, north


def format_bounding_box_inline(argv: Sequence[str]) -> list[str]:
    """Rewrite `--bbox -125,42,...` to `--bbox=-125,42,...`; argparse reads a leading `-` as a flag."""
    items = list(argv)
    normalised: list[str] = []
    index = 0
    while index < len(items):
        if items[index] == BBOX_OPTION and index + 1 < len(items):
            normalised.append(f"{BBOX_OPTION}={items[index + 1]}")
            index += 2
            continue
        normalised.append(items[index])
        index += 1
    return normalised


#: Documented alias for the pre-extraction name, importable directly from this module. Not
#: deprecated here -- only `ingest.mtbs.inline_bbox_value` warns; this is the mechanism's new home.
inline_bbox_value = format_bounding_box_inline
