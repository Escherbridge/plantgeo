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
# Names include every selectable data surface and its dedicated serving reader.

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
# climate and soil cell-grid streams.
STREAM_SURFACE_NAMES: Final = (
    "crop-cover",
    "land-context-boundaries",
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

APP_SURFACE_NAMES: Final = (
    "demand-heatmap",
    "interventions",
    "strategy-recommendations",
    "soil-phh2o",
    "soil-soc",
    "soil-nitrogen",
    "soil-bdod",
    "soil-cec",
    "soil-ocd",
    "land-context",
    "fire-risk",
    "weather-forecast",
)
BOTANICAL_SURFACE_NAMES: Final = (
    "botanical-occurrences",
    "botanical-richness",
    "botanical-collection-effort",
    "gbif-occurrences",
)
AGENT_SURFACE_NAMES: Final = tuple(
    sorted(
        set(
            FEATURE_SURFACE_NAMES
            + STREAM_SURFACE_NAMES
            + APP_SURFACE_NAMES
            + BOTANICAL_SURFACE_NAMES
            + ("fire-risk", "weather-forecast", "land-context")
        )
    )
)

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
# App-owned surfaces use their declared app readers; this table lists only Parquet products.
SURFACE_PARQUET_LANES: Final[dict[str, tuple[str, ...]]] = {
    "crop-cover": ("crop-cover",),
    "land-context-boundaries": ("land-context-boundaries",),
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
    "fire-risk": ("fire-risk",),
    "weather-forecast": ("weather-forecast",),
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

# The lanes `fire_history_near_point` summarises, in the order it reports them. Spelled here rather
# than resolved through `ingest/firms.py` and `ingest/mtbs.py` as the PostgreSQL statement did:
# those resolvers answer with a `geo.layers` row name, and a Parquet lane slug is a different
# namespace that happens to agree today.
FIRE_LANE_NAMES: Final = ("burn-severity", "fire-detections")

# Internal point readers use z13; selection retrieval follows the caller's map zoom.
AGENT_ZOOM_TIER: Final[ZoomTier] = 13


# --- Which region-manifest LAYER each surface is bound through --------------------
#
# A third hand-spelled table, for the same reason the two above are hand-spelled: derived from the
# lane names it would be wrong wherever the two namespaces disagree, and they disagree in exactly
# the places that matter. `drought-areas` binds through `drought`; climate and soil products
# bind their own surface names to their declared upstream sources. App-owned data has no
# environmental Parquet binding; see `foundation/region/AGENTS.md`.
#
# `federation.md` §2: a surface whose layer this region binds no source for answers
# `not_available_in_region` rather than an empty success. See `agent/AGENTS.md`.
SURFACE_REGION_LAYER_SLUGS: Final[dict[str, str]] = {
    "crop-cover": "crop-cover",
    "land-context-boundaries": "land-context",
    "burn-severity": "burn-severity",
    "evacuation-zones": "evacuation-zones",
    "fire-detections": "fire-detections",
    "fire-perimeters": "fire-perimeters",
    "sensors": "sensors",
    "soil-survey": "soil-survey",
    "vegetation": "vegetation",
    "watersheds": "watersheds",
    "water-gauges": "water-gauges",
    "weather-observations": "weather-observations",
    "fire-risk": "fire-risk",
    "weather-forecast": "weather-forecast",
    "land-context": "land-context",
    "botanical-occurrences": "botanical-occurrences",
    "botanical-richness": "botanical-occurrences",
    "botanical-collection-effort": "botanical-occurrences",
    "gbif-occurrences": "botanical-occurrences",
    "drought-areas": "drought",
    "climate-field-air-temperature": "climate-field-air-temperature",
    "climate-field-dew-point": "climate-field-dew-point",
    "climate-field-precipitation": "climate-field-precipitation",
    "climate-field-relative-humidity": "climate-field-relative-humidity",
    "climate-field-shortwave-radiation": "climate-field-shortwave-radiation",
    "climate-field-wind-speed": "climate-field-wind-speed",
    "climate-field-soil-wetness-surface": "climate-field-soil-wetness-surface",
    "climate-field-soil-wetness-root-zone": "climate-field-soil-wetness-root-zone",
    "climate-field-soil-wetness-profile": "climate-field-soil-wetness-profile",
    "soil-field-moisture": "soil-field-moisture",
    "soil-field-temperature": "soil-field-temperature",
    "soil-field-vpd": "soil-field-vpd",
}

#: The manifest layer slugs `fire_history_near_point` summarises, in `FIRE_LANE_NAMES` order.
FIRE_REGION_LAYERS: Final = ("burn-severity", "fire-detections")


def surface_lanes(surface_name: str) -> tuple[str, ...]:
    """Return the Parquet lanes serving one surface, or an empty tuple when none does."""
    return SURFACE_PARQUET_LANES.get(surface_name, ())


def surface_region_layer(surface_name: str) -> str | None:
    """Return the region-manifest layer one surface binds through, or `None` for an unknown surface."""
    return SURFACE_REGION_LAYER_SLUGS.get(surface_name)
