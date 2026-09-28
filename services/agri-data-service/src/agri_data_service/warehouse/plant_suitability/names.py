"""Taxon name keys shared by pool building, exclusion matching and wetland ratings; see AGENTS.md here."""

from __future__ import annotations

import re

INFRASPECIFIC_MARKERS = frozenset({"ssp.", "subsp.", "var.", "f.", "forma"})
MULTIPLICATION_SIGN = "\u00d7"
CULTIVAR_QUOTE = "'"
BINOMIAL_TOKEN_COUNT = 2
NOTHOSPECIES_EPITHET_INDEX = 2
SPECIES_ROW, AUTONYM_ROW, OTHER_INFRASPECIFIC_ROW = 0, 1, 2


def name_tokens(name: str) -> list[str]:
    """Lower-case tokens with the hybrid sign spelled 'x' and cultivar quotes split off."""
    cleaned = name.replace(MULTIPLICATION_SIGN, " x ").replace(CULTIVAR_QUOTE, f" {CULTIVAR_QUOTE} ")
    return re.sub(r"\s+", " ", cleaned).strip().lower().split(" ")


def bare_tokens(name: str) -> list[str]:
    """Name tokens without cultivar quotes."""
    return [token for token in name_tokens(name) if token != CULTIVAR_QUOTE]


def binomial_key(name: str) -> str | None:
    """Genus + epithet ('genus x epithet' for a nothospecies); None for a bare genus."""
    tokens = bare_tokens(name)
    if len(tokens) < BINOMIAL_TOKEN_COUNT:
        return None
    if tokens[1] == "x" and len(tokens) > NOTHOSPECIES_EPITHET_INDEX:
        return f"{tokens[0]} x {tokens[NOTHOSPECIES_EPITHET_INDEX]}"
    return f"{tokens[0]} {tokens[1]}"


def is_infraspecific(name: str | None) -> bool:
    """True when the name carries ssp./var./f. ."""
    return bool(name) and bool(set(name_tokens(name or "")) & INFRASPECIFIC_MARKERS)


def is_autonym(name: str | None) -> bool:
    """True for an infraspecific name whose last epithet repeats the species epithet."""
    if not name or not is_infraspecific(name):
        return False
    return bare_tokens(name)[-1] == (binomial_key(name) or "").split(" ")[-1]


def representative_rank(name: str) -> int:
    """Species row, then autonym, then any other infraspecific taxon."""
    if not is_infraspecific(name):
        return SPECIES_ROW
    return AUTONYM_ROW if is_autonym(name) else OTHER_INFRASPECIFIC_ROW
