"""Revision ordering for the current greenfield schema."""

from __future__ import annotations

BASELINE_REVISION = "20260912_0000"
REVISION_ORDER: tuple[str, ...] = (BASELINE_REVISION,)

_RANK_BY_REVISION: dict[str, int] = {revision: rank for rank, revision in enumerate(REVISION_ORDER)}


class UnknownAlembicRevisionError(LookupError):
    """A revision id that is not in ``REVISION_ORDER``, so no ordering claim can be made about it."""


def revision_rank(revision: str) -> int:
    """Position of ``revision`` in the schema's applied history."""
    try:
        return _RANK_BY_REVISION[revision]
    except KeyError as exc:
        raise UnknownAlembicRevisionError(
            f"unknown Alembic revision {revision!r}: this build recognizes only the current baseline"
        ) from exc


def revision_is_at_least(observed: str, minimum: str) -> bool:
    """True when a database at ``observed`` carries at least the schema ``minimum`` produced."""
    return revision_rank(observed) >= revision_rank(minimum)
