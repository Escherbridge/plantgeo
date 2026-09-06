"""USDM release-week enumeration: which Tuesdays the dated archive publishes, clamped to the record's start.

THE POSTGRES HISTORY WALK THAT LIVED HERE WAS DELETED 2026-09-06 (owner directive, "remove the code for
ingestion into the DB"): `run_usdm_history_backfill`, `ingest_release_week`, `PostgresStoredReleaseIndex`,
`HistoryBackfillPlan` and the `ingest-drought-history` verb all wrote `geo.drought_areas`.
`pipeline/direct/drought/backfill.py` owns that window now and writes Parquet. What survives is the pure
calendar -- `usdm_release_weeks` -- which `pipeline/direct/drought/products.py` and
`pipeline/validation/drought.py` both read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

import structlog

from agri_data_service.ingest.usdm import DAYS_PER_WEEK, TUESDAY, usdm_source_url

logger = structlog.get_logger()

# USDM's first published release. Earlier Tuesdays are not a gap in our history, they are before the record.
USDM_ARCHIVE_START: Final = date(2000, 1, 4)


class UsdmHistoryContractError(ValueError):
    """Raised when a history walk is asked for a week or a bound that cannot honestly be walked."""


@dataclass(frozen=True, slots=True)
class ReleaseWeek:
    """One USDM release week: the Tuesday it is valid for and the dated archive file it is read from."""

    release_date: date

    def __post_init__(self) -> None:
        """Refuse any weekday but Tuesday, the only day a USDM release is ever valid for."""
        # Held as a `date` rather than text so the stored `valid_date` spelling is canonical by construction
        # and `usdm._require_tuesday`'s ISO round-trip guard has nothing left to catch.
        if self.release_date.weekday() != TUESDAY:
            raise UsdmHistoryContractError("a USDM release week must be a Tuesday")

    @property
    def valid_date(self) -> str:
        """The canonical `YYYY-MM-DD` spelling that `geo.drought_areas.valid_date` stores."""
        return self.release_date.isoformat()

    @property
    def source_url(self) -> str:
        """The exact archive file this week is read from, which is stored as the row's provenance."""
        return usdm_source_url(self.valid_date)


def usdm_release_weeks(start: date, end: date) -> tuple[ReleaseWeek, ...]:
    """Every USDM release Tuesday from `start` through `end` inclusive, oldest first, clamped to the archive."""
    first = max(start, USDM_ARCHIVE_START)
    if start < USDM_ARCHIVE_START:
        logger.info(
            "usdm_history_window_clamped",
            requested_start=start.isoformat(),
            effective_start=first.isoformat(),
        )
    if first > end:
        return ()
    cursor = first + timedelta(days=(TUESDAY - first.weekday() + DAYS_PER_WEEK) % DAYS_PER_WEEK)
    weeks: list[ReleaseWeek] = []
    while cursor <= end:
        weeks.append(ReleaseWeek(release_date=cursor))
        cursor += timedelta(days=DAYS_PER_WEEK)
    return tuple(weeks)
