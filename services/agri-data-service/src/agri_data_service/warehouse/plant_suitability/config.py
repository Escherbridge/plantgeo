"""Rule presets: every v0-vs-production difference is one named field; see AGENTS.md §Presets here."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from agri_data_service.warehouse.plant_suitability.labels import fire_wording
from agri_data_service.warehouse.plant_suitability.licences import (
    COMMERCIAL_USE_LICENCES,
    KNOWN_LICENCES,
    iso_calendar_date,
)
from agri_data_service.warehouse.plant_suitability.schemas import GUILDS, SITE_INPUT_GROUPS, USPS_STATE_CODES

INTRODUCED_FLAG_CPS_394 = "introduced — native alternative preferred (CPS 394)"
INTRODUCED_FLAG_PLAIN = "introduced — native alternative preferred"
V0_PICK_DEFINITION = (
    "envelope-passing candidate: fails none of the PLANTS range axes, the wetland check, the source-row check (no "
    "regional row naming it has its own precipitation band and habitat qualifier known to exclude the cell) or state "
    "noxious lists; not a field-tested recommendation"
)
PRODUCTION_PICK_DEFINITION = (
    "Picks are envelope-passing candidates: each fails none of the PLANTS range axes, the wetland check, the "
    "source-row check or state noxious lists. They are not field-tested recommendations, and a taxon's absence from "
    "a cell is not evidence that it is unsuitable there."
)
# Owner reading: 'eastern Oregon' / Intermountain guides lie east of the Cascade crest; its region and source ids are
# curation-region data (regions.CurationRegionData), resolved per prepare().
EAST_OF_CASCADE_CREST_READING = "east_of_cascade_crest"
# The prototype's pull of every dated-attribution source (PRISM): production must declare a later pull of its own.
PROTOTYPE_ACCESS_DATE = "2026-09-26"
# Owner rule "exclude non-commercial sources from v1", read as an allow-list of licence ids (AGENTS.md §Licences);
# it includes PRISM's attribution-only terms (owner decision 2026-10-03), so PRODUCTION reads PRISM precipitation.
PRODUCTION_PERMITTED_LICENCES = COMMERCIAL_USE_LICENCES


@dataclass(frozen=True)
class RuleConfig:
    """One named rule set; AGENTS.md §Presets gives each field's owner decision."""

    name: str
    in_region_readings: frozenset[str]
    range_wide_origin_is_not_state_claim: bool
    inherit_stratification: bool
    null_restriction_depth_is_unknown: bool
    introduced_flag_text: Mapping[str, str]
    pick_definition: str
    permitted_licences: frozenset[str] | None
    required_site_input_groups: frozenset[str]
    label_unchecked_axes: bool
    declared_empty_exclusion_states: frozenset[str] = frozenset()
    dated_attribution_accessed_after: str | None = None
    # Whether groups declared `fixture=True` (the frozen prototype's pull) may serve: set by the rule set, not the data.
    allow_fixture_site_inputs: bool = False

    def __post_init__(self) -> None:
        """Freeze copies of the mapping and set fields, so a caller's object mutated after prepare() changes nothing."""
        object.__setattr__(self, "introduced_flag_text", MappingProxyType(dict(self.introduced_flag_text)))
        object.__setattr__(self, "in_region_readings", frozenset(self.in_region_readings))
        if self.permitted_licences is not None:
            object.__setattr__(self, "permitted_licences", frozenset(self.permitted_licences))
        object.__setattr__(self, "required_site_input_groups", frozenset(self.required_site_input_groups))
        object.__setattr__(self, "declared_empty_exclusion_states", frozenset(self.declared_empty_exclusion_states))


V0_FROZEN = RuleConfig(
    name="v0_frozen",
    in_region_readings=frozenset(),
    range_wide_origin_is_not_state_claim=False,
    inherit_stratification=False,
    null_restriction_depth_is_unknown=False,
    introduced_flag_text=MappingProxyType(dict.fromkeys(GUILDS, INTRODUCED_FLAG_CPS_394)),
    pick_definition=V0_PICK_DEFINITION,
    permitted_licences=None,
    required_site_input_groups=frozenset(),
    label_unchecked_axes=False,
    # The prototype's own rule set replays the prototype's own pull.
    allow_fixture_site_inputs=True,
)

PRODUCTION = RuleConfig(
    name="production",
    in_region_readings=frozenset({EAST_OF_CASCADE_CREST_READING}),
    range_wide_origin_is_not_state_claim=True,
    inherit_stratification=True,
    null_restriction_depth_is_unknown=True,
    introduced_flag_text=MappingProxyType(
        {
            "greenstrip": INTRODUCED_FLAG_CPS_394,
            "post_fire_restoration": INTRODUCED_FLAG_PLAIN,
            "hedgerow_buffer": INTRODUCED_FLAG_PLAIN,
        }
    ),
    pick_definition=PRODUCTION_PICK_DEFINITION,
    permitted_licences=PRODUCTION_PERMITTED_LICENCES,
    # Every site measurement group the envelope reads: a withheld one would pass its axes as unknown (review B1).
    required_site_input_groups=frozenset(SITE_INPUT_GROUPS),
    label_unchecked_axes=True,
    # A PRISM declaration dated the prototype's pull (or earlier) is the fixture's, not production's own.
    dated_attribution_accessed_after=PROTOTYPE_ACCESS_DATE,
)


def canonical_value(value: object) -> object:
    """A JSON-ready copy with mapping keys and set members sorted, so equal values always serialise identically."""
    if isinstance(value, Mapping):
        return {str(key): canonical_value(item) for key, item in sorted(value.items())}
    if isinstance(value, frozenset | set):
        return sorted((canonical_value(item) for item in value), key=canonical_json)
    if isinstance(value, tuple | list):
        return [canonical_value(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    """Compact, key-sorted JSON of `canonical_value(value)`."""
    return json.dumps(canonical_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rule_config_fingerprint(config: RuleConfig) -> str:
    """'<name> sha256:<hex>' over every field as canonical JSON, so a modified preset cannot pose as the original."""
    values = {field.name: getattr(config, field.name) for field in dataclasses.fields(config)}
    digest = hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()
    return f"{config.name} sha256:{digest}"


def assert_rule_config(config: RuleConfig) -> None:
    """Raise on an unknown licence id, state code or site-input group, a malformed access floor, or fire wording."""
    # Flag texts are served in every introduced label and the pick definition in the metadata (AGENTS.md §Labels).
    fire_worded = fire_wording([*config.introduced_flag_text.values(), config.pick_definition])
    if fire_worded:
        message = f"rule config {config.name!r} flag texts or pick definition carry fire wording: {fire_worded}"
        raise ValueError(message)
    unknown_licences = sorted((config.permitted_licences or frozenset()) - KNOWN_LICENCES)
    unknown_states = sorted(config.declared_empty_exclusion_states - USPS_STATE_CODES)
    unknown_groups = sorted(config.required_site_input_groups - set(SITE_INPUT_GROUPS))
    floor = config.dated_attribution_accessed_after
    if floor is not None and iso_calendar_date(floor) is None:
        message = f"rule config {config.name!r} access floor {floor!r} is not written YYYY-MM-DD"
        raise ValueError(message)
    if unknown_licences or unknown_states or unknown_groups:
        message = (
            f"rule config {config.name!r} names unknown licence ids {unknown_licences}, state codes {unknown_states} "
            f"or site input groups {unknown_groups}"
        )
        raise ValueError(message)
