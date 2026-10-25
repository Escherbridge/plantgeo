"""The drought layer's source contract: coverage, availability walk, and one bounded dated pull.

See `AGENTS.md` in this directory, section "The source protocol", for what the layer owns, what a
source owns, and which part of the record is not normalized yet.

`DroughtReleasePayload`/`DroughtAreaPayload` enumerate EXACTLY the members the lane reads off a
release -- nothing wider, so `mypy` sees the same shape the code does, and nothing narrower, so a
second region's source can satisfy them without inheriting from anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime

    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim


@runtime_checkable
class DroughtAreaPayload(Protocol):
    """One drought class of one release: the severity step and the polygon it covers.

    `geometry` is a GeoJSON mapping in WGS84 (`federation.md` §2), the datum every geometry lane in
    this tree stores and serves in. A source publishing in a projected CRS reprojects inside its own
    implementation; the lane's repair chain (`support.py::repair_drought_areas_to_wkb`) assumes 4326
    and has no CRS argument to tell it otherwise.
    """

    #: Read-only properties, not plain attributes -- see `DroughtReleaseDay` for why.
    @property
    def drought_intensity_class(self) -> int:
        """The severity step this area covers on THE LAYER's scale: `0`..`4`, driest step highest.

        The scale is the layer's, not a source's: `0` is "abnormally dry", `4` is "exceptional
        drought", and every source maps onto it. It is USDM-SHAPED -- the pilot's source publishes
        exactly these five steps, and the scale was chosen because the first binding already used
        it -- which is a debt this contract states rather than hides (`drought/AGENTS.md`, "Still
        not normalized"). A source with a different number of classes (the EU Combined Drought
        Indicator's three, a national monitor's six) maps onto these five IN ITS OWN
        IMPLEMENTATION, next to the rest of its unit and calendar normalisation
        (`federation.md` §2), and never by teaching the lane about its scale: a branch on the
        source's class count anywhere in `rows.py`, `support.py`, `adapter.py`, the Parquet schema
        or the serving plane is the bug that rule exists to prevent.

        The member no longer carries a source system's NAME (it was `drought_monitor_category`
        until STYLE-REVIEW-W5 S1): "Drought Monitor" is the US Drought Monitor, and a layer
        contract naming one region's institution is the fork's first ancestor.
        """
        ...

    @property
    def geometry(self) -> Mapping[str, object]:
        """The area's WGS84 GeoJSON `Polygon`/`MultiPolygon`, unrepaired as the source published it."""
        ...


@runtime_checkable
class DroughtReleasePayload(Protocol):
    """One dated drought release's payload, in exactly the shape the lane reads it.

    These three members are the WHOLE of what `rows.py`, `adapter.py` and `forward.py` consume; a
    second region's source satisfies this protocol structurally and the lane never learns its name
    (`federation.md` §2, "layer logic is source-agnostic"). Deliberately NOT the union of everything
    USDM happens to publish: a member nothing reads is a member the next implementer has to fake.
    """

    @property
    def release_day(self) -> date:
        """The UTC calendar day this release is valid for."""
        ...

    @property
    def source_url(self) -> str:
        """The exact upstream file this release was read from, stored as the row's provenance."""
        ...

    @property
    def areas(self) -> Sequence[DroughtAreaPayload]:
        """Every drought class this release publishes, one per severity step."""
        ...


@runtime_checkable
class DroughtReleaseDay(Protocol):
    """One dated drought release as the layer consumes it: the day, the payload, and when it was read.

    `release` is `None` for a source's own documented "not published yet" answer -- a real answer,
    not a fetch failure, which is what lets the adapter turn it into a governed absence rather than
    a refusal. Timestamps are UTC (`federation.md` §2, "units, datums and calendars normalize at the
    source boundary"); the region's declared local timezone lives in the manifest, not here.
    """

    #: Read-only properties, not plain attributes: a frozen dataclass implementation's fields are
    #: themselves read-only, and mypy's Protocol structural check requires a plain attribute to be
    #: settable, so a settable-attribute Protocol member can never be satisfied by a frozen
    #: dataclass field even when the types match exactly.
    @property
    def day(self) -> date:
        """The dated release this record answers for."""
        ...

    @property
    def fetched_at(self) -> datetime:
        """When this record was read, UTC."""
        ...

    @property
    def release(self) -> DroughtReleasePayload | None:
        """The release's payload, or `None` for the source's own "not published yet" answer.

        Read-only so a concrete source may narrow this to its own release payload type -- USDM's
        `ingest.usdm.DroughtRelease` satisfies `DroughtReleasePayload` structurally, and
        `tests/foundation/test_source_protocols.py` proves it on a fixture rather than asserting it
        in a comment.
        """
        ...


@runtime_checkable
class DroughtSource(Protocol):
    """One region's drought source: what it covers, which days it publishes, and how one day is read.

    Implemented by `usdm.py` for the pilot. A second region implements this against its own national
    drought monitor and binds it in the region manifest; nothing in `rows.py`, `adapter.py`,
    `forward.py`, `warehouse/schemas/drought.py` or `planes/drought.py` learns the source's name.
    """

    #: The manifest's `source_slug` for this implementation, e.g. `"usdm"`.
    source_slug: str
    #: Where this source can fill the drought layer at all (`federation.md` §2).
    coverage: SourceCoverageClaim

    def release_days(self, first_day: date, last_day: date) -> tuple[date, ...]:
        """Every day this source publishes a release for in `[first_day, last_day]`, oldest first."""
        ...

    async def fetch_release_day(
        self,
        day: date,
        *,
        retry_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> DroughtReleaseDay:
        """Fetch exactly one dated release, retrying transport failures as one bounded unit."""
        ...


__all__ = [
    "DroughtAreaPayload",
    "DroughtReleaseDay",
    "DroughtReleasePayload",
    "DroughtSource",
]
