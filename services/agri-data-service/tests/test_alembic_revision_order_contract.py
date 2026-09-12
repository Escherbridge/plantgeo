"""The reset migration history recognizes only the current baseline."""

import pytest

from agri_data_service.db.revisions import (
    BASELINE_REVISION,
    REVISION_ORDER,
    UnknownAlembicRevisionError,
    revision_is_at_least,
    revision_rank,
)


def test_revision_order_contains_only_the_baseline() -> None:
    assert BASELINE_REVISION == "20260912_0000"
    assert REVISION_ORDER == (BASELINE_REVISION,)
    assert revision_rank(BASELINE_REVISION) == 0
    assert revision_is_at_least(BASELINE_REVISION, BASELINE_REVISION)


def test_historical_revision_ids_are_unknown() -> None:
    with pytest.raises(UnknownAlembicRevisionError):
        revision_rank("20260912_0028")
