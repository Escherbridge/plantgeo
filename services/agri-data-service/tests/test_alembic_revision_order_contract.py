"""`db/revisions.REVISION_ORDER` must be the real applied history, and the promotion path must use it.

No database. WHY THIS EXISTS. Two governance gates compared Alembic revision ids for **equality**:
`execution/promotion.py::validate_target_preflight` and
`routes/historical_promotion.py::_require_target_revision` -- the second on a field literally named
`minimum_target_revision`. The 2026-08-25 greenfield stamp moves a byte-identical schema from
`20260817_0025` to `20260825_0000`, so under equality every bundle exported before the stamp is
refused forever: the schema is the same, the string is not.

`revision_is_at_least` replaces both comparisons, and it can only be trusted if the order it reads
is the order the revision files actually declare -- string sorting is not that order, because the
baseline `20260825_0000` supersedes `20260825_0026` while sorting below it. So the tuple is stated
in one place and re-derived here from `alembic/archive/` and `alembic/versions/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agri_data_service.db.revisions import (
    BASELINE_REVISION,
    REVISION_ORDER,
    UnknownAlembicRevisionError,
    revision_is_at_least,
    revision_rank,
)
from tests.test_alembic_head_pin_contract import revision_chain

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_VERSIONS = _SERVICE_ROOT / "alembic" / "versions"
_ARCHIVE = _SERVICE_ROOT / "alembic" / "archive"


def test_the_stated_order_is_the_archive_chain_followed_by_the_migration_path() -> None:
    """Every new revision appends exactly one line here, in the same change as the revision."""
    expected = revision_chain(_ARCHIVE) + revision_chain(_VERSIONS)

    assert list(REVISION_ORDER) == expected, (
        "db/revisions.py REVISION_ORDER is stale. It must be alembic/archive/ walked root-to-head, "
        "then alembic/versions/ walked root-to-head. Append the new revision id in the same change "
        "as the revision, alongside tests/conftest.py EXPECTED_ALEMBIC_HEAD and "
        "routes/health/contracts.py EXPECTED_ALEMBIC_REVISION."
    )
    assert len(set(REVISION_ORDER)) == len(REVISION_ORDER), "REVISION_ORDER repeats a revision id"


def test_the_baseline_outranks_every_revision_it_collapsed() -> None:
    """The whole point: a stamped database is not 'behind' the chain it replaced."""
    archived = revision_chain(_ARCHIVE)

    assert revision_chain(_VERSIONS)[0] == BASELINE_REVISION
    for revision in archived:
        assert revision_is_at_least(BASELINE_REVISION, revision), (
            f"{BASELINE_REVISION} must outrank the archived {revision}: it builds the schema that "
            "chain ended at, so a database stamped to it satisfies any floor the chain satisfied"
        )
    assert not revision_is_at_least(archived[-1], BASELINE_REVISION)


def test_a_string_comparison_would_have_got_this_wrong() -> None:
    """Records the exact reason the order is stated rather than computed from the id text."""
    baseline_id, collapsed_id = "20260825_0000", "20260825_0026"

    assert baseline_id < collapsed_id, "the id text sorts the baseline BELOW the revision it collapsed"
    assert revision_rank(baseline_id) > revision_rank(collapsed_id)


def test_an_unknown_revision_fails_closed_rather_than_sorting() -> None:
    """A revision this build has never heard of is not 'old' or 'new'; callers must refuse it."""
    with pytest.raises(UnknownAlembicRevisionError):
        revision_rank("20991231_9999")
    with pytest.raises(UnknownAlembicRevisionError):
        revision_is_at_least(BASELINE_REVISION, "not-a-revision")


def test_neither_promotion_gate_compares_revisions_for_equality_any_more() -> None:
    """The two call sites the stamp would have broken, asserted by source so a regression is loud."""
    promotion = (_SERVICE_ROOT / "src" / "agri_data_service" / "execution" / "promotion.py").read_text(encoding="utf-8")
    receiver = (_SERVICE_ROOT / "src" / "agri_data_service" / "routes" / "historical_promotion.py").read_text(
        encoding="utf-8"
    )

    assert "target.alembic_revision != normalized.source.alembic_revision" not in promotion
    assert "revision_is_at_least(target.alembic_revision" in promotion
    assert "if observed != expected:" not in receiver
    assert "revision_is_at_least(str(observed), minimum)" in receiver
