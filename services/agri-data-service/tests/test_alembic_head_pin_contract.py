"""Runtime and test revision pins must equal the single live Alembic head."""

from __future__ import annotations

import re
from pathlib import Path

from agri_data_service.routes.health.contracts import EXPECTED_ALEMBIC_REVISION
from tests.conftest import EXPECTED_ALEMBIC_HEAD

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Read module-level assignments without importing Alembic or opening the baseline SQL file.
_REVISION = re.compile(r'^revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DOWN_REVISION = re.compile(r'^down_revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def revision_parents(directory: Path = _VERSIONS) -> dict[str, str | None]:
    """Every declared revision id in ``directory`` mapped to its ``down_revision`` (``None`` = a root)."""
    parents: dict[str, str | None] = {}
    for path in sorted(directory.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        match = _REVISION.search(source)
        assert match is not None, f"{path.name} declares no module-level `revision`"
        down = _DOWN_REVISION.search(source)
        parents[match.group(1)] = down.group(1) if down is not None else None
    return parents


def revision_chain(directory: Path = _VERSIONS) -> list[str]:
    """The revisions of ``directory`` walked root-to-head. Requires a single linear chain."""
    parents = revision_parents(directory)
    roots = [revision for revision, parent in parents.items() if parent is None]
    assert len(roots) == 1, f"expected exactly one root in {directory.name}, found {sorted(roots)}"
    children = {parent: revision for revision, parent in parents.items() if parent is not None}
    chain = [roots[0]]
    while chain[-1] in children:
        chain.append(children[chain[-1]])
    assert len(chain) == len(parents), (
        f"{directory.name} is not one linear chain: walked {len(chain)} of {len(parents)} revisions"
    )
    return chain


def revision_graph(directory: Path = _VERSIONS) -> tuple[set[str], set[str]]:
    """Every declared revision id in ``directory``, and every id named as some revision's parent."""
    parents = revision_parents(directory)
    return set(parents), {parent for parent in parents.values() if parent is not None}


def test_the_migration_tree_has_exactly_one_head() -> None:
    """A second head would make `alembic upgrade head` ambiguous and this pin meaningless."""
    revisions, parents = revision_graph()
    heads = revisions - parents
    assert len(heads) == 1, f"expected exactly one alembic head, found {sorted(heads)}"


def test_expected_alembic_head_matches_the_versions_directory() -> None:
    """The constant the real-database gate keys on must BE the head, not a stale copy of a past one."""
    revisions, parents = revision_graph()
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
    revisions, parents = revision_graph()
    (head,) = revisions - parents
    assert head == EXPECTED_ALEMBIC_REVISION, (
        f"routes/health/contracts.py EXPECTED_ALEMBIC_REVISION is {EXPECTED_ALEMBIC_REVISION!r} but "
        f"the head of alembic/versions/ is {head!r}. Bump it in the SAME change as the revision: "
        "while they disagree, /ready refuses to report the service ready at all."
    )


def test_every_named_parent_revision_actually_exists() -> None:
    """A `down_revision` pointing at nothing makes the chain unwalkable and the head undefined."""
    revisions, parents = revision_graph()
    assert not (parents - revisions), f"down_revision names unknown revision(s): {sorted(parents - revisions)}"
