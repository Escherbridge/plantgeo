"""The map surfaces the agent may be asked about, and the Parquet lanes each one is served from.

Hand-spelled on both sides, deliberately. See `agent/AGENTS.md`, "The catalogue the agent and the
map share", for why neither half is derived from a query or from the lane registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.zoom import ZoomTier

# --- The catalogue the agent and the map share -------------------------------------
#
# HAND-SPELLED, and deliberately not derived. docs/layer-lane-standard.md section 9 requires the
# slider capability catalogue to be asserted against a hand-spelled list precisely because a
# generated list drifts silently with the thing it is meant to check. The same reasoning applies
# here: if this tuple were built from `LANE_REGISTRATIONS`, a lane that vanished from the registry
# would vanish from the agent's vocabulary too, and the agent would answer "I do not know that
# surface" instead of "that surface stopped being served".
#
# 24 names, matching the map's catalogue exactly as of 2026-08-15: 11 geo.layers rows, the 4
# SLIDER_STREAM_LAYER_NAMES entries (src/types/time-slider.ts), and the 9
# `climate-field-<signal>` names CLIMATE_FIELD_SIGNAL_IDS produces
# (src/lib/environmental/climate-field.ts).

# The 11 feature-backed surfaces -- the half the map draws as individual features.
FEATURE_SURFACE_NAMES: Final = (
    "burn-severity",
    "evacuation-zones",
    "fire-detections",
    "fire-perimeters",
    "interventions",
    "sensors",
    "soil-survey",
    "vegetation",
    "watersheds",
    "water-gauges",
    "weather-observations",
)

# The 13 stream names, which are NOT geo.layers rows: one polygon-backed release set and twelve
# signal-backed cell-grid streams.
STREAM_SURFACE_NAMES: Final = (
    "climate-field-air-temperature",
    "climate-field-dew-point",
    "climate-field-precipitation",
    "climate-field-relative-humidity",
    "climate-field-shortwave-radiation",
    "climate-field-soil-wetness-profile",
    "climate-field-soil-wetness-root-zone",
    "climate-field-soil-wetness-surface",
    "climate-field-wind-speed",
    "drought-areas",
    "soil-field-moisture",
    "soil-field-temperature",
    "soil-field-vpd",
)

AGENT_SURFACE_NAMES: Final = tuple(sorted(FEATURE_SURFACE_NAMES + STREAM_SURFACE_NAMES))

# --- Which Parquet lanes serve each surface ----------------------------------------
#
# MIRRORS `src/lib/server/services/parquet-slider-capabilities.ts`, whose
# `DIRECT_PARQUET_CAPABILITIES` / `CLIMATE_PARQUET_LANES` / `SIGNAL_PARQUET_CAPABILITIES` tables are
# the client-catalogue ownership contract. A second, differently-shaped mapping here is how the
# agent would answer about a lane the map never asks for -- so this table is copied from that one
# rather than re-derived, and `test_the_agent_surface_lanes_match_the_client_catalogue` compares
# them name by name.
#
# A surface backed by SEVERAL lanes is covered only where every one of its lanes is: the map's
# `commonPublishedRanges` intersects them for the same reason. Three examples carry that weight --
# air temperature publishes mean/max/min as three lanes, soil moisture three depths, soil
# temperature four -- and a day one depth is missing is a day the surface cannot be drawn.
#
# `interventions` is deliberately absent. RUNBOOK section 0.26.1 keeps that lane in PostgreSQL: it
# is community data a user writes, not environmental data an upstream publishes, so it has no
# registered Parquet lane and `SURFACE_PARQUET_LANES` must not invent one for it.
SURFACE_PARQUET_LANES: Final[dict[str, tuple[str, ...]]] = {
    "burn-severity": ("burn-severity",),
    "evacuation-zones": ("evacuation-zones",),
    "fire-detections": ("fire-detections",),
    "fire-perimeters": ("fire-perimeters",),
    "sensors": ("sensors",),
    "soil-survey": ("soil-survey",),
    "vegetation": ("vegetation",),
    "watersheds": ("watersheds",),
    "water-gauges": ("water-gauges",),
    "weather-observations": ("weather-observations",),
    "drought-areas": ("drought",),
    "climate-field-air-temperature": (
        "climate-field-air-temperature-mean",
        "climate-field-air-temperature-max",
        "climate-field-air-temperature-min",
    ),
    "climate-field-dew-point": ("climate-field-dew-point",),
    "climate-field-precipitation": ("climate-field-precipitation",),
    "climate-field-relative-humidity": ("climate-field-relative-humidity",),
    "climate-field-shortwave-radiation": ("climate-field-shortwave-radiation",),
    "climate-field-wind-speed": ("climate-field-wind-speed",),
    "climate-field-soil-wetness-surface": ("soil-wetness-surface",),
    "climate-field-soil-wetness-root-zone": ("soil-wetness-root-zone",),
    "climate-field-soil-wetness-profile": ("soil-wetness-profile",),
    "soil-field-moisture": (
        "soil-field-moisture-0-7cm",
        "soil-field-moisture-7-28cm",
        "soil-field-moisture-28-100cm",
    ),
    "soil-field-temperature": (
        "soil-temperature-0-to-7cm",
        "soil-temperature-7-to-28cm",
        "soil-temperature-28-to-100cm",
        "soil-temperature-100-to-255cm",
    ),
    "soil-field-vpd": ("soil-field-vpd",),
}

# The one surface the map publishes that has no Parquet lane and never will; see the note above.
POSTGRESQL_ONLY_SURFACE_NAMES: Final = ("interventions",)

# The lane the four signal tools read. NOT one of the map's surface names: `signal` is the governed
# cell-day plane the climate and soil streams are DERIVED from, so it is addressed by lane slug and
# never by surface name. `warehouse/parquet/schema.py::SIGNAL_PLANE_STREAM` is the definition.
SIGNAL_PLANE_LANE: Final = "signal"

# The lanes `fire_history_near_point` summarises, in the order it reports them. Spelled here rather
# than resolved through `ingest/firms.py` and `ingest/mtbs.py` as the PostgreSQL statement did:
# those resolvers answer with a `geo.layers` row name, and a Parquet lane slug is a different
# namespace that happens to agree today.
FIRE_LANE_NAMES: Final = ("burn-severity", "fire-detections")

# --- The rung the agent reads ------------------------------------------------------
#
# Every agent question is a POINT question inside a radius capped at 50 km, which is a z13 viewport
# by any reading of `foundation/parquet/zoom.py`'s ladder. The base rung is also the only one that
# carries `cell_id` -- the coarse rungs null it because a coarsened cell spans many source cells and
# can honestly name none of them -- and the only one whose rows were not aggregated a second time.
# Reading a coarse rung would answer a farm-scale question with a continent-scale average.
AGENT_ZOOM_TIER: Final[ZoomTier] = 13


def surface_lanes(surface_name: str) -> tuple[str, ...]:
    """Return the Parquet lanes serving one surface, or an empty tuple when none does."""
    return SURFACE_PARQUET_LANES.get(surface_name, ())
