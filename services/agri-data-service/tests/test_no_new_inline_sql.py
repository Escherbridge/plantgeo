"""Advisory ratchet: no *new* multi-line ``text(\"\"\"...\"\"\")`` SQL under ``src/``.

The standard is `conductor/code_styleguides/sql.md`, section *Runtime query SQL
lives in dedicated files, not Python strings*, and the operating manual is
``src/agri_data_service/sql/AGENTS.md``. This test does not demand the tree be
clean today -- it freezes today's real counts per module and fails only when one
goes **up**. Extracting a statement drops a module below its number, which
passes; ratchet the number down in the same commit so the ground is held, and
delete the entry when it reaches zero.

Advisory and ad-hoc by owner decision: it runs under ``scripts/check.py``, not
as a build or deploy gate. The walker is pure stdlib (``ast``), matches only a
call to a bare name ``text`` whose first argument is a string literal containing
a newline, and sees nothing outside ``src/`` -- ``alembic/versions/**`` is
immutable and lives above it, so it is excluded by construction.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"

# Ratcheted 2026-08-08 after the extraction sweep moved 79 statements into
# ``src/agri_data_service/sql/**``. Ten modules reached zero and their entries are gone, per the
# docstring above; cli.py joined them on 2026-08-08 when its five forecast statements moved to
# `sql/cli/`. What remains is not debt of the same kind -- each survivor has a reason:
#
#   execution/geospatial_pilot.py   FROZEN, never extract. `DERIVED_VALUES_SQL` is sha256'd into an
#                                   immutable provenance receipt, so any byte change -- including
#                                   adding a comment header -- re-identifies every receipt derived
#                                   from it. A revised formula gets a new method version, never a
#                                   rewritten digest. See sql/AGENTS.md, "Checksummed SQL is frozen".
#   ingest/commands.py              out of scope for that sweep (owned by the ML/CLI lane). It rose
#                                   from 2 to 3 when `_JOB_LANE_TIMESTAMPS` was added; the number
#                                   below is its true current count, and extracting all three to
#                                   `sql/ingest/` is the follow-up.
#   ingest/validation/queries.py    `_SET_READ_ONLY_SNAPSHOT` stays inline for good: `SET LOCAL`
#                                   accepts no bind parameters and the whole statement is one line
#                                   (it only trips this walker because its marker comment makes the
#                                   literal span two lines). Its nine report queries were extracted.
#                                   Keyed on the new path: `ingest/validation.py` became a package on
#                                   2026-08-08 and its SQL bindings moved to `queries.py` unchanged.
BASELINE: dict[str, int] = {
    "execution/geospatial_pilot.py": 1,
    "ingest/commands.py": 3,
    "ingest/validation/queries.py": 1,
}


def _is_multiline_text_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """True for ``text(\"\"\"...\"\"\")`` -- a call to the bare name ``text`` on a multi-line string literal."""
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Name) or node.func.id != "text" or not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Constant) and isinstance(first.value, str) and "\n" in first.value


def _inline_sql_lines(module: Path) -> list[int]:
    """Line numbers of every multi-line inline-SQL call in one module."""
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    return sorted(node.lineno for node in ast.walk(tree) if _is_multiline_text_call(node))


def survey_inline_sql(source_root: Path = _SOURCE_ROOT) -> dict[str, list[int]]:
    """Map ``<module path relative to the package>`` to its inline-SQL line numbers."""
    found: dict[str, list[int]] = {}
    for module in sorted(source_root.rglob("*.py")):
        lines = _inline_sql_lines(module)
        if lines:
            found[module.relative_to(source_root).as_posix()] = lines
    return found


def test_no_module_exceeds_its_inline_sql_baseline() -> None:
    """Fail on new inline SQL; shrinking below the baseline is the intended direction."""
    regressions = [
        f"  {module}: {len(lines)} multi-line text(...) calls, baseline {BASELINE.get(module, 0)}"
        f" -- call sites at line {', '.join(str(line) for line in lines)}"
        for module, lines in sorted(survey_inline_sql().items())
        if len(lines) > BASELINE.get(module, 0)
    ]
    assert not regressions, (
        "New multi-line inline SQL. Extract it to src/agri_data_service/sql/<package>/<name>.sql and load it\n"
        "with agri_data_service.db.sql_queries.load_query_sql -- see src/agri_data_service/sql/AGENTS.md.\n"
        "If you instead extracted SQL and a count fell, ratchet that module's BASELINE number down to match\n"
        "(and delete the entry once it reaches zero).\n" + "\n".join(regressions)
    )
