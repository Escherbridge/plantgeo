"""NWPL 2022 wetland ratings per taxon, with the name-resolution table; see AGENTS.md §Wetland here."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

import polars as pl

from agri_data_service.warehouse.plant_suitability.names import binomial_key
from agri_data_service.warehouse.plant_suitability.schemas import WETLAND_LIST_SCHEMA, conform

if TYPE_CHECKING:
    from collections.abc import Iterable

WETNESS_RANK = {"UPL": 1, "FACU": 2, "FAC": 3, "FACW": 4, "OBL": 5}
# NWPL 2022 files these under names the PLANTS synonym lists do not link (keyed by the PLANTS / regional name).
NWPL_NAME_RESOLUTIONS = {"Cornus sericea": "Cornus alba", "Ledum glandulosum": "Rhododendron columbianum"}
WETLAND_GENERA = [
    "Alnus", "Carex", "Cornus", "Eleocharis", "Juncus", "Ledum", "Rhododendron", "Salix", "Schoenoplectus",
    "Scirpus", "Typha",
]  # fmt: skip
NWPL_CHECKED_ABSENT = ["Salix pentandra"]
NOT_LISTED = "not_listed"
RATING_COLUMNS = ("nwpl_aw", "nwpl_wmvc", "nwpl_route")


class WetlandRecord(TypedDict):
    """One taxon's NWPL 2022 ratings in the two western regions and the route that found them."""

    plant_id: int
    nwpl_aw: str | None
    nwpl_wmvc: str | None
    nwpl_route: str


def wettest(codes: Iterable[str | None]) -> str | None:
    """The wettest indicator among the codes (conservative for the wetland axis); None when there is none."""
    known = [code for code in codes if code]
    return max(known, key=WETNESS_RANK.__getitem__) if known else None


def ratings_by_binomial(wetland_list: pl.DataFrame) -> dict[str, dict[str, str | None]]:
    """NWPL ratings keyed by binomial; a binomial listed twice keeps the wettest rating per region."""
    collected: dict[str, dict[str, list[str | None]]] = {}
    for row in conform(wetland_list, WETLAND_LIST_SCHEMA, "wetland list").iter_rows(named=True):
        key = binomial_key(row["nwpl_name"])
        if key is not None:
            entry = collected.setdefault(key, {"aw": [], "wmvc": []})
            entry["aw"].append(row["nwpl_aw"])
            entry["wmvc"].append(row["nwpl_wmvc"])
    return {key: {"aw": wettest(entry["aw"]), "wmvc": wettest(entry["wmvc"])} for key, entry in collected.items()}


def binomials(names: list[str | None]) -> set[str]:
    """Binomial keys of the non-empty names."""
    keys = (binomial_key(name) for name in names if name)
    return {key for key in keys if key is not None}


def wetland_record(taxon: dict[str, Any], lookup: dict[str, dict[str, str | None]]) -> WetlandRecord:
    """Ratings from the first route with an NWPL hit: accepted name, synonyms, the resolution table, regional names."""
    synonyms: list[str | None] = taxon["synonym_names"] or []
    regional: list[str | None] = taxon["regional_names"] or []
    resolved = {binomial_key(listed): binomial_key(target) for listed, target in NWPL_NAME_RESOLUTIONS.items()}
    plants_binomials = binomials([taxon["accepted_name"], *synonyms, *regional])
    routes: list[tuple[str, list[str | None]]] = [
        ("accepted_binomial", [taxon["accepted_name"]]),
        ("plants_synonym", synonyms),
        ("nwpl_name_resolution", [resolved[key] for key in plants_binomials if key in resolved]),
        ("regional_list_name", regional),
    ]
    for route, names in routes:
        hits = [lookup[key] for key in binomials(names) if key in lookup]
        if hits:
            return {
                "plant_id": taxon["plant_id"],
                "nwpl_aw": wettest(hit["aw"] for hit in hits),
                "nwpl_wmvc": wettest(hit["wmvc"] for hit in hits),
                "nwpl_route": route,
            }
    return {"plant_id": taxon["plant_id"], "nwpl_aw": None, "nwpl_wmvc": None, "nwpl_route": NOT_LISTED}


def resolve_wetland_ratings(
    species: pl.DataFrame, guide_rows: pl.DataFrame, wetland_list: pl.DataFrame
) -> pl.DataFrame:
    """The species envelope with nwpl_aw / nwpl_wmvc / nwpl_route resolved from an NWPL list (build-time step)."""
    lookup = ratings_by_binomial(wetland_list)
    regional_names = (
        guide_rows.explode("matched_plant_ids", empty_as_null=True)
        .drop_nulls("matched_plant_ids")
        .group_by(pl.col("matched_plant_ids").alias("plant_id"))
        .agg(pl.col("listed_scientific_name").drop_nulls().unique().alias("regional_names"))
    )
    taxa = species.join(regional_names, on="plant_id", how="left")
    records = [wetland_record(taxon, lookup) for taxon in taxa.iter_rows(named=True)]
    ratings = pl.DataFrame(
        records,
        schema={"plant_id": pl.Int64, "nwpl_aw": pl.String, "nwpl_wmvc": pl.String, "nwpl_route": pl.String},
    )
    return species.drop(RATING_COLUMNS, strict=False).join(ratings, on="plant_id", how="left").select(species.columns)


def assert_wetland_genera_rated(pool: pl.DataFrame) -> None:
    """A pool taxon of a wetland genus with no NWPL rating is a name-resolution miss and fails the build."""
    genus = pl.col("display_name").str.split(" ").list.first()
    unrated = pool.filter(
        (pl.col("nwpl_route") == NOT_LISTED)
        & genus.is_in(WETLAND_GENERA)
        & ~pl.col("display_name").is_in(NWPL_CHECKED_ABSENT)
    )
    if unrated.height:
        message = f"wetland-genus pool taxa without an NWPL rating: {unrated['display_name'].to_list()}"
        raise ValueError(message)
