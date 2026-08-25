"""`db/tools/verify_stamp_target.py` exists, is read-only on its target, and gates what it claims to.

WHY THIS FILE EXISTS. Before 2026-08-25 the stamp had no procedure anywhere in the repository: a
repo-wide grep for `20260825_0000` returned the revision, two pins, some tests and docstrings, and
nothing an operator could run. The track's own risk register requires a `pg_dump` diff to gate the
stamp; there was no such tool. This asserts the tool is present and keeps its two most important
promises -- it never writes to the target, and `timescaledb` is a hard gate rather than a note.

The gates that need two live databases are exercised by running the tool, not by this module. What
is checked here is structural and cannot run dark.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.test_alembic_baseline_contract import executed_literals

if TYPE_CHECKING:
    from types import ModuleType

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_TOOL = _SERVICE_ROOT / "db" / "tools" / "verify_stamp_target.py"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_stamp_target", _TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_pre_stamp_verifier_exists_and_is_runnable() -> None:
    """The missing procedure, as tooling. Prose alone was what the review rejected."""
    assert _TOOL.is_file(), "there is no pre-stamp verification tool; a stamp would be unverifiable"
    module = _tool()
    assert callable(module.main)
    assert module.BASELINE_REVISION == "20260825_0000"


def test_timescaledb_is_a_gate_and_not_a_warning() -> None:
    """Skipping `20260825_0026` is safe only where the extension was already dropped by hand."""
    module = _tool()
    assert "timescaledb" in module.FORBIDDEN_EXTENSIONS
    assert "timescaledb_toolkit" in module.FORBIDDEN_EXTENSIONS
    source = _TOOL.read_text(encoding="utf-8")
    assert "_gate_timescaledb_absent(psql, target_dsn)" in source, (
        "the timescaledb pre-check must be one of the gates whose failure returns a non-zero exit, "
        "not a line in the privilege report"
    )


def test_only_the_two_revisions_the_baseline_supersedes_may_be_stamped() -> None:
    """An earlier database has to be walked forward through the archive; a stamp would skip real DDL."""
    module = _tool()
    assert module.STAMPABLE_REVISIONS == ("20260817_0025", "20260825_0026")


def test_the_verifier_never_writes_to_its_target() -> None:
    """Read-only by construction: only the disposable reference database is ever created or dropped.

    Parsed rather than grepped, so a `CREATE`/`DROP` inside a docstring or a diff-rendering string
    cannot fail this, and a real one cannot hide from it.
    """
    executed = executed_literals(_TOOL)
    tree = ast.parse(_TOOL.read_text(encoding="utf-8"), filename=str(_TOOL))
    unscoped = [
        literal
        for literal in executed
        if ("CREATE DATABASE" in literal.upper() or "DROP DATABASE" in literal.upper())
        and "{database}" not in literal
        and "{arguments.reference_db_name}" not in literal
    ]
    assert not unscoped, (
        f"CREATE/DROP DATABASE not scoped to the disposable reference database: {unscoped}"
    )

    mutations = [
        literal
        for literal in executed
        if any(
            verb in literal.upper()
            for verb in ("INSERT INTO PUBLIC.ALEMBIC_VERSION", "UPDATE PUBLIC.ALEMBIC_VERSION", "DELETE FROM")
        )
    ]
    assert not mutations, f"the verifier writes to its target: {mutations}"

    # No subprocess argv may carry `stamp`: the tool reports that a stamp is safe, it never runs one.
    argv_elements = [
        element.value
        for node in ast.walk(tree)
        if isinstance(node, ast.List)
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]
    assert "stamp" not in argv_elements, "the pre-stamp verifier builds an `alembic stamp` command line"


def test_a_gate_renders_its_evidence_not_just_its_verdict() -> None:
    """An operator who cannot see WHY a gate failed will re-run it until it passes by accident."""
    module = _tool()
    rendered = module.Gate("example", False, "because of X").render()

    assert "FAIL" in rendered
    assert "because of X" in rendered
    assert "PASS" in module.Gate("example", True, "evidence").render()


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        (
            "postgresql://user:secret@switchback.proxy.rlwy.net:37967/plantgeo",
            "switchback.proxy.rlwy.net:37967/plantgeo",
        ),
        ("postgresql://plantgeo_owner:pw@127.0.0.1:5442/agri_baseline", "127.0.0.1:5442/agri_baseline"),
    ],
)
def test_the_target_is_announced_without_its_credentials(dsn: str, expected: str) -> None:
    """The operator must SEE which database is about to be stamped; they must not see the password."""
    module = _tool()
    rendered = module._redact(dsn)

    assert rendered == expected
    assert "secret" not in rendered
    assert "pw@" not in rendered
