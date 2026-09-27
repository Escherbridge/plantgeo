"""The server-built site brief `site-brief/1` (CONTRACT C5): a pure builder, canonical JSON and site facts.

`build_site_brief` is a pure function of `SiteBriefInputs` and has a TypeScript twin
(`src/lib/server/services/site-brief.ts::buildSiteBrief`); both are checked against
`tests/fixtures/site_brief_golden.json` after `canonical_json`. Readers live in `agent/graph.py`.
See agent/AGENTS.md, "Site brief".
"""

from __future__ import annotations

import json
import math
import os
from datetime import date
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

BRIEF_VERSION: Final = "site-brief/1"

#: The two kill switches (CONTRACT C2; review M7). Both unset = the wave-2 agent exactly.
SOIL_READS_ENABLED_VARIABLE: Final = "SOIL_PROPERTIES_READS_ENABLED"
SITE_BRIEF_ENABLED_VARIABLE: Final = "SITE_BRIEF_ENABLED"
_FLAG_ON: Final = "true"


def flag_enabled(variable: str) -> bool:
    """CONTRACT C2 flag rule, shared with the web: only the exact value `true`, whitespace trimmed, is on."""
    return os.environ.get(variable, "").strip() == _FLAG_ON


def site_brief_enabled() -> bool:
    """`SITE_BRIEF_ENABLED`: off (the default) means the brief node reads and says nothing."""
    return flag_enabled(SITE_BRIEF_ENABLED_VARIABLE)


def soil_context_enabled() -> bool:
    """Either flag on: the prompt's soil and site-fact text carries its basis labels."""
    return flag_enabled(SOIL_READS_ENABLED_VARIABLE) or site_brief_enabled()


#: MTBS thematic classes 2-4 and their names (CONTRACT C5.1); classes 1, 5 and 6 carry no severity.
BURN_SEVERITY_CLASSES: Final[Mapping[str, str]] = {
    "2": "low",
    "3": "moderate",
    "4": "high",
    "low": "low",
    "moderate": "moderate",
    "high": "high",
}


def burn_severity_of(value: object) -> str | None:
    """The severity of an MTBS class code or name, or None for a class that carries none."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    return BURN_SEVERITY_CLASSES.get(text.strip().lower())


SOILGRIDS_SOURCE: Final = "SoilGrids v2.0"
SOILGRIDS_RESOLUTION_M: Final = 250
SOIL_CELL_DEGREES: Final = 0.005
TOPSOIL_DEPTH_LABEL: Final = "0-30 cm (thickness-weighted)"
TOPSOIL_LABEL: Final = f"{SOILGRIDS_SOURCE} {SOILGRIDS_RESOLUTION_M} m model estimate, {TOPSOIL_DEPTH_LABEL}"
LAND_COVER_SOURCE: Final = "USDA CDL"
MAX_LITERATURE_SEED_CHARACTERS: Final = 600
COORDINATE_SCALE: Final = 100_000

#: C5.6: the one reason vocabulary every unavailable section draws from.
UNAVAILABLE_REASONS: Final = frozenset(
    {
        "reads_disabled",
        "lane_never_written",
        "not_published",
        "outside_release_coverage",
        "not_bound_in_region",
        "no_cell_within_radius",
        "no_observation_within_radius",
        "serving_at_capacity",
        "timeout",
        "read_failed",
    }
)

#: ISRIC depth spelling (C5.1 keys) -> label spelling.
SOIL_DEPTHS: Final[tuple[tuple[str, str], ...]] = (
    ("0-5cm", "0-5 cm"),
    ("5-15cm", "5-15 cm"),
    ("15-30cm", "15-30 cm"),
)
#: Thickness of each depth in cm, for the thickness-weighted 0-30 cm mean.
DEPTH_THICKNESS_CM: Final[Mapping[str, int]] = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15}
TOPSOIL_THICKNESS_CM: Final = 30

#: CONTRACT C1 unit table: ISRIC code -> (output key, divisor). Mirrors `products.py::PROPERTY_UNITS`.
SOIL_UNITS: Final[tuple[tuple[str, str, int], ...]] = (
    ("phh2o", "ph", 10),
    ("soc", "soc_g_kg", 10),
    ("nitrogen", "nitrogen_g_kg", 100),
    ("bdod", "bdod_g_cm3", 100),
    ("cec", "cec_cmolc_kg", 10),
    ("ocd", "ocd_kg_m3", 10),
    ("clay", "clay_pct", 10),
    ("sand", "sand_pct", 10),
    ("silt", "silt_pct", 10),
    ("cfvo", "cfvo_vol_pct", 10),
)
SOIL_PROPERTY_CODES: Final = tuple(code for code, _key, _divisor in SOIL_UNITS)

#: C5.2 topsoil outputs: ISRIC code -> (output key, divisor applied to N before rounding, emit divisor).
_TOPSOIL_OUTPUTS: Final[tuple[tuple[str, str, int, int], ...]] = (
    ("phh2o", "ph", TOPSOIL_THICKNESS_CM, 10),
    ("soc", "soc_pct", 10 * TOPSOIL_THICKNESS_CM, 10),
    ("clay", "clay_pct", TOPSOIL_THICKNESS_CM, 10),
    ("sand", "sand_pct", TOPSOIL_THICKNESS_CM, 10),
    ("silt", "silt_pct", TOPSOIL_THICKNESS_CM, 10),
    ("cfvo", "cfvo_vol_pct", TOPSOIL_THICKNESS_CM, 10),
    ("bdod", "bdod_g_cm3", TOPSOIL_THICKNESS_CM, 100),
)

#: USDA Soil Survey Manual reaction classes: (inclusive upper bound in pH tenths, class).
_REACTION_CLASSES: Final[tuple[tuple[int, str], ...]] = (
    (34, "ultra acid"),
    (44, "extremely acid"),
    (50, "very strongly acid"),
    (55, "strongly acid"),
    (60, "moderately acid"),
    (65, "slightly acid"),
    (73, "neutral"),
    (78, "slightly alkaline"),
    (84, "moderately alkaline"),
    (90, "strongly alkaline"),
)
_ALKALINE_CEILING_CLASS: Final = "very strongly alkaline"

#: SOC band on soc_pct tenths: (exclusive upper bound, band).
_SOC_BANDS: Final[tuple[tuple[int, str], ...]] = ((10, "low"), (20, "moderate"), (40, "high"))
_SOC_CEILING_BAND: Final = "very high"

USDM_CLASS_NAMES: Final[Mapping[str, str]] = {
    "none": "no drought",
    "D0": "abnormally dry",
    "D1": "moderate drought",
    "D2": "severe drought",
    "D3": "extreme drought",
    "D4": "exceptional drought",
}

UNCLASSIFIED_TEXTURE: Final = "unclassified"
_TEXTURE_TOTAL_TENTHS: Final = 1000
_FLOAT_INTEGER_LIMIT: Final = 2**53

Basis = Literal["measured", "model_estimate", "classified", "classified_remote_sensing", "survey_estimate"]


# --- Integer arithmetic (C5.2) --------------------------------------------------------


def round_half_up(numerator: int, denominator: int) -> int:
    """Round half up by integer division: `(2N + D) // (2D)` for N >= 0, D > 0."""
    if numerator < 0 or denominator <= 0:
        raise ValueError(f"round_half_up needs N >= 0 and D > 0, got N={numerator} D={denominator}")
    return (2 * numerator + denominator) // (2 * denominator)


def _tenths_text(tenths: int) -> str:
    """Render a non-negative tenths integer with one fixed decimal, never through a float."""
    return f"{tenths // 10}.{tenths % 10}"


def reaction_class(ph_tenths: int) -> str:
    """USDA soil reaction class of a pH given in tenths."""
    for ceiling, name in _REACTION_CLASSES:
        if ph_tenths <= ceiling:
            return name
    return _ALKALINE_CEILING_CLASS


def soc_band(soc_pct_tenths: int) -> str:
    """Organic-carbon band of a SOC percentage given in tenths."""
    for ceiling, name in _SOC_BANDS:
        if soc_pct_tenths < ceiling:
            return name
    return _SOC_CEILING_BAND


def normalised_texture(clay_tenths: int, sand_tenths: int, silt_tenths: int) -> tuple[int, int, int] | None:
    """Scale clay/sand/silt tenths to a 1000-tenths total as (sand, silt, clay); None for a zero total."""
    total = clay_tenths + sand_tenths + silt_tenths
    if total <= 0:
        return None
    clay = round_half_up(_TEXTURE_TOTAL_TENTHS * clay_tenths, total)
    sand = round_half_up(_TEXTURE_TOTAL_TENTHS * sand_tenths, total)
    return sand, _TEXTURE_TOTAL_TENTHS - clay - sand, clay


def texture_class(sand: int, silt: int, clay: int) -> str:  # noqa: PLR0911 - one return per class, first match wins.
    """USDA soil texture triangle on normalised tenths of a percent (CONTRACT C5.2 order)."""
    if 2 * silt + 3 * clay < 300:  # noqa: PLR2004 - the triangle's own boundaries, in tenths.
        return "sand"
    if 2 * silt + 3 * clay >= 300 and silt + 2 * clay < 300:  # noqa: PLR2004
        return "loamy sand"
    if (70 <= clay < 200 and sand > 520 and silt + 2 * clay >= 300) or (  # noqa: PLR2004
        clay < 70 and silt < 500 and silt + 2 * clay >= 300  # noqa: PLR2004
    ):
        return "sandy loam"
    if 70 <= clay < 270 and 280 <= silt < 500 and sand <= 520:  # noqa: PLR2004
        return "loam"
    if (silt >= 500 and 120 <= clay < 270) or (500 <= silt < 800 and clay < 120):  # noqa: PLR2004
        return "silt loam"
    if silt >= 800 and clay < 120:  # noqa: PLR2004
        return "silt"
    if 200 <= clay < 350 and silt < 280 and sand > 450:  # noqa: PLR2004
        return "sandy clay loam"
    if 270 <= clay < 400 and 200 < sand <= 450:  # noqa: PLR2004
        return "clay loam"
    if 270 <= clay < 400 and sand <= 200:  # noqa: PLR2004
        return "silty clay loam"
    if clay >= 350 and sand > 450:  # noqa: PLR2004
        return "sandy clay"
    if clay >= 400 and silt >= 400:  # noqa: PLR2004
        return "silty clay"
    if clay >= 400 and sand <= 450 and silt < 400:  # noqa: PLR2004
        return "clay"
    return UNCLASSIFIED_TEXTURE


# --- Canonical JSON (C5.5) ------------------------------------------------------------


def _canonical_number(value: float) -> str:
    """Integral finite numbers as integer literals, everything else as the shortest round-trip repr."""
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError("canonical JSON has no representation for a non-finite number")
    if value.is_integer() and abs(value) < _FLOAT_INTEGER_LIMIT:
        return str(int(value))
    return repr(value)


def canonical_json(value: Any) -> str:
    """Render `value` as C5.5 canonical JSON: sorted keys, no spaces, integral floats as integers."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return "{" + ",".join(f"{json.dumps(str(key))}:{canonical_json(item)}" for key, item in items) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    raise TypeError(f"canonical JSON cannot render {type(value).__name__}")


# --- Sections ---------------------------------------------------------------------------


def _unavailable(section: Mapping[str, Any], *, soil: bool = False) -> dict[str, Any]:
    """Pass an unavailable section through with its reason (and a soil radius), refusing unknown reasons."""
    reason = section.get("reason")
    if reason not in UNAVAILABLE_REASONS:
        raise ValueError(f"unknown unavailable reason {reason!r}")
    result: dict[str, Any] = {"state": "unavailable", "reason": reason}
    if soil and reason == "no_cell_within_radius":
        result["radius_m"] = int(section["radius_m"])
    return result


def _is_available(section: Mapping[str, Any] | None) -> bool:
    return section is not None and section.get("state") == "available"


def _soil_depths(mapped: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, float]]:
    """Per-depth physical values: exact `mapped / divisor` (C1)."""
    return {
        depth: {key: int(mapped[depth][code]) / divisor for code, key, divisor in SOIL_UNITS}
        for depth, _label in SOIL_DEPTHS
    }


def _topsoil_integers(mapped: Mapping[str, Mapping[str, int]]) -> dict[str, int]:
    """Thickness-weighted 0-30 cm mean of each topsoil property, as the integer before the emit division."""
    result: dict[str, int] = {}
    for code, key, denominator, _emit in _TOPSOIL_OUTPUTS:
        numerator = sum(DEPTH_THICKNESS_CM[depth] * int(mapped[depth][code]) for depth, _label in SOIL_DEPTHS)
        result[key] = round_half_up(numerator, denominator)
    return result


def build_soil_section(soil: Mapping[str, Any]) -> dict[str, Any]:
    """The C5.3 soil section alone, for `soil_properties_at_point` (C6)."""
    return _soil_section(soil)[0]


def _soil_section(soil: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The C5.3 soil section and its two descriptors (reaction+texture, SOC band)."""
    mapped: Mapping[str, Mapping[str, int]] = soil["mapped"]
    integers = _topsoil_integers(mapped)
    texture = normalised_texture(integers["clay_pct"], integers["sand_pct"], integers["silt_pct"])
    texture_name = UNCLASSIFIED_TEXTURE if texture is None else texture_class(*texture)
    reaction = reaction_class(integers["ph"])
    band = f"{soc_band(integers['soc_pct'])} organic carbon"
    topsoil: dict[str, Any] = {key: integers[key] / emit for _code, key, _denominator, emit in _TOPSOIL_OUTPUTS}
    topsoil.update(texture_class=texture_name, reaction_class=reaction, soc_band=band, label=TOPSOIL_LABEL)
    distance = int(soil["distance_m"])
    release_id = str(soil["release_id"])
    section = {
        "state": "available",
        "basis": "model_estimate",
        "source": SOILGRIDS_SOURCE,
        "release_id": release_id,
        "resolution_m": SOILGRIDS_RESOLUTION_M,
        "cell_deg": SOIL_CELL_DEGREES,
        "distance_m": distance,
        "label": (
            f"{SOILGRIDS_SOURCE} {SOILGRIDS_RESOLUTION_M} m model estimate, sampled at the centre of a ~500 m cell "
            f"{distance} m from this point (release {release_id})"
        ),
        "depths": _soil_depths(mapped),
        "topsoil_0_30cm": topsoil,
    }
    textured = reaction if texture_name == UNCLASSIFIED_TEXTURE else f"{reaction} {texture_name}"
    descriptors = [
        {
            "text": f"{textured} topsoil (pH {_tenths_text(integers['ph'])}, SoilGrids model estimate 0-30 cm)",
            "seed": f"{textured} topsoil",
        },
        {
            "text": f"{band} topsoil ({_tenths_text(integers['soc_pct'])}% SOC, SoilGrids model estimate 0-30 cm)",
            "seed": band,
        },
    ]
    return section, descriptors


def _fire_section(fire: Mapping[str, Any], built_on: date) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The measured fire section: MTBS/perimeter burn history first, else recent satellite detections."""
    latest = fire.get("latest_fire_day")
    severity = fire.get("burn_severity")
    detections = int(fire.get("detections_last_30_days") or 0)
    days_since = (built_on - date.fromisoformat(latest)).days if latest else None
    label = (
        f"Mapped fire perimeter and MTBS burn severity, burned {latest}"
        if latest
        else (
            f"No mapped fire perimeter at this point; {detections} satellite fire detections nearby in the last 30 days"
        )
    )
    section = {
        "state": "available",
        "basis": "measured",
        "days_since_fire": days_since,
        "latest_fire_day": latest,
        "burn_severity": severity,
        "detections_last_30_days": detections,
        "source": str(fire["source"]),
        "label": label,
    }
    severity_suffix = f", {severity} severity" if severity else ""
    if latest:
        descriptor = {
            "text": f"burned {latest}{severity_suffix} (mapped perimeter and MTBS, measured)",
            "seed": f"burned {latest[:4]}{severity_suffix}",
        }
        return section, [descriptor]
    if detections > 0:
        descriptor = {
            "text": f"{detections} satellite fire detections nearby in the last 30 days (measured)",
            "seed": "recent fire activity",
        }
        return section, [descriptor]
    return section, []


def _drought_section(drought: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The US Drought Monitor class of the polygon containing the point, or `none`."""
    usdm_class = str(drought["usdm_class"])
    if usdm_class not in USDM_CLASS_NAMES:
        raise ValueError(f"unknown USDM class {usdm_class!r}")
    week_of = str(drought["week_of"])
    name = USDM_CLASS_NAMES[usdm_class]
    section = {
        "state": "available",
        "basis": "classified",
        "usdm_class": usdm_class,
        "week_of": week_of,
        "label": f"US Drought Monitor class, week of {week_of}",
    }
    text = (
        f"no drought (US Drought Monitor, week of {week_of})"
        if usdm_class == "none"
        else f"{name} {usdm_class} (US Drought Monitor, week of {week_of})"
    )
    return section, [{"text": text, "seed": name}]


def _weather_section(weather: Mapping[str, Any]) -> dict[str, Any]:
    """The nearest weather-station reading; context only, so it contributes no descriptor."""
    distance = int(weather["distance_m"])
    observed_at = str(weather["observed_at"])
    kilometres = _tenths_text(round_half_up(distance, 100))
    return {
        "state": "available",
        "basis": "measured",
        "observed_at": observed_at,
        "distance_m": distance,
        "temperature_c": int(weather["temperature_tenths_c"]) / 10,
        "relative_humidity_pct": int(weather["relative_humidity_pct"]),
        "label": f"Nearest weather-station observation, {observed_at}, {kilometres} km away",
    }


def _land_cover_section(land_cover: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The dominant CDL class of the crop-cover cell containing the point."""
    class_name = str(land_cover["class_name"])
    fraction_pct = round_half_up(int(land_cover["fraction_permille"]), 10)
    edition_year = int(land_cover["edition_year"])
    release_day = str(land_cover["release_day"])
    cell_km = int(land_cover["cell_m"]) / 1000
    cell_text = _canonical_number(cell_km)
    section = {
        "state": "available",
        "basis": "classified_remote_sensing",
        "source": LAND_COVER_SOURCE,
        "class_name": class_name,
        "class_code": int(land_cover["class_code"]),
        "fraction_pct": fraction_pct,
        "edition_year": edition_year,
        "release_day": release_day,
        "cell_km": cell_km,
        "label": (
            f"{LAND_COVER_SOURCE} {edition_year} (released {release_day}), dominant class of a {cell_text} km cell "
            f"({fraction_pct}%)"
        ),
    }
    lowered = class_name.lower()
    descriptor = {
        "text": (
            f"{lowered}, {fraction_pct}% of a {cell_text} km cell "
            f"({LAND_COVER_SOURCE} {edition_year} classified imagery)"
        ),
        "seed": lowered,
    }
    return section, [descriptor]


def literature_seed(descriptors: list[dict[str, str]]) -> str:
    """C5.4: descriptor seeds joined with '; ', cut at the last space inside 600 characters."""
    joined = "; ".join(descriptor["seed"] for descriptor in descriptors if descriptor.get("seed"))
    if len(joined) <= MAX_LITERATURE_SEED_CHARACTERS:
        return joined
    cut = joined[:MAX_LITERATURE_SEED_CHARACTERS]
    index = cut.rfind(" ")
    return cut[:index] if index > 0 else cut


def build_site_brief(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Build `site-brief/1` from normalised `SiteBriefInputs`; pure, deterministic, never invents a value."""
    built_on = date.fromisoformat(str(inputs["built_on"]))
    point = inputs["point"]
    brief: dict[str, Any] = {
        "brief_version": BRIEF_VERSION,
        "built_on": built_on.isoformat(),
        "point": {
            "longitude": int(point["longitude_e5"]) / COORDINATE_SCALE,
            "latitude": int(point["latitude_e5"]) / COORDINATE_SCALE,
        },
    }
    descriptors: list[dict[str, str]] = []
    soil = inputs.get("soil") or {}
    if _is_available(soil):
        brief["soil"], soil_descriptors = _soil_section(soil)
        descriptors.extend(soil_descriptors)
    else:
        brief["soil"] = _unavailable(soil, soil=True)
    fire = inputs.get("fire") or {}
    if _is_available(fire):
        brief["fire"], fire_descriptors = _fire_section(fire, built_on)
        descriptors.extend(fire_descriptors)
    else:
        brief["fire"] = _unavailable(fire)
    drought = inputs.get("drought") or {}
    if _is_available(drought):
        brief["drought"], drought_descriptors = _drought_section(drought)
        descriptors.extend(drought_descriptors)
    else:
        brief["drought"] = _unavailable(drought)
    weather = inputs.get("weather") or {}
    brief["weather"] = _weather_section(weather) if _is_available(weather) else _unavailable(weather)
    land_cover = inputs.get("land_cover") or {}
    if _is_available(land_cover):
        brief["land_cover"], land_descriptors = _land_cover_section(land_cover)
        descriptors.extend(land_descriptors)
    else:
        brief["land_cover"] = _unavailable(land_cover)
    brief["descriptors"] = descriptors
    brief["literature_seed"] = literature_seed(descriptors)
    return brief


# --- Site facts for the literature context (CONTRACT C3) --------------------------------


def _soil_provenance(soil: Mapping[str, Any]) -> dict[str, Any]:
    distance = int(soil["distance_m"])
    return {
        "basis": "model_estimate",
        "source": SOILGRIDS_SOURCE,
        "release_id": soil["release_id"],
        "depth": TOPSOIL_DEPTH_LABEL,
        "resolution_m": SOILGRIDS_RESOLUTION_M,
        "distance_m": distance,
        "label": f"{TOPSOIL_LABEL}, cell centre {distance} m away",
    }


def site_facts_from_brief(brief: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str | None]:
    """The C3 mapping: (site_facts, site_facts_provenance, site_brief_query) from one built brief."""
    facts: dict[str, Any] = {}
    provenance: dict[str, dict[str, Any]] = {}
    soil = brief.get("soil") or {}
    if _is_available(soil):
        topsoil = soil["topsoil_0_30cm"]
        soil_label = _soil_provenance(soil)
        for fact_key, topsoil_key in (
            ("soil_ph", "ph"),
            ("soil_organic_carbon_pct", "soc_pct"),
            ("sand_pct", "sand_pct"),
            ("clay_pct", "clay_pct"),
        ):
            facts[fact_key] = topsoil[topsoil_key]
            provenance[fact_key] = dict(soil_label)
    fire = brief.get("fire") or {}
    if _is_available(fire) and fire.get("latest_fire_day"):
        fire_label = {"basis": "measured", "source": str(fire["source"])[:80], "label": str(fire["label"])[:200]}
        facts["days_since_fire"] = fire["days_since_fire"]
        provenance["days_since_fire"] = dict(fire_label)
        if fire.get("burn_severity"):
            facts["burn_severity"] = fire["burn_severity"]
            provenance["burn_severity"] = dict(fire_label)
    land_cover = brief.get("land_cover") or {}
    if _is_available(land_cover):
        facts["land_cover"] = land_cover["class_name"]
        provenance["land_cover"] = {
            "basis": "classified_remote_sensing",
            "source": LAND_COVER_SOURCE,
            "label": str(land_cover["label"])[:200],
        }
    seed = str(brief.get("literature_seed") or "")
    return facts, provenance, seed or None


__all__ = [
    "BRIEF_VERSION",
    "BURN_SEVERITY_CLASSES",
    "SITE_BRIEF_ENABLED_VARIABLE",
    "SOIL_DEPTHS",
    "SOIL_PROPERTY_CODES",
    "SOIL_READS_ENABLED_VARIABLE",
    "SOIL_UNITS",
    "TOPSOIL_LABEL",
    "UNAVAILABLE_REASONS",
    "USDM_CLASS_NAMES",
    "Basis",
    "build_site_brief",
    "build_soil_section",
    "burn_severity_of",
    "canonical_json",
    "flag_enabled",
    "literature_seed",
    "normalised_texture",
    "reaction_class",
    "round_half_up",
    "site_brief_enabled",
    "site_facts_from_brief",
    "soc_band",
    "soil_context_enabled",
    "texture_class",
]
