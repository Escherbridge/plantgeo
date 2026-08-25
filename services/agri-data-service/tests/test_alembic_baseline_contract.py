"""Static contracts for the greenfield baseline `20260825_0000` and the archive it replaced.

No database. These are the assertions that make the collapse safe to leave alone: that Alembic sees
exactly one revision, that the archive stays inert and complete, and that the deadlock the collapse
existed to break cannot reappear by someone copying a file back.

THE DEADLOCK, for whoever reads this next. `20260719_0001`'s preflight refuses to create schema
`agri` unless `timescaledb` is installed, and its text is immutable applied history.
`infra/local-warehouse/enable-extensions.sql` stopped creating that extension on 2026-08-25, and
`tests/test_migration_runtime_contract.py` asserts it stays that way. Between those two facts a
build from revision zero was impossible without an operator hand-installing an extension purely so
`20260825_0026` could drop it again. `test_no_revision_on_the_migration_path_requires_timescaledb`
below is the guard that keeps it broken.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

from agri_data_service.routes.health.contracts import REQUIRED_EXTENSIONS
from tests.test_alembic_head_pin_contract import revision_graph

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_VERSIONS = _SERVICE_ROOT / "alembic" / "versions"
_ARCHIVE = _SERVICE_ROOT / "alembic" / "archive"
_BASELINE = _VERSIONS / "20260825_0000_agri_greenfield_baseline.py"

BASELINE_REVISION = "20260825_0000"
ARCHIVED_ROOT_REVISION = "20260719_0001"
ARCHIVED_HEAD_REVISION = "20260825_0026"
ARCHIVED_REVISION_COUNT = 26


def _baseline() -> str:
    return _BASELINE.read_text(encoding="utf-8")


def executed_sql(path: Path) -> str:
    """Every string literal in a revision EXCEPT its module docstring, joined.

    The absence checks below have to read what a revision *executes*, not what it *explains*. A
    revision whose whole purpose is to remove ``timescaledb`` necessarily says the word in its
    docstring; asserting over the raw source made this file fail on its own prose, which is a test
    that punishes documentation. Dropping the module docstring and keeping every other literal is
    the narrowest cut that separates the two -- a `%s` of SQL hidden in a comment cannot execute,
    and a SQL constant cannot hide from `ast`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_docstring = ast.get_docstring(tree, clean=False)
    return "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != module_docstring
    )


def test_versions_holds_exactly_the_baseline_and_nothing_else() -> None:
    """Two revision files on the path is how `alembic upgrade head` becomes ambiguous again."""
    found = sorted(path.name for path in _VERSIONS.glob("*.py"))
    assert found == [_BASELINE.name], (
        f"alembic/versions/ must contain exactly {_BASELINE.name}, found {found}. "
        "Historical revisions belong in alembic/archive/ (see alembic/archive/AGENTS.md)."
    )


def test_the_baseline_is_the_root_and_the_head_at_once() -> None:
    revisions, parents = revision_graph(_VERSIONS)
    assert revisions == {BASELINE_REVISION}
    assert not parents, f"the baseline must declare no parent, but a down_revision names {sorted(parents)}"
    assert "down_revision: str | None = None" in _baseline()


def test_the_archive_is_complete_and_still_chains_end_to_end() -> None:
    """The archive is history, so it must stay walkable: one root, one head, no dangling parent."""
    files = sorted(path.name for path in _ARCHIVE.glob("*.py"))
    assert len(files) == ARCHIVED_REVISION_COUNT, (
        f"expected {ARCHIVED_REVISION_COUNT} archived revisions, found {len(files)}"
    )

    revisions, parents = revision_graph(_ARCHIVE)
    (head,) = revisions - parents
    assert head == ARCHIVED_HEAD_REVISION
    assert ARCHIVED_ROOT_REVISION in revisions
    assert not (parents - revisions), f"archived down_revision names unknown revision(s): {sorted(parents - revisions)}"


def test_the_archive_is_not_reachable_from_alembics_version_path() -> None:
    """`alembic/archive/` is a SIBLING of `versions/`, not a child; Alembic walks children only."""
    assert _ARCHIVE.parent == _VERSIONS.parent
    assert _ARCHIVE not in _VERSIONS.parents
    configuration = (_SERVICE_ROOT / "alembic.ini").read_text(encoding="utf-8")
    assert "version_locations" not in configuration, (
        "alembic.ini gained a version_locations setting; if it now names alembic/archive/, the "
        "collapsed chain is back on the migration path and there are 27 revisions again."
    )


def test_no_revision_on_the_migration_path_requires_timescaledb() -> None:
    """The whole point of the collapse: a fresh build never installs the extension it would drop."""
    for path in sorted(_VERSIONS.glob("*.py")):
        assert "timescaledb" not in executed_sql(path).lower(), (
            f"{path.name} executes SQL naming timescaledb. A revision on the migration path may "
            "neither require nor drop it -- infra/local-warehouse/enable-extensions.sql does not "
            "create it, so requiring it deadlocks a build from revision zero."
        )


def test_the_baseline_preflight_demands_exactly_the_readiness_extensions_and_creates_none() -> None:
    """Same fail-closed shape as the archived foundation, minus the extension that caused the deadlock."""
    baseline = _baseline()

    assert "CREATE EXTENSION" not in baseline
    assert "This migration never creates extensions" in baseline
    assert "Agri baseline preflight failed" in baseline
    assert "pg_extension" in baseline
    assert "ERRCODE = '55000'" in baseline
    for extension in REQUIRED_EXTENSIONS:
        assert f"('{extension}'::text)" in baseline, f"the preflight does not require {extension!r}"
    declared = set(re.findall(r"\('([a-z_]+)'::text\)", baseline))
    assert declared == set(REQUIRED_EXTENSIONS), (
        f"the baseline preflight requires {sorted(declared)} but routes/health/contracts.py "
        f"REQUIRED_EXTENSIONS is {sorted(REQUIRED_EXTENSIONS)}; /ready and the migration must agree"
    )


def test_the_baseline_builds_the_schema_from_the_declarative_tree_not_a_transcription() -> None:
    """One definition of every object. A hand-copied CREATE TABLE here could silently outlive the tree."""
    baseline = _baseline()

    assert "load_object_sql" in baseline
    assert "manifest.sql" in baseline
    assert "op.create_table" not in baseline
    executed = executed_sql(_BASELINE)
    assert "CREATE TABLE" not in executed
    assert "CREATE FUNCTION" not in executed


def test_the_baseline_reapplies_the_public_lockdown_the_tree_cannot_carry() -> None:
    """`--no-owner --no-privileges` means the tree has no ACLs; without this the schema ships open."""
    baseline = _baseline()

    assert "REVOKE CREATE ON SCHEMA agri FROM PUBLIC" in baseline
    assert "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA agri FROM PUBLIC" in baseline
    assert "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA agri FROM PUBLIC" in baseline
    # ALL ROUTINES, not ALL FUNCTIONS: PostgreSQL excludes procedures from the latter, which is why
    # 20260723_0010 had to name agri.materialize_forecast_iteration and
    # agri.reconcile_forecast_iteration_actuals one at a time.
    assert "REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA agri FROM PUBLIC" in baseline


def test_the_baseline_creates_no_roles_because_the_chain_retired_every_one_it_made() -> None:
    """`20260803_0018` and `20260808_0019` retired all four owner roles after reassigning their objects."""
    baseline = executed_sql(_BASELINE)

    assert "CREATE ROLE" not in baseline
    for retired_role in (
        "plantgeo_intervention_guard_owner",
        "plantgeo_forecast_input_recorder_owner",
        "plantgeo_forecast_mv_refresh_owner",
        "plantgeo_release_lineage_guard_owner",
    ):
        assert retired_role not in baseline, (
            f"{retired_role} was retired by 20260803_0018/20260808_0019; recreating it in the baseline "
            "would resurrect a role production does not have"
        )


def test_the_baseline_keeps_the_conditional_operator_role_grant_read_only() -> None:
    """Verbatim from `20260723_0010`: these roles live outside Alembic, so the IF EXISTS is load-bearing."""
    baseline = _baseline()

    assert "GRANT SELECT ON agri.forecast_input_recorded_at TO %I" in baseline
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON " in baseline
    assert "SELECT 1 FROM pg_roles WHERE rolname = role_name" in baseline
    for operator_role in (
        "plantgeo_local_developer",
        "plantgeo_loader",
        "plantgeo_forecast_mv_refresher",
        "plantgeo_forecast_refresh_operator",
        "plantgeo_local_viewer",
    ):
        assert f"'{operator_role}'" in baseline


def test_the_baseline_is_forward_only() -> None:
    assert "raise NotImplementedError" in _baseline()


def test_the_baseline_reads_every_object_the_manifest_references() -> None:
    """The revision's own manifest parser, run against the committed manifest, must find them all."""
    spec = importlib.util.spec_from_file_location("agri_greenfield_baseline", _BASELINE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    manifest = (_SERVICE_ROOT / "db" / "manifest.sql").read_text(encoding="utf-8")
    included = [line.split(maxsplit=1)[1].strip() for line in manifest.splitlines() if line.startswith("\\i ")]
    parsed = module.manifest_object_paths()

    assert parsed == [path.removeprefix("agri/") for path in included], (
        "the baseline's manifest parser and db/manifest.sql disagree on which objects to build, "
        "or on the order to build them in"
    )
    assert len(parsed) == len(set(parsed)), "the manifest includes an object file twice"
    for relative_path in parsed:
        assert (_SERVICE_ROOT / "db" / "agri" / relative_path).is_file(), f"missing declarative object {relative_path}"
