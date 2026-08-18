"""`tests/conftest.py`'s EXPECTED_ALEMBIC_HEAD must equal the real head of `alembic/versions/`.

WHY THIS FILE EXISTS. That constant is the gate every `agri_db`-marked test passes through: the
`conftest.py` fixture refuses an `AGRI_TEST_DATABASE_URL` whose `alembic_version` is not exactly that
string. It is a hand-maintained literal, and it was NOT bumped when `20260816_0024` landed -- so from
that revision until `20260817_0025` every disposable-database test refused any database actually at
head, the whole real-database gate ran dark, and the sweep stayed green while testing nothing.

The window matters: `20260816_0024` is the revision that CREATED `agri.matview_refresh_state`, i.e.
exactly the migration whose absence dead-lettered ten shards in production and which
`worker.py::preflight_required_relations` was written to catch. The gate went dark in the one window it
was most needed.

Deriving the head here rather than restating it makes the class of failure impossible: a new revision
that forgets to bump the constant fails this test instead of silently disabling its own coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

from agri_data_service.routes.health.contracts import EXPECTED_ALEMBIC_REVISION
from tests.conftest import EXPECTED_ALEMBIC_HEAD

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Module-level assignments only. The optional `(?:\s*:[^=]+)?` matters: this tree mixes both styles --
# `revision = "20260817_0025"` and `revision: str = "20260719_0001"` -- and a regex that accepted only
# the bare form silently skipped the annotated files, which is how a head-derivation test could itself
# derive the wrong head. Deliberately a regex over the source rather than an import of every revision
# module: importing them executes their top-level `load_object_sql` calls and pulls in the whole
# alembic runtime, a far heavier dependency than reading two literals.
_REVISION = re.compile(r'^revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DOWN_REVISION = re.compile(r'^down_revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _revision_graph() -> tuple[set[str], set[str]]:
    """Every declared revision id, and every id named as some revision's parent."""
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted(_VERSIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        match = _REVISION.search(source)
        assert match is not None, f"{path.name} declares no module-level `revision`"
        revisions.add(match.group(1))
        down = _DOWN_REVISION.search(source)
        if down is not None:
            parents.add(down.group(1))
    return revisions, parents


def test_the_migration_tree_has_exactly_one_head() -> None:
    """A second head would make `alembic upgrade head` ambiguous and this pin meaningless."""
    revisions, parents = _revision_graph()
    heads = revisions - parents
    assert len(heads) == 1, f"expected exactly one alembic head, found {sorted(heads)}"


def test_expected_alembic_head_matches_the_versions_directory() -> None:
    """The constant the real-database gate keys on must BE the head, not a stale copy of a past one."""
    revisions, parents = _revision_graph()
    (head,) = revisions - parents
    assert head == EXPECTED_ALEMBIC_HEAD, (
        f"tests/conftest.py EXPECTED_ALEMBIC_HEAD is {EXPECTED_ALEMBIC_HEAD!r} but the head of "
        f"alembic/versions/ is {head!r}. Bump the constant in the SAME change as the revision: while "
        "they disagree, every agri_db-marked test refuses a database at head and silently does not run."
    )


def test_readiness_revision_pin_matches_the_versions_directory() -> None:
    """The SECOND hand-maintained copy of the head, and the one an operator sees fail.

    `sql/routes/health_migration.sql` demands EXACT equality, so a stale pin makes /ready report
    migration=false against a database that is perfectly migrated -- indistinguishable, from outside,
    from a deploy that ran before its migration. It went stale across BOTH 20260816_0024 and
    20260817_0025 because the only test comparing it to a real head needs `AGRI_TEST_DATABASE_URL`,
    which the sweep that shipped them did not set. This one needs no database, so it cannot run dark.
    """
    revisions, parents = _revision_graph()
    (head,) = revisions - parents
    assert head == EXPECTED_ALEMBIC_REVISION, (
        f"routes/health/contracts.py EXPECTED_ALEMBIC_REVISION is {EXPECTED_ALEMBIC_REVISION!r} but "
        f"the head of alembic/versions/ is {head!r}. Bump it in the SAME change as the revision: "
        "while they disagree, /ready refuses to report the service ready at all."
    )


def test_every_named_parent_revision_actually_exists() -> None:
    """A `down_revision` pointing at nothing makes the chain unwalkable and the head undefined."""
    revisions, parents = _revision_graph()
    assert not (parents - revisions), f"down_revision names unknown revision(s): {sorted(parents - revisions)}"
