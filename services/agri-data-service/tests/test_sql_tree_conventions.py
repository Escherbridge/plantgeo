"""Promote the extraction sweep's ad-hoc trap-checker into a permanent, parametrized suite.

Enforces four independent conventions from ``src/agri_data_service/sql/AGENTS.md`` against
every ``.sql`` file discovered under ``src/agri_data_service/sql/`` at collection time, so a
newly extracted file is covered automatically without touching this module:

  a. HEADER        -- the file opens with ``-- Purpose:`` and ``-- Loaded by:`` lines within
                       its first 24 lines (a line-1 dispatch marker is allowed above the
                       header; see "Marker protocol" below).
  b. PHANTOM BIND   -- every bind name ``sqlalchemy.text()`` finds when it scans the *whole*
                       file also shows up once the file's comment lines are stripped out. A
                       name that only appears because of the comments is a phantom bind the
                       loading module can never supply -- see "Header/bind-param trap" in
                       sql/AGENTS.md.
  c. MARKER SAFETY  -- a bare ``-- <word>`` comment line is the test-dispatch marker protocol
                       (see "Marker protocol" in sql/AGENTS.md) and may only sit on line 1; the
                       same shape inside a walkthrough collides with that protocol.
  d. LOADED ONCE    -- every file is referenced by exactly one ``load_query_sql("...")`` call
                       somewhere under src/, and every such call found under src/ points at a
                       file that actually exists.

Rule (d)'s call sites are collected once, module-wide, by walking the ``ast`` of every module
under src/ rather than regex-scanning source text -- ``db/sql_queries.py``'s module docstring
quotes ``load_query_sql("ingest/insert_features.sql")`` as a worked example, and an AST walk
never mistakes prose inside a string constant for a second real call.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import sqlalchemy

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _SERVICE_ROOT / "src" / "agri_data_service"
_SQL_ROOT = _SRC_ROOT / "sql"

# The wheel-packaging smoke fixture (tests/test_sql_queries_loader.py). It is read directly by
# its own loader test, not referenced by any load_query_sql() call site under src/.
_LOADED_EXEMPT = frozenset({"db/_loader_smoke.sql"})

# Matches the same shape as the test-dispatch marker protocol: a comment line holding nothing
# but a single word, e.g. "-- claim_work_item".
_BARE_MARKER = re.compile(r"^--\s+(\w+)\s*$")

# The colon-prefixed-word lookalike sqlalchemy.text() itself scans for, used only to locate
# which comment line a phantom bind name came from for the failure message.
_BIND_LOOKALIKE = re.compile(r"(?<![:\w$]):(\w+)(?![:\w$])")

# Every documented header has both required lines well inside this window; see sql/AGENTS.md,
# "Documentation standard".
_HEADER_WINDOW_LINES = 24


def _discover_sql_files() -> list[Path]:
    """Every ``.sql`` file under the runtime query tree, sorted for stable test IDs."""
    return sorted(_SQL_ROOT.rglob("*.sql"))


def _relative_id(sql_path: Path) -> str:
    """The path this suite reports in messages, e.g. ``ingest/insert_features.sql``."""
    return sql_path.relative_to(_SQL_ROOT).as_posix()


def _comment_lines(lines: list[str]) -> list[tuple[int, str]]:
    """1-indexed ``(line number, text)`` pairs for every ``--`` comment line."""
    return [(number, line) for number, line in enumerate(lines, start=1) if line.lstrip().startswith("--")]


def _body_only(lines: list[str]) -> str:
    """The file with every comment line blanked out, everything else untouched."""
    return "\n".join("" if line.lstrip().startswith("--") else line for line in lines)


def _call_target_name(node: ast.Call) -> str | None:
    """The called function's bare name, whether invoked as ``f(...)`` or ``module.f(...)``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _load_query_sql_call_sites() -> dict[str, list[tuple[str, int]]]:
    """Map each ``load_query_sql("...")`` string argument to every ``(module, line)`` call site.

    Walks the AST of every module under src/ so a call embedded in a docstring's prose -- not
    executable code -- is never counted as a real reference.
    """
    sites: dict[str, list[tuple[str, int]]] = {}
    for module in sorted(_SRC_ROOT.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        rel_module = module.relative_to(_SRC_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_target_name(node) != "load_query_sql":
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                sites.setdefault(first_arg.value, []).append((rel_module, node.lineno))
    return sites


_SQL_FILES = _discover_sql_files()
_CALL_SITES = _load_query_sql_call_sites()


@pytest.mark.parametrize("sql_path", _SQL_FILES, ids=_relative_id)
def test_header_present(sql_path: Path) -> None:
    """HEADER rule: the file opens with '-- Purpose:' and '-- Loaded by:' lines."""
    rel = _relative_id(sql_path)
    lines = sql_path.read_text(encoding="utf-8").splitlines()
    head = "\n".join(lines[:_HEADER_WINDOW_LINES])
    missing = [marker for marker in ("-- Purpose:", "-- Loaded by:") if marker not in head]
    assert not missing, (
        f"{rel}: HEADER rule violated -- missing {missing} within the first "
        f"{_HEADER_WINDOW_LINES} lines. Every file under sql/ must open with a '-- Purpose:' "
        "line and a '-- Loaded by:' line (a line-1 dispatch marker may sit above them); see "
        "sql/AGENTS.md, 'Documentation standard'."
    )


@pytest.mark.parametrize("sql_path", _SQL_FILES, ids=_relative_id)
def test_no_phantom_bind_params(sql_path: Path) -> None:
    """PHANTOM BIND rule: a comment introduces no bind name the statement body lacks."""
    rel = _relative_id(sql_path)
    lines = sql_path.read_text(encoding="utf-8").splitlines()
    binds_in_whole_file = set(sqlalchemy.text("\n".join(lines))._bindparams)
    binds_in_body_only = set(sqlalchemy.text(_body_only(lines))._bindparams)
    phantom_names = binds_in_whole_file - binds_in_body_only
    offenders = [
        f"{rel}:{number}: phantom bind ':{name}' in a comment -> {line.strip()[:90]}"
        for name in sorted(phantom_names)
        for number, line in _comment_lines(lines)
        if _BIND_LOOKALIKE.search(line) and any(match == name for match in _BIND_LOOKALIKE.findall(line))
    ]
    assert not offenders, (
        f"{rel}: PHANTOM BIND rule violated -- sqlalchemy.text() finds a bind name in a comment "
        "that the statement body never uses, so the loading module can never supply it at "
        "execution time (see sql/AGENTS.md, 'Header/bind-param trap'). Reword the comment so no "
        "colon leads directly into the word; a colon trailing a word (e.g. \"text: 'none'\") is "
        "safe.\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("sql_path", _SQL_FILES, ids=_relative_id)
def test_marker_only_on_line_one(sql_path: Path) -> None:
    """MARKER SAFETY rule: a bare '-- <word>' comment line may only be the file's first line."""
    rel = _relative_id(sql_path)
    lines = sql_path.read_text(encoding="utf-8").splitlines()
    misplaced = [number for number, line in _comment_lines(lines) if number != 1 and _BARE_MARKER.match(line.strip())]
    assert not misplaced, (
        f"{rel}: MARKER SAFETY rule violated -- bare '-- <word>' comment line(s) at line(s) "
        f"{misplaced}, not line 1. That exact shape is the test-dispatch marker protocol (see "
        "sql/AGENTS.md, 'Marker protocol'); a walkthrough line matching it by accident must be "
        "reworded to more than one word."
    )


@pytest.mark.parametrize("sql_path", _SQL_FILES, ids=_relative_id)
def test_loaded_exactly_once(sql_path: Path) -> None:
    """LOADED rule: every file is referenced by exactly one load_query_sql(...) call under src/."""
    rel = _relative_id(sql_path)
    if rel in _LOADED_EXEMPT:
        pytest.skip(f"{rel} is the wheel-packaging smoke fixture; it is read directly, not via load_query_sql()")
    sites = _CALL_SITES.get(rel, [])
    if not sites:
        pytest.fail(
            f'{rel}: LOADED rule violated -- ORPHAN. No load_query_sql("{rel}") call anywhere '
            "under src/. Wire it up at its call site, or delete the file if it is unused."
        )
    site_list = ", ".join(f"{module}:{lineno}" for module, lineno in sites)
    assert len(sites) == 1, (
        f"{rel}: LOADED rule violated -- referenced {len(sites)} times, not exactly once: "
        f"{site_list}. Each runtime query file should have one call site; consolidate the "
        "callers or split the query if two call sites genuinely need independent copies."
    )


def test_every_load_query_sql_call_resolves_to_an_existing_file() -> None:
    """LOADED rule (converse): every load_query_sql("...") argument under src/ names a real file."""
    dangling = [
        f'  load_query_sql("{rel}") at {module}:{lineno} -- no such file under {_SQL_ROOT}'
        for rel, sites in sorted(_CALL_SITES.items())
        if not (_SQL_ROOT / rel).is_file()
        for module, lineno in sites
    ]
    assert not dangling, "LOADED rule violated -- dangling load_query_sql() call(s):\n" + "\n".join(dangling)
