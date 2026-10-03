"""Flows: inputs that must never reach a served table are refused at load, removed from pools, or stop both seams."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl
import pytest

from agri_data_service.warehouse.plant_suitability import engine as engine_module
from agri_data_service.warehouse.plant_suitability.config import (
    INTRODUCED_FLAG_PLAIN,
    PRODUCTION,
    V0_FROZEN,
    rule_config_fingerprint,
)
from agri_data_service.warehouse.plant_suitability.engine import MAX_CELLS_PER_CALL, METADATA_RULE_CONFIG, prepare
from agri_data_service.warehouse.plant_suitability.labels import FIRE_LABEL_PREFIX, pick_label_expression
from agri_data_service.warehouse.plant_suitability.licences import ALL_RIGHTS_RESERVED, UNRECORDED, US_GOVERNMENT_WORK
from agri_data_service.warehouse.plant_suitability.schemas import GUILDS
from agri_data_service.warehouse.plant_suitability.site import SiteInputProvenance
from agri_data_service.warehouse.plant_suitability.wetland import resolve_wetland_ratings
from tests.plant_suitability.support import candidate, cell_row, served_labels

if TYPE_CHECKING:
    from collections.abc import Callable

    from _pytest.mark.structures import ParameterSet

    from agri_data_service.warehouse.plant_suitability.config import RuleConfig
    from tests.plant_suitability.support import SuitabilityFixture

NOXIOUS_STATE = "ID"
# Idaho fescue is PLANTS L48 native; sheep and hard fescue are introduced. All three are in the Boise PRODUCTION pool.
NATIVE_FESCUE = "Festuca idahoensis"
INTRODUCED_FESCUES = frozenset({"Festuca ovina", "Festuca brevipila"})
# A federal restoration guide whose title names a fire; it states this woody-buffer taxon's Boise origin.
FIRE_NAMED_SOURCE = "BLM Range Fire 2026"
FIRE_CITING_WOODY_TAXON = "Eriogonum umbellatum"
FIRE_NAMED_EDITION = " (Wildfire Edition)"
WOODY = "hedgerow_buffer"
# A federal woody guide whose source tag (with its page) a woody top-3 label cites at the Boise licence cell.
WOODY_TOP_THREE_SOURCE = ("idpm_tn2a_2017", "NRCS TN PM-2A (2017)")
# ODFW 2017 is one document curated under two source ids (odfw_2017, odfw_rehab_2017).
ODFW_SECOND_SOURCE_ID = "odfw_rehab_2017"
# A variant that does not require the soil survey, so its licence gate withholds the group instead of refusing it.
SOIL_SURVEY_OPTIONAL = replace(
    PRODUCTION, required_site_input_groups=PRODUCTION.required_site_input_groups - {"soil_survey"}
)
# A variant requiring no site input group, so an undeclared group with values meets the per-call withholding check.
NOTHING_REQUIRED = replace(PRODUCTION, required_site_input_groups=frozenset())
# Rule sets naming a licence id, a state code or a site-input group outside the fixed vocabularies.
UNKNOWN_LICENCE_RULES = replace(PRODUCTION, permitted_licences=frozenset({"CC-BY"}))
UNKNOWN_STATE_RULES = replace(PRODUCTION, declared_empty_exclusion_states=frozenset({"Oregon"}))
UNKNOWN_GROUP_RULES = replace(PRODUCTION, required_site_input_groups=frozenset({"rainfall"}))
# Rule sets whose own texts carry fire wording (review R3): a claim, a bare fire word, a disguised claim, and a claim
# with no fire-family stem. Flag texts are served in every introduced label, the pick definition in the metadata.
FLAG_TEXT_CLAIM_RULES = replace(
    PRODUCTION,
    introduced_flag_text={
        **PRODUCTION.introduced_flag_text,
        "post_fire_restoration": "introduced \u2014 this hedge slows wildfire spread",
    },
)
FLAG_TEXT_FIRE_WORD_RULES = replace(
    PRODUCTION, introduced_flag_text=dict.fromkeys(GUILDS, "introduced \u2014 a fire-following annual")
)
DISGUISED_PICK_DEFINITION_RULES = replace(PRODUCTION, pick_definition="Picks are f\u00a1re-res\u00a1stant hedges")
STEMLESS_PICK_DEFINITION_RULES = replace(PRODUCTION, pick_definition="Picks are hedges that create defensible space")
RULE_TEXT_FIRE_WORDING = "rule config 'production' flag texts or pick definition carry fire wording"
# The verbatim PLANTS field with a Cyrillic i: both guards fold it back to the allowed prefix.
LOOKALIKE_FIRE_LABEL_PREFIX = FIRE_LABEL_PREFIX.replace("fire-", "f\u0456re-", 1)


def replaced_value(frame: pl.DataFrame, column: str, old: str, new: str | None) -> pl.DataFrame:
    """The frame with every `old` in the column replaced by `new`."""
    return frame.with_columns(
        pl.when(pl.col(column) == old).then(pl.lit(new, dtype=pl.String)).otherwise(pl.col(column)).alias(column)
    )


def unchanged(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """The fixture as generated (the rule set is what is malformed)."""
    return fixture


def envelope_never_resolved(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """The envelope as build_fixtures.py wrote it: ratings from the prototype, never resolved by the engine."""
    return replace(fixture, fixed_envelope=fixture.species)


def envelope_resolved_under_v0(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """An envelope whose NWPL ratings read printed names the PRODUCTION licence gate drops (review p10)."""
    resolved = resolve_wetland_ratings(fixture.species, fixture.guide_rows, fixture.wetland_list, V0_FROZEN)
    return replace(fixture, fixed_envelope=resolved)


def hyphenated_origin_scope(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'range-wide' for 'range_wide'."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "origin_scope", "range_wide", "range-wide"))


def short_guild_name(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'hedgerow' for 'hedgerow_buffer'."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "guild", WOODY, "hedgerow"))


def unknown_plant_role(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'shrubs' for 'shrub'."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "plant_role", "shrub", "shrubs"))


def unknown_match_route(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """A route name the matcher never emits."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "match_route", "accepted_name", "exact"))


def unmapped_habitat_qualifier(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'sandy' for 'sandy_or_loam': a qualifier with no site mapping."""
    guide_rows = replaced_value(fixture.guide_rows, "habitat_qualifier", "sandy_or_loam", "sandy")
    return replace(fixture, guide_rows=guide_rows)


def free_text_licence(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """The federal guides' licence written as prose instead of the canonical id."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "license", US_GOVERNMENT_WORK, "US Gov work"))


def mixed_licences_in_one_source(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """One row of a federal guide relicensed, so the source would be both admitted and excluded."""
    source_id = fixture.taxa("boise", "licence_kept_source_id")
    first_row = fixture.guide_rows.filter(pl.col("source_id") == source_id)["guide_row_id"].min()
    relicensed = pl.when(pl.col("guide_row_id") == first_row).then(pl.lit(ALL_RIGHTS_RESERVED))
    return replace(
        fixture, guide_rows=fixture.guide_rows.with_columns(relicensed.otherwise("license").alias("license"))
    )


def mixed_licences_in_one_document(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """One of ODFW 2017's two source ids relicensed: labels would cite one short name under two licences."""
    relicensed = pl.when(pl.col("source_id") == ODFW_SECOND_SOURCE_ID).then(pl.lit(US_GOVERNMENT_WORK))
    return replace(
        fixture, guide_rows=fixture.guide_rows.with_columns(relicensed.otherwise("license").alias("license"))
    )


def two_short_names_for_one_source(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """One row of a federal guide cited under another short name."""
    source_id = fixture.taxa("boise", "licence_kept_source_id")
    first_row = fixture.guide_rows.filter(pl.col("source_id") == source_id)["guide_row_id"].min()
    renamed = pl.when(pl.col("guide_row_id") == first_row).then(pl.lit("NRCS TN PM-77"))
    return replace(
        fixture,
        guide_rows=fixture.guide_rows.with_columns(renamed.otherwise("source_short_name").alias("source_short_name")),
    )


def guide_row_naming_a_missing_taxon(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """The envelope without a taxon a guide row names."""
    named = fixture.guide_rows.select(pl.col("matched_plant_ids").explode(empty_as_null=True)).to_series().drop_nulls()
    return replace(fixture, species=fixture.species.filter(pl.col("plant_id") != named[0]))


def inverted_species_range(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """One taxon's PLANTS pH minimum lifted above its maximum."""
    first = fixture.species.filter(pl.col("ph_min").is_not_null() & pl.col("ph_max").is_not_null())["plant_id"][0]
    inverted = pl.when(pl.col("plant_id") == first).then(pl.col("ph_max") + 1).otherwise(pl.col("ph_min"))
    return replace(fixture, species=fixture.species.with_columns(inverted.alias("ph_min")))


def nwpl_list_without_dogwoods(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """An NWPL list missing every dogwood, as if the red-osier dogwood rename target were lost."""
    return replace(fixture, wetland_list=fixture.wetland_list.filter(~pl.col("nwpl_name").str.starts_with("Cornus ")))


def lower_case_nwpl_region(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'aw' for the Arid West code 'AW' (it would otherwise read the WMVC rating)."""
    return replace(fixture, site=replaced_value(fixture.site, "nwpl_region", "AW", "aw"))


def lower_case_hydric_rating(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'no' for the SSURGO hydric rating 'No'."""
    return replace(fixture, site=replaced_value(fixture.site, "hydric_rating", "No", "no"))


def lower_case_drainage_class(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'well drained' for the SSURGO class 'Well drained' (it would read as unknown wetness, never a pass)."""
    return replace(fixture, site=replaced_value(fixture.site, "drainage_class", "Well drained", "well drained"))


def capitalised_restriction_status(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'Recorded' for 'recorded' (the root-depth axis would never compare the recorded depth)."""
    return replace(fixture, site=replaced_value(fixture.site, "restriction_status", "recorded", "Recorded"))


def soil_class_for_texture_group(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'sand' for the PLANTS texture group 'coarse' (the texture axis would read it as unknown)."""
    return replace(fixture, site=replaced_value(fixture.site, "site_texture_group", "coarse", "sand"))


def lower_case_site_state(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'id' for the Boise cells' state code."""
    return replace(fixture, site=replaced_value(fixture.site, "state", "ID", "id"))


def spelled_out_exclusion_state(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """The Idaho noxious list keyed 'Idaho' instead of 'ID' (it would otherwise match no Boise cell)."""
    return replace(fixture, exclusions=replaced_value(fixture.exclusions, "state", "ID", "Idaho"))


def spelled_out_noxious_state(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """A guide row's own noxious annotation keyed 'Idaho' (it would otherwise match no Boise cell)."""
    first_row = pl.col("guide_row_id") == fixture.guide_rows["guide_row_id"].min()
    annotated = pl.when(first_row).then(pl.lit(["Idaho"], dtype=pl.List(pl.String))).otherwise("noxious_states")
    return replace(fixture, guide_rows=fixture.guide_rows.with_columns(annotated.alias("noxious_states")))


def bare_genus_exclusion(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """An Idaho listing of the bare genus 'Festuca', without 'spp.' (no rule could key it, so it excluded nothing)."""
    listing = pl.DataFrame({"state": [NOXIOUS_STATE], "scientific_name": ["Festuca"]})
    return replace(fixture, exclusions=pl.concat([fixture.exclusions, listing], how="diagonal_relaxed"))


def fire_claim_in_a_species_common_name(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """One taxon's PLANTS common name rewritten as a fire claim (it would be served in the top-3 common names)."""
    first = pl.col("plant_id") == fixture.species["plant_id"].min()
    claimed = pl.when(first).then(pl.lit("hedge that slows wildfire")).otherwise("common_name")
    return replace(fixture, species=fixture.species.with_columns(claimed.alias("common_name")))


def claim_composed_from_a_short_name_and_a_page(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """A federal woody guide titled '... Plants That Reduce' with pages 'Wildfire appendix': each clean alone."""
    source = pl.col("source_id") == WOODY_TOP_THREE_SOURCE[0]
    return replace(
        fixture,
        guide_rows=fixture.guide_rows.with_columns(
            pl.when(source)
            .then(pl.lit("NRCS Plants That Reduce"))
            .otherwise("source_short_name")
            .alias("source_short_name"),
            pl.when(source).then(pl.lit("Wildfire appendix")).otherwise("page").alias("page"),
        ),
    )


def repeated_cell_id(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """The first fixture cell served twice in one call."""
    return replace(fixture, site=pl.concat([fixture.site, fixture.site.head(1)]))


def missing_state_noxious_list(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """No Oregon noxious-list rows while Bend and Corvallis cells are scored."""
    return replace(fixture, exclusions=fixture.exclusions.filter(pl.col("state") != "OR"))


def region_spanning_two_states(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """Two Boise cells, one of them relabelled Oregon."""
    boise = fixture.site.filter(pl.col("region") == "boise").head(2)
    relabelled = boise.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.lit("OR")).otherwise("state").alias("state")
    )
    return replace(fixture, site=relabelled)


def capitalised_region(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'Boise' for the region key 'boise'."""
    return replace(fixture, site=replaced_value(fixture.site, "region", "boise", "Boise"))


def undeclared_site_input_group(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """The record-low temperatures served with no source or licence declared for them."""
    groups = {group: source for group, source in fixture.site_provenance.groups.items() if group != "cold"}
    return replace(fixture, site_provenance=SiteInputProvenance(groups))


def unknown_site_input_group(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """A declaration for a group the engine does not define."""
    groups = {**fixture.site_provenance.groups, "rainfall": fixture.era5_precipitation_source}
    return replace(fixture, site_provenance=SiteInputProvenance(groups))


def free_text_site_input_licence(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """The ERA5 declaration's licence written as prose instead of the canonical id."""
    return replace(fixture, era5_precipitation_source=replace(fixture.era5_precipitation_source, licence="CC BY"))


def over_the_cell_budget(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """One more cell than a single call may evaluate."""
    copies = fixture.site.head(1).sample(n=MAX_CELLS_PER_CALL + 1, with_replacement=True)
    return replace(fixture, site=copies.with_columns(pl.format("cell_{}", pl.int_range(pl.len())).alias("cell_id")))


def rejection(
    corrupt: Callable[[SuitabilityFixture], SuitabilityFixture],
    message: str,
    config: RuleConfig = PRODUCTION,
    name: str | None = None,
) -> ParameterSet:
    """One row of the rejection table, named after the corruption unless the rule set is what is malformed."""
    return pytest.param(corrupt, config, message, id=name or corrupt.__name__)


@pytest.mark.parametrize(
    ("corrupt", "config", "message"),
    [
        rejection(envelope_never_resolved, r"resolved under \['None'\]"),
        rejection(envelope_resolved_under_v0, r"resolved under \['v0_frozen "),
        rejection(hyphenated_origin_scope, r"unknown origin_scope values \['range-wide'\]"),
        rejection(short_guild_name, r"unknown guild values \['hedgerow'\]"),
        rejection(unknown_plant_role, r"unknown plant_role values \['shrubs'\]"),
        rejection(unknown_match_route, r"unknown match_route values \['exact'\]"),
        rejection(unmapped_habitat_qualifier, r"unknown habitat_qualifier values \['sandy'\]"),
        rejection(free_text_licence, r"unknown license values \['US Gov work'\]"),
        rejection(mixed_licences_in_one_source, "source_id -> license"),
        rejection(mixed_licences_in_one_document, "source_short_name -> license"),
        rejection(two_short_names_for_one_source, "source_id -> source_short_name"),
        rejection(guide_row_naming_a_missing_taxon, "missing from the species envelope"),
        rejection(inverted_species_range, r"inverted ranges .*ph_min>ph_max"),
        rejection(nwpl_list_without_dogwoods, "wetland-genus pool taxa without an NWPL rating"),
        rejection(lower_case_nwpl_region, r"unknown nwpl_region values \['aw'\]"),
        rejection(lower_case_hydric_rating, r"unknown hydric_rating values \['no'\]"),
        rejection(lower_case_drainage_class, r"unknown drainage_class values \['well drained'\]"),
        rejection(capitalised_restriction_status, r"unknown restriction_status values \['Recorded'\]"),
        rejection(soil_class_for_texture_group, r"unknown site_texture_group values \['sand'\]"),
        rejection(lower_case_site_state, r"site conditions carry unknown state values \['id'\]"),
        rejection(spelled_out_exclusion_state, r"exclusions carry unknown state values \['Idaho'\]"),
        rejection(spelled_out_noxious_state, r"guide rows carry unknown noxious_states values \['Idaho'\]"),
        rejection(bare_genus_exclusion, r"exclusions name neither a binomial.*\['ID: Festuca'\]"),
        rejection(fire_claim_in_a_species_common_name, r"species envelope taxa \[.+\] claim a fire effect"),
        rejection(
            claim_composed_from_a_short_name_and_a_page,
            r"composed source tags or listed names from sources \['idpm_tn2a_2017'\] claim a fire effect",
        ),
        rejection(repeated_cell_id, "site conditions repeat cell ids"),
        rejection(missing_state_noxious_list, "no state noxious-list rows for OR"),
        rejection(region_spanning_two_states, "region boise spans states"),
        rejection(capitalised_region, r"unknown regions \['Boise'\]"),
        rejection(undeclared_site_input_group, r"undeclared \['cold'\]"),
        rejection(
            undeclared_site_input_group,
            "carry values without a provenance declaration",
            NOTHING_REQUIRED,
            "undeclared_optional_site_input_group",
        ),
        rejection(unknown_site_input_group, r"unknown groups \['rainfall'\]"),
        rejection(free_text_site_input_licence, r"licence ids \['CC BY'\]"),
        rejection(unchanged, r"licence ids \['CC-BY'\]", UNKNOWN_LICENCE_RULES, "rule_set_licence"),
        rejection(unchanged, r"state codes \['Oregon'\]", UNKNOWN_STATE_RULES, "rule_set_state"),
        rejection(unchanged, r"site input groups \['rainfall'\]", UNKNOWN_GROUP_RULES, "rule_set_site_input_group"),
        rejection(unchanged, RULE_TEXT_FIRE_WORDING, FLAG_TEXT_CLAIM_RULES, "rule_set_flag_text_claim"),
        rejection(unchanged, RULE_TEXT_FIRE_WORDING, FLAG_TEXT_FIRE_WORD_RULES, "rule_set_flag_text_fire_word"),
        rejection(unchanged, RULE_TEXT_FIRE_WORDING, DISGUISED_PICK_DEFINITION_RULES, "rule_set_disguised_definition"),
        rejection(unchanged, RULE_TEXT_FIRE_WORDING, STEMLESS_PICK_DEFINITION_RULES, "rule_set_stemless_definition"),
        rejection(over_the_cell_budget, "exceed the per-call budget"),
    ],
)
def test_malformed_inputs_are_refused_before_any_cell_is_scored(
    suitability: SuitabilityFixture,
    corrupt: Callable[[SuitabilityFixture], SuitabilityFixture],
    config: RuleConfig,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        corrupt(suitability).evaluate(config)


def served_top_three(cells: pl.DataFrame, cell_id: str) -> set[str]:
    """Every name any guild serves in its top three at the cell."""
    row = cell_row(cells, cell_id)
    return {name for guild in GUILDS for name in (row[f"{guild}_top3"] or "").split(";") if name}


def named(names: set[str], subject: str) -> set[str]:
    """The names that are the subject taxon, or that belong to the subject genus."""
    return {name for name in names if subject in (name, name.split(" ")[0])}


def pool_names(candidates: pl.DataFrame) -> set[str]:
    """Every taxon in any guild's pool at the cell."""
    return set(candidates["display_name"].drop_nulls().to_list())


def renamed_sources(fixture: SuitabilityFixture, guild: str, suffix: str) -> SuitabilityFixture:
    """Every source with a row in the guild, cited under its short name plus the suffix (whole sources, all rows)."""
    sources = fixture.guide_rows.filter(pl.col("guild") == guild)["source_id"].unique().to_list()
    renamed = pl.when(pl.col("source_id").is_in(sources)).then(pl.col("source_short_name") + suffix)
    return replace(
        fixture,
        guide_rows=fixture.guide_rows.with_columns(renamed.otherwise("source_short_name").alias("source_short_name")),
    )


def test_state_noxious_listings_remove_a_taxon_from_the_pool_and_every_top_three(
    suitability: SuitabilityFixture, v0_cells: pl.DataFrame
) -> None:
    binomial_taxon = suitability.taxa("boise", "noxious_binomial_taxon")
    genus = suitability.taxa("boise", "noxious_genus")
    annotated_taxon = suitability.taxa("boise", "noxious_annotation_taxon")
    annotated_id = suitability.species.filter(pl.col("accepted_name") == annotated_taxon)["plant_id"].item()
    listings = pl.DataFrame(
        {"state": [NOXIOUS_STATE, NOXIOUS_STATE], "scientific_name": [binomial_taxon, f"{genus} spp."]},
        schema={"state": pl.String, "scientific_name": pl.String},
    )
    annotated_rows = pl.col("matched_plant_ids").list.contains(annotated_id).fill_null(value=False)
    listed = replace(
        suitability,
        exclusions=pl.concat([suitability.exclusions, listings], how="diagonal_relaxed"),
        guide_rows=suitability.guide_rows.with_columns(
            pl.when(annotated_rows)
            .then(pl.lit([NOXIOUS_STATE], dtype=pl.List(pl.String)))
            .otherwise(pl.col("noxious_states"))
            .alias("noxious_states")
        ),
    )

    listed_cells = listed.evaluate(V0_FROZEN)

    boise_cells = v0_cells.filter(pl.col("region") == "boise")["cell_id"].to_list()
    for subject in (binomial_taxon, genus, annotated_taxon):
        picked_at = [cell_id for cell_id in boise_cells if named(served_top_three(v0_cells, cell_id), subject)]
        assert picked_at, f"{subject} is in no v0 top three, so this flow proves nothing"
        assert not [cell_id for cell_id in picked_at if named(served_top_three(listed_cells, cell_id), subject)]
        pool = listed.candidates(picked_at[0], V0_FROZEN)["display_name"].drop_nulls().to_list()
        assert not named(set(pool), subject)


def test_a_non_native_only_genus_listing_removes_introduced_members_and_keeps_the_native_one(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    listing = pl.DataFrame(
        {"state": [NOXIOUS_STATE], "scientific_name": ["Festuca spp."], "listed_name": ["Festuca, non-native species"]}
    )
    listed = replace(suitability, exclusions=pl.concat([suitability.exclusions, listing], how="diagonal_relaxed"))

    unlisted_pool = pool_names(suitability.candidates(cell_id, PRODUCTION))
    listed_pool = pool_names(listed.candidates(cell_id, PRODUCTION))

    assert {NATIVE_FESCUE, *INTRODUCED_FESCUES} <= unlisted_pool
    assert NATIVE_FESCUE in listed_pool
    assert not INTRODUCED_FESCUES & listed_pool


def test_a_state_declared_to_have_no_noxious_list_is_served_without_one(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("bend", "burned_cell")
    without_oregon = replace(suitability, exclusions=suitability.exclusions.filter(pl.col("state") != "OR"))
    declared = replace(PRODUCTION, declared_empty_exclusion_states=frozenset({"OR"}))

    cells = without_oregon.evaluate(declared, site=suitability.site_row(cell_id))

    assert [cell_row(cells, cell_id)[f"{guild}_status"] for guild in GUILDS] == ["scored"] * len(GUILDS)


def test_an_engine_keeps_each_region_on_the_state_it_was_first_served_or_warmed_with(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    site = suitability.served_site(suitability.site_row(cell_id), PRODUCTION)
    relabelled = site.with_columns(pl.lit("OR").alias("state"))
    served_first = suitability.prepared(PRODUCTION)
    warmed = suitability.prepared(PRODUCTION)

    served_first.candidates_for_cell(site)
    warmed.warm({"boise": "OR"})

    with pytest.raises(ValueError, match=r"served before with another state .*'boise': \('ID', 'OR'\)"):
        served_first.evaluate_cells(relabelled)
    with pytest.raises(ValueError, match=r"served before with another state .*'boise': \('OR', 'ID'\)"):
        warmed.candidates_for_cell(site)
    assert served_first.candidates_for_cell(site).height > 0


def test_a_withheld_soil_survey_still_withholds_the_cells_it_marks(suitability: SuitabilityFixture) -> None:
    groups = dict(suitability.site_provenance.groups)
    groups["soil_survey"] = replace(groups["soil_survey"], licence=UNRECORDED)
    survey_withheld = replace(suitability, site_provenance=SiteInputProvenance(groups))

    cells = survey_withheld.evaluate(SOIL_SURVEY_OPTIONAL)

    marked = suitability.site.filter(pl.col("scoring_null_reason").is_not_null())
    assert marked.height > 0
    served_reasons = cells.join(marked.select("cell_id", "scoring_null_reason"), on="cell_id", suffix="_marked")
    assert served_reasons["scoring_null_reason"].to_list() == served_reasons["scoring_null_reason_marked"].to_list()
    for guild in GUILDS:
        assert served_reasons[f"{guild}_status"].null_count() == marked.height
        assert served_reasons[f"{guild}_count"].null_count() == marked.height
    scored = cells.filter(pl.col("scoring_null_reason").is_null())
    assert scored.height == suitability.site.height - marked.height
    assert scored.filter(pl.col(f"{WOODY}_status") == "scored").height == scored.height


def test_a_caller_mutating_its_declaration_or_rule_config_after_prepare_changes_nothing_served(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "soil_failures_cell")
    groups = dict(suitability.provenance(PRODUCTION).groups)
    flag_texts = dict(PRODUCTION.introduced_flag_text)
    # Every set field handed in as the caller's own mutable set (review R3).
    overrides, licences = set(PRODUCTION.in_region_overrides), set(PRODUCTION.permitted_licences or ())
    required = set(PRODUCTION.required_site_input_groups)
    declared_empty = set(PRODUCTION.declared_empty_exclusion_states)
    config = replace(
        PRODUCTION,
        introduced_flag_text=flag_texts,
        in_region_overrides=overrides,
        permitted_licences=licences,
        required_site_input_groups=required,
        declared_empty_exclusion_states=declared_empty,
    )
    engine = prepare(suitability.envelope(config), suitability.guide_rows, suitability.exclusions, config,
                     SiteInputProvenance(groups))  # fmt: skip
    untouched = suitability.prepared(PRODUCTION)
    site = suitability.served_site(suitability.site, PRODUCTION)
    point_site = suitability.served_site(suitability.site_row(cell_id), PRODUCTION)
    point = untouched.candidates_for_cell(point_site)
    woody_origins = point.filter(pl.col("guild") == WOODY)["origin_label"]
    assert woody_origins.str.starts_with(INTRODUCED_FLAG_PLAIN).any(), "no introduced woody taxon: proves nothing"

    groups["precipitation"] = replace(groups["precipitation"], licence=UNRECORDED)
    flag_texts[WOODY] = "introduced (rewritten after prepare)"
    for caller_set in (overrides, licences, required):
        caller_set.clear()
    declared_empty.add(NOXIOUS_STATE)

    assert engine.evaluate_cells(site).equals(untouched.evaluate_cells(site))
    assert engine.candidates_for_cell(point_site).equals(point)
    assert engine.metadata() == untouched.metadata()
    assert rule_config_fingerprint(engine.config) == untouched.metadata()[METADATA_RULE_CONFIG]


@pytest.mark.parametrize(
    ("appended_to_woody_source_names", "message"),
    [
        pytest.param(" hedge that reduces wildfire", r"guide rows from sources \[.+\] claim a fire effect", id="claim"),
        pytest.param(f" {FIRE_LABEL_PREFIX}Yes", "hedgerow_buffer labels carry the PLANTS fire field", id="fire_field"),
    ],
)
def test_both_seams_refuse_fire_wording_that_arrives_in_a_source_name(
    suitability: SuitabilityFixture, appended_to_woody_source_names: str, message: str
) -> None:
    renamed = renamed_sources(suitability, WOODY, appended_to_woody_source_names)
    cell_id = suitability.role_cell("boise", "burned_cell")

    with pytest.raises(ValueError, match=message):
        renamed.evaluate(PRODUCTION, site=suitability.site_row(cell_id))
    with pytest.raises(ValueError, match=message):
        renamed.candidates(cell_id, PRODUCTION)


def test_a_fire_named_source_is_a_citation_both_seams_serve_on_the_woody_buffer(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    woody = pl.col("guild") == WOODY
    retitled = renamed_sources(suitability, WOODY, FIRE_NAMED_EDITION)

    point = suitability.candidates(cell_id, PRODUCTION)
    batch = suitability.evaluate(PRODUCTION, site=suitability.site_row(cell_id))
    retitled_batch = served_labels(retitled.evaluate(PRODUCTION, site=suitability.site_row(cell_id)), WOODY)
    retitled_point = retitled.candidates(cell_id, PRODUCTION).filter(woody & pl.col("is_pick"))["pick_label"]

    assert FIRE_NAMED_SOURCE in candidate(point, FIRE_CITING_WOODY_TAXON, WOODY)["origin_label"]
    assert cell_row(batch, cell_id)[f"{WOODY}_status"] == "scored"
    assert retitled_batch
    assert all(FIRE_NAMED_EDITION in label for label in retitled_batch)
    assert retitled_point.len() > 0
    assert all(FIRE_NAMED_EDITION in label for label in retitled_point)


STEM_IN_METADATA = "fire words outside the allow-list"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        *(
            pytest.param(f"ERA5-Land record low, where hedges reduce{separator}wildfire", STEM_IN_METADATA, id=name)
            for separator, name in ((" ", "space"), ("\n", "newline"), ("\t", "tab"), ("\r", "carriage_return"))
        ),
        # Review R3: a claim with no fire-family stem passes the stem guard, so the metadata is also read for claims.
        pytest.param(
            "ERA5-Land; these hedges create defensible space", "metadata texts claim a fire effect", id="stemless"
        ),
    ],
)
def test_a_fire_claim_in_the_site_input_provenance_stops_both_seams(
    suitability: SuitabilityFixture, source: str, message: str
) -> None:
    groups = dict(suitability.site_provenance.groups)
    groups["cold"] = replace(groups["cold"], source=source)
    claimed = replace(suitability, site_provenance=SiteInputProvenance(groups))
    cell_id = suitability.role_cell("boise", "burned_cell")

    with pytest.raises(ValueError, match=message):
        claimed.evaluate(PRODUCTION, site=suitability.site_row(cell_id))
    with pytest.raises(ValueError, match=message):
        claimed.candidates(cell_id, PRODUCTION)


def test_an_unregistered_fire_word_in_a_page_citation_stops_the_batch_top_three_and_the_point_tool(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "licence_cell")
    source_id, short_name = WOODY_TOP_THREE_SOURCE
    rows = suitability.guide_rows
    page = pl.when(pl.col("source_id") == source_id).then(pl.lit("Fire appendix")).otherwise(pl.col("page"))
    repaged = replace(suitability, guide_rows=rows.with_columns(page.alias("page")))
    labels = served_labels(suitability.evaluate(PRODUCTION, site=suitability.site_row(cell_id)), WOODY)
    assert any(f"{short_name} p." in label for label in labels), "no top-3 source tag cites it here: proves nothing"

    with pytest.raises(ValueError, match="fire words outside the allow-list"):
        repaged.evaluate(PRODUCTION, site=suitability.site_row(cell_id))
    with pytest.raises(ValueError, match="fire words outside the allow-list"):
        repaged.candidates(cell_id, PRODUCTION)


# Since review R3 the rule set refuses fire wording in its flag texts, so only the verbatim PLANTS field (which both
# guards allow) can still reach a woody origin label; the woody check is what stops it there.
@pytest.mark.parametrize(
    "woody_flag_text",
    [
        pytest.param(f"introduced \u2014 {FIRE_LABEL_PREFIX}Yes", id="verbatim_field"),
        pytest.param(f"introduced \u2014 {LOOKALIKE_FIRE_LABEL_PREFIX}Yes", id="lookalike_letter"),
    ],
)
def test_the_point_tool_refuses_the_fire_field_in_a_woody_origin_label_even_on_a_non_pick(
    suitability: SuitabilityFixture, woody_flag_text: str
) -> None:
    cell_id = suitability.role_cell("boise", "soil_failures_cell")
    flag_texts = MappingProxyType({**PRODUCTION.introduced_flag_text, WOODY: woody_flag_text})
    config = replace(PRODUCTION, introduced_flag_text=flag_texts)
    woody = suitability.candidates(cell_id, PRODUCTION).filter(pl.col("guild") == WOODY)
    introduced = woody.filter(pl.col("origin_label").str.starts_with(INTRODUCED_FLAG_PLAIN))
    assert introduced.height > 0, "no introduced woody taxon here, so this flow proves nothing"
    assert not introduced["is_pick"].any(), "an introduced woody pick here would carry the flag in its pick label"

    with pytest.raises(ValueError, match="hedgerow_buffer labels carry the PLANTS fire field"):
        suitability.candidates(cell_id, config)


def test_both_seams_refuse_a_fire_label_guild_whose_labels_lose_the_verbatim_prefix(
    suitability: SuitabilityFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")

    def woody_wording(_guild: str, *, label_unchecked_axes: bool) -> pl.Expr:
        """A regressed label builder that renders every guild as the woody buffer (no fire field)."""
        return pick_label_expression(WOODY, label_unchecked_axes=label_unchecked_axes)

    monkeypatch.setattr(engine_module, "pick_label_expression", woody_wording)

    with pytest.raises(ValueError, match=r"(greenstrip|post_fire_restoration) labels lack the verbatim PLANTS fire"):
        suitability.evaluate(PRODUCTION, site=suitability.site_row(cell_id))
    with pytest.raises(ValueError, match=r"(greenstrip|post_fire_restoration) labels lack the verbatim PLANTS fire"):
        suitability.candidates(cell_id, PRODUCTION)
