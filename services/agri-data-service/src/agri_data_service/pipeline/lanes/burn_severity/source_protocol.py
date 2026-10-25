"""The burn-severity layer's source contract: coverage, governed release days, and one bounded pull.

See `AGENTS.md` in this directory, section "The source protocol", for what the layer owns, what a
source owns, and why this layer's pull signature carries an ignition-year cohort the others do not.

`BurnSeverityRecordPayload`/`BurnSeverityThresholdsPayload` enumerate EXACTLY the members the lane
reads off one burned-area record -- nothing wider, so `mypy` sees the same shape `rows.py` does,
and nothing narrower, so a second region's source can satisfy them without inheriting from anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agri_data_service.foundation.geography.bounding_box import BoundingBox

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime

    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim


@runtime_checkable
class BurnSeverityThresholdsPayload(Protocol):
    """The per-fire dNBR thresholds a source publishes in place of a polygon-level severity class.

    Every member is `int | None` because a burn-severity programme may publish any subset of them;
    `None` is "this programme does not publish this threshold", never zero. dNBR is a dimensionless
    index, so there is no SI conversion owed here (`federation.md` §2) -- the numbers are the
    index's own scaled integers and cross a source boundary unchanged.
    """

    #: Read-only properties, not plain attributes -- see `BurnSeverityReleaseDay` for why.
    @property
    def dnbr_offset(self) -> int | None:
        """The dNBR offset subtracted before the thresholds below are applied."""
        ...

    @property
    def dnbr_standard_deviation(self) -> int | None:
        """The dNBR standard deviation the thresholds were derived against."""
        ...

    @property
    def nodata_threshold(self) -> int | None:
        """Below this dNBR the pixel is no-data rather than a severity class."""
        ...

    @property
    def greenness_threshold(self) -> int | None:
        """Below this dNBR the pixel reads as increased greenness."""
        ...

    @property
    def low_threshold(self) -> int | None:
        """The dNBR floor of the low-severity class."""
        ...

    @property
    def moderate_threshold(self) -> int | None:
        """The dNBR floor of the moderate-severity class."""
        ...

    @property
    def high_threshold(self) -> int | None:
        """The dNBR floor of the high-severity class."""
        ...


@runtime_checkable
class BurnSeverityRecordPayload(Protocol):
    """One burned-area boundary as the layer consumes it: identity, provenance, dates and geometry.

    These members are the WHOLE of what `rows.py` writes and `adapter.py`/`forward.py` count; a
    member nothing reads is a member the next region's implementer would have to fake
    (`federation.md` §2, "layer logic is source-agnostic"). `acres` is the one member whose unit is
    NOT SI -- see `AGENTS.md`, "What is NOT normalized yet", for why the schema pins it there.
    """

    #: Read-only properties, not plain attributes -- see `BurnSeverityReleaseDay` for why.
    @property
    def producer_local_id(self) -> str:
        """The source's own identifier for this fire, unique within one release day."""
        ...

    @property
    def natural_key(self) -> str:
        """`<producer>:<producer_local_id>`, minted by the identity builder and carried, not rebuilt."""
        ...

    @property
    def release_identifier(self) -> str:
        """Which release published this boundary; two releases of one fire differ here."""
        ...

    @property
    def mapping_revision(self) -> str:
        """The source's own revision of the mapping, distinguishing a re-map from a re-release."""
        ...

    @property
    def ignition_year(self) -> int:
        """The cohort year this fire ignited in, which is what a release is published per."""
        ...

    @property
    def ignition_date(self) -> date:
        """The day the fire ignited, as the source's own fire-calendar date."""
        ...

    @property
    def data_available_at(self) -> datetime:
        """When the source made this boundary available, UTC (`federation.md` §2)."""
        ...

    @property
    def fire_name(self) -> str | None:
        """The fire's published name, or `None` when the source names no fire."""
        ...

    @property
    def fire_type(self) -> str | None:
        """The source's fire-type word (wildfire, prescribed, ...), or `None` when unpublished."""
        ...

    @property
    def assessment_type(self) -> str | None:
        """The source's assessment word (initial, extended, ...), or `None` when unpublished."""
        ...

    @property
    def acres(self) -> float | None:
        """Burned area in ACRES, not SI -- the unit `warehouse/schemas/burn_severity.py` pins."""
        ...

    @property
    def severity_class(self) -> str | None:
        """The polygon-level severity word, or `None` when the source publishes thresholds instead."""
        ...

    @property
    def severity_thresholds(self) -> BurnSeverityThresholdsPayload:
        """The dNBR thresholds this record carries; always present, its members individually optional."""
        ...

    @property
    def geometry(self) -> Mapping[str, object]:
        """The burned area's WGS84 GeoJSON polygon, unrepaired as the source published it."""
        ...


@runtime_checkable
class BurnSeverityReleaseDay(Protocol):
    """One governed release day as the layer consumes it: the day, its cohorts, rows, and read clock.

    `records` may be an empty tuple and that is an honest zero, not a fetch failure: a real fire
    year whose whole cohort falls outside the deployment's envelope publishes nothing here. Unlike
    the drought layer there is no "not published yet" state -- every governed release day is already
    a past release. Timestamps are UTC (`federation.md` §2).
    """

    #: Read-only properties, not plain attributes: a frozen dataclass implementation's fields are
    #: themselves read-only, and mypy's Protocol structural check requires a plain attribute to be
    #: settable, so a settable-attribute Protocol member can never be satisfied by a frozen
    #: dataclass field even when the types match exactly.
    @property
    def day(self) -> date:
        """The governed release day this record answers for."""
        ...

    @property
    def ignition_years(self) -> tuple[int, ...]:
        """Every ignition-year cohort this release day's records were drawn from."""
        ...

    @property
    def fetched_at(self) -> datetime:
        """When this record was read, UTC."""
        ...

    @property
    def records(self) -> Sequence[BurnSeverityRecordPayload]:
        """Every burned-area boundary this release day publishes; empty is an honest zero.

        Read-only so a concrete source may narrow this to its own release-record type -- MTBS's
        `ingest.mtbs.MtbsBurnSeverityRecord` satisfies `BurnSeverityRecordPayload` structurally, and
        `tests/foundation/test_source_protocols.py` proves it on a fixture rather than asserting it
        in a comment.
        """
        ...


@runtime_checkable
class BurnSeveritySource(Protocol):
    """One region's burn-severity source: coverage, the governed release calendar, and a dated pull.

    Implemented by `mtbs.py` for the pilot. The pull takes the ignition-year cohorts the release day
    maps to and the envelope to pull inside, because a burn-severity release is published per fire
    year rather than per calendar day; the envelope is the region manifest's, passed in
    (`layer-lanes.md` §1b), never a module constant.
    """

    #: The manifest's `source_slug` for this implementation, e.g. `"mtbs"`.
    source_slug: str
    #: Where this source can fill the burn-severity layer at all (`federation.md` §2).
    coverage: SourceCoverageClaim

    def release_days(self) -> tuple[date, ...]:
        """Every release day this source has governed evidence for, oldest first.

        No window argument, unlike the drought layer's calendar walk: this set grows only through a
        governance action dating a fire year's completion, never through the passage of time, so the
        whole candidate set is always small enough to return entire.
        """
        ...

    def ignition_years_by_release_day(self) -> dict[date, tuple[int, ...]]:
        """Each governed release day mapped to the ignition-year cohort(s) it publishes."""
        ...

    async def fetch_release_day(  # noqa: PLR0913 -- the layer's own pull shape (`AGENTS.md`, "The source protocol")
        self,
        day: date,
        ignition_years: Sequence[int],
        *,
        bounding_box: BoundingBox,
        retry_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> BurnSeverityReleaseDay:
        """Page every cohort mapped to `day` to completion inside `bounding_box`, as one retry unit."""
        ...


__all__ = [
    "BoundingBox",
    "BurnSeverityRecordPayload",
    "BurnSeverityReleaseDay",
    "BurnSeveritySource",
    "BurnSeverityThresholdsPayload",
]
