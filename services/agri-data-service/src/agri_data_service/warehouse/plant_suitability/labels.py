"""Per-pick label text and the single fire-claim guard; see AGENTS.md §Labels here."""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

FIRE_LABEL_PREFIX = "PLANTS fire-resistant label (not a fire claim; judged against California fires): "
IN_REGION_YES = "in-region: yes"
IN_REGION_NO = "in-region: no — neighbouring guide"
UNCERTAIN_FROST_FREE = "uncertain frost-free fit"
CONFIRMED_FROST_FREE = "frost-free fit: confirmed"
UNKNOWN_FROST_FREE = "frost-free fit: unknown (no PLANTS minimum)"
UNCHECKABLE_SOURCES = "sources (band or habitat not checkable here): "
STRATIFICATION_PREFIX = "site stratification from "
GUILDS_WITH_FIRE_LABEL = ("greenstrip", "post_fire_restoration")
NO_FIRE_CLAIM = "Nothing in this layer claims that any plant prevents, slows or reduces fire."
CLAIM_VERBS = r"(?:prevent|reduc|slow|stop|suppress|retard|lower|limit|halt|block|decreas|curb|mitigat|protect)\w*"
FIRE_NOUNS = r"(?:(?:wild)?fires?|flames?|flammab\w*|ignit\w*)"
PROOFING_STEMS = r"(?:resist|retard|proof|safe|wise|smart|slow|stop|block|halt|suppress|prevent|reduc|limit)\w*"
# A claim verb within six words of a fire noun, "keeps fire from/away/out", or a fire-proofing compound; §Labels.
FIRE_CLAIM_PATTERN = re.compile(
    rf"\b{CLAIM_VERBS}\W+(?:\w+\W+){{0,6}}?{FIRE_NOUNS}\b"
    rf"|\bkeeps?\W+(?:\w+\W+){{0,2}}?{FIRE_NOUNS}\W+(?:from|away|out|back)\b"
    rf"|\b(?:wild)?(?:fire|flame)[- ]?{PROOFING_STEMS}",
    re.IGNORECASE,
)
# The only texts allowed to pair fire words with those verbs: the verbatim PLANTS label prefix and the disclaimer.
FIRE_CLAIM_ALLOW_LIST = (FIRE_LABEL_PREFIX, NO_FIRE_CLAIM)
# Prototype build_cell_recommendations.assert_no_fire_field_on_woody_buffer: any "fire" in a woody label.
FIRE_WORD = re.compile("fire", re.IGNORECASE)


def fire_resistance_label(values: list[str] | None) -> str:
    """Yes / No / conflicting / unknown from every PLANTS 'Fire Resistant' value of the taxon and merged members."""
    distinct = set(values or [])
    if not distinct:
        return "unknown"
    if distinct in ({"Yes"}, {"No"}):
        return next(iter(distinct))
    return "conflicting"


def pick_label_expression(guild: str) -> pl.Expr:
    """' | '-joined label: fire field (greenstrip, restoration), origin, in-region, frost-free fit, names, sources."""
    fire_label = pl.lit(FIRE_LABEL_PREFIX) + pl.col("fire_resistance_label")
    fire_field = [fire_label] if guild in GUILDS_WITH_FIRE_LABEL else []
    frost_free = (
        pl.when(pl.col("frost_free_uncertain"))
        .then(pl.lit(UNCERTAIN_FROST_FREE))
        .when(pl.col("axis_frost_free_days") == 1)
        .then(pl.lit(CONFIRMED_FROST_FREE))
        .otherwise(pl.lit(UNKNOWN_FROST_FREE))
    )
    sources_prefix = (
        pl.when(pl.col("axis_source_applicability").is_null())
        .then(pl.lit(UNCHECKABLE_SOURCES))
        .otherwise(pl.lit("sources: "))
    )
    base = pl.concat_str(
        [
            *fire_field,
            pl.col("origin_label"),
            pl.when(pl.col("in_region_supported")).then(pl.lit(IN_REGION_YES)).otherwise(pl.lit(IN_REGION_NO)),
            frost_free,
            pl.lit("listed as: ") + pl.col("supporting_names").list.join(", "),
            sources_prefix + pl.col("supporting_tags").list.join(" + "),
        ],
        separator=" | ",
    )
    stratification = pl.when(pl.col("stratification_sources").list.len() > 0).then(
        pl.lit(STRATIFICATION_PREFIX) + pl.col("stratification_sources").list.join(" + ")
    )
    with_stratification = pl.concat_str([base, stratification], separator=" | ")
    return pl.when(stratification.is_null()).then(base).otherwise(with_stratification)


def find_fire_claims(texts: Iterable[str | None]) -> list[str]:
    """Every text that claims a plant prevents, slows or reduces fire once the allow-listed wording is removed."""

    def without_allowed(text: str) -> str:
        return functools.reduce(lambda remaining, allowed: remaining.replace(allowed, " "), FIRE_CLAIM_ALLOW_LIST, text)

    return [text for text in texts if text and FIRE_CLAIM_PATTERN.search(without_allowed(text))]


def assert_no_fire_claims(cells: pl.DataFrame, metadata: Mapping[str, str] | None = None) -> None:
    """Raise when any string or list-of-string output value, or metadata value, claims a fire effect."""
    texts: list[str | None] = [*(metadata or {}).values()]
    for name, dtype in cells.schema.items():
        if dtype == pl.String:
            texts.extend(cells[name].to_list())
        elif dtype == pl.List(pl.String):
            texts.extend(cells.select(pl.col(name).explode(empty_as_null=True)).to_series().to_list())
    claims = find_fire_claims(texts)
    if claims:
        message = f"{len(claims)} output texts claim a fire effect, e.g. {claims[0]!r}"
        raise ValueError(message)


def assert_no_fire_field_on_woody_buffer(labels_by_guild: Mapping[str, Iterable[str | None]]) -> None:
    """Raise when a woody-buffer label mentions fire, or another guild's label lacks the verbatim PLANTS fire field."""
    for guild, labels in labels_by_guild.items():
        texts = [label for label in labels if label]
        if guild in GUILDS_WITH_FIRE_LABEL:
            offending = [label for label in texts if not label.startswith(FIRE_LABEL_PREFIX)]
            problem = "lack the verbatim PLANTS fire field"
        else:
            offending = [label for label in texts if FIRE_WORD.search(label)]
            problem = "mention fire (the woody buffer carries no fire field)"
        if offending:
            message = f"{len(offending)} {guild} labels {problem}, e.g. {offending[0]!r}"
            raise ValueError(message)
