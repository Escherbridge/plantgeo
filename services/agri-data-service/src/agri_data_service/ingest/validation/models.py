"""The stream catalog: which streams exist, where they are stored, and which lanes fill them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.ingest.validation.constants import StreamKind, StreamStore


# ---------------------------------------------------------------------------------------------------------------
# The declared shape of a stream. Cadence is what turns a gap into a finding, so it is stated per stream and
# its basis is stated with it.
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamDefinition:
    """One reportable stream: where it is stored, how often it should publish, and which lanes fill it."""

    stream: str
    kind: StreamKind
    store: StreamStore
    publication_cadence_days: int | None = None
    cadence_basis: str | None = None
    lane_names: tuple[str, ...] = ()
    expected_first_day: date | None = None

    def __post_init__(self) -> None:
        """Refuse a cadence with no measurement behind it, and a lane name no lane registry can mint."""
        if not self.stream.strip():
            raise ValueError("stream must not be blank")
        if self.publication_cadence_days is not None:
            if self.publication_cadence_days < 1:
                raise ValueError(f"{self.stream}: publication_cadence_days must be at least one day")
            if not (self.cadence_basis or "").strip():
                raise ValueError(f"{self.stream}: a declared cadence must carry the basis it was measured from")

    @property
    def is_time_series(self) -> bool:
        """True when the stream owes a new observation on a cadence, so silence at the tail is a gap."""
        return self.publication_cadence_days is not None


# Cadence basis strings name a measurement, never an intention. Scheduled acquisition cadence comes from
# the sole executor registry; an upstream cadence is the publisher's stated release rhythm.
#
# Source-direct writers have no synthetic archive-lane name.
DEFAULT_STREAM_DEFINITIONS: Final[tuple[StreamDefinition, ...]] = (
    StreamDefinition(
        stream="fire-detections",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis=(
            "durable lane jobs-firms-archive walks whole days; the forward postgres-firms lane and its "
            "ingest-firms verb were deleted 2026-09-06, so a day with no detection row is a gap only "
            "below the archive walk's reach"
        ),
    ),
    StreamDefinition(
        stream="water-gauges",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis=(
            "durable lane jobs-streamflow-archive walks whole days; the forward postgres-streamflow lane "
            "and its ingest-streamflow verb were deleted 2026-09-06"
        ),
    ),
    StreamDefinition(
        stream="weather-observations",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis=(
            "FROZEN: postgres-weather and its ingest-weather verb were deleted 2026-09-06 and "
            "weather-observations-direct-forward is still shadow, so this stream has NO live producer and "
            "every day after the freeze is a gap by construction, not by upstream silence"
        ),
    ),
    StreamDefinition(
        stream="vegetation",
        kind="time_series",
        store="features",
        publication_cadence_days=5,
        cadence_basis=(
            "FROZEN: postgres-vegetation and its ingest-ndvi verb were deleted 2026-09-06 and "
            "vegetation-sentinel2-ndvi-direct-forward is still shadow. Sentinel-2 L2A revisits mid-latitudes "
            "about every five days, which is the shortest cadence the upstream could honour if a producer "
            "were running"
        ),
    ),
    StreamDefinition(
        stream="fire-perimeters",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis="job-executor lane fire-perimeters-direct-forward runs hourly",
    ),
    StreamDefinition(
        stream="evacuation-zones",
        kind="time_series",
        store="features",
        publication_cadence_days=1,
        cadence_basis=(
            "job-executor lane evacuation-zones-direct-forward runs hourly at :35; the "
            "postgres-evacuation-zones poller whose cadence it inherited was deleted 2026-09-07"
        ),
    ),
    StreamDefinition(stream="sensors", kind="snapshot", store="features", cadence_basis=None),
    StreamDefinition(stream="soil-survey", kind="reference", store="features"),
    StreamDefinition(stream="watersheds", kind="reference", store="features"),
    StreamDefinition(stream="burn-severity", kind="reference", store="features"),
    StreamDefinition(stream="interventions", kind="reference", store="features"),
    StreamDefinition(
        stream="drought_areas",
        kind="time_series",
        store="drought_areas",
        publication_cadence_days=7,
        cadence_basis="USDM publishes one release every Tuesday, so a weekly rhythm is complete, not sparse",
    ),
    # DEREGISTERED 2026-08-15, AND THE SCAN THEY LEFT BEHIND WAS DELETED 2026-09-04.
    #
    # historical_vegetation / historical_fire_data / historical_water_drought lost their
    # `StreamDefinition`s on 2026-08-15, on the owner directive "if there is no planned track to
    # populate the table then remove the layer": the three `geo.historical_*` tables they named have
    # no producer anywhere in the tree. Deregistering the CATALOG ENTRY did not stop the SCAN at the
    # time, and the half that was left behind did not merely waste time -- it asserted something false
    # on every tick, until the whole validity-report stack that read this catalog was itself deleted
    # (see ingest/AGENTS.md's deletion table). Nothing reads the three tables from this service any
    # more. See docs/pending-migrations/0029-pre-aggregation.md for the ordered apply sequence the
    # original deregistration sits inside.
)
