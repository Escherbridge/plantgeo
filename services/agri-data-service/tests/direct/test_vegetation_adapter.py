"""The ownership-handoff boundary: `backfill.py` owns on-or-before it, `forward.py` owns after it."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agri_data_service.pipeline.direct.vegetation.adapter import (
    DirectVegetationError,
    no_mirrored_past_proof,
    refuse_pre_ownership_day,
)
from agri_data_service.pipeline.direct.vegetation.backfill import backfill_ceiling
from agri_data_service.pipeline.direct.vegetation.forward import history_floor
from agri_data_service.pipeline.direct.vegetation.products import VEGETATION_DIRECT_WRITER_START_DAY


def test_the_boundary_day_itself_is_refused() -> None:
    with pytest.raises(DirectVegetationError, match="ownership handoff boundary"):
        refuse_pre_ownership_day(VEGETATION_DIRECT_WRITER_START_DAY)


def test_a_day_before_the_boundary_is_refused() -> None:
    with pytest.raises(DirectVegetationError, match="ownership handoff boundary"):
        refuse_pre_ownership_day(VEGETATION_DIRECT_WRITER_START_DAY - timedelta(days=1))


def test_the_day_after_the_boundary_is_accepted() -> None:
    refuse_pre_ownership_day(VEGETATION_DIRECT_WRITER_START_DAY + timedelta(days=1))  # must not raise


def test_the_fail_closed_default_proves_nothing() -> None:
    """`no_mirrored_past_proof` is the default `mirrored_past_proof`; a caller must supply real evidence."""
    assert no_mirrored_past_proof() is None


def test_the_two_windows_abut_exactly_so_no_day_is_owned_by_neither_driver() -> None:
    """DO NOT DELETE. THE invariant the boundary constant means, pinned rather than described.

    `refuse_pre_ownership_day` refuses `day <= START` and `history_floor()` starts at `START + 1`,
    while `backfill_ceiling()` used to stop at `START - 1`: `START` itself was then owned by NEITHER
    driver AT ANY VALUE of the constant, and invisibly -- forward's census began above it, backfill's
    window ended below it, and `parity.py` bounded its own window by Postgres's MIN/MAX, so with
    `postgres-vegetation` stopped the day fell outside every report. One exclusive boundary, asserted.
    """
    assert backfill_ceiling() + timedelta(days=1) == history_floor()


def test_the_boundary_day_itself_belongs_to_backfill() -> None:
    """The other half of the same invariant: the day forward refuses is the day backfill ends ON."""
    assert backfill_ceiling() == VEGETATION_DIRECT_WRITER_START_DAY
    with pytest.raises(DirectVegetationError, match="ownership handoff boundary"):
        refuse_pre_ownership_day(backfill_ceiling())
