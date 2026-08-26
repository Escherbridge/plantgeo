"""Static contracts for the greenfield baseline `20260825_0000` and the archive it replaced.

No database. These are the assertions that make the collapse safe to leave alone: that the baseline
is the single root of the migration path, that the archive stays inert and complete, and that the
deadlock the collapse existed to break cannot reappear by someone copying a file back.

WHAT THIS FILE MUST NOT ASSERT. It must not require `alembic/versions/` to hold exactly one file or
exactly one revision id. It did until 2026-08-25, and that is a ban on ever writing another
migration -- an ordinary follow-on revision failed two assertions here while the head count was
still one. Everything below is phrased as *the baseline is present and is the root*, never *the
baseline is alone*.

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
from typing import TYPE_CHECKING

from agri_data_service.db.extensions import REQUIRED_EXTENSIONS
from agri_data_service.execution.promotion import REQUIRED_EXTENSION_NAMES
from agri_data_service.routes.health import contracts as readiness_contracts
from tests.test_alembic_head_pin_contract import revision_chain, revision_graph, revision_parents

if TYPE_CHECKING:
    from types import ModuleType

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


def baseline_module() -> ModuleType:
    """Import the revision file by path so its rendered SQL constants can be asserted on directly."""
    spec = importlib.util.spec_from_file_location("agri_greenfield_baseline", _BASELINE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def executed_sql(path: Path) -> str:
    """``executed_literals`` joined with newlines, for substring checks over a whole revision."""
    return "\n".join(executed_literals(path))


def executed_literals(path: Path) -> list[str]:
    """Every string literal in a revision EXCEPT its module docstring.

    The absence checks below have to read what a revision *executes*, not what it *explains*. A
    revision whose whole purpose is to remove ``timescaledb`` necessarily says the word in its
    docstring; asserting over the raw source made this file fail on its own prose, which is a test
    that punishes documentation. Dropping the module docstring and keeping every other literal is
    the narrowest cut that separates the two -- a `%s` of SQL hidden in a comment cannot execute,
    and a SQL constant cannot hide from `ast`.

    F-strings are unparsed WHOLE rather than shredded into their constant fragments. Walking an
    ``ast.JoinedStr`` yields only the pieces between the placeholders, so
    ``f"CREATE TABLE {name} (...)"`` used to reduce to ``"CREATE TABLE "`` plus ``" (...)"`` and
    every phrase-level check silently stopped matching. The baseline's own extension preflight
    became an f-string on 2026-08-25, which is how the hole was found.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_docstring = ast.get_docstring(tree, clean=False)
    formatted = [node for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)]
    inside_formatted = {id(child) for node in formatted for child in ast.walk(node) if child is not node}
    literals = [ast.unparse(node) for node in formatted]
    literals += [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value != module_docstring
        and id(node) not in inside_formatted
    ]
    return literals


def test_the_baseline_is_present_and_is_the_only_root_of_the_migration_path() -> None:
    """PRESENT and ROOT -- deliberately not ALONE.

    The first draft of this file asserted `versions/` held exactly one file and exactly one
    revision id. That is not the property the collapse needs; it is a ban on ever writing another
    migration, and the same track schedules two. Ambiguity of `alembic upgrade head` is a
    *head-count* property and is already guarded by
    `test_alembic_head_pin_contract.py::test_the_migration_tree_has_exactly_one_head`. What must
    hold here is that the baseline is on the path, is the root of it, and that nothing grows a
    second root -- a second root is the shape that would put an archived revision back in charge.
    """
    assert _BASELINE.is_file(), f"the greenfield baseline {_BASELINE.name} is missing from alembic/versions/"

    parents = revision_parents(_VERSIONS)
    assert BASELINE_REVISION in parents, f"alembic/versions/ declares {sorted(parents)}, without {BASELINE_REVISION}"
    roots = sorted(revision for revision, parent in parents.items() if parent is None)
    assert roots == [BASELINE_REVISION], (
        f"the migration path must have exactly one root and it must be the greenfield baseline; "
        f"found roots {roots}. A second root means `alembic upgrade head` no longer starts here."
    )
    assert "down_revision: str | None = None" in _baseline()
    # Every follow-on revision must chain, so the path stays walkable from the baseline forward.
    assert revision_chain(_VERSIONS)[0] == BASELINE_REVISION


def test_no_archived_revision_id_or_file_reappears_on_the_migration_path() -> None:
    """Copying a file back from `alembic/archive/` is the one way this collapse silently unravels.

    An archived id on the path would either re-introduce the `timescaledb` deadlock (`20260719_0001`)
    or re-run DDL a baseline-built database already has. Both are caught here by identity, and by
    file name too, since a rename would keep the id.
    """
    archived_ids, _ = revision_graph(_ARCHIVE)
    live_ids, _ = revision_graph(_VERSIONS)
    reappeared = sorted(archived_ids & live_ids)
    assert not reappeared, (
        f"revision id(s) {reappeared} are declared in BOTH alembic/archive/ and alembic/versions/. "
        "Archived revisions are applied history; a behaviour change goes in a NEW revision "
        "authored against db/agri/** (see alembic/archive/AGENTS.md)."
    )

    archived_files = {path.name for path in _ARCHIVE.glob("*.py")}
    live_files = {path.name for path in _VERSIONS.glob("*.py")}
    copied_back = sorted(archived_files & live_files)
    assert not copied_back, f"archived revision file(s) copied back: {copied_back}"


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
    """Same fail-closed shape as the archived foundation, minus the extension that caused the deadlock.

    Asserted on the RENDERED SQL, not the source text: the preflight is now generated from
    ``agri_data_service.db.extensions.REQUIRED_EXTENSIONS`` -- the one definition /ready and
    ``db/tools/verify_stamp_target.py`` also read -- so there is no literal list to read here.
    """
    baseline = _baseline()
    preflight = baseline_module()._REQUIRED_EXTENSION_PREFLIGHT_SQL

    assert "CREATE EXTENSION" not in baseline
    assert "This migration never creates extensions" in preflight
    assert "Agri baseline preflight failed" in preflight
    assert "pg_extension" in preflight
    assert "ERRCODE = '55000'" in preflight
    for extension in REQUIRED_EXTENSIONS:
        assert f"('{extension}'::text)" in preflight, f"the preflight does not require {extension!r}"
    declared = set(re.findall(r"\('([a-z_]+)'::text\)", preflight))
    assert declared == set(REQUIRED_EXTENSIONS), (
        f"the baseline preflight requires {sorted(declared)} but db/extensions.py REQUIRED_EXTENSIONS "
        f"is {sorted(REQUIRED_EXTENSIONS)}; /ready and the migration must agree"
    )


def test_the_extension_list_has_exactly_one_definition() -> None:
    """Four hand-kept copies existed before 2026-08-25; every reader now derives from `db/extensions`."""
    assert readiness_contracts.REQUIRED_EXTENSIONS is REQUIRED_EXTENSIONS
    assert baseline_module().REQUIRED_EXTENSIONS is REQUIRED_EXTENSIONS
    assert frozenset(REQUIRED_EXTENSIONS) == REQUIRED_EXTENSION_NAMES
    contracts_source = (_SERVICE_ROOT / "src" / "agri_data_service" / "routes" / "health" / "contracts.py").read_text(
        encoding="utf-8"
    )
    assert "REQUIRED_EXTENSIONS = (" not in contracts_source, (
        "routes/health/contracts.py re-declares REQUIRED_EXTENSIONS; it must re-export "
        "agri_data_service.db.extensions so the migration, /ready and the verifier cannot diverge"
    )


def test_the_capture_settings_are_the_manifests_own_preamble() -> None:
    """The four pg_dump settings the tree was captured under, stated once by split_schema.py.

    The baseline re-spells them because a Python migration cannot ``\\i`` a psql script, so the two
    copies are tied here instead: the assertion parses the GENERATED ``db/manifest.sql`` preamble,
    which is what a regeneration actually writes.
    """
    manifest = (_SERVICE_ROOT / "db" / "manifest.sql").read_text(encoding="utf-8")
    preamble = manifest.split("-- ====", 1)[0]
    manifest_settings = [
        line.rstrip(";").strip()
        for line in preamble.splitlines()
        if line.startswith(("SET ", "SELECT pg_catalog.set_config"))
    ]

    assert list(baseline_module()._CAPTURE_SETTINGS_SQL) == manifest_settings, (
        "the baseline's _CAPTURE_SETTINGS_SQL and db/manifest.sql's generated preamble disagree. "
        "db/tools/split_schema.py::_MANIFEST_PREAMBLE writes the manifest; the baseline replays the "
        "tree, so it must load it under the same settings pg_dump captured it with -- "
        "check_function_bodies above all, since manifest order creates routines before the "
        "relations their bodies read."
    )


def test_the_baseline_hands_back_execute_on_every_check_invoked_routine() -> None:
    """A CHECK runs with the WRITER's privileges, so the blanket REVOKE locks non-owner writers out.

    Catalogue-driven on purpose: `pg_constraint` -> `pg_depend` -> `pg_proc` finds whatever is
    CHECK-invoked at apply time, so a CHECK added by a later revision is covered without anyone
    editing a hand-list. `tests/test_alembic_baseline_parity.py` proves it on a live database, both
    that the grant works and that removing it breaks the INSERT.
    """
    baseline = _baseline()
    grants = baseline_module()._CHECK_CONSTRAINT_EXECUTE_GRANTS_SQL

    assert "GRANT EXECUTE ON FUNCTION %s TO %I" in grants
    assert "contype = 'c'" in grants
    assert "'pg_constraint'::regclass" in grants
    assert "'pg_proc'::regclass" in grants
    assert "SELECT 1 FROM pg_roles WHERE rolname = role_name" in grants, (
        "the operator roles are provisioned outside Alembic, so the IF EXISTS guard is load-bearing"
    )
    # Order matters: handing EXECUTE back before the blanket revoke would revoke it again.
    assert baseline.index("_REVOKE_FROM_PUBLIC_SQL:\n        op.execute(statement)") < baseline.index(
        "op.execute(_CHECK_CONSTRAINT_EXECUTE_GRANTS_SQL)"
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
    module = baseline_module()

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
