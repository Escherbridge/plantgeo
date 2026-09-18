"""Stray-literal guard: fails on a new WGS84 footprint literal outside the region manifest.

`conductor/code_styleguides/federation.md` §1 "Permitted literal coordinates" and §5 step 4. Walks
every `src/agri_data_service/**/*.py` module with `ast` and flags an `Assign`/`AnnAssign` statement
whose right-hand side contains: a 4-number tuple/list that reads as a WGS84 bbox in either
hemisphere (west < east, south < north, in range, and NOT a zoom-tier ladder or an RGBA colour
tuple -- see `_looks_like_footprint_tuple` -- and either name-hinted, keyed west/south/east/north,
or a plausible region span); a short string equal to one of the PNW admin codes
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


def _name_is_footprint_hinted(name: str) -> bool:
    """`\\b` treats `_` as a word character, so `PACIFIC_NORTHWEST_BBOX` never reaches a boundary
    around `BBOX`; normalising underscores to spaces first restores the hint for real
    `snake_case`/`SCREAMING_SNAKE_CASE` Python identifiers, which is the common case here."""
    return bool(_NAME_HINT_PATTERN.search(name.replace("_", " ")))
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

#: A region footprint spans between this wide and this narrow on each axis -- narrower than a
#: state, wider than a neighbourhood. Zoom ladders and colour tuples are excluded by their own
#: explicit predicates, not by this range, so this only has to be "plausible for a region".
_MIN_PLAUSIBLE_REGION_SPAN_DEGREES: Final = 0.5
_MAX_PLAUSIBLE_REGION_SPAN_DEGREES: Final = 60.0

#: The highest zoom tier this service's ladders ever declare; sizes the zoom-ladder exclusion only.
_MAX_PLAUSIBLE_ZOOM_LADDER_VALUE: Final = 24

#: A string this long is prose (a citation, a docstring-adjacent rationale), not a declared
#: identifier or region code; capping length keeps the region-word/admin-code check from matching
#: sentences that merely mention the pilot region rather than restating its footprint. Aligned with
#: the TS guard's `MAX_DECLARED_STRING_LENGTH` at 40 (NIT 3, W3 review) -- the two guards had
#: independently picked 30 and 40 for the identical rule, and there was no reason for either number
#: to differ; re-scanned against the live tree at 40 and it introduces no new match.
_MAX_DECLARED_STRING_LENGTH: Final = 40

#: Files that ARE the manifest's own declaration -- never "a literal outside the manifest".
_ALLOWED_RELATIVE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "foundation/region/manifest.py",
        "foundation/region/__init__.py",
    }
)

#: Known offenders, enumerated by (path relative to `_SRC_ROOT`, description) -- NOT line number,
#: which shifts on any unrelated edit above it and fails this test in both directions for nobody's
#: benefit; the description already carries the offending value, which is what identifies the
#: literal. Migrated per federation.md §5 step 2/step 2b -- one per push, never re-literalled.
#: Removing an entry once its value is read from `load_region()` is the only edit this set may
#: receive; a new offender anywhere else fails this test rather than growing this set.
#:
#: Empty today: the 2026-09-18 waves already replaced every Python-side footprint constant this
#: guard can see with a manifest-reading function (`burn_severity_bounding_box()` in `ingest/mtbs.py`,
#: `botanical_seed_envelope()` in `foundation/botanical_occurrences/coordinates.py`), so there is no
#: module-level tuple left for this walk to see as an offender. Kept as a real (initially empty) set, not a comment, so the next offender this walk
#: finds has somewhere to go without inventing the shape.
KNOWN_OFFENDERS: Final[frozenset[tuple[str, str]]] = frozenset()


def _constant_number(node: ast.expr) -> float | None:
    """Reads a literal number out of `node`, unwrapping a leading unary minus; `None` otherwise."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _constant_number(node.operand)
        if inner is not None:
            return -inner
    return None


def _is_plausible_color_tuple(values: list[float]) -> bool:
    """All four values are integers in the 0-255 RGBA byte range -- a colour tuple, not a footprint."""
    return all(value == int(value) and 0 <= value <= 255 for value in values)  # noqa: PLR2004


def _is_plausible_zoom_ladder(values: list[float]) -> bool:
    """Strictly increasing small integers (e.g. `(0, 5, 9, 13)`) -- a zoom-tier ladder, not a footprint."""
    if not all(value == int(value) and 0 <= value <= _MAX_PLAUSIBLE_ZOOM_LADDER_VALUE for value in values):
        return False
    return all(values[i] > values[i - 1] for i in range(1, len(values)))


def _looks_like_footprint_tuple(elements: list[ast.expr], names: list[str]) -> tuple[float, ...] | None:  # noqa: PLR0911
    """Returns the four numbers if `elements` reads as a west/south/east/north WGS84 footprint.

    Hemisphere-neutral: a 4-tuple in range, ordered west<east and south<north, and NOT a zoom-tier
    ladder or an RGBA colour tuple, is a footprint when it is EITHER assigned to a footprint-hinting
    name OR its span is the size a region actually is (not a state, not a neighbourhood). Structural
    range/order membership alone is not sufficient -- zoom ladders and colour tuples clear that bar
    too -- so those two shapes are excluded explicitly before the name/span test runs.
    """
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
    if _is_plausible_color_tuple(values) or _is_plausible_zoom_ladder(values):  # type: ignore[arg-type]
        return None
    name_hinted = any(_name_is_footprint_hinted(name) for name in names)
    lon_span = east - west
    lat_span = north - south
    span_plausible = (
        _MIN_PLAUSIBLE_REGION_SPAN_DEGREES <= lon_span <= _MAX_PLAUSIBLE_REGION_SPAN_DEGREES
        and _MIN_PLAUSIBLE_REGION_SPAN_DEGREES <= lat_span <= _MAX_PLAUSIBLE_REGION_SPAN_DEGREES
    )
    if not (name_hinted or span_plausible):
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
                    footprint = _looks_like_footprint_tuple(list(subnode.elts), names)
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
            # without a 4-number sibling -- e.g. a lone `default_latitude = 46.5`. Bounded by
            # WHICH hint matched (NIT 12, W3 review): a `lat`/`latitude` name is bounded to
            # latitude's [-90, 90], not longitude's wider [-180, 180] -- a name-hinted latitude of
            # 100 is out of range and must not be waved through as though it were a longitude.
            for name in names:
                normalised_name = name.replace("_", " ")
                is_latitude_hinted = bool(re.search(r"(?i)\b(lat|latitude)\b", normalised_name))
                if is_latitude_hinted or _name_is_footprint_hinted(name):
                    scalar = _constant_number(value)
                    min_bound = _MIN_LATITUDE if is_latitude_hinted else _MIN_LONGITUDE
                    max_bound = _MAX_LATITUDE if is_latitude_hinted else _MAX_LONGITUDE
                    is_in_range = scalar is not None and min_bound <= scalar <= max_bound
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
    raw_found = _find_offenders()
    # Keyed by (path, description) -- NOT line -- so an unrelated edit above a known offender
    # cannot fail this test in both directions; the line is reported in the failure message only.
    found = {(path, description) for path, _line, description in raw_found}
    line_by_key = {(path, description): line for path, line, description in raw_found}
    known = set(KNOWN_OFFENDERS)
    new_offenders = found - known
    assert not new_offenders, (
        "New footprint/admin-code/region-name literal(s) found outside the region manifest "
        f"(federation.md §1): {[(p, d, line_by_key[(p, d)]) for p, d in sorted(new_offenders)]}. "
        "Either read the value from `agri_data_service.foundation.region.load_region()` in a "
        "behaviour-neutral one-line change, or add it to KNOWN_OFFENDERS with a citation."
    )
    stale_known_offenders = known - found
    assert not stale_known_offenders, (
        f"KNOWN_OFFENDERS lists {sorted(stale_known_offenders)} which this walk no longer finds; "
        "remove the stale entry rather than let the debt list grow unverifiable."
    )


def _footprint(west: float, south: float, east: float, north: float, name: str) -> tuple[float, ...] | None:
    elements = [ast.Constant(value=value) for value in (west, south, east, north)]
    return _looks_like_footprint_tuple(elements, [name])


def test_footprint_discriminator_catches_eastern_hemisphere() -> None:
    """S1: a fabricated eastern-hemisphere box (Kenya) must be caught, name-hinted or not."""
    assert _footprint(34.0, -1.5, 38.0, 1.5, "bbox") is not None
    # No name hint at all -- still caught, because the span (4 degrees each axis) is region-sized.
    assert _footprint(34.0, -1.5, 38.0, 1.5, "unrelated_name") is not None


def test_footprint_discriminator_excludes_zoom_ladder() -> None:
    """The zoom-tier ladder `(0, 5, 9, 13)` is in range and ordered, and must stay excluded."""
    assert _footprint(0, 5, 9, 13, "not_a_footprint_name") is None


def test_footprint_discriminator_excludes_color_tuple() -> None:
    """A 4-item RGBA-range integer tuple must stay excluded even though it is in range and ordered."""
    assert _footprint(12, 34, 56, 78, "not_a_footprint_name") is None
