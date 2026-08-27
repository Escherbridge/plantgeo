"""A total order over every Alembic revision the `agri` schema has ever carried.

WHY AN EXPLICIT ORDER RATHER THAN A STRING COMPARISON. Two callers need to answer *"is this
database at least as migrated as that one?"* -- ``execution/promotion.py`` before a semantic
restore and ``routes/historical_promotion.py`` before accepting a bundle chunk. Revision ids sort
usefully by string only while the chain is monotonic, and the 2026-08-25 collapse broke that: the
greenfield baseline ``20260825_0000`` supersedes the whole archived chain including
``20260825_0026``, yet sorts *below* it. So the order is stated, and
``tests/test_alembic_revision_order_contract.py`` re-derives it from the two revision directories
and fails if this tuple drifts.

FAIL CLOSED. A revision id that is not listed here is not "old" or "new", it is unknown, and every
helper raises ``UnknownAlembicRevisionError`` rather than guessing. Callers turn that into a
refusal, never into an approval.

EVERY NEW REVISION APPENDS ONE LINE. Same change as the revision, same change as
``tests/conftest.py`` ``EXPECTED_ALEMBIC_HEAD`` and ``routes/health/contracts.py``
``EXPECTED_ALEMBIC_REVISION``.
"""

from __future__ import annotations

BASELINE_REVISION = "20260825_0000"

# alembic/archive/ -- the 26 applied revisions in chain order, then alembic/versions/. The baseline
# is placed AFTER the whole archived chain because it builds the schema that chain ended at: a
# database reading `20260825_0000` has everything a database reading `20260817_0025` had.
REVISION_ORDER: tuple[str, ...] = (
    "20260719_0001",
    "20260720_0002",
    "20260720_0003",
    "20260720_0004",
    "20260722_0005",
    "20260722_0006",
    "20260722_0007",
    "20260722_0008",
    "20260723_0009",
    "20260723_0010",
    "20260725_0011",
    "20260725_0012",
    "20260725_0013",
    "20260801_0014",
    "20260802_0015",
    "20260802_0016",
    "20260803_0017",
    "20260803_0018",
    "20260808_0019",
    "20260814_0020",
    "20260814_0021",
    "20260814_0022",
    "20260814_0023",
    "20260816_0024",
    "20260817_0025",
    "20260825_0026",
    BASELINE_REVISION,
    "20260827_0027",
)

_RANK_BY_REVISION: dict[str, int] = {revision: rank for rank, revision in enumerate(REVISION_ORDER)}


class UnknownAlembicRevisionError(LookupError):
    """A revision id that is not in ``REVISION_ORDER``, so no ordering claim can be made about it."""


def revision_rank(revision: str) -> int:
    """Position of ``revision`` in the schema's applied history."""
    try:
        return _RANK_BY_REVISION[revision]
    except KeyError as exc:
        raise UnknownAlembicRevisionError(
            f"unknown Alembic revision {revision!r}: it is in neither alembic/versions/ nor "
            "alembic/archive/, so this build cannot order it against anything"
        ) from exc


def revision_is_at_least(observed: str, minimum: str) -> bool:
    """True when a database at ``observed`` carries at least the schema ``minimum`` produced."""
    return revision_rank(observed) >= revision_rank(minimum)
