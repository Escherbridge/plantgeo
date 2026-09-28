"""Rule presets: every v0-vs-production difference is one named field; see AGENTS.md §Presets here."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from agri_data_service.warehouse.plant_suitability.schemas import GUILDS

if TYPE_CHECKING:
    from collections.abc import Mapping

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
# Owner reading: 'eastern Oregon' / Intermountain guides lie east of the Cascade crest, so they are in-region for Bend.
EAST_OF_CASCADE_CREST_SOURCE_IDS = ("idpm_tn2a_2017", "nrcs_tn50_2008", "orwa_2000", "orwa_guide_2000")
EAST_OF_CASCADE_CREST_REGION = "bend"
# Owner rule "exclude non-commercial sources from v1", read as an allow-list that fails closed (AGENTS.md §Pools).
PRODUCTION_PERMITTED_LICENCES = frozenset({"public domain", "US Government work", "CC0", "CC BY"})


@dataclass(frozen=True)
class RuleConfig:
    """One named rule set; AGENTS.md §Presets gives each field's owner decision."""

    name: str
    in_region_overrides: frozenset[tuple[str, str]]
    range_wide_origin_is_not_state_claim: bool
    inherit_stratification: bool
    null_restriction_depth_is_unknown: bool
    introduced_flag_text: Mapping[str, str]
    pick_definition: str
    permitted_licences: frozenset[str] | None


V0_FROZEN = RuleConfig(
    name="v0_frozen",
    in_region_overrides=frozenset(),
    range_wide_origin_is_not_state_claim=False,
    inherit_stratification=False,
    null_restriction_depth_is_unknown=False,
    introduced_flag_text=MappingProxyType(dict.fromkeys(GUILDS, INTRODUCED_FLAG_CPS_394)),
    pick_definition=V0_PICK_DEFINITION,
    permitted_licences=None,
)

PRODUCTION = RuleConfig(
    name="production",
    in_region_overrides=frozenset(
        (source_id, EAST_OF_CASCADE_CREST_REGION) for source_id in EAST_OF_CASCADE_CREST_SOURCE_IDS
    ),
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
)
