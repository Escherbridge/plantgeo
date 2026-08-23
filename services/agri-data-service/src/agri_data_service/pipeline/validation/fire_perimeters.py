"""Reconcile the `fire-perimeters` lane against WFIGS `_Current`, the only oracle it has.

Layer: `pipeline` (needs the network; `layer-lanes.md` #1 forbids that in `method`). Per
`docs/lanes/fire-perimeters.md` #6, WFIGS `_Current` is a live mutable snapshot with no day filter,
so this module can only ever answer "did we capture everything active right now" -- never "on day
X in the past." It answers that by checking, for one day this lane already exported, whether every
incident it wrote is still present in a fresh WFIGS fetch; a day with zero incidents is normal for
this event-shaped lane and is never treated as a gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.wfigs import WFIGS_BOUNDS, fetch_fire_perimeters
from agri_data_service.pipeline.lanes.fire_perimeters import read_fire_perimeters_day
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FirePerimetersReconciliation:
    """One point-in-time reconciliation: incidents this lane wrote for `day` vs. a fresh WFIGS fetch.

    `bbox` must be the same bbox this lane's ingest job used
    (`agri_data_service.ingest.policy.resolve_bounded_bbox`); a narrower bbox here would report a
    real incident as "missing" purely for having fallen outside a smaller box, never having closed.
    """

    day: date
    checked_at: datetime
    bbox: str
    written_fire_identifiers: frozenset[str]
    wfigs_current_fire_identifiers: frozenset[str]

    @property
    def missing_from_source(self) -> frozenset[str]:
        """Incidents written for `day` that a fresh WFIGS `_Current` fetch no longer reports.

        Zero is the expected answer for a same-day check: WFIGS itself dated these to `day`, so
        they should still be current minutes or hours later. A non-empty result is a real,
        reportable gap -- an incident that closed within the window checked -- never interpolated
        into agreement.
        """
        return self.written_fire_identifiers - self.wfigs_current_fire_identifiers

    @property
    def is_complete(self) -> bool:
        """True when every incident this lane wrote for `day` is still WFIGS `_Current`.

        Vacuously true for a quiet day with nothing written -- an empty set has no missing member --
        which is exactly the governed-absence case `docs/lanes/fire-perimeters.md` #5 says must
        never be raised as a false alarm.
        """
        return not self.missing_from_source

    def failure_message(self) -> str | None:
        """Name the day, the lane, and the source response; `None` when the counts agree."""
        if self.is_complete:
            return None
        return (
            f"{FIRE_PERIMETERS_STREAM} reconciliation for {self.day.isoformat()} "
            f"(checked {self.checked_at.isoformat()} against WFIGS _Current, bbox {self.bbox!r}): "
            f"{len(self.missing_from_source)} of {len(self.written_fire_identifiers)} incidents written "
            f"for {self.day.isoformat()} no longer appear among the {len(self.wfigs_current_fire_identifiers)} "
            f"perimeters WFIGS currently reports: {sorted(self.missing_from_source)}"
        )


def _current_fire_identifiers(perimeters: list[dict[str, object]]) -> frozenset[str]:
    """Extract each perimeter's WFIGS native key.

    `fetch_fire_perimeters`'s own parsing already requires a non-blank `uniqueFireIdentifier` on
    every record it returns; the `isinstance` check here is what lets that guarantee satisfy `mypy`
    rather than trusting an untyped dict value.
    """
    identifiers: set[str] = set()
    for perimeter in perimeters:
        identifier = perimeter.get("uniqueFireIdentifier")
        if isinstance(identifier, str):
            identifiers.add(identifier)
    return frozenset(identifiers)


async def reconcile_fire_perimeters_day(
    session: AsyncSession,
    *,
    day: date,
    bbox: str,
    client: httpx.AsyncClient | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> FirePerimetersReconciliation:
    """Compare one day's export against a fresh WFIGS `_Current` fetch for the same bbox.

    The written side reuses `pipeline.lanes.fire_perimeters.read_fire_perimeters_day` -- the exact
    `geo.feature_observation_day` scoping the exporter itself writes with -- rather than a second,
    independently-scoped query. `geo.features` holds one row per incident refreshed in place, not
    one row per (incident, day); recomputing the day any other way reports the same snapshot as
    "today" on every run and is a phantom agreement, not a validation
    (`docs/lanes/fire-perimeters.md` #4). The source side reuses
    `agri_data_service.ingest.wfigs.fetch_fire_perimeters`, the exact paged, bounded, retrying fetch
    the ingest job itself runs, so this reconciliation issues the request production already trusts
    rather than a second, possibly-drifted one.
    """
    exported = await read_fire_perimeters_day(session, day=day)
    written_ids: frozenset[str] = frozenset(exported.column("unique_fire_identifier").to_pylist())
    if client is None:
        async with upstream_client(WFIGS_BOUNDS) as owned_client:
            current_perimeters, _more_remaining = await fetch_fire_perimeters(owned_client, bbox)
    else:
        current_perimeters, _more_remaining = await fetch_fire_perimeters(client, bbox)
    return FirePerimetersReconciliation(
        day=day,
        checked_at=now(),
        bbox=bbox,
        written_fire_identifiers=written_ids,
        wfigs_current_fire_identifiers=_current_fire_identifiers(current_perimeters),
    )
