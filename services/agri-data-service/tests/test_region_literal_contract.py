"""Stray-literal guard: fails on a new WGS84 footprint literal outside the region manifest.

`conductor/code_styleguides/federation.md` §1 "Permitted literal coordinates" and §5 step 4. Walks
every `src/agri_data_service/**/*.py` module with `ast` and flags an `Assign`/`AnnAssign` statement
whose right-hand side contains: a 4-number tuple/list that reads as a western-hemisphere WGS84
bbox (west < east, south < north, both west and east negative -- the discriminator that keeps this
from matching unrelated 4-element literals like zoom-tier ladders `(0, 5, 9, 13)` or RGBA colour
arrays, which are all non-negative); a short string equal to one of the PNW admin codes
(`US-WA`/`US-OR`/`US-ID`); or a short string containing the literal words `Pacific Northwest` or
`PNW`. See `AGENTS.md` in this directory for why the walk is assignment-scoped rather than every
string literal, and for the false positives that scoping choice was built to avoid.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

_SERVICE_ROOT: Final = Path(__file__).resolve().parents[1]
_SRC_ROOT: Final = _SERVICE_ROOT / "src" / "agri_data_service"

_NAME_HINT_PATTERN: Final = re.compile(r"(?i)\b(bbox|envelope|bounds|extent|lat|lon|longitude|latitude)\b")
_ADMIN_CODE_PATTERN: Final = re.compile(r"^US-(WA|OR|ID)$")
_REGION_WORD_PATTERN: Final = re.compile(r"\b(Pacific Northwest|PNW)\b")

#: The world-extent sentinel federation.md §1 permits explicitly: "no viewport" bbox.
_WORLD_EXTENT: Final = (-180.0, -90.0, 180.0, 90.0)

#: WGS84 bounds and the footprint-tuple arity, named so the range checks below read as domain
#: constants rather than magic numbers.
_FOOTPRINT_TUPLE_LENGTH: Final = 4
_MIN_LONGITUDE: Final = -180.0
_MAX_LONGITUDE: Final = 180.0
_MIN_LATITUDE: Final = -90.0
_MAX_LATITUDE: Final = 90.0

#: A string this long is prose (a citation, a docstring-adjacent rationale), not a declared
#: identifier or region code; capping length keeps the region-word/admin-code check from matching
#: sentences that merely mention the pilot region rather than restating its footprint.
_MAX_DECLARED_STRING_LENGTH: Final = 30

#: Files that ARE the manifest's own declaration -- never "a literal outside the manifest".
_ALLOWED_RELATIVE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "foundation/region/manifest.py",
        "foundation/region/__init__.py",
    }
)

#: Known offenders, enumerated by (path relative to `_SRC_ROOT`, line, description), migrated per
#: federation.md §5 step 2/step 2b -- one per push, never re-literalled. Removing an entry once its
#: value is read from `load_region()` is the only edit this set may receive; a new offender
#: anywhere else fails this test rather than growing this set.
#:
#: Empty today: the 2026-09-18 wave-2 push already pointed every Python-side footprint constant
#: this guard can see (`PACIFIC_NORTHWEST_BBOX` in `ingest/mtbs.py`, `SEED_ENVELOPE` in
#: `foundation/botanical_occurrences/coordinates.py`) at `load_region()` -- both are now tuples of
#: `Attribute` reads off the manifest, not `Constant` literals, so this walk does not see them as
#: offenders. Kept as a real (initially empty) set, not a comment, so the next offender this walk
#: finds has somewhere to go without inventing the shape.
KNOWN_OFFENDERS: Final[frozenset[tuple[str, int, str]]] = frozenset()


def _constant_number(node: ast.expr) -> float | None:
    """Reads a literal number out of `node`, unwrapping a leading unary minus; `None` otherwise."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _constant_number(node.operand)
        if inner is not None:
            return -inner
    return None


def _looks_like_footprint_tuple(elements: list[ast.expr]) -> tuple[float, ...] | None:  # noqa: PLR0911
    """Returns the four numbers if `elements` reads as a west/south/east/north WGS84 footprint."""
    if len(elements) != _FOOTPRINT_TUPLE_LENGTH:
        return None
    values = [_constant_number(element) for element in elements]
    if any(value is None for value in values):
        return None
    west, south, east, north = values  # type: ignore[misc]
    if tuple(values) == _WORLD_EXTENT:
        return None  # the permitted "no viewport" sentinel, not a footprint claim
    if not (
        _MIN_LONGITUDE <= west <= _MAX_LONGITUDE
        and _MIN_LATITUDE <= south <= _MAX_LATITUDE
        and _MIN_LONGITUDE <= east <= _MAX_LONGITUDE
        and _MIN_LATITUDE <= north <= _MAX_LATITUDE
    ):
        return None
    if not (west < east and south < north):
        return None
    # Every real regional/source footprint literal this codebase has ever declared sits entirely
    # in the western hemisphere (west AND east both negative); a zoom-tier ladder or an RGBA/index
    # array that happens to have four in-range numbers does not, so this is the discriminator that
    # keeps this scan from flooding on unrelated 4-element literals.
    if not (west < 0 and east < 0):
        return None
    return west, south, east, north


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _find_offenders() -> list[tuple[str, int, str]]:  # noqa: PLR0912
    """Walks the source tree, returning `(relative_path, line, description)` for every offender."""
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative_path = path.relative_to(_SRC_ROOT).as_posix()
        if relative_path in _ALLOWED_RELATIVE_PATHS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None:
                continue
            names = _assignment_targets(node)

            for subnode in ast.walk(value):
                if isinstance(subnode, (ast.Tuple, ast.List)):
                    footprint = _looks_like_footprint_tuple(list(subnode.elts))
                    if footprint is not None:
                        description = f"footprint tuple {footprint} assigned via {names or '<nested>'}"
                        offenders.append((relative_path, node.lineno, description))
                if isinstance(subnode, ast.Constant) and isinstance(subnode.value, str):
                    text = subnode.value
                    if len(text) > _MAX_DECLARED_STRING_LENGTH:
                        continue
                    if _ADMIN_CODE_PATTERN.match(text):
                        description = f"admin code literal {text!r} assigned via {names or '<nested>'}"
                        offenders.append((relative_path, node.lineno, description))
                    elif _REGION_WORD_PATTERN.search(text):
                        description = f"region-name literal {text!r} assigned via {names or '<nested>'}"
                        offenders.append((relative_path, node.lineno, description))

            # A scalar (single lat/lon component) assigned to a name that says what it is, even
            # without a 4-number sibling -- e.g. a lone `default_latitude = 46.5`.
            for name in names:
                if _NAME_HINT_PATTERN.search(name):
                    scalar = _constant_number(value)
                    is_in_range = scalar is not None and _MIN_LONGITUDE <= scalar <= _MAX_LONGITUDE
                    is_world_extent_component = scalar in (_MIN_LONGITUDE, _MAX_LONGITUDE, _MIN_LATITUDE, _MAX_LATITUDE)
                    if is_in_range and not is_world_extent_component:
                        description = f"name-hinted scalar {scalar} assigned to {name}"
                        offenders.append((relative_path, node.lineno, description))
    return offenders


def test_no_stray_footprint_literal_outside_known_offenders() -> None:
    """A new footprint/admin-code/region-name literal outside the manifest must fail this test.

    Passes today because `KNOWN_OFFENDERS` is empty and the live walk also finds nothing --
    `ingest/mtbs.py` and `foundation/botanical_occurrences/coordinates.py` already read their
    envelopes off `load_region()`. Removing an entry from `KNOWN_OFFENDERS` once it moves into the
    manifest is the only edit this test's contract allows to that set.
    """
    found = {(path, line, description) for path, line, description in _find_offenders()}
    known = set(KNOWN_OFFENDERS)
    new_offenders = found - known
    assert not new_offenders, (
        "New footprint/admin-code/region-name literal(s) found outside the region manifest "
        f"(federation.md §1): {sorted(new_offenders)}. Either read the value from "
        "`agri_data_service.foundation.region.load_region()` in a behaviour-neutral one-line change, "
        "or add it to KNOWN_OFFENDERS with a citation."
    )
    stale_known_offenders = known - found
    assert not stale_known_offenders, (
        f"KNOWN_OFFENDERS lists {sorted(stale_known_offenders)} which this walk no longer finds; "
        "remove the stale entry rather than let the debt list grow unverifiable."
    )
